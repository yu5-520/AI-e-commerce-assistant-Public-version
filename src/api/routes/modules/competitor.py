"""Competitor module routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter

from src.api.routes.modules.common import find_or_404
from src.services.module_data_service import COMPETITORS
from src.services.module_task_service import visible_candidates

router = APIRouter()
COMPETITOR_ROUTE_VERSION = "14.1.0"


def competitor_task_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    return {"entityType": "竞品", "entityId": item["id"], "riskDomain": "风险" if item["status"] == "风险" else "上新", "actionType": "复查" if item["status"] == "风险" else "测试", "sourceModule": "竞品观察列表", "source": "竞品触发", "sourceRoute": "business-competitors", "productId": item["id"], "imageLabel": item["imageLabel"], "productShort": item["targetProduct"], "productTitle": item["title"], "title": item["title"], "platform": item["platform"], "store": item["store"], "priority": "高" if item["status"] == "风险" else "中", "priorityLevel": "danger" if item["status"] == "风险" else "warning", "deadline": "今天内" if item["status"] == "风险" else "明天前", "taskType": "竞品风险" if item["status"] == "风险" else "竞品机会", "taskSignal": item["opportunity"], "task": "复查竞品风险，不直接跟价" if item["status"] == "风险" else "生成对标测试任务", "reason": item["suggestion"], "judgmentTags": [item["pricePosition"], item["badReview"], item["status"]]}


@router.get("/competitor")
def competitor() -> List[Dict[str, Any]]:
    return visible_candidates(COMPETITORS, competitor_task_payload)


@router.post("/competitor/{competitor_id}/tasks")
def competitor_task(competitor_id: str) -> Dict[str, Any]:
    item = find_or_404(COMPETITORS, competitor_id, "competitor")
    return {"version": COMPETITOR_ROUTE_VERSION, "mode": "legacy_direct_task_write_disabled", "candidate": competitor_task_payload(item), "createdTaskCount": 0, "rule": "V14.1：竞品模块不再绕过任务快照站直接写入任务池。"}
