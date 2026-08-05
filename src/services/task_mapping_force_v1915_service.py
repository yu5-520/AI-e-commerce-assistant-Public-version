"""V19.15.3 task mapping station.

Task mapping does not judge, reselect actions, or write business semantics. It
receives Agent1 visible judgment, matrixDispatch, action-family data pack and
Agent2 hidden action plan, then directly assembles one decision per package.

V19.15.3 adds route/action-family consistency protection and blocks generic
conversion_repair fallback templates from entering formal operator SOP.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List

import src.services.dual_agent_product_task_service as base
import src.services.task_mapping_force_v197_service as v197
import src.services.task_mapping_force_v199_service as _legacy_mapping
from src.services.action_parameter_enrichment_v1914_service import (
    ACTION_PARAMETER_ENRICHMENT_VERSION,
    HIGH_RISK_ACTIONS,
    action_parameter_enrichment_station_v1914,
    compose_parameterized_sop,
    enrich_package_with_action_parameters,
    select_action_parameter_pack,
)
from src.services.action_plan_judgment_agent_v1915_service import action_plan_judgment_agent_station_v1915
from src.services.agent_budget_ledger_service import get_or_create_agent_budget_ledger, register_agent_event
from src.services.metric_trigger_expansion_v171_service import is_first_report_baseline
from src.services.route_action_department_matrix_v1915_service import (
    MATRIX_DISPATCH_VERSION,
    attach_matrix_dispatch,
    operator_task_title,
    selected_family as matrix_selected_family,
)

TASK_MAPPING_FORCE_V1915_VERSION = "19.15.3"
TASK_AGENT_MODE = "v1915_3_matrix_consistent_sop_assembly_and_renderer_contract"
FORMAL_DECISIONS = {"create_task_snapshot", "manager_review_required"}
STRICT_TEMPLATE_MARKERS = [
    "核心场景词",
    "核心卖点",
    "标题方向一",
    "标题方向二",
    "标题方向三",
    "主图方向一",
    "主图方向二",
    "主图方向三",
    "设计2-3组新标题和主图变体",
    "设计3组标题主图组合",
    "商品主体+核心场景+关键卖点",
    "使用场景等占位词",
]
GENERIC_CONVERSION_REPAIR_LINES = [
    "更新详情页模块并进行A/B测试",
    "配置满减券和加购赠品",
    "优化评价展示区",
    "完善客服承诺页面",
    "监控转化率、支付金额、退款率",
]

_ORIGINAL_LEGACY_LOCKED_FAMILY = _legacy_mapping._locked_family


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _has_template_placeholder(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "")
    return any(marker in text for marker in STRICT_TEMPLATE_MARKERS)


def _valid_groups(creative: Dict[str, Any]) -> List[Dict[str, Any]]:
    groups = creative.get("groups") if isinstance(creative.get("groups"), list) else []
    out: List[Dict[str, Any]] = []
    for group in groups[:5]:
        if not isinstance(group, dict):
            continue
        if _has_template_placeholder(group):
            continue
        title = str(group.get("fullTitle") or "").strip()
        if not title:
            continue
        if not isinstance(group.get("mainImageStructure"), dict):
            continue
        focus = group.get("testFocusWords")
        if not isinstance(focus, list) or not [x for x in focus if str(x).strip()]:
            continue
        out.append({**group, "fullTitle": title})
    return out


def _product_identity(package: Dict[str, Any]) -> Dict[str, Any]:
    product = _as_dict(package.get("productIdentity"))
    matrix = _as_dict(package.get("matrixDispatch"))
    display = _as_dict(matrix.get("displayTitleContract"))
    return {
        "productId": package.get("productId") or product.get("productId"),
        "storeId": package.get("storeId") or product.get("storeId"),
        "title": product.get("shortTitle") or product.get("productTitle") or product.get("title") or display.get("productTitle") or package.get("productId"),
        "platform": product.get("platform"),
        "verticalCategory": product.get("verticalCategory"),
    }


def _matrix_locked_family(package: Dict[str, Any], plan: Dict[str, Any] | None = None) -> str:
    try:
        item = attach_matrix_dispatch(package)
        return matrix_selected_family(item) or _ORIGINAL_LEGACY_LOCKED_FAMILY(item, plan)
    except Exception:
        return _ORIGINAL_LEGACY_LOCKED_FAMILY(package, plan)


def _creative_from_package(package: Dict[str, Any]) -> Dict[str, Any]:
    agent2 = _as_dict(package.get("agent2ActionPlan"))
    for value in [agent2.get("creativeTestPlan"), package.get("creativeTestPlan"), package.get("agentCreativePack")]:
        if isinstance(value, dict) and isinstance(value.get("groups"), list) and value.get("groups"):
            return value
    return {}


def _repair_agent2_creative_status(package: Dict[str, Any]) -> Dict[str, Any]:
    item = attach_matrix_dispatch(dict(package))
    family = matrix_selected_family(item)
    if family != "title_image_test":
        return item
    creative = _creative_from_package(item)
    valid = _valid_groups(creative)
    if len(valid) < 2:
        return item
    creative = {**creative, "groups": valid[:5], "groupCount": min(5, max(2, int(creative.get("groupCount") or len(valid))))}
    agent2 = _as_dict(item.get("agent2ActionPlan"))
    if agent2.get("actionPlanStatus") != "ready":
        agent2 = {**agent2, "actionPlanStatus": "ready", "statusOverriddenByCreativeGroups": True, "missingData": [], "conflictReason": None}
    agent2["operatorActionSteps"] = []
    agent2["creativeTestPlan"] = creative
    item["agent2ActionPlan"] = agent2
    item["actionPlanStatus"] = "ready"
    item["creativeTestPlan"] = creative
    item["agentCreativePack"] = creative
    return item


def _install_matrix_patch() -> None:
    _legacy_mapping.enrich_package_with_action_parameters = enrich_package_with_action_parameters
    _legacy_mapping.select_action_parameter_pack = select_action_parameter_pack
    _legacy_mapping.compose_parameterized_sop = compose_parameterized_sop
    _legacy_mapping.action_plan_judgment_agent_station_v1913 = action_plan_judgment_agent_station_v1915
    _legacy_mapping._locked_family = _matrix_locked_family
    _legacy_mapping._has_template_marker = _has_template_placeholder
    _legacy_mapping._valid_groups = _valid_groups
    _legacy_mapping.TASK_MAPPING_FORCE_V199_VERSION = TASK_MAPPING_FORCE_V1915_VERSION
    _legacy_mapping.TASK_AGENT_MODE = TASK_AGENT_MODE


def _ensure_parameters(packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for package in packages:
        item = attach_matrix_dispatch(package)
        item = enrich_package_with_action_parameters(item)
        item = attach_matrix_dispatch(item)
        family = matrix_selected_family(item)
        item["selectedActionFamilyHint"] = family
        item["actionParameterPack"] = select_action_parameter_pack(item, family)
        item = _repair_agent2_creative_status(item)
        enriched.append(item)
    if enriched:
        base._save_packages(enriched)
    return enriched


def _raw_decision_from_package(package: Dict[str, Any], data_version: str | None) -> Dict[str, Any]:
    item = attach_matrix_dispatch(package)
    product = _product_identity(item)
    family = matrix_selected_family(item)
    matrix = _as_dict(item.get("matrixDispatch"))
    title = operator_task_title(item)
    plan = {
        "title": title,
        "taskTitle": title,
        "productId": product.get("productId"),
        "storeId": product.get("storeId"),
        "productIdentity": product,
        "selectedActionFamily": family,
        "taskType": "operation_action",
        "taskResponsibility": "operator_growth",
        "departmentTaskType": matrix.get("departmentScope") or "operator_growth",
        "operatorJudgmentView": {
            "selectedDirection": _as_dict(item.get("agent1OperatingJudgment")).get("selectedOperatingRoute") or matrix.get("routeId"),
            "displayReason": _as_dict(item.get("agent1OperatingJudgment")).get("primaryOperatingGap") or _as_dict(item.get("agent1OperatingJudgment")).get("businessHypothesis"),
            "selectedActionFamilyLabel": matrix.get("selectedActionFamilyLabel"),
            "testFocus": _as_dict(item.get("agent1OperatingJudgment")).get("primaryBusinessSignal"),
            "recapBasis": "; ".join(str(x) for x in (item.get("evidenceFacts") or [])[:4]),
        },
        "agentJudgmentTrace": {"matrixDispatch": matrix, "agent1OperatingJudgment": item.get("agent1OperatingJudgment"), "agent2ActionPlan": item.get("agent2ActionPlan")},
        "priority": "高" if family in HIGH_RISK_ACTIONS else "中",
        "executionDeadline": "6小时内" if family in {"title_image_test", "platform_activity"} else "24小时内",
        "followUpDeadline": "系统自动复盘",
        "reviewCycle": "系统自动复盘",
        "assigneeRole": "operator",
        "approvalRequired": family in HIGH_RISK_ACTIONS,
        "sopSteps": [],
        "operatorExecutionSop": [],
        "evidenceRequirements": [],
        "reviewMetrics": [],
        "reason": "Agent1定路线，矩阵锁动作族，Agent2生成动作方案，任务映射只组装SOP。",
        "matrixDispatch": matrix,
    }
    return {
        "version": TASK_MAPPING_FORCE_V1915_VERSION,
        "decisionId": base.make_id("TGD"),
        "packageId": item.get("packageId"),
        "dataVersion": data_version or item.get("dataVersion"),
        "storeId": product.get("storeId"),
        "productId": product.get("productId"),
        "decision": "create_task_snapshot",
        "taskTitle": title,
        "priority": plan["priority"],
        "reason": plan["reason"],
        "taskPlan": plan,
        "productJudgmentPackage": item,
        "taskMappingAgentEvidence": {"source": "matrix_direct_sop_assembly", "mappingMode": TASK_AGENT_MODE, "matrixDispatchVersion": MATRIX_DISPATCH_VERSION, "noMappingLlm": True, "actionFamily": family},
        "rule": "V19.15.3: task mapping creates one decision per package from matrixDispatch and Agent2 plan; conversion fallback templates are blocked.",
    }


def _conversion_plan_ready(package: Dict[str, Any]) -> bool:
    agent2 = _as_dict(package.get("agent2ActionPlan"))
    plan = _as_dict(agent2.get("conversionRepairPlan"))
    steps = plan.get("steps")
    if isinstance(steps, list) and len([x for x in steps if str(x).strip()]) >= 2:
        return True
    product_specific = plan.get("productSpecificEvidence") or plan.get("productSpecificSteps")
    return isinstance(product_specific, list) and len(product_specific) >= 2


def _sop_has_generic_conversion_template(plan: Dict[str, Any]) -> bool:
    lines = plan.get("operatorExecutionSop") or plan.get("sopSteps") or []
    text = "\n".join(str(x) for x in lines)
    return any(line in text for line in GENERIC_CONVERSION_REPAIR_LINES)


def _parameterized_decision(package: Dict[str, Any], data_version: str | None) -> Dict[str, Any] | None:
    package = _repair_agent2_creative_status(package)
    raw = _raw_decision_from_package(package, data_version)
    package_by_id = {str(package.get("packageId")): package}
    item = _legacy_mapping._parameterize_decision(raw, package_by_id, data_version)
    if not item:
        return None
    plan = item.get("taskPlan") if isinstance(item.get("taskPlan"), dict) else {}
    title = operator_task_title(package)
    family = matrix_selected_family(package)
    if family == "conversion_repair" and _sop_has_generic_conversion_template(plan) and not _conversion_plan_ready(package):
        pack = select_action_parameter_pack(package, family)
        blocked = _legacy_mapping._data_evidence_decision(package, family, pack, data_version, "conversion_repair 缺少商品级Agent2转化修复方案，禁止通用模板进入正式运营任务。")
        blocked["version"] = TASK_MAPPING_FORCE_V1915_VERSION
        blocked["taskTitle"] = f"系统生成异常｜{_product_identity(package).get('title') or package.get('productId')}｜conversion_repair模板拦截"
        blocked["rule"] = "V19.15.3: generic conversion_repair fallback templates are internal generation exceptions, not operator SOP."
        return blocked
    if plan:
        plan["title"] = title
        plan["taskTitle"] = title
        plan["matrixDispatch"] = package.get("matrixDispatch")
        plan["titleSource"] = "matrixDispatch.displayTitleContract.operatorTaskTitle"
        if (plan.get("selectedActionFamily") == "title_image_test") and isinstance(package.get("creativeTestPlan"), dict):
            plan["creativeTestPlan"] = package.get("creativeTestPlan")
    item["taskTitle"] = title
    item["version"] = TASK_MAPPING_FORCE_V1915_VERSION
    evidence = item.get("taskMappingAgentEvidence") if isinstance(item.get("taskMappingAgentEvidence"), dict) else {}
    evidence.update({"mappingMode": TASK_AGENT_MODE, "matrixDispatchVersion": MATRIX_DISPATCH_VERSION, "noMappingLlm": True, "creativeStatusRepairEnabled": True, "genericConversionTemplateBlocked": False})
    item["taskMappingAgentEvidence"] = evidence
    item["rule"] = "V19.15.3: matrix dispatch owns route/family/title; mapping assembles SOP, preserves valid Agent2 creative groups and blocks generic conversion templates."
    return item


def task_mapping_agent_station_v1915(data_version: str | None, **_: Any) -> Dict[str, Any]:
    _install_matrix_patch()
    baseline = is_first_report_baseline(data_version)
    if baseline.get("isFirstReportBaseline"):
        v197._delete_version_decisions(data_version)
        return {"version": TASK_MAPPING_FORCE_V1915_VERSION, "stationId": "task_mapping_agent_station", "dataVersion": data_version, "baselineMode": "first_report", "taskDecisionCount": 0, "formalTaskDecisionCount": 0, "rule": "V19.15.3 first report only builds baseline."}

    action_parameter_enrichment_station_v1914(data_version)
    agent2_result = action_plan_judgment_agent_station_v1915(data_version)
    packages = _ensure_parameters(v197._load_packages(data_version))
    v197._delete_version_decisions(data_version)

    ledger = get_or_create_agent_budget_ledger(data_version=data_version, source="v19_15_3_matrix_direct_sop_mapping")
    decisions = [item for item in (_parameterized_decision(package, data_version) for package in packages) if item]
    for decision in decisions:
        base._save_decision(decision)

    by_family = Counter(str(((item.get("taskPlan") or {}).get("selectedActionFamily"))) for item in decisions)
    by_param = Counter(str(((((item.get("taskPlan") or {}).get("actionParameterPack") or {}).get("status")))) for item in decisions)
    agent2_missing = sum(1 for item in decisions if (item.get("taskPlan") or {}).get("taskType") == "data_evidence_task")
    creative_ready = sum(1 for item in decisions if (((item.get("taskPlan") or {}).get("creativeTestPlan") or {}).get("groups")))
    conversion_template_blocked = sum(1 for item in decisions if "模板拦截" in str(item.get("taskTitle") or ""))
    register_agent_event(
        ledger_id=ledger["ledgerId"],
        data_version=data_version,
        stage="task_mapping_agent_station",
        call_type="v19_15_3_matrix_direct_sop_assembly",
        requested_calls=0,
        actual_calls=0,
        fallback_used=False,
        rag_retrievals=0,
        actual_input_tokens=0,
        actual_output_tokens=0,
        reason="V19.15.3: task mapping is deterministic; route/action consistency is locked and generic conversion templates are blocked.",
        payload={"agent2": agent2_result, "expectedDecisionCount": len(packages), "actualDecisionCount": len(decisions), "bySelectedActionFamily": dict(by_family), "byParameterStatus": dict(by_param), "agent2MissingCount": agent2_missing, "creativeReadyDecisionCount": creative_ready, "conversionTemplateBlockedCount": conversion_template_blocked},
    )
    status = "completed" if len(decisions) == len(packages) and packages else "partial" if decisions else "failed" if packages else "no_formal_judgments"
    return {
        "version": TASK_MAPPING_FORCE_V1915_VERSION,
        "stationId": "task_mapping_agent_station",
        "dataVersion": data_version,
        "status": status,
        "candidatePackageCount": len(packages),
        "formalJudgmentPackageCount": len(packages),
        "taskDecisionCount": len(decisions),
        "formalTaskDecisionCount": len(decisions),
        "oneToOneGapCount": max(0, len(packages) - len(decisions)),
        "bySelectedActionFamily": dict(by_family),
        "byParameterStatus": dict(by_param),
        "agent2MissingCount": agent2_missing,
        "creativeReadyDecisionCount": creative_ready,
        "conversionTemplateBlockedCount": conversion_template_blocked,
        "agent2": agent2_result,
        "taskMappingApiCallCount": 0,
        "taskMappingProviderStatus": "deterministic_matrix_mapping",
        "taskMappingProvider": {"providerStatus": "deterministic_matrix_mapping", "actualCalls": 0, "expectedDecisionCount": len(packages), "mode": TASK_AGENT_MODE},
        "taskGenerationDecisionRef": f"task_generation_decision:{data_version or 'latest'}",
        "outputRef": f"task_generation_decision:{data_version or 'latest'}",
        "decisions": decisions[:50],
        "rule": "V19.15.3: one package becomes one decision; mapping preserves valid Agent2 creative plans, blocks conversion fallback templates and does not run an LLM.",
    }


# Backward-compatible name if imported through older station code.
task_mapping_agent_station_v199 = task_mapping_agent_station_v1915
