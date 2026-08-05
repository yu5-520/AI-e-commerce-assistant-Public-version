"""V22.5.5 execution lock with the V22.5.13 reversible-test repair."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from src.runtime_version import THREE_AGENT_PIPELINE_VERSION

EXECUTION_LOCK_VERSION = THREE_AGENT_PIPELINE_VERSION
EXECUTION_LOCK_HOTFIX_VERSION = "22.5.13"
EVIDENCE_SUFFICIENT = "sufficient"
EVIDENCE_INSUFFICIENT = "insufficient"
EVIDENCE_CONFLICT = "conflict"
EVIDENCE_STATUSES = {
    EVIDENCE_SUFFICIENT,
    EVIDENCE_INSUFFICIENT,
    EVIDENCE_CONFLICT,
}

_REVERSIBLE_TARGET_TYPES = {
    "title_image_test": "product_creative_asset",
    "title_test": "product_creative_asset",
    "image_test": "product_creative_asset",
    "creative_test": "product_creative_asset",
    "creative_optimization": "product_creative_asset",
    "title_image_optimization": "product_creative_asset",
    "roas_scale": "product_ad_plan_scope",
    "roas_adjustment": "product_ad_plan_scope",
    "traffic_scaling": "product_ad_plan_scope",
    "budget_scaling": "product_ad_plan_scope",
    "ad_plan_adjustment": "product_ad_plan_scope",
    "activity_test": "platform_activity_application",
    "activity_enrollment": "platform_activity_application",
    "promotion_enrollment": "platform_activity_application",
    "pricing_test": "product_price_test",
    "price_test": "product_price_test",
    "detail_page_test": "product_detail_page",
    "product_detail_optimization": "product_detail_page",
}

_HARD_EVIDENCE_MARKERS = (
    "source identity incomplete",
    "source lineage",
    "sourceidentity",
    "数据来源不完整",
    "来源身份不完整",
    "来源血缘",
    "evidence conflict",
    "证据冲突",
    "permission",
    "authorization",
    "权限",
    "审批",
    "budget cap",
    "budget limit",
    "budget permission",
    "预算上限",
    "预算权限",
    "利润率",
    "毛利",
    "profit margin",
    "gross margin",
    "成本边界",
    "cost boundary",
    "compliance",
    "policy violation",
    "合规",
    "违规",
    "不可逆",
    "irreversible",
    "下架",
    "大幅降价",
    "executionlock.selectedoperatingroute",
    "executionlock.selectedactionfamily",
    "executionlock.primaryproblemnode",
    "executionlock.primaryaction",
    "executionlock.primaryowner",
    "executionlock.primaryexecutiontarget.targetid",
    "executionlock.decisivefacts",
)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 800) -> str:
    if isinstance(value, dict):
        value = (
            value.get("summary")
            or value.get("text")
            or value.get("action")
            or value.get("id")
        )
    return " ".join(str(value or "").split())[:limit]


def _canonical_code(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _text(value, 160).lower()).strip("_")


def _target(value: Any, *, product_id: Any = None) -> Dict[str, Any]:
    source = _dict(value)
    if not source and isinstance(value, str) and value.strip():
        source = {"targetType": value.strip()}
    result = {
        "targetType": _text(
            source.get("targetType")
            or source.get("type")
            or source.get("objectType")
            or source.get("target"),
            160,
        ),
        "targetId": _text(
            source.get("targetId")
            or source.get("id")
            or source.get("objectId")
            or source.get("selector")
            or product_id,
            220,
        ),
        "owner": _text(
            source.get("owner")
            or source.get("directOwner")
            or source.get("responsibleRole"),
            120,
        ),
        "scope": _text(
            source.get("scope") or source.get("targetScope"),
            220,
        ),
    }
    return {
        key: item
        for key, item in result.items()
        if item not in (None, "", [], {})
    }


def _decision_ir(source: Dict[str, Any]) -> Dict[str, Any]:
    judgment = _dict(source.get("agent1OperatingJudgment"))
    return _dict(source.get("agent1DecisionIR")) or _dict(
        judgment.get("agent1DecisionIR")
    )


def _decision_type(
    source: Dict[str, Any],
    judgment: Dict[str, Any],
    ir: Dict[str, Any],
) -> str:
    return _first_text(
        40,
        source.get("decisionType"),
        judgment.get("decisionType"),
        ir.get("decisionType"),
    ).lower()


def _first_text(limit: int, *values: Any) -> str:
    for value in values:
        current = _text(value, limit)
        if current:
            return current
    return ""


def _selected_route(
    source: Dict[str, Any],
    judgment: Dict[str, Any],
    ir: Dict[str, Any],
    existing: Dict[str, Any],
    route_lock: Dict[str, Any],
) -> str:
    return _first_text(
        120,
        existing.get("selectedOperatingRoute"),
        source.get("selectedOperatingRoute"),
        source.get("route"),
        source.get("routeId"),
        judgment.get("selectedOperatingRoute"),
        judgment.get("route"),
        ir.get("selectedOperatingRoute"),
        route_lock.get("selectedOperatingRoute"),
    )


def _selected_family(
    source: Dict[str, Any],
    judgment: Dict[str, Any],
    ir: Dict[str, Any],
    existing: Dict[str, Any],
    family_lock: Dict[str, Any],
    matrix: Dict[str, Any],
) -> str:
    return _first_text(
        120,
        existing.get("selectedActionFamily"),
        source.get("selectedActionFamily"),
        (source.get("lockedActionFamily") or source.get("selectedActionFamilyHint")),
        source.get("actionFamily"),
        judgment.get("selectedActionFamily"),
        (judgment.get("lockedActionFamily") or judgment.get("selectedActionFamilyHint")),
        ir.get("selectedActionFamily"),
        family_lock.get("selectedActionFamily"),
        matrix.get("selectedActionFamily"),
    )


def _flatten_missing_evidence(values: List[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        if isinstance(value, list):
            result.extend(_flatten_missing_evidence(value))
            continue
        if isinstance(value, dict):
            current = _text(
                value.get("reason")
                or value.get("summary")
                or value.get("text")
                or value,
                500,
            )
        else:
            current = _text(value, 500)
        if current:
            result.append(current)
    return list(dict.fromkeys(result))[:24]


def _hard_evidence_blockers(values: List[Any]) -> List[str]:
    blockers: List[str] = []
    for value in _flatten_missing_evidence(values):
        canonical = _canonical_code(value)
        lowered = value.lower()
        if any(
            marker in lowered
            or (
                bool(_canonical_code(marker))
                and _canonical_code(marker) in canonical
            )
            for marker in _HARD_EVIDENCE_MARKERS
        ):
            blockers.append(value)
    return list(dict.fromkeys(blockers))


def _inferred_target_type(*, family: str, route: str) -> str:
    for value in (family, route):
        code = _canonical_code(value)
        if code in _REVERSIBLE_TARGET_TYPES:
            return _REVERSIBLE_TARGET_TYPES[code]
    return ""


def _reversible_test_candidate(*, family: str, route: str) -> bool:
    return bool(_inferred_target_type(family=family, route=route))


def _complete_handoff(lock: Dict[str, Any]) -> bool:
    target = _dict(lock.get("primaryExecutionTarget"))
    required = (
        lock.get("selectedOperatingRoute"),
        lock.get("selectedActionFamily"),
        lock.get("primaryProblemNode"),
        lock.get("primaryAction"),
        lock.get("primaryOwner"),
        target.get("targetType"),
        target.get("targetId"),
    )
    return all(_text(value, 800) for value in required) and bool(
        _arr(lock.get("decisiveFacts"))
    )


def _apply_reversible_evidence_policy(
    lock: Dict[str, Any],
) -> Dict[str, Any]:
    result = dict(lock)
    family = _text(result.get("selectedActionFamily"), 120)
    route = _text(result.get("selectedOperatingRoute"), 120)
    missing = _arr(result.get("missingEvidence"))
    blockers = _hard_evidence_blockers(missing)
    if (
        str(result.get("decisionType") or "") == "act"
        and str(result.get("evidenceStatus") or "") != EVIDENCE_CONFLICT
        and _reversible_test_candidate(family=family, route=route)
        and _complete_handoff(result)
        and not blockers
    ):
        result.update(
            evidenceStatus=EVIDENCE_SUFFICIENT,
            evidenceBasis="cross_validated_reversible_test",
            riskClass="reversible_test",
            reversibleTest=True,
            reviewRequired=True,
            rollbackRequired=True,
            advisoryMissingEvidence=_flatten_missing_evidence(missing),
            hardEvidenceBlockers=[],
            evidencePolicyVersion=EXECUTION_LOCK_HOTFIX_VERSION,
        )
    else:
        result.update(
            hardEvidenceBlockers=blockers,
            evidencePolicyVersion=EXECUTION_LOCK_HOTFIX_VERSION,
        )
    return result


def execution_lock_from(source: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one execution lock without inventing a business action."""
    judgment = _dict(source.get("agent1OperatingJudgment"))
    ir = _decision_ir(source)
    matrix = _dict(source.get("matrixDispatch"))
    existing = (
        _dict(source.get("executionLock"))
        or _dict(judgment.get("executionLock"))
        or _dict(ir.get("executionLock"))
        or _dict(matrix.get("executionLock"))
    )
    product_id = source.get("productId") or _dict(
        source.get("productIdentity")
    ).get("productId")
    family_lock = _dict(judgment.get("actionFamilyLock"))
    route_lock = _dict(judgment.get("routeLock"))
    route = _selected_route(
        source, judgment, ir, existing, route_lock
    )
    family = _selected_family(
        source, judgment, ir, existing, family_lock, matrix
    )
    decision_type = _decision_type(source, judgment, ir)
    missing_evidence = (
        _arr(existing.get("missingEvidence"))
        or _arr(source.get("missingEvidence"))
        or _arr(judgment.get("missingEvidence"))
        or _arr(ir.get("missingEvidence"))
    )
    evidence_status = _first_text(
        40,
        existing.get("evidenceStatus"),
        source.get("evidenceStatus"),
        judgment.get("evidenceStatus"),
        ir.get("evidenceStatus"),
    ).lower()
    if evidence_status not in EVIDENCE_STATUSES:
        evidence_status = (
            EVIDENCE_INSUFFICIENT if missing_evidence else ""
        )

    target = _target(
        existing.get("primaryExecutionTarget")
        or source.get("primaryExecutionTarget")
        or judgment.get("primaryExecutionTarget")
        or ir.get("primaryExecutionTarget")
        or matrix.get("selectedExecutionTarget"),
        product_id=product_id,
    )
    if not _text(target.get("targetType"), 160):
        inferred = _inferred_target_type(
            family=family, route=route
        )
        if inferred:
            target["targetType"] = inferred
            target["targetTypeSource"] = (
                "canonical_action_family_mapping"
            )
    if not _text(target.get("targetId"), 220) and product_id:
        target["targetId"] = _text(product_id, 220)
    owner = _first_text(
        120,
        existing.get("primaryOwner"),
        source.get("primaryOwner"),
        judgment.get("primaryOwner"),
        ir.get("primaryOwner"),
        matrix.get("selectedOwner"),
        target.get("owner"),
    )
    if owner and not target.get("owner"):
        target["owner"] = owner

    result: Dict[str, Any] = {
        "version": EXECUTION_LOCK_VERSION,
        "hotfixVersion": EXECUTION_LOCK_HOTFIX_VERSION,
        "decisionType": decision_type,
        "locked": bool(existing.get("locked") is True),
        "evidenceStatus": evidence_status,
        "selectedOperatingRoute": route,
        "selectedActionFamily": family,
        "primaryProblemNode": _first_text(
            500,
            existing.get("primaryProblemNode"),
            source.get("primaryProblemNode"),
            judgment.get("primaryProblemNode"),
            ir.get("primaryProblemNode"),
        ),
        "primaryAction": _first_text(
            500,
            existing.get("primaryAction"),
            source.get("primaryAction"),
            judgment.get("primaryAction"),
            ir.get("primaryAction"),
            matrix.get("selectedPrimaryAction"),
        ),
        "primaryExecutionTarget": target,
        "primaryOwner": owner,
        "decisiveFacts": (
            _arr(existing.get("decisiveFacts"))
            or _arr(source.get("decisiveFacts"))
            or _arr(judgment.get("decisiveFacts"))
            or _arr(ir.get("decisiveFacts"))
        )[:12],
        "supportingCoordination": (
            _arr(existing.get("supportingCoordination"))
            or _arr(source.get("supportingCoordination"))
            or _arr(judgment.get("supportingCoordination"))
            or _arr(ir.get("supportingCoordination"))
        )[:6],
        "forbiddenActionDomains": (
            _arr(existing.get("forbiddenActionDomains"))
            or _arr(source.get("forbiddenActionDomains"))
            or _arr(judgment.get("forbiddenActionDomains"))
            or _arr(ir.get("forbiddenActionDomains"))
        )[:12],
        "missingEvidence": missing_evidence[:12],
        "lockReason": _first_text(
            800,
            existing.get("lockReason"),
            judgment.get("decisionSummary"),
            ir.get("decisionSummary"),
            source.get("finding"),
        ),
        "singlePrimaryAction": True,
        "singlePrimaryExecutionTarget": True,
        "forbiddenOverride": True,
    }
    result = _apply_reversible_evidence_policy(result)
    result["locked"] = (
        decision_type == "act"
        and not bool(missing_execution_lock(result))
    )
    return {
        key: value
        for key, value in result.items()
        if value not in (None, "", [], {})
        or key
        in {
            "locked",
            "singlePrimaryAction",
            "singlePrimaryExecutionTarget",
            "forbiddenOverride",
        }
    }


