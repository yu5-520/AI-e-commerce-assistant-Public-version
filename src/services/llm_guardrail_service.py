"""LLM output guardrail service.

The guardrail layer does not try to be a full policy engine. It protects the
business workflow contract: LLM can enrich language and generate drafts, but it
must not introduce direct execution instructions for price, ads, refunds,
publishing, or ERP / CRM writes.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

GUARDRAIL_VERSION = "4.5.0"

FORBIDDEN_ACTIONS = [
    "直接改价",
    "自动改价",
    "直接投放",
    "自动投放",
    "提高预算",
    "直接退款",
    "自动退款",
    "直接发布",
    "自动发布",
    "直接上架",
    "自动上架",
    "回写 ERP",
    "回写 CRM",
    "写入店铺后台",
]

REQUIRED_BOUNDARY = "LLM 只生成草案和表达增强，不直接执行经营动作；所有动作必须进入任务池、人工确认和复核。"


def scan_forbidden_text(text: str) -> List[str]:
    if not text:
        return []
    return [word for word in FORBIDDEN_ACTIONS if word in text]


def _walk_strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: List[str] = []
        for item in value:
            result.extend(_walk_strings(item))
        return result
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(_walk_strings(item))
        return result
    return []


def validate_llm_payload(payload: Dict[str, Any], *, expected_keys: List[str] | None = None) -> Dict[str, Any]:
    expected_keys = expected_keys or []
    missing = [key for key in expected_keys if key not in payload]
    hits: List[str] = []
    for text in _walk_strings(payload):
        hits.extend(scan_forbidden_text(text))
    return {
        "version": GUARDRAIL_VERSION,
        "valid": not missing and not hits,
        "missingKeys": missing,
        "forbiddenHits": sorted(set(hits)),
        "boundary": REQUIRED_BOUNDARY,
    }


def apply_output_guardrail(payload: Dict[str, Any], *, expected_keys: List[str] | None = None) -> Dict[str, Any]:
    item = deepcopy(payload or {})
    check = validate_llm_payload(item, expected_keys=expected_keys)
    item["llmGuardrail"] = check
    if not check["valid"]:
        item["llmFallbackRequired"] = True
        item["llmFallbackReason"] = "missing_schema_keys_or_forbidden_actions"
    item.setdefault("forbiddenActions", ["不直接改价", "不直接投放", "不直接退款", "不直接发布商品", "不直接回写 ERP / CRM"])
    item["llmBoundary"] = REQUIRED_BOUNDARY
    return item


def guardrail_summary() -> Dict[str, Any]:
    return {
        "version": GUARDRAIL_VERSION,
        "forbiddenActions": FORBIDDEN_ACTIONS,
        "requiredBoundary": REQUIRED_BOUNDARY,
        "rule": "LLM 输出只能作为草案；若命中越权动作或缺失结构字段，则使用确定性 fallback。",
    }
