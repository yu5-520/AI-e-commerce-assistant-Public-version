"""V19.8 task detail projection.

The task detail page must not expose backend thinking blocks such as
``titleVariants`` or ``mainImageStructures`` as standalone UI modules. Those
materials are merged into an operator-facing SOP. Automatic recap is projected
from the existing lifecycle/recap scheduler instead of creating a new manual
review system.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from src.services.task_lifecycle_orchestrator_service import lifecycle_snapshot
from src.services.task_recap_scheduler_service import list_recap_cycles_for_task, recap_policy_for_task

TASK_DETAIL_PROJECTION_VERSION = "19.8"
DEFAULT_RECAP_METRICS = ["点击率", "点击量", "转化率", "支付金额", "GMV", "广告消耗", "ROI/ROAS"]


def _arr(value: Any) -> List[Any]:
    return [item for item in value if item] if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ["title", "action", "summary", "text", "value", "reason"]:
            if value.get(key):
                return str(value.get(key)).strip()
        return " ".join(str(v).strip() for v in value.values() if v not in {None, ""})[:240]
    return str(value).strip()


def _dedupe(lines: List[str], limit: int = 8) -> List[str]:
    result: List[str] = []
    seen = set()
    for line in lines:
        clean = " ".join(str(line or "").split()).strip(" ，,;；")
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _plan(task: Dict[str, Any], report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    report = report or {}
    if isinstance(report.get("taskPlan"), dict):
        return report["taskPlan"]
    if isinstance(task.get("taskPlan"), dict):
        return task["taskPlan"]
    if isinstance(task.get("taskCard"), dict):
        return task["taskCard"]
    return {}


def _operator_view(task: Dict[str, Any], report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    plan = _plan(task, report)
    if isinstance(plan.get("operatorJudgmentView"), dict):
        return plan["operatorJudgmentView"]
    if isinstance(task.get("operatorJudgmentView"), dict):
        return task["operatorJudgmentView"]
    return {}


def _title_lines(plan: Dict[str, Any]) -> List[str]:
    variants = _arr(plan.get("titleVariants"))
    lines = []
    for index, item in enumerate(variants[:3], 1):
        if isinstance(item, dict):
            title = item.get("title") or item.get("text") or item.get("headline")
            angle = item.get("style") or item.get("angle") or f"方案{index}"
            if title:
                lines.append(f"标题{index}（{angle}）：{title}")
        else:
            lines.append(f"标题{index}：{item}")
    return lines


def _image_lines(plan: Dict[str, Any]) -> List[str]:
    structures = _arr(plan.get("mainImageStructures"))
    lines = []
    for index, item in enumerate(structures[:3], 1):
        if isinstance(item, dict):
            scene = item.get("visualScene") or item.get("structure") or item.get("composition") or "主图结构"
            headline = item.get("headlineText") or item.get("mainTitle") or item.get("title")
            pieces = [str(scene)]
            if headline:
                pieces.append(f"主标题：{headline}")
            selling = _arr(item.get("sellingPoints"))[:3]
            if selling:
                pieces.append("卖点：" + " / ".join(str(x) for x in selling))
            lines.append(f"主图{index}：" + "；".join(pieces))
        else:
            lines.append(f"主图{index}：{item}")
    return lines


def _metric_lines(plan: Dict[str, Any]) -> List[str]:
    metrics = _arr(plan.get("reviewMetrics")) or _arr(plan.get("testMetric")) or DEFAULT_RECAP_METRICS[:4]
    success = _arr(plan.get("successCriteria"))
    failure = _arr(plan.get("failureCriteria"))
    lines = ["监控指标：" + "、".join(_text(item) for item in metrics[:6])]
    if success:
        lines.append("有效判断：" + "；".join(_text(item) for item in success[:2]))
    if failure:
        lines.append("无效判断：" + "；".join(_text(item) for item in failure[:2]))
    return lines


def build_operator_execution_sop(task: Dict[str, Any], report: Dict[str, Any] | None = None) -> List[str]:
    report = report or {}
    plan = _plan(task, report)
    existing = _arr(plan.get("operatorExecutionSop")) or _arr(task.get("operatorExecutionSop")) or _arr(report.get("operatorExecutionSop"))
    if existing:
        return _dedupe([_text(item) for item in existing], limit=10)

    family = str(plan.get("selectedActionFamily") or task.get("actionFamily") or "")
    view = _operator_view(task, report)
    direction = view.get("selectedDirection") or plan.get("businessHypothesis") or task.get("title") or "当前经营动作"
    deadline = plan.get("executionDeadline") or task.get("deadline") or "6小时内"
    title_lines = _title_lines(plan)
    image_lines = _image_lines(plan)
    raw_steps = [_text(item) for item in _arr(plan.get("sopSteps")) or _arr(task.get("sopSteps"))]

    lines: List[str] = []
    if family == "title_image_test" or title_lines or image_lines:
        lines.append(f"{deadline}围绕【{direction}】整理3套标题+主图组合，不把标题方案和主图结构拆成单独任务。")
        if title_lines:
            lines.append("标题组合：" + "；".join(title_lines))
        if image_lines:
            lines.append("主图组合：" + "；".join(image_lines))
        lines.append("将标题与主图一一绑定成A/B/C测试组，保持预算、入口和投放时段一致，避免变量混杂。")
        lines.extend(_metric_lines(plan))
        lines.append("测试结束后只提交执行痕迹；指标结果由系统在后续报表中自动复盘。")
    elif family == "platform_activity" or _arr(plan.get("activityPlan")):
        lines.append(f"{deadline}筛选1-3个与商品类目、价格带、库存承接匹配的平台活动。")
        lines.extend(_text(item) for item in _arr(plan.get("activityPlan"))[:4])
        checklist = _arr(plan.get("activityEligibilityChecklist")) + _arr(plan.get("activityMaterialChecklist"))
        if checklist:
            lines.append("活动提交前核对：" + "；".join(_text(item) for item in checklist[:6]))
        lines.extend(_metric_lines(plan))
    elif family in {"roas_scale", "roas_guard"} or _arr(plan.get("budgetAdjustmentPlan")) or _arr(plan.get("cutBudgetPlan")):
        action = "小幅放量" if family == "roas_scale" else "收缩低效计划"
        lines.append(f"{deadline}只对符合判断包证据的广告计划执行{action}，不得误伤自然增长入口。")
        lines.extend(_text(item) for item in (_arr(plan.get("budgetAdjustmentPlan")) + _arr(plan.get("cutBudgetPlan")) + _arr(plan.get("campaignSelectionRule")) + _arr(plan.get("preserveTrafficRule")))[:6])
        lines.extend(_metric_lines(plan))
    elif family == "conversion_repair" or _arr(plan.get("conversionBlockers")):
        lines.append(f"{deadline}按转化阻塞点检查详情页、价格权益、评价承诺和客服话术。")
        lines.extend(_text(item) for item in (_arr(plan.get("conversionBlockers")) + _arr(plan.get("detailPageChecklist")) + _arr(plan.get("priceOrCouponPlan")))[:6])
        lines.extend(_metric_lines(plan))
    else:
        lines.extend(raw_steps)
        if not lines:
            lines.append(f"{deadline}执行【{direction}】对应的运营动作，并保留执行痕迹到提交页。")
            lines.extend(_metric_lines(plan))
            lines.append("后续数据由系统自动复盘，运营不需要人工填写复盘结论。")
    return _dedupe(lines, limit=10)


def _writeback_targets(recap_target: str | None, cycle_day: int | None = None) -> List[str]:
    targets = ["daily_report", "weekly_report", "monthly_report"]
    text = str(recap_target or "")
    if "周" in text:
        targets = ["weekly_report", "monthly_report"]
    if "月" in text:
        targets = ["monthly_report"]
    if cycle_day is not None and cycle_day <= 1 and "daily_report" not in targets:
        targets.insert(0, "daily_report")
    targets.append("rag_feedback_candidate")
    return list(dict.fromkeys(targets))


def build_auto_recap_plan(task: Dict[str, Any], report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    report = report or {}
    task_id = task.get("id") or task.get("taskId") or report.get("taskId")
    lifecycle = task.get("taskLifecycle") if isinstance(task.get("taskLifecycle"), dict) else {}
    cycles = _arr(lifecycle.get("recapCycles")) or (list_recap_cycles_for_task(str(task_id)) if task_id else [])
    policy = recap_policy_for_task(task)
    if not cycles:
        base = datetime.now()
        metrics = _arr(_plan(task, report).get("reviewMetrics")) or DEFAULT_RECAP_METRICS
        cycles = [
            {
                "id": f"virtual_recap_{day}",
                "status": "planned_after_submission",
                "cycleKind": policy.get("kind"),
                "cycleName": policy.get("name"),
                "cycleDay": day,
                "recapTarget": policy.get("target"),
                "scheduledAt": (base + timedelta(days=int(day))).date().isoformat(),
                "requiredMetrics": metrics,
                "rule": "任务提交或复核通过后由旧生命周期系统正式生成自动复盘周期。",
            }
            for day in policy.get("cycles", [3])
        ]
    normalized = []
    for item in cycles:
        day = int(item.get("cycleDay") or 3)
        metrics = _arr(item.get("requiredMetrics")) or _arr(_plan(task, report).get("reviewMetrics")) or DEFAULT_RECAP_METRICS
        target = item.get("recapTarget") or policy.get("target") or "日报"
        normalized.append({
            "cycleId": item.get("id"),
            "cycleName": item.get("cycleName") or policy.get("name") or "经营动作复盘",
            "cycleDay": day,
            "scheduledAt": item.get("scheduledAt"),
            "status": item.get("status") or "planned_after_submission",
            "requiredMetrics": metrics[:8],
            "writebackTargets": _writeback_targets(target, day),
            "recapTarget": target,
        })
    lines = []
    for item in normalized[:3]:
        lines.append(f"执行后第{item['cycleDay']}天系统自动复盘，读取：{'、'.join(str(x) for x in item['requiredMetrics'][:6])}。")
        lines.append("复盘结果自动写入：" + "、".join(item["writebackTargets"]))
    return {
        "version": TASK_DETAIL_PROJECTION_VERSION,
        "source": "legacy_task_lifecycle_recap_scheduler",
        "manualRecapRequired": False,
        "lifecycleStage": (lifecycle.get("stage") or task.get("lifecycleStage") or "generated"),
        "nextExpected": lifecycle.get("nextExpected") or "运营提交执行痕迹后，系统等待后续报表自动复盘。",
        "cycles": normalized,
        "displayLines": _dedupe(lines, limit=6),
        "rule": "复盘来自旧生命周期recap scheduler；任务详情页只展示自动复盘计划，不展示人工复盘标准。",
    }


def project_task_detail(task: Dict[str, Any], report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    report = dict(report or {})
    try:
        lifecycle = lifecycle_snapshot(task) if task.get("id") or task.get("taskId") else task.get("taskLifecycle") or {}
    except Exception:
        lifecycle = task.get("taskLifecycle") or {}
    operator_sop = build_operator_execution_sop(task, report)
    auto_recap = build_auto_recap_plan({**task, "taskLifecycle": lifecycle}, report)
    return {
        "version": TASK_DETAIL_PROJECTION_VERSION,
        "operatorExecutionSop": operator_sop,
        "autoReviewPlan": auto_recap,
        "autoRecapPlan": auto_recap,
        "taskLifecycle": lifecycle,
        "hiddenBackendThinkingFields": ["titleVariants", "mainImageStructures", "testVariables", "successCriteria", "failureCriteria", "submissionConclusionOptions", "evidenceRequirements"],
        "detailDisplayContract": "task_detail_only_shows_judgment_sop_auto_recap_status",
    }
