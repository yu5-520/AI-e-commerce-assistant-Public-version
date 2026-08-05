"""V21.7.7 Agent2 single-action contract and input-cost governance.

The runtime before V21.7.7 locked ``actionFamily`` but still sent the complete
metric warehouse to Agent2 and exposed every family plan field in one schema.
That allowed a ROAS task to carry a creative plan, inflated provider input, and
made later projections choose different plans.

This overlay keeps the full fact warehouse outside Agent2, builds a compact
family-specific metric digest, groups provider calls by locked action family,
uses a family-specific output contract, discards cross-family plan fields, and
publishes one ``activeActionContract`` for SOP, authority and UI projections.
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Tuple

from src.services.route_action_department_matrix_v1915_service import (
    attach_matrix_dispatch,
    selected_family,
)

AGENT2_SINGLE_ACTION_CONTRACT_VERSION = "21.7.7"
METRIC_DIGEST_VERSION = "21.7.7"
ACTIVE_ACTION_CONTRACT_VERSION = "21.7.7"

_PLAN_FIELDS = {
    "creativeTestPlan",
    "budgetPlan",
    "activityPlan",
    "conversionRepairPlan",
    "similarProductPlan",
}

_FAMILY_PLAN_FIELD = {
    "title_image_test": "creativeTestPlan",
    "roas_scale": "budgetPlan",
    "roas_guard": "budgetPlan",
    "platform_activity": "activityPlan",
    "activity_apply": "activityPlan",
    "conversion_repair": "conversionRepairPlan",
    "service_repair": "conversionRepairPlan",
    "similar_product_test": "similarProductPlan",
}

_COMMON_OUTPUT_FIELDS = [
    "packageId",
    "productId",
    "storeId",
    "actionFamily",
    "actionPlanStatus",
    "finalTaskTitle",
    "operationMode",
    "differentiationReason",
    "executionObject",
    "operationPlan",
    "executionParameters",
    "operatorActionSteps",
    "executionSteps",
    "decisionBranches",
    "submissionEvidence",
    "crossDepartmentActions",
    "ragUsedCaseIds",
    "ragRejectedCaseIds",
    "ragApplicationReason",
    "reviewMetrics",
    "missingData",
    "reason",
]

_COMMON_METRICS = {
    "gmv": {"gmv", "salesamount", "paymentamount", "revenue", "销售额", "支付金额"},
    "orders": {"orders", "ordercount", "paidorders", "订单量", "支付订单"},
    "conversionRate": {"conversionrate", "cvr", "支付转化率", "转化率"},
    "clickThroughRate": {"clickthroughrate", "ctr", "点击率"},
    "visitors": {"visitors", "uv", "访客", "访客数"},
    "inventoryDays": {"inventorydays", "saleabledays", "可售天数", "库存天数"},
}

_FAMILY_METRICS = {
    "roas_scale": {
        "currentBudget": {"currentbudget", "budget", "当前预算"},
        "recommendedBudget": {"recommendedbudget", "targetbudget", "建议预算", "目标预算"},
        "recommendedBudgetUpperBound": {"recommendedbudgetupperbound", "budgetupperbound", "预算上限"},
        "currentROI": {"currentroi", "roi", "当前roi"},
        "safetyROI": {"safetyroi", "minroi", "最低roi", "安全线"},
        "targetROAS": {"targetroas", "roas", "目标roas"},
        "spend": {"spend", "cost", "adspend", "消耗", "广告消耗"},
        "bid": {"bid", "currentbid", "出价"},
    },
    "roas_guard": {
        "currentBudget": {"currentbudget", "budget", "当前预算"},
        "currentROI": {"currentroi", "roi", "当前roi"},
        "safetyROI": {"safetyroi", "minroi", "最低roi", "安全线"},
        "targetROAS": {"targetroas", "roas", "目标roas"},
        "spend": {"spend", "cost", "adspend", "消耗", "广告消耗"},
        "bid": {"bid", "currentbid", "出价"},
    },
    "title_image_test": {
        "clickThroughRate": {"clickthroughrate", "ctr", "点击率"},
        "clicks": {"clicks", "clickcount", "点击量"},
        "impressions": {"impressions", "exposure", "曝光", "展现量"},
        "conversionRate": {"conversionrate", "cvr", "转化率"},
    },
    "platform_activity": {
        "naturalTraffic": {"naturaltraffic", "organictraffic", "自然流量"},
        "grossMargin": {"grossmargin", "margin", "毛利率", "利润率"},
        "inventoryDays": {"inventorydays", "saleabledays", "可售天数", "库存天数"},
    },
    "activity_apply": {
        "naturalTraffic": {"naturaltraffic", "organictraffic", "自然流量"},
        "grossMargin": {"grossmargin", "margin", "毛利率", "利润率"},
        "inventoryDays": {"inventorydays", "saleabledays", "可售天数", "库存天数"},
    },
    "conversion_repair": {
        "conversionRate": {"conversionrate", "cvr", "转化率"},
        "bounceRate": {"bouncerate", "跳失率"},
        "refundRate": {"refundrate", "退款率"},
    },
    "service_repair": {
        "refundRate": {"refundrate", "退款率"},
        "afterSaleRate": {"aftersalerate", "售后率"},
        "rating": {"rating", "score", "评分", "好评率"},
    },
    "similar_product_test": {
        "conversionRate": {"conversionrate", "cvr", "转化率"},
        "clickThroughRate": {"clickthroughrate", "ctr", "点击率"},
        "gmv": {"gmv", "salesamount", "支付金额"},
    },
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def _normalized_key(value: Any) -> str:
    return "".join(ch for ch in _text(value).lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _walk(value: Any, depth: int = 0) -> Iterable[Tuple[str, Any]]:
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:40]:
            yield from _walk(child, depth + 1)


def _small_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        scalar = [item for item in value if isinstance(item, (str, int, float, bool))]
        return scalar[-5:] if scalar else None
    if isinstance(value, dict):
        selected: Dict[str, Any] = {}
        for key in (
            "value",
            "currentValue",
            "previousValue",
            "changeRate",
            "changeRatio",
            "trend",
            "direction",
            "unit",
        ):
            child = value.get(key)
            if isinstance(child, (str, int, float, bool)):
                selected[key] = child
        return selected or None
    return None


def _find_first(source: Dict[str, Any], aliases: set[str]) -> Any:
    normalized_aliases = {_normalized_key(alias) for alias in aliases}
    for key, value in _walk(source):
        if _normalized_key(key) not in normalized_aliases:
            continue
        compact = _small_value(value)
        if compact not in (None, {}, []):
            return compact
    return None


def _metric_entry_name(entry: Dict[str, Any]) -> str:
    return _text(
        entry.get("metricCode")
        or entry.get("metricName")
        or entry.get("field")
        or entry.get("code")
        or entry.get("name")
        or entry.get("label")
    )


def _matching_metric_entries(evidence: Dict[str, Any], alias_union: set[str]) -> List[Dict[str, Any]]:
    aliases = {_normalized_key(value) for value in alias_union}
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for _, value in _walk(evidence):
        if not isinstance(value, list):
            continue
        for entry in value[:40]:
            if not isinstance(entry, dict):
                continue
            name = _metric_entry_name(entry)
            if not name or _normalized_key(name) not in aliases:
                continue
            compact = {
                "metric": name,
                "current": entry.get("currentValue", entry.get("value")),
                "previous": entry.get("previousValue", entry.get("before")),
                "changeRate": entry.get("changeRate", entry.get("changeRatio")),
                "trend": entry.get("trend", entry.get("direction")),
                "unit": entry.get("unit"),
            }
            compact = {key: value for key, value in compact.items() if value not in (None, "", [], {})}
            fingerprint = json.dumps(compact, ensure_ascii=False, sort_keys=True, default=str)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            result.append(compact)
            if len(result) >= 16:
                return result
    return result


def metric_digest_for_family(package: Dict[str, Any], family: str | None = None) -> Dict[str, Any]:
    item = attach_matrix_dispatch(package)
    family = _text(family or selected_family(item))
    pack = _dict(item.get("actionParameterPack") or item.get("actionDataPack"))
    evidence = _dict(item.get("metricEvidence") or item.get("metricLayer"))
    metric_map: Dict[str, set[str]] = dict(_COMMON_METRICS)
    metric_map.update(_FAMILY_METRICS.get(family, {}))

    current: Dict[str, Any] = {}
    for canonical, aliases in metric_map.items():
        value = _find_first(pack, aliases)
        if value is None:
            value = _find_first(evidence, aliases)
        if value not in (None, "", [], {}):
            current[canonical] = value

    alias_union: set[str] = set()
    for aliases in metric_map.values():
        alias_union.update(aliases)
    recent = _matching_metric_entries(evidence, alias_union)

    coordination = _dict(pack.get("inventoryCoordination") or pack.get("coordination"))
    permissions = _dict(
        item.get("experimentPolicy")
        or _dict(item.get("crossValidation")).get("experimentPolicy")
    )
    return {
        "version": METRIC_DIGEST_VERSION,
        "actionFamily": family,
        "current": current,
        "recentFiveOrLatestFacts": recent[:16],
        "inventoryCoordination": coordination,
        "permissionBounds": {
            key: permissions.get(key)
            for key in (
                "budgetChangeCeiling",
                "trafficShareCeiling",
                "durationHours",
                "mainlineMutationAllowed",
                "operationScope",
                "targetObject",
            )
            if permissions.get(key) is not None
        },
        "source": "action_family_metric_projection",
        "fullMetricEvidenceExcluded": True,
    }


def allowed_plan_fields(family: str) -> set[str]:
    field = _FAMILY_PLAN_FIELD.get(_text(family))
    return {field} if field else set()


def _family_instruction(family: str) -> str:
    if family in {"roas_scale", "roas_guard"}:
        return (
            "必须输出operationPlan.operations和budgetPlan。每个预算、出价、目标ROAS、止损操作必须拆开，"
            "写明operationType、target、direction、currentValue、targetValue、回滚条件。"
        )
    if family == "title_image_test":
        return "必须只输出creativeTestPlan，包含2-5组fullTitle、mainImageStructure、testFocusWords。"
    if family in {"platform_activity", "activity_apply"}:
        return "必须只输出activityPlan，写明活动、门槛、价格或优惠、周期、库存承接和退出条件。"
    if family in {"conversion_repair", "service_repair"}:
        return "必须只输出conversionRepairPlan，写明问题节点、执行动作、验证周期和停止条件。"
    if family == "similar_product_test":
        return "必须只输出similarProductPlan，写明对照对象、控制变量、执行周期和复盘指标。"
    return "只输出锁定动作族需要的operationPlan和通用执行字段。"


def build_family_messages(
    data_version: str | None,
    packages: List[Dict[str, Any]],
    compact_package: Any,
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    families = {
        _text(selected_family(attach_matrix_dispatch(item)))
        for item in packages
    }
    families.discard("")
    if len(families) != 1:
        raise ValueError("V21.7.7 requires one locked action family per provider call")
    family = next(iter(families))
    specific = sorted(allowed_plan_fields(family))
    allowed = list(_COMMON_OUTPUT_FIELDS)
    for field in specific:
        if field not in allowed:
            allowed.append(field)
    payload = {
        "dataVersion": data_version,
        "version": AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
        "lockedActionFamily": family,
        "allowedOutputFields": allowed,
        "packages": [compact_package(item) for item in packages],
    }
    prompt = (
        f"你是经营链路真实Agent2。本批次动作族已锁定为{family}，不得改动作族。"
        "每个商品只生成一份正式执行方案，不得补充、建议或返回其他动作族方案。"
        f"{_family_instruction(family)}"
        "每项必须给出具体执行对象、前值、目标值、控制变量、回滚条件和提交凭证。"
        "operatorActionSteps至少4步，executionSteps至少3步，decisionBranches至少2条，submissionEvidence至少2项。"
        "库存只允许形成仓储协同，不得仅因库存低让运营断流。关键事实不足返回action_plan_missing_data。"
        "只返回严格JSON对象，顶层plans数组。每项只允许这些字段："
        + ",".join(allowed)
        + "。不得返回任何未列字段。"
    )
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ]
    return messages, payload


def active_action_contract(
    plan: Dict[str, Any],
    *,
    sop: Dict[str, Any] | None = None,
    authority: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    family = _text(plan.get("actionFamily"))
    family_field = _FAMILY_PLAN_FIELD.get(family)
    family_plan = _dict(plan.get(family_field)) if family_field else {}
    sop = _dict(sop)
    authority = _dict(authority)
    steps = (
        _arr(sop.get("operatorExecutionSop"))
        or _arr(_dict(sop.get("taskPlan")).get("operatorExecutionSop"))
        or _arr(plan.get("operatorActionSteps"))
    )
    return {
        "version": ACTIVE_ACTION_CONTRACT_VERSION,
        "activeActionFamily": family,
        "activeOperationPlan": _dict(plan.get("operationPlan")),
        "activeFamilyPlan": family_plan,
        "activeSopPlan": {
            "operatorActionSteps": steps,
            "executionSteps": _arr(plan.get("executionSteps")),
            "decisionBranches": _arr(plan.get("decisionBranches")),
            "submissionEvidence": _arr(plan.get("submissionEvidence")),
            "reviewMetrics": _arr(plan.get("reviewMetrics")),
        },
        "activeAuthority": authority,
        "supportingCoordination": _arr(plan.get("crossDepartmentActions")),
        "source": "single_locked_action_family",
    }


def sanitize_plan(plan: Dict[str, Any], raw: Dict[str, Any] | None = None) -> Dict[str, Any]:
    result = copy.deepcopy(plan)
    raw = _dict(raw)
    family = _text(result.get("actionFamily"))
    allowed = allowed_plan_fields(family)
    discarded: List[str] = []
    for field in sorted(_PLAN_FIELDS):
        source_value = raw.get(field) if field in raw else result.get(field)
        if field not in allowed:
            if _nonempty(source_value):
                discarded.append(field)
            result[field] = None
    result["discardedCrossFamilyFields"] = discarded
    result["singleActionContractVersion"] = AGENT2_SINGLE_ACTION_CONTRACT_VERSION
    result["crossFamilyFieldStatus"] = "discarded" if discarded else "clean"
    result["activeActionContract"] = active_action_contract(result)
    return result


def compact_sop_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(decision)
    plan = _dict(result.get("agent2ActionPlan") or _dict(result.get("taskPlan")).get("agent2ActionPlan"))
    plan = sanitize_plan(plan)
    contract = active_action_contract(plan, sop=result)
    result["agent2ActionPlan"] = plan
    result["activeActionContract"] = contract
    result["agent2PlanRef"] = f"agent2_plan:{plan.get('packageId') or result.get('packageId') or 'unknown'}"

    task_plan = _dict(result.get("taskPlan"))
    task_plan.pop("agent2ActionPlan", None)
    for field in _PLAN_FIELDS:
        if field not in allowed_plan_fields(_text(plan.get("actionFamily"))):
            task_plan.pop(field, None)
    task_plan["activeActionContract"] = contract
    task_plan["agent2PlanRef"] = result["agent2PlanRef"]
    result["taskPlan"] = task_plan

    product_package = _dict(result.get("productJudgmentPackage"))
    product_package.pop("agent2ActionPlan", None)
    product_package["agent2PlanRef"] = result["agent2PlanRef"]
    product_package["metricDigest"] = product_package.get("metricDigest") or {}
    result["productJudgmentPackage"] = product_package
    return result


def _merge_provider_summaries(summaries: List[Dict[str, Any]], plans: Dict[str, Dict[str, Any]], errors: List[str]) -> Dict[str, Any]:
    all_proofs: Dict[str, Dict[str, Any]] = {}
    for summary in summaries:
        all_proofs.update(_dict(summary.get("itemProvenance")))
    return {
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
        "singleActionContractVersion": AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
        "familyCallCount": len(summaries),
        "cacheEnabled": True,
    }


def install_v2177_agent2_single_action_contract() -> None:
    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import agent_runtime_contract_v2141_service as runtime_contract
    from src.services import pipeline_action_microbatch_v205_service as action_worker
    from src.services import pipeline_sop_task_pool_v2010_service as sop_worker
    from src.services import sop_builder_core_v20_service as sop_builder

    if getattr(agent2, "_V2177_SINGLE_ACTION_CONTRACT_INSTALLED", False):
        return

    original_compact = agent2._compact_package
    original_normalize = agent2._normalize_plan
    original_attach = agent2.attach_agent2_action_plans
    original_agent2_contract = runtime_contract.normalize_agent2_completed_contract
    original_sop_contract = runtime_contract.normalize_sop_mapped_contract
    original_task_contract = runtime_contract.normalize_task_admitted_contract
    original_sop_builder = sop_builder.build_sop_decision_from_package

    def compact_package_v2177(package: Dict[str, Any]) -> Dict[str, Any]:
        base = original_compact(package)
        family = _text(base.get("lockedActionFamily") or selected_family(attach_matrix_dispatch(package)))
        base.pop("metricEvidence", None)
        base["metricDigest"] = metric_digest_for_family(package, family)
        base["metricEvidenceRef"] = {
            "dataVersion": base.get("dataVersion"),
            "productId": base.get("productId"),
            "source": "fact_layer_not_in_llm_context",
        }
        base["singleActionContract"] = {
            "version": AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
            "actionFamily": family,
            "allowedPlanFields": sorted(allowed_plan_fields(family)),
        }
        return base

    def build_messages_v2177(
        data_version: str | None,
        packages: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        return build_family_messages(data_version, packages, compact_package_v2177)

    def normalize_plan_v2177(
        raw: Dict[str, Any],
        package: Dict[str, Any],
        proof: Dict[str, Any],
    ) -> Dict[str, Any]:
        return sanitize_plan(original_normalize(raw, package, proof), raw)

    def call_agent2_action_plans_v2177(
        packages: List[Dict[str, Any]],
        data_version: str | None,
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        if not packages:
            return {}, {
                "providerStatus": "no_packages",
                "actualCalls": 0,
                "itemProvenance": {},
                "fallbackUsed": False,
                "singleActionContractVersion": AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
            }
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for package in packages:
            grouped[_text(selected_family(attach_matrix_dispatch(package))) or "missing"].append(package)

        plans: Dict[str, Dict[str, Any]] = {}
        summaries: List[Dict[str, Any]] = []
        errors: List[str] = []
        for family in sorted(grouped):
            for batch in agent2._chunks(grouped[family], agent2.MAX_PACKAGES_PER_CALL):
                by_id = {
                    str(item.get("packageId") or item.get("itemId")): item
                    for item in batch
                }
                try:
                    messages, cache_payload = agent2._build_messages(data_version, batch)
                    payload, usage = agent2.call_json_with_item_provenance(
                        stage="action_plan_judgment_agent",
                        prompt_version=AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
                        messages=messages,
                        temperature=0.12,
                        timeout_seconds=agent2.TIMEOUT_SECONDS,
                        cache_payload=cache_payload,
                        cache_enabled=True,
                    )
                    summary = agent2.provider_summary(usage)
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
                        proof = agent2.proof_for_package(summary, package_id)
                        if package and proof:
                            plans[package_id] = agent2._normalize_plan(raw, package, proof)
                except Exception as exc:
                    errors.append(f"{family}:{str(exc)[:450]}")
        provider = _merge_provider_summaries(summaries, plans, errors)
        provider["agent2ActionPlanCoreVersion"] = agent2.AGENT2_ACTION_PLAN_CORE_VERSION
        provider["groupedActionFamilies"] = sorted(grouped)
        return plans, provider

    def attach_agent2_action_plans_v2177(
        packages: List[Dict[str, Any]],
        plans: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        enriched = original_attach(packages, plans)
        for item in enriched:
            plan = _dict(item.get("agent2ActionPlan"))
            if not plan:
                continue
            item["metricDigest"] = metric_digest_for_family(item, _text(plan.get("actionFamily")))
            item["activeActionContract"] = active_action_contract(plan)
            item["singleActionContractVersion"] = AGENT2_SINGLE_ACTION_CONTRACT_VERSION
        return enriched

    def normalize_agent2_contract_v2177(
        package: Dict[str, Any],
        plan: Dict[str, Any],
        provider: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        clean_plan = sanitize_plan(plan)
        base = original_agent2_contract(package, clean_plan, provider)
        base.pop("metricEvidence", None)
        base.pop("metricLayer", None)
        if base.get("plan") == base.get("agent2ActionPlan"):
            base.pop("plan", None)
        base["metricDigest"] = metric_digest_for_family(package, _text(clean_plan.get("actionFamily")))
        base["activeActionContract"] = active_action_contract(clean_plan)
        base["singleActionContractVersion"] = AGENT2_SINGLE_ACTION_CONTRACT_VERSION
        return base

    def normalize_sop_contract_v2177(
        package: Dict[str, Any],
        decision: Dict[str, Any],
    ) -> Dict[str, Any]:
        compact_decision = compact_sop_decision(decision)
        base = original_sop_contract(package, compact_decision)
        plan = _dict(base.get("agent2ActionPlan") or compact_decision.get("agent2ActionPlan"))
        base.pop("metricEvidence", None)
        base.pop("metricLayer", None)
        if base.get("plan") == base.get("agent2ActionPlan"):
            base.pop("plan", None)
        base["activeActionContract"] = active_action_contract(plan, sop=compact_decision)
        base["singleActionContractVersion"] = AGENT2_SINGLE_ACTION_CONTRACT_VERSION
        return base

    def normalize_task_contract_v2177(
        package: Dict[str, Any],
        admission: Dict[str, Any],
    ) -> Dict[str, Any]:
        base = original_task_contract(package, admission)
        decision = _dict(base.get("sopDecision") or package.get("sopDecision"))
        plan = _dict(base.get("agent2ActionPlan") or decision.get("agent2ActionPlan"))
        authority = _dict(base.get("actionAuthorization") or admission.get("authorizationDecision"))
        base.pop("metricEvidence", None)
        base.pop("metricLayer", None)
        if base.get("plan") == base.get("agent2ActionPlan"):
            base.pop("plan", None)
        base["activeActionContract"] = active_action_contract(plan, sop=decision, authority=authority)
        base["singleActionContractVersion"] = AGENT2_SINGLE_ACTION_CONTRACT_VERSION
        return base

    def build_sop_decision_v2177(*args: Any, **kwargs: Any) -> Dict[str, Any] | None:
        decision = original_sop_builder(*args, **kwargs)
        return compact_sop_decision(decision) if decision else None

    agent2._compact_package = compact_package_v2177
    agent2._build_messages = build_messages_v2177
    agent2._normalize_plan = normalize_plan_v2177
    agent2.call_agent2_action_plans = call_agent2_action_plans_v2177
    agent2.attach_agent2_action_plans = attach_agent2_action_plans_v2177

    runtime_contract.normalize_agent2_completed_contract = normalize_agent2_contract_v2177
    runtime_contract.normalize_sop_mapped_contract = normalize_sop_contract_v2177
    runtime_contract.normalize_task_admitted_contract = normalize_task_contract_v2177
    sop_builder.build_sop_decision_from_package = build_sop_decision_v2177

    action_worker.call_agent2_action_plans = call_agent2_action_plans_v2177
    action_worker.attach_agent2_action_plans = attach_agent2_action_plans_v2177
    action_worker.normalize_agent2_completed_contract = normalize_agent2_contract_v2177
    sop_worker.build_sop_decision_from_package = build_sop_decision_v2177
    sop_worker.normalize_sop_mapped_contract = normalize_sop_contract_v2177
    sop_worker.normalize_task_admitted_contract = normalize_task_contract_v2177

    for module in (agent2, runtime_contract, action_worker, sop_worker, sop_builder):
        module.AGENT2_SINGLE_ACTION_CONTRACT_VERSION = AGENT2_SINGLE_ACTION_CONTRACT_VERSION
        module._V2177_SINGLE_ACTION_CONTRACT_INSTALLED = True


__all__ = [
    "ACTIVE_ACTION_CONTRACT_VERSION",
    "AGENT2_SINGLE_ACTION_CONTRACT_VERSION",
    "METRIC_DIGEST_VERSION",
    "active_action_contract",
    "allowed_plan_fields",
    "build_family_messages",
    "compact_sop_decision",
    "install_v2177_agent2_single_action_contract",
    "metric_digest_for_family",
    "sanitize_plan",
]
