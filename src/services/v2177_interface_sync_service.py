"""V21.7.7 public Station and task-detail interface synchronization.

The HTTP host remains V21.6.2 and the Station core remains V21.6.1. This overlay
publishes the V21.7.7 single-action fields without creating a second runtime or
rewriting the historical Station component version.
"""

from __future__ import annotations

from typing import Any, Dict

from src.runtime_version import (
    ACTIVE_ACTION_CONTRACT_VERSION,
    AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
    METRIC_DIGEST_VERSION,
    SINGLE_ACTION_INTERFACE_VERSION,
)
from src.services.v2177_agent2_single_action_contract_service import active_action_contract

INTERFACE_SYNC_VERSION = SINGLE_ACTION_INTERFACE_VERSION

_AGENT2_FIELDS = [
    "singleActionContractVersion",
    "metricDigestVersion",
    "activeActionContractVersion",
    "groupedActionFamilies",
    "familyCallCount",
    "inputTokens",
    "outputTokens",
    "cacheHits",
    "discardedCrossFamilyFieldCount",
]
_SOP_FIELDS = [
    "activeActionFamily",
    "agent2PlanRef",
    "activeActionContract",
]
_TASK_POOL_FIELDS = [
    "activeActionFamily",
    "activeOperationPlan",
    "activeAuthority",
    "activeActionContract",
]


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _append_unique(values: list[Any], additions: list[str]) -> list[Any]:
    result = list(values or [])
    for item in additions:
        if item not in result:
            result.append(item)
    return result


def _update_nested_detail_contract(
    result: Dict[str, Any],
    contract: Dict[str, Any],
    operation_plan: Dict[str, Any],
    proof: Dict[str, Any],
) -> None:
    result["activeActionContract"] = contract
    if operation_plan:
        result["operationPlan"] = operation_plan
    if proof:
        result["agent2ExecutionProof"] = proof
    for key in ("relatedTask", "taskDetailReport"):
        container = result.get(key)
        if not isinstance(container, dict):
            continue
        container["activeActionContract"] = contract
        if operation_plan:
            container["operationPlan"] = operation_plan
        if proof:
            container["agent2ExecutionProof"] = proof
        task_plan = container.get("taskPlan")
        if isinstance(task_plan, dict):
            task_plan["activeActionContract"] = contract
            if operation_plan:
                task_plan["operationPlan"] = operation_plan
            if proof:
                task_plan["agent2ExecutionProof"] = proof


