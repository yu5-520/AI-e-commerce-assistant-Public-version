"""V22.2.4 retirement of semantic payloads from ``pipeline_items``.

The migration stores every legacy row payload in Artifact Hub, attaches the current
stage reference, validates it, and only then clears the database payload column.
After a complete migration, SQLite triggers prevent any writer from persisting a
second full semantic copy beside the immutable artifact.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.artifact_transport_service import (
    merge_artifact_refs,
    pipeline_payload_artifact,
    validate_artifact,
)
from src.services.pipeline_item_service import (
    build_item_envelope,
    ensure_pipeline_item_tables,
)

PIPELINE_PAYLOAD_RETIREMENT_VERSION = "22.2.4"
_INSERT_TRIGGER = "trg_pipeline_items_reference_only_insert_v224"
_UPDATE_TRIGGER = "trg_pipeline_items_reference_only_update_v224"
_AGENT1_SIGNAL_STAGES = {"signal_admitted", "agent1_pending", "agent1_running"}


def _load_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        parsed = loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _legacy_payload(value: Any) -> Dict[str, Any]:
    raw = _load_mapping(value)
    nested = raw.get("payload") if isinstance(raw.get("payload"), dict) else None
    if nested is None:
        return raw
    result = {
        key: item
        for key, item in raw.items()
        if key not in {"payload", "artifactRefs", "payloadArtifactRef"}
    }
    result.update(nested)
    return result


def _row_envelope(row: Any, refs: Dict[str, Any], *, stage: str | None = None) -> Dict[str, Any]:
    return build_item_envelope(
        data_version=row["data_version"],
        item_id=row["item_id"],
        product_id=row["product_id"],
        store_id=row["store_id"],
        signal_id=row["signal_id"],
        package_id=row["package_id"],
        decision_id=row["decision_id"],
        task_id=row["task_id"],
        action_family=row["action_family"],
        route=row["route"],
        output_ref=row["output_ref"],
        stage=stage or row["current_stage"],
        artifact_refs=refs,
    )


def _current_ref(row: Any, refs: Dict[str, Any]) -> str | None:
    for value in (
        row["payload_artifact_ref"],
        refs.get("currentStageRef"),
    ):
        artifact_id = str(value or "").strip()
        if artifact_id.startswith("ART-"):
            return artifact_id
    return None


def _signal_payload_from_pool(row: Any) -> Dict[str, Any]:
    signal_id = str(row["signal_id"] or "").strip()
    if not signal_id:
        return {}
    with connect() as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_pool_v14'"
        ).fetchone()
        if not table:
            return {}
        signal_row = conn.execute(
            """
            SELECT payload FROM signal_pool_v14
            WHERE signal_id=?
               OR (source_signal_id=? AND COALESCE(data_version,'')=COALESCE(? ,''))
            ORDER BY updated_at DESC LIMIT 1
            """,
            (signal_id, signal_id, row["data_version"]),
        ).fetchone()
    return _load_mapping(signal_row["payload"]) if signal_row else {}


def _ensure_agent1_signal_ref(row: Any, refs: Dict[str, Any]) -> tuple[Dict[str, Any], str | None]:
    stage = str(row["current_stage"] or "")
    if stage not in _AGENT1_SIGNAL_STAGES:
        return refs, None
    existing = str(refs.get("signalRef") or "").strip()
    if existing:
        validation = validate_artifact(existing)
        if validation.get("ok") is True:
            return refs, None
        return refs, str(validation.get("status") or "signal_artifact_invalid")

    current_ref = _current_ref(row, refs)
    if stage == "signal_admitted" and current_ref:
        validation = validate_artifact(current_ref)
        if validation.get("ok") is True:
            return merge_artifact_refs(refs, {"signalRef": current_ref}), None

    signal_payload = _signal_payload_from_pool(row)
    if not signal_payload:
        candidate = _legacy_payload(row["payload"])
        if candidate.get("signalId") or candidate.get("signal_id"):
            signal_payload = candidate
    if not signal_payload:
        return refs, "agent1_signal_ref_migration_source_missing"

    transfer = pipeline_payload_artifact(
        envelope=_row_envelope(row, refs, stage="signal_admitted"),
        stage="signal_admitted",
        payload=signal_payload,
        station_id="v22_2_4_signal_ref_migration",
        previous_artifact_refs=refs,
    )
    signal_ref = str(transfer.get("payloadArtifactRef") or "")
    validation = validate_artifact(signal_ref)
    if validation.get("ok") is not True:
        return refs, str(validation.get("status") or "migrated_signal_artifact_invalid")
    return merge_artifact_refs(
        refs,
        transfer.get("artifactRefs"),
        {"signalRef": signal_ref},
    ), None


def remove_reference_only_payload_triggers() -> Dict[str, Any]:
    ensure_pipeline_item_tables()
    with connect() as conn:
        conn.execute(f"DROP TRIGGER IF EXISTS {_INSERT_TRIGGER}")
        conn.execute(f"DROP TRIGGER IF EXISTS {_UPDATE_TRIGGER}")
        conn.commit()
    return {
        "version": PIPELINE_PAYLOAD_RETIREMENT_VERSION,
        "installed": False,
        "triggers": [],
        "writeMode": "migration_not_sealed",
    }


def install_reference_only_payload_triggers() -> Dict[str, Any]:
    """Install a database-level guard so every writer obeys reference-only storage."""
    ensure_pipeline_item_tables()
    with connect() as conn:
        conn.execute(f"DROP TRIGGER IF EXISTS {_INSERT_TRIGGER}")
        conn.execute(f"DROP TRIGGER IF EXISTS {_UPDATE_TRIGGER}")
        conn.execute(
            f"""
            CREATE TRIGGER {_INSERT_TRIGGER}
            AFTER INSERT ON pipeline_items
            WHEN NEW.payload_artifact_ref LIKE 'ART-%'
              AND NEW.payload IS NOT NULL
              AND LENGTH(TRIM(NEW.payload)) > 0
            BEGIN
                UPDATE pipeline_items
                SET payload=NULL
                WHERE item_id=NEW.item_id;
            END
            """
        )
        conn.execute(
            f"""
            CREATE TRIGGER {_UPDATE_TRIGGER}
            AFTER UPDATE OF payload,payload_artifact_ref ON pipeline_items
            WHEN NEW.payload_artifact_ref LIKE 'ART-%'
              AND NEW.payload IS NOT NULL
              AND LENGTH(TRIM(NEW.payload)) > 0
            BEGIN
                UPDATE pipeline_items
                SET payload=NULL
                WHERE item_id=NEW.item_id;
            END
            """
        )
        conn.commit()
    return {
        "version": PIPELINE_PAYLOAD_RETIREMENT_VERSION,
        "installed": True,
        "triggers": [_INSERT_TRIGGER, _UPDATE_TRIGGER],
        "writeMode": "artifact_ref_only",
    }


def migrate_pipeline_payloads_to_artifacts(
    *,
    limit: int = 10000,
    fail_on_unmigrated: bool = False,
) -> Dict[str, Any]:
    """Migrate legacy payload rows without using payload as a runtime fallback."""
    ensure_pipeline_item_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM pipeline_items
            WHERE (payload IS NOT NULL AND LENGTH(TRIM(payload)) > 0)
               OR COALESCE(payload_artifact_ref,'') = ''
               OR COALESCE(artifact_refs_json,'') = ''
               OR (
                    current_stage IN ('signal_admitted','agent1_pending','agent1_running')
                    AND COALESCE(artifact_refs_json,'') NOT LIKE '%signalRef%'
               )
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (max(1, min(100000, int(limit or 10000))),),
        ).fetchall()

    migrated = 0
    signal_refs_migrated = 0
    stripped = 0
    already_reference_only = 0
    failures: List[Dict[str, Any]] = []

    for row in rows:
        refs = _load_mapping(row["artifact_refs_json"])
        artifact_id = _current_ref(row, refs)
        if artifact_id:
            validation = validate_artifact(artifact_id)
            if validation.get("ok") is not True:
                failures.append(
                    {
                        "itemId": row["item_id"],
                        "stage": row["current_stage"],
                        "code": validation.get("status") or "artifact_invalid",
                        "artifactId": artifact_id,
                        "existingRefRepairFromPayloadAllowed": False,
                    }
                )
                continue
        else:
            payload = _legacy_payload(row["payload"])
            if not payload:
                failures.append(
                    {
                        "itemId": row["item_id"],
                        "stage": row["current_stage"],
                        "code": "unmigrated_payload_and_artifact_ref_missing",
                        "artifactId": None,
                    }
                )
                continue
            transfer = pipeline_payload_artifact(
                envelope=_row_envelope(row, refs),
                stage=str(row["current_stage"] or "unknown_stage"),
                payload=payload,
                station_id="v22_2_4_payload_retirement",
                previous_artifact_refs=refs,
            )
            artifact_id = str(transfer.get("payloadArtifactRef") or "")
            refs = merge_artifact_refs(refs, transfer.get("artifactRefs"))
            validation = validate_artifact(artifact_id)
            if validation.get("ok") is not True:
                failures.append(
                    {
                        "itemId": row["item_id"],
                        "stage": row["current_stage"],
                        "code": validation.get("status") or "migrated_artifact_invalid",
                        "artifactId": artifact_id,
                    }
                )
                continue
            migrated += 1

        refs_before_signal = dict(refs)
        refs, signal_error = _ensure_agent1_signal_ref(row, refs)
        if signal_error:
            failures.append(
                {
                    "itemId": row["item_id"],
                    "stage": row["current_stage"],
                    "code": signal_error,
                    "artifactId": artifact_id,
                    "failureOwner": "artifact_transport",
                }
            )
            continue
        if refs.get("signalRef") and refs.get("signalRef") != refs_before_signal.get("signalRef"):
            signal_refs_migrated += 1

        with connect() as conn:
            conn.execute(
                """
                UPDATE pipeline_items
                SET artifact_refs_json=?, payload_artifact_ref=?, payload=NULL,
                    last_error_code=CASE
                        WHEN last_error_code='legacy_payload_runtime_retired'
                        THEN NULL ELSE last_error_code END,
                    updated_at=?
                WHERE item_id=?
                """,
                (
                    dumps(refs),
                    artifact_id,
                    datetime.now().isoformat(),
                    row["item_id"],
                ),
            )
            conn.commit()
        if row["payload"] not in (None, ""):
            stripped += 1
        else:
            already_reference_only += 1

    trigger_result = (
        install_reference_only_payload_triggers()
        if not failures
        else remove_reference_only_payload_triggers()
    )
    status = payload_retirement_status()
    result = {
        "version": PIPELINE_PAYLOAD_RETIREMENT_VERSION,
        "candidateCount": len(rows),
        "migratedArtifactCount": migrated,
        "migratedSignalRefCount": signal_refs_migrated,
        "strippedPayloadCount": stripped,
        "alreadyReferenceOnlyCount": already_reference_only,
        "failedCount": len(failures),
        "failures": failures[:100],
        "triggerInstallation": trigger_result,
        "status": status,
        "runtimeLegacyPayloadFallbackAllowed": False,
        "runtimeSignalPoolFallbackAllowed": False,
        "existingInvalidRefRepairFromPayloadAllowed": False,
    }
    if fail_on_unmigrated and (failures or not status.get("sealed")):
        raise RuntimeError(f"pipeline_payload_retirement_incomplete:{result}")
    return result


def payload_retirement_status() -> Dict[str, Any]:
    ensure_pipeline_item_tables()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_items,
                SUM(CASE WHEN payload IS NOT NULL AND LENGTH(TRIM(payload)) > 0 THEN 1 ELSE 0 END) AS payload_rows,
                SUM(CASE WHEN payload_artifact_ref LIKE 'ART-%' THEN 1 ELSE 0 END) AS reference_rows,
                SUM(CASE WHEN COALESCE(payload_artifact_ref,'') = '' THEN 1 ELSE 0 END) AS missing_reference_rows,
                SUM(CASE
                    WHEN current_stage IN ('signal_admitted','agent1_pending','agent1_running')
                     AND COALESCE(artifact_refs_json,'') NOT LIKE '%signalRef%'
                    THEN 1 ELSE 0 END
                ) AS missing_signal_ref_rows
            FROM pipeline_items
            """
        ).fetchone()
        trigger_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name IN (?,?)",
            (_INSERT_TRIGGER, _UPDATE_TRIGGER),
        ).fetchall()
    total = int(row["total_items"] or 0) if row else 0
    payload_rows = int(row["payload_rows"] or 0) if row else 0
    reference_rows = int(row["reference_rows"] or 0) if row else 0
    missing_reference_rows = int(row["missing_reference_rows"] or 0) if row else 0
    missing_signal_ref_rows = int(row["missing_signal_ref_rows"] or 0) if row else 0
    installed = {item["name"] for item in trigger_rows}
    triggers_ready = {_INSERT_TRIGGER, _UPDATE_TRIGGER}.issubset(installed)
    return {
        "version": PIPELINE_PAYLOAD_RETIREMENT_VERSION,
        "totalItemCount": total,
        "referenceOnlyItemCount": reference_rows,
        "semanticPayloadRowCount": payload_rows,
        "missingArtifactReferenceCount": missing_reference_rows,
        "missingAgent1SignalRefCount": missing_signal_ref_rows,
        "triggersInstalled": triggers_ready,
        "writeMode": "artifact_ref_only" if triggers_ready else "migration_not_sealed",
        "readMode": "artifact_ref_only",
        "legacyPayloadRuntimeFallbackAllowed": False,
        "legacySignalPoolFallbackAllowed": False,
        "sealed": (
            payload_rows == 0
            and missing_reference_rows == 0
            and missing_signal_ref_rows == 0
            and triggers_ready
        ),
    }


__all__ = [
    "PIPELINE_PAYLOAD_RETIREMENT_VERSION",
    "remove_reference_only_payload_triggers",
    "install_reference_only_payload_triggers",
    "migrate_pipeline_payloads_to_artifacts",
    "payload_retirement_status",
]
