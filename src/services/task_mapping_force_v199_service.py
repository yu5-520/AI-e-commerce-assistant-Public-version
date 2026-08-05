"""V19.13 task mapping station.

Task mapping no longer judges, selects actions, creates title/main-image plans, or
calculates budgets. It consumes Agent1 operating judgment, action-family data
pack and Agent2 action plan, then assembles a single operator SOP.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Tuple

import src.services.dual_agent_product_task_service as base
import src.services.task_mapping_force_v197_service as v197
from src.services.action_parameter_enrichment_v199_service import (
    ACTION_PARAMETER_ENRICHMENT_VERSION,
    HIGH_RISK_ACTIONS,
    action_parameter_enrichment_station_v199,
    compose_parameterized_sop,
    enrich_package_with_action_parameters,
    select_action_parameter_pack,
)
from src.services.action_plan_judgment_agent_v1913_service import action_plan_judgment_agent_station_v1913
from src.services.agent_budget_ledger_service import get_or_create_agent_budget_ledger, register_agent_event
from src.services.metric_trigger_expansion_v171_service import is_first_report_baseline

TASK_MAPPING_FORCE_V199_VERSION = "19.13"
TASK_AGENT_MODE = "v1913_mapping_assembles_agent2_action_plan"
APPEND_ONLY_SOP_SOURCE = "v19_13_agent1_agent2_action_plan_mapped_sop"
TEMPLATE_MARKERS = ["核心场景词", "核心卖点", "设计2-3组新标题和主图变体", "在广告平台创建A/B测试", "监控测试数据", "评估测试结果并应用最优素材", "商品主体+核心场景+关键卖点", "围绕核心场景词重写标题", "突出主卖点与场景", "标题方向一突出"]


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _clean_lines(values: List[Any], limit: int = 16) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip(" ，,;；")
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _money(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return "待补齐"


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "待补齐"


def _product_identity(package: Dict[str, Any]) -> Dict[str, Any]:
    return package.get("productIdentity") if isinstance(package.get("productIdentity"), dict) else {"productId": package.get("productId"), "storeId": package.get("storeId")}


def _agent1(package: Dict[str, Any]) -> Dict[str, Any]:
    return package.get("agent1OperatingJudgment") if isinstance(package.get("agent1OperatingJudgment"), dict) else {}


def _agent2(package: Dict[str, Any]) -> Dict[str, Any]:
    return package.get("agent2ActionPlan") if isinstance(package.get("agent2ActionPlan"), dict) else {}


def _locked_family(package: Dict[str, Any], plan: Dict[str, Any] | None = None) -> str:
    plan = plan or {}
    agent1 = _agent1(package)
    lock = agent1.get("actionFamilyLock") if isinstance(agent1.get("actionFamilyLock"), dict) else {}
    return str(lock.get("selectedActionFamily") or agent1.get("selectedActionFamily") or package.get("selectedActionFamilyHint") or plan.get("selectedActionFamily") or "").strip()


def _ensure_parameters(packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for item in packages:
        packs = item.get("actionParameterPacks") if isinstance(item.get("actionParameterPacks"), dict) else {}
        enriched.append(item if packs else enrich_package_with_action_parameters(item))
    return enriched


def _has_template_marker(value: Any) -> bool:
    return any(marker in str(value) for marker in TEMPLATE_MARKERS)


def _creative_plan(package: Dict[str, Any], plan: Dict[str, Any] | None = None) -> Dict[str, Any]:
    plan = plan or {}
    agent2 = _agent2(package)
    for value in [agent2.get("creativeTestPlan"), plan.get("creativeTestPlan"), package.get("creativeTestPlan"), package.get("agentCreativePack")]:
        if isinstance(value, dict) and isinstance(value.get("groups"), list) and value.get("groups"):
            return value
    return {}


def _valid_groups(creative: Dict[str, Any]) -> List[Dict[str, Any]]:
    groups = creative.get("groups") if isinstance(creative.get("groups"), list) else []
    out: List[Dict[str, Any]] = []
    for group in groups[:5]:
        if not isinstance(group, dict):
            continue
        if _has_template_marker(group):
            continue
        if not str(group.get("fullTitle") or "").strip():
            continue
        if not isinstance(group.get("mainImageStructure"), dict):
            continue
        out.append(group)
    return out


def _creative_sop_from_plan(package: Dict[str, Any], plan: Dict[str, Any] | None = None) -> List[str]:
    creative = _creative_plan(package, plan)
    groups = _valid_groups(creative)
    if len(groups) < 2:
        return []
    product = _product_identity(package)
    name = product.get("productTitle") or product.get("title") or product.get("shortTitle") or package.get("productId") or "该商品"
    lines = [f"围绕【{name}】执行Agent2生成的{len(groups)}组标题主图测试。"]
    for index, group in enumerate(groups, 1):
        group_name = group.get("groupName") or f"{chr(64 + index)}组"
        structure = group.get("mainImageStructure") if isinstance(group.get("mainImageStructure"), dict) else {}
        words = "、".join(str(x) for x in _arr(group.get("testFocusWords"))[:6])
        lines.append(f"{group_name}标题：{group.get('fullTitle')}")
        lines.append(f"{group_name}主图结构：{structure.get('scene') or ''}；{structure.get('foreground') or ''}；重点突出{structure.get('focus') or ''}；画面文案“{structure.get('copy') or ''}”；目标：{structure.get('visualGoal') or ''}。")
        if words:
            lines.append(f"{group_name}测试重点词：{words}。")
    lines.append("所有组保持预算、入口、人群和时间窗口一致，只测试标题词与主图表达差异。")
    return _clean_lines(lines, 16)


def _agent2_steps(package: Dict[str, Any], family: str) -> List[str]:
    agent2 = _agent2(package)
    steps = _arr(agent2.get("operatorActionSteps"))
    if steps and not _has_template_marker(steps):
        return _clean_lines(steps, 12)
    if family == "title_image_test":
        return _creative_sop_from_plan(package)
    if isinstance(agent2.get("budgetPlan"), dict):
        plan = agent2["budgetPlan"]
        return _clean_lines([
            f"按Agent2预算方案执行：当前消耗{_money(plan.get('currentAdSpend'))}，建议调整比例{plan.get('recommendedIncreaseRate') or plan.get('recommendedBudgetIncreaseRate') or '待补齐'}，预算上限{_money(plan.get('recommendedUpperBound') or plan.get('recommendedBudgetUpperBound'))}。",
            f"执行窗口：{plan.get('executionWindow') or '24小时'}；止损条件：{plan.get('stopLossCondition') or 'ROI/ROAS低于安全线或支付金额未同步增长时停止'}。",
        ], 4)
    if isinstance(agent2.get("activityPlan"), dict):
        plan = agent2["activityPlan"]
        return _clean_lines([
            f"按Agent2活动方案执行：优惠金额{_money(plan.get('couponAmount') or plan.get('recommendedCouponAmount'))}，周期{plan.get('activityDays') or plan.get('recommendedActivityDays') or '7'}天，目标人群：{plan.get('targetAudience') or '新访客和加购未成交人群'}。",
            f"毛利保护边界：{plan.get('marginProtectionRule') or plan.get('stopLossCondition') or '券后毛利明显压缩或退款率上升时停止'}。",
        ], 4)
    if isinstance(agent2.get("conversionRepairPlan"), dict):
        plan = agent2["conversionRepairPlan"]
        return _clean_lines(_arr(plan.get("steps")) or ["按Agent2转化修复方案检查详情页、价格权益、评价信任和客服承诺。"], 8)
    return []


def _parameter_hint_lines(family: str, pack: Dict[str, Any]) -> List[str]:
    if not isinstance(pack, dict) or not pack:
        return []
    if family in {"roas_scale", "roas_guard"} and pack.get("status") == "valid":
        return _clean_lines([f"参数补充：当前广告消耗{_money(pack.get('currentAdSpend'))}，ROI/ROAS{_money(pack.get('currentROI'))}，毛利率{_pct(pack.get('grossMarginRate'))}，库存{_money(pack.get('inventory'))}，可售天数{_money(pack.get('availableDays'))}。"], 2)
    if family == "platform_activity" and pack.get("status") == "valid":
        return _clean_lines([f"参数补充：售价{_money(pack.get('currentPrice'))}，成本{_money(pack.get('productCost'))}，单件毛利{_money(pack.get('grossProfitAmount'))}，建议优惠{_money(pack.get('recommendedCouponAmount'))}元，周期{int(pack.get('recommendedActivityDays') or 7)}天。"], 2)
    if family == "title_image_test":
        return _clean_lines([f"测试参数补充：周期{int(pack.get('testDurationDays') or 3)}天；系统复盘指标为{'、'.join(str(x) for x in _arr(pack.get('reviewMetrics'))[:6]) or '点击率、点击量、转化率、支付金额'}。"], 2)
    return []


def _sop(family: str, pack: Dict[str, Any], plan: Dict[str, Any], package: Dict[str, Any]) -> List[str]:
    steps = _agent2_steps(package, family)
    if not steps:
        if family == "title_image_test":
            return ["动作方案缺失：Agent2必须先生成2-5组完整标题和主图结构。", "禁止使用模板占位词生成标题主图任务。"]
        steps = _clean_lines(compose_parameterized_sop(family, pack, plan, package), 8)
    return _clean_lines(steps + _parameter_hint_lines(family, pack), 16)


def _data_evidence_decision(package: Dict[str, Any], family: str, pack: Dict[str, Any], data_version: str | None, reason: str | None = None) -> Dict[str, Any]:
    product = _product_identity(package)
    title = f"数据补全｜{product.get('title') or product.get('productId') or package.get('productId')}｜{family}方案不足"
    sop = [reason or f"{family} 缺少Agent2动作方案或关键参数，不能生成正式SOP。", "补齐后重新运行动作族数据补包站、Agent2动作方案站和任务映射站。"]
    plan = {"title": title, "taskTitle": title, "productId": product.get("productId") or package.get("productId"), "storeId": product.get("storeId") or package.get("storeId"), "productIdentity": product, "selectedActionFamily": family, "taskType": "data_evidence_task", "taskResponsibility": "operator_growth", "departmentTaskType": "operator_growth", "operatorExecutionSop": sop, "sopSteps": sop, "actionParameterPack": pack, "actionParameterPacks": package.get("actionParameterPacks") or {}, "agent1OperatingJudgment": _agent1(package), "agent2ActionPlan": _agent2(package), "priority": "中", "executionDeadline": "6小时内", "followUpDeadline": "补齐后重新生成", "reviewCycle": "数据补齐后", "assigneeRole": "operator", "approvalRequired": False, "reason": reason or f"{family} 缺少Agent2动作方案或关键参数，不能生成正式SOP。", "reviewMetrics": pack.get("reviewMetrics") or [], "evidenceRequirements": ["补齐缺失字段或Agent2动作方案", "上传或同步对应后台字段截图"]}
    return {"version": TASK_MAPPING_FORCE_V199_VERSION, "decisionId": base.make_id("TGD"), "packageId": package.get("packageId"), "dataVersion": data_version or package.get("dataVersion"), "storeId": product.get("storeId") or package.get("storeId"), "productId": product.get("productId") or package.get("productId"), "decision": "create_task_snapshot", "taskTitle": title, "priority": "中", "reason": plan["reason"], "taskPlan": plan, "productJudgmentPackage": package, "taskMappingAgentEvidence": {"source": "agent2_action_plan_mapping", "mappingMode": TASK_AGENT_MODE, "parameterStatus": pack.get("status"), "appendOnly": True}, "rule": "V19.13: missing Agent2 action plan becomes data completion task, not generic SOP."}


def _parameterize_decision(decision: Dict[str, Any], package_by_id: Dict[str, Dict[str, Any]], data_version: str | None) -> Dict[str, Any] | None:
    package = package_by_id.get(str(decision.get("packageId") or "")) or decision.get("productJudgmentPackage") or {}
    plan = decision.get("taskPlan") if isinstance(decision.get("taskPlan"), dict) else {}
    family = _locked_family(package, plan)
    pack = select_action_parameter_pack(package, family)
    agent2 = _agent2(package)
    if agent2.get("actionPlanStatus") in {"action_plan_missing_data", "conflict_requires_rejudgment"}:
        return _data_evidence_decision(package, family, pack, data_version, agent2.get("reason") or agent2.get("conflictReason") or "Agent2动作方案未就绪。")
    if family in HIGH_RISK_ACTIONS and pack.get("status") != "valid":
        return _data_evidence_decision(package, family, pack, data_version)
    if family == "title_image_test" and len(_valid_groups(_creative_plan(package, plan))) < 2:
        return _data_evidence_decision(package, family, pack, data_version, "title_image_test 缺少Agent2 creativeTestPlan，禁止模板兜底。")
    sop = _sop(family, pack, plan, package)
    plan = dict(plan)
    plan.update({"selectedActionFamily": family, "agent1OperatingJudgment": _agent1(package), "agent2ActionPlan": agent2, "actionParameterPack": pack, "actionParameterPacks": package.get("actionParameterPacks") or {}, "operatorExecutionSop": sop, "sopSteps": sop, "sopSource": APPEND_ONLY_SOP_SOURCE, "reviewMetrics": agent2.get("reviewMetrics") or pack.get("reviewMetrics") or plan.get("reviewMetrics") or [], "reason": plan.get("reason") or agent2.get("reason") or "Agent1定方向，Agent2生成动作方案，任务映射组装SOP。", "appendOnlyActionParameterPack": True, "creativeTestPlan": _creative_plan(package, plan) or plan.get("creativeTestPlan")})
    decision = dict(decision)
    decision["version"] = TASK_MAPPING_FORCE_V199_VERSION
    decision["taskPlan"] = plan
    decision["taskTitle"] = plan.get("title") or plan.get("taskTitle") or decision.get("taskTitle")
    decision["productJudgmentPackage"] = package
    evidence = decision.get("taskMappingAgentEvidence") if isinstance(decision.get("taskMappingAgentEvidence"), dict) else {}
    evidence.update({"mappingMode": TASK_AGENT_MODE, "actionParameterEnrichmentVersion": ACTION_PARAMETER_ENRICHMENT_VERSION, "agent2ActionPlanVersion": agent2.get("version"), "appendOnly": True, "sourceAware": True, "creativePlanGroupCount": len(_valid_groups(_creative_plan(package, plan))), "actionFamily": family, "parameterStatus": pack.get("status")})
    decision["taskMappingAgentEvidence"] = evidence
    decision["rule"] = "V19.13: task mapping assembles Agent2 action plan; it does not judge or create action content."
    return decision


def _call_mapping(packages: List[Dict[str, Any]], data_version: str | None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    enriched = _ensure_parameters(packages)
    base._save_packages(enriched)
    raw_decisions, provider = v197._call_mapping(enriched, data_version)
    package_by_id = {str(item.get("packageId")): item for item in enriched if item.get("packageId")}
    decisions = []
    for decision in raw_decisions:
        item = _parameterize_decision(decision, package_by_id, data_version)
        if item:
            decisions.append(item)
    provider = dict(provider)
    provider.update({"mode": TASK_AGENT_MODE, "appendOnlyDecisionCount": len(decisions), "expectedDecisionCount": len(enriched)})
    return decisions, provider


def task_mapping_agent_station_v199(data_version: str | None, **_: Any) -> Dict[str, Any]:
    baseline = is_first_report_baseline(data_version)
    if baseline.get("isFirstReportBaseline"):
        v197._delete_version_decisions(data_version)
        return {"version": TASK_MAPPING_FORCE_V199_VERSION, "stationId": "task_mapping_agent_station", "dataVersion": data_version, "baselineMode": "first_report", "taskDecisionCount": 0, "formalTaskDecisionCount": 0, "rule": "V19.13 first report only builds baseline."}
    action_parameter_enrichment_station_v199(data_version)
    agent2_result = action_plan_judgment_agent_station_v1913(data_version)
    packages = _ensure_parameters(v197._load_packages(data_version))
    v197._delete_version_decisions(data_version)
    ledger = get_or_create_agent_budget_ledger(data_version=data_version, source="v19_13_mapping_assembles_agent2_action_plan")
    try:
        decisions, provider = _call_mapping(packages, data_version) if packages else ([], {"providerStatus": "no_formal_judgment_packages", "actualCalls": 0, "expectedDecisionCount": 0})
    except Exception as exc:
        decisions, provider = [], {"providerStatus": "failed", "actualCalls": 0, "errors": [str(exc)[:500]], "expectedDecisionCount": len(packages)}
    for decision in decisions:
        base._save_decision(decision)
    by_family = Counter(str((item.get("taskPlan") or {}).get("selectedActionFamily")) for item in decisions)
    by_param = Counter(str((((item.get("taskPlan") or {}).get("actionParameterPack") or {}).get("status"))) for item in decisions)
    agent2_missing = sum(1 for item in decisions if (item.get("taskPlan") or {}).get("taskType") == "data_evidence_task")
    register_agent_event(ledger_id=ledger["ledgerId"], data_version=data_version, stage="task_mapping_agent_station", call_type="v19_13_mapping_assembles_agent2_action_plan", requested_calls=1 if packages else 0, actual_calls=int(provider.get("actualCalls") or 0), fallback_used=False, rag_retrievals=0, actual_input_tokens=int(provider.get("inputTokens") or 0), actual_output_tokens=int(provider.get("outputTokens") or 0), reason="V19.13: task mapping assembles Agent2 action plan into SOP.", payload={"provider": provider, "agent2": agent2_result, "expectedDecisionCount": len(packages), "actualDecisionCount": len(decisions), "bySelectedActionFamily": dict(by_family), "byParameterStatus": dict(by_param), "agent2MissingCount": agent2_missing})
    status = "completed" if len(decisions) == len(packages) and packages else "partial" if decisions else "failed" if packages else "no_formal_judgments"
    return {"version": TASK_MAPPING_FORCE_V199_VERSION, "stationId": "task_mapping_agent_station", "dataVersion": data_version, "status": status, "candidatePackageCount": len(packages), "formalJudgmentPackageCount": len(packages), "taskDecisionCount": len(decisions), "formalTaskDecisionCount": len(decisions), "oneToOneGapCount": max(0, len(packages) - len(decisions)), "bySelectedActionFamily": dict(by_family), "byParameterStatus": dict(by_param), "agent2MissingCount": agent2_missing, "agent2": agent2_result, "taskMappingApiCallCount": int(provider.get("actualCalls") or 0), "taskMappingProviderStatus": provider.get("providerStatus"), "taskMappingProvider": provider, "taskGenerationDecisionRef": f"task_generation_decision:{data_version or 'latest'}", "outputRef": f"task_generation_decision:{data_version or 'latest'}", "decisions": decisions[:50], "rule": "V19.13: Agent1判断栏可展示，Agent2动作方案不单独展示，只被整合进SOP。"}
