"""V12.8 automatic task recap scheduler.

A recap is not a manual copy-paste note. After a task is reviewed, the system
creates one or more recap cycles. Later report facts can close those cycles and
turn the result into a RAG candidate.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Dict, List
from uuid import uuid4

from src.services.task_recap_service import add_recap_candidate

RECAP_SCHEDULER_VERSION = "12.8.0"
RECAP_CYCLES: List[Dict[str, Any]] = []


def now_iso() -> str:
    return datetime.now().isoformat()


def make_id(prefix: str = "RECAPCYCLE") -> str:
    return f"{prefix}_{uuid4().hex[:10]}".upper()


def _text(task: Dict[str, Any]) -> str:
    gate = task.get("actionAuthorization") or task.get("v127ActionGate") or {}
    detail = task.get("taskDetailReport") or {}
    return " ".join(str(value or "") for value in [
        task.get("title"), task.get("task"), task.get("riskDomain"), task.get("reason"),
        gate.get("actionType"), gate.get("actionLabel"), detail.get("warningSummary"),
    ])


def recap_policy_for_task(task: Dict[str, Any]) -> Dict[str, Any]:
    text = _text(task)
    if any(token in text for token in ("活动", "报名", "平台补贴", "活动价")):
        return {"kind": "activity", "cycles": [3, 7], "target": "周报", "name": "活动效果复盘"}
    if any(token in text for token in ("标题", "主图", "素材", "点击", "创意")):
        return {"kind": "creative_test", "cycles": [3], "target": "周报", "name": "素材/标题/主图测试复盘"}
    if any(token in text for token in ("库存", "补货", "调拨", "可售天数", "缺货", "断货")):
        return {"kind": "inventory", "cycles": [3, 7], "target": "日报", "name": "库存承接复盘"}
    if any(token in text for token in ("广告", "投放", "预算", "ROI", "GMV")):
        return {"kind": "ad_roi", "cycles": [1, 3], "target": "日报", "name": "投放/ROI复盘"}
    if any(token in text for token in ("退款", "售后")):
        return {"kind": "after_sales", "cycles": [7], "target": "周报", "name": "售后退款复盘"}
    return {"kind": "general", "cycles": [3], "target": task.get("recapTarget") or "日报", "name": "经营动作复盘"}


def _existing(task_id: str, cycle_day: int) -> Dict[str, Any] | None:
    return next((item for item in RECAP_CYCLES if item.get("taskId") == task_id and item.get("cycleDay") == cycle_day), None)


def schedule_recap_cycles(task: Dict[str, Any], *, trigger: str = "manager_reviewed", actor_user_id: str | None = None) -> Dict[str, Any]:
    policy = recap_policy_for_task(task)
    created: List[Dict[str, Any]] = []
    base = datetime.now()
    for day in policy["cycles"]:
        existing = _existing(task.get("id"), day)
        if existing:
            created.append(deepcopy(existing))
            continue
        item = {
            "id": make_id(),
            "version": RECAP_SCHEDULER_VERSION,
            "taskId": task.get("id"),
            "taskTitle": task.get("title") or task.get("productTitle"),
            "cycleKind": policy["kind"],
            "cycleName": policy["name"],
            "cycleDay": day,
            "recapTarget": policy["target"],
            "status": "scheduled",
            "scheduledAt": (base + timedelta(days=day)).date().isoformat(),
            "trigger": trigger,
            "actorUserId": actor_user_id,
            "requiredMetrics": ["ROI", "GMV/支付金额", "访客数", "点击率", "转化率", "广告消耗", "库存消耗", "退款率", "毛利率"],
            "rule": "系统在复盘周期到达后从事实表/后续报表读取指标变化，运营不手填预测结果。",
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
        }
        RECAP_CYCLES.insert(0, item)
        created.append(deepcopy(item))
    return {"version": RECAP_SCHEDULER_VERSION, "policy": policy, "createdCycles": created, "scheduledCount": len(created)}


def list_recap_cycles_for_task(task_id: str) -> List[Dict[str, Any]]:
    return [deepcopy(item) for item in RECAP_CYCLES if item.get("taskId") == task_id]


def complete_recap_cycle(task: Dict[str, Any], *, cycle_id: str | None = None, before_metrics: Dict[str, Any] | None = None, after_metrics: Dict[str, Any] | None = None, reviewer_id: str | None = None, conclusion: str | None = None) -> Dict[str, Any]:
    before_metrics = before_metrics or {}
    after_metrics = after_metrics or {}
    cycle = next((item for item in RECAP_CYCLES if item.get("id") == cycle_id), None) if cycle_id else next((item for item in RECAP_CYCLES if item.get("taskId") == task.get("id") and item.get("status") == "scheduled"), None)
    if not cycle:
        schedule_recap_cycles(task, trigger="manual_complete", actor_user_id=reviewer_id)
        cycle = next((item for item in RECAP_CYCLES if item.get("taskId") == task.get("id") and item.get("status") == "scheduled"), None)
    if not cycle:
        return {"version": RECAP_SCHEDULER_VERSION, "ok": False, "message": "no recap cycle"}
    metric_keys = sorted(set(before_metrics) & set(after_metrics))
    effective = bool(metric_keys and conclusion not in {"无效", "失败", "invalid"})
    result = {
        "version": RECAP_SCHEDULER_VERSION,
        "taskId": task.get("id"),
        "cycleId": cycle.get("id"),
        "cycleDay": cycle.get("cycleDay"),
        "beforeMetrics": before_metrics,
        "afterMetrics": after_metrics,
        "metricKeys": metric_keys,
        "effective": effective,
        "conclusion": conclusion or ("任务动作有效，进入RAG候选" if effective else "指标不足，继续观察"),
        "completedAt": now_iso(),
        "reviewerId": reviewer_id,
    }
    cycle.update({"status": "completed", "result": result, "updatedAt": now_iso()})
    recap_candidate = add_recap_candidate({**task, "recapTarget": cycle.get("recapTarget")}, evidence=task.get("latestEvidenceRecord"), review=task.get("latestEvidenceReview"), source="automatic_recap_cycle")
    result["recapCandidate"] = recap_candidate
    return result


def recap_schedule_summary() -> Dict[str, Any]:
    return {
        "version": RECAP_SCHEDULER_VERSION,
        "total": len(RECAP_CYCLES),
        "scheduled": len([item for item in RECAP_CYCLES if item.get("status") == "scheduled"]),
        "completed": len([item for item in RECAP_CYCLES if item.get("status") == "completed"]),
        "latest": deepcopy(RECAP_CYCLES[0]) if RECAP_CYCLES else None,
    }
