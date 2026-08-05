"""V12.14.2 Ops Diagnostic Train.

The ops train does not carry real business data. It checks station contracts and
also checks whether deprecated files leaked back into the mainline.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from time import perf_counter
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, ensure_columns
from src.services.deprecated_station_registry_service import DEPRECATED_STATION_VERSION, mainline_purity_check
from src.services.station_contract_service import STATION_CONTRACT_VERSION, run_station_contract, station_health, validate_contract_payload
from src.services.station_registry_service import STATION_REGISTRY_VERSION, get_station, list_stations

OPS_DIAGNOSTIC_TRAIN_VERSION = "12.14.2"


def now_iso() -> str:
    return datetime.now().isoformat()


def make_run_id() -> str:
    return f"OPS-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"


def ensure_ops_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ops_diagnostic_runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                mode TEXT,
                diagnostic_data_version TEXT,
                station_count INTEGER DEFAULT 0,
                failed_stage TEXT,
                summary TEXT,
                payload TEXT,
                started_at TEXT,
                finished_at TEXT,
                duration_ms INTEGER DEFAULT 0,
                created_by TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ops_station_checks (
                check_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                station_id TEXT,
                stage TEXT,
                status TEXT,
                duration_ms INTEGER DEFAULT 0,
                input_contract_status TEXT,
                output_contract_status TEXT,
                gate_written INTEGER DEFAULT 0,
                next_station_readable INTEGER DEFAULT 0,
                error_message TEXT,
                warning_message TEXT,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(conn, "ops_diagnostic_runs", {"diagnostic_data_version": "TEXT", "payload": "TEXT", "created_by": "TEXT"})
        ensure_columns(conn, "ops_station_checks", {"warning_message": "TEXT", "payload": "TEXT"})
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_station_checks_run ON ops_station_checks(run_id, station_id)")
        conn.commit()


def _write_run(run: Dict[str, Any]) -> None:
    ensure_ops_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO ops_diagnostic_runs (
                run_id, status, mode, diagnostic_data_version, station_count, failed_stage,
                summary, payload, started_at, finished_at, duration_ms, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.get("runId"),
                run.get("status"),
                run.get("mode"),
                run.get("diagnosticDataVersion"),
                len(run.get("stations") or []),
                run.get("failedStage"),
                run.get("summary"),
                json.dumps(run, ensure_ascii=False),
                run.get("startedAt"),
                run.get("finishedAt"),
                int(run.get("totalDurationMs") or 0),
                run.get("createdBy"),
            ),
        )
        for check in run.get("stations") or []:
            conn.execute(
                """
                INSERT OR REPLACE INTO ops_station_checks (
                    check_id, run_id, station_id, stage, status, duration_ms,
                    input_contract_status, output_contract_status, gate_written,
                    next_station_readable, error_message, warning_message, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    check.get("checkId"),
                    run.get("runId"),
                    check.get("stationId"),
                    check.get("stage"),
                    check.get("status"),
                    int(check.get("durationMs") or 0),
                    check.get("inputContract"),
                    check.get("outputContract"),
                    1 if check.get("gateWritten") else 0,
                    1 if check.get("nextStationReadable") else 0,
                    check.get("errorMessage"),
                    check.get("warningMessage"),
                    json.dumps(check, ensure_ascii=False),
                    now_iso(),
                ),
            )
        conn.commit()


def _read_run(row: Any) -> Dict[str, Any]:
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}
    return payload or {"runId": row["run_id"], "status": row["status"], "mode": row["mode"], "failedStage": row["failed_stage"], "summary": row["summary"]}


def station_health_summary() -> Dict[str, Any]:
    stations = []
    for station in list_stations():
        health = station_health(station["stationId"])
        stations.append({
            "stationId": station["stationId"],
            "stage": station["stage"],
            "title": station["title"],
            "status": health.get("status"),
            "moduleImportOk": health.get("moduleImportOk"),
            "nextStation": station.get("nextStation"),
        })
    deprecated_check = mainline_purity_check()
    failed = [item for item in stations if item.get("status") not in {"healthy", "ok"}]
    if deprecated_check.get("status") == "blocked":
        failed.append({"stationId": "deprecated_station_archive", "status": "blocked"})
    return {
        "version": OPS_DIAGNOSTIC_TRAIN_VERSION,
        "registryVersion": STATION_REGISTRY_VERSION,
        "contractVersion": STATION_CONTRACT_VERSION,
        "deprecatedStationVersion": DEPRECATED_STATION_VERSION,
        "status": "healthy" if not failed else "degraded",
        "failedCount": len(failed),
        "stations": stations,
        "deprecatedMainlineCheck": deprecated_check,
        "rule": "系统页读取站点健康和废弃站点回流检查，不触发真实业务数据流。",
    }


