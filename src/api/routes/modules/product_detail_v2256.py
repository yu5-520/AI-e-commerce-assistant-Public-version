"""V22.5.6 composite product-detail read route.

The list read model remains lightweight. A product detail request combines the rich
V16.4 product projection with the V21.7 recent-five/history trend projection so the
page does not lose metric groups or traffic-source facts when the compact cache is used.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request

from src.services.account_service import user_id_from_headers
from src.services.product_trend_read_model_v217_service import read_product_trend

router = APIRouter()
PRODUCT_DETAIL_COMPOSITE_VERSION = "22.5.6"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _aliases(item: Dict[str, Any]) -> set[str]:
    position = item.get("productPosition") if isinstance(item.get("productPosition"), dict) else {}
    values = {
        item.get("id"),
        item.get("objectId"),
        item.get("archiveId"),
        item.get("productId"),
        item.get("rawProductId"),
        item.get("skuId"),
        position.get("productId"),
        position.get("skuId"),
    }
    return {_text(value) for value in values if _text(value)}


@router.get("/product-detail-v2256/{product_id}")
def product_detail_v2256(
    request: Request,
    product_id: str,
    store_id: str | None = Query(default=None, alias="storeId"),
) -> Dict[str, Any]:
    # Delayed import avoids creating a second product router authority.
    from src.api.routes.modules.product import product_items

    user_id = user_id_from_headers(request.headers)
    wanted = _text(product_id)
    candidates = product_items(user_id, store_id=store_id)
    matched = next(
        (
            item
            for item in candidates
            if wanted in _aliases(item)
            and (not store_id or _text(item.get("storeId")) == _text(store_id))
        ),
        None,
    )
    if not matched:
        raise HTTPException(status_code=404, detail="product not found in composite product detail projection")

    raw_product_id = _text(matched.get("productId") or matched.get("rawProductId") or wanted)
    resolved_store_id = _text(matched.get("storeId") or store_id) or None
    trend = read_product_trend(raw_product_id, store_id=resolved_store_id)
    latest_snapshot = (trend.get("recentSnapshots") or [])[-1] if trend.get("recentSnapshots") else None

    return {
        "version": PRODUCT_DETAIL_COMPOSITE_VERSION,
        "ready": True,
        "item": matched,
        "trend": trend,
        "latestSnapshot": latest_snapshot,
        "dataCompleteness": trend.get("observationSummary") or {},
        "sourceLineage": {
            "productProjection": "module_projection_service.projected_products",
            "productArchive": "modules.product.product_items",
            "recentTrend": "product_trend_read_model_v217_service",
            "compactListReadModelUsedAsDetail": False,
        },
        "readRule": "Product detail combines rich current facts, traffic-source child facts, recent-five direct comparisons and older-history algorithms; list cache never replaces detail facts.",
    }
