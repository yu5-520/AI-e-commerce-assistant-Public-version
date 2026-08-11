"""V21.5.2 single-source signal pool.

Signal Pool is a persistence boundary, not a snapshot generator. It accepts only
an already materialized V21.5 operating-evidence snapshot, validates every
fullProductBundle, scopes persisted ids by dataVersion, and fails closed when an
old or incomplete snapshot reaches the runtime. Every admitted formal signal is
also materialized once through Artifact Transport so downstream Agent handoff
receives an immutable ART-* signalRef rather than a mutable database identity.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services import product_signal_snapshot_service
from src.services.artifact_transport_service import (
    artifact_type_for_stage,
    pipeline_payload_artifact,
    validate_artifact,
)

SIGNAL_POOL_VERSION = "21.5.2"
EXPECTED_EVIDENCE_VERSION = "21.5.0"
EXPECTED_EVIDENCE_CONTRACT = "operatingEvidenceGraph.v1"
AGENT_READY_STATUS = "pending_rag_agent"
FORMAL_SIGNAL_ARTIFACT_STAGE = "signal_admitted"
PACKAGE_PENDING_STATUSES = {
    "pending_agent_judgment",
    "pending_product_signal_package",
    "pending_signal_package",
    "pending_rag_agent",
}
TERMINAL_STATUSES = {
    "judged_pending_snapshot",
    "ignored_noise",
    "observed_only",
    "data_gap_observed",
    "merge_candidate",
    "task_snapshot_created",
    "failed_retry",
}


def now_iso() -> str:
    return datetime.now().isoformat()


def ensure_signal_pool_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_pool_v14 (
                signal_id TEXT PRIMARY KEY,
                source_signal_id TEXT,
                data_version TEXT,
                entity_type TEXT,
                entity_id TEXT,
                store_id TEXT,
                signal_type TEXT NOT NULL,
                signal_strength TEXT,
                status TEXT NOT NULL,
                source_ref TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "signal_pool_v14",
            {
                "source_signal_id": "TEXT",
                "data_version": "TEXT",
                "entity_type": "TEXT",
                "entity_id": "TEXT",
                "store_id": "TEXT",
                "signal_strength": "TEXT",
                "source_ref": "TEXT",
                "updated_at": "TEXT",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_pool_v14_version "
            "ON signal_pool_v14(data_version, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_pool_v14_entity "
            "ON signal_pool_v14(entity_type, entity_id, store_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_pool_v14_status "
            "ON signal_pool_v14(status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_pool_v14_source_signal "
            "ON signal_pool_v14(source_signal_id, data_version)"
        )
        conn.commit()


def _agent_ready_status(
    existing_status: str | None,
    incoming_status: str | None,
) -> str:
    if existing_status in TERMINAL_STATUSES:
        return str(existing_status)
    if existing_status in PACKAGE_PENDING_STATUSES:
        return AGENT_READY_STATUS
    if incoming_status in PACKAGE_PENDING_STATUSES:
        return AGENT_READY_STATUS
    return incoming_status or AGENT_READY_STATUS


def _source_signal_id(signal: Dict[str, Any]) -> str:
    return str(
        signal.get("sourceSignalId")
        or signal.get("signalId")
        or ""
    ).strip()


def _scoped_signal_id(signal: Dict[str, Any]) -> str:
    source_signal_id = _source_signal_id(signal)
    data_version = str(signal.get("dataVersion") or "latest").strip() or "latest"
    digest = hashlib.sha1(
        f"{data_version}|{source_signal_id}".encode("utf-8")
    ).hexdigest()[:18].upper()
    return f"PSIGV-{digest}"


def _formal_signal_payload(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Return the immutable business payload used to derive signalRef.

    Mutable pool lifecycle state and already-produced Artifact references are
    intentionally excluded. Re-running the same dataVersion therefore resolves
    to the same content-addressed ART-* identity even after the pool row advances
    to a later status.
    """

    excluded = {
        "artifactRefs",
        "signalRef",
        "signal_ref",
        "payload",
        "createdAt",
        "updatedAt",
        "agentReadyStatus",
    }
    payload = {
        key: value
        for key, value in signal.items()
        if key not in excluded
    }
    payload["signalId"] = signal.get("signalId")
    payload["sourceSignalId"] = signal.get("sourceSignalId")
    payload["dataVersion"] = signal.get("dataVersion")
    payload["status"] = AGENT_READY_STATUS
    payload["formalSignalContract"] = "competition.formal_signal.v1"
    payload["evidenceContract"] = EXPECTED_EVIDENCE_CONTRACT
    payload["evidenceVersion"] = EXPECTED_EVIDENCE_VERSION
    return payload


