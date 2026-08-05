"""Merchant-facing view service built on top of the internal workflow.

The internal workflow keeps detailed engineering fields. This service converts
that structure into product API payloads that match the AI operating advisor UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from src.services.data_import_service import validate_all_imports
from src.workflow.mock_workflow import build_mock_workflow_result

ROOT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT_DIR / "outputs"


def _workflow(write_outputs: bool = False, record_logs: bool = False) -> Dict[str, Any]:
    return build_mock_workflow_result(write_outputs=write_outputs, record_logs=record_logs)


def _risk_label(level: str | None) -> str:
    return {"high": "重点风险", "medium": "需要关注", "low": "状态正常"}.get(level or "", "需要关注")


def _decision_label(decision: str | None) -> str:
    return {
        "enter_after_sales_diagnosis": "先查售后",
        "stop_or_reduce_budget": "先止损",
        "change_title_or_main_image": "换标题 / 主图",
        "adjust_sku_price_or_detail_page": "调规格 / 定价",
        "scale_carefully": "谨慎放量",
        "continue_testing": "继续观察",
    }.get(decision or "", decision or "继续观察")


def _module_label(module: str | None) -> str:
    return {
        "crm_after_sales_diagnosis": "售后归因",
        "erp_profit_and_budget_review": "利润与预算复核",
        "competitor_title_image_review": "标题 / 主图复查",
        "listing_sku_pricing_review": "规格 / 定价复查",
        "erp_product_risk_review": "商品风险复查",
        "controlled_scale_review": "小幅放量复核",
        "continue_operating_loop": "继续循环",
    }.get(module or "", module or "继续循环")


def _frequency_label(frequency: str | None) -> str:
    return {"daily": "每天", "weekly": "每周", "monthly": "每月"}.get(frequency or "", frequency or "未设置")


def _count_high_risk(items: List[Dict[str, Any]]) -> int:
    return len([item for item in items if item.get("risk_level") == "high"])


def _task_queue(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    summary = result.get("summary", {})
    products = result.get("product_diagnosis", [])
    traffic = result.get("traffic_feedback_report", {})
    listing = result.get("listing_growth_plan", {})
    tasks = result.get("approval_required_tasks") or result.get("rpa_tasks", [])

    product_count = summary.get("product_count") or len(products)
    high_action_count = _count_high_risk(tasks)
    if tasks and high_action_count == 0:
        high_action_count = 1

    return [
        {
            "rank": 1,
            "title": "复查高退款商品",
            "urgency": "紧急",
            "urgency_level": "high",
            "deadline": "今天 18:00 前",
            "count": product_count,
            "impact": "退款率 / 评分",
            "reason": "先复查尺码、面料、物流、客服 SOP 和卖点承诺，售后原因完成前不建议继续放量。",
            "source": "商品体检 + 售后回流",
        },
        {
            "rank": 2,
            "title": "确认售后敏感问题",
            "urgency": "紧急",
            "urgency_level": "high",
            "deadline": "今天内",
            "count": high_action_count,
            "impact": "客服承接",
            "reason": "退款、客户触达和高风险动作必须先由人工确认，系统只生成判断与草案。",
            "source": "确认动作",
        },
        {
            "rank": 3,
            "title": "小范围流量测试",
            "urgency": "中",
            "urgency_level": "medium",
            "deadline": "明天 12:00 前",
            "count": summary.get("traffic_experiment_count") or traffic.get("experiment_count") or 0,
            "impact": "ROI / 库存承接",
            "reason": "可小幅测试，但必须继续观察退款率、ROI 和库存承接，不直接扩大投放。",
            "source": "流量复盘",
        },
        {
            "rank": 4,
            "title": "上新前确认素材",
            "urgency": "中",
            "urgency_level": "medium",
            "deadline": "明天内",
            "count": summary.get("listing_candidate_count") or listing.get("candidate_count") or 0,
            "impact": "转化率",
            "reason": "上新候选只进入标题、主图、规格和合规检查，确认后再进入下一步。",
            "source": "上新建议",
        },
    ]


def _task_distribution(task_queue: List[Dict[str, Any]], summary: Dict[str, Any], traffic: Dict[str, Any]) -> List[Dict[str, Any]]:
    urgent_count = sum(item.get("count", 0) for item in task_queue if item.get("urgency_level") == "high")
    due_count = task_queue[0].get("count", 0) if task_queue else 0
    pending_count = summary.get("approval_required_count", 0)
    test_count = summary.get("traffic_experiment_count") or traffic.get("experiment_count") or 0
    return [
        {"title": "紧急任务", "value": urgent_count, "desc": "需要先处理"},
        {"title": "到期任务", "value": due_count, "desc": "有时间限制"},
        {"title": "待确认", "value": pending_count, "desc": "确认前不执行"},
        {"title": "可测试机会", "value": test_count, "desc": "小范围观察"},
    ]


def get_today_advice(write_outputs: bool = False, record_logs: bool = False) -> Dict[str, Any]:
    result = _workflow(write_outputs=write_outputs, record_logs=record_logs)
    summary = result.get("summary", {})
    loop = result.get("operating_loop_summary", {})
    traffic = result.get("traffic_feedback_report", {})
    operating_unit = result.get("operating_unit", {})
    cycle_policy = result.get("cycle_policy", {})
    task_queue = _task_queue(result)
    task_distribution = _task_distribution(task_queue, summary, traffic)

    return {
        "page_title": "任务清单",
        "priority": {
            "title": "任务清单",
            "reason": traffic.get("next_action") or "按紧急程度、截止时间和经营影响自动排序。",
            "next_steps": loop.get("next_iteration_plan", []),
            "pending_count": summary.get("approval_required_count", 0),
            "urgent_count": task_distribution[0]["value"],
            "due_count": task_distribution[1]["value"],
            "next_module_label": _module_label(summary.get("loop_next_module") or loop.get("next_module")),
        },
        "operating_unit": {
            "name": summary.get("unit_name") or operating_unit.get("unit_name"),
            "id": summary.get("operating_unit_id") or operating_unit.get("operating_unit_id"),
            "source": "根据 ERP 商品结构识别",
        },
        "cycle": {
            "frequency": summary.get("cycle_frequency") or cycle_policy.get("cycle_frequency"),
            "frequency_label": _frequency_label(summary.get("cycle_frequency") or cycle_policy.get("cycle_frequency")),
            "type": summary.get("cycle_type") or cycle_policy.get("cycle_type"),
            "run_time": cycle_policy.get("run_time"),
            "report_type": cycle_policy.get("report_type"),
        },
        "task_distribution": task_distribution,
        "cards": task_distribution,
        "task_queue": task_queue,
        "execution_rules": [
            "未确认前，不自动上架、改价或投放",
            "涉及退款、客户触达和库存调整，必须人工确认",
            "系统只生成任务、判断、草案和报告",
        ],
        "raw": result,
    }


def get_operating_unit_view() -> Dict[str, Any]:
    result = _workflow()
    unit = result.get("operating_unit", {})
    policy = result.get("cycle_policy", {})
    return {
        "unit_name": unit.get("unit_name"),
        "unit_id": unit.get("operating_unit_id"),
        "source": "根据 ERP 商品结构识别",
        "dominant_product_group": unit.get("dominant_product_group"),
        "reason": unit.get("reason"),
        "product_group_summary": unit.get("product_group_summary", {}),
        "keyword_signals": unit.get("keyword_signals", {}),
        "cycle_policy": {
            "frequency": policy.get("cycle_frequency"),
            "frequency_label": _frequency_label(policy.get("cycle_frequency")),
            "type": policy.get("cycle_type"),
            "run_time": policy.get("run_time"),
            "report_type": policy.get("report_type"),
            "description": policy.get("description"),
            "trigger_rules": policy.get("trigger_rules", []),
        },
    }


def get_data_health() -> Dict[str, Any]:
    validation = validate_all_imports()
    return {
        "status": validation.get("status"),
        "summary": {
            "dataset_count": len(validation.get("datasets", [])),
            "failed_count": validation.get("failed_count", 0),
            "relationship_check_count": len(validation.get("relationship_checks", [])),
        },
        "datasets": [
            {
                "name": item.get("label") or item.get("dataset_name"),
                "filename": item.get("filename"),
                "row_count": item.get("row_count"),
                "status": item.get("status"),
            }
            for item in validation.get("datasets", [])
        ],
        "message": "数据足够支撑本轮经营判断。" if validation.get("status") == "passed" else "数据仍需补齐后再判断。",
    }


def get_product_health() -> Dict[str, Any]:
    result = _workflow()
    products = result.get("product_diagnosis", [])
    return {
        "title": "商品体检结果",
        "summary": result.get("summary", {}),
        "items": [
            {
                "product_id": item.get("product_id"),
                "product_name": item.get("product_name"),
                "risk_level": item.get("risk_level"),
                "risk_label": _risk_label(item.get("risk_level")),
                "risks": item.get("risks", []),
                "suggestions": item.get("suggested_actions", []),
                "stock": item.get("stock"),
                "gross_margin": item.get("gross_margin"),
                "activity_margin": item.get("activity_margin"),
            }
            for item in products
        ],
    }


def get_competitor_opportunities() -> Dict[str, Any]:
    result = _workflow()
    analysis = result.get("competitor_analysis", {})
    review_gap = analysis.get("review_gap", {})
    return {
        "title": "竞品机会",
        "category_name": analysis.get("category_name"),
        "competitor_count": analysis.get("competitor_count", 0),
        "trigger_product": analysis.get("reference_product", {}),
        "price_gap": analysis.get("price_gap", {}),
        "bad_review_keywords": review_gap.get("top_bad_review_keywords", []),
        "opportunity_actions": review_gap.get("opportunity_actions", []),
        "next_action": analysis.get("next_action"),
        "safe_use_policy": analysis.get("safe_use_policy"),
    }


def get_listing_suggestions() -> Dict[str, Any]:
    result = _workflow()
    plan = result.get("listing_growth_plan", {})
    draft = plan.get("listing_draft", {})
    return {
        "title": "上新建议",
        "candidate_count": plan.get("candidate_count", 0),
        "top_candidate": plan.get("top_candidate", {}),
        "title_draft": draft.get("title_draft"),
        "image_plan": draft.get("image_plan", []),
        "sku_plan": draft.get("sku_plan", []),
        "compliance_checklist": draft.get("compliance_checklist", []),
        "next_action": plan.get("next_action"),
        "safe_use_policy": plan.get("safe_use_policy"),
    }


def get_traffic_review() -> Dict[str, Any]:
    result = _workflow()
    report = result.get("traffic_feedback_report", {})
    return {
        "title": "流量复盘",
        "experiment_count": report.get("experiment_count", 0),
        "decision_summary": report.get("decision_summary", {}),
        "risk_summary": report.get("risk_summary", {}),
        "next_action": report.get("next_action"),
        "loopback_actions": report.get("loopback_actions", []),
        "items": [
            {
                **item,
                "decision_label": _decision_label(item.get("decision")),
                "risk_label": _risk_label(item.get("risk_level")),
            }
            for item in report.get("diagnoses", [])
        ],
        "safe_use_policy": report.get("safe_use_policy"),
    }


def get_action_confirmations() -> Dict[str, List[Dict[str, Any]]]:
    result = _workflow()
    tasks = result.get("approval_required_tasks") or result.get("rpa_tasks", [])
    return {
        "items": [
            {
                "action_id": task.get("task_id"),
                "action_name": task.get("task_type"),
                "risk_level": task.get("risk_level"),
                "risk_label": _risk_label(task.get("risk_level")),
                "suggestion": task.get("ai_suggestion"),
                "status": task.get("approval_status") or task.get("status") or "pending",
                "auto_execution_allowed": task.get("auto_execution_allowed", False),
                "policy_reason": task.get("policy_reason"),
            }
            for task in tasks
        ]
    }


def get_business_report_text() -> str:
    report_path = OUTPUT_DIR / "operating_report.md"
    if not report_path.exists():
        _workflow(write_outputs=True, record_logs=True)
    return report_path.read_text(encoding="utf-8")
