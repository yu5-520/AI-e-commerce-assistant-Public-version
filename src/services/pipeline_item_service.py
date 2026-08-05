"""V22.2.1 pipeline state and artifact-reference layer.

`pipeline_items` remains the current Agent queue during migration, but every stage
payload is also written once to the immutable Artifact Hub. Events keep references
and compact diagnostics instead of duplicating the complete semantic package.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads

PIPELINE_ITEM_VERSION = "22.2.1"
AGENT1_RECOVERY_VERSION = "23.1.3"
_AGENT1_RECOVERY_STAGES = ("agent1_failed", "agent1_output_invalid")

STATION_TO_ITEM_STAGE = {
    "report_receive_station": "data_received",
    "report_schema_station": "schema_ready",
    "report_fact_station": "fact_ready",
    "product_master_station": "product_master_ready",
    "product_metric_snapshot_station": "metric_snapshot_ready",
    "full_product_bundle_station": "context_bundle_ready",
    "bundle_validation_station": "quality_gate_ready",
    "product_signal_admission_station": "signal_admitted",
    "product_judgment_agent_station": "agent1_completed",
    "action_parameter_enrichment_station": "action_pack_ready",
    "action_plan_judgment_agent_station": "agent2_completed",
    "task_mapping_agent_station": "sop_mapped",
    "task_pool_admission_station": "task_admitted",
    "frontend_read_model_station": "read_model_ready",
    "task_pool_acceptance_station": "task_loop_ready",
}

STAGE_ORDER = {
    "batch_created": 1,
    "data_received": 5,
    "schema_ready": 10,
    "fact_ready": 15,
    "product_master_ready": 20,
    "metric_snapshot_ready": 25,
    "context_bundle_ready": 30,
    "quality_gate_ready": 35,
    "signal_admitted": 40,
    "observed_soft_gate": 42,
    "agent1_pending": 45,
    "agent1_running": 46,
    "agent1_failed": 47,
    "agent1_completed": 50,
    "agent1_output_invalid": 51,
    "action_pack_ready": 70,
    "action_pack_invalid": 72,
    "agent2_running": 75,
    "agent2_failed": 76,
    "agent2_completed": 80,
    "sop_mapped": 90,
    "task_admitted": 100,
    "read_model_ready": 110,
    "task_loop_ready": 120,
}


def now_iso() -> str:
    return datetime.now().isoformat()


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16].upper()


def _table_exists(conn: Any, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def ensure_pipeline_item_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_items (
                item_id TEXT PRIMARY KEY,
                data_version TEXT,
                product_id TEXT,
                store_id TEXT,
                signal_id TEXT,
                package_id TEXT,
                decision_id TEXT,
                task_id TEXT,
                current_stage TEXT,
                status TEXT,
                priority INTEGER DEFAULT 50,
                route TEXT,
                action_family TEXT,
                input_fingerprint TEXT,
                output_ref TEXT,
                retry_count INTEGER DEFAULT 0,
                error_reason TEXT,
                payload TEXT,
                artifact_refs_json TEXT,
                payload_artifact_ref TEXT,
                last_error_code TEXT,
                last_error_artifact_ref TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_item_events (
                event_id TEXT PRIMARY KEY,
                item_id TEXT,
                data_version TEXT,
                product_id TEXT,
                store_id TEXT,
                stage TEXT,
                status TEXT,
                station_id TEXT,
                input_ref TEXT,
                output_ref TEXT,
                input_refs_json TEXT,
                output_refs_json TEXT,
                error_code TEXT,
                payload_artifact_ref TEXT,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "pipeline_items",
            {
                "data_version": "TEXT",
                "product_id": "TEXT",
                "store_id": "TEXT",
                "signal_id": "TEXT",
                "package_id": "TEXT",
                "decision_id": "TEXT",
                "task_id": "TEXT",
                "current_stage": "TEXT",
                "status": "TEXT",
                "priority": "INTEGER DEFAULT 50",
                "route": "TEXT",
                "action_family": "TEXT",
                "input_fingerprint": "TEXT",
                "output_ref": "TEXT",
                "retry_count": "INTEGER DEFAULT 0",
                "error_reason": "TEXT",
                "payload": "TEXT",
                "artifact_refs_json": "TEXT",
                "payload_artifact_ref": "TEXT",
                "last_error_code": "TEXT",
                "last_error_artifact_ref": "TEXT",
                "updated_at": "TEXT",
            },
        )
        ensure_columns(
            conn,
            "pipeline_item_events",
            {
                "item_id": "TEXT",
                "data_version": "TEXT",
                "product_id": "TEXT",
                "store_id": "TEXT",
                "stage": "TEXT",
                "status": "TEXT",
                "station_id": "TEXT",
                "input_ref": "TEXT",
                "output_ref": "TEXT",
                "input_refs_json": "TEXT",
                "output_refs_json": "TEXT",
                "error_code": "TEXT",
                "payload_artifact_ref": "TEXT",
                "payload": "TEXT",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_items_version ON pipeline_items(data_version, current_stage, status, priority)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_items_product ON pipeline_items(data_version, product_id, package_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_items_artifact ON pipeline_items(payload_artifact_ref)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_item_events_version ON pipeline_item_events(data_version, stage, created_at)"
        )
        conn.commit()


def make_item_id(
    data_version: str | None,
    product_id: str | None = None,
    signal_id: str | None = None,
    package_id: str | None = None,
    decision_id: str | None = None,
) -> str:
    raw = "|".join(
        str(value or "")
        for value in [data_version, product_id, signal_id, package_id, decision_id]
    )
    return f"PI-{_hash(raw)}"


def build_item_envelope(
    *,
    data_version: str | None,
    item_id: str | None = None,
    product_id: str | None = None,
    store_id: str | None = None,
    signal_id: str | None = None,
    package_id: str | None = None,
    decision_id: str | None = None,
    task_id: str | None = None,
    action_family: str | None = None,
    route: str | None = None,
    input_ref: str | None = None,
    output_ref: str | None = None,
    stage: str | None = None,
    artifact_refs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    resolved_item_id = item_id or make_item_id(
        data_version,
        product_id,
        signal_id,
        package_id,
        decision_id,
    )
    return {
        "version": PIPELINE_ITEM_VERSION,
        "itemId": resolved_item_id,
        "dataVersion": data_version,
        "productId": product_id,
        "storeId": store_id,
        "signalId": signal_id,
        "packageId": package_id,
        "decisionId": decision_id,
        "taskId": task_id,
        "actionFamily": action_family,
        "route": route,
        "stage": stage,
        "inputRef": input_ref,
        "outputRef": output_ref,
        "artifactRefs": artifact_refs or {},
        "idempotencyKey": f"{stage or 'stage'}:{resolved_item_id}:{_hash(str(input_ref or output_ref or data_version or ''))}",
        "rule": "V22.2.1 pipelineItem carries state and artifact references; full payload remains compatibility-only during dual-write migration.",
    }


def _payload_identity(payload: Dict[str, Any]) -> Dict[str, Any]:
    product = payload.get("productIdentity") if isinstance(payload.get("productIdentity"), dict) else {}
    matrix = payload.get("matrixDispatch") if isinstance(payload.get("matrixDispatch"), dict) else {}
    agent1 = payload.get("agent1OperatingJudgment") if isinstance(payload.get("agent1OperatingJudgment"), dict) else {}
    return {
        "data_version": payload.get("dataVersion") or payload.get("data_version"),
        "product_id": payload.get("productId") or product.get("productId"),
        "store_id": payload.get("storeId") or product.get("storeId"),
        "signal_id": payload.get("signalId") or payload.get("signal_id"),
        "package_id": payload.get("packageId") or payload.get("package_id"),
        "decision_id": payload.get("decisionId") or payload.get("decision_id"),
        "task_id": payload.get("taskId") or payload.get("task_id") or payload.get("id"),
        "action_family": matrix.get("selectedActionFamily") or payload.get("selectedActionFamilyHint") or payload.get("actionFamily") or agent1.get("selectedActionFamily"),
        "route": matrix.get("routeId") or agent1.get("selectedOperatingRoute") or payload.get("route"),
    }


def _existing_item(item_id: str) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_items WHERE item_id=?",
            (item_id,),
        ).fetchone()
    return dict(row) if row else {}


def _artifact_refs(value: Any) -> Dict[str, Any]:
    return _load(value)


def _transport_payload(
    *,
    envelope: Dict[str, Any],
    stage: str,
    payload: Dict[str, Any],
    previous_refs: Dict[str, Any],
    station_id: str | None = None,
) -> Dict[str, Any]:
    if not payload:
        return {
            "payloadArtifactRef": None,
            "artifactRefs": previous_refs,
            "contentHash": None,
        }
    from src.services.artifact_transport_service import pipeline_payload_artifact

    return pipeline_payload_artifact(
        envelope=envelope,
        stage=stage,
        payload=payload,
        station_id=station_id or stage,
        previous_artifact_refs=previous_refs,
    )


def upsert_pipeline_item(
    envelope: Dict[str, Any],
    *,
    stage: str | None = None,
    status: str = "ready",
    priority: int | None = None,
    output_ref: str | None = None,
    payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    ensure_pipeline_item_tables()
    payload = payload or {}
    identity = _payload_identity(payload)
    data_version = envelope.get("dataVersion") or identity.get("data_version")
    product_id = envelope.get("ProductId") or envelope.get("productId") or identity.get("product_id")
    store_id = envelope.get("storeId") or identity.get("store_id")
    signal_id = envelope.get("signalId") or identity.get("signal_id")
    package_id = envelope.get("packageId") or identity.get("package_id")
    decision_id = envelope.get("decisionId") or identity.get("decision_id")
    task_id = envelope.get("taskId") or identity.get("task_id")
    action_family = envelope.get("actionFamily") or identity.get("action_family")
    route = envelope.get("route") or identity.get("route")
    item_id = envelope.get("itemId") or make_item_id(
        data_version,
        product_id,
        signal_id,
        package_id,
        decision_id,
    )
    current_stage = stage or envelope.get("stage") or "batch_created"
    selected_priority = int(priority if priority is not None else envelope.get("priority") or 50)
    out_ref = output_ref or envelope.get("outputRef")
    existing = _existing_item(item_id)
    if existing:
        old_stage = existing.get("current_stage") or "batch_created"
        if STAGE_ORDER.get(current_stage, 0) < STAGE_ORDER.get(old_stage, 0):
            current_stage = str(old_stage)
        selected_priority = min(
            selected_priority,
            int(existing.get("priority") or selected_priority),
        )
    previous_refs = {
        **_artifact_refs(existing.get("artifact_refs_json")),
        **(envelope.get("artifactRefs") if isinstance(envelope.get("artifactRefs"), dict) else {}),
        **(payload.get("artifactRefs") if isinstance(payload.get("artifactRefs"), dict) else {}),
    }
    normalized_envelope = build_item_envelope(
        data_version=data_version,
        item_id=item_id,
        product_id=product_id,
        store_id=store_id,
        signal_id=signal_id,
        package_id=package_id,
        decision_id=decision_id,
        task_id=task_id,
        action_family=action_family,
        route=route,
        input_ref=envelope.get("inputRef"),
        output_ref=out_ref,
        stage=current_stage,
        artifact_refs=previous_refs,
    )
    transfer = _transport_payload(
        envelope=normalized_envelope,
        stage=current_stage,
        payload=payload,
        previous_refs=previous_refs,
    )
    refs = transfer.get("artifactRefs") if isinstance(transfer.get("artifactRefs"), dict) else previous_refs
    payload_artifact_ref = transfer.get("payloadArtifactRef") or existing.get("payload_artifact_ref")
    normalized_envelope["artifactRefs"] = refs
    normalized_envelope["payloadArtifactRef"] = payload_artifact_ref
    row_payload = {
        "envelope": normalized_envelope,
        "payload": payload,
        "artifactRefs": refs,
        "payloadArtifactRef": payload_artifact_ref,
        "version": PIPELINE_ITEM_VERSION,
        "migrationMode": "artifact_dual_write",
    }
    error_code = str(payload.get("reason") or "") if status == "failed" else None
    now = now_iso()
    with connect() as conn:
        if existing:
            conn.execute(
                """
                UPDATE pipeline_items
                SET data_version=COALESCE(?, data_version),
                    product_id=COALESCE(?, product_id),
                    store_id=COALESCE(?, store_id),
                    signal_id=COALESCE(?, signal_id),
                    package_id=COALESCE(?, package_id),
                    decision_id=COALESCE(?, decision_id),
                    task_id=COALESCE(?, task_id),
                    current_stage=?, status=?, priority=?,
                    route=COALESCE(?, route),
                    action_family=COALESCE(?, action_family),
                    output_ref=COALESCE(?, output_ref),
                    payload=?, artifact_refs_json=?, payload_artifact_ref=?,
                    last_error_code=?, last_error_artifact_ref=?, updated_at=?
                WHERE item_id=?
                """,
                (
                    data_version, product_id, store_id, signal_id, package_id,
                    decision_id, task_id, current_stage, status, selected_priority,
                    route, action_family, out_ref, dumps(row_payload), dumps(refs),
                    payload_artifact_ref, error_code,
                    payload_artifact_ref if error_code else None, now, item_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO pipeline_items (
                    item_id, data_version, product_id, store_id, signal_id,
                    package_id, decision_id, task_id, current_stage, status,
                    priority, route, action_family, output_ref, payload,
                    artifact_refs_json, payload_artifact_ref, last_error_code,
                    last_error_artifact_ref, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id, data_version, product_id, store_id, signal_id,
                    package_id, decision_id, task_id, current_stage, status,
                    selected_priority, route, action_family, out_ref,
                    dumps(row_payload), dumps(refs), payload_artifact_ref,
                    error_code, payload_artifact_ref if error_code else None, now, now,
                ),
            )
        conn.commit()
    return normalized_envelope


def _event_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for key in (
        "reason", "missing", "count", "rowCount", "productCount",
        "completedItemCount", "createdTaskCount", "taskDecisionCount",
        "providerStatus", "frontendFailureLabel", "taskAdmissionAllowed",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            summary[key] = value
    return summary


def record_pipeline_item_event(
    envelope: Dict[str, Any],
    *,
    station_id: str | None = None,
    stage: str | None = None,
    status: str = "completed",
    input_ref: str | None = None,
    output_ref: str | None = None,
    payload: Dict[str, Any] | None = None,
) -> None:
    ensure_pipeline_item_tables()
    payload = payload or {}
    item_id = envelope.get("itemId") or make_item_id(
        envelope.get("dataVersion"),
        envelope.get("productId"),
        envelope.get("signalId"),
        envelope.get("packageId"),
        envelope.get("decisionId"),
    )
    current_stage = str(stage or envelope.get("stage") or "unknown_stage")
    existing = _existing_item(item_id)
    previous_refs = {
        **_artifact_refs(existing.get("artifact_refs_json")),
        **(envelope.get("artifactRefs") if isinstance(envelope.get("artifactRefs"), dict) else {}),
    }
    transfer = _transport_payload(
        envelope=envelope,
        stage=current_stage,
        payload=payload,
        previous_refs=previous_refs,
        station_id=station_id,
    )
    refs = transfer.get("artifactRefs") if isinstance(transfer.get("artifactRefs"), dict) else previous_refs
    artifact_ref = transfer.get("payloadArtifactRef") or existing.get("payload_artifact_ref")
    now = now_iso()
    event_id = f"PIE-{_hash('|'.join(str(value or '') for value in [item_id, station_id, current_stage, output_ref, now]))}"
    compact_payload = {
        "version": PIPELINE_ITEM_VERSION,
        "artifactRef": artifact_ref,
        "artifactRefs": refs,
        "summary": _event_summary(payload),
        "fullPayloadStoredInArtifactHub": bool(artifact_ref),
    }
    error_code = str(payload.get("reason") or "") if status == "failed" else None
    input_refs = {
        key: value
        for key, value in refs.items()
        if key not in {"currentStageRef"}
    }
    output_refs = {
        "currentStageRef": artifact_ref,
        "outputRef": output_ref or envelope.get("outputRef"),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO pipeline_item_events (
                event_id, item_id, data_version, product_id, store_id, stage,
                status, station_id, input_ref, output_ref, input_refs_json,
                output_refs_json, error_code, payload_artifact_ref, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, item_id, envelope.get("dataVersion"),
                envelope.get("productId"), envelope.get("storeId"),
                current_stage, status, station_id,
                input_ref or envelope.get("inputRef"),
                output_ref or envelope.get("outputRef"),
                dumps(input_refs), dumps(output_refs), error_code,
                artifact_ref, dumps(compact_payload), now,
            ),
        )
        conn.commit()


def _iter_payload_rows(table: str, data_version: str | None) -> Iterable[Dict[str, Any]]:
    if not data_version:
        return []
    with connect() as conn:
        if not _table_exists(conn, table):
            return []
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if "data_version" not in columns:
            return []
        payload_column = "payload" if "payload" in columns else "task_payload" if "task_payload" in columns else None
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE data_version=?",
            (data_version,),
        ).fetchall()
    result: List[Dict[str, Any]] = []
    for row in rows:
        data = _load(row[payload_column]) if payload_column else dict(row)
        if isinstance(data, dict):
            result.append(data)
    return result


def seed_pipeline_items_from_artifacts(
    data_version: str | None,
    *,
    stage: str = "batch_created",
    source: str = "station_observation",
) -> Dict[str, Any]:
    ensure_pipeline_item_tables()
    if not data_version:
        return {
            "version": PIPELINE_ITEM_VERSION,
            "dataVersion": data_version,
            "seededCount": 0,
            "source": source,
        }
    seeded: List[Dict[str, Any]] = []
    anchor = build_item_envelope(
        data_version=data_version,
        item_id=f"PI-BATCH-{_hash(data_version)}",
        stage=stage,
    )
    seeded.append(
        upsert_pipeline_item(
            anchor,
            stage=stage,
            status="running",
            priority=100,
            output_ref=f"batch:{data_version}",
            payload={"source": source},
        )
    )
    for table, table_stage in [("product_signal_pool_v15", "signal_admitted")]:
        for payload in _iter_payload_rows(table, data_version):
            identity = _payload_identity(payload)
            envelope = build_item_envelope(
                data_version=data_version,
                product_id=identity.get("product_id"),
                store_id=identity.get("store_id"),
                signal_id=identity.get("signal_id"),
                package_id=identity.get("package_id"),
                decision_id=identity.get("decision_id"),
                task_id=identity.get("task_id"),
                action_family=identity.get("action_family"),
                route=identity.get("route"),
                output_ref=f"{table}:{data_version}",
                stage=table_stage,
            )
            seeded.append(
                upsert_pipeline_item(
                    envelope,
                    stage=table_stage,
                    status="ready",
                    priority=50,
                    output_ref=f"{table}:{data_version}",
                    payload=payload,
                )
            )
    return {
        "version": PIPELINE_ITEM_VERSION,
        "dataVersion": data_version,
        "seededCount": len(seeded),
        "source": source,
        "stage": stage,
        "runtimeSeedSources": ["product_signal_pool_v15"],
        "forbiddenRuntimeSeedSources": [
            "agent_product_judgments_v15",
            "product_judgment_packages_v15",
            "task_generation_decisions_v15",
            "task_pool_entries",
        ],
        "rule": "V22.2.1 seeds only pre-Agent signals and dual-writes their payload artifacts.",
    }


def record_station_output_as_item_state(
    data_version: str | None,
    station_id: str,
    output: Dict[str, Any],
    *,
    input_ref: str | None = None,
    output_ref: str | None = None,
) -> Dict[str, Any]:
    stage = STATION_TO_ITEM_STAGE.get(station_id, station_id)
    seed = seed_pipeline_items_from_artifacts(
        data_version,
        stage=stage,
        source=f"station:{station_id}",
    )
    envelope = build_item_envelope(
        data_version=data_version,
        item_id=f"PI-BATCH-{_hash(data_version or 'latest')}",
        stage=stage,
        input_ref=input_ref,
        output_ref=output_ref or output.get("outputRef"),
    )
    envelope = upsert_pipeline_item(
        envelope,
        stage=stage,
        status="completed",
        priority=100,
        output_ref=output_ref or output.get("outputRef"),
        payload={"stationId": station_id, "output": output},
    )
    record_pipeline_item_event(
        envelope,
        station_id=station_id,
        stage=stage,
        status="completed",
        input_ref=input_ref,
        output_ref=output_ref or output.get("outputRef"),
        payload=output,
    )
    return {
        "version": PIPELINE_ITEM_VERSION,
        "stage": stage,
        "seededCount": seed.get("seededCount", 0),
        "outputRef": output_ref or output.get("outputRef"),
        "artifactRefs": envelope.get("artifactRefs") or {},
        "runtimeSeedSources": seed.get("runtimeSeedSources"),
        "rule": "Station state is indexed by pipeline item and immutable artifact reference.",
    }



def _agent1_recovery_plan_hash(value: Dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _load_agent1_recovery_rows(
    data_version: str,
    product_id: str,
    store_id: str | None = None,
) -> List[Dict[str, Any]]:
    ensure_pipeline_item_tables()
    where = [
        "data_version=?",
        "product_id=?",
        "current_stage IN (?,?)",
        "status='failed'",
    ]
    params: List[Any] = [
        data_version,
        product_id,
        *_AGENT1_RECOVERY_STAGES,
    ]
    if store_id:
        where.append("store_id=?")
        params.append(store_id)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_items WHERE "
            + " AND ".join(where)
            + " ORDER BY store_id ASC,item_id ASC",
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def _current_agent1_recovery_row(item_id: str) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_items WHERE item_id=?",
            (item_id,),
        ).fetchone()
    return dict(row) if row else {}


def agent1_recovery_plan(
    data_version: str,
    product_id: str,
    *,
    store_id: str | None = None,
    policy_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a selector-bound dry-run plan. This function performs zero writes."""
    if not str(data_version or "").strip() or not str(product_id or "").strip():
        raise ValueError("agent1_recovery_selector_required")
    from src.services.agent_input_transport_v2258_service import (
        inspect_agent1_input_ref,
    )

    rows = _load_agent1_recovery_rows(data_version, product_id, store_id)
    items: List[Dict[str, Any]] = []
    for row in rows:
        inspection = inspect_agent1_input_ref(
            row,
            policy_context=policy_context,
        )
        items.append(
            {
                "itemId": row.get("item_id"),
                "dataVersion": row.get("data_version"),
                "productId": row.get("product_id"),
                "storeId": row.get("store_id"),
                "signalId": row.get("signal_id"),
                "expectedStage": row.get("current_stage"),
                "expectedStatus": row.get("status"),
                "expectedUpdatedAt": row.get("updated_at"),
                "signalRef": inspection.get("signalRef"),
                "currentAgent1InputRef": inspection.get("currentAgent1InputRef"),
                "inputDecision": inspection.get("decision"),
                "inputValidation": inspection.get("validation"),
            }
        )
    material = {
        "version": AGENT1_RECOVERY_VERSION,
        "selector": {
            "dataVersion": data_version,
            "productId": product_id,
            "storeId": store_id,
        },
        "items": items,
    }
    return {
        "schema": "pipeline.agent1_recovery_plan.v1",
        **material,
        "targetCount": len(items),
        "planHash": _agent1_recovery_plan_hash(material),
        "dryRun": True,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }


