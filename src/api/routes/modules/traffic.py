"""Traffic module routes backed by V14.8 frontend product read model."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from src.services.account_service import user_id_from_headers
from src.services.frontend_read_model_service import FRONTEND_READ_MODEL_VERSION, read_product_views

router = APIRouter()
TRAFFIC_ROUTE_VERSION = "14.8.0"


def _items() -> List[Dict[str, Any]]:
    return read_product_views(limit=500).get("items") or []


def _traffic_card(item: Dict[str, Any]) -> Dict[str, Any]:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    product_id = item.get("productId") or item.get("bundleId")
    strength = item.get("signalStrength") or "normal"
    level = "danger" if strength == "high" else "warning" if strength == "medium" else "good"
    return {"id": product_id, "productId": product_id, "storeId": item.get("storeId"), "title": item.get("title") or product_id, "platform": item.get("platform"), "store": item.get("storeName"), "channel": "商品全量包", "source": "frontend_product_view", "roi": metrics.get("roas") or metrics.get("roi"), "inventory": metrics.get("inventory"), "clickRate": metrics.get("clickRate"), "conversionRate": metrics.get("conversionRate"), "status": item.get("primarySignalType") or "读模型观察", "statusLevel": level, "backflow": "RAG波动边界观察", "nextStep": "前端只读缓存；任务由Agent软路由生成。", "readModelVersion": FRONTEND_READ_MODEL_VERSION}


def traffic_task_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    store_id = item.get("storeId")
    return {"entityType": "商品", "entityId": item.get("productId") or item.get("id"), "riskDomain": "流量", "actionType": "观察" if item.get("statusLevel") == "good" else "复查", "sourceType": "流量模块", "taskLayer": "operator_execution", "visibleRoleIds": ["manager", "operator", "finance"], "sourceModule": "流量模块", "source": "frontend_product_view", "sourceRoute": "business-traffic", "productId": item.get("productId") or item.get("id"), "storeIds": [store_id] if store_id else [], "visibleStoreIds": [store_id] if store_id else [], "imageLabel": "流", "productShort": (item.get("title") or item.get("productId") or item.get("id") or "商品")[:8], "productTitle": item.get("title") or item.get("productId"), "title": item.get("title") or item.get("productId"), "platform": item.get("platform") or "导入数据", "store": item.get("store") or "未绑定店铺", "priority": "高" if item.get("statusLevel") == "danger" else "中" if item.get("statusLevel") == "warning" else "低", "priorityLevel": item.get("statusLevel") or "warning", "deadline": "今天 18:00 前" if item.get("statusLevel") == "danger" else "明天前", "taskType": item.get("backflow") or "流量承接复查", "taskSignal": item.get("status") or "读模型触发", "task": item.get("nextStep") or "根据读模型复核流量承接。", "reason": f"成交 {item.get('roi', '—')}，库存 {item.get('inventory', '—')}。", "judgmentTags": [f"成交 {item.get('roi', '—')}", f"库存 {item.get('inventory', '—')}", item.get("status", "读模型")]} 


@router.get("/traffic")
def traffic(request: Request) -> list[Dict[str, Any]]:
    user_id_from_headers(request.headers)
    return [_traffic_card(item) for item in _items()]


@router.post("/traffic/{traffic_id}/tasks")
def traffic_task(request: Request, traffic_id: str) -> Dict[str, Any]:
    user_id_from_headers(request.headers)
    item = next((candidate for candidate in [_traffic_card(row) for row in _items()] if traffic_id in {candidate.get("id"), candidate.get("productId")}), None)
    if not item:
        raise HTTPException(status_code=404, detail="traffic item not found in read model")
    return {"version": TRAFFIC_ROUTE_VERSION, "mode": "candidate_only_read_model", "candidate": traffic_task_payload(item), "createdTaskCount": 0, "rule": "V14.8 traffic route returns candidate only; visible tasks must come from fullProductBundle Agent soft routing."}
