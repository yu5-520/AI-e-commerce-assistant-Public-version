"""V22 canonical Agent runtime contract.

Every Agent station reads and writes pipeline_items.payload. Action family and
route are read only from Agent1's canonical locks; observation is a legal terminal
result and no downstream field guessing or default route is permitted.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from src.repositories.sqlite_repository import loads
from src.runtime_version import VERSION

AGENT_RUNTIME_CONTRACT_VERSION = VERSION
AGENT1_JUDGMENT_CONTRACT_VERSION = VERSION
MATRIX_DISPATCH_CONTRACT_VERSION = VERSION
ACTION_PACK_CONTRACT_VERSION = VERSION
AGENT2_PLAN_CONTRACT_VERSION = VERSION
SOP_DECISION_CONTRACT_VERSION = VERSION
SOURCE_PIPELINE_ITEMS_ONLY = "pipeline_items.payload_only"
FORBIDDEN_RUNTIME_SOURCES = [
    "agent_product_judgments_v15",
    "product_judgment_packages_v15",
    "task_generation_decisions_v15",
    "frontend_task_view",
]


def blank(value: Any) -> bool:
    return value is None or (
        isinstance(value, str)
        and value.strip() in {"", "—", "未识别", "UNKNOWN", "null", "None"}
    )


def first_present(*values: Any) -> Any:
    for value in values:
        if not blank(value):
            return value
    return None


def safe_load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def deep_find(obj: Any, keys: List[str]) -> Any:
    """Read-only historical helper. V22 runtime decisions never use this for locks."""
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and not blank(obj.get(key)):
                return obj.get(key)
        for value in obj.values():
            found = deep_find(value, keys)
            if not blank(found):
                return found
    elif isinstance(obj, list):
        for value in obj[:12]:
            found = deep_find(value, keys)
            if not blank(found):
                return found
    return None


def merge_current(*parts: Dict[str, Any] | None) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        for key, value in part.items():
            if blank(value):
                continue
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = merge_current(out[key], value)
            else:
                out[key] = value
    return out


def deep_merge_keep(base: Dict[str, Any] | None, *extras: Dict[str, Any] | None) -> Dict[str, Any]:
    return merge_current(base, *extras)


def payload_from_row(row: Any) -> Dict[str, Any]:
    try:
        raw = safe_load(row["payload"])
    except Exception:
        raw = safe_load(row.get("payload") if isinstance(row, dict) else None)
    nested = raw.get("payload") if isinstance(raw.get("payload"), dict) else None
    if nested is not None:
        return merge_current({key: value for key, value in raw.items() if key != "payload"}, nested)
    return raw


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(value or "") for value in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def _row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        return row.get(key) if isinstance(row, dict) else None


def product_id_of(package: Dict[str, Any], row: Any | None = None) -> Any:
    return first_present(
        _row_value(row, "product_id") if row is not None else None,
        package.get("productId"),
        package.get("product_id"),
        deep_find(package.get("productIdentity"), ["productId", "product_id"]),
    )


def store_id_of(package: Dict[str, Any], row: Any | None = None) -> Any:
    return first_present(
        _row_value(row, "store_id") if row is not None else None,
        package.get("storeId"),
        package.get("store_id"),
        deep_find(package.get("productIdentity"), ["storeId", "store_id"]),
    )


def product_title_of(package: Dict[str, Any]) -> Any:
    return first_present(
        package.get("productTitle"),
        package.get("title"),
        deep_find(package.get("productIdentity"), ["productTitle", "title", "shortName"]),
    )


def _agent1(package: Dict[str, Any]) -> Dict[str, Any]:
    return package.get("agent1OperatingJudgment") if isinstance(package.get("agent1OperatingJudgment"), dict) else {}


def _decision_type(package: Dict[str, Any]) -> str:
    agent1 = _agent1(package)
    ir = package.get("agent1DecisionIR") if isinstance(package.get("agent1DecisionIR"), dict) else {}
    if not ir:
        ir = agent1.get("agent1DecisionIR") if isinstance(agent1.get("agent1DecisionIR"), dict) else {}
    return str(ir.get("decisionType") or agent1.get("decisionType") or package.get("decisionType") or "").strip().lower()


def action_family_of(package: Dict[str, Any]) -> Any:
    agent1 = _agent1(package)
    lock = agent1.get("actionFamilyLock") if isinstance(agent1.get("actionFamilyLock"), dict) else {}
    if _decision_type(package) == "observe" or lock.get("observationOnly") is True:
        return None
    if lock.get("locked") is True and lock.get("forbiddenOverride") is True:
        return first_present(lock.get("selectedActionFamily"), agent1.get("selectedActionFamily"))
    return None


def route_of(package: Dict[str, Any], family: Any = None) -> Any:
    del family
    agent1 = _agent1(package)
    lock = agent1.get("routeLock") if isinstance(agent1.get("routeLock"), dict) else {}
    if lock.get("locked") is True:
        return lock.get("selectedOperatingRoute")
    return None


def normalize_agent1_judgment(raw: Dict[str, Any], family: Any = None, route: Any = None) -> Dict[str, Any]:
    source = raw.get("agent1OperatingJudgment") if isinstance(raw.get("agent1OperatingJudgment"), dict) else raw
    decision_type = str(
        source.get("decisionType")
        or (source.get("agent1DecisionIR") or {}).get("decisionType")
        or raw.get("decisionType")
        or "act"
    ).strip().lower()
    if decision_type == "observe":
        family = None
        route = "observe"
    else:
        family = first_present(family, source.get("selectedActionFamily"))
        route = first_present(route, source.get("selectedOperatingRoute"))
    return {
        **source,
        "stage": "agent1_contextual_diagnosis",
        "version": VERSION,
        "displayInDetail": True,
        "decisionType": decision_type,
        "selectedOperatingRoute": route,
        "selectedActionFamily": family,
        "routeLock": {
            **(source.get("routeLock") if isinstance(source.get("routeLock"), dict) else {}),
            "locked": True,
            "selectedOperatingRoute": route,
            "observationOnly": decision_type == "observe",
        },
        "actionFamilyLock": {
            **(source.get("actionFamilyLock") if isinstance(source.get("actionFamilyLock"), dict) else {}),
            "locked": True,
            "selectedActionFamily": family,
            "forbiddenOverride": True,
            "observationOnly": decision_type == "observe",
        },
        "contractVersion": VERSION,
    }


def normalize_agent1_completed_contract(
    *,
    item: Dict[str, Any] | None = None,
    signal: Dict[str, Any] | None = None,
    judgment: Dict[str, Any] | None = None,
    provider: Dict[str, Any] | None = None,
    data_version: str | None = None,
) -> Dict[str, Any]:
    item = item or {}
    signal = signal or {}
    judgment = judgment or {}
    provider = provider or {}
    base = merge_current(signal, judgment)
    product_id = first_present(item.get("product_id"), product_id_of(base))
    store_id = first_present(item.get("store_id"), store_id_of(base))
    signal_id = first_present(item.get("signal_id"), base.get("signalId"), base.get("signal_id"))
    title = product_title_of(base)
    agent1 = normalize_agent1_judgment(judgment)
    decision_type = str(agent1.get("decisionType") or "act")
    family = agent1.get("actionFamilyLock", {}).get("selectedActionFamily")
    route = agent1.get("routeLock", {}).get("selectedOperatingRoute")
    payload = merge_current(
        signal,
        {
            "version": VERSION,
            "contractVersion": VERSION,
            "dataVersion": first_present(data_version, item.get("data_version"), base.get("dataVersion")),
            "itemId": item.get("item_id") or base.get("itemId"),
            "productId": product_id,
            "storeId": store_id,
            "signalId": signal_id,
            "productTitle": title,
            "title": title,
            "productIdentity": merge_current(
                base.get("productIdentity") if isinstance(base.get("productIdentity"), dict) else {},
                {"productId": product_id, "storeId": store_id, "productTitle": title, "title": title},
            ),
            "systemFacts": base.get("systemFacts") if isinstance(base.get("systemFacts"), dict) else signal,
            "metricEvidence": base.get("metricEvidence") if isinstance(base.get("metricEvidence"), dict) else base.get("metricLayer") if isinstance(base.get("metricLayer"), dict) else {},
            "signalEvidence": base.get("signalEvidence") or signal,
            "agent1OperatingJudgment": agent1,
            "agent1DecisionIR": agent1.get("agent1DecisionIR") or judgment.get("agent1DecisionIR"),
            "decisionType": decision_type,
            "selectedOperatingRoute": route,
            "selectedActionFamily": family,
            "actionFamily": family,
            "route": route,
            "providerStatus": provider.get("providerStatus"),
            "provider": provider,
            "fallbackAllowed": False,
            "lineage": {
                "currentStage": "observed_soft_gate" if decision_type == "observe" else "agent1_completed",
                "completedStages": ["agent1_completed"],
                "source": SOURCE_PIPELINE_ITEMS_ONLY,
            },
            "outputContract": "V22.agent1_completed",
        },
    )
    payload.pop("selectedActionFamilyHint", None)
    if decision_type == "observe":
        payload.update(
            observationOnly=True,
            taskAdmissionAllowed=False,
            actionFamily=None,
            selectedActionFamily=None,
            route="observe",
            selectedOperatingRoute="observe",
        )
        payload.pop("matrixDispatch", None)
    else:
        payload["matrixDispatch"] = {
            "version": VERSION,
            "routeId": route,
            "selectedActionFamily": family,
            "source": "agent1_immutable_lock",
            "lockedByAgent1": True,
            "routeActionConsistency": "passed",
            "fallbackAllowed": False,
        }
    return payload


def normalize_action_pack_ready_contract(package: Dict[str, Any], pack: Dict[str, Any]) -> Dict[str, Any]:
    family = action_family_of(package)
    package_id = first_present(
        package.get("packageId"),
        stable_id("PKG", package.get("dataVersion"), product_id_of(package), package.get("signalId"), package.get("itemId")),
    )
    lineage = package.get("lineage") if isinstance(package.get("lineage"), dict) else {}
    completed = list(lineage.get("completedStages") or [])
    if "action_pack_ready" not in completed:
        completed.append("action_pack_ready")
    return merge_current(
        package,
        {
            "version": VERSION,
            "contractVersion": VERSION,
            "packageId": package_id,
            "productId": product_id_of(package),
            "actionFamily": family,
            "selectedActionFamily": family,
            "route": route_of(package, family),
            "actionParameterPack": pack,
            "actionPackStatus": pack.get("status"),
            "lineage": {**lineage, "currentStage": "action_pack_ready", "completedStages": completed, "source": SOURCE_PIPELINE_ITEMS_ONLY},
            "outputContract": "V22.action_pack_ready",
            "fallbackAllowed": False,
        },
    )


def normalize_agent2_completed_contract(package: Dict[str, Any], plan: Dict[str, Any], provider: Dict[str, Any] | None = None) -> Dict[str, Any]:
    provider = provider or {}
    lineage = package.get("lineage") if isinstance(package.get("lineage"), dict) else {}
    completed = list(lineage.get("completedStages") or [])
    if "agent2_completed" not in completed:
        completed.append("agent2_completed")
    return merge_current(
        package,
        {
            "version": VERSION,
            "contractVersion": VERSION,
            "actionFamily": action_family_of(package),
            "agent2ActionPlan": plan,
            "operationPlan": plan.get("operationPlan"),
            "activeActionContract": plan.get("activeActionContract"),
            "metricDigest": plan.get("metricDigest"),
            "actionPlanStatus": plan.get("actionPlanStatus"),
            "providerStatus": provider.get("providerStatus"),
            "agent2Provider": provider,
            "agent2Source": plan.get("agent2Source") or "llm_provider_call",
            "agent2ExecutionProof": plan.get("agent2ExecutionProof"),
            "fallbackAllowed": False,
            "lineage": {**lineage, "currentStage": "agent2_completed", "completedStages": completed, "source": SOURCE_PIPELINE_ITEMS_ONLY},
            "outputContract": "V22.agent2_completed",
        },
    )


def normalize_sop_mapped_contract(package: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    lineage = package.get("lineage") if isinstance(package.get("lineage"), dict) else {}
    completed = list(lineage.get("completedStages") or [])
    if "sop_mapped" not in completed:
        completed.append("sop_mapped")
    decision_id = first_present(
        decision.get("decisionId"),
        stable_id("TGD-ITEM", package.get("dataVersion"), package.get("packageId"), package.get("productId")),
    )
    decision["decisionId"] = decision_id
    return merge_current(
        package,
        {
            "version": VERSION,
            "contractVersion": VERSION,
            "decisionId": decision_id,
            "sopDecision": decision,
            "sopSteps": decision.get("operatorExecutionSop") or (decision.get("taskPlan") or {}).get("operatorExecutionSop") or [],
            "activeActionContract": decision.get("activeActionContract") or (decision.get("taskPlan") or {}).get("activeActionContract"),
            "lineage": {**lineage, "currentStage": "sop_mapped", "completedStages": completed, "source": SOURCE_PIPELINE_ITEMS_ONLY},
            "chainIntegrity": {"passed": True, "source": "V22 SOP mapped payload", "completedStages": completed},
            "outputContract": "V22.sop_mapped",
        },
    )


def normalize_task_admitted_contract(package: Dict[str, Any], admission: Dict[str, Any]) -> Dict[str, Any]:
    lineage = package.get("lineage") if isinstance(package.get("lineage"), dict) else {}
    completed = list(lineage.get("completedStages") or [])
    if "task_admitted" not in completed:
        completed.append("task_admitted")
    return merge_current(
        package,
        {
            "version": VERSION,
            "contractVersion": VERSION,
            "taskAdmission": admission,
            "taskId": first_present(admission.get("taskId"), package.get("taskId")),
            "lineage": {**lineage, "currentStage": "task_admitted", "completedStages": completed, "source": SOURCE_PIPELINE_ITEMS_ONLY},
            "chainIntegrity": {"passed": True, "source": "V22 task admitted payload", "completedStages": completed},
            "outputContract": "V22.task_admitted",
        },
    )


def missing_agent1_contract(package: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    if blank(product_id_of(package)):
        missing.append("productId")
    if blank(store_id_of(package)):
        missing.append("storeId")
    if blank(product_title_of(package)):
        missing.append("productTitle/title")
    agent1 = _agent1(package)
    if not agent1:
        return [*missing, "agent1OperatingJudgment"]
    route_lock = agent1.get("routeLock") if isinstance(agent1.get("routeLock"), dict) else {}
    family_lock = agent1.get("actionFamilyLock") if isinstance(agent1.get("actionFamilyLock"), dict) else {}
    decision_type = _decision_type(package)
    if route_lock.get("locked") is not True or blank(route_lock.get("selectedOperatingRoute")):
        missing.append("agent1OperatingJudgment.routeLock")
    if family_lock.get("locked") is not True or family_lock.get("forbiddenOverride") is not True:
        missing.append("agent1OperatingJudgment.actionFamilyLock")
    if decision_type == "observe":
        if family_lock.get("selectedActionFamily") not in {None, ""}:
            missing.append("observe.actionFamily_null")
        if route_lock.get("selectedOperatingRoute") != "observe":
            missing.append("observe.route")
        return list(dict.fromkeys(missing))
    if blank(family_lock.get("selectedActionFamily")):
        missing.append("agent1OperatingJudgment.actionFamilyLock.selectedActionFamily")
    if blank(route_lock.get("selectedOperatingRoute")):
        missing.append("agent1OperatingJudgment.routeLock.selectedOperatingRoute")
    return list(dict.fromkeys(missing))


def missing_action_pack_contract(package: Dict[str, Any]) -> List[str]:
    missing = missing_agent1_contract(package)
    if _decision_type(package) == "observe":
        return list(dict.fromkeys(missing))
    family = action_family_of(package)
    pack = package.get("actionParameterPack")
    if not isinstance(pack, dict) or not pack:
        missing.append("actionParameterPack")
    else:
        if str(pack.get("status") or "") not in {"valid", "creative_context_ready", "ready"}:
            missing.append("actionParameterPack.status_ready")
        if str(pack.get("actionFamily") or "") != str(family or ""):
            missing.append("actionParameterPack.actionFamily_matches_agent1_lock")
        if pack.get("compilerRole") != "facts_permissions_and_numeric_limits_only":
            missing.append("actionParameterPack.compilerRole")
    matrix = package.get("matrixDispatch") if isinstance(package.get("matrixDispatch"), dict) else {}
    if not matrix or matrix.get("lockedByAgent1") is not True:
        missing.append("matrixDispatch.lockedByAgent1")
    elif str(matrix.get("selectedActionFamily") or "") != str(family or ""):
        missing.append("matrixDispatch.selectedActionFamily_matches_agent1_lock")
    return list(dict.fromkeys(missing))


def missing_agent2_contract(package: Dict[str, Any]) -> List[str]:
    missing = missing_action_pack_contract(package)
    plan = package.get("agent2ActionPlan")
    if not isinstance(plan, dict) or not plan:
        missing.append("agent2ActionPlan")
        return list(dict.fromkeys(missing))
    if str(plan.get("actionPlanStatus") or "") != "ready":
        missing.append("agent2ActionPlan.actionPlanStatus_ready")
    if plan.get("fallbackAllowed") is True:
        missing.append("agent2ActionPlan.fallback_not_allowed")
    if plan.get("semanticContractMissing"):
        missing.append("agent2ActionPlan.semanticContractMissing_empty")
    operator_steps = [item for item in plan.get("operatorActionSteps") or [] if str(item).strip()]
    structured_steps = [item for item in plan.get("executionSteps") or [] if isinstance(item, dict) and item]
    if not operator_steps and not structured_steps:
        missing.append("agent2ActionPlan.executable_action_required")
    if str(plan.get("actionFamily") or "") != str(action_family_of(package) or ""):
        missing.append("agent2ActionPlan.actionFamily_matches_agent1_lock")
    if not isinstance(plan.get("agent2ExecutionProof") or package.get("agent2ExecutionProof"), dict):
        missing.append("agent2ExecutionProof")
    return list(dict.fromkeys(missing))


def missing_sop_contract(package: Dict[str, Any]) -> List[str]:
    missing = missing_agent2_contract(package)
    decision = package.get("sopDecision") if isinstance(package.get("sopDecision"), dict) else {}
    if not decision:
        missing.append("sopDecision")
    else:
        steps = decision.get("operatorExecutionSop") or (decision.get("taskPlan") or {}).get("operatorExecutionSop")
        if not [item for item in steps or [] if str(item).strip()]:
            missing.append("sopDecision.operatorExecutionSop")
        if not isinstance(decision.get("agent2ExecutionProof") or (decision.get("taskPlan") or {}).get("agent2ExecutionProof"), dict):
            missing.append("sopDecision.agent2ExecutionProof")
    return list(dict.fromkeys(missing))
