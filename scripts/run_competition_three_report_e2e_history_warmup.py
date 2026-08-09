#!/usr/bin/env python3
"""Warm historical reports and prove the first-report baseline gate.

The first real scenario report must complete the eight deterministic pre-Agent
stations but must create zero Signal Pool rows and zero Agent1 work. The second
historical report may create comparison Signals. Before the real scenario starts,
the exact same first-report import request is submitted twice without a sleep to
prove fresh run identity, then runtime state is reset.

The wrapper also times the first explicit Agent pipeline tick after the third import.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_competition_three_report_e2e as base  # noqa: E402
import run_competition_three_report_e2e_compat as compat  # noqa: E402


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _response_data_version(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    direct = str(value.get("dataVersion") or "")
    if direct:
        return direct
    versions = value.get("dataVersions")
    if isinstance(versions, list) and versions:
        return str(versions[-1] or "")
    return ""


def _response_content_hash(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in (
        "sourceContentHash",
        "source_content_hash",
        "contentHash",
        "content_hash",
        "sourceHash",
    ):
        text = str(value.get(key) or "").strip()
        if text:
            return text
    return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone() is not None


def _first_report_baseline_probe(
    database_path: Path,
    data_version: str,
) -> dict[str, Any]:
    """Inspect persisted state immediately after the eight pre-Agent stations."""
    probe: dict[str, Any] = {
        "schema": "competition.first_report_baseline_gate.v1",
        "dataVersion": data_version,
        "verified": False,
        "signalPoolCount": 0,
        "agent1ItemCount": 0,
        "agent1StageCounts": {},
        "admissionOutputSummary": {},
        "assertions": {},
    }
    conn = sqlite3.connect(str(database_path))
    conn.row_factory = sqlite3.Row
    try:
        if _table_exists(conn, "signal_pool_v14"):
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM signal_pool_v14 WHERE COALESCE(data_version,'')=COALESCE(?, '')",
                (data_version,),
            ).fetchone()
            probe["signalPoolCount"] = int(row["c"] or 0) if row else 0

        if _table_exists(conn, "pipeline_items"):
            rows = conn.execute(
                """
                SELECT current_stage,COUNT(*) AS c
                FROM pipeline_items
                WHERE COALESCE(data_version,'')=COALESCE(?, '')
                  AND current_stage IN (
                    'agent1_pending','agent1_running','agent1_completed',
                    'agent1_failed','agent1_output_invalid','agent1_decision_unresolved'
                  )
                GROUP BY current_stage
                """,
                (data_version,),
            ).fetchall()
            stage_counts = {str(row["current_stage"]): int(row["c"] or 0) for row in rows}
            probe["agent1StageCounts"] = stage_counts
            probe["agent1ItemCount"] = sum(stage_counts.values())

        admission_summary: dict[str, Any] = {}
        if _table_exists(conn, "station_queue"):
            row = conn.execute(
                """
                SELECT payload,status
                FROM station_queue
                WHERE COALESCE(data_version,'')=COALESCE(?, '')
                  AND station_id='product_signal_admission_station'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (data_version,),
            ).fetchone()
            if row and row["payload"]:
                payload = json.loads(row["payload"])
                station_run = payload.get("stationRun") if isinstance(payload, dict) else {}
                admission_summary = (
                    station_run.get("outputSummary")
                    if isinstance(station_run, dict)
                    and isinstance(station_run.get("outputSummary"), dict)
                    else {}
                )
                probe["admissionStationStatus"] = row["status"]
        probe["admissionOutputSummary"] = admission_summary

        assertions = {
            "signalPoolEmpty": probe["signalPoolCount"] == 0,
            "agent1NotCreated": probe["agent1ItemCount"] == 0,
            "admissionStationCompleted": probe.get("admissionStationStatus") == "completed",
            "baselineOnly": admission_summary.get("baselineOnly") is True,
            "signalEligibilityClosed": admission_summary.get("signalEligibility") is False,
            "fullSignalCountZero": int(admission_summary.get("fullSignalCount") or 0) == 0,
            "generatedSignalCountZero": int(admission_summary.get("generatedSignalCount") or 0) == 0,
            "admittedSignalCountZero": int(admission_summary.get("admittedSignalCount") or 0) == 0,
            "observedSignalCountZero": int(admission_summary.get("observedSignalCount") or 0) == 0,
            "agent1PendingZero": int(admission_summary.get("agent1PendingItemCount") or 0) == 0,
        }
        probe["assertions"] = assertions
        probe["verified"] = all(assertions.values())
        return probe
    finally:
        conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = base.parse_args(argv)
    scenario_path = Path(args.scenario).expanduser().resolve()
    scenario_digest = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
    candidate_id = f"{args.source_commit.strip()[:12]}-{scenario_digest[:12]}"
    database_path = (
        Path(args.candidate_base).expanduser()
        / candidate_id
        / "state"
        / "logs"
        / "product_workbench.sqlite3"
    )

    os.environ["STATION_QUEUE_WORKER_ENABLED"] = "false"
    os.environ["AGENT_PIPELINE_ITEM_WORKER_ENABLED"] = "true"
    os.environ["STATION_QUEUE_WORKER_MAX_JOBS_PER_TICK"] = "12"
    base.SCRIPT_DIR = SCRIPTS_DIR / "competition_e2e_compat_runtime"
    base.query_runtime_database = compat.query_runtime_database

    original_http_json = base.http_json
    import_count = 0
    history_warmups: list[dict[str, Any]] = []
    first_report_baseline_probe: dict[str, Any] = {
        "schema": "competition.first_report_baseline_gate.v1",
        "attempted": False,
        "verified": False,
    }
    fresh_upload_probe: dict[str, Any] = {
        "schema": "competition.fresh_upload_probe.v1",
        "attempted": False,
        "verified": False,
        "sameRequestContent": False,
        "freshDataVersion": False,
        "sourceContentHashStableWhenExposed": None,
        "errors": [],
    }
    worker_handoff_probe: dict[str, Any] = {
        "schema": "competition.worker_handoff_probe.v1",
        "mode": "explicit_single_worker_tick_path",
        "attempted": False,
        "verified": False,
        "firstTickDurationSeconds": None,
        "thresholdSeconds": 300.0,
        "autonomousBackgroundHandoffVerified": False,
        "errors": [],
    }
    fresh_probe_done = False
    main_tick_measured = False

    def controlled_http_json(*request_args: Any, **request_kwargs: Any) -> tuple[int, Any]:
        nonlocal import_count, fresh_probe_done, main_tick_measured, first_report_baseline_probe
        method = str(
            request_args[0] if request_args else request_kwargs.get("method") or ""
        )
        url = str(
            request_args[1] if len(request_args) > 1 else request_kwargs.get("url") or ""
        )

        if (
            method.upper() == "POST"
            and "/api/data/import/confirm" in url
            and not fresh_probe_done
        ):
            payload = request_kwargs.get("payload")
            if payload is None and len(request_args) > 2:
                payload = request_args[2]
            request_hash = _canonical_hash(payload)
            fresh_upload_probe.update(attempted=True, requestContentHash=request_hash)
            probe_results: list[dict[str, Any]] = []
            try:
                for attempt in (1, 2):
                    started = time.monotonic()
                    probe_status, probe_response = original_http_json(*request_args, **request_kwargs)
                    duration = round(time.monotonic() - started, 6)
                    version = _response_data_version(probe_response)
                    if not version:
                        raise base.ThreeReportE2EError(
                            f"FRESH_UPLOAD_PROBE_DATA_VERSION_MISSING:{attempt}:{probe_response}"
                        )
                    probe_results.append(
                        {
                            "attempt": attempt,
                            "httpStatus": probe_status,
                            "dataVersion": version,
                            "durationSeconds": duration,
                            "sourceContentHash": _response_content_hash(probe_response),
                            "requestContentHash": request_hash,
                        }
                    )
                versions = [item["dataVersion"] for item in probe_results]
                source_hashes = [item.get("sourceContentHash") for item in probe_results if item.get("sourceContentHash")]
                fresh_upload_probe["results"] = probe_results
                fresh_upload_probe["sameRequestContent"] = len({item["requestContentHash"] for item in probe_results}) == 1
                fresh_upload_probe["freshDataVersion"] = len(versions) == 2 and len(set(versions)) == 2
                if len(source_hashes) == 2:
                    fresh_upload_probe["sourceContentHashStableWhenExposed"] = len(set(source_hashes)) == 1
                fresh_upload_probe["verified"] = bool(
                    fresh_upload_probe["sameRequestContent"]
                    and fresh_upload_probe["freshDataVersion"]
                    and fresh_upload_probe["sourceContentHashStableWhenExposed"] is not False
                )
                if fresh_upload_probe["verified"] is not True:
                    raise base.ThreeReportE2EError(
                        "FRESH_UPLOAD_IDENTITY_REUSED:"
                        + json.dumps(fresh_upload_probe, ensure_ascii=False, sort_keys=True)
                    )
                app_url = url.split("/api/data/import/confirm", 1)[0]
                original_http_json(
                    "POST",
                    app_url + "/api/system/reset-runtime-data?confirm=true&include_audit_logs=true&scope=demo",
                    headers=base.USER_HEADERS,
                    timeout=60.0,
                )
                fresh_upload_probe["probeStateResetBeforeScenario"] = True
            except Exception as exc:
                fresh_upload_probe.setdefault("errors", []).append(f"{type(exc).__name__}:{exc}")
                raise
            finally:
                fresh_probe_done = True

        if (
            method.upper() == "POST"
            and "/api/system/run-agent-pipeline-tick" in url
            and import_count >= 3
            and not main_tick_measured
        ):
            worker_handoff_probe["attempted"] = True
            started = time.monotonic()
            status, response = original_http_json(*request_args, **request_kwargs)
            duration = round(time.monotonic() - started, 6)
            worker_handoff_probe["firstTickDurationSeconds"] = duration
            worker_handoff_probe["httpStatus"] = status
            worker_handoff_probe["tickRan"] = response.get("ran") if isinstance(response, dict) else None
            worker_handoff_probe["verified"] = duration < float(worker_handoff_probe["thresholdSeconds"])
            if worker_handoff_probe["verified"] is not True:
                error = f"EXPLICIT_WORKER_HANDOFF_EXCEEDED_THRESHOLD:{duration}>={worker_handoff_probe['thresholdSeconds']}"
                worker_handoff_probe["errors"].append(error)
                raise base.ThreeReportE2EError(error)
            main_tick_measured = True
            return status, response

        status, response = original_http_json(*request_args, **request_kwargs)
        if method.upper() != "POST" or "/api/data/import/confirm" not in url:
            return status, response

        import_count += 1
        if import_count > 2 or not isinstance(response, dict):
            return status, response
        version = str(response.get("dataVersion") or "")
        if not version:
            raise base.ThreeReportE2EError(f"HISTORY_IMPORT_DATA_VERSION_MISSING:{import_count}")

        app_url = url.split("/api/data/import/confirm", 1)[0]
        tick_started = time.monotonic()
        _tick_status, tick = original_http_json(
            "POST",
            app_url + "/api/system/run-agent-pipeline-tick?limit=8",
            headers=base.USER_HEADERS,
            timeout=240.0,
        )
        tick_duration = round(time.monotonic() - tick_started, 6)
        if not isinstance(tick, dict) or tick.get("ran") is not True:
            raise base.ThreeReportE2EError(f"HISTORY_PRE_AGENT_WARMUP_FAILED:{version}:{tick}")

        if import_count == 1:
            first_report_baseline_probe = _first_report_baseline_probe(database_path, version)
            first_report_baseline_probe["attempted"] = True
            if first_report_baseline_probe.get("verified") is not True:
                raise base.ThreeReportE2EError(
                    "FIRST_REPORT_BASELINE_GATE_FAILED:"
                    + json.dumps(first_report_baseline_probe, ensure_ascii=False, sort_keys=True)
                )

        compat._history_only(database_path, version)
        history_warmups.append(
            {
                "dataVersion": version,
                "importIndex": import_count,
                "preAgentTickRan": True,
                "preAgentTickDurationSeconds": tick_duration,
                "firstReportBaselineVerified": (
                    first_report_baseline_probe.get("verified") if import_count == 1 else None
                ),
                "historyOnlyApplied": True,
                "rule": "eight_pre_agent_stations_completed_before_agent1_suppression",
            }
        )
        return status, response

    base.http_json = controlled_http_json
    try:
        return base.main(argv)
    finally:
        output = Path(args.output).expanduser().resolve()
        if output.is_file():
            try:
                report = json.loads(output.read_text(encoding="utf-8"))
                if isinstance(report, dict):
                    report["historyWarmups"] = history_warmups
                    report["firstReportBaselineProbe"] = first_report_baseline_probe
                    report["freshUploadProbe"] = fresh_upload_probe
                    report["workerHandoffProbe"] = worker_handoff_probe
                    material = {
                        key: value
                        for key, value in report.items()
                        if key not in {"verificationHash", "verified"}
                    }
                    report["verificationHash"] = "sha256:" + hashlib.sha256(
                        json.dumps(
                            material,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        ).encode("utf-8")
                    ).hexdigest()
                    output.write_text(
                        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8",
                    )
            except Exception:
                pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition three-report history warmup failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