def _deprecated_check_item(run_id: str) -> Dict[str, Any]:
    t0 = perf_counter()
    check = mainline_purity_check()
    status = "passed" if check.get("status") == "clean" else "failed"
    return {
        "checkId": f"{run_id}:deprecated_station_archive",
        "stationId": "deprecated_station_archive",
        "stage": "deprecated_mainline_leak",
        "title": "废弃站点回流检查",
        "status": status,
        "durationMs": int((perf_counter() - t0) * 1000),
        "inputContract": "passed",
        "outputContract": "passed" if status == "passed" else "failed",
        "gateWritten": False,
        "nextStationReadable": True,
        "warningMessage": f"{check.get('warningCount', 0)} legacy adapter warnings",
        "errorMessage": None if status == "passed" else f"{check.get('violationCount', 0)} deprecated mainline violations",
        "details": check,
    }


def run_ops_train(mode: str = "contract", created_by: str | None = None) -> Dict[str, Any]:
    ensure_ops_tables()
    run_id = make_run_id()
    diagnostic_data_version = f"DIAG-{run_id}"
    started = now_iso()
    t0 = perf_counter()
    checks: List[Dict[str, Any]] = []
    failed_stage = None
    previous_output_ref = None

    deprecated_check = _deprecated_check_item(run_id)
    checks.append(deprecated_check)
    if deprecated_check["status"] == "failed":
        failed_stage = "deprecated_mainline_leak"
        if mode == "stop_on_failure":
            run = _finalize_run(run_id, mode, diagnostic_data_version, started, t0, created_by, checks, failed_stage)
            _write_run(run)
            return run

    for station in list_stations():
        station_t0 = perf_counter()
        sid = station["stationId"]
        payload = {
            "dataVersion": diagnostic_data_version,
            "source": "ops_diagnostic_train",
            "rawReportRef": previous_output_ref or f"diagnostic_raw:{diagnostic_data_version}",
            "parsedRowsRef": previous_output_ref or f"diagnostic_rows:{diagnostic_data_version}",
            "metricFactRef": previous_output_ref or f"diagnostic_metric_facts:{diagnostic_data_version}",
            "operatingObjectRef": previous_output_ref or f"diagnostic_objects:{diagnostic_data_version}",
            "snapshotRef": previous_output_ref or f"diagnostic_snapshot:{diagnostic_data_version}",
            "taskSignalRef": previous_output_ref or f"diagnostic_tasks:{diagnostic_data_version}",
            "taskId": "DIAG-TASK",
            "submitterId": created_by or "OPS",
            "evidenceRef": previous_output_ref or f"diagnostic_evidence:{diagnostic_data_version}",
            "recapRef": previous_output_ref or f"diagnostic_recap:{diagnostic_data_version}",
            "reviewStatus": "diagnostic",
            "upstreamStage": station.get("stage"),
            "isDiagnostic": True,
            "userId": created_by or "OPS",
        }
        try:
            result = run_station_contract(sid, payload, diagnostic=True)
            previous_output_ref = (result.get("output") or {}).get("outputRef") or previous_output_ref
            input_status = (result.get("inputContract") or {}).get("status")
            output_status = (result.get("outputContract") or {}).get("status")
            gate_written = bool(result.get("gate"))
            status = "passed" if result.get("ok") and gate_written else "failed"
            warning = None
            if input_status == "warning" or output_status == "warning":
                status = "warning" if status == "passed" else status
                warning = "contract warning"
            check = {
                "checkId": f"{run_id}:{sid}",
                "stationId": sid,
                "stage": station["stage"],
                "title": station["title"],
                "status": status,
                "durationMs": int((perf_counter() - station_t0) * 1000),
                "inputContract": input_status,
                "outputContract": output_status,
                "gateWritten": gate_written,
                "nextStationReadable": bool(previous_output_ref),
                "outputRef": previous_output_ref,
                "warningMessage": warning,
                "errorMessage": result.get("error"),
            }
        except Exception as exc:
            check = {"checkId": f"{run_id}:{sid}", "stationId": sid, "stage": station["stage"], "title": station["title"], "status": "failed", "durationMs": int((perf_counter() - station_t0) * 1000), "inputContract": "failed", "outputContract": "failed", "gateWritten": False, "nextStationReadable": False, "errorMessage": str(exc)}
        checks.append(check)
        if check["status"] == "failed" and not failed_stage:
            failed_stage = check["stage"]
            if mode == "stop_on_failure":
                break

    run = _finalize_run(run_id, mode, diagnostic_data_version, started, t0, created_by, checks, failed_stage)
    _write_run(run)
    return run


