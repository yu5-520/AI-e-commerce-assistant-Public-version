"""Install V25.10-V25.12 governance behind existing knowledge entrypoints.

No Agent runtime or physical RAG provider is replaced. Existing task recap,
manual review and Agent RAG call sites remain the public/runtime contract.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from src.repositories.sqlite_repository import connect
from src.services import agent_rag_context_v2028_service as rag
from src.services import experience_memory_service as memory
from src.services import v25_knowledge_index_v2512_service as index
from src.services import v25_knowledge_lifecycle_v2511_service as lifecycle
from src.services import v25_knowledge_revision_v2510_service as revision

VERSION = "25.12.0"
_INSTALLED = False
_ORIGINAL_UPSERT = None
_ORIGINAL_UPDATE_STATUS = None
_ORIGINAL_BUILD_RAG = None


def _candidate_card(incoming: Dict[str, Any], saved: Dict[str, Any]) -> Dict[str, Any]:
    item = deepcopy(incoming)
    item["caseId"] = saved.get("caseId") or item.get("caseId")
    return item


def _register_candidate(card: Dict[str, Any]) -> str | None:
    if str(card.get("status") or "pending_review") != "pending_review":
        return None
    revision_id = revision.ensure_revision(card)
    if revision_id:
        lifecycle.register_revision(revision_id)
    return revision_id


def _active_for_case(case_id: str, *, excluding: str) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.revision_id
            FROM rag_knowledge_revisions r
            JOIN rag_knowledge_revision_state s ON s.revision_id = r.revision_id
            WHERE r.case_id = ? AND r.revision_id <> ? AND s.lifecycle_state = 'active'
            ORDER BY r.created_at DESC
            """,
            (case_id, excluding),
        ).fetchall()
    return [str(row["revision_id"]) for row in rows]


def _promote(case_id: str, *, reviewer_id: str, reason: str, migration: bool = False) -> Dict[str, Any]:
    target = revision.latest_revision(case_id, states=("pending_review", "re_review"))
    if not target:
        raise ValueError(f"no reviewable knowledge revision for {case_id}")
    before = lifecycle.state_of(target) or "pending_review"
    review_hash = revision.record_review(
        target,
        decision="approved",
        reviewer_id=reviewer_id,
        before_state=before,
        after_state="active",
        reason=reason,
        migration=migration,
    )
    lifecycle_hash = lifecycle.transition(
        target,
        "active",
        actor_id=reviewer_id,
        reason=reason or "human_review_approved",
    )
    superseded: list[str] = []
    for old_revision in _active_for_case(case_id, excluding=target):
        lifecycle.transition(
            old_revision,
            "superseded",
            actor_id=reviewer_id,
            reason="new_active_revision_supersedes_previous_active_revision",
            replacement_revision_id=target,
        )
        superseded.append(old_revision)
    index.release_rollback_pin(actor_id=reviewer_id, reason="knowledge_promotion_mutation")
    manifest = index.ensure_active_manifest(actor_id=reviewer_id, reason="knowledge_promotion")
    receipt = {
        "schema": "rag.knowledge_promotion_receipt.v1",
        "version": "25.10.0",
        "revisionId": target,
        "reviewEventHash": review_hash,
        "lifecycleEventHash": lifecycle_hash,
        "supersededRevisionIds": superseded,
        "indexManifestHash": manifest.get("manifestHash"),
    }
    receipt["promotionReceiptHash"] = revision.hash_value(receipt)
    return receipt


