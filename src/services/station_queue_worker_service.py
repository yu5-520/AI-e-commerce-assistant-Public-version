"""V21.3 background station and Agent pipeline worker.

The worker advances unfinished pipeline items by fair dataVersion scheduling.
It never forces a new task snapshot on every tick, so retries preserve task-pool
idempotency instead of manufacturing duplicate lifecycle tasks.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Any, Dict

from src.services.agent_pipeline_governance_v213_service import (
    AGENT_PIPELINE_GOVERNANCE_VERSION,
    runtime_governance_summary,
    select_runnable_data_version,
)
from src.services.station_queue_service import (
    STATION_QUEUE_VERSION,
    queue_summary,
    run_next_station_job,
)

STATION_QUEUE_WORKER_VERSION = "21.3"

_STATE: Dict[str, Any] = {
    "enabled": False,
    "running": False,
    "workerId": None,
    "startedAt": None,
    "lastTickAt": None,
    "lastResult": None,
    "lastSelectedDataVersion": None,
    "totalRuns": 0,
    "totalStationRuns": 0,
    "totalAgentPipelineRuns": 0,
    "lastError": None,
}
_THREAD: threading.Thread | None = None
_STOP = threading.Event()
_LOCK = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _env_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _env_float(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _now() -> str:
    return datetime.now().isoformat()


def _set_state(**kwargs: Any) -> None:
    with _LOCK:
        _STATE.update(kwargs)


def _increment_state(name: str, amount: int = 1) -> None:
    with _LOCK:
        _STATE[name] = int(_STATE.get(name) or 0) + int(amount)


def worker_config() -> Dict[str, Any]:
    return {
        "version": STATION_QUEUE_WORKER_VERSION,
        "governanceVersion": AGENT_PIPELINE_GOVERNANCE_VERSION,
        "queueVersion": STATION_QUEUE_VERSION,
        "enabledByEnv": _env_bool("STATION_QUEUE_WORKER_ENABLED", True),
        "agentPipelineEnabled": _env_bool(
            "AGENT_PIPELINE_ITEM_WORKER_ENABLED",
            True,
        ),
        "intervalSeconds": _env_float(
            "STATION_QUEUE_WORKER_INTERVAL",
            2.0,
            1.0,
            60.0,
        ),
        "maxJobsPerTick": _env_int(
            "STATION_QUEUE_WORKER_MAX_JOBS_PER_TICK",
            3,
            1,
            12,
        ),
        "agentActionPackBatchSize": _env_int(
            "AGENT_PIPELINE_ACTION_PACK_BATCH_SIZE",
            8,
            1,
            50,
        ),
        "agent2BatchSize": _env_int(
            "AGENT_PIPELINE_AGENT2_BATCH_SIZE",
            5,
            1,
            12,
        ),
        "sopBatchSize": _env_int(
            "AGENT_PIPELINE_SOP_BATCH_SIZE",
            8,
            1,
            50,
        ),
        "poolBatchSize": _env_int(
            "AGENT_PIPELINE_POOL_BATCH_SIZE",
            8,
            1,
            50,
        ),
        "systemType": os.getenv(
            "STATION_QUEUE_WORKER_SYSTEM_TYPE",
            "task_generation",
        ),
        "dataVersionSelection": "oldest_highest_priority_runnable",
        "forceNewSnapshot": False,
        "contractRecovery": (
            "repair proven protocol/state breakpoints only; business failures "
            "remain failed"
        ),
        "rule": (
            "V21.3 selects a runnable dataVersion across all unfinished imports, "
            "advances one semantic stage, and preserves task idempotency."
        ),
    }


def _agent_pipeline_tick(
    worker_id: str,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    from src.services.agent_pipeline_item_worker_v2010_service import (
        run_agent_pipeline_tick,
    )
    from src.services.pipeline_runtime_recovery_v2028_service import (
        recover_pipeline_runtime_breakpoints,
    )

    recovery = recover_pipeline_runtime_breakpoints()
    preferred = None
    with _LOCK:
        previous = _STATE.get("lastSelectedDataVersion")
        if isinstance(previous, str) and previous.strip():
            preferred = previous
    data_version = select_runnable_data_version(preferred)
    if not data_version:
        return {
            "version": STATION_QUEUE_WORKER_VERSION,
            "governanceVersion": AGENT_PIPELINE_GOVERNANCE_VERSION,
            "ran": bool(recovery.get("ran")),
            "reason": "no_runnable_agent_pipeline_items",
            "dataVersion": None,
            "runtimeRecovery": recovery,
        }

    _set_state(lastSelectedDataVersion=data_version)
    result = run_agent_pipeline_tick(
        data_version=data_version,
        worker_id=worker_id,
        action_pack_batch_size=int(config["agentActionPackBatchSize"]),
        agent2_batch_size=int(config["agent2BatchSize"]),
        sop_batch_size=int(config["sopBatchSize"]),
        pool_batch_size=int(config["poolBatchSize"]),
        force_new_snapshot=False,
    )
    return {
        **result,
        "ran": bool(result.get("ran") or recovery.get("ran")),
        "selectedDataVersion": data_version,
        "runtimeRecovery": recovery,
        "forceNewSnapshot": False,
    }


def worker_status(include_queue: bool = True) -> Dict[str, Any]:
    with _LOCK:
        state = dict(_STATE)
    result: Dict[str, Any] = {
        "version": STATION_QUEUE_WORKER_VERSION,
        "governanceVersion": AGENT_PIPELINE_GOVERNANCE_VERSION,
        "config": worker_config(),
        "state": state,
        "governance": runtime_governance_summary(),
        "rule": (
            "V21.3 background worker uses fair dataVersion scheduling and the "
            "single pipeline_items runtime."
        ),
    }
    if include_queue:
        try:
            result["queueSummary"] = queue_summary(limit=20)
        except Exception as exc:
            result["queueSummaryError"] = str(exc)
        try:
            from src.services.agent_pipeline_item_worker_v2010_service import (
                agent_pipeline_status,
            )

            selected = select_runnable_data_version(
                state.get("lastSelectedDataVersion")
            )
            result["agentPipelineStatus"] = agent_pipeline_status(selected)
        except Exception as exc:
            result["agentPipelineStatusError"] = str(exc)
    return result


def _run_one_worker_job(
    worker_id: str,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    if bool(config.get("agentPipelineEnabled")):
        agent_result = _agent_pipeline_tick(worker_id, config)
        if agent_result.get("ran"):
            _increment_state("totalAgentPipelineRuns")
            return {
                "type": "agent_pipeline_item_worker",
                "ran": True,
                "result": agent_result,
            }

    station_result = run_next_station_job(
        worker_id=worker_id,
        system_type=str(config["systemType"]),
    )
    return {
        "type": "station_queue_pre_agent_only",
        **station_result,
    }


def _worker_loop(worker_id: str) -> None:
    config = worker_config()
    interval = float(config["intervalSeconds"])
    max_jobs = int(config["maxJobsPerTick"])
    _set_state(
        enabled=True,
        running=True,
        workerId=worker_id,
        startedAt=_now(),
        lastError=None,
    )
    while not _STOP.is_set():
        tick_results = []
        try:
            for _ in range(max_jobs):
                result = _run_one_worker_job(worker_id, config)
                tick_results.append(result)
                _increment_state("totalRuns")
                if result.get("ran"):
                    if result.get("type") == "station_queue_pre_agent_only":
                        _increment_state("totalStationRuns")
                    continue
                break
            _set_state(
                lastTickAt=_now(),
                lastResult=tick_results,
                lastError=None,
            )
        except Exception as exc:
            _set_state(
                lastTickAt=_now(),
                lastError=str(exc),
                lastResult=tick_results,
            )
        _STOP.wait(interval)
    _set_state(running=False, lastTickAt=_now())


def start_station_queue_worker(
    worker_id: str = "auto-worker",
) -> Dict[str, Any]:
    global _THREAD
    if not _env_bool("STATION_QUEUE_WORKER_ENABLED", True):
        _set_state(
            enabled=False,
            running=False,
            workerId=worker_id,
            lastError="disabled_by_env",
        )
        return worker_status(include_queue=False)

    with _LOCK:
        alive = _THREAD is not None and _THREAD.is_alive()
    if alive:
        return worker_status(include_queue=False)

    _STOP.clear()
    thread = threading.Thread(
        target=_worker_loop,
        args=(worker_id,),
        name="station-queue-worker",
        daemon=True,
    )
    _THREAD = thread
    thread.start()
    time.sleep(0.05)
    return worker_status(include_queue=False)


def stop_station_queue_worker() -> Dict[str, Any]:
    _STOP.set()
    thread = _THREAD
    if thread and thread.is_alive():
        thread.join(timeout=2.0)
    _set_state(
        enabled=False,
        running=False,
        lastTickAt=_now(),
    )
    return worker_status(include_queue=False)


def run_worker_tick(
    worker_id: str = "manual-tick",
    limit: int | None = None,
) -> Dict[str, Any]:
    config = worker_config()
    max_jobs = max(
        1,
        min(int(limit or config["maxJobsPerTick"]), 30),
    )
    results = []
    for _ in range(max_jobs):
        result = _run_one_worker_job(worker_id, config)
        results.append(result)
        if not result.get("ran"):
            break

    _increment_state("totalRuns", len(results))
    _increment_state(
        "totalStationRuns",
        sum(
            1
            for item in results
            if item.get("type") == "station_queue_pre_agent_only"
            and item.get("ran")
        ),
    )
    _set_state(lastTickAt=_now(), lastResult=results)

    return {
        "version": STATION_QUEUE_WORKER_VERSION,
        "governanceVersion": AGENT_PIPELINE_GOVERNANCE_VERSION,
        "ranCount": sum(1 for item in results if item.get("ran")),
        "agentPipelineRanCount": sum(
            1
            for item in results
            if item.get("type") == "agent_pipeline_item_worker"
            and item.get("ran")
        ),
        "stationQueueRanCount": sum(
            1
            for item in results
            if item.get("type") == "station_queue_pre_agent_only"
            and item.get("ran")
        ),
        "results": results,
        "workerStatus": worker_status(include_queue=True),
        "rule": (
            "V21.3 manual tick uses fair dataVersion scheduling, semantic "
            "recovery and idempotent task admission."
        ),
    }