def install_v2177_interface_sync() -> None:
    from src.services import station_contract_service as contracts
    from src.services import station_registry_service as registry
    from src.services import task_detail_snapshot_v2024_service as detail

    if getattr(contracts, "_V2177_INTERFACE_SYNC_INSTALLED", False):
        return

    original_station_contract = contracts.station_contract
    original_registry_summary = registry.registry_summary
    original_build_task_detail = detail.build_task_detail_snapshot

    def station_contract_v2177(station_id: str) -> Dict[str, Any]:
        result = original_station_contract(station_id)
        if not result.get("ok"):
            return result
        sid = str(result.get("stationId") or station_id)
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        recommended = list(output.get("recommended") or [])
        if sid == "action_plan_judgment_agent_station":
            recommended = _append_unique(recommended, _AGENT2_FIELDS)
            result["singleActionContract"] = {
                "version": AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
                "oneLockedActionFamilyPerItem": True,
                "oneFamilyPerProviderCall": True,
                "familySpecificMetricDigest": True,
                "crossFamilyPlanFields": "discarded",
            }
        elif sid == "task_mapping_agent_station":
            recommended = _append_unique(recommended, _SOP_FIELDS)
            result["mappingContract"] = {
                "version": INTERFACE_SYNC_VERSION,
                "canonicalActionField": "activeActionContract",
                "agent2PlanCopyAllowed": False,
            }
        elif sid == "task_pool_admission_station":
            recommended = _append_unique(recommended, _TASK_POOL_FIELDS)
            result["admissionContract"] = {
                "version": INTERFACE_SYNC_VERSION,
                "authoritySource": "activeActionContract.activeOperationPlan",
                "crossFamilyFallbackAllowed": False,
            }
        output["recommended"] = recommended
        result["output"] = output
        result["singleActionInterfaceVersion"] = INTERFACE_SYNC_VERSION
        result["metricDigestVersion"] = METRIC_DIGEST_VERSION
        result["activeActionContractVersion"] = ACTIVE_ACTION_CONTRACT_VERSION
        result["rule"] = (
            "V21.7.7 keeps one locked action family and exposes activeActionContract "
            "as the canonical downstream action source over the V21.6.1 Station core."
        )
        return result

    def registry_summary_v2177() -> Dict[str, Any]:
        result = original_registry_summary()
        result["singleActionInterfaceVersion"] = INTERFACE_SYNC_VERSION
        result["singleActionContract"] = {
            "version": AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
            "agent2Input": "family_specific_metricDigest",
            "agent2Output": "family_specific_schema",
            "sopSource": "activeActionContract",
            "authoritySource": "activeActionContract.activeOperationPlan",
            "taskDetailSource": "materialized_activeActionContract",
        }
        result["singleActionMainlinePurity"] = "v21_7_7_single_action_overlay"
        result["singleActionRule"] = (
            "One item, one action family, one activeActionContract and no shadow "
            "station fallback."
        )
        return result

    def build_task_detail_v2177(task: Dict[str, Any]) -> Dict[str, Any]:
        result = original_build_task_detail(task)
        agent2 = _dict(result.get("agent2ActionPlan"))
        operation_plan = _dict(result.get("operationPlan")) or _dict(agent2.get("operationPlan"))
        proof = _dict(result.get("agent2ExecutionProof")) or _dict(agent2.get("agent2ExecutionProof"))
        authority = _dict(result.get("authorizationDecision") or result.get("actionAuthorization"))
        sop = {
            "operatorExecutionSop": result.get("operatorExecutionSop") or [],
            "taskPlan": _dict(_dict(result.get("taskDetailReport")).get("taskPlan")),
        }
        contract = _dict(result.get("activeActionContract"))
        if agent2:
            contract = active_action_contract(agent2, sop=sop, authority=authority)
        if contract:
            contract["version"] = ACTIVE_ACTION_CONTRACT_VERSION
            if operation_plan:
                contract["activeOperationPlan"] = operation_plan
            if authority:
                contract["activeAuthority"] = authority
            _update_nested_detail_contract(result, contract, operation_plan, proof)
        elif operation_plan or proof:
            if operation_plan:
                result["operationPlan"] = operation_plan
            if proof:
                result["agent2ExecutionProof"] = proof
        result["singleActionContractVersion"] = AGENT2_SINGLE_ACTION_CONTRACT_VERSION
        result["metricDigestVersion"] = METRIC_DIGEST_VERSION
        result["activeActionContractVersion"] = ACTIVE_ACTION_CONTRACT_VERSION
        return result

    for item in registry.STATIONS:
        sid = item.get("stationId")
        if sid == "action_plan_judgment_agent_station":
            item["acceptance"] = (
                "one locked family + family metricDigest + real provider or exact replay "
                "+ family-specific schema + cross-family fields discarded"
            )
        elif sid == "task_mapping_agent_station":
            item["acceptance"] = (
                "one ready Agent2 item = one SOP decision sourced only from activeActionContract"
            )
        elif sid == "task_pool_admission_station":
            item["acceptance"] = (
                "one sop_mapped item = one idempotent admission using activeOperationPlan and activeAuthority"
            )
        item["singleActionInterfaceVersion"] = INTERFACE_SYNC_VERSION

    contracts.station_contract = station_contract_v2177
    registry.registry_summary = registry_summary_v2177
    detail.build_task_detail_snapshot = build_task_detail_v2177
    contracts._V2177_INTERFACE_SYNC_INSTALLED = True
    registry._V2177_INTERFACE_SYNC_INSTALLED = True
    detail._V2177_INTERFACE_SYNC_INSTALLED = True


__all__ = ["INTERFACE_SYNC_VERSION", "install_v2177_interface_sync"]