def _finalize_run(run_id: str, mode: str, diagnostic_data_version: str, started: str, t0: float, created_by: str | None, checks: List[Dict[str, Any]], failed_stage: str | None) -> Dict[str, Any]:
    status = "passed" if not failed_stage and all(item["status"] in {"passed", "warning"} for item in checks) else "failed"
    if status == "passed" and any(item["status"] == "warning" for item in checks):
        status = "warning"
    return {
        "version": OPS_DIAGNOSTIC_TRAIN_VERSION,
        "runId": run_id,
        "status": status,
        "mode": mode,
        "diagnosticDataVersion": diagnostic_data_version,
        "failedStage": failed_stage,
        "totalDurationMs": int((perf_counter() - t0) * 1000),
        "startedAt": started,
        "finishedAt": now_iso(),
        "createdBy": created_by or "OPS",
        "summary": "运维火车巡检完成" if status in {"passed", "warning"} else f"运维火车发现断点：{failed_stage}",
        "stations": checks,
        "rule": "运维火车只跑 diagnostic data version，并检查 deprecated 文件是否回流主线。",
    }


def latest_ops_train() -> Dict[str, Any]:
    ensure_ops_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM ops_diagnostic_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return {"version": OPS_DIAGNOSTIC_TRAIN_VERSION, "status": "missing", "message": "No diagnostic train run yet."}
    return _read_run(row)


def list_ops_runs(limit: int = 20) -> Dict[str, Any]:
    ensure_ops_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM ops_diagnostic_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    runs = [_read_run(row) for row in rows]
    return {"version": OPS_DIAGNOSTIC_TRAIN_VERSION, "runs": runs, "runCount": len(runs)}


def get_ops_run(run_id: str) -> Dict[str, Any]:
    ensure_ops_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM ops_diagnostic_runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        return {"version": OPS_DIAGNOSTIC_TRAIN_VERSION, "status": "not_found", "runId": run_id}
    return _read_run(row)


def check_single_station(station_id: str, created_by: str | None = None) -> Dict[str, Any]:
    if station_id == "deprecated_station_archive":
        return {"version": OPS_DIAGNOSTIC_TRAIN_VERSION, "stationId": station_id, "result": mainline_purity_check()}
    station = get_station(station_id)
    if not station:
        return {"version": OPS_DIAGNOSTIC_TRAIN_VERSION, "status": "failed", "stationId": station_id, "error": "station_not_found"}
    payload = {"dataVersion": f"DIAG-SINGLE-{station_id}", "source": "ops_station_check", "userId": created_by or "OPS", "isDiagnostic": True}
    for key in ["rawReportRef", "parsedRowsRef", "metricFactRef", "operatingObjectRef", "snapshotRef", "taskSignalRef", "taskId", "submitterId", "evidenceRef", "recapRef", "reviewStatus"]:
        payload.setdefault(key, f"diagnostic:{station_id}:{key}")
    input_check = validate_contract_payload(station_id, payload, direction="input")
    result = run_station_contract(station_id, payload, diagnostic=True)
    return {"version": OPS_DIAGNOSTIC_TRAIN_VERSION, "stationId": station_id, "inputContract": input_check, "result": result}
