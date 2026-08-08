"""V22.5.6 composite product-detail read route.

The list read model remains lightweight. A product detail request combines the rich
current product projection with recent-five/history trend math. After canonical product
migration, trend observations are read from canonical snapshot sets so multiple report
versions remain visible instead of falling through the retired legacy snapshot table.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request

from src.services.canonical_product_trend_v2_service import read_canonical_product_trend
from src.services.competition_operator_context_service import user_id_from_headers

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
    # objectId is the operating-unit identity (platform/store/product/sku). Prefer it
    # for history matching so the same global product in another store cannot bleed in.
    trend_lookup_id = _text(matched.get("objectId") or matched.get("id") or raw_product_id)
    trend = read_canonical_product_trend(
        trend_lookup_id,
        store_id=resolved_store_id,
        user_id=user_id,
    )
    latest_snapshot = (trend.get("recentSnapshots") or [])[-1] if trend.get("recentSnapshots") else None

    return {
        "version": PRODUCT_DETAIL_COMPOSITE_VERSION,
        "ready": True,
        "item": matched,
        "trend": trend,
        "latestSnapshot": latest_snapshot,
        "dataCompleteness": trend.get("observationSummary") or {},
        "sourceLineage": {
            "productProjection": "canonical_product_snapshot_service.list_product_details",
            "productArchive": "modules.product.product_items",
            "recentTrend": "canonical_product_trend_v2_service",
            "trendAlgorithm": "product_trend_read_model_v217_service",
            "snapshotAuthority": "canonical_product_snapshot_sets_v1",
            "compactListReadModelUsedAsDetail": False,
        },
        "readRule": "Product detail combines canonical current facts with the same operating unit across historical dataVersions; latest five compare directly and older history remains algorithm input.",
    }
