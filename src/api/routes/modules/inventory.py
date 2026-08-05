"""Inventory center routes backed by V14.8 frontend product read model."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from src.services.account_service import user_id_from_headers
from src.services.frontend_read_model_service import FRONTEND_READ_MODEL_VERSION, read_product_views

router = APIRouter()
INVENTORY_ROUTE_VERSION = "14.8.0"


def _products() -> List[Dict[str, Any]]:
    return read_product_views(limit=500).get("items") or []


def inventory_status(item: Dict[str, Any]) -> str:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    inventory = metrics.get("inventory")
    try:
        value = float(str(inventory or 0).replace(",", ""))
    except Exception:
        value = 0
    if value <= 0 and inventory not in {None, "", "—", "未识别"}:
        return "库存告急"
    if item.get("signalStrength") in {"high", "medium"} and item.get("metricCode") == "inventory":
        return "库存关注"
    return "正常"


def inventory_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    status = inventory_status(item)
    high_risk = status == "库存告急"
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    return {"entityType": "库存", "entityId": item.get("productId") or item.get("bundleId"), "riskDomain": "库存", "actionType": "观察" if status == "正常" else "复查", "sourceModule": "库存中心", "source": "frontend_product_view", "sourceRoute": "inventory-center", "productId": item.get("productId"), "storeIds": [item.get("storeId")] if item.get("storeId") else [], "visibleStoreIds": [item.get("storeId")] if item.get("storeId") else [], "imageLabel": "库", "productShort": item.get("title") or item.get("productId"), "productTitle": item.get("title") or item.get("productId"), "title": f"库存复查｜{item.get('title') or item.get('productId')}", "platform": item.get("platform"), "store": item.get("storeName"), "priority": "高" if high_risk else "中" if status != "正常" else "低", "priorityLevel": "danger" if high_risk else "warning" if status != "正常" else "good", "deadline": "今天内" if high_risk else "明天前", "taskType": "库存补货复核" if high_risk else "库存承接检查", "taskSignal": "读模型库存信号", "task": "确认可售库存、安全库存和补货周期，再决定活动节奏。", "reason": "库存中心读取前端商品读模型，不触发商品投影或任务生成。", "judgmentTags": [status, f"库存 {metrics.get('inventory', '—')}", f"毛利 {metrics.get('grossMargin', '—')}", item.get("storeName")]}


def inventory_card(item: Dict[str, Any]) -> Dict[str, Any]:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    product_id = item.get("productId") or item.get("bundleId")
    return {"id": product_id, "storeId": item.get("storeId"), "productId": product_id, "title": item.get("title") or product_id, "shortName": item.get("title") or product_id, "imageLabel": "库", "platform": item.get("platform"), "store": item.get("storeName"), "inventory": metrics.get("inventory"), "inventoryStatus": inventory_status(item), "inventoryLevel": "danger" if inventory_status(item) == "库存告急" else "warning" if inventory_status(item) == "库存关注" else "good", "price": metrics.get("price"), "grossMargin": metrics.get("grossMargin"), "suggestion": "读模型库存观察", "sourceRoute": "inventory-center", "readModelVersion": FRONTEND_READ_MODEL_VERSION}


@router.get("/inventory")
def inventory(request: Request) -> Dict[str, Any]:
    user_id_from_headers(request.headers)
    items = [inventory_card(item) for item in _products()]
    danger = len([item for item in items if item.get("inventoryLevel") == "danger"])
    warning = len([item for item in items if item.get("inventoryLevel") == "warning"])
    return {"module": "库存中心", "version": INVENTORY_ROUTE_VERSION, "readMode": "frontend_product_view_only", "metrics": {"skuCount": len(items), "danger": danger, "warning": warning, "normal": max(len(items) - danger - warning, 0)}, "items": items, "rules": ["V14.8：库存页GET只读frontend_product_view", "任务生成由fullProductBundle→Agent软路由→SOP任务池完成"]}


@router.post("/inventory/{product_id}/tasks")
def inventory_task(product_id: str) -> Dict[str, Any]:
    item = next((product for product in _products() if product_id in {product.get("productId"), product.get("bundleId"), product.get("signalId")}), None)
    if not item:
        raise HTTPException(status_code=404, detail="inventory product not found in read model")
    return {"version": INVENTORY_ROUTE_VERSION, "mode": "candidate_only_read_model", "candidate": inventory_payload(item), "createdTaskCount": 0, "rule": "V14.8：库存模块按钮只返回候选，不绕过Agent和SOP任务快照直接写任务。"}
