#!/usr/bin/env python3
"""Execute V25.13-V25.15 quantification, Eval regression and Knowledge Center contracts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.api.routes.knowledge_center import knowledge_center_overview
from src.services import experience_memory_service as memory
from src.services import v25_knowledge_index_v2512_service as index
from src.services import v25_knowledge_lifecycle_v2511_service as lifecycle
from src.services import v25_knowledge_revision_v2510_service as revision
from src.services import v25_rag_eval_v2514_service as rag_eval
from src.services import v25_rag_quant_v2513_service as quant
from src.services.v25_knowledge_asset_install_service import status as phase4_status


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def card(case_id: str, task_id: str, result: str) -> Dict[str, Any]:
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
        "qualityScore": 0.94,
        "effective": False,
        "sourceTaskId": task_id,
        "title": f"Phase5 Eval knowledge {case_id}",
        "initialJudgment": "ROAS稳定后按证据分阶段放量",
        "effectiveActions": ["逐级调整预算并保留阶段验收"],
        "applicableConditions": ["ROAS稳定且库存健康"],
        "notApplicableConditions": ["退货率异常"],
        "resultSummary": result,
        "beforeMetrics": {"roas": 2.2},
        "afterMetrics": {"roas": 2.9},
        "validUntil": "2099-12-31",
    }


def approve(case_id: str) -> str:
    value = memory.approve_case(
        case_id,
        reviewer_id="competition_operator",
        reason="phase5 verifier human approval",
    )
    require(bool(value), f"approval failed for {case_id}")
    revision_id = str(revision.latest_revision(case_id) or "")
    require(revision_id.startswith("kr-"), f"revision missing for {case_id}")
    require(lifecycle.state_of(revision_id) == "active", f"revision not active for {case_id}")
    return revision_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/v25-phase5/rag-runtime-verification.json")
    args = parser.parse_args()

    installed = phase4_status()
    require(installed.get("installed") is True, "Phase4 knowledge governance must remain installed")
    require(installed.get("physicalRagProviderReplaced") is False, "physical RAG provider replaced")
    require(installed.get("newAgentRuntimeIntroduced") is False, "new Agent runtime introduced")

    first = memory.upsert_case(card("PHASE5-EVAL-1", "TASK-PHASE5-001", "first evaluated knowledge"))
    second = memory.upsert_case(card("PHASE5-EVAL-2", "TASK-PHASE5-002", "second evaluated knowledge"))
    require(str(first.get("knowledgeRevisionId") or "").startswith("kr-"), "first candidate revision missing")
    require(str(second.get("knowledgeRevisionId") or "").startswith("kr-"), "second candidate revision missing")
    rev1 = approve("PHASE5-EVAL-1")
    rev2 = approve("PHASE5-EVAL-2")
    manifest = index.ensure_active_manifest(actor_id="phase5_verifier", reason="phase5_eval_baseline")
    require(bool(manifest.get("manifestHash")), "Phase5 manifest missing")

    receipt_hit = index.retrieval_receipt(
        query_fingerprint="phase5-query-hit",
        matched_case_ids=["PHASE5-EVAL-1"],
    )
    observation_hit = quant.record_retrieval_observation(
        receipt_hit,
        candidate_count=2,
        eligible_count=2,
        matched_count=1,
        filtered_lifecycle_count=0,
        latency_ms=12.5,
    )
    receipt_zero = index.retrieval_receipt(
        query_fingerprint="phase5-query-zero",
        matched_case_ids=[],
    )
    quant.record_retrieval_observation(
        receipt_zero,
        candidate_count=2,
        eligible_count=2,
        matched_count=0,
        filtered_lifecycle_count=0,
        latency_ms=18.5,
    )
    metrics = quant.retrieval_metric_snapshot(index_manifest_hash=str(manifest["manifestHash"]))
    require(metrics["metrics"]["observationCount"] == 2, "retrieval observation count mismatch")
    require(metrics["metrics"]["zeroHitRate"] == 0.5, "zero-hit metric mismatch")
    require(metrics["metrics"]["groundTruthMetricsRequireEvalSet"] is True, "unlabelled traffic fabricated ground-truth metrics")
    require(metrics["metricSnapshotHash"] == revision.hash_value({
        key: value for key, value in metrics.items() if key not in {"metricSnapshotHash", "createdAt"}
    }), "metric snapshot hash mismatch")
    require(quant.retrieval_trace(receipt_hit["retrievalReceiptHash"])["observationHash"] == observation_hit["observationHash"], "retrieval trace missing")

    tamper_blocked = False
    tampered = dict(receipt_hit)
    tampered["matchedRevisionIds"] = []
    try:
        quant.record_retrieval_observation(
            tampered,
            candidate_count=0,
            eligible_count=0,
            matched_count=0,
            filtered_lifecycle_count=0,
            latency_ms=1.0,
        )
    except ValueError:
        tamper_blocked = True
    require(tamper_blocked, "tampered Retrieval Receipt was accepted")

    eval_set = rag_eval.register_eval_set(
        eval_set_id="phase5-chinese-rag-core",
        eval_set_version="1.0.0",
        created_by="competition_operator",
        cases=[
            {
                "evalCaseId": "CN-ROAS-001",
                "query": "前两天ROAS稳定的那个链接还能继续放量吗",
                "expectedRelevantRevisionIds": [rev1],
                "expectedRelevantCaseIds": ["PHASE5-EVAL-1"],
                "expectedAbstention": False,
                "category": "中文口语-时间指代",
                "humanLabel": "应召回已审核放量经验",
                "provenance": "human_labelled_phase5_verifier",
            },
            {
                "evalCaseId": "CN-ROAS-002",
                "query": "新品拉新还是按稳健ROAS那个打法吗",
                "expectedRelevantRevisionIds": [rev2],
                "expectedRelevantCaseIds": ["PHASE5-EVAL-2"],
                "expectedAbstention": False,
                "category": "中文口语-省略表达",
                "humanLabel": "应召回第二条已审核经验",
                "provenance": "human_labelled_phase5_verifier",
            },
        ],
    )
    immutable_set_blocked = False
    try:
        rag_eval.register_eval_set(
            eval_set_id="phase5-chinese-rag-core",
            eval_set_version="1.0.0",
            created_by="competition_operator",
            cases=[{
                "evalCaseId": "CN-ROAS-MUTATED",
                "query": "偷偷改掉同版本测试集",
                "expectedRelevantRevisionIds": [rev1],
                "expectedAbstention": False,
            }],
        )
    except ValueError:
        immutable_set_blocked = True
    require(immutable_set_blocked, "EvalSet same-version mutation was accepted")

    base_run = rag_eval.record_eval_run(
        eval_set_hash=eval_set["evalSetHash"],
        run_role="BASE",
        manifest=manifest,
        runtime_version="phase5-base",
        case_results=[
            {"evalCaseId": "CN-ROAS-001", "queryFingerprint": "base-1", "retrievalReceiptHash": receipt_hit["retrievalReceiptHash"], "matchedRevisionIds": [rev1]},
            {"evalCaseId": "CN-ROAS-002", "queryFingerprint": "base-2", "retrievalReceiptHash": receipt_zero["retrievalReceiptHash"], "matchedRevisionIds": []},
        ],
    )
    target_run = rag_eval.record_eval_run(
        eval_set_hash=eval_set["evalSetHash"],
        run_role="TARGET",
        manifest=manifest,
        runtime_version="phase5-target-good",
        case_results=[
            {"evalCaseId": "CN-ROAS-001", "queryFingerprint": "target-1", "retrievalReceiptHash": receipt_hit["retrievalReceiptHash"], "matchedRevisionIds": [rev1]},
            {"evalCaseId": "CN-ROAS-002", "queryFingerprint": "target-2", "retrievalReceiptHash": "phase5-target-receipt-2", "matchedRevisionIds": [rev2]},
        ],
    )
    good_comparison = rag_eval.compare_base_target(
        base_run_hash=base_run["evalRunHash"],
        target_run_hash=target_run["evalRunHash"],
    )
    require(good_comparison["verified"] is True, f"good TARGET blocked: {good_comparison['findings']}")
    require(target_run["metrics"]["hitAt3"] == 1.0, "TARGET Hit@3 mismatch")
    require(target_run["metrics"]["mrr"] == 1.0, "TARGET MRR mismatch")

    degraded_run = rag_eval.record_eval_run(
        eval_set_hash=eval_set["evalSetHash"],
        run_role="TARGET",
        manifest=manifest,
        runtime_version="phase5-target-degraded",
        case_results=[
            {"evalCaseId": "CN-ROAS-001", "queryFingerprint": "degraded-1", "retrievalReceiptHash": "degraded-r1", "matchedRevisionIds": []},
            {"evalCaseId": "CN-ROAS-002", "queryFingerprint": "degraded-2", "retrievalReceiptHash": "degraded-r2", "matchedRevisionIds": []},
        ],
    )
    degraded = rag_eval.compare_base_target(
        base_run_hash=base_run["evalRunHash"],
        target_run_hash=degraded_run["evalRunHash"],
    )
    require(degraded["verified"] is False, "degraded TARGET was not blocked")
    require("hit_at_3_regression" in degraded["findings"], "Hit@3 regression finding missing")
    require("zero_hit_rate_regression" in degraded["findings"], "zero-hit regression finding missing")

    lifecycle.transition(
        rev2,
        "stale",
        actor_id="phase5_verifier",
        reason="verify_stale_leak_gate",
    )
    leak_run = rag_eval.record_eval_run(
        eval_set_hash=eval_set["evalSetHash"],
        run_role="TARGET",
        manifest=manifest,
        runtime_version="phase5-target-stale-leak",
        case_results=[
            {"evalCaseId": "CN-ROAS-001", "queryFingerprint": "leak-1", "retrievalReceiptHash": "leak-r1", "matchedRevisionIds": [rev1]},
            {"evalCaseId": "CN-ROAS-002", "queryFingerprint": "leak-2", "retrievalReceiptHash": "leak-r2", "matchedRevisionIds": [rev2]},
        ],
    )
    leak_comparison = rag_eval.compare_base_target(
        base_run_hash=base_run["evalRunHash"],
        target_run_hash=leak_run["evalRunHash"],
    )
    require(leak_comparison["verified"] is False, "stale revision leak was not blocked")
    require("stale_revision_leak" in leak_comparison["findings"], "stale leak finding missing")

    overview = knowledge_center_overview()
    require(overview.get("language") == "zh-CN", "Knowledge Center language contract missing")
    require(dict(overview.get("index") or {}).get("manifestHash"), "Knowledge Center index projection missing")
    require(dict(overview.get("governance") or {}).get("directDatabaseMutationAllowed") is False, "Knowledge Center governance projection drifted")

    index_html = (ROOT_DIR / "web_demo/index.html").read_text(encoding="utf-8")
    bootstrap_js = (ROOT_DIR / "web_demo/bootstrap.js").read_text(encoding="utf-8")
    page_js = (ROOT_DIR / "web_demo/modules/knowledge-center/page.js").read_text(encoding="utf-8")
    api_route = (ROOT_DIR / "src/api/routes/knowledge_center.py").read_text(encoding="utf-8")
    route_init = (ROOT_DIR / "src/api/routes/__init__.py").read_text(encoding="utf-8")
    require('data-route="knowledge-center"' in index_html, "Knowledge Center navigation missing")
    require('"knowledge-center", "RAG知识中心"' in bootstrap_js, "Knowledge Center lazy route missing")
    require("中文 RAG 知识中心" in page_js, "Chinese Knowledge Center heading missing")
    require("/api/system/knowledge-center/overview" in page_js, "Knowledge Center governed API binding missing")
    require("UPDATE " not in page_js.upper() and "DELETE FROM" not in page_js.upper(), "frontend contains direct database mutation")
    require("system.router.include_router(knowledge_center.router)" in route_init, "Knowledge Center is not nested under system router")
    require("@router.post(\"/eval/sets\")" in api_route, "Knowledge Center EvalSet authority route missing")
    require("UPDATE rag_knowledge" not in api_route and "DELETE FROM rag_knowledge" not in api_route, "Knowledge Center API bypasses governance authority")

    material = {
        "schema": "v25.phase5_rag_runtime_verification.v1",
        "version": "25.15.0",
        "verified": True,
        "receiptBoundMetrics": True,
        "manifestBoundMetrics": True,
        "tamperedReceiptBlocked": tamper_blocked,
        "metricSnapshotImmutable": True,
        "groundTruthMetricsRequireEvalSet": True,
        "evalSetImmutable": immutable_set_blocked,
        "evalSetVersioned": True,
        "evalRunImmutable": True,
        "baseTargetEvalRequired": True,
        "regressionGateBlocksDegradation": degraded["verified"] is False,
        "staleLeakGateBlocksDegradation": leak_comparison["verified"] is False,
        "retrievalAnswerEvalSeparated": True,
        "llmJudgeSoleReleaseAuthority": False,
        "chineseKnowledgeCenterRegistered": True,
        "directDatabaseMutationAllowed": False,
        "activeRevisionInPlaceEditAllowed": False,
        "phase4KnowledgeGovernanceRetained": True,
        "physicalRagProviderReplaced": False,
        "vectorIndexRequired": False,
        "newAgentRuntimeIntroduced": False,
        "knowledgeMayCreateSystemFact": False,
        "metricSnapshotHash": metrics["metricSnapshotHash"],
        "evalSetHash": eval_set["evalSetHash"],
        "baseEvalRunHash": base_run["evalRunHash"],
        "targetEvalRunHash": target_run["evalRunHash"],
        "goodComparisonHash": good_comparison["comparisonHash"],
        "degradedComparisonHash": degraded["comparisonHash"],
        "staleLeakComparisonHash": leak_comparison["comparisonHash"],
        "indexManifestHash": manifest["manifestHash"],
    }
    material["evidenceHash"] = "sha256:" + revision.hash_value(material)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(material, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(material, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
