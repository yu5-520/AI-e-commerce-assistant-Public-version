"""V22.5.8 metadata facade over the single hard pipeline runtime.

The V22.5.7 hard transition owner remains single. Its Agent1 contract, transport and
Token Runtime imports resolve to V22.5.8 compatibility aliases. This facade upgrades
public metadata without creating another queue, worker or state machine.
"""
from __future__ import annotations

from typing import Any, Dict

from src.services import agent_runtime_hard_interface_v2257_service as legacy

AGENT_RUNTIME_HARD_INTERFACE_VERSION = "22.5.8"
THREE_AGENT_PIPELINE_VERSION = legacy.THREE_AGENT_PIPELINE_VERSION
EXECUTION_LOCK_CONTRACT = legacy.EXECUTION_LOCK_CONTRACT


def _upgrade(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: _upgrade(child) for key, child in value.items()}
        if str(result.get("version") or "") == "22.5.7":
            result["version"] = AGENT_RUNTIME_HARD_INTERFACE_VERSION
        if "agent1InputProjectionVersion" in result:
            result["agent1InputProjectionVersion"] = "22.5.8"
        if "runtimeSource" in result and isinstance(result["runtimeSource"], str):
            result["runtimeSource"] = result["runtimeSource"].replace(
                "agent1InputRef.v2", "agent1InputRef.v3"
            )
        return result
    if isinstance(value, list):
        return [_upgrade(item) for item in value]
    return value


def run_agent1_microbatch_hard(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 8,
) -> Dict[str, Any]:
    result = legacy.run_agent1_microbatch_hard(
        data_version,
        user_id=user_id,
        batch_size=batch_size,
    )
    upgraded = _upgrade(result)
    upgraded.update(
        version=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        agent1InputProjectionVersion="22.5.8",
        runtimeSource="agent1InputRef.v3",
        evidenceContract="source_lineage_plus_metric_cross_validation",
        outputNormalizationContract="decision_aliases_then_execution_lock",
    )
    return upgraded


def select_runnable_data_version_v225(preferred: str | None = None) -> str | None:
    return legacy.select_runnable_data_version_v225(preferred)


def run_agent_pipeline_tick_hard(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    result = legacy.run_agent_pipeline_tick_hard(*args, **kwargs)
    upgraded = _upgrade(result)
    upgraded.update(
        version=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        agent1InputProjectionVersion="22.5.8",
        runtimeSource="agent1InputRef.v3_or_v22_5_5_downstream_inputs",
        evidenceContract="source_lineage_plus_metric_cross_validation",
        outputNormalizationContract="decision_aliases_then_execution_lock",
    )
    return upgraded


startup_agent_runtime_hard = legacy.startup_agent_runtime_hard

__all__ = [
    "AGENT_RUNTIME_HARD_INTERFACE_VERSION",
    "THREE_AGENT_PIPELINE_VERSION",
    "EXECUTION_LOCK_CONTRACT",
    "run_agent1_microbatch_hard",
    "select_runnable_data_version_v225",
    "run_agent_pipeline_tick_hard",
    "startup_agent_runtime_hard",
]
