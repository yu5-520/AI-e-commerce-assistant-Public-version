"""After-sales center routes backed by V14.8 frontend product read model."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from src.services.account_service import user_id_from_headers
from src.services.frontend_read_model_service import FRONTEND_READ_MODEL_VERSION, read_product_views

router = APIRouter()
AFTERSALES_ROUTE_VERSION = "14.8.0"


def _products() -> List[Dict[str, Any]]:
    return read_product_views(limit=500).get("items") or []


def aftersales_status(item: Dict[str, Any]) -> str:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    refund = metrics.get("refundRate")
    try:
        value = float(str(refund or 0).replace("%", ""))
    except Exception:
        value = 0
    if item.get("signalStrength") == "high" and item.get("metricCode") == "refundRate":
        return "售后高危"
    if value >= 10:
        return "售后高危"
    if item.get("signalStrength") == "medium" and item.get("metricCode") == "refundRate":
        return "售后关注"
    return "正常"


def aftersales_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    status = aftersales_status(item)
    high_risk = status != "正常"
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    product_id = item.get("productId") or item.get("bundleId")
    return {"entityType": "售后", "entityId": product_id, "riskDomain": "售后", "actionType": "复查" if high_risk else "观察", "sourceModule": "售后中心", "source": "frontend_product_view", "sourceRoute": "aftersales-center", "productId": product_id, "storeIds": [item.get("storeId")] if item.get("storeId") else [], "visibleStoreIds": [item.get("storeId")] if item.get("storeId") else [], "imageLabel": "售", "productShort": item.get("title") or product_id, "productTitle": item.get("title") or product_id, "title": f"售后复查｜{item.get('title') or product_id}", "platform": item.get("platform"), "store": item.get("storeName"), "priority": "高" if high_risk else "低", "priorityLevel": "danger" if high_risk else "good", "deadline": "今天内" if high_risk else "本周内", "taskType": "售后归因复查" if high_risk else "售后观察", "taskSignal": "读模型售后信号", "task": "复查退款原因、详情页承诺和客服话术，售后归因完成前不继续放量。", "reason": "售后中心读取前端商品读模型，不触发商品投影或任务生成。", "judgmentTags": [status, f"退款率 {metrics.get('refundRate', '—')}", f"毛利 {metrics.get('grossMargin', '—')}", item.get("storeName")]}


def aftersales_card(item: Dict[str, Any]) -> Dict[str, Any]:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    product_id = item.get("productId") or item.get("bundleId")
    status = aftersales_status(item)
    return {"id": product_id, "storeId": item.get("storeId"), "productId": product_id, "title": item.get("title") or product_id, "shortName": item.get("title") or product_id, "imageLabel": "售", "platform": item.get("platform"), "store": item.get("storeName"), "afterSales": status, "afterSalesLevel": "danger" if status == "售后高危" else "warning" if status == "售后关注" else "good", "inventoryStatus": "读模型", "refundFocus": "退款率 / 评价 / 客服话术 / 承诺口径", "grossMargin": metrics.get("grossMargin"), "refundRate": metrics.get("refundRate"), "suggestion": "读模型售后观察", "sourceRoute": "aftersales-center", "readModelVersion": FRONTEND_READ_MODEL_VERSION}


@router.get("/aftersales")
def aftersales(request: Request) -> Dict[str, Any]:
    user_id_from_headers(request.headers)
    items = [aftersales_card(item) for item in _products()]
    abnormal = len([item for item in items if item.get("afterSalesLevel") != "good"])
    sensitive = len([item for item in items if item.get("afterSalesLevel") == "warning"])
    return {"module": "售后中心", "version": AFTERSALES_ROUTE_VERSION, "readMode": "frontend_product_view_only", "metrics": {"productCount": len(items), "abnormal": abnormal, "sensitive": sensitive, "normal": max(len(items) - abnormal, 0)}, "items": items, "rules": ["V14.8：售后页GET只读frontend_product_view", "任务生成由fullProductBundle→Agent软路由→SOP任务池完成"]}


@router.post("/aftersales/{product_id}/tasks")
def aftersales_task(product_id: str) -> Dict[str, Any]:
    item = next((product for product in _products() if product_id in {product.get("productId"), product.get("bundleId"), product.get("signalId")}), None)
    if not item:
        raise HTTPException(status_code=404, detail="aftersales product not found in read model")
    return {"version": AFTERSALES_ROUTE_VERSION, "mode": "candidate_only_read_model", "candidate": aftersales_payload(item), "createdTaskCount": 0, "rule": "V14.8：售后模块按钮只返回候选，不绕过Agent和SOP任务快照直接写任务。"}
