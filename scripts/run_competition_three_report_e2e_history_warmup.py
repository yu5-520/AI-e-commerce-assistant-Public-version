#!/usr/bin/env python3
"""Warm historical reports and probe fresh-upload / worker-handoff behavior.

The first two real scenario reports must pass the eight pre-Agent stations so the
third report receives real lineage/trend context. Before the real scenario starts,
the exact same first-report import request is submitted twice without a sleep. This
proves whether content identity is incorrectly reused as run identity. Probe state is
then reset so it cannot contaminate the three-report business-chain evidence.

The wrapper also times the first explicit Agent pipeline tick after the third import.
That timing proves the competition's explicit single-worker/tick path does not contain
a fixed ~402 second handoff sleep. It does not claim autonomous background scheduling.
"""
from __future__ import annotations

import hashlib
import json
import os
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
        nonlocal import_count, fresh_probe_done, main_tick_measured
        method = str(
            request_args[0] if request_args else request_kwargs.get("method") or ""
        )
        url = str(
            request_args[1] if len(request_args) > 1 else request_kwargs.get("url") or ""
        )

        # Before the first real scenario import, submit the exact same request twice.
        # No sleep is inserted: a fresh run identity must not depend on wall-clock
        # separation and must never be replaced by source-content identity.
        if (
            method.upper() == "POST"
            and "/api/data/import/confirm" in url
            and not fresh_probe_done
        ):
            payload = request_kwargs.get("payload")
            if payload is None and len(request_args) > 2:
                payload = request_args[2]
            request_hash = _canonical_hash(payload)
            fresh_upload_probe.update(
                attempted=True,
                requestContentHash=request_hash,
            )
            probe_results: list[dict[str, Any]] = []
            try:
                for attempt in (1, 2):
                    started = time.monotonic()
                    probe_status, probe_response = original_http_json(
                        *request_args, **request_kwargs
                    )
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
                source_hashes = [
                    item.get("sourceContentHash")
                    for item in probe_results
                    if item.get("sourceContentHash")
                ]
                fresh_upload_probe["results"] = probe_results
                fresh_upload_probe["sameRequestContent"] = (
                    len({item["requestContentHash"] for item in probe_results}) == 1
                )
                fresh_upload_probe["freshDataVersion"] = (
                    len(versions) == 2 and len(set(versions)) == 2
                )
                if len(source_hashes) == 2:
                    fresh_upload_probe["sourceContentHashStableWhenExposed"] = (
                        len(set(source_hashes)) == 1
                    )
                fresh_upload_probe["verified"] = bool(
                    fresh_upload_probe["sameRequestContent"]
                    and fresh_upload_probe["freshDataVersion"]
                    and fresh_upload_probe["sourceContentHashStableWhenExposed"]
                    is not False
                )
                if fresh_upload_probe["verified"] is not True:
                    raise base.ThreeReportE2EError(
                        "FRESH_UPLOAD_IDENTITY_REUSED:"
                        + json.dumps(fresh_upload_probe, ensure_ascii=False, sort_keys=True)
                    )

                app_url = url.split("/api/data/import/confirm", 1)[0]
                original_http_json(
                    "POST",
                    app_url
                    + "/api/system/reset-runtime-data?confirm=true&include_audit_logs=true&scope=demo",
                    headers=base.USER_HEADERS,
                    timeout=60.0,
                )
                fresh_upload_probe["probeStateResetBeforeScenario"] = True
            except Exception as exc:
                fresh_upload_probe.setdefault("errors", []).append(
                    f"{type(exc).__name__}:{exc}"
                )
                raise
            finally:
                fresh_probe_done = True

        # Time the first explicit pipeline tick after all three real reports exist.
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
            worker_handoff_probe["tickRan"] = (
                response.get("ran") if isinstance(response, dict) else None
            )
            worker_handoff_probe["verified"] = duration < float(
                worker_handoff_probe["thresholdSeconds"]
            )
            if worker_handoff_probe["verified"] is not True:
                error = (
                    "EXPLICIT_WORKER_HANDOFF_EXCEEDED_THRESHOLD:"
                    f"{duration}>={worker_handoff_probe['thresholdSeconds']}"
                )
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
            raise base.ThreeReportE2EError(
                f"HISTORY_IMPORT_DATA_VERSION_MISSING:{import_count}"
            )

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
            raise base.ThreeReportE2EError(
                f"HISTORY_PRE_AGENT_WARMUP_FAILED:{version}:{tick}"
            )
        compat._history_only(database_path, version)
        history_warmups.append(
            {
                "dataVersion": version,
                "importIndex": import_count,
                "preAgentTickRan": True,
                "preAgentTickDurationSeconds": tick_duration,
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
                    report["freshUploadProbe"] = fresh_upload_probe
                    report["workerHandoffProbe"] = worker_handoff_probe
                    # Re-hash the enriched evidence boundary. This wrapper owns the
                    # final attestation bytes used by CI.
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
                        json.dumps(
                            report,
                            ensure_ascii=False,
                            sort_keys=True,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
            except Exception:
                pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition three-report history warmup failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
