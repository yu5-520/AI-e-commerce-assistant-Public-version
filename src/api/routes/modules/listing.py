"""Listing module routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Request

from src.api.routes.modules.common import find_or_404
from src.services.account_service import current_user, user_id_from_headers, visible_store_ids_for_user
from src.services.module_data_service import LISTINGS
from src.services.module_task_service import visible_candidates

router = APIRouter()
LISTING_ROUTE_VERSION = "14.1.0"


def scoped_items(items: List[Dict[str, Any]], request: Request) -> List[Dict[str, Any]]:
    user_id = user_id_from_headers(request.headers)
    user = current_user(user_id)
    if user.get("roleId") in {"owner", "manager", "finance"}:
        return items
    allowed = set(visible_store_ids_for_user(user_id))
    return [item for item in items if item.get("storeId") in allowed]


def listing_task_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    return {"entityType": "竞品机会" if item["mode"] == "competitor" else "商品", "entityId": item["id"], "riskDomain": "上新", "actionType": "复盘" if "复盘" in item["testType"] else "测试", "sourceModule": "上新测试台", "source": "上新触发", "sourceRoute": "business-listing", "productId": item["id"], "storeIds": [item.get("storeId")] if item.get("storeId") else [], "visibleStoreIds": [item.get("storeId")] if item.get("storeId") else [], "imageLabel": item["imageLabel"], "productShort": item["sourceName"], "productTitle": item["title"], "title": item["title"], "platform": item["platform"], "store": item["store"], "priority": "高" if item["statusLevel"] == "danger" else "中", "priorityLevel": item["statusLevel"], "deadline": item["due"], "taskType": item["testType"], "taskSignal": "确认测试版本", "task": f"{item['testType']}：{item['testPlan']}", "reason": f"{item['risk']} {item['suggestion']}", "judgmentTags": [item["sourceLabel"], item["testType"], item["targetMetric"]]}


@router.get("/listing")
def listing(request: Request) -> List[Dict[str, Any]]:
    return visible_candidates(scoped_items(LISTINGS, request), listing_task_payload)


@router.post("/listing/{listing_id}/tasks")
def listing_task(listing_id: str) -> Dict[str, Any]:
    item = find_or_404(LISTINGS, listing_id, "listing")
    return {"version": LISTING_ROUTE_VERSION, "mode": "v14_1_snapshot_required", "candidate": listing_task_payload(item), "createdTaskCount": 0, "rule": "V14.1 listing route returns candidate only; visible pool entry must come from task_snapshot_station."}
