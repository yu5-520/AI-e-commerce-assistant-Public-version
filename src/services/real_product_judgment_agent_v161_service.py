"""Legacy product judgment route removed in V19.7.

Import-safe marker only. Current station routing uses
real_product_judgment_agent_v197_service -> llm_gateway_v196_service.
"""

from __future__ import annotations

from typing import Any, Dict

REAL_PRODUCT_AGENT_VERSION = "LEGACY_REMOVED_IN_19.7"
MAX_PRODUCTS_PER_CALL = 0
MAX_PRODUCT_AGENT_CALLS_PER_RUN = 0


def _strict_product_id(bundle: Dict[str, Any]) -> str:
    profile = bundle.get("profileLayer") if isinstance(bundle.get("profileLayer"), dict) else {}
    return str(bundle.get("productId") or bundle.get("entityId") or profile.get("productId") or "").strip()


def legacy_route_removed(*_: Any, **__: Any) -> None:
    raise RuntimeError("LEGACY_PRODUCT_JUDGMENT_ROUTE_REMOVED_IN_V19_7")
