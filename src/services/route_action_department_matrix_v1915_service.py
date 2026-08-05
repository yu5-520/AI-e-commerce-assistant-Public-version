"""V22 strict action-family and department dispatch.

Agent1 is the sole owner of the route and primary action family. Downstream code
validates the immutable lock and enriches facts only. Missing or invalid locks
fail closed; no default family or generated route exists.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from src.runtime_version import VERSION

MATRIX_DISPATCH_VERSION = VERSION
DEFAULT_DEPARTMENT_SCOPE = "operator_growth"

ALLOWED_ACTION_FAMILIES = {
    "title_image_test",
    "roas_scale",
    "roas_guard",
    "platform_activity",
    "activity_apply",
    "conversion_repair",
    "service_repair",
    "similar_product_test",
}

ACTION_FAMILY_LABELS = {
    "title_image_test": "标题主图点击率修复测试",
    "roas_scale": "ROAS放量验证",
    "roas_guard": "ROAS止损校准",
    "platform_activity": "平台活动增长验证",
    "activity_apply": "平台活动增长验证",
    "conversion_repair": "转化承接修复",
    "service_repair": "售后体验修复",
    "similar_product_test": "相似商品对照测试",
}

ACTION_DATA_REQUIREMENTS = {
    "title_image_test": ["productIdentity", "creativeContext", "clickRate", "conversionRate"],
    "roas_scale": ["adSpend", "ROI/ROAS", "grossMarginRate", "paymentAmount"],
    "roas_guard": ["adSpend", "ROI/ROAS", "grossMarginRate", "conversionRate"],
    "platform_activity": ["price", "cost", "grossProfitAmount", "organicVisitors"],
    "activity_apply": ["price", "cost", "grossProfitAmount", "organicVisitors"],
    "conversion_repair": ["conversionRate", "clickRate", "paymentAmount", "refundRate"],
    "service_repair": ["refundRate", "afterSaleRate", "rating"],
    "similar_product_test": ["productIdentity", "verticalCategory", "priceBand"],
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _agent1(package: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(package.get("agent1OperatingJudgment"))


def _identity(package: Dict[str, Any]) -> Dict[str, Any]:
    product = _dict(package.get("productIdentity"))
    return {
        "productId": package.get("productId") or product.get("productId"),
        "storeId": package.get("storeId") or product.get("storeId"),
        "title": package.get("productTitle")
        or package.get("title")
        or product.get("productTitle")
        or product.get("title"),
        "platform": product.get("platform"),
        "verticalCategory": product.get("verticalCategory"),
    }


def _locked_family(package: Dict[str, Any]) -> tuple[str, str]:
    agent1 = _agent1(package)
    lock = _dict(agent1.get("actionFamilyLock"))
    decision_type = str(
        _dict(package.get("agent1DecisionIR")).get("decisionType")
        or agent1.get("decisionType")
        or package.get("decisionType")
        or ""
    ).strip().lower()
    if decision_type == "observe" or lock.get("observationOnly") is True:
        raise ValueError("observe_result_has_no_action_dispatch")
    if lock.get("locked") is not True or lock.get("forbiddenOverride") is not True:
        raise ValueError("agent1_action_family_lock_missing")
    family = str(lock.get("selectedActionFamily") or "").strip()
    if family not in ALLOWED_ACTION_FAMILIES:
        raise ValueError("agent1_action_family_lock_invalid")
    return family, "agent1.actionFamilyLock"


def _locked_route(package: Dict[str, Any]) -> str:
    agent1 = _agent1(package)
    lock = _dict(agent1.get("routeLock"))
    if lock.get("locked") is not True:
        raise ValueError("agent1_route_lock_missing")
    route = str(lock.get("selectedOperatingRoute") or "").strip()
    if not route or route == "observe":
        raise ValueError("agent1_route_lock_invalid")
    return route


def _task_title(product_title: str, family: str) -> str:
    label = ACTION_FAMILY_LABELS.get(family, family)
    name = re.sub(r"\s+", " ", str(product_title or "该商品")).strip()
    return f"【{name}】{label}"


def selected_family(package: Dict[str, Any]) -> str:
    return _locked_family(package)[0]


def attach_matrix_dispatch(package: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(package)
    family, lock_source = _locked_family(item)
    route = _locked_route(item)
    identity = _identity(item)
    dispatch = {
        "version": MATRIX_DISPATCH_VERSION,
        "routeId": route,
        "selectedActionFamily": family,
        "departmentScope": DEFAULT_DEPARTMENT_SCOPE,
        "actionDataRequirements": ACTION_DATA_REQUIREMENTS.get(family, []),
        "taskTitlePreview": _task_title(str(identity.get("title") or "该商品"), family),
        "lockedByAgent1": True,
        "agent1LockSource": lock_source,
        "agent1LockMissing": False,
        "routeActionConsistency": "passed",
        "fallbackAllowed": False,
        "compilerRole": "dispatch_validation_only",
        "rule": (
            "V22 accepts only the canonical Agent1 lock. Downstream code never "
            "guesses, repairs or defaults an action family."
        ),
    }
    item.update(
        actionFamily=family,
        selectedActionFamily=family,
        route=route,
        selectedOperatingRoute=route,
        matrixDispatch=dispatch,
        matrixDispatchVersion=MATRIX_DISPATCH_VERSION,
    )
    item.pop("selectedActionFamilyHint", None)
    return item


__all__ = [
    "MATRIX_DISPATCH_VERSION",
    "ALLOWED_ACTION_FAMILIES",
    "ACTION_DATA_REQUIREMENTS",
    "attach_matrix_dispatch",
    "selected_family",
]
