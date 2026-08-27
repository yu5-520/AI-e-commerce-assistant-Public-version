#!/usr/bin/env python3
"""Execute the V25.10-V25.12 knowledge governance contract on real entrypoints."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Dict

from src.services import agent_rag_context_v2028_service as rag
from src.services import experience_memory_service as memory
from src.services import v25_knowledge_index_v2512_service as index
from src.services import v25_knowledge_lifecycle_v2511_service as lifecycle
from src.services import v25_knowledge_revision_v2510_service as revision
from src.services.v25_knowledge_asset_install_service import status


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def card(case_id: str, result: str, *, valid_until: str = "2099-12-31") -> Dict[str, Any]:
    return {
        "caseId": case_id,
        "caseType": "operation_solution",
        "level": "L1",
        "status": "pending_review",
        "categoryId": "home_living_goods",
        "platform": "通用",
        "storeId": "global",
        "problemType": "roas_scale",
        "actionFamily": "roas_scale",
        "operatorStyle": "稳健型",
        "qualityScore": 0.92,
        "effective": False,
        "sourceTaskId": "TASK-PHASE4-001",
        "title": "Phase4 reviewed ROAS experience",
        "initialJudgment": "ROAS稳定后逐级放量",
        "effectiveActions": ["小幅提高预算并按验收指标决定下一阶段"],
        "applicableConditions": ["ROAS稳定且库存健康"],
        "notApplicableConditions": ["退货率异常"],
        "resultSummary": result,
        "beforeMetrics": {"roas": 2.1},
        "afterMetrics": {"roas": 2.8},
        "validUntil": valid_until,
    }


def package() -> Dict[str, Any]:
    return {
        "productId": "PHASE4-PRODUCT",
        "productTitle": "Phase4 verifier product",
        "platform": "通用",
        "verticalCategory": "home_living_goods",
        "storeId": "global",
        "actionFamily": "roas_scale",
        "agent1OperatingJudgment": {
            "actionFamilyLock": {"selectedActionFamily": "roas_scale"},
            "primaryBusinessSignal": "ROAS stable",
            "primaryOperatingGap": "need controlled scale",
            "selectedOperatingRoute": "roas_scale",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/v25-phase4/knowledge-runtime-verification.json")
    args = parser.parse_args()

    runtime = status()
    require(runtime.get("installed") is True, "phase4 runtime not installed")
    require(runtime.get("physicalRagProviderReplaced") is False, "physical RAG provider replaced")
    require(runtime.get("newAgentRuntimeIntroduced") is False, "new Agent runtime introduced")

    first = memory.upsert_case(card("PHASE4-CASE-1", "first reviewed result"))
    rev1 = str(first.get("knowledgeRevisionId") or "")
    require(rev1.startswith("kr-"), "immutable candidate revision missing")
    require(lifecycle.state_of(rev1) == "pending_review", "candidate not pending_review")

    pre_review = rag.build_agent_rag_context_snapshot(package())
    require("PHASE4-CASE-1" not in pre_review.get("approvedCaseIds", []), "pending knowledge leaked into retrieval")

    approved1 = memory.approve_case(
        "PHASE4-CASE-1",
        reviewer_id="competition_operator",
        reason="phase4 runtime verifier approval",
    )
    require(bool(approved1), "first approval failed")
    require(lifecycle.state_of(rev1) == "active", "approved revision not active")
    manifest1 = index.current_manifest()
    require(bool(manifest1.get("manifestHash")), "first index manifest missing")

    post_review = rag.build_agent_rag_context_snapshot(package())
    require("PHASE4-CASE-1" in post_review.get("approvedCaseIds", []), "approved knowledge not retrieved")
    require(rev1 in post_review.get("matchedRevisionIds", []), "retrieval missing revision identity")
    require(post_review.get("indexManifestHash") == manifest1.get("manifestHash"), "retrieval not bound to current manifest")
    require(bool(post_review.get("retrievalReceiptHash")), "retrieval receipt missing")

    second = memory.upsert_case(card("PHASE4-CASE-1", "second reviewed result with changed evidence"))
    rev2 = str(second.get("knowledgeRevisionId") or "")
    require(rev2.startswith("kr-") and rev2 != rev1, "changed content did not create new revision")
    require(lifecycle.state_of(rev2) == "pending_review", "replacement revision bypassed review")
    require(revision.revision_record(rev1) is not None, "old immutable revision disappeared")

    approved2 = memory.approve_case(
        "PHASE4-CASE-1",
        reviewer_id="competition_operator",
        reason="approve replacement revision",
    )
    require(bool(approved2), "replacement approval failed")
    require(lifecycle.state_of(rev2) == "active", "replacement revision not active")
    require(lifecycle.state_of(rev1) == "superseded", "old active revision not superseded")
    manifest2 = index.current_manifest()
    require(manifest2.get("manifestHash") != manifest1.get("manifestHash"), "knowledge mutation did not rotate manifest")
    require(manifest2.get("parentManifestHash") == manifest1.get("manifestHash"), "manifest lineage parent mismatch")

    latest = rag.build_agent_rag_context_snapshot(package())
    require(rev2 in latest.get("matchedRevisionIds", []), "latest active revision not retrieved")
    require(rev1 not in latest.get("matchedRevisionIds", []), "superseded revision leaked into current Head")

    rolled = index.rollback_head(
        actor_id="competition_operator",
        reason="phase4 exact rollback verification",
        target_manifest_hash=str(manifest1.get("manifestHash")),
    )
    require(bool(dict(rolled.get("head") or {}).get("rollbackPinned")), "rollback did not pin Head")
    rolled_snapshot = rag.build_agent_rag_context_snapshot(package())
    require(rolled_snapshot.get("indexManifestHash") == manifest1.get("manifestHash"), "retrieval ignored rollback Head")
    require(rev1 in rolled_snapshot.get("matchedRevisionIds", []), "rollback did not restore old revision")
    require(rev2 not in rolled_snapshot.get("matchedRevisionIds", []), "rollback leaked newer revision")

    reused = rag.build_agent_rag_context_snapshot({
        **package(),
        "ragContextSnapshot": deepcopy(rolled_snapshot),
    })
    require(reused.get("indexManifestHash") == manifest1.get("manifestHash"), "pinned rollback silently rolled forward")

    resumed = index.resume_current_active_set(
        actor_id="competition_operator",
        reason="phase4 rollback verification complete",
    )
    require(resumed.get("knowledgeSnapshotHash") == manifest2.get("knowledgeSnapshotHash"), "resume did not restore active revision set")

    expired = memory.upsert_case(card("PHASE4-CASE-EXPIRED", "expired knowledge", valid_until="2000-01-01"))
    expired_rev = str(expired.get("knowledgeRevisionId") or "")
    memory.approve_case(
        "PHASE4-CASE-EXPIRED",
        reviewer_id="competition_operator",
        reason="verify expiry lifecycle guard",
    )
    index.ensure_active_manifest(actor_id="phase4_verifier", reason="expiry_guard_verification")
    require(lifecycle.state_of(expired_rev) == "stale", "expired active knowledge did not become stale")
    expired_snapshot = rag.build_agent_rag_context_snapshot(package())
    require(expired_rev not in expired_snapshot.get("matchedRevisionIds", []), "stale knowledge remained retrievable")

    material = {
        "schema": "v25.phase4_runtime_verification.v1",
        "version": "25.12.0",
        "verified": True,
        "pendingReviewRetrievalBlocked": True,
        "humanApprovalPromotesImmutableRevision": True,
        "reviewedRevisionRetrievable": True,
        "newRevisionSupersedesOldRevision": True,
        "oldRevisionPreserved": True,
        "indexManifestRotatesOnKnowledgeMutation": True,
        "indexManifestParentLineageVerified": True,
        "retrievalReceiptBindsRevisionAndManifest": True,
        "headRollbackExact": True,
        "headRollbackPinnedAcrossRetrieval": True,
        "rollbackResumeVerified": True,
        "expiredKnowledgeBecomesStale": True,
        "staleKnowledgeRetrievalBlocked": True,
        "automaticApprovalAllowed": False,
        "automaticDeleteAllowed": False,
        "physicalRagProviderReplaced": False,
        "newAgentRuntimeIntroduced": False,
        "revision1": rev1,
        "revision2": rev2,
        "manifest1": manifest1.get("manifestHash"),
        "manifest2": manifest2.get("manifestHash"),
        "retrievalReceiptHash": latest.get("retrievalReceiptHash"),
    }
    material["evidenceHash"] = revision.hash_value(material)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(material, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(material, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