def missing_execution_lock(
    lock_or_source: Dict[str, Any],
) -> List[str]:
    lock = (
        lock_or_source
        if "primaryAction" in lock_or_source
        or "evidenceStatus" in lock_or_source
        else execution_lock_from(lock_or_source)
    )
    missing: List[str] = []
    if str(lock.get("evidenceStatus") or "") != EVIDENCE_SUFFICIENT:
        missing.append("executionLock.evidenceStatus_sufficient")
    for key in (
        "selectedOperatingRoute",
        "selectedActionFamily",
        "primaryProblemNode",
        "primaryAction",
        "primaryOwner",
    ):
        if not _text(lock.get(key), 800):
            missing.append(f"executionLock.{key}")
    target = _dict(lock.get("primaryExecutionTarget"))
    if not _text(target.get("targetType"), 160):
        missing.append(
            "executionLock.primaryExecutionTarget.targetType"
        )
    if not _text(target.get("targetId"), 220):
        missing.append(
            "executionLock.primaryExecutionTarget.targetId"
        )
    if not _arr(lock.get("decisiveFacts")):
        missing.append("executionLock.decisiveFacts")
    return list(dict.fromkeys(missing))


def execution_handoff(source: Dict[str, Any]) -> Dict[str, Any]:
    lock = execution_lock_from(source)
    return {
        "version": EXECUTION_LOCK_VERSION,
        "hotfixVersion": EXECUTION_LOCK_HOTFIX_VERSION,
        "executionLock": lock,
        "lockedActionFamily": lock.get("selectedActionFamily"),
        "primaryProblemNode": lock.get("primaryProblemNode"),
        "primaryAction": lock.get("primaryAction"),
        "primaryExecutionTarget": lock.get(
            "primaryExecutionTarget"
        ),
        "primaryOwner": lock.get("primaryOwner"),
        "decisiveFacts": lock.get("decisiveFacts") or [],
        "supportingCoordination": (
            lock.get("supportingCoordination") or []
        ),
        "forbiddenActionDomains": (
            lock.get("forbiddenActionDomains") or []
        ),
        "permissionBoundary": _dict(
            source.get("permissionBoundary")
        )
        or _dict(
            _dict(source.get("actionParameterPack")).get(
                "permissionBounds"
            )
        ),
        "parameterBoundary": _dict(
            source.get("parameterBoundary")
        )
        or _dict(
            _dict(source.get("actionParameterPack")).get(
                "parameterBounds"
            )
        ),
        "inputRule": (
            "one evidence-backed execution lock; diagnostic "
            "hypotheses are audit-only"
        ),
    }


__all__ = [
    "EXECUTION_LOCK_VERSION",
    "EXECUTION_LOCK_HOTFIX_VERSION",
    "EVIDENCE_SUFFICIENT",
    "EVIDENCE_INSUFFICIENT",
    "EVIDENCE_CONFLICT",
    "execution_lock_from",
    "missing_execution_lock",
    "execution_handoff",
]
