"""V25.15 Chinese RAG Knowledge Center API over V25.10-V25.14 authorities."""
from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Query

from src.repositories.sqlite_repository import connect
from src.services.v25_knowledge_index_v2512_service import current_manifest, ensure_active_manifest
from src.services.v25_rag_eval_v2514_service import (
    compare_base_target,
    list_eval_runs,
    list_eval_sets,
    register_eval_set,
)
from src.services.v25_rag_quant_v2513_service import (
    knowledge_health_snapshot,
    recent_observations,
    retrieval_metric_snapshot,
    retrieval_trace,
)

VERSION = "25.15.0"
router = APIRouter(prefix="/knowledge-center", tags=["rag-knowledge-center"])


def _recent_revisions(limit: int = 50, state: str | None = None) -> list[Dict[str, Any]]:
    resolved_limit = max(1, min(int(limit), 200))
    params: list[Any] = []
    where = ""
    if state:
        where = "WHERE s.lifecycle_state = ?"
        params.append(str(state))
    params.append(resolved_limit)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT r.revision_id, r.case_id, r.content_hash, r.source_task_id,
                   r.previous_revision_id, r.valid_until, r.created_at,
                   s.lifecycle_state, s.stale_reason, s.replacement_revision_id,
                   s.updated_at
            FROM rag_knowledge_revisions r
            LEFT JOIN rag_knowledge_revision_state s ON s.revision_id = r.revision_id
            {where}
            ORDER BY r.created_at DESC, r.revision_id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [
        {
            "revisionId": str(row["revision_id"]),
            "caseId": str(row["case_id"]),
            "contentHash": str(row["content_hash"]),
            "sourceTaskId": str(row["source_task_id"]),
            "previousRevisionId": row["previous_revision_id"],
            "validUntil": row["valid_until"],
            "createdAt": str(row["created_at"]),
            "lifecycleState": str(row["lifecycle_state"] or "unknown"),
            "staleReason": row["stale_reason"],
            "replacementRevisionId": row["replacement_revision_id"],
            "stateUpdatedAt": row["updated_at"],
        }
        for row in rows
    ]


def _recent_review_events(limit: int = 30) -> list[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT event_hash, revision_id, decision, reviewer_id, reason,
                   before_hash, after_hash, migration, created_at
            FROM rag_knowledge_review_events
            ORDER BY created_at DESC, event_hash DESC LIMIT ?
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    return [
        {
            "eventHash": str(row["event_hash"]),
            "revisionId": str(row["revision_id"]),
            "decision": str(row["decision"]),
            "reviewerId": str(row["reviewer_id"]),
            "reason": str(row["reason"] or ""),
            "beforeHash": str(row["before_hash"]),
            "afterHash": str(row["after_hash"]),
            "migration": bool(row["migration"]),
            "createdAt": str(row["created_at"]),
        }
        for row in rows
    ]


@router.get("/overview")
def knowledge_center_overview() -> Dict[str, Any]:
    manifest = ensure_active_manifest(actor_id="knowledge_center", reason="knowledge_center_overview")
    return {
        "schema": "rag.knowledge_center_overview.v1",
        "version": VERSION,
        "language": "zh-CN",
        "index": manifest,
        "knowledgeHealth": knowledge_health_snapshot(),
        "retrievalMetrics": retrieval_metric_snapshot(),
        "recentRevisions": _recent_revisions(limit=20),
        "recentReviewEvents": _recent_review_events(limit=12),
        "evalSets": list_eval_sets(limit=12),
        "evalRuns": list_eval_runs(limit=12),
        "governance": {
            "directDatabaseMutationAllowed": False,
            "activeRevisionInPlaceEditAllowed": False,
            "rollbackAuthority": "V25.12_INDEX_HEAD",
            "evalSetAuthority": "V25.14_IMMUTABLE_EVAL_SET",
            "physicalRagProviderReplaced": False,
            "vectorIndexRequired": False,
            "newAgentRuntimeIntroduced": False,
        },
    }


@router.get("/index")
def knowledge_index() -> Dict[str, Any]:
    manifest = current_manifest()
    if not manifest:
        manifest = ensure_active_manifest(actor_id="knowledge_center", reason="knowledge_center_index")
    return {"version": VERSION, "index": manifest}


@router.get("/metrics")
def rag_metrics() -> Dict[str, Any]:
    return retrieval_metric_snapshot()


@router.get("/revisions")
def knowledge_revisions(
    limit: int = Query(default=50, ge=1, le=200),
    state: str | None = Query(default=None),
) -> Dict[str, Any]:
    return {"version": VERSION, "items": _recent_revisions(limit=limit, state=state)}


@router.get("/retrievals")
def retrievals(limit: int = Query(default=50, ge=1, le=200)) -> Dict[str, Any]:
    return {"version": VERSION, "items": recent_observations(limit=limit)}


@router.get("/retrievals/{receipt_hash}")
def retrieval_detail(receipt_hash: str) -> Dict[str, Any]:
    item = retrieval_trace(receipt_hash)
    if not item:
        raise HTTPException(status_code=404, detail="retrieval receipt observation not found")
    return item


@router.get("/eval/sets")
def eval_sets(limit: int = Query(default=100, ge=1, le=500)) -> Dict[str, Any]:
    return {"version": VERSION, "items": list_eval_sets(limit=limit)}


@router.get("/eval/runs")
def eval_runs(limit: int = Query(default=100, ge=1, le=500)) -> Dict[str, Any]:
    return {"version": VERSION, "items": list_eval_runs(limit=limit)}


@router.post("/eval/sets")
def create_eval_set(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return register_eval_set(
            eval_set_id=str(payload.get("evalSetId") or ""),
            eval_set_version=str(payload.get("evalSetVersion") or ""),
            cases=list(payload.get("cases") or []),
            created_by=str(payload.get("createdBy") or "competition_operator"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/eval/compare")
def compare_eval_runs(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return compare_base_target(
            base_run_hash=str(payload.get("baseRunHash") or ""),
            target_run_hash=str(payload.get("targetRunHash") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