def _set_agent1_retry_pending(
    row: Dict[str, Any],
    receipt: Dict[str, Any],
    *,
    plan_hash: str,
) -> None:
    item_id = str(row.get("item_id") or "")
    now = now_iso()
    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_items
            SET current_stage='agent1_pending',status='retry',
                retry_count=COALESCE(retry_count,0)+1,
                error_reason=NULL,last_error_code=NULL,updated_at=?
            WHERE item_id=? AND current_stage IN (?,?) AND status='failed'
            """,
            (now, item_id, *_AGENT1_RECOVERY_STAGES),
        )
        changed = int(conn.execute("SELECT changes() AS n").fetchone()["n"] or 0)
        conn.commit()
    if changed != 1:
        raise RuntimeError(f"agent1_recovery_state_changed:{item_id}")
    envelope = build_item_envelope(
        data_version=row.get("data_version"),
        item_id=item_id,
        product_id=row.get("product_id"),
        store_id=row.get("store_id"),
        signal_id=row.get("signal_id"),
        package_id=row.get("package_id"),
        action_family=row.get("action_family"),
        route=row.get("route"),
        input_ref=receipt.get("activeAgent1InputRef"),
        output_ref=row.get("output_ref"),
        stage="agent1_pending",
    )
    record_pipeline_item_event(
        envelope,
        station_id="agent1_safe_retry_station",
        stage="agent1_pending",
        status="ready",
        input_ref=receipt.get("activeAgent1InputRef"),
        output_ref=row.get("output_ref"),
        payload={
            "reason": "agent1_safe_retry_prepared",
            "version": AGENT1_RECOVERY_VERSION,
            "planHash": plan_hash,
            "inputAction": receipt.get("inputAction"),
            "oldAgent1InputRef": receipt.get("currentAgent1InputRef"),
            "activeAgent1InputRef": receipt.get("activeAgent1InputRef"),
            "providerCallsExecuted": 0,
            "historicalFailureArtifactPreserved": bool(
                row.get("last_error_artifact_ref")
            ),
        },
    )


def apply_agent1_recovery_plan(
    plan: Dict[str, Any],
    *,
    policy_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if plan.get("schema") != "pipeline.agent1_recovery_plan.v1":
        raise ValueError("agent1_recovery_plan_schema_invalid")
    selector = plan.get("selector") if isinstance(plan.get("selector"), dict) else {}
    current = agent1_recovery_plan(
        str(selector.get("dataVersion") or ""),
        str(selector.get("productId") or ""),
        store_id=str(selector.get("storeId") or "") or None,
        policy_context=policy_context,
    )
    if current.get("planHash") != plan.get("planHash"):
        raise RuntimeError("STALE_AGENT1_RECOVERY_PLAN")
    from src.services.agent_input_transport_v2258_service import (
        ensure_agent1_input_ref_with_receipt,
    )

    applied: List[Dict[str, Any]] = []
    for item in current.get("items") or []:
        item_id = str(item.get("itemId") or "")
        row = _current_agent1_recovery_row(item_id)
        if (
            not row
            or row.get("current_stage") not in _AGENT1_RECOVERY_STAGES
            or row.get("status") != "failed"
            or row.get("data_version") != selector.get("dataVersion")
            or row.get("product_id") != selector.get("productId")
            or (selector.get("storeId") and row.get("store_id") != selector.get("storeId"))
        ):
            raise RuntimeError(f"agent1_recovery_selector_or_state_changed:{item_id}")
        receipt = ensure_agent1_input_ref_with_receipt(
            row,
            policy_context=policy_context,
        )
        _set_agent1_retry_pending(
            row,
            receipt,
            plan_hash=str(plan.get("planHash") or ""),
        )
        applied.append(
            {
                "itemId": item_id,
                "storeId": row.get("store_id"),
                "inputAction": receipt.get("inputAction"),
                "oldAgent1InputRef": receipt.get("currentAgent1InputRef"),
                "activeAgent1InputRef": receipt.get("activeAgent1InputRef"),
                "stage": "agent1_pending",
                "status": "retry",
            }
        )
    return {
        "schema": "pipeline.agent1_recovery_result.v1",
        "version": AGENT1_RECOVERY_VERSION,
        "selector": selector,
        "planHash": plan.get("planHash"),
        "appliedCount": len(applied),
        "items": applied,
        "databaseMutated": bool(applied),
        "providerCallsExecuted": 0,
    }


def run_agent1_recovery(
    data_version: str,
    product_id: str,
    *,
    store_id: str | None = None,
    apply: bool = False,
    expected_plan_hash: str | None = None,
    policy_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    plan = agent1_recovery_plan(
        data_version,
        product_id,
        store_id=store_id,
        policy_context=policy_context,
    )
    if not apply:
        return plan
    if not expected_plan_hash or expected_plan_hash != plan.get("planHash"):
        raise RuntimeError("agent1_recovery_plan_hash_required_or_stale")
    return apply_agent1_recovery_plan(
        plan,
        policy_context=policy_context,
    )


def pipeline_item_summary(
    data_version: str | None = None,
    *,
    limit: int = 50,
) -> Dict[str, Any]:
    ensure_pipeline_item_tables()
    where: List[str] = []
    params: List[Any] = []
    if data_version:
        where.append("data_version=?")
        params.append(data_version)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with connect() as conn:
        items = conn.execute(
            f"SELECT * FROM pipeline_items{clause} ORDER BY priority ASC, updated_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        counts = conn.execute(
            f"SELECT current_stage, status, COUNT(*) AS c FROM pipeline_items{clause} GROUP BY current_stage, status",
            tuple(params),
        ).fetchall()
    by_stage_status = {
        f"{row['current_stage']}:{row['status']}": int(row["c"] or 0)
        for row in counts
    }
    return {
        "version": PIPELINE_ITEM_VERSION,
        "dataVersion": data_version,
        "itemCount": len(items),
        "byStageStatus": by_stage_status,
        "items": [
            {
                "itemId": row["item_id"],
                "dataVersion": row["data_version"],
                "productId": row["product_id"],
                "storeId": row["store_id"],
                "signalId": row["signal_id"],
                "packageId": row["package_id"],
                "decisionId": row["decision_id"],
                "taskId": row["task_id"],
                "currentStage": row["current_stage"],
                "status": row["status"],
                "priority": row["priority"],
                "route": row["route"],
                "actionFamily": row["action_family"],
                "outputRef": row["output_ref"],
                "payloadArtifactRef": row["payload_artifact_ref"],
                "artifactRefs": _artifact_refs(row["artifact_refs_json"]),
                "lastErrorCode": row["last_error_code"],
                "lastErrorArtifactRef": row["last_error_artifact_ref"],
                "updatedAt": row["updated_at"],
            }
            for row in items
        ],
        "runtimeContract": "pipeline state plus immutable artifact references; payload column is temporary compatibility storage",
        "rule": "V22.2.1 summaries expose refs and never expand artifact content.",
    }


__all__ = [
    "PIPELINE_ITEM_VERSION",
    "STATION_TO_ITEM_STAGE",
    "STAGE_ORDER",
    "ensure_pipeline_item_tables",
    "make_item_id",
    "build_item_envelope",
    "upsert_pipeline_item",
    "record_pipeline_item_event",
    "seed_pipeline_items_from_artifacts",
    "record_station_output_as_item_state",
    "AGENT1_RECOVERY_VERSION",
    "agent1_recovery_plan",
    "apply_agent1_recovery_plan",
    "run_agent1_recovery",
    "pipeline_item_summary",
]
