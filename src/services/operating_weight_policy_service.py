"""V12.8.2 operating weight policy.

High-weight store/product status is a governance label, not a report performance
label. Imported labels such as "高权重店铺" are not trusted unless the source is
RAG, manager, owner, or multi-period historical contribution.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

OPERATING_WEIGHT_POLICY_VERSION = "12.8.2"

EXPLICIT_HIGH_MARKERS = ("高权重", "主推商品", "核心商品", "核心SKU", "战略店铺", "品牌主店", "老板关注", "审批保护", "不可直接改价", "不可直接改主图", "不可直接改标题")
EXPLICIT_STRATEGIC_MARKERS = ("战略", "老板指定", "品牌主店", "核心店铺", "核心商品", "核心SKU")
REPORT_ONLY_MARKERS = ("高ROI", "高GMV", "低库存", "点击率", "转化率", "活动流量", "春夏新品", "常规款", "季节款", "已入库", "已入库商品", "商品数量")
TRUSTED_GOVERNANCE_SOURCES = {"owner_marked", "manager_marked", "rag_declared", "historical_contribution"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _collect_explicit_text(task: Dict[str, Any]) -> str:
    ownership = task.get("ownership") or {}
    detail = task.get("taskDetailReport") or {}
    rag = task.get("ragBusinessMemory") or task.get("v126RagMemory") or {}
    policy = rag.get("companyBaseline") if isinstance(rag.get("companyBaseline"), dict) else {}
    allowed_keys = (
        "governanceWeightTag",
        "governanceWeightLevel",
        "governanceWeightSource",
        "weightSource",
        "weightLevel",
        "governanceSource",
    )
    parts: list[str] = []
    for source in (task, ownership, detail, policy):
        for key in allowed_keys:
            if isinstance(source, dict) and source.get(key):
                parts.append(_clean(source.get(key)))
    explicit = task.get("explicitWeightTags") or task.get("governanceTags") or []
    if isinstance(explicit, list):
        parts.extend(_clean(item) for item in explicit)
    return " ".join(part for part in parts if part)


def _declared_source(task: Dict[str, Any]) -> str:
    text = _collect_explicit_text(task)
    lower = text.lower()
    if "owner_marked" in lower or "老板" in text:
        return "owner_marked"
    if "manager_marked" in lower or "主管" in text or "总管" in text:
        return "manager_marked"
    if "rag_declared" in lower or "rag" in lower or "配置" in text:
        return "rag_declared"
    if "historical_contribution" in lower or "90" in text or "长期贡献" in text or "多期" in text:
        return "historical_contribution"
    if text:
        return "untrusted_imported_label"
    return "first_report_baseline"


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def infer_operating_weight(task: Dict[str, Any]) -> Dict[str, Any]:
    explicit_text = _collect_explicit_text(task)
    source = _declared_source(task)
    trusted_source = source in TRUSTED_GOVERNANCE_SOURCES
    strategic_marker = _contains_any(explicit_text, EXPLICIT_STRATEGIC_MARKERS)
    high_marker = strategic_marker or _contains_any(explicit_text, EXPLICIT_HIGH_MARKERS) or "high" in explicit_text.lower() or "strategic" in explicit_text.lower()
    strategic = trusted_source and strategic_marker
    high = trusted_source and high_marker
    if strategic:
        level = "strategic"
        confidence = "high"
    elif high:
        level = "high"
        confidence = "high"
    else:
        level = "middle"
        confidence = "low" if source in {"first_report_baseline", "untrusted_imported_label"} else "medium"
    can_trigger_approval = level in {"high", "strategic"} and trusted_source and confidence in {"medium", "high"}
    return {
        "version": OPERATING_WEIGHT_POLICY_VERSION,
        "mode": "trusted_governance_weight_only",
        "storeWeight": level,
        "productWeight": level,
        "combinedWeight": level,
        "weightLevel": level,
        "weightConfidence": confidence,
        "weightSource": source,
        "trustedGovernanceSource": trusted_source,
        "canTriggerApproval": can_trigger_approval,
        "explicitWeightText": explicit_text,
        "ignoredImportedHighWeightLabel": bool(high_marker and not trusted_source),
        "reportOnlyMarkersIgnored": list(REPORT_ONLY_MARKERS),
        "trustedSources": sorted(TRUSTED_GOVERNANCE_SOURCES),
        "rule": "高权重必须来自RAG配置、主管/老板标记或多期历史贡献；导入标签、商品数量、高ROI、高GMV、任务优先级和首份报表标签不能触发高权重审批。",
    }


def is_governance_high_weight(weight: Dict[str, Any]) -> bool:
    return bool(weight.get("canTriggerApproval") and weight.get("combinedWeight") in {"high", "strategic"} and weight.get("trustedGovernanceSource"))
