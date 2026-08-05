"""V22.5 company operating style and SOP RAG context.

This module creates a compact, deterministic company context for Agent3. It never
contains provider secrets and it does not select the business action family.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

THREE_AGENT_PIPELINE_VERSION = "22.5.0"
COMPANY_SOP_RAG_CONTEXT_VERSION = THREE_AGENT_PIPELINE_VERSION


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _env_json(name: str) -> Dict[str, Any]:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _principles(values: Any, limit: int = 12) -> List[str]:
    return [
        _text(item, 320)
        for item in _arr(values)[:limit]
        if _text(item, 320)
    ]


def build_company_operating_policy_snapshot(source: Dict[str, Any] | None = None) -> Dict[str, Any]:
    source = _dict(source)
    explicit = _dict(source.get("companyOperatingPolicySnapshot"))
    configured = _env_json("COMPANY_OPERATING_POLICY_JSON")
    values = {**configured, **explicit}
    return {
        "version": COMPANY_SOP_RAG_CONTEXT_VERSION,
        "mode": "company_execution_policy",
        "managementStyle": _text(
            values.get("managementStyle")
            or os.getenv("COMPANY_MANAGEMENT_STYLE")
            or "少而准、数据可追溯、运营可直接执行"
        ),
        "principles": _principles(values.get("principles"))
        or [
            "SOP必须说明执行对象、唯一变量、时限、验证指标和停止条件。",
            "观察类判断不进入任务池；库存问题归仓储协同，不归运营绩效。",
            "额度内运营动作自动执行，高风险或超权限动作进入总管审核。",
            "禁止用固定模板填充不同商品，必须保留平台、类目和商品差异。",
        ],
        "taskTimingPolicy": _dict(values.get("taskTimingPolicy"))
        or {
            "urgent": "6小时内",
            "normal": "12小时内",
            "reviewCycles": ["3天", "7天", "14天", "30天", "90天"],
        },
        "responsibilityBoundary": _dict(values.get("responsibilityBoundary"))
        or {
            "operator": ["商品表达", "流量投放", "平台活动", "转化修复"],
            "warehouse": ["库存补货", "转仓", "断货协同"],
            "manager": ["超权限预算", "高风险表达", "跨部门争议"],
        },
        "fallbackAllowed": False,
    }


def build_approval_policy_snapshot(source: Dict[str, Any] | None = None) -> Dict[str, Any]:
    source = _dict(source)
    explicit = _dict(source.get("approvalPolicySnapshot"))
    configured = _env_json("COMPANY_APPROVAL_POLICY_JSON")
    values = {**configured, **explicit}
    return {
        "version": COMPANY_SOP_RAG_CONTEXT_VERSION,
        "operatorAutoExecuteWithinAuthority": values.get(
            "operatorAutoExecuteWithinAuthority", True
        )
        is not False,
        "managerApprovalTriggers": _principles(values.get("managerApprovalTriggers"))
        or [
            "超出运营预算额度",
            "高风险品牌、功效或合规表达",
            "需要跨部门资源且责任边界不清",
        ],
        "approvalEvidenceRequired": _principles(values.get("approvalEvidenceRequired"))
        or ["当前数据基线", "拟执行参数", "风险与回滚条件"],
    }


def build_brand_style_snapshot(source: Dict[str, Any] | None = None) -> Dict[str, Any]:
    source = _dict(source)
    identity = _dict(source.get("productIdentity"))
    explicit = _dict(source.get("brandStyleSnapshot"))
    platform = _text(
        explicit.get("platform")
        or identity.get("platform")
        or source.get("platform")
        or "通用货架电商"
    )
    default_style = {
        "天猫": "品牌质感、功能表达清晰、避免低价堆字",
        "京东": "参数可信、功能明确、强调履约和品质",
        "抖音": "场景直接、利益点前置、避免空泛品牌话术",
        "拼多多": "价格利益清晰、信息密度高、避免虚假夸张",
    }
    return {
        "version": COMPANY_SOP_RAG_CONTEXT_VERSION,
        "platform": platform,
        "brandTone": _text(
            explicit.get("brandTone")
            or os.getenv("COMPANY_BRAND_TONE")
            or default_style.get(platform)
            or "简洁、可信、可验证"
        ),
        "operatorLanguage": _text(
            explicit.get("operatorLanguage")
            or "使用运营可以直接执行的动作语言，不使用工程术语和空泛建议"
        ),
        "avoid": _principles(explicit.get("avoid"))
        or ["模板化套话", "无对象的建议", "无法验证的目标", "越权动作"],
    }


def build_company_sop_rag_snapshot(source: Dict[str, Any] | None = None) -> Dict[str, Any]:
    source = _dict(source)
    explicit = _dict(source.get("companySopRagSnapshot"))
    existing = _dict(source.get("ragContextSnapshot") or source.get("verticalActionRag"))
    approved = explicit.get("approvedCaseIds") or existing.get("approvedCaseIds") or []
    positive = explicit.get("positiveExperienceCards") or existing.get("positiveExperienceCards") or []
    negative = explicit.get("negativeCases") or existing.get("negativeCases") or []
    return {
        "version": COMPANY_SOP_RAG_CONTEXT_VERSION,
        "status": explicit.get("status") or existing.get("status") or "context_ready",
        "mode": "company_sop_rag",
        "approvedCaseIds": [str(item) for item in _arr(approved)[:12]],
        "positiveExperienceCards": [
            item for item in _arr(positive)[:8] if isinstance(item, dict)
        ],
        "negativeCases": [item for item in _arr(negative)[:8] if isinstance(item, dict)],
        "companyExecutionPrinciples": _principles(
            explicit.get("companyExecutionPrinciples")
        )
        or [
            "先保留执行前基线，再改变唯一变量。",
            "动作必须匹配商品身份、平台和类目，禁止跨商品复制数字。",
            "验证结果决定继续、回滚或升级，不用重复观察代替动作。",
            "SOP只在Agent1动作族和Agent2草案边界内展开。",
        ],
        "agentInstruction": _text(
            explicit.get("agentInstruction")
            or "根据公司执行习惯重组SOP，不照抄历史案例，不改变动作族与权限边界。",
            800,
        ),
        "fallbackAllowed": False,
    }


def build_agent3_company_context(source: Dict[str, Any] | None = None) -> Dict[str, Any]:
    source = _dict(source)
    return {
        "companyOperatingPolicySnapshot": build_company_operating_policy_snapshot(source),
        "companySopRagSnapshot": build_company_sop_rag_snapshot(source),
        "approvalPolicySnapshot": build_approval_policy_snapshot(source),
        "brandStyleSnapshot": build_brand_style_snapshot(source),
    }


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "COMPANY_SOP_RAG_CONTEXT_VERSION",
    "build_company_operating_policy_snapshot",
    "build_company_sop_rag_snapshot",
    "build_approval_policy_snapshot",
    "build_brand_style_snapshot",
    "build_agent3_company_context",
]
