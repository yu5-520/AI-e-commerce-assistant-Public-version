"""V20.8.1 Task Detail Projection Core.

Task detail pages should render judgment, operator SOP and automatic recap from
V20 task payloads. V20.8.1 adds a strict guard against legacy engineering
fallback SOP lines such as "补齐后重新运行..." so old template/cache payloads
cannot be displayed as operator execution instructions.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from src.services.action_pack_core_v20_service import compose_parameterized_sop, select_action_parameter_pack
from src.services.task_lifecycle_orchestrator_service import lifecycle_snapshot
from src.services.task_recap_scheduler_service import list_recap_cycles_for_task, recap_policy_for_task

TASK_DETAIL_PROJECTION_CORE_VERSION = "20.8.1"
DEFAULT_RECAP_METRICS = ["点击率", "点击量", "转化率", "支付金额", "GMV", "广告消耗", "ROI/ROAS"]
TEMPLATE_MARKERS = [
    "核心场景词",
    "核心卖点",
    "设计2-3组新标题和主图变体",
    "在广告平台创建A/B测试",
    "监控测试数据",
    "评估测试结果并应用最优素材",
    "商品主体+核心场景+关键卖点",
]
LEGACY_FALLBACK_MARKERS = [
    "补齐后重新运行",
    "缺失数据或动作方案",
    "动作族数据补包站",
    "Agent2动作方案站",
    "任务映射站",
    "补齐【",
    "重新运行动作族",
    "后由系统重新运行",
    "系统生成异常",
    "action_plan_missing_data",
    "data_evidence_task",
]


def arr(value: Any) -> List[Any]:
    return [x for x in value if x] if isinstance(value, list) else []


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ["fullTitle", "title", "action", "summary", "text", "value", "reason", "name"]:
            if value.get(key):
                return str(value.get(key)).strip()
        return " ".join(str(v).strip() for v in value.values() if not isinstance(v, (dict, list)) and v not in [None, ""])[:240]
    return str(value).strip()


def dedupe(lines: List[str], limit: int = 14) -> List[str]:
    out: List[str] = []
    seen = set()
    for line in lines:
        item = " ".join(str(line or "").split()).strip(" ,;，；")
        if item and item not in seen:
            seen.add(item)
            out.append(item)
        if len(out) >= limit:
            break
    return out


def get_plan(task: Dict[str, Any], report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    report = report or {}
    for value in [report.get("taskPlan"), task.get("taskPlan"), task.get("taskCard")]:
        if isinstance(value, dict):
            return value
    return {}


def get_package(task: Dict[str, Any], report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    report = report or {}
    plan = get_plan(task, report)
    for value in [plan.get("productJudgmentPackage"), task.get("productJudgmentPackage"), report.get("productJudgmentPackage")]:
        if isinstance(value, dict):
            return value
    packs = plan.get("actionParameterPacks") if isinstance(plan.get("actionParameterPacks"), dict) else task.get("actionParameterPacks") if isinstance(task.get("actionParameterPacks"), dict) else {}
    pack = plan.get("actionParameterPack") if isinstance(plan.get("actionParameterPack"), dict) else task.get("actionParameterPack") if isinstance(task.get("actionParameterPack"), dict) else {}
    return {"productId": task.get("productId"), "storeId": task.get("storeId"), "productIdentity": task.get("productIdentity") or {}, "actionParameterPacks": packs, "actionParameterPack": pack}


def has_template(lines: Any) -> bool:
    text = str(lines)
    return any(marker in text for marker in TEMPLATE_MARKERS)


def has_legacy_fallback(lines: Any) -> bool:
    text = str(lines)
    return any(marker in text for marker in LEGACY_FALLBACK_MARKERS)


def clean_operator_lines(lines: Any) -> List[str]:
    clean = []
    for line in arr(lines):
        text = txt(line)
        if not text or has_template(text) or has_legacy_fallback(text):
            continue
        clean.append(text)
    return dedupe(clean)


def _structure_text(structure: Dict[str, Any]) -> str:
    pairs = [
        ("场景", structure.get("scene") or structure.get("background") or structure.get("usageScene") or structure.get("scenario")),
        ("商品呈现", structure.get("foreground") or structure.get("productPosition") or structure.get("productDisplay") or structure.get("mainSubject")),
        ("视觉重点", structure.get("focus") or structure.get("highlight") or structure.get("sellingPoint") or structure.get("visualFocus")),
        ("画面文案", structure.get("copy") or structure.get("textOverlay") or structure.get("imageText") or structure.get("mainText")),
        ("目标", structure.get("visualGoal") or structure.get("goal") or structure.get("purpose")),
    ]
    return "；".join(f"{k}：{v}" for k, v in pairs if v)


def creative_plan_lines(plan: Dict[str, Any], package: Dict[str, Any]) -> List[str]:
    creative = plan.get("creativeTestPlan") if isinstance(plan.get("creativeTestPlan"), dict) else package.get("creativeTestPlan") if isinstance(package.get("creativeTestPlan"), dict) else {}
    groups = creative.get("groups") if isinstance(creative.get("groups"), list) else []
    good = []
    for group in groups[:5]:
        if not isinstance(group, dict) or has_template(group) or has_legacy_fallback(group):
            continue
        structure = group.get("mainImageStructure") if isinstance(group.get("mainImageStructure"), dict) else {}
        if not group.get("fullTitle") or not structure:
            continue
        good.append((group, structure))
    if len(good) < 2:
        return []
    lines = [f"按Agent2生成的{len(good)}组完整标题主图方案执行测试。"]
    for index, (group, structure) in enumerate(good, 1):
        name = group.get("groupName") or f"{chr(64 + index)}组"
        lines.append(f"{name}标题：{group.get('fullTitle')}")
        structure_line = _structure_text(structure)
        if structure_line:
            lines.append(f"{name}主图结构：{structure_line}。")
        words = "、".join(str(x) for x in arr(group.get("testFocusWords"))[:6])
        if words:
            lines.append(f"{name}测试重点词：{words}。")
    return dedupe(lines, 14)


def metric_lines(plan: Dict[str, Any]) -> List[str]:
    metrics = arr(plan.get("reviewMetrics")) or arr(plan.get("testMetric")) or DEFAULT_RECAP_METRICS[:4]
    return ["系统复盘指标：" + "、".join(txt(x) for x in metrics[:6])]


def blocked_plan_message(family: str) -> List[str]:
    if family == "title_image_test":
        return ["当前标题主图任务未通过V20.8.1执行SOP门禁：Agent2需先生成2组以上完整标题和主图结构；旧补齐/重跑话术已被拦截，不能展示给运营执行。"]
    return ["当前任务未通过V20.8.1执行SOP门禁：后端返回的是系统补齐/重跑话术，不是运营可执行SOP；该任务应回到对应动作方案站重新生成。"]


def build_operator_execution_sop(task: Dict[str, Any], report: Dict[str, Any] | None = None) -> List[str]:
    report = report or {}
    plan = get_plan(task, report)
    package = get_package(task, report)
    family = str(plan.get("selectedActionFamily") or task.get("actionFamily") or package.get("selectedActionFamilyHint") or "")
    raw_existing = arr(plan.get("operatorExecutionSop")) or arr(task.get("operatorExecutionSop")) or arr(report.get("operatorExecutionSop")) or arr(plan.get("sopSteps"))
    existing = clean_operator_lines(raw_existing)
    if existing:
        return existing
    if family == "title_image_test":
        creative_lines = creative_plan_lines(plan, package)
        if creative_lines:
            return dedupe(creative_lines + metric_lines(plan), 14)
        if raw_existing and has_legacy_fallback(raw_existing):
            return blocked_plan_message(family)
        return ["创意测试方案缺失：需先由Agent2生成2-5组完整标题和主图结构，禁止使用模板占位词生成标题主图任务。"]
    if raw_existing and has_legacy_fallback(raw_existing):
        return blocked_plan_message(family)
    pack = plan.get("actionParameterPack") if isinstance(plan.get("actionParameterPack"), dict) else select_action_parameter_pack(package, family)
    if pack:
        sop = clean_operator_lines(compose_parameterized_sop(family or str(pack.get("actionFamily") or ""), pack, plan, package))
        if sop:
            return sop
    view = plan.get("operatorJudgmentView") if isinstance(plan.get("operatorJudgmentView"), dict) else {}
    direction = view.get("selectedDirection") or plan.get("businessHypothesis") or task.get("title") or "当前运营动作"
    deadline = plan.get("executionDeadline") or task.get("deadline") or "6小时内"
    return dedupe([f"{deadline}执行【{direction}】对应运营动作，并在提交页保留执行痕迹。", *metric_lines(plan), "后续数据由系统自动复盘，运营不填写人工复盘结论。"])


def writeback_targets(target: str | None, cycle_day: int | None = None) -> List[str]:
    text = str(target or "")
    targets = ["日报", "周报", "月报"]
    if "周" in text:
        targets = ["周报", "月报"]
    if "月" in text:
        targets = ["月报"]
    if cycle_day is not None and cycle_day <= 1 and "日报" not in targets:
        targets.insert(0, "日报")
    targets.append("RAG优化候选")
    return list(dict.fromkeys(targets))


def build_auto_recap_plan(task: Dict[str, Any], report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    report = report or {}
    task_id = task.get("id") or task.get("taskId") or report.get("taskId")
    lifecycle = task.get("taskLifecycle") if isinstance(task.get("taskLifecycle"), dict) else {}
    cycles = arr(lifecycle.get("recapCycles")) or (list_recap_cycles_for_task(str(task_id)) if task_id else [])
    policy = recap_policy_for_task(task)
    if not cycles:
        base = datetime.now()
        metrics = arr(get_plan(task, report).get("reviewMetrics")) or DEFAULT_RECAP_METRICS
        cycles = [{"id": f"virtual_recap_{day}", "status": "planned_after_submission", "cycleName": policy.get("name"), "cycleDay": day, "recapTarget": policy.get("target"), "scheduledAt": (base + timedelta(days=int(day))).date().isoformat(), "requiredMetrics": metrics} for day in policy.get("cycles", [3])]
    normalized = []
    for item in cycles:
        day = int(item.get("cycleDay") or 3)
        metrics = arr(item.get("requiredMetrics")) or arr(get_plan(task, report).get("reviewMetrics")) or DEFAULT_RECAP_METRICS
        target = item.get("recapTarget") or policy.get("target") or "日报"
        normalized.append({"cycleId": item.get("id"), "cycleName": item.get("cycleName") or policy.get("name") or "系统自动复盘", "cycleDay": day, "scheduledAt": item.get("scheduledAt"), "status": item.get("status") or "planned_after_submission", "requiredMetrics": metrics[:8], "writebackTargets": writeback_targets(target, day), "recapTarget": target})
    lines = []
    for item in normalized[:3]:
        lines.append(f"执行后第{item['cycleDay']}天系统自动复盘，读取：{'、'.join(str(x) for x in item['requiredMetrics'][:6])}。")
        lines.append("复盘结果自动写入：" + "、".join(item["writebackTargets"]))
    return {"version": TASK_DETAIL_PROJECTION_CORE_VERSION, "source": "v20_task_lifecycle_recap_scheduler", "manualRecapRequired": False, "lifecycleStage": lifecycle.get("stage") or task.get("lifecycleStage") or "generated", "nextExpected": lifecycle.get("nextExpected") or "提交执行痕迹后系统等待后续报表自动复盘。", "cycles": normalized, "displayLines": dedupe(lines, 6)}


def project_task_detail(task: Dict[str, Any], report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    report = dict(report or {})
    try:
        lifecycle = lifecycle_snapshot(task) if task.get("id") or task.get("taskId") else task.get("taskLifecycle") or {}
    except Exception:
        lifecycle = task.get("taskLifecycle") or {}
    auto_recap = build_auto_recap_plan({**task, "taskLifecycle": lifecycle}, report)
    return {
        "version": TASK_DETAIL_PROJECTION_CORE_VERSION,
        "operatorExecutionSop": build_operator_execution_sop(task, report),
        "autoReviewPlan": auto_recap,
        "autoRecapPlan": auto_recap,
        "taskLifecycle": lifecycle,
        "hiddenBackendThinkingFields": ["titleVariants", "mainImageStructures", "testVariables", "successCriteria", "failureCriteria", "submissionConclusionOptions", "evidenceRequirements", "actionParameterPack", "actionParameterPacks"],
        "detailDisplayContract": "task_detail_only_shows_judgment_sop_auto_recap_status",
        "blockedLegacyFallbackMarkers": LEGACY_FALLBACK_MARKERS,
        "rule": "V20.8.1: task detail projection blocks legacy补齐/重跑 fallback SOP and only displays executable operator SOP or a generation-gate message.",
    }
