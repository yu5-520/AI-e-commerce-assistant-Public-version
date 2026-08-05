"""Risk classification and approval rules for RPA task drafts."""

from __future__ import annotations

from typing import Dict

HIGH_RISK_TASK_TYPES = {
    "auto_price_change",
    "auto_campaign_registration",
    "auto_ad_spend_change",
    "auto_customer_message_blast",
    "auto_refund",
    "auto_product_publish",
    "auto_product_unpublish",
}

MEDIUM_RISK_TASK_TYPES = {
    "activity_prepare_table",
    "retention_task_list",
    "customer_message_draft",
    "sku_price_table",
}

LOW_RISK_TASK_TYPES = {
    "daily_report",
    "customer_segmentation_report",
    "after_sales_analysis",
    "review_iteration_report",
}


def classify_task(task_type: str) -> Dict[str, object]:
    """Return risk policy for a task type."""
    if task_type in HIGH_RISK_TASK_TYPES:
        return {
            "risk_level": "high",
            "requires_approval": True,
            "auto_execution_allowed": False,
            "policy_reason": "高风险任务涉及资金、平台操作、客户触达或不可回滚动作，只允许输出建议。",
        }

    if task_type in MEDIUM_RISK_TASK_TYPES:
        return {
            "risk_level": "medium",
            "requires_approval": True,
            "auto_execution_allowed": False,
            "policy_reason": "中风险任务可能影响价格、活动或客户运营，需要人工确认。",
        }

    return {
        "risk_level": "low",
        "requires_approval": True,
        "auto_execution_allowed": False,
        "policy_reason": "MVP 阶段默认保守：即使是低风险任务，也先进入人工确认后再执行。",
    }
