from __future__ import annotations

from typing import Any, Dict, List


def build_category_risk_rules(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Build lightweight category-specific risk rules for workflow context."""
    category_name = profile.get("category_name", "未知类目")
    return {
        "category_name": category_name,
        "risk_focus": [
            "价格带与活动价安全线",
            "尺码退换风险",
            "面料与功效表达风险",
            "主图夸大承诺风险",
            "季节性库存压力",
        ],
        "default_human_review_triggers": [
            "活动价低于安全线",
            "出现绝对化防晒功效表达",
            "一次性新增过多颜色或尺码库存",
            "高退款率但仍计划放量",
            "上新字段草案准备进入真实平台后台",
        ],
        "safe_output_policy": (
            f"{category_name} 类目当前只允许生成经营判断、竞品比对、上新资料草案和流量测试计划；"
            "真实上架、改价、投放和客户触达必须人工确认。"
        ),
    }


def suggest_category_next_steps(profile: Dict[str, Any]) -> List[str]:
    """Return roadmap-friendly next steps for this category profile."""
    category_name = profile.get("category_name", "当前类目")
    return [
        f"把 {category_name} 类目知识注入商品经营诊断。",
        f"补充 {category_name} 同类目竞品比对 Mock 数据和报告生成。",
        f"补充 {category_name} 供应链货盘和上新候选评分。",
        f"补充 {category_name} 流量测试回流和下一步动作判断。",
    ]
