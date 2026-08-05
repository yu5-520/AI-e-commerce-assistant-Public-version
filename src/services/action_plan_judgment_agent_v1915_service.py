"""V20.28 sealed compatibility wrapper for the current Agent2 pipeline worker.

The historical module name is retained so old imports do not crash, but no V19
Agent2 implementation, monkey patch, legacy package table or alternate prompt
protocol participates in runtime.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.action_pack_core_v20_service import select_action_parameter_pack
from src.services.agent2_action_plan_core_v20_service import (
    AGENT2_ACTION_PLAN_CORE_VERSION,
    _compact_package,
    _contract_missing,
)
from src.services.agent_rag_context_v2028_service import AGENT_RAG_CONTEXT_VERSION
from src.services.route_action_department_matrix_v1915_service import (
    MATRIX_DISPATCH_VERSION,
    attach_matrix_dispatch,
    selected_family,
)

ACTION_PLAN_AGENT_VERSION = "20.28"
ACTION_PLAN_AGENT_MODE = "v20_28_direct_agent2_with_dynamic_rag"


def _locked_family(package: Dict[str, Any]) -> str:
    return selected_family(attach_matrix_dispatch(package))


def _semantic_contract_missing(
    raw: Dict[str, Any],
    package: Dict[str, Any],
    family: str,
) -> List[str]:
    return _contract_missing(raw, package, family)


def _install_matrix_patch() -> None:
    """No-op retained for import compatibility; monkey patching is removed."""
    return None


def action_plan_judgment_agent_station_v1915(
    data_version: str | None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from src.services.pipeline_action_microbatch_v205_service import (
        pending_agent2_item_count,
        run_agent2_microbatch_loop_v205,
    )

    batch_size = int(kwargs.get("agent2MicroBatchSize") or kwargs.get("micro_batch_size") or 5)
    max_batches = int(kwargs.get("maxAgent2MicroBatches") or kwargs.get("max_micro_batches") or 20)
    if kwargs.get("pipeline_stream_mode"):
        max_batches = 1
    result = run_agent2_microbatch_loop_v205(
        data_version=data_version,
        user_id=kwargs.get("userId") or kwargs.get("user_id"),
        batch_size=batch_size,
        max_batches=max_batches,
    )
    result.update(
        {
            "version": ACTION_PLAN_AGENT_VERSION,
            "stationId": "action_plan_judgment_agent_station",
            "adapterMode": ACTION_PLAN_AGENT_MODE,
            "matrixDispatchVersion": MATRIX_DISPATCH_VERSION,
            "agent2ActionPlanCoreVersion": AGENT2_ACTION_PLAN_CORE_VERSION,
            "ragContextVersion": AGENT_RAG_CONTEXT_VERSION,
            "legacyAgent2ModuleUsed": False,
            "legacyPackageTableRead": False,
            "pendingItemCount": pending_agent2_item_count(data_version),
            "rule": "V20.28 compatibility entry delegates to the single current Agent2 pipeline-item worker.",
        }
    )
    return result


# Historical export aliases point to the same current implementation.
action_plan_judgment_agent_station_v1913 = action_plan_judgment_agent_station_v1915