def _materialize_formal_signal_artifact(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Attach one validated immutable signalRef to the formal Signal Pool row."""

    next_signal = dict(signal)
    inherited = (
        next_signal.get("artifactRefs")
        if isinstance(next_signal.get("artifactRefs"), dict)
        else {}
    )
    formal_payload = _formal_signal_payload(next_signal)
    transport = pipeline_payload_artifact(
        envelope={
            "itemId": next_signal.get("signalId"),
            "dataVersion": next_signal.get("dataVersion"),
            "productId": next_signal.get("productId") or next_signal.get("entityId"),
            "storeId": next_signal.get("storeId"),
            "outputRef": next_signal.get("sourceRef"),
            "artifactRefs": inherited,
        },
        stage=FORMAL_SIGNAL_ARTIFACT_STAGE,
        payload=formal_payload,
        station_id="signal_pool_service",
        previous_artifact_refs=inherited,
    )
    refs = (
        transport.get("artifactRefs")
        if isinstance(transport.get("artifactRefs"), dict)
        else {}
    )
    signal_ref = str(refs.get("signalRef") or "").strip()
    expected_type = artifact_type_for_stage(FORMAL_SIGNAL_ARTIFACT_STAGE)
    validation = (
        validate_artifact(signal_ref, expected_type=expected_type)
        if signal_ref.startswith("ART-")
        else {"ok": False, "status": "formal_signal_ref_missing"}
    )
    if validation.get("ok") is not True:
        raise RuntimeError(
            "formal_signal_artifact_invalid:"
            f"dataVersion={next_signal.get('dataVersion') or 'latest'};"
            f"signalId={next_signal.get('signalId') or 'UNKNOWN'};"
            f"status={validation.get('status') or 'invalid'}"
        )

    next_signal["signalRef"] = signal_ref
    next_signal["artifactRefs"] = refs
    next_signal["signalArtifactContentHash"] = transport.get("contentHash")
    next_signal["signalArtifactType"] = expected_type
    next_signal["signalArtifactIdempotentHit"] = bool(transport.get("idempotentHit"))
    return next_signal


def _save_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    ensure_signal_pool_tables()
    now = now_iso()
    next_signal = dict(signal)
    source_signal_id = _source_signal_id(next_signal)
    signal_id = _scoped_signal_id(next_signal)
    next_signal["sourceSignalId"] = source_signal_id
    next_signal["signalId"] = signal_id
    next_signal = _materialize_formal_signal_artifact(next_signal)

    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM signal_pool_v14 WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
        status = _agent_ready_status(
            existing["status"] if existing else None,
            next_signal.get("status"),
        )
        created_at = existing["created_at"] if existing else now
        payload = {
            **next_signal,
            "version": SIGNAL_POOL_VERSION,
            "status": status,
            "agentReadyStatus": AGENT_READY_STATUS,
            "signalIdScope": "data_version",
            "evidenceContract": EXPECTED_EVIDENCE_CONTRACT,
            "evidenceVersion": EXPECTED_EVIDENCE_VERSION,
        }
        conn.execute(
            """
            INSERT OR REPLACE INTO signal_pool_v14 (
                signal_id,source_signal_id,data_version,entity_type,entity_id,
                store_id,signal_type,signal_strength,status,source_ref,payload,
                created_at,updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                source_signal_id,
                next_signal.get("dataVersion"),
                next_signal.get("entityType"),
                next_signal.get("entityId"),
                next_signal.get("storeId"),
                next_signal.get("signalType"),
                next_signal.get("signalStrength"),
                status,
                next_signal.get("sourceRef")
                or f"full_product_bundle:{next_signal.get('dataVersion') or 'latest'}",
                dumps(payload),
                created_at,
                now,
            ),
        )
        conn.commit()

    next_signal["status"] = status
    return next_signal


