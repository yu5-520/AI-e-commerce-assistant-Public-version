"""Repair V20 queue rows whose outer status said completed while inner run failed."""
from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads
from src.services.station_queue_service import TASK_GENERATION_SEQUENCE, ensure_queue_tables, now_iso

STATION_TRUTH_REPAIR_VERSION = "22.2.5"
_SEQUENCE_INDEX = {station: index for index, (station, _stage) in enumerate(TASK_GENERATION_SEQUENCE)}
_STAGE_BY_STATION = {station: stage for station, stage in TASK_GENERATION_SEQUENCE}


def _payload(row: Any) -> Dict[str, Any]:
    try:
        value = loads(row["payload"]) if row["payload"] else {}
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def repair_fake_completed_station_runs(*, limit: int = 200) -> Dict[str, Any]:
    ensure_queue_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM station_queue
            WHERE status='completed'
            ORDER BY updated_at DESC LIMIT ?
            """,
            (max(1, min(5000, int(limit or 200))),),
        ).fetchall()

    mismatches: List[Dict[str, Any]] = []
    for row in rows:
        payload = _payload(row)
        run = payload.get("stationRun") if isinstance(payload.get("stationRun"), dict) else {}
        if run.get("status") != "failed":
            continue
        mismatches.append(
            {
                "stationJobId": row["station_job_id"],
                "parentJobId": row["parent_job_id"],
                "dataVersion": row["data_version"],
                "stationId": row["station_id"],
                "stage": row["stage"],
                "error": run.get("error") or row["error_message"] or "historical_inner_run_failed",
                "sequenceIndex": _SEQUENCE_INDEX.get(row["station_id"], 999),
            }
        )

    first_by_job: Dict[str, Dict[str, Any]] = {}
    for item in mismatches:
        current = first_by_job.get(item["parentJobId"])
        if current is None or item["sequenceIndex"] < current["sequenceIndex"]:
            first_by_job[item["parentJobId"]] = item

    repaired: List[Dict[str, Any]] = []
    deleted_false_gate_count = 0
    now = now_iso()
    with connect() as conn:
        for parent_job_id, first in first_by_job.items():
            conn.execute(
                """
                UPDATE station_queue
                SET status='retry', output_ref=NULL, locked_by=NULL, locked_until=NULL,
                    error_message='V22.2.5 replay: historical outer completed / inner failed',
                    updated_at=?
                WHERE station_job_id=?
                """,
                (now, first["stationJobId"]),
            )
            downstream = [
                station
                for station, _stage in TASK_GENERATION_SEQUENCE
                if _SEQUENCE_INDEX.get(station, 999) > first["sequenceIndex"]
            ]
            if downstream:
                placeholders = ",".join("?" for _ in downstream)
                conn.execute(
                    f"""
                    UPDATE station_queue
                    SET status='disabled', error_message='V22.2.5 removed downstream job after upstream truth failure',
                        locked_by=NULL, locked_until=NULL, updated_at=?
                    WHERE parent_job_id=? AND station_id IN ({placeholders})
                    """,
                    (now, parent_job_id, *downstream),
                )
            conn.execute(
                """
                UPDATE pipeline_jobs
                SET status='running', current_station=?, output_ref=NULL,
                    error_message='V22.2.5 replay from first real failed station', updated_at=?
                WHERE job_id=?
                """,
                (first["stationId"], now, parent_job_id),
            )

            stages_to_remove = [
                stage
                for station, stage in TASK_GENERATION_SEQUENCE
                if _SEQUENCE_INDEX.get(station, 999) >= first["sequenceIndex"]
            ]
            if first.get("stage") and first["stage"] not in stages_to_remove:
                stages_to_remove.insert(0, first["stage"])
            if stages_to_remove:
                gate_placeholders = ",".join("?" for _ in stages_to_remove)
                gate_cursor = conn.execute(
                    f"""
                    DELETE FROM pipeline_stage_gates
                    WHERE data_version=? AND stage IN ({gate_placeholders})
                      AND status='completed'
                      AND COALESCE(output_ref,'') NOT LIKE 'ART-%'
                    """,
                    (first["dataVersion"], *stages_to_remove),
                )
                deleted_false_gate_count += max(0, int(gate_cursor.rowcount or 0))
            repaired.append(
                {
                    **first,
                    "disabledDownstreamStations": downstream,
                    "removedFalseGateStages": stages_to_remove,
                }
            )
        conn.commit()

    return {
        "version": STATION_TRUTH_REPAIR_VERSION,
        "mismatchCount": len(mismatches),
        "repairedJobCount": len(repaired),
        "deletedFalseCompletedGateCount": deleted_false_gate_count,
        "repaired": repaired,
        "replayFromFirstRealFailure": True,
        "allDownstreamFalseGatesRemoved": True,
        "legacyPayloadFallbackRestored": False,
        "rule": "Only queue rows proven completed/failed contradictory are replayed; immutable source artifacts are preserved.",
    }


__all__ = ["STATION_TRUTH_REPAIR_VERSION", "repair_fake_completed_station_runs"]
