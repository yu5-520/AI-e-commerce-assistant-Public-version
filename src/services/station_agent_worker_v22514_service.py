"""V22.5.14 release-bound single three-Agent station worker."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Any, Dict

from src.services.agent_runtime_hard_interface_v22514_service import (
    AGENT_RUNTIME_HARD_INTERFACE_VERSION,
    run_agent_pipeline_tick_hard,
    select_runnable_data_version_v225,
)
from src.services.release_identity_service import release_identity
from src.services.station_queue_service import queue_summary, run_next_station_job

THREE_AGENT_PIPELINE_VERSION = "22.5.5"
STATION_AGENT_WORKER_VERSION = "22.5.14"

_STATE: Dict[str, Any] = {
    "enabled": False,
    "running": False,
    "workerId": None,
    "processId": os.getpid(),
    "runtimeRoot": None,
    "sourceCommit": None,
    "releaseHash": None,
    "manifestVerified": False,
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


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
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


def _increment(name: str, amount: int = 1) -> None:
    with _LOCK:
        _STATE[name] = int(_STATE.get(name) or 0) + int(amount)


def worker_config() -> Dict[str, Any]:
    identity = release_identity(verify_content=False)
    return {
        "version": STATION_AGENT_WORKER_VERSION,
        "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
        "hardAgentRuntimeVersion": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "agent1InputProjectionVersion": "22.5.8",
        "agent2EvidenceSliceVersion": "22.5.14",
        "releaseIdentity": identity,
        "enabledByEnv": _env_bool("STATION_QUEUE_WORKER_ENABLED", True),
        "agentPipelineEnabled": _env_bool("AGENT_PIPELINE_ITEM_WORKER_ENABLED", True),
        "intervalSeconds": _env_float("STATION_QUEUE_WORKER_INTERVAL", 2.0, 1.0, 60.0),
        "maxJobsPerTick": _env_int("STATION_QUEUE_WORKER_MAX_JOBS_PER_TICK", 3, 1, 12),
        "agent1BatchSize": _env_int("AGENT_PIPELINE_AGENT1_BATCH_SIZE", 8, 1, 20),
        "agentActionPackBatchSize": _env_int("AGENT_PIPELINE_ACTION_PACK_BATCH_SIZE", 8, 1, 50),
        "agent2DraftBatchSize": _env_int("AGENT_PIPELINE_AGENT2_BATCH_SIZE", 5, 1, 12),
        "agent3SopBatchSize": _env_int("AGENT_PIPELINE_AGENT3_BATCH_SIZE", 2, 1, 6),
        "taskMappingBatchSize": _env_int("AGENT_PIPELINE_TASK_MAPPING_BATCH_SIZE", 8, 1, 50),
        "poolBatchSize": _env_int("AGENT_PIPELINE_POOL_BATCH_SIZE", 8, 1, 50),
        "systemType": os.getenv("STATION_QUEUE_WORKER_SYSTEM_TYPE", "task_generation"),
        "dataVersionSelection": "oldest_highest_priority_runnable_after_agent2_recovery",
        "forceNewSnapshot": False,
        "agentExecutionMode": "agent1_full_audit_then_agent2_action_evidence_slice",
        "agent1RuntimeSource": "artifactRefs.agent1InputRef.v3+inputContentHash",
        "agent2RuntimeSource": "artifactRefs.agent2DraftInputRef.v22514",
        "agent3RuntimeSource": "artifactRefs.agent3SopInputRef",
        "executionLockContract": "one_problem_one_action_one_owner_one_target",
        "agent1FullDiagnosisAuditOnly": True,
        "agent2ReceivesActionEvidenceSliceOnly": True,
        "agent2StaleLeaseRecovery": "before_selection_and_startup",
        "fallbackAllowed": False,
    }


def _agent_pipeline_tick(worker_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    preferred = None
    with _LOCK:
        previous = _STATE.get("lastSelectedDataVersion")
        if isinstance(previous, str) and previous.strip():
            preferred = previous
    data_version = select_runnable_data_version_v225(preferred)
    if not data_version:
        return {
            "version": STATION_AGENT_WORKER_VERSION,
            "ran": False,
            "reason": "no_runnable_agent_pipeline_items",
            "dataVersion": None,
            "executionMode": "agent1_full_audit_then_agent2_action_evidence_slice",
            "fallbackAllowed": False,
        }
    _set_state(lastSelectedDataVersion=data_version)
    result = run_agent_pipeline_tick_hard(
        data_version=data_version,
        worker_id=worker_id,
        agent1_batch_size=int(config["agent1BatchSize"]),
        action_pack_batch_size=int(config["agentActionPackBatchSize"]),
        agent2_batch_size=int(config["agent2DraftBatchSize"]),
        agent3_batch_size=int(config["agent3SopBatchSize"]),
        mapping_batch_size=int(config["taskMappingBatchSize"]),
        pool_batch_size=int(config["poolBatchSize"]),
        force_new_snapshot=False,
    )
    return {
        **result,
        "selectedDataVersion": data_version,
        "forceNewSnapshot": False,
        "executionMode": "agent1_full_audit_then_agent2_action_evidence_slice",
        "fallbackAllowed": False,
    }


def _run_one(worker_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    if bool(config.get("agentPipelineEnabled")):
        agent_result = _agent_pipeline_tick(worker_id, config)
        if agent_result.get("ran"):
            _increment("totalAgentPipelineRuns")
            return {
                "type": "three_agent_hard_interface_worker",
                "ran": True,
                "result": agent_result,
            }
    station_result = run_next_station_job(
        worker_id=worker_id,
        system_type=str(config["systemType"]),
    )
    return {"type": "station_queue_pre_agent_only", **station_result}


def worker_status(include_queue: bool = True) -> Dict[str, Any]:
    identity = release_identity(verify_content=False)
    with _LOCK:
        state = dict(_STATE)
    state.update(
        processId=os.getpid(),
        runtimeRoot=identity.get("runtimeRoot"),
        sourceCommit=identity.get("sourceCommit"),
        releaseHash=identity.get("releaseHash"),
        manifestVerified=bool(identity.get("verified")),
    )
    result: Dict[str, Any] = {
        "version": STATION_AGENT_WORKER_VERSION,
        "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
        "hardAgentRuntimeVersion": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "agent1InputProjectionVersion": "22.5.8",
        "agent2EvidenceSliceVersion": "22.5.14",
        "releaseIdentity": identity,
        "config": worker_config(),
        "state": state,
        "executionMode": "agent1_full_audit_then_agent2_action_evidence_slice",
        "fallbackAllowed": False,
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

            selected = select_runnable_data_version_v225(
                state.get("lastSelectedDataVersion")
            )
            result["agentPipelineStatus"] = agent_pipeline_status(selected)
        except Exception as exc:
            result["agentPipelineStatusError"] = str(exc)
    return result


def _loop(worker_id: str) -> None:
    config = worker_config()
    identity = config.get("releaseIdentity") or {}
    interval = float(config["intervalSeconds"])
    max_jobs = int(config["maxJobsPerTick"])
    _set_state(
        enabled=True,
        running=True,
        workerId=worker_id,
        processId=os.getpid(),
        runtimeRoot=identity.get("runtimeRoot"),
        sourceCommit=identity.get("sourceCommit"),
        releaseHash=identity.get("releaseHash"),
        manifestVerified=bool(identity.get("verified")),
        startedAt=_now(),
        lastError=None,
    )
    while not _STOP.is_set():
        results = []
        try:
            for _ in range(max_jobs):
                result = _run_one(worker_id, config)
                results.append(result)
                _increment("totalRuns")
                if result.get("ran"):
                    if result.get("type") == "station_queue_pre_agent_only":
                        _increment("totalStationRuns")
                    continue
                break
            _set_state(lastTickAt=_now(), lastResult=results, lastError=None)
        except Exception as exc:
            _set_state(lastTickAt=_now(), lastResult=results, lastError=str(exc))
        _STOP.wait(interval)
    _set_state(running=False, lastTickAt=_now())


def start_station_queue_worker(
    worker_id: str = "fastapi-three-agent-worker",
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
    _THREAD = threading.Thread(
        target=_loop,
        args=(worker_id,),
        name="station-agent-v22514-worker",
        daemon=True,
    )
    _THREAD.start()
    time.sleep(0.05)
    return worker_status(include_queue=False)


def stop_station_queue_worker() -> Dict[str, Any]:
    _STOP.set()
    thread = _THREAD
    if thread and thread.is_alive():
        thread.join(timeout=2.0)
    _set_state(enabled=False, running=False, lastTickAt=_now())
    return worker_status(include_queue=False)


def run_worker_tick(
    worker_id: str = "manual-three-agent-tick",
    limit: int | None = None,
) -> Dict[str, Any]:
    config = worker_config()
    max_jobs = max(1, min(int(limit or config["maxJobsPerTick"]), 30))
    results = []
    for _ in range(max_jobs):
        result = _run_one(worker_id, config)
        results.append(result)
        _increment("totalRuns")
        if result.get("ran"):
            if result.get("type") == "station_queue_pre_agent_only":
                _increment("totalStationRuns")
            else:
                _increment("totalAgentPipelineRuns")
            continue
        break
    _set_state(lastTickAt=_now(), lastResult=results, lastError=None)
    return {
        "version": STATION_AGENT_WORKER_VERSION,
        "workerId": worker_id,
        "ran": any(bool(item.get("ran")) for item in results),
        "results": results,
        "state": worker_status(include_queue=False),
    }


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "STATION_AGENT_WORKER_VERSION",
    "worker_config",
    "worker_status",
    "start_station_queue_worker",
    "stop_station_queue_worker",
    "run_worker_tick",
]