def update_signal_status(
    signal_id: str | None,
    status: str,
    patch: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    if not signal_id:
        return None
    ensure_signal_pool_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM signal_pool_v14 WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
        if not row:
            return None
        payload = loads(row["payload"])
        payload.update(patch or {})
        payload["status"] = status
        payload["updatedAt"] = now_iso()
        conn.execute(
            "UPDATE signal_pool_v14 SET status=?,payload=?,updated_at=? "
            "WHERE signal_id=?",
            (status, dumps(payload), payload["updatedAt"], signal_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM signal_pool_v14 WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
    return row_to_signal(row) if row else None


def row_to_signal(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    return {
        **payload,
        "payload": payload,
        "signalId": row["signal_id"],
        "sourceSignalId": row["source_signal_id"] or payload.get("sourceSignalId"),
        "dataVersion": row["data_version"],
        "entityType": row["entity_type"],
        "entityId": row["entity_id"],
        "storeId": row["store_id"],
        "signalType": row["signal_type"],
        "signalStrength": row["signal_strength"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_signals(
    data_version: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> Dict[str, Any]:
    ensure_signal_pool_tables()
    clauses = []
    params: List[Any] = []
    if data_version:
        clauses.append("data_version = ?")
        params.append(data_version)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM signal_pool_v14 {where} ORDER BY created_at ASC LIMIT ?",
            [*params, limit],
        ).fetchall()
    items = [row_to_signal(row) for row in rows]
    by_type: Dict[str, int] = defaultdict(int)
    by_status: Dict[str, int] = defaultdict(int)
    for item in items:
        by_type[str(item.get("signalType"))] += 1
        by_status[str(item.get("status"))] += 1
    return {
        "version": SIGNAL_POOL_VERSION,
        "dataVersion": data_version,
        "signalCount": len(items),
        "byType": dict(by_type),
        "byStatus": dict(by_status),
        "signals": items,
    }


def _normalize_snapshot_signal(
    signal: Dict[str, Any],
    source_ref: str,
) -> Dict[str, Any]:
    return {
        **signal,
        "version": SIGNAL_POOL_VERSION,
        "sourceRef": source_ref,
        "status": AGENT_READY_STATUS,
        "rule": (
            "V21.5.2 Signal Pool accepts only one prebuilt, cross-validated "
            "fullProductBundle per product."
        ),
    }


def _purge_legacy_unscoped_rows(data_version: str | None) -> int:
    if not data_version:
        return 0
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM signal_pool_v14 "
            "WHERE data_version=? AND signal_id NOT LIKE 'PSIGV-%'",
            (data_version,),
        )
        conn.commit()
    return max(0, int(cursor.rowcount or 0))


def _resolve_signal_snapshot(
    data_version: str | None,
    *,
    signal_snapshot: Dict[str, Any] | None,
) -> tuple[Dict[str, Any], str]:
    if isinstance(signal_snapshot, dict) and signal_snapshot:
        return signal_snapshot, "provided_by_admission"
    persisted = product_signal_snapshot_service.get_product_signal_snapshot(data_version)
    if isinstance(persisted, dict) and persisted:
        return persisted, "persisted_enriched_snapshot"
    raise RuntimeError(
        "signal_snapshot_missing_before_signal_pool:"
        f"dataVersion={data_version or 'latest'}"
    )


def _validate_snapshot_contract(
    snapshot: Dict[str, Any],
    signals: List[Dict[str, Any]],
    *,
    data_version: str | None,
    snapshot_source: str,
) -> Dict[str, Any]:
    baseline = snapshot.get("baseline") if isinstance(snapshot.get("baseline"), dict) else {}
    baseline_only = bool(
        snapshot.get("baselineNoPrevious")
        or baseline.get("baselineNoPrevious")
    )
    if baseline_only:
        raise RuntimeError(
            "baseline_snapshot_must_not_enter_signal_pool:"
            f"dataVersion={data_version or 'latest'}"
        )

    invalid: List[str] = []
    for signal in signals:
        cross = signal.get("crossValidation")
        decision = cross.get("decision") if isinstance(cross, dict) else None
        if (
            not isinstance(cross, dict)
            or cross.get("version") != EXPECTED_EVIDENCE_VERSION
            or not isinstance(decision, dict)
            or not decision.get("status")
            or decision.get("status") == "missing"
        ):
            invalid.append(
                str(signal.get("productId") or signal.get("entityId") or "UNKNOWN")
            )

    if not signals or invalid:
        raise RuntimeError(
            "signal_snapshot_contract_invalid_v21_5:"
            f"dataVersion={data_version or 'latest'};"
            f"source={snapshot_source};snapshotVersion={snapshot.get('version')};"
            f"signalCount={len(signals)};invalidCount={len(invalid)};"
            f"sample={','.join(invalid[:5])}"
        )
    return {
        "ok": True,
        "contract": EXPECTED_EVIDENCE_CONTRACT,
        "version": EXPECTED_EVIDENCE_VERSION,
        "validatedSignalCount": len(signals),
    }


def generate_signal_pool(
    data_version: str | None = None,
    *,
    max_signals: int = 200,
    user_id: str | None = None,
    signal_snapshot: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    del user_id
    ensure_signal_pool_tables()
    resolved_snapshot, snapshot_source = _resolve_signal_snapshot(
        data_version,
        signal_snapshot=signal_snapshot,
    )
    source_ref = (
        resolved_snapshot.get("productSignalSnapshotRef")
        or resolved_snapshot.get("outputRef")
        or f"product_signal_snapshot:{data_version or 'latest'}"
    )
    values = (
        resolved_snapshot.get("productSignalPackages")
        or resolved_snapshot.get("signals")
        or []
    )
    raw_signals = [signal for signal in values if isinstance(signal, dict)]
    contract_validation = _validate_snapshot_contract(
        resolved_snapshot,
        raw_signals,
        data_version=data_version,
        snapshot_source=snapshot_source,
    )

    strength_rank = {"high": 0, "medium": 1, "low": 2, "normal": 3}
    raw_signals.sort(
        key=lambda item: (
            strength_rank.get(str(item.get("signalStrength")), 9),
            item.get("entityId") or "",
            item.get("metricCode") or "",
        )
    )
    selected = raw_signals[:max_signals]
    legacy_removed = _purge_legacy_unscoped_rows(data_version)
    saved = [
        _save_signal(_normalize_snapshot_signal(signal, source_ref))
        for signal in selected
    ]

    by_type: Dict[str, int] = defaultdict(int)
    by_strength: Dict[str, int] = defaultdict(int)
    by_status: Dict[str, int] = defaultdict(int)
    for signal in saved:
        by_type[str(signal.get("signalType"))] += 1
        by_strength[str(signal.get("signalStrength"))] += 1
        by_status[str(signal.get("status"))] += 1

    ref = f"signal_pool:{data_version or 'latest'}"
    return {
        "version": SIGNAL_POOL_VERSION,
        "mode": "strict_single_enriched_snapshot_data_version_scoped",
        "dataVersion": data_version,
        "productSnapshotCount": resolved_snapshot.get("productSnapshotCount", 0),
        "productSignalPackageCount": resolved_snapshot.get(
            "productSignalPackageCount",
            resolved_snapshot.get("productSignalCount", 0),
        ),
        "productSignalCount": resolved_snapshot.get("productSignalCount", 0),
        "taskSignalRef": ref,
        "outputRef": ref,
        "signalCount": len(saved),
        "createdTaskCount": 0,
        "legacyUnscopedRowsRemoved": legacy_removed,
        "signalIdScope": "data_version",
        "payloadContract": "explicit_nested_payload",
        "formalSignalArtifactStage": FORMAL_SIGNAL_ARTIFACT_STAGE,
        "signalSnapshotSource": snapshot_source,
        "snapshotRematerialized": False,
        "contractValidation": contract_validation,
        "byType": dict(by_type),
        "byStrength": dict(by_strength),
        "byStatus": dict(by_status),
        "signals": saved,
        "productSignalSnapshot": resolved_snapshot,
        "rule": (
            "V21.5.2 fails closed unless every non-baseline bundle carries "
            "crossValidation 21.5.0 and an operating decision; every accepted "
            "formal signal is materialized as an immutable ART-* signalRef."
        ),
    }