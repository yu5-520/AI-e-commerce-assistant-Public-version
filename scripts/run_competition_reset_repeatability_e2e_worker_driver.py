#!/usr/bin/env python3
"""Official-history driver for the Runtime Generation repeatability E2E.

The competition three-report contract is not "let H1/H2/H3 all enter Agents".
The official E2E deterministically completes the pre-Agent stations for H1 and H2,
then converts those two versions to history-only inputs; only H3 is the current
business version allowed to enter Agent1 -> Agent2 -> Agent3 -> Task.

This driver reuses that exact history-only boundary for *both* clean runs while still
keeping one candidate process, one SQLite database and the same preserved Artifact /
semantic-cache / canonical-history archives across the Reset boundary. Therefore the
comparison proves Reset repeatability for the real competition chain rather than for a
different three-current-version experiment.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
for path in (str(SCRIPT_DIR), str(ROOT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import run_competition_reset_repeatability_e2e as probe  # noqa: E402
import run_competition_three_report_e2e_compat as compat  # noqa: E402

_ORIGINAL_ENVIRONMENT = probe._environment


def _official_history_environment(app_root, state_root, provider_url, app_port):
    environment = _ORIGINAL_ENVIRONMENT(app_root, state_root, provider_url, app_port)
    # Match scripts/run_competition_three_report_e2e_history_warmup.py exactly:
    # explicit single-worker ticks own all state transitions while H1/H2 are frozen as
    # history-only before H3 is admitted to the Agent chain.
    environment["STATION_QUEUE_WORKER_ENABLED"] = "false"
    environment["AGENT_PIPELINE_ITEM_WORKER_ENABLED"] = "true"
    environment["STATION_QUEUE_WORKER_MAX_JOBS_PER_TICK"] = "12"
    environment["RUNTIME_GENERATION_REPEATABILITY_E2E"] = "true"
    return environment


def _response_data_version(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    direct = str(value.get("dataVersion") or "")
    if direct:
        return direct
    versions = value.get("dataVersions")
    return str(versions[-1] or "") if isinstance(versions, list) and versions else ""


def _official_history_run_once(
    *,
    run_index: int,
    app_url: str,
    scenario: Dict[str, Any],
    database_path: Path,
    max_ticks: int,
) -> Dict[str, Any]:
    reset = probe._reset(
        app_url,
        database_path,
        label=f"run_{run_index}_pre_reset",
    )

    imports: List[Dict[str, Any]] = []
    history_warmups: List[Dict[str, Any]] = []
    reports = scenario.get("reports") or []
    if len(reports) != 3:
        raise probe.ResetRepeatabilityError(
            f"THREE_REPORT_SCENARIO_REQUIRED:run={run_index}:count={len(reports)}"
        )

    for index, raw in enumerate(reports, 1):
        if not isinstance(raw, dict):
            raise probe.ResetRepeatabilityError(
                f"REPORT_OBJECT_REQUIRED:run={run_index}:index={index}"
            )
        payload = {
            "datasetName": scenario.get("datasetName") or "products",
            "sourceSystem": scenario.get("sourceSystem") or "competition_fixture",
            "rows": raw.get("rows"),
            "reportProfile": {
                "scenarioId": scenario.get("scenarioId"),
                "reportId": raw.get("reportId"),
                "period": raw.get("period"),
                "fixture": True,
            },
        }
        _, imported = probe.base.http_json(
            "POST",
            app_url + "/api/data/import/confirm",
            payload=payload,
            headers=probe.base.USER_HEADERS,
            timeout=60.0,
        )
        imported = imported if isinstance(imported, dict) else {}
        if imported.get("ok") is not True:
            raise probe.ResetRepeatabilityError(
                f"IMPORT_FAILED:run={run_index}:index={index}:{imported}"
            )
        data_version = _response_data_version(imported)
        if not data_version:
            raise probe.ResetRepeatabilityError(
                f"IMPORT_DATA_VERSION_MISSING:run={run_index}:index={index}"
            )
        imports.append(
            {
                "reportId": raw.get("reportId"),
                "period": raw.get("period"),
                "dataVersion": data_version,
                "rowCount": imported.get("rowCount"),
            }
        )

        if index <= 2:
            tick_started = time.monotonic()
            _, tick = probe.base.http_json(
                "POST",
                app_url + "/api/system/run-agent-pipeline-tick?limit=8",
                headers=probe.base.USER_HEADERS,
                timeout=240.0,
            )
            duration = round(time.monotonic() - tick_started, 6)
            if not isinstance(tick, dict) or tick.get("ran") is not True:
                raise probe.ResetRepeatabilityError(
                    f"HISTORY_PRE_AGENT_WARMUP_FAILED:run={run_index}:"
                    f"index={index}:dataVersion={data_version}:tick={tick}"
                )
            compat._history_only(database_path, data_version)
            history_warmups.append(
                {
                    "importIndex": index,
                    "dataVersion": data_version,
                    "preAgentTickRan": True,
                    "preAgentTickDurationSeconds": duration,
                    "historyOnlyApplied": True,
                    "rule": "official_eight_pre_agent_stations_then_history_only",
                }
            )

        # Preserve distinct import/business-time identity just like the official E2E.
        time.sleep(1.05)

    latest_version = imports[-1]["dataVersion"]
    ticks = probe._drain_worker(app_url, max_ticks)
    _, tasks_view = probe.base.http_json(
        "GET",
        app_url + "/api/view/tasks",
        headers=probe.base.USER_HEADERS,
        timeout=30.0,
    )
    _, live = probe.base.http_json(
        "GET",
        app_url + "/api/view/pipeline-live?limit=100",
        headers=probe.base.USER_HEADERS,
        timeout=30.0,
    )

    payloads = probe._task_payloads(database_path, latest_version)
    fingerprint = probe.task_set_semantic_hash(
        data_version=latest_version,
        tasks=payloads,
    )
    task_view_count = probe.base.recursive_list_count(tasks_view)
    if fingerprint.get("taskCount") != task_view_count:
        raise probe.ResetRepeatabilityError(
            f"TASK_COUNT_AUTHORITY_MISMATCH:run={run_index}:"
            f"pool={fingerprint.get('taskCount')}:view={task_view_count}"
        )
    if int(fingerprint.get("taskCount") or 0) < 1:
        raise probe.ResetRepeatabilityError(
            f"NO_TASKS_CREATED:run={run_index}:"
            + json.dumps(
                {
                    "imports": imports,
                    "historyWarmups": history_warmups,
                    "ticks": ticks[-12:],
                    "pipelineSummary": (live or {}).get("summary") if isinstance(live, dict) else None,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    generation = (live or {}).get("runtimeGeneration") if isinstance(live, dict) else {}
    generation = generation if isinstance(generation, dict) else {}
    if str(generation.get("generationHash") or "") != reset["generationHash"]:
        raise probe.ResetRepeatabilityError(
            f"ACTIVE_GENERATION_CHANGED_WITHOUT_RESET:run={run_index}:"
            f"{reset['generationHash']}:{generation.get('generationHash')}"
        )

    return {
        "runIndex": run_index,
        "preReset": reset,
        "imports": imports,
        "historyWarmups": history_warmups,
        "latestDataVersion": latest_version,
        "ticks": ticks,
        "taskViewCount": task_view_count,
        "taskFingerprint": fingerprint,
        "pipelineLive": live,
        "runtimeGeneration": generation,
        "scenarioMode": "official_h1_h2_history_only_h3_current",
    }


def main(argv: Sequence[str] | None = None) -> int:
    probe._environment = _official_history_environment
    probe._run_once = _official_history_run_once
    # Use the exact compatibility fixture-provider boundary used by the official
    # three-report gate. Imports above are already bound, so changing this module
    # constant affects only the subprocess provider path selected by run_repeatability.
    probe.SCRIPT_DIR = SCRIPT_DIR / "competition_e2e_compat_runtime"
    try:
        return probe.main(argv)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "verified": False,
                    "driver": "official_history_single_worker",
                    "error": f"{type(exc).__name__}:{exc}",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
