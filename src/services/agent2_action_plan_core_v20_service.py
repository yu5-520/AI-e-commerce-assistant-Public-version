"""V22 autonomous Agent2 action-plan core.

Each provider call contains one Agent1-locked family and a compact capability
and metric digest. Agent2 authors the smallest complete execution path; code
validates objects, numeric authority, Plan IR, provenance and family isolation.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from src.runtime_version import VERSION
from src.services.action_plan_ir_v214_service import (
    ROAS_FAMILIES,
    missing_action_plan_ir,
    normalize_action_plan_ir,
)
from src.services.agent2_provenance_v2141_service import (
    agent2_proof_missing_reason,
    call_json_with_item_provenance,
    proof_for_package,
    provider_has_valid_agent2_proof,
    provider_summary,
)
from src.services.agent_rag_context_v2028_service import rag_context_summary
from src.services.route_action_department_matrix_v1915_service import (
    attach_matrix_dispatch,
    selected_family,
)

AGENT2_ACTION_PLAN_CORE_VERSION = VERSION
AGENT2_PROVENANCE_VERSION = VERSION
AGENT_RAG_CONTEXT_VERSION = VERSION
REAL_AGENT2_PROVIDER_REQUIRED = True
TIMEOUT_SECONDS = int(os.getenv("ACTION_PLAN_AGENT_TIMEOUT", "180"))
MAX_PACKAGES_PER_CALL = int(os.getenv("ACTION_PLAN_AGENT_BATCH_SIZE", "5"))

_TEMPLATE_MARKERS = {
    "核心场景词",
    "核心卖点",
    "标题方向一",
    "主图方向一",
    "使用场景等占位词",
    "商品主体+核心场景+关键卖点",
    "设计2-3组新标题和主图变体",
}
_INVENTORY_CUTOFF_PATTERN = re.compile(
    r"(?:库存|可售天数).{0,24}(?:暂停广告|停止放量|停止投放|断流|关闭计划|暂停所有)"
)
_PLAN_FIELDS = {
    "creativeDraft",
    "budgetPlan",
    "activityPlan",
    "conversionRepairPlan",
    "similarProductPlan",
}
_FAMILY_PLAN_FIELD = {
    "title_image_test": "creativeDraft",
    "roas_scale": "budgetPlan",
    "roas_guard": "budgetPlan",
    "platform_activity": "activityPlan",
    "activity_apply": "activityPlan",
    "conversion_repair": "conversionRepairPlan",
    "service_repair": "conversionRepairPlan",
    "similar_product_test": "similarProductPlan",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _chunks(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    size = max(1, size)
    return [items[index : index + size] for index in range(0, len(items), size)]


def _identity(package: Dict[str, Any]) -> Dict[str, Any]:
    product = _dict(package.get("productIdentity"))
    return {
        **product,
        "productId": package.get("productId") or product.get("productId"),
        "storeId": package.get("storeId") or product.get("storeId"),
        "productTitle": package.get("productTitle")
        or package.get("title")
        or product.get("productTitle")
        or product.get("title"),
    }


def metric_digest_for_family(package: Dict[str, Any], family: str) -> Dict[str, Any]:
    pack = _dict(package.get("actionParameterPack"))
    current = {
        key: value
        for key, value in pack.items()
        if key
        not in {
            "ragContextSummary",
            "trafficSourceSummary",
            "inventoryCoordination",
            "permissionBounds",
            "compilerRole",
            "strategyDecisionOwnedBy",
        }
        and isinstance(value, (str, int, float, bool))
        and value not in (None, "")
    }
    return {
        "version": VERSION,
        "actionFamily": family,
        "current": current,
        "recentFacts": _arr(package.get("recentFiveOrLatestFacts"))[:16],
        "permissionBounds": _dict(pack.get("permissionBounds")),
        "inventoryCoordination": _dict(pack.get("inventoryCoordination")),
        "trafficSourceSummary": _arr(pack.get("trafficSourceSummary"))[:10],
        "source": "v22_action_capability_projection",
        "fullMetricEvidenceExcluded": True,
    }


def _compact_package(package: Dict[str, Any]) -> Dict[str, Any]:
    item = attach_matrix_dispatch(package)
    family = selected_family(item)
    rag = _dict(item.get("ragContextSnapshot"))
    return {
        "packageId": item.get("packageId") or item.get("itemId"),
        "dataVersion": item.get("dataVersion"),
        "productId": item.get("productId"),
        "storeId": item.get("storeId"),
        "productIdentity": _identity(item),
        "lockedActionFamily": family,
        "agent1DecisionIR": _dict(item.get("agent1DecisionIR"))
        or _dict(_dict(item.get("agent1OperatingJudgment")).get("agent1DecisionIR")),
        "agent1OperatingJudgment": _dict(item.get("agent1OperatingJudgment")),
        "metricDigest": metric_digest_for_family(item, family),
        "capabilityPack": _dict(item.get("actionParameterPack")),
        "ragContext": {
            "version": rag.get("version"),
            "status": rag.get("status"),
            "queryFingerprint": rag.get("queryFingerprint"),
            "matchedCount": int(rag.get("matchedCount") or 0),
            "approvedCaseIds": rag.get("approvedCaseIds") or [],
            "positiveExperienceCards": rag.get("positiveExperienceCards") or [],
            "negativeCases": rag.get("negativeCases") or [],
            "agentInstruction": rag.get("agentInstruction"),
            "taskGate": False,
        },
        "contract": {
            "oneFamily": True,
            "smallestCompletePath": True,
            "fixedStepMinimum": False,
            "fabricatedObjectForbidden": True,
            "crossFamilyOutputForbidden": True,
        },
    }


def _family_instruction(family: str) -> str:
    if family in ROAS_FAMILIES:
        return (
            "仅输出有事实支撑的operationPlan.operations；预算、出价、目标ROAS和停止规则按真实需要拆分，"
            "每个操作写明operationType、target、direction、currentValue、targetValue和回滚条件。"
        )
    if family == "title_image_test":
        return "只输出creativeTestPlan，包含2-5组fullTitle、mainImageStructure和testFocusWords。"
    if family in {"platform_activity", "activity_apply"}:
        return "只输出activityPlan，写清活动对象、门槛、权益、周期、毛利边界、承接条件和退出条件。"
    if family in {"conversion_repair", "service_repair"}:
        return "只输出conversionRepairPlan，写清问题节点、执行动作、验证周期和停止条件。"
    if family == "similar_product_test":
        return "只输出similarProductPlan，写清对照对象、唯一变量、执行周期和复盘指标。"
    raise ValueError("unsupported_locked_action_family")


def _build_messages(
    data_version: str | None,
    packages: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    families = {selected_family(attach_matrix_dispatch(item)) for item in packages}
    if len(families) != 1:
        raise ValueError("v22_one_locked_action_family_required_per_call")
    family = next(iter(families))
    payload = {
        "dataVersion": data_version,
        "version": VERSION,
        "lockedActionFamily": family,
        "packages": [_compact_package(item) for item in packages],
    }
    prompt = (
        f"你是V22经营执行Agent2。Agent1已经锁定唯一动作族{family}，你不得改变动作族。"
        "根据Agent1DecisionIR、真实执行对象、参数、权限边界、执行经验和失败反例，自主设计当前商品最小但完整的执行路径。"
        "不要填固定模板，不要求固定步骤数；简单任务可以两步，复杂任务按实际需要展开。每个步骤必须产生独立操作、"
        "验证结果或决策分支，禁止为了数量重复预算、监控、暂停和恢复。库存只能作为启动前置条件或仓储协同，"
        "不能成为ROI绩效回滚理由。只使用事实包中存在的对象和数字，不得编造计划ID。"
        + _family_instruction(family)
        + "输出需包含packageId,productId,storeId,actionFamily,actionPlanStatus,finalTaskTitle,operationMode,"
        "differentiationReason,executionObject,operationPlan,executionParameters,operatorActionSteps,executionSteps,"
        "decisionBranches,submissionEvidence,crossDepartmentActions,reviewMetrics,missingData。preconditions和monitoringPlan按需输出。"
        "事实不足返回action_plan_missing_data。只返回严格JSON对象，顶层plans数组。"
    )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
    ], payload


def _valid_creative_plan(value: Any) -> Dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    clean: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for group in _arr(value.get("groups"))[:5]:
        if not isinstance(group, dict):
            rejected.append({"reason": "group_not_object"})
            continue
        text = json.dumps(group, ensure_ascii=False)
        if any(marker in text for marker in _TEMPLATE_MARKERS):
            rejected.append({"reason": "template_placeholder"})
            continue
        title = _text(group.get("fullTitle"))
        image = group.get("mainImageStructure")
        focus = [_text(item) for item in _arr(group.get("testFocusWords")) if _text(item)]
        if not title or not isinstance(image, dict) or not image or not focus:
            rejected.append({"reason": "missing_fullTitle_mainImageStructure_or_testFocusWords"})
            continue
        clean.append({**group, "fullTitle": title, "testFocusWords": focus})
    if len(clean) < 2:
        return None
    return {
        **value,
        "groupCount": len(clean),
        "groups": clean,
        "rejectedGroups": rejected[:5],
        "source": "v22_agent2_provider_call",
        "validationStatus": "ready",
    }


def _rag_missing(raw: Dict[str, Any], package: Dict[str, Any]) -> List[str]:
    approved = {
        str(value)
        for value in _arr(_dict(package.get("ragContextSnapshot")).get("approvedCaseIds"))
        if _text(value)
    }
    if not approved:
        return []
    used = {str(value) for value in _arr(raw.get("ragUsedCaseIds")) if _text(value)}
    rejected = {str(value) for value in _arr(raw.get("ragRejectedCaseIds")) if _text(value)}
    missing: List[str] = []
    if not _text(raw.get("ragApplicationReason")):
        missing.append("ragApplicationReason")
    if (used | rejected) != approved:
        missing.append("rag_case_audit_must_cover_all_approvedCaseIds")
    if used & rejected:
        missing.append("rag_case_id_cannot_be_used_and_rejected")
    return missing


def _contract_missing(raw: Dict[str, Any], package: Dict[str, Any], family: str) -> List[str]:
    missing: List[str] = []
    for key in ("finalTaskTitle", "operationMode", "differentiationReason"):
        if not _text(raw.get(key)):
            missing.append(key)
    execution = _dict(raw.get("executionObject"))
    if not execution:
        missing.append("executionObject")
    elif not execution.get("targetId") and not execution.get("targetSelector"):
        missing.append("executionObject.targetId_or_targetSelector")
    steps = [_text(value) for value in _arr(raw.get("operatorActionSteps")) if _text(value)]
    structured = [value for value in _arr(raw.get("executionSteps")) if isinstance(value, dict) and value]
    if not steps and not structured:
        missing.append("executable_action_required")
    if _INVENTORY_CUTOFF_PATTERN.search(
        json.dumps({"steps": steps, "branches": raw.get("decisionBranches")}, ensure_ascii=False)
    ):
        missing.append("inventory_cannot_directly_cut_operator_traffic")
    if family == "title_image_test" and _valid_creative_plan(raw.get("creativeDraft")) is None:
        missing.append("creativeTestPlan.groups_min_2")
    if family in ROAS_FAMILIES:
        missing.extend(missing_action_plan_ir(raw, family))
    if family in {"platform_activity", "activity_apply"} and not isinstance(raw.get("activityPlan"), dict):
        missing.append("activityPlan")
    if family in {"conversion_repair", "service_repair"} and not isinstance(raw.get("conversionRepairPlan"), dict):
        missing.append("conversionRepairPlan")
    if family == "similar_product_test" and not isinstance(raw.get("similarProductPlan"), dict):
        missing.append("similarProductPlan")
    missing.extend(_rag_missing(raw, package))
    return list(dict.fromkeys(missing))


def _sanitize_family_fields(raw: Dict[str, Any], family: str) -> tuple[Dict[str, Any], List[str]]:
    result = dict(raw)
    allowed = _FAMILY_PLAN_FIELD.get(family)
    discarded: List[str] = []
    for field in _PLAN_FIELDS:
        if field != allowed and result.get(field) not in (None, "", [], {}):
            discarded.append(field)
        if field != allowed:
            result[field] = None
    return result, sorted(discarded)


def _normalize_plan(raw: Dict[str, Any], package: Dict[str, Any], proof: Dict[str, Any]) -> Dict[str, Any]:
    family = selected_family(package)
    returned = _text(raw.get("actionFamily") or family)
    status = _text(raw.get("actionPlanStatus") or "ready")
    conflict = raw.get("conflictReason")
    if returned != family:
        status = "conflict_requires_rejudgment"
        conflict = f"Agent2 attempted to change locked action family from {family} to {returned}."
    governed_raw = dict(raw)
    try:
        from src.services.v2174_budget_governance_service import govern_budget_operations

        governed_raw = govern_budget_operations(governed_raw, package)
    except Exception:
        governed_raw = dict(raw)
    governed_raw, discarded = _sanitize_family_fields(governed_raw, family)
    creative = _valid_creative_plan(governed_raw.get("creativeDraft")) if family == "title_image_test" else None
    operation_plan = normalize_action_plan_ir(governed_raw, family)
    normalized = {
        "stage": "agent2_action_plan",
        "version": VERSION,
        "packageId": governed_raw.get("packageId") or package.get("packageId") or package.get("itemId"),
        "productId": governed_raw.get("productId") or package.get("productId"),
        "storeId": governed_raw.get("storeId") or package.get("storeId"),
        "actionFamily": family,
        "actionPlanStatus": status if status in {"ready", "action_plan_missing_data", "conflict_requires_rejudgment"} else "action_plan_missing_data",
        "conflictReason": conflict,
        "finalTaskTitle": governed_raw.get("finalTaskTitle"),
        "operationMode": governed_raw.get("operationMode"),
        "differentiationReason": governed_raw.get("differentiationReason"),
        "executionObject": _dict(governed_raw.get("executionObject")),
        "operationPlan": operation_plan,
        "creativeDraft": creative,
        "budgetPlan": governed_raw.get("budgetPlan") if family in ROAS_FAMILIES and isinstance(governed_raw.get("budgetPlan"), dict) else None,
        "activityPlan": governed_raw.get("activityPlan") if family in {"platform_activity", "activity_apply"} and isinstance(governed_raw.get("activityPlan"), dict) else None,
        "conversionRepairPlan": governed_raw.get("conversionRepairPlan") if family in {"conversion_repair", "service_repair"} and isinstance(governed_raw.get("conversionRepairPlan"), dict) else None,
        "similarProductPlan": governed_raw.get("similarProductPlan") if family == "similar_product_test" and isinstance(governed_raw.get("similarProductPlan"), dict) else None,
        "executionParameters": _dict(governed_raw.get("executionParameters")),
        "preconditions": _arr(governed_raw.get("preconditions")),
        "monitoringPlan": _arr(governed_raw.get("monitoringPlan")),
        "operatorActionSteps": [_text(value) for value in _arr(governed_raw.get("operatorActionSteps")) if _text(value)],
        "executionSteps": [value for value in _arr(governed_raw.get("executionSteps")) if isinstance(value, dict) and value],
        "decisionBranches": [value for value in _arr(governed_raw.get("decisionBranches")) if isinstance(value, dict) and value],
        "submissionEvidence": [value for value in _arr(governed_raw.get("submissionEvidence")) if isinstance(value, dict) and value],
        "crossDepartmentActions": [value for value in _arr(governed_raw.get("crossDepartmentActions")) if isinstance(value, dict) and value],
        "ragUsedCaseIds": [str(value) for value in _arr(governed_raw.get("ragUsedCaseIds"))],
        "ragRejectedCaseIds": [str(value) for value in _arr(governed_raw.get("ragRejectedCaseIds"))],
        "ragApplicationReason": _text(governed_raw.get("ragApplicationReason")),
        "ragContextSummary": rag_context_summary(_dict(package.get("ragContextSnapshot"))),
        "reviewMetrics": [str(value) for value in _arr(governed_raw.get("reviewMetrics"))],
        "missingData": [str(value) for value in _arr(governed_raw.get("missingData"))],
        "reason": governed_raw.get("reason"),
        "agent2ExecutionProof": proof,
        "agent2Source": "validated_exact_semantic_replay" if proof.get("exactReplayValidated") else "llm_provider_call",
        "fallbackAllowed": False,
        "provenanceVersion": VERSION,
        "singleActionContractVersion": VERSION,
        "discardedCrossFamilyFields": discarded,
        "metricDigest": metric_digest_for_family(package, family),
    }
    missing = _contract_missing(normalized, package, family)
    normalized["semanticContractMissing"] = missing
    if normalized["actionPlanStatus"] == "ready" and missing:
        normalized["actionPlanStatus"] = (
            "conflict_requires_rejudgment"
            if "inventory_cannot_directly_cut_operator_traffic" in missing
            else "action_plan_missing_data"
        )
        normalized["conflictReason"] = "Agent2 output did not satisfy V22 contract: " + ",".join(missing)
        normalized["reason"] = normalized["conflictReason"]
    normalized["activeActionContract"] = active_action_contract(normalized)
    return normalized


def active_action_contract(plan: Dict[str, Any]) -> Dict[str, Any]:
    family = _text(plan.get("actionFamily"))
    family_field = _FAMILY_PLAN_FIELD.get(family)
    return {
        "version": VERSION,
        "activeActionFamily": family,
        "activeOperationPlan": _dict(plan.get("operationPlan")),
        "activeFamilyPlan": _dict(plan.get(family_field)) if family_field else {},
        "activeSopPlan": {
            "operatorActionSteps": _arr(plan.get("operatorActionSteps")),
            "executionSteps": _arr(plan.get("executionSteps")),
            "decisionBranches": _arr(plan.get("decisionBranches")),
            "submissionEvidence": _arr(plan.get("submissionEvidence")),
            "reviewMetrics": _arr(plan.get("reviewMetrics")),
        },
        "supportingCoordination": _arr(plan.get("crossDepartmentActions")),
        "source": "v22_single_locked_action_family",
    }


def provider_has_real_agent2_call(
    provider: Dict[str, Any] | None,
    package_id: str | None = None,
    proof: Dict[str, Any] | None = None,
) -> bool:
    return provider_has_valid_agent2_proof(provider, package_id, proof)


def real_agent2_provider_missing_reason(
    provider: Dict[str, Any] | None,
    package_id: str | None = None,
    proof: Dict[str, Any] | None = None,
) -> str | None:
    return agent2_proof_missing_reason(provider, package_id, proof)


def call_agent2_action_plans(
    packages: List[Dict[str, Any]],
    data_version: str | None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    if not packages:
        return {}, {
            "providerStatus": "no_packages",
            "actualCalls": 0,
            "itemProvenance": {},
            "fallbackUsed": False,
            "agent2ActionPlanCoreVersion": VERSION,
        }
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for package in packages:
        grouped[selected_family(attach_matrix_dispatch(package))].append(package)
    plans: Dict[str, Dict[str, Any]] = {}
    summaries: List[Dict[str, Any]] = []
    errors: List[str] = []
    for family in sorted(grouped):
        for batch in _chunks(grouped[family], MAX_PACKAGES_PER_CALL):
            by_id = {str(item.get("packageId") or item.get("itemId")): item for item in batch}
            try:
                messages, cache_payload = _build_messages(data_version, batch)
                payload, usage = call_json_with_item_provenance(
                    stage="action_plan_judgment_agent",
                    prompt_version=VERSION,
                    messages=messages,
                    temperature=0.12,
                    timeout_seconds=TIMEOUT_SECONDS,
                    cache_payload=cache_payload,
                    cache_enabled=True,
                )
                summary = provider_summary(usage)
                summary["actionFamily"] = family
                summaries.append(summary)
                raw_plans = payload.get("plans") if isinstance(payload, dict) else None
                if not isinstance(raw_plans, list):
                    raise ValueError("agent2_json_missing_plans_array")
                for raw in raw_plans:
                    if not isinstance(raw, dict):
                        continue
                    package_id = _text(raw.get("packageId"))
                    package = by_id.get(package_id)
                    proof = proof_for_package(summary, package_id)
                    if package and proof:
                        plans[package_id] = _normalize_plan(raw, package, proof)
            except Exception as exc:
                errors.append(f"{family}:{str(exc)[:450]}")
    all_proofs: Dict[str, Dict[str, Any]] = {}
    for summary in summaries:
        all_proofs.update(_dict(summary.get("itemProvenance")))
    provider = {
        "providerStatus": "ok" if plans and not errors else "partial" if plans else "failed",
        "actualCalls": sum(int(item.get("actualCalls") or 0) for item in summaries),
        "idempotentReplays": sum(int(item.get("idempotentReplays") or 0) for item in summaries),
        "cacheHits": sum(int(item.get("cacheHits") or 0) for item in summaries),
        "inputTokens": sum(int(item.get("inputTokens") or 0) for item in summaries),
        "outputTokens": sum(int(item.get("outputTokens") or 0) for item in summaries),
        "itemProvenance": all_proofs,
        "errors": errors,
        "fallbackUsed": False,
        "fallbackAllowed": False,
        "agent2ActionPlanCoreVersion": VERSION,
        "singleActionContractVersion": VERSION,
        "groupedActionFamilies": sorted(grouped),
        "familyCallCount": len(summaries),
        "cacheEnabled": True,
    }
    return plans, provider


def attach_agent2_action_plans(
    packages: List[Dict[str, Any]],
    plans: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for package in packages:
        item = dict(package)
        plan = plans.get(str(item.get("packageId") or item.get("itemId") or ""))
        if plan:
            item.update(
                agent2ActionPlan=plan,
                operationPlan=plan.get("operationPlan"),
                agent2ExecutionProof=plan.get("agent2ExecutionProof"),
                actionPlanStatus=plan.get("actionPlanStatus"),
                agent2Source=plan.get("agent2Source"),
                actionPlanSource="v22_agent2_autonomous_core",
                activeActionContract=plan.get("activeActionContract"),
                metricDigest=plan.get("metricDigest"),
                singleActionContractVersion=VERSION,
                fallbackAllowed=False,
            )
            item.pop("plan", None)
        enriched.append(item)
    return enriched


__all__ = [
    "AGENT2_ACTION_PLAN_CORE_VERSION",
    "call_agent2_action_plans",
    "attach_agent2_action_plans",
    "provider_has_real_agent2_call",
    "real_agent2_provider_missing_reason",
    "active_action_contract",
    "metric_digest_for_family",
]