def _reject(case_id: str, *, reviewer_id: str, reason: str) -> Dict[str, Any]:
    target = revision.latest_revision(case_id, states=("pending_review", "re_review"))
    if not target:
        raise ValueError(f"no reviewable knowledge revision for {case_id}")
    before = lifecycle.state_of(target) or "pending_review"
    review_hash = revision.record_review(
        target,
        decision="rejected",
        reviewer_id=reviewer_id,
        before_state=before,
        after_state="rejected",
        reason=reason,
    )
    lifecycle_hash = lifecycle.transition(
        target,
        "rejected",
        actor_id=reviewer_id,
        reason=reason or "human_review_rejected",
    )
    return {
        "schema": "rag.knowledge_review_receipt.v1",
        "version": "25.10.0",
        "revisionId": target,
        "reviewEventHash": review_hash,
        "lifecycleEventHash": lifecycle_hash,
    }


def _governed_upsert(card: Dict[str, Any]) -> Dict[str, Any]:
    saved = _ORIGINAL_UPSERT(card)
    candidate = _candidate_card(card, saved)
    revision_id = _register_candidate(candidate)
    if not revision_id:
        return saved
    return {
        **saved,
        "knowledgeRevisionId": revision_id,
        "knowledgeLifecycleState": lifecycle.state_of(revision_id),
    }


def _governed_update_status(
    case_id: str,
    *,
    status: str,
    reviewer_id: str | None = None,
    reason: str = "",
) -> Dict[str, Any] | None:
    result = _ORIGINAL_UPDATE_STATUS(
        case_id,
        status=status,
        reviewer_id=reviewer_id,
        reason=reason,
    )
    if not result or status not in {"approved", "rejected"}:
        return result

    reviewer = str(result.get("reviewerId") or reviewer_id or "").strip()
    if not reviewer:
        raise ValueError("knowledge review requires explicit reviewer identity")

    # A pre-Phase4 row may not yet have an immutable revision. Create it from
    # reviewed content once, then bind the human decision to that revision.
    target = revision.latest_revision(case_id, states=("pending_review", "re_review"))
    if not target:
        target = revision.ensure_revision(result)
        if target:
            lifecycle.register_revision(target)

    if status == "approved":
        if not bool(result.get("effective")):
            return {
                **result,
                "knowledgePromotionBlocked": True,
                "knowledgePromotionReason": "legacy_quality_gate_not_effective",
            }
        receipt = _promote(
            case_id,
            reviewer_id=reviewer,
            reason=reason or "human_review_approved",
        )
        return {
            **result,
            "knowledgeLifecycleState": "active",
            "knowledgePromotionReceipt": receipt,
        }

    receipt = _reject(
        case_id,
        reviewer_id=reviewer,
        reason=reason or "human_review_rejected",
    )
    return {
        **result,
        "knowledgeLifecycleState": "rejected",
        "knowledgeReviewReceipt": receipt,
    }


def _governed_load_real_approved_cases(limit: int = 300) -> list[Dict[str, Any]]:
    return index.load_head_cases()[: max(20, min(1000, int(limit)))]


def _governed_build_rag(
    package: Dict[str, Any],
    action_pack: Dict[str, Any] | None = None,
    *,
    limit: int = rag.DEFAULT_LIMIT,
) -> Dict[str, Any]:
    manifest = index.ensure_active_manifest(
        actor_id="agent_rag_retrieval",
        reason="retrieval_snapshot_guard",
    )
    expected = str(manifest.get("manifestHash") or "")
    safe_package = package
    existing = dict(package.get("ragContextSnapshot") or {})
    if existing and str(existing.get("indexManifestHash") or "") != expected:
        safe_package = deepcopy(package)
        safe_package.pop("ragContextSnapshot", None)
    snapshot = _ORIGINAL_BUILD_RAG(safe_package, action_pack, limit=limit)
    receipt = index.retrieval_receipt(
        query_fingerprint=str(snapshot.get("queryFingerprint") or ""),
        matched_case_ids=[str(item) for item in snapshot.get("approvedCaseIds") or []],
    )
    return {
        **snapshot,
        "retrievalSource": "knowledge_index_head->rag_experience_cards",
        "knowledgeSnapshotHash": receipt.get("knowledgeSnapshotHash"),
        "indexVersion": receipt.get("indexVersion"),
        "indexManifestHash": receipt.get("indexManifestHash"),
        "matchedRevisionIds": receipt.get("matchedRevisionIds") or [],
        "knowledgeRetrievalReceipt": receipt,
        "retrievalReceiptHash": receipt.get("retrievalReceiptHash"),
        "snapshotReuseRequiresCurrentManifest": True,
    }


