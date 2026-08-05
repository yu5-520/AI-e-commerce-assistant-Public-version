"""V19.12.2 decision write guard.

This module patches the legacy ``dual_agent_product_task_service._save_decision``
write boundary. Any historical worker or route that still tries to write old
V19.9 template decisions into ``task_generation_decisions_v15`` is blocked before
it reaches the database.

The rule is intentionally strict for title/main-image tasks:
- old sopSource is rejected;
- placeholder creative templates are rejected;
- formal title_image_test tasks need judgment Agent creativeTestPlan.groups >= 2;
- creative_plan_missing/data_evidence_task is allowed as a data completion task.
"""

from __future__ import annotations

import json
from importlib import import_module
from typing import Any, Dict, List

DECISION_WRITE_GUARD_VERSION = "19.12.2"
FORMAL_DECISIONS = {"create_task_snapshot", "manager_review_required"}
LEGACY_SOP_SOURCES = {"v19_9_action_parameter_pack_sop"}
TEMPLATE_MARKERS = [
    "核心场景词",
    "核心卖点",
    "使用场景等占位词",
    "设计2-3组新标题和主图变体",
    "在广告平台创建A/B测试",
    "监控测试数据",
    "评估测试结果并应用最优素材",
    "商品主体+核心场景+关键卖点",
    "围绕核心场景词重写标题",
    "突出主卖点与场景",
    "标题方向一突出",
    "v19_9_action_parameter_pack_sop",
]
_ALLOWED_MISSING_STATUS = {"creative_plan_missing", "insufficient"}
_INSTALLED = False
_ORIGINAL_SAVE = None


def _plan(decision: Dict[str, Any]) -> Dict[str, Any]:
    value = decision.get("taskPlan")
    return value if isinstance(value, dict) else {}


def _package(decision: Dict[str, Any]) -> Dict[str, Any]:
    value = decision.get("productJudgmentPackage")
    return value if isinstance(value, dict) else {}


def _family(decision: Dict[str, Any]) -> str:
    plan = _plan(decision)
    package = _package(decision)
    return str(plan.get("selectedActionFamily") or package.get("selectedActionFamilyHint") or "").strip()


def _pack_status(decision: Dict[str, Any]) -> str:
    plan = _plan(decision)
    package = _package(decision)
    for value in [plan.get("actionParameterPack"), package.get("actionParameterPack")]:
        if isinstance(value, dict) and value.get("status"):
            return str(value.get("status"))
    return ""


def _creative_plan(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = _plan(decision)
    package = _package(decision)
    for value in [plan.get("creativeTestPlan"), package.get("creativeTestPlan"), package.get("agentCreativePack")]:
        if isinstance(value, dict):
            return value
    return {}


def _has_template_marker(value: Any) -> List[str]:
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else str(value)
    return [marker for marker in TEMPLATE_MARKERS if marker in text]


def _creative_group_count(decision: Dict[str, Any]) -> int:
    creative = _creative_plan(decision)
    groups = creative.get("groups") if isinstance(creative.get("groups"), list) else []
    count = 0
    for group in groups[:5]:
        if not isinstance(group, dict):
            continue
        if _has_template_marker(group):
            continue
        if group.get("fullTitle") and isinstance(group.get("mainImageStructure"), dict):
            count += 1
    return count


def _is_creative_missing_task(decision: Dict[str, Any]) -> bool:
    plan = _plan(decision)
    task_type = str(plan.get("taskType") or "")
    reason = str(plan.get("reason") or decision.get("reason") or "")
    status = _pack_status(decision)
    return bool(
        task_type == "data_evidence_task"
        or plan.get("creativePlanMissing")
        or status in _ALLOWED_MISSING_STATUS
        or "creativeTestPlan" in reason
        or "创意测试方案缺失" in reason
    )


def decision_reject_reasons(decision: Dict[str, Any]) -> List[str]:
    if not isinstance(decision, dict):
        return []
    reasons: List[str] = []
    plan = _plan(decision)
    evidence = decision.get("taskMappingAgentEvidence") if isinstance(decision.get("taskMappingAgentEvidence"), dict) else {}
    family = _family(decision)
    decision_type = str(decision.get("decision") or "")
    sop_source = str(plan.get("sopSource") or "")
    mapping_mode = str(evidence.get("mappingMode") or "")
    inspect_text = {
        "sopSource": sop_source,
        "mappingMode": mapping_mode,
        "taskTitle": decision.get("taskTitle") or plan.get("taskTitle") or plan.get("title"),
        "sopSteps": plan.get("operatorExecutionSop") or plan.get("sopSteps"),
        "titleVariants": plan.get("titleVariants"),
        "mainImageStructures": plan.get("mainImageStructures"),
    }

    if sop_source in LEGACY_SOP_SOURCES:
        reasons.append("legacy_sop_source_removed")

    markers = _has_template_marker(inspect_text)
    if markers:
        reasons.append("legacy_template_markers_removed:" + ",".join(markers[:4]))

    if decision_type in FORMAL_DECISIONS and family == "title_image_test":
        if _is_creative_missing_task(decision):
            # Data completion tasks are allowed, but they still must not carry old templates.
            if markers or sop_source in LEGACY_SOP_SOURCES:
                reasons.append("creative_missing_task_contains_removed_template")
        elif _creative_group_count(decision) < 2:
            reasons.append("formal_title_image_missing_creativeTestPlan_groups")

    return list(dict.fromkeys(reasons))


def install_decision_write_guard() -> Dict[str, Any]:
    global _INSTALLED, _ORIGINAL_SAVE
    if _INSTALLED:
        return {"version": DECISION_WRITE_GUARD_VERSION, "installed": True, "alreadyInstalled": True}

    base = import_module("src.services.dual_agent_product_task_service")
    original = getattr(base, "_save_decision", None)
    if not callable(original):
        return {"version": DECISION_WRITE_GUARD_VERSION, "installed": False, "reason": "base_save_decision_missing"}

    _ORIGINAL_SAVE = original

    def guarded_save_decision(decision: Dict[str, Any]) -> None:
        reasons = decision_reject_reasons(decision)
        if reasons:
            try:
                print(
                    "[V19.12.2 decision-write-guard] rejected legacy task decision:",
                    ";".join(reasons),
                    "decisionId=",
                    decision.get("decisionId") if isinstance(decision, dict) else None,
                    "packageId=",
                    decision.get("packageId") if isinstance(decision, dict) else None,
                )
            except Exception:
                pass
            return None
        return original(decision)

    guarded_save_decision.__name__ = "guarded_save_decision_v19122"
    guarded_save_decision.__doc__ = "V19.12.2 guarded _save_decision: blocks legacy SOP/template writes."
    setattr(base, "_save_decision", guarded_save_decision)
    setattr(base, "DECISION_WRITE_GUARD_VERSION", DECISION_WRITE_GUARD_VERSION)
    setattr(base, "decision_reject_reasons", decision_reject_reasons)
    _INSTALLED = True
    return {"version": DECISION_WRITE_GUARD_VERSION, "installed": True, "baseModule": base.__name__}
