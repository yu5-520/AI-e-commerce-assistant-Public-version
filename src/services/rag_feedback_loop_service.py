"""V20.28 task recap -> reviewed experience -> Agent RAG feedback loop.

Completed recap cycles create pending experience cards. Only cards explicitly
approved, effective and linked to a real source task are eligible for future Agent
retrieval. Demo seed cards never enter the production Agent chain.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from src.services.agent_rag_context_v2028_service import (
    AGENT_RAG_CONTEXT_VERSION,
    build_agent_rag_context_snapshot,
)
from src.services.experience_memory_service import draft_experience_from_task
from src.services.module_task_service import find_task

RAG_FEEDBACK_LOOP_VERSION = "20.28"

ACTION_FAMILIES = {
    "title_image_test",
    "roas_scale",
    "roas_guard",
    "platform_activity",
    "conversion_repair",
    "similar_product_test",
    "warehouse_coordination",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _action_family(task: Dict[str, Any]) -> str:
    task_plan = _dict(task.get("taskPlan"))
    plan = _dict(task.get("agent2ActionPlan"))
    family = str(
        task.get("selectedActionFamily")
        or task.get("actionFamily")
        or task_plan.get("selectedActionFamily")
        or plan.get("actionFamily")
        or ""
    ).strip()
    if family in ACTION_FAMILIES:
        return family
    text = " ".join(str(value or "") for value in [task.get("riskDomain"), task.get("taskType"), task.get("title"), task.get("reason"), task.get("task")])
    if any(token in text for token in ("主图", "标题", "CTR", "点击率")):
        return "title_image_test"
    if any(token in text for token in ("ROAS放量", "扩大投放", "增加预算")):
        return "roas_scale"
    if any(token in text for token in ("ROAS", "ROI", "低效计划", "止损", "收缩预算")):
        return "roas_guard"
    if any(token in text for token in ("活动", "优惠券", "平台补贴")):
        return "platform_activity"
    if any(token in text for token in ("转化", "详情页", "评价", "承接", "退款", "售后")):
        return "conversion_repair"
    if any(token in text for token in ("库存", "补货", "仓储", "可售天数")):
        return "warehouse_coordination"
    return "similar_product_test"


def build_rag_candidate_from_recap(
    task_id: str,
    *,
    recap_result: Dict[str, Any] | None = None,
    user_id: str | None = None,
) -> Dict[str, Any] | None:
    task = find_task(task_id)
    if not task:
        return None
    recap_result = recap_result or {}
    card = draft_experience_from_task(
        task_id,
        operator_submission=task.get("submissionNote") or task.get("submitSummary") or "运营提交材料待补充。",
        manager_review=recap_result.get("conclusion") or task.get("reviewNote") or "复盘完成，等待人工审核是否进入RAG。",
        before_metrics=recap_result.get("beforeMetrics") or task.get("beforeMetrics") or {},
        after_metrics=recap_result.get("afterMetrics") or task.get("afterMetrics") or {},
        user_id=user_id,
    )
    if not card:
        return None
    experience = _dict(card.get("experienceCard"))
    experience["actionFamily"] = _action_family(task)
    experience["problemType"] = experience["actionFamily"]
    experience["retrievalContractVersion"] = AGENT_RAG_CONTEXT_VERSION
    experience["agentRetrievalEligible"] = False
    experience["eligibilityRule"] = "pending_review cards are never retrieved; a manager must approve the card and quality must remain >= 0.70."
    return {
        "version": RAG_FEEDBACK_LOOP_VERSION,
        "source": "automatic_recap_completed",
        "taskId": task_id,
        "actionFamily": experience.get("actionFamily"),
        "recapResult": recap_result,
        "ragCandidate": {**card, "experienceCard": experience},
        "rule": "V20.28 recap creates a pending candidate; only later approved/effective real-task cards can be retrieved by Agent2.",
    }


def retrieve_rag_feedback_for_task(task: Dict[str, Any], *, limit: int = 5) -> Dict[str, Any]:
    """Use the same production retrieval path as the Agent pipeline."""
    family = _action_family(task)
    task_plan = _dict(task.get("taskPlan"))
    product = _dict(task.get("productIdentity") or task_plan.get("productIdentity"))
    package = {
        "productId": task.get("productId") or task_plan.get("productId") or product.get("productId"),
        "storeId": task.get("storeId") or task_plan.get("storeId") or product.get("storeId") or "global",
        "productTitle": task.get("productShort") or task.get("title") or product.get("productTitle"),
        "platform": task.get("platform") or product.get("platform"),
        "verticalCategory": task.get("categoryId") or product.get("verticalCategory"),
        "productIdentity": product,
        "actionFamily": family,
        "agent1OperatingJudgment": {
            "selectedActionFamily": family,
            "primaryBusinessSignal": task.get("taskSignal") or task.get("reason"),
            "primaryOperatingGap": task.get("reason") or task.get("task"),
            "selectedOperatingRoute": task.get("operationMode") or task_plan.get("operationMode"),
            "actionFamilyLock": {"locked": True, "selectedActionFamily": family},
        },
    }
    snapshot = build_agent_rag_context_snapshot(package, task_plan, limit=limit)
    items: List[Dict[str, Any]] = [deepcopy(item) for item in (snapshot.get("positiveExperienceCards") or []) + (snapshot.get("negativeCases") or [])]
    return {
        "version": RAG_FEEDBACK_LOOP_VERSION,
        "mode": "approved_real_experience_to_agent_generation",
        "actionFamily": family,
        "items": items,
        "matchedCount": len(items),
        "ragContextSnapshot": snapshot,
        "rule": "Only approved/effective cards linked to real tasks are retrieved; Demo seed and pending_review cards are excluded.",
    }


def apply_rag_feedback_to_task(task: Dict[str, Any]) -> Dict[str, Any]:
    feedback = retrieve_rag_feedback_for_task(task)
    memory = dict(task.get("ragBusinessMemory") or {})
    memory["feedbackLoopVersion"] = RAG_FEEDBACK_LOOP_VERSION
    memory["approvedExperienceCards"] = feedback.get("items") or []
    memory["approvedExperienceMatchedCount"] = feedback.get("matchedCount", 0)
    memory["feedbackRule"] = feedback.get("rule")
    return {**task, "ragBusinessMemory": memory, "ragFeedbackLoop": feedback}
