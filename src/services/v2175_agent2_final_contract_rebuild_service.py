"""V21.7.5 final Agent2 contract rebuild.

V21.7.4 correctly capped executable budget values, but an older permission
conflict could leave derivative semantic flags behind. This overlay recomputes
the complete Agent2 contract after every normalization, clears only stale
budget-permission derivatives, and promotes proven historical plans directly to
``agent2_completed`` without another provider call.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.agent2_provenance_v2141_service import (
    provider_has_valid_agent2_proof,
)
from src.services.v2172_inventory_action_guard_service import (
    _arr,
    _dict,
    _package_family,
    _payload,
    _policy,
    _text,
)
from src.services.v2173_agent2_policy_shape_recovery_service import (
    align_experiment_policy_to_locked_family,
)
from src.services.v2174_budget_governance_service import (
    executable_permission_violations,
    finalize_governed_plan,
)

AGENT2_FINAL_CONTRACT_REBUILD_VERSION = "21.7.5"
_BUDGET_FAILURE_CODE = "budget_change_exceeds_ceiling"
_BUDGET_DERIVATIVE_MISSING = {
    "agent2ActionPlan.actionPlanStatus_ready",
    "agent2ActionPlan.semanticContractMissing_empty",
}
_STALE_FAILURE_FIELDS = {
    "reason",
    "blockedReason",
    "missing",
    "failureOwner",
    "frontendFailureLabel",
    "taskAdmissionAllowed",
    "agent2RetryPolicy",
    "failureCode",
    "failureClass",
}
_INSTALLED = False


def _budget_reason(value: Any) -> bool:
    return _BUDGET_FAILURE_CODE in _text(value)


def _budget_failure_history(payload: Dict[str, Any]) -> bool:
    plan = _dict(payload.get("agent2ActionPlan") or payload.get("plan"))
    recovery = _dict(payload.get("budgetGovernanceRecovery"))
    sources: List[Any] = [
        payload.get("reason"),
        payload.get("blockedReason"),
        plan.get("reason"),
        plan.get("conflictReason"),
        *_arr(payload.get("missing")),
        *_arr(plan.get("semanticContractMissing")),
        *_arr(plan.get("experimentPermissionViolations")),
        *_arr(recovery.get("previousMissing")),
    ]
    return (
        recovery.get("version") == "21.7.4"
        or any(_budget_reason(value) for value in sources)
    )


def _clean_budget_derivatives(values: Any) -> List[str]:
    result: List[str] = []
    for value in _arr(values):
        item = _text(value)
        if not item:
            continue
        if item in _BUDGET_DERIVATIVE_MISSING or _budget_reason(item):
            continue
        if item not in result:
            result.append(item)
    return result


def rebuild_final_agent2_plan(
    plan: Dict[str, Any],
    package: Dict[str, Any],
) -> Dict[str, Any]:
    """Recompute semantic and permission state from the final executable plan."""
    from src.services import agent2_action_plan_core_v20_service as agent2

    aligned_package = align_experiment_policy_to_locked_family(package)
    family = _package_family(aligned_package)
    rebuilt = copy.deepcopy(plan)

    # Old permission rejection is not an independent business judgment. Remove
    # only those derivatives, then recompute from the executable values below.
    rebuilt["semanticContractMissing"] = _clean_budget_derivatives(
        rebuilt.get("semanticContractMissing")
    )
    rebuilt["experimentPermissionViolations"] = []
    if _budget_reason(rebuilt.get("reason")):
        rebuilt["reason"] = None
    if _budget_reason(rebuilt.get("conflictReason")):
        rebuilt["conflictReason"] = None
    if rebuilt.get("actionPlanStatus") == "conflict_requires_rejudgment" and not rebuilt.get("conflictReason"):
        rebuilt["actionPlanStatus"] = "ready"

    rebuilt = finalize_governed_plan(rebuilt, aligned_package)
    policy = _policy(aligned_package)
    violations = executable_permission_violations(rebuilt, policy)

    # _contract_missing is the authoritative semantic check used by Agent2.
    # Running it here after budget governance prevents stale pre-normalization
    # flags from controlling the final admission decision.
    semantic_missing = agent2._contract_missing(rebuilt, aligned_package, family)
    semantic_missing = _clean_budget_derivatives(semantic_missing)

    rebuilt["semanticContractMissing"] = semantic_missing
    rebuilt["experimentPolicy"] = policy
    rebuilt["experimentPermissionViolations"] = violations
    rebuilt["experimentPermissionStatus"] = "passed" if not violations else "rejected"
    rebuilt["experimentPermissionApplied"] = bool(policy)
    rebuilt["agent2FinalContractRebuild"] = {
        "version": AGENT2_FINAL_CONTRACT_REBUILD_VERSION,
        "actionFamily": family,
        "semanticMissing": semantic_missing,
        "permissionViolations": violations,
        "recomputedAfterBudgetGovernance": True,
    }

    if not semantic_missing and not violations:
        rebuilt["actionPlanStatus"] = "ready"
        rebuilt["taskAdmissionAllowed"] = True
        rebuilt["reason"] = None
        rebuilt["conflictReason"] = None
    else:
        rebuilt["taskAdmissionAllowed"] = False
        if violations:
            rebuilt["actionPlanStatus"] = "conflict_requires_rejudgment"
            rebuilt["conflictReason"] = (
                "Agent2 final executable plan exceeds experiment permission: "
                + ",".join(violations)
            )
            rebuilt["reason"] = rebuilt["conflictReason"]
        elif rebuilt.get("actionPlanStatus") == "ready":
            rebuilt["actionPlanStatus"] = "action_plan_missing_data"
            rebuilt["reason"] = (
                "Agent2 final executable plan did not satisfy contract: "
                + ",".join(semantic_missing)
            )
    return rebuilt


def _clean_failed_package(payload: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key not in _STALE_FAILURE_FIELDS
        and key not in {
            "agent2ActionPlan",
            "plan",
            "operationPlan",
            "actionPlanStatus",
            "agent2Source",
        }
    }
    lineage = dict(_dict(cleaned.get("lineage")))
    lineage["currentStage"] = "agent2_completed"
    lineage["source"] = "pipeline_items.payload_final_contract_rebuild"
    cleaned["lineage"] = lineage
    return cleaned


def recover_final_budget_contract_failures(
    data_version: str | None,
    *,
    limit: int = 50,
) -> Dict[str, Any]:
    """Promote an existing proven plan without calling Agent2 again."""
    if not data_version:
        return {
            "version": AGENT2_FINAL_CONTRACT_REBUILD_VERSION,
            "dataVersion": None,
            "recoveredItemCount": 0,
        }

    from src.services.agent_runtime_contract_v2141_service import (
        missing_agent2_contract,
        normalize_agent2_completed_contract,
    )
    from src.services.agent2_runtime_resilience_v2143_service import (
        clear_agent2_runtime_control,
        ensure_agent2_runtime_columns,
    )
    from src.services.pipeline_item_service import (
        build_item_envelope,
        record_pipeline_item_event,
        upsert_pipeline_item,
    )

    ensure_agent2_runtime_columns()
    recovered = 0
    skipped_no_budget_history = 0
    skipped_no_plan = 0
    skipped_invalid_proof = 0
    skipped_contract = 0

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM pipeline_items
            WHERE data_version=?
              AND current_stage='agent2_output_invalid'
              AND status='failed'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (data_version, max(1, min(200, int(limit or 50)))),
        ).fetchall()

    for source_row in rows:
        row = dict(source_row)
        outer = loads(row.get("payload")) if row.get("payload") else {}
        outer = outer if isinstance(outer, dict) else {}
        payload = _payload(outer)
        if not _budget_failure_history(payload):
            skipped_no_budget_history += 1
            continue

        plan = _dict(payload.get("agent2ActionPlan") or payload.get("plan"))
        if not plan:
            skipped_no_plan += 1
            continue

        provider = _dict(payload.get("agent2Provider"))
        proof = _dict(
            payload.get("agent2ExecutionProof")
            or plan.get("agent2ExecutionProof")
            or _dict(provider.get("itemProvenance")).get(
                str(payload.get("packageId") or plan.get("packageId") or row.get("package_id") or "")
            )
        )
        package_id = str(
            payload.get("packageId")
            or plan.get("packageId")
            or row.get("package_id")
            or row.get("item_id")
            or ""
        )
        if not provider_has_valid_agent2_proof(provider, package_id, proof):
            skipped_invalid_proof += 1
            continue

        rebuilt_plan = rebuild_final_agent2_plan(plan, payload)
        base = _clean_failed_package(payload)
        candidate = normalize_agent2_completed_contract(base, rebuilt_plan, provider)
        candidate["agent2FinalContractRecovery"] = {
            "version": AGENT2_FINAL_CONTRACT_REBUILD_VERSION,
            "sourceStage": row.get("current_stage"),
            "providerCallReused": True,
            "newProviderCallExecuted": False,
            "recoveredAt": datetime.now().isoformat(),
            "previousMissing": _arr(payload.get("missing")),
        }
        candidate["agent2Source"] = rebuilt_plan.get("agent2Source") or "llm_provider_call"
        candidate["taskAdmissionAllowed"] = True
        candidate["fallbackAllowed"] = False

        missing = missing_agent2_contract(candidate)
        if rebuilt_plan.get("actionPlanStatus") != "ready" or missing:
            skipped_contract += 1
            continue

        output_ref = f"agent2_final_contract_rebuilt:{data_version}:{package_id}"
        envelope = build_item_envelope(
            data_version=row.get("data_version"),
            item_id=row.get("item_id"),
            product_id=row.get("product_id") or candidate.get("productId"),
            store_id=row.get("store_id") or candidate.get("storeId"),
            signal_id=row.get("signal_id") or candidate.get("signalId"),
            package_id=row.get("package_id") or candidate.get("packageId"),
            decision_id=row.get("decision_id"),
            action_family=row.get("action_family") or candidate.get("actionFamily"),
            route=row.get("route") or candidate.get("route"),
            input_ref=row.get("output_ref"),
            output_ref=output_ref,
            stage="agent2_completed",
        )
        envelope = upsert_pipeline_item(
            envelope,
            stage="agent2_completed",
            status="ready",
            priority=int(row.get("priority") or 50),
            output_ref=output_ref,
            payload=candidate,
        )
        clear_agent2_runtime_control(row.get("item_id"))
        record_pipeline_item_event(
            envelope,
            station_id="agent2_final_contract_rebuild_station",
            stage="agent2_completed",
            status="ready",
            input_ref=row.get("output_ref"),
            output_ref=output_ref,
            payload=candidate,
        )
        recovered += 1

    return {
        "version": AGENT2_FINAL_CONTRACT_REBUILD_VERSION,
        "dataVersion": data_version,
        "recoveredItemCount": recovered,
        "skippedNoBudgetHistoryCount": skipped_no_budget_history,
        "skippedNoPlanCount": skipped_no_plan,
        "skippedInvalidProofCount": skipped_invalid_proof,
        "skippedFinalContractCount": skipped_contract,
        "newProviderCallCount": 0,
        "rule": "Rebuild the final contract from the existing proven Agent2 plan; never spend another provider call for a budget-only stale-state failure.",
    }


def install_v2175_agent2_final_contract_rebuild() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import agent_pipeline_item_worker_v2010_service as pipeline_worker

    if getattr(agent2, "_V2175_AGENT2_FINAL_CONTRACT_REBUILD_INSTALLED", False):
        _INSTALLED = True
        return

    original_normalize = agent2._normalize_plan
    original_tick = pipeline_worker.run_agent_pipeline_tick

    def normalize_plan_v2175(
        raw: Dict[str, Any],
        package: Dict[str, Any],
        proof: Dict[str, Any],
    ) -> Dict[str, Any]:
        plan = original_normalize(raw, package, proof)
        return rebuild_final_agent2_plan(plan, package)

    def run_pipeline_tick_v2175(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        data_version = (
            kwargs.get("data_version")
            or (args[0] if args else None)
            or pipeline_worker.latest_data_version()
        )
        recovery = recover_final_budget_contract_failures(data_version)
        result = original_tick(*args, **kwargs)
        result["agent2FinalContractRebuildVersion"] = AGENT2_FINAL_CONTRACT_REBUILD_VERSION
        result["agent2FinalContractRecovery"] = recovery
        return result

    agent2._normalize_plan = normalize_plan_v2175
    pipeline_worker.run_agent_pipeline_tick = run_pipeline_tick_v2175

    for module in (agent2, pipeline_worker):
        module.AGENT2_FINAL_CONTRACT_REBUILD_VERSION = AGENT2_FINAL_CONTRACT_REBUILD_VERSION
        module._V2175_AGENT2_FINAL_CONTRACT_REBUILD_INSTALLED = True
    _INSTALLED = True


__all__ = [
    "AGENT2_FINAL_CONTRACT_REBUILD_VERSION",
    "install_v2175_agent2_final_contract_rebuild",
    "rebuild_final_agent2_plan",
    "recover_final_budget_contract_failures",
]
