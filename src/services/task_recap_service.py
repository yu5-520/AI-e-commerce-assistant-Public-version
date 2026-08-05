"""V3.0.9 task recap candidate service.

Completed evidence-reviewed tasks should automatically become daily / weekly
retrospective candidates instead of relying on a manual copy-paste step.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from uuid import uuid4

RECAP_VERSION = "3.0.9"
RECAP_CANDIDATES: List[Dict[str, Any]] = []


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}".upper()


def _risk_level(task: Dict[str, Any]) -> str:
    return task.get("priority") or "中"


def _scope(task: Dict[str, Any]) -> str:
    store = task.get("store") or task.get("storeName") or "经营单元"
    platform = task.get("platform") or "全部平台"
    return f"{platform} · {store}"


def candidate_exists(task_id: str) -> Dict[str, Any] | None:
    return next((item for item in RECAP_CANDIDATES if item.get("taskId") == task_id), None)


def add_recap_candidate(task: Dict[str, Any], evidence: Dict[str, Any] | None = None, review: Dict[str, Any] | None = None, source: str = "task_evidence_review") -> Dict[str, Any]:
    existing = candidate_exists(task.get("id"))
    evidence = evidence or task.get("latestEvidenceRecord") or {}
    review = review or task.get("latestEvidenceReview") or {}
    candidate = {
        "id": existing.get("id") if existing else make_id("RECAP"),
        "version": RECAP_VERSION,
        "taskId": task.get("id"),
        "source": source,
        "recapTarget": task.get("recapTarget") or "日报",
        "title": task.get("title") or task.get("productTitle") or "经营任务复盘",
        "riskDomain": task.get("riskDomain") or "经营",
        "riskLevel": _risk_level(task),
        "scope": _scope(task),
        "storeIds": task.get("storeIds") or task.get("visibleStoreIds") or [],
        "operatorName": task.get("assigneeName") or evidence.get("submittedByName") or "未记录",
        "reviewerName": task.get("reviewerName") or review.get("reviewerName") or "店群总管",
        "problemSource": task.get("sourceModule") or task.get("source") or "任务系统",
        "triggerData": task.get("reason") or task.get("taskSignal") or "任务触发数据",
        "handlingAction": evidence.get("action") or task.get("handlingAction") or "已处理",
        "handlingResult": evidence.get("result") or task.get("handlingResult") or "已复核",
        "evidenceSummary": evidence.get("summary") or task.get("submitSummary") or task.get("submissionNote") or "已提交处理证据",
        "reviewComment": review.get("comment") or task.get("reviewComment") or task.get("reviewNote") or "复核通过",
        "nextSuggestion": "观察是否复发，并在下一周期复盘同类问题。",
        "status": "候选",
        "createdAt": task.get("completedAt") or review.get("reviewedAt") or task.get("updatedAt"),
        "updatedAt": review.get("reviewedAt") or task.get("updatedAt"),
    }
    if existing:
        existing.clear()
        existing.update(candidate)
        return deepcopy(existing)
    RECAP_CANDIDATES.insert(0, candidate)
    del RECAP_CANDIDATES[200:]
    return deepcopy(candidate)


def list_recap_candidates(target: str | None = None, limit: int = 50) -> List[Dict[str, Any]]:
    items = RECAP_CANDIDATES
    if target:
        items = [item for item in items if item.get("recapTarget") == target]
    return deepcopy(items[:limit])


def recap_summary() -> Dict[str, Any]:
    items = list_recap_candidates(limit=200)
    return {
        "version": RECAP_VERSION,
        "total": len(items),
        "daily": len([item for item in items if item.get("recapTarget") == "日报"]),
        "weekly": len([item for item in items if item.get("recapTarget") == "周报"]),
        "monthly": len([item for item in items if item.get("recapTarget") == "月报"]),
        "highRisk": len([item for item in items if item.get("riskLevel") == "高"]),
        "latest": items[0] if items else None,
    }
