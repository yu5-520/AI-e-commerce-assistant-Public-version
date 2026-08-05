"""V4.4 feedback flywheel service.

V4.4 closes the loop between task handling, daily / weekly recap, structured
experience-card drafting, review approval, and next-round RAG retrieval.
The service is advisory-only and does not approve memory automatically.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Dict, List

from src.services.account_service import current_user
from src.services.experience_memory_service import (
    draft_experience_from_task,
    list_cases,
    memory_summary,
    search_cases,
)
from src.services.module_task_service import DONE_STATUS, list_logs, list_tasks

FEEDBACK_FLYWHEEL_VERSION = "4.4.0"


def _viewer(user_id: str | None) -> Dict[str, Any]:
    user = current_user(user_id)
    return {"userId": user.get("id"), "roleId": user.get("roleId"), "roleName": user.get("roleName")}


def _all_tasks() -> List[Dict[str, Any]]:
    return list_tasks(active_only=False)


def _completed_tasks() -> List[Dict[str, Any]]:
    return [task for task in _all_tasks() if task.get("status") in DONE_STATUS or task.get("workflowStatus") in {"已归档", "已写入复盘", "已通过"}]


def _pending_tasks() -> List[Dict[str, Any]]:
    return [task for task in _all_tasks() if task.get("status") not in DONE_STATUS]


def _task_problem_type(task: Dict[str, Any]) -> str:
    text = " ".join(str(value) for value in [task.get("riskDomain"), task.get("taskType"), task.get("taskSignal"), task.get("task"), task.get("reason"), *(task.get("judgmentTags") or [])] if value)
    if any(word in text for word in ["ROI", "ROAS", "退款", "售后"]):
        return "low_roi_high_refund"
    if any(word in text for word in ["库存", "补货", "承接"]):
        return "low_inventory_activity"
    if any(word in text for word in ["点击", "CTR", "主图", "标题", "创意"]):
        return "low_ctr_low_conversion"
    if any(word in text for word in ["竞品", "差评"]):
        return "competitor_signal_to_test"
    return "general_operation"


def _source_task_ids_in_memory() -> set[str]:
    return {case.get("sourceTaskId") for case in list_cases(limit=500) if case.get("sourceTaskId")}


def _learning_candidates(limit: int = 12) -> List[Dict[str, Any]]:
    used = _source_task_ids_in_memory()
    candidates = []
    for task in _completed_tasks():
        if task.get("id") in used:
            continue
        candidates.append(
            {
                "taskId": task.get("id"),
                "title": task.get("title"),
                "riskDomain": task.get("riskDomain"),
                "problemType": _task_problem_type(task),
                "status": task.get("status"),
                "workflowStatus": task.get("workflowStatus"),
                "qualityHint": "有运营提交和总管复核，适合生成经验卡草案。" if task.get("submissionNote") and task.get("reviewNote") else "缺少提交 / 复核细节，需补充后再入库。",
                "operatorSubmission": task.get("submissionNote") or "",
                "managerReview": task.get("reviewNote") or "",
                "sourceModule": task.get("sourceModule"),
                "store": task.get("store"),
                "platform": task.get("platform"),
            }
        )
    return candidates[:limit]


def _agent_eval_metrics() -> Dict[str, Any]:
    tasks = _all_tasks()
    completed = _completed_tasks()
    pending = _pending_tasks()
    cases = list_cases(limit=500)
    approved = [case for case in cases if case.get("status") in {"approved", "seed_approved"}]
    pending_cases = [case for case in cases if case.get("status") == "pending_review"]
    by_problem = Counter(_task_problem_type(task) for task in tasks)
    return {
        "taskTotal": len(tasks),
        "taskCompleted": len(completed),
        "taskPending": len(pending),
        "completionRate": round(len(completed) / len(tasks), 3) if tasks else 0,
        "memoryTotal": len(cases),
        "memoryApproved": len(approved),
        "memoryPendingReview": len(pending_cases),
        "memoryApprovalRate": round(len(approved) / len(cases), 3) if cases else 0,
        "learningCandidateCount": len(_learning_candidates(limit=100)),
        "problemDistribution": dict(by_problem),
        "evalBoundary": "这里只评估任务-经验-召回闭环，不代表真实经营收益。",
    }


def feedback_flywheel_summary(*, user_id: str | None = None) -> Dict[str, Any]:
    memory = memory_summary()
    logs = list_logs()[:20]
    candidates = _learning_candidates()
    return {
        "version": FEEDBACK_FLYWHEEL_VERSION,
        "agentName": "回流任务 Agent",
        "mode": "task_to_memory_to_rag_flywheel",
        "viewer": _viewer(user_id),
        "rule": "任务完成后先生成经验卡草案，老板 / 总管复核后再进入正式 RAG 召回。",
        "chain": ["任务处理", "运营提交", "总管复核", "日报 / 周报归档", "经验卡草案", "复核入库", "下轮 RAG 召回"],
        "memorySummary": memory,
        "agentEvalMetrics": _agent_eval_metrics(),
        "learningCandidates": candidates,
        "recentLogs": logs,
        "humanDecision": ["哪些任务值得沉淀", "哪些经验可以复用", "哪些失败案例应该作为避坑边界", "哪些经验需要过期或降权"],
        "forbiddenActions": ["不自动批准经验入库", "不把原始日志直接写入正式 RAG", "不绕过总管复核", "不直接执行经营动作"],
    }


def cycle_feedback_agent(target: str = "日报", *, user_id: str | None = None, limit: int = 8) -> Dict[str, Any]:
    target = target or "日报"
    tasks = _all_tasks()
    completed = _completed_tasks()
    pending = _pending_tasks()
    review_tasks = [task for task in tasks if task.get("status") == "待复核"]
    candidates = _learning_candidates(limit=limit)
    problem_counter = Counter(_task_problem_type(task) for task in completed)
    top_problems = problem_counter.most_common(5)
    retrieved = []
    if top_problems:
        retrieved = search_cases(problem_type=top_problems[0][0], effective_only=False, limit=5).get("items") or []
    return {
        "version": FEEDBACK_FLYWHEEL_VERSION,
        "agentName": f"{target}回流 Agent",
        "target": target,
        "viewer": _viewer(user_id),
        "summary": f"本轮{target}复盘重点：已完成 {len(completed)} 项，待处理 {len(pending)} 项，待复核 {len(review_tasks)} 项，经验候选 {len(candidates)} 项。",
        "completedTasks": completed[:limit],
        "pendingTasks": pending[:limit],
        "reviewTasks": review_tasks[:limit],
        "learningCandidates": candidates,
        "problemDistribution": dict(problem_counter),
        "retrievedMemory": retrieved,
        "draftSections": [
            {"title": "今日完成", "items": [task.get("title") for task in completed[:limit]]},
            {"title": "待复核", "items": [task.get("title") for task in review_tasks[:limit]]},
            {"title": "可沉淀经验", "items": [item.get("title") for item in candidates[:limit]]},
            {"title": "下轮风险", "items": [task.get("title") for task in pending[:limit]]},
        ],
        "nextStep": "由总管确认哪些学习候选生成经验卡草案，再决定是否批准进入 RAG。",
        "forbiddenActions": ["不自动批准经验", "不直接把日报原文写入 RAG", "不代替复核人做经营判断"],
    }


def draft_cycle_memory(target: str = "日报", *, task_ids: List[str] | None = None, user_id: str | None = None, limit: int = 6) -> Dict[str, Any]:
    target = target or "日报"
    candidate_ids = [item["taskId"] for item in _learning_candidates(limit=limit)]
    selected = task_ids or candidate_ids
    drafts: List[Dict[str, Any]] = []
    failed: List[str] = []
    for task_id in selected[:limit]:
        task = next((item for item in _completed_tasks() if item.get("id") == task_id), None)
        if not task:
            failed.append(task_id)
            continue
        result = draft_experience_from_task(
            task_id,
            operator_submission=task.get("submissionNote") or f"{target}回流：运营处理动作待补充。",
            manager_review=task.get("reviewNote") or f"{target}回流：待总管复核是否可复用。",
            before_metrics=task.get("beforeMetrics") or {},
            after_metrics=task.get("afterMetrics") or {},
            user_id=user_id,
        )
        if result:
            drafts.append(result)
        else:
            failed.append(task_id)
    return {
        "version": FEEDBACK_FLYWHEEL_VERSION,
        "agentName": f"{target}经验回流 Agent",
        "target": target,
        "draftedCount": len(drafts),
        "failedTaskIds": failed,
        "drafts": drafts,
        "needsHumanReviewBeforeWrite": True,
        "nextStep": "老板 / 总管到 RAG Memory 待复核列表确认是否入库。",
        "writeBoundary": "生成经验卡草案不等于批准入库；正式召回仍需复核通过。",
    }
