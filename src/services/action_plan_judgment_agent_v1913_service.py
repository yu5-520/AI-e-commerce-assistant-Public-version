"""V20.28 sealed legacy Agent2 module name.

All historical imports delegate to the single current Agent2 core. The former
V20.16 package-table reader, budget ledger protocol, prompt builder and result
persistence are removed from runtime.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.services.agent2_action_plan_core_v20_service import (
    AGENT2_ACTION_PLAN_CORE_VERSION,
    _build_messages as _current_build_messages,
    _compact_package as _current_compact_package,
    _normalize_plan as _current_normalize_plan,
    attach_agent2_action_plans,
    call_agent2_action_plans,
)
from src.services.route_action_department_matrix_v1915_service import (
    attach_matrix_dispatch,
    selected_family,
)

ACTION_PLAN_AGENT_VERSION = "20.28"
ACTION_PLAN_AGENT_MODE = "v20_28_sealed_legacy_name_current_core_only"
ALLOWED_ACTION_FAMILIES = {
    "title_image_test",
    "roas_scale",
    "roas_guard",
    "platform_activity",
    "conversion_repair",
    "similar_product_test",
}
STRICT_TEMPLATE_MARKERS: List[str] = []
TEMPLATE_MARKERS = STRICT_TEMPLATE_MARKERS


def _locked_family(package: Dict[str, Any]) -> str:
    return selected_family(attach_matrix_dispatch(package))


def _compact_package(package: Dict[str, Any]) -> Dict[str, Any]:
    return _current_compact_package(package)


def _build_messages(
    data_version: str | None,
    packages: List[Dict[str, Any]],
):
    return _current_build_messages(data_version, packages)


def _normalize_plan(
    raw: Dict[str, Any],
    package_by_id: Dict[str, Dict[str, Any]] | Dict[str, Any],
) -> Dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if isinstance(package_by_id, dict) and any(
        isinstance(value, dict) for value in package_by_id.values()
    ):
        package = package_by_id.get(str(raw.get("packageId") or "")) or {}
    else:
        package = package_by_id if isinstance(package_by_id, dict) else {}
    if not package:
        return None
    return _current_normalize_plan(raw, package)


def _call_agent2(
    packages: List[Dict[str, Any]],
    data_version: str | None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    return call_agent2_action_plans(packages, data_version)


def _attach_plans(
    packages: List[Dict[str, Any]],
    plans: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return attach_agent2_action_plans(packages, plans)


def _load_packages(*_: Any, **__: Any) -> List[Dict[str, Any]]:
    return []


def _save_packages(*_: Any, **__: Any) -> None:
    return None


def action_plan_judgment_agent_station_v1913(
    data_version: str | None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from src.services.action_plan_judgment_agent_v1915_service import (
        action_plan_judgment_agent_station_v1915,
    )

    result = action_plan_judgment_agent_station_v1915(data_version, **kwargs)
    result["legacyModuleName"] = "action_plan_judgment_agent_v1913_service"
    result["legacyRuntimeUsed"] = False
    result["agent2ActionPlanCoreVersion"] = AGENT2_ACTION_PLAN_CORE_VERSION
    return result
