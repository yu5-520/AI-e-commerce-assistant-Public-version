"""V22.5.14 hard runtime facade.

The sealed Agent1/Agent3/task owners remain unchanged. This facade owns only:
- Agent2 expired-lease recovery before data-version selection;
- old projection-budget failure recovery;
- Agent2 action-evidence-slice execution.
"""
from __future__ import annotations

from typing import Any, Dict

from src.services import agent_runtime_hard_interface_v2257_service as legacy
from src.services.agent2_runtime_resilience_v2143_service import (
    recover_stale_agent2_claims,
)
from src.services.agent2_runtime_v22514_service import (
    migrate_agent2_projection_failures_v22514,
    run_agent2_draft_microbatch_hard,
)

AGENT_RUNTIME_HARD_INTERFACE_VERSION = "22.5.14"
THREE_AGENT_PIPELINE_VERSION = legacy.THREE_AGENT_PIPELINE_VERSION
EXECUTION_LOCK_CONTRACT = legacy.EXECUTION_LOCK_CONTRACT
AGENT2_EVIDENCE_SLICE_VERSION = "22.5.14"


def _recover_agent2(data_version: str | None) -> Dict[str, Any]:
    return {
        "staleRunning": recover_stale_agent2_claims(
            data_version,
            limit=500,
        ),
        "projectionFailures": migrate_agent2_projection_failures_v22514(
            data_version,
            limit=500,
        ),
    }


def select_runnable_data_version_v225(
    preferred: str | None = None,
) -> str | None:
    # Recover all expired Agent2 leases first. A stale running row is otherwise
    # invisible to the runnable-stage selector.
    _recover_agent2(None)
    return legacy.select_runnable_data_version_v225(preferred)


def _augment(
    value: Dict[str, Any],
    *,
    recovery: Dict[str, Any],
    data_version: str | None,
) -> Dict[str, Any]:
    result = dict(value)
    result.update(
        version=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        threeAgentPipelineVersion=THREE_AGENT_PIPELINE_VERSION,
        agent2EvidenceSliceVersion=AGENT2_EVIDENCE_SLICE_VERSION,
        agent2StaleRunningRecovery=recovery.get("staleRunning") or {},
        agent2ProjectionFailureRecovery=(
            recovery.get("projectionFailures") or {}
        ),
        dataVersion=result.get("dataVersion") or data_version,
        agent2RuntimeSource="artifactRefs.agent2DraftInputRef.v22514",
        executionMode="agent1_full_audit_then_agent2_action_evidence_slice",
        fallbackAllowed=False,
    )
    return result