def _bootstrap_legacy_reviewed() -> Dict[str, int]:
    imported = 0
    pending = 0
    memory.ensure_memory_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM rag_experience_cards
            WHERE source_task_id IS NOT NULL AND TRIM(source_task_id) != ''
            ORDER BY created_at ASC
            """
        ).fetchall()
    for row in rows:
        card = memory._row_to_case(row)
        if not revision.is_real_task_experience(card):
            continue
        revision_id = revision.ensure_revision(card)
        if not revision_id:
            continue
        lifecycle.register_revision(revision_id)
        if str(card.get("status") or "") == "approved" and bool(card.get("effective")):
            if lifecycle.state_of(revision_id) in {"pending_review", "re_review"}:
                _promote(
                    str(card.get("caseId")),
                    reviewer_id=str(card.get("reviewerId") or "legacy_review_migration"),
                    reason="pre_phase4_reviewed_knowledge_migration",
                    migration=True,
                )
                imported += 1
        else:
            pending += 1
    return {"importedReviewed": imported, "pendingCandidates": pending}


def status(*, idempotent: bool = False) -> Dict[str, Any]:
    manifest = index.current_manifest()
    with connect() as conn:
        states = {
            str(row["lifecycle_state"]): int(row["count"])
            for row in conn.execute(
                "SELECT lifecycle_state, COUNT(*) AS count FROM rag_knowledge_revision_state GROUP BY lifecycle_state"
            ).fetchall()
        }
        reviews = int(conn.execute(
            "SELECT COUNT(*) AS c FROM rag_knowledge_review_events"
        ).fetchone()["c"])
    return {
        "version": VERSION,
        "installed": _INSTALLED,
        "idempotent": idempotent,
        "runtimeMode": "VERSIONED_KNOWLEDGE_ASSET_GOVERNANCE",
        "physicalRagProviderReplaced": False,
        "newAgentRuntimeIntroduced": False,
        "automaticApprovalAllowed": False,
        "automaticDeleteAllowed": False,
        "lifecycleCounts": states,
        "reviewEventCount": reviews,
        "indexVersion": manifest.get("indexVersion"),
        "indexManifestHash": manifest.get("manifestHash"),
        "knowledgeSnapshotHash": manifest.get("knowledgeSnapshotHash"),
    }


def install_v25_knowledge_asset_governance() -> Dict[str, Any]:
    global _INSTALLED, _ORIGINAL_UPSERT, _ORIGINAL_UPDATE_STATUS, _ORIGINAL_BUILD_RAG
    if _INSTALLED:
        return status(idempotent=True)

    revision.ensure_tables()
    lifecycle.ensure_tables()
    index.ensure_tables()
    migration = _bootstrap_legacy_reviewed()
    index.ensure_active_manifest(actor_id="phase4_bootstrap", reason="phase4_cutover_snapshot")

    _ORIGINAL_UPSERT = memory.upsert_case
    _ORIGINAL_UPDATE_STATUS = memory.update_case_status
    _ORIGINAL_BUILD_RAG = rag.build_agent_rag_context_snapshot

    memory.upsert_case = _governed_upsert
    memory.update_case_status = _governed_update_status
    rag._load_real_approved_cases = _governed_load_real_approved_cases
    rag.build_agent_rag_context_snapshot = _governed_build_rag
    _INSTALLED = True
    return {**status(), "legacyMigration": migration}


__all__ = [
    "VERSION",
    "install_v25_knowledge_asset_governance",
    "status",
]