def run_agent_pipeline_tick_hard(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    worker_id: str | None = None,
    agent1_batch_size: int = 8,
    action_pack_batch_size: int = 8,
    agent2_batch_size: int = 5,
    agent3_batch_size: int = 2,
    mapping_batch_size: int = 8,
    pool_batch_size: int = 8,
    force_new_snapshot: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    from src.services.agent_runtime_hard_interface_v2255_service import (
        _refresh_read_models,
    )
    from src.services.pipeline_action_microbatch_v205_service import (
        pending_agent2_item_count,
    )
    from src.services.pipeline_agent3_sop_v225_service import (
        pending_agent3_sop_item_count,
    )
    from src.services.pipeline_task_mapping_v225_service import (
        pending_task_mapping_item_count,
        pending_task_pool_item_count,
    )

    recovery = _recover_agent2(data_version)
    resolved = data_version or legacy.select_runnable_data_version_v225()
    if not resolved:
        return _augment(
            {
                "ran": bool(
                    int(
                        _dict(
                            recovery.get("staleRunning")
                        ).get("recoveredItemCount")
                        or 0
                    )
                    or int(
                        _dict(
                            recovery.get("projectionFailures")
                        ).get("recoveredItemCount")
                        or 0
                    )
                ),
                "reason": "no_runnable_agent_pipeline_items",
                "selectedStage": "agent2_recovery_only",
                "dataVersion": data_version,
            },
            recovery=recovery,
            data_version=data_version,
        )

    # Preserve the sealed downstream priority. Only take ownership when Agent2
    # is the highest runnable stage.
    higher_priority_pending = any(
        [
            pending_task_pool_item_count(resolved) > 0,
            pending_task_mapping_item_count(resolved) > 0,
            pending_agent3_sop_item_count(resolved) > 0,
        ]
    )
    if not higher_priority_pending and pending_agent2_item_count(resolved) > 0:
        stage_result = run_agent2_draft_microbatch_hard(
            resolved,
            user_id=user_id,
            batch_size=agent2_batch_size,
        )
        output = {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
            "ran": bool(stage_result.get("ran")),
            "workerId": worker_id,
            "selectedStage": (
                "agent2DraftInputRef.v22514_to_agent2_draft_ready"
            ),
            "dataVersion": resolved,
            "result": stage_result,
            "runtimeSource": "agent2DraftInputRef.v22514",
            "executionLockContract": EXECUTION_LOCK_CONTRACT,
            "fallbackAllowed": False,
        }
        _refresh_read_models(output, resolved)
        return _augment(
            output,
            recovery=recovery,
            data_version=resolved,
        )

    delegated = legacy.run_agent_pipeline_tick_hard(
        data_version=resolved,
        user_id=user_id,
        worker_id=worker_id,
        agent1_batch_size=agent1_batch_size,
        action_pack_batch_size=action_pack_batch_size,
        agent2_batch_size=agent2_batch_size,
        agent3_batch_size=agent3_batch_size,
        mapping_batch_size=mapping_batch_size,
        pool_batch_size=pool_batch_size,
        force_new_snapshot=force_new_snapshot,
        **kwargs,
    )
    return _augment(
        delegated,
        recovery=recovery,
        data_version=resolved,
    )


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def startup_agent_runtime_hard() -> Dict[str, Any]:
    recovery = _recover_agent2(None)
    result = legacy.startup_agent_runtime_hard()
    return _augment(
        result,
        recovery=recovery,
        data_version=result.get("dataVersion"),
    )


def agent_runtime_hard_interface_status() -> Dict[str, Any]:
    result = legacy.legacy.agent_runtime_hard_interface_status()
    result.update(
        version=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        agent2EvidenceSliceVersion=AGENT2_EVIDENCE_SLICE_VERSION,
        agent2RuntimeSource="artifactRefs.agent2DraftInputRef.v22514",
        agent1FullDiagnosisAuditOnly=True,
        agent2ReceivesActionEvidenceSliceOnly=True,
        fullReportReadByAgent2Allowed=False,
        rawAgent1OutputReadByAgent2Allowed=False,
        agent2StaleLeaseRecovery="before_selection_and_startup",
        executionMode="agent1_full_audit_then_agent2_action_evidence_slice",
        fallbackAllowed=False,
    )
    return result


run_agent1_microbatch_hard = legacy.run_agent1_microbatch_hard
run_agent2_microbatch_hard = run_agent2_draft_microbatch_hard
migrate_legacy_agent2_outputs = legacy.legacy.migrate_legacy_agent2_outputs
migrate_misclassified_agent2_input_failures = (
    migrate_agent2_projection_failures_v22514
)


__all__ = [
    "AGENT_RUNTIME_HARD_INTERFACE_VERSION",
    "THREE_AGENT_PIPELINE_VERSION",
    "EXECUTION_LOCK_CONTRACT",
    "AGENT2_EVIDENCE_SLICE_VERSION",
    "run_agent1_microbatch_hard",
    "run_agent2_draft_microbatch_hard",
    "run_agent2_microbatch_hard",
    "run_agent_pipeline_tick_hard",
    "select_runnable_data_version_v225",
    "startup_agent_runtime_hard",
    "agent_runtime_hard_interface_status",
    "migrate_legacy_agent2_outputs",
    "migrate_misclassified_agent2_input_failures",
]
