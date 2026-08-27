"""V25.14 immutable RAG EvalSet, EvalRun and BASE/TARGET regression authority."""
from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Dict, Iterable, Mapping, Sequence

from src.repositories.sqlite_repository import connect
from src.services.v25_knowledge_revision_v2510_service import hash_value

VERSION = "25.14.0"
EVAL_SET_SCHEMA = "rag.eval_set.v1"
EVAL_RUN_SCHEMA = "rag.eval_run.v1"
REGRESSION_SCHEMA = "rag.eval_regression_comparison.v1"
MAX_HIT_AT3_REGRESSION = 0.03
MAX_ZERO_HIT_RATE_INCREASE = 0.02


def _now() -> str:
    return datetime.now().isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def ensure_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_eval_sets(
                eval_set_hash TEXT PRIMARY KEY,
                eval_set_id TEXT NOT NULL,
                eval_set_version TEXT NOT NULL,
                cases_json TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(eval_set_id, eval_set_version)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_eval_runs(
                eval_run_hash TEXT PRIMARY KEY,
                eval_set_hash TEXT NOT NULL,
                run_role TEXT NOT NULL,
                index_version TEXT NOT NULL,
                index_manifest_hash TEXT NOT NULL,
                knowledge_snapshot_hash TEXT NOT NULL,
                retrieval_policy_version TEXT NOT NULL,
                runtime_version TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                case_results_json TEXT NOT NULL,
                judge_evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_eval_regression_comparisons(
                comparison_hash TEXT PRIMARY KEY,
                base_run_hash TEXT NOT NULL,
                target_run_hash TEXT NOT NULL,
                verified INTEGER NOT NULL,
                findings_json TEXT NOT NULL,
                comparison_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_eval_run_set ON rag_eval_runs(eval_set_hash, created_at)")
        conn.commit()


def _normalize_cases(cases: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    normalized: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in cases:
        case = dict(raw)
        case_id = str(case.get("evalCaseId") or "").strip()
        query = str(case.get("query") or "").strip()
        if not case_id or not query:
            raise ValueError("evalCaseId and query are required")
        if case_id in seen:
            raise ValueError(f"duplicate evalCaseId: {case_id}")
        seen.add(case_id)
        relevant_revisions = sorted({str(value) for value in case.get("expectedRelevantRevisionIds") or [] if str(value)})
        relevant_cases = sorted({str(value) for value in case.get("expectedRelevantCaseIds") or [] if str(value)})
        expected_abstention = bool(case.get("expectedAbstention", False))
        if not expected_abstention and not relevant_revisions and not relevant_cases:
            raise ValueError(f"eval case {case_id} requires relevant knowledge or expectedAbstention=true")
        normalized.append({
            "evalCaseId": case_id,
            "query": query,
            "expectedRelevantRevisionIds": relevant_revisions,
            "expectedRelevantCaseIds": relevant_cases,
            "expectedAbstention": expected_abstention,
            "category": str(case.get("category") or "general"),
            "humanLabel": str(case.get("humanLabel") or ""),
            "provenance": str(case.get("provenance") or "human_eval_set"),
        })
    if not normalized:
        raise ValueError("EvalSet must contain at least one case")
    return sorted(normalized, key=lambda item: item["evalCaseId"])


def register_eval_set(
    *,
    eval_set_id: str,
    eval_set_version: str,
    cases: Iterable[Mapping[str, Any]],
    created_by: str,
) -> Dict[str, Any]:
    ensure_tables()
    set_id = str(eval_set_id or "").strip()
    set_version = str(eval_set_version or "").strip()
    actor = str(created_by or "").strip()
    if not set_id or not set_version or not actor:
        raise ValueError("eval_set_id, eval_set_version and created_by are required")
    normalized_cases = _normalize_cases(cases)
    identity = {
        "schema": EVAL_SET_SCHEMA,
        "version": VERSION,
        "evalSetId": set_id,
        "evalSetVersion": set_version,
        "cases": normalized_cases,
    }
    eval_set_hash = hash_value(identity)
    created_at = _now()
    with connect() as conn:
        existing = conn.execute(
            "SELECT eval_set_hash FROM rag_eval_sets WHERE eval_set_id = ? AND eval_set_version = ?",
            (set_id, set_version),
        ).fetchone()
        if existing and str(existing["eval_set_hash"]) != eval_set_hash:
            raise ValueError("immutable EvalSet version already exists with different content")
        conn.execute(
            """
            INSERT OR IGNORE INTO rag_eval_sets(
                eval_set_hash, eval_set_id, eval_set_version, cases_json, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (eval_set_hash, set_id, set_version, _canonical(normalized_cases), actor, created_at),
        )
        conn.commit()
    return {**identity, "evalSetHash": eval_set_hash, "createdBy": actor, "createdAt": created_at}


def eval_set(eval_set_hash: str) -> Dict[str, Any]:
    ensure_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM rag_eval_sets WHERE eval_set_hash = ?", (str(eval_set_hash),)).fetchone()
    if not row:
        return {}
    return {
        "schema": EVAL_SET_SCHEMA,
        "version": VERSION,
        "evalSetId": str(row["eval_set_id"]),
        "evalSetVersion": str(row["eval_set_version"]),
        "evalSetHash": str(row["eval_set_hash"]),
        "cases": json.loads(str(row["cases_json"])),
        "createdBy": str(row["created_by"]),
        "createdAt": str(row["created_at"]),
    }


def list_eval_sets(*, limit: int = 100) -> list[Dict[str, Any]]:
    ensure_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT eval_set_hash FROM rag_eval_sets ORDER BY created_at DESC, eval_set_hash DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [eval_set(str(row["eval_set_hash"])) for row in rows]


def _revision_states(revision_ids: Sequence[str]) -> Dict[str, str | None]:
    values = sorted({str(value) for value in revision_ids if str(value)})
    if not values:
        return {}
    marks = ",".join("?" for _ in values)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT revision_id, lifecycle_state FROM rag_knowledge_revision_state WHERE revision_id IN ({marks})",
            tuple(values),
        ).fetchall()
    found = {str(row["revision_id"]): str(row["lifecycle_state"]) for row in rows}
    return {value: found.get(value) for value in values}


def _compute_metrics(cases: Sequence[Mapping[str, Any]], case_results: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_id = {str(dict(item).get("evalCaseId") or ""): dict(item) for item in case_results}
    if len(by_id) != len(case_results):
        raise ValueError("duplicate eval case result")
    expected_ids = {str(dict(case).get("evalCaseId") or "") for case in cases}
    if set(by_id) != expected_ids:
        raise ValueError("EvalRun must contain exactly one result for every EvalSet case")

    evaluable = 0
    hit1 = hit3 = hit5 = 0
    reciprocal_rank = 0.0
    zero_hits = 0
    abstention_correct = 0
    abstention_total = 0
    stale_leaks: set[str] = set()
    superseded_leaks: set[str] = set()
    normalized_results: list[Dict[str, Any]] = []

    for case in cases:
        case_id = str(case["evalCaseId"])
        result = by_id[case_id]
        matched = [str(value) for value in result.get("matchedRevisionIds") or [] if str(value)]
        if len(matched) != len(set(matched)):
            raise ValueError(f"duplicate matched revision in {case_id}")
        states = _revision_states(matched)
        stale_leaks.update(revision_id for revision_id, state in states.items() if state == "stale")
        superseded_leaks.update(revision_id for revision_id, state in states.items() if state == "superseded")
        if not matched:
            zero_hits += 1

        expected_abstention = bool(case.get("expectedAbstention"))
        relevant = set(str(value) for value in case.get("expectedRelevantRevisionIds") or [])
        if expected_abstention:
            abstention_total += 1
            if not matched:
                abstention_correct += 1
        elif relevant:
            evaluable += 1
            ranks = [index + 1 for index, revision_id in enumerate(matched) if revision_id in relevant]
            if any(rank <= 1 for rank in ranks):
                hit1 += 1
            if any(rank <= 3 for rank in ranks):
                hit3 += 1
            if any(rank <= 5 for rank in ranks):
                hit5 += 1
            if ranks:
                reciprocal_rank += 1.0 / min(ranks)

        normalized_results.append({
            "evalCaseId": case_id,
            "queryFingerprint": str(result.get("queryFingerprint") or ""),
            "retrievalReceiptHash": str(result.get("retrievalReceiptHash") or ""),
            "matchedRevisionIds": matched,
        })

    total = len(cases)
    return {
        "caseCount": total,
        "groundTruthRevisionCaseCount": evaluable,
        "zeroHitRate": round(zero_hits / total, 6) if total else None,
        "hitAt1": round(hit1 / evaluable, 6) if evaluable else None,
        "hitAt3": round(hit3 / evaluable, 6) if evaluable else None,
        "hitAt5": round(hit5 / evaluable, 6) if evaluable else None,
        "mrr": round(reciprocal_rank / evaluable, 6) if evaluable else None,
        "abstentionAccuracy": round(abstention_correct / abstention_total, 6) if abstention_total else None,
        "staleLeakCount": len(stale_leaks),
        "supersededLeakCount": len(superseded_leaks),
        "staleLeakRevisionIds": sorted(stale_leaks),
        "supersededLeakRevisionIds": sorted(superseded_leaks),
        "normalizedCaseResults": sorted(normalized_results, key=lambda item: item["evalCaseId"]),
    }


def record_eval_run(
    *,
    eval_set_hash: str,
    run_role: str,
    manifest: Mapping[str, Any],
    runtime_version: str,
    case_results: Sequence[Mapping[str, Any]],
    judge_evidence: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    ensure_tables()
    role = str(run_role or "").upper()
    if role not in {"BASE", "TARGET"}:
        raise ValueError("run_role must be BASE or TARGET")
    source_set = eval_set(eval_set_hash)
    if not source_set:
        raise ValueError("unknown EvalSet")
    required_manifest = ("indexVersion", "manifestHash", "knowledgeSnapshotHash", "retrievalContractVersion")
    for field in required_manifest:
        if not str(manifest.get(field) or "").strip():
            raise ValueError(f"EvalRun manifest missing {field}")
    runtime = str(runtime_version or "").strip()
    if not runtime:
        raise ValueError("runtime_version is required")
    metrics = _compute_metrics(source_set["cases"], case_results)
    normalized_results = metrics.pop("normalizedCaseResults")
    judge = dict(judge_evidence or {})
    if judge and (not str(judge.get("modelVersion") or "") or not str(judge.get("promptVersion") or "")):
        raise ValueError("LLM judge evidence requires modelVersion and promptVersion")
    binding = {
        "schema": EVAL_RUN_SCHEMA,
        "version": VERSION,
        "evalSetHash": str(eval_set_hash),
        "runRole": role,
        "indexVersion": str(manifest["indexVersion"]),
        "indexManifestHash": str(manifest["manifestHash"]),
        "knowledgeSnapshotHash": str(manifest["knowledgeSnapshotHash"]),
        "retrievalPolicyVersion": str(manifest["retrievalContractVersion"]),
        "runtimeVersion": runtime,
        "metrics": metrics,
        "caseResults": normalized_results,
        "judgeEvidence": judge,
        "llmJudgeSoleReleaseAuthority": False,
        "retrievalEvalSeparatedFromAnswerEval": True,
    }
    eval_run_hash = hash_value(binding)
    created_at = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO rag_eval_runs(
                eval_run_hash, eval_set_hash, run_role, index_version,
                index_manifest_hash, knowledge_snapshot_hash, retrieval_policy_version,
                runtime_version, metrics_json, case_results_json, judge_evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eval_run_hash,
                str(eval_set_hash),
                role,
                binding["indexVersion"],
                binding["indexManifestHash"],
                binding["knowledgeSnapshotHash"],
                binding["retrievalPolicyVersion"],
                runtime,
                _canonical(metrics),
                _canonical(normalized_results),
                _canonical(judge),
                created_at,
            ),
        )
        conn.commit()
    return {**binding, "evalRunHash": eval_run_hash, "createdAt": created_at}


def eval_run(eval_run_hash: str) -> Dict[str, Any]:
    ensure_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM rag_eval_runs WHERE eval_run_hash = ?", (str(eval_run_hash),)).fetchone()
    if not row:
        return {}
    return {
        "schema": EVAL_RUN_SCHEMA,
        "version": VERSION,
        "evalRunHash": str(row["eval_run_hash"]),
        "evalSetHash": str(row["eval_set_hash"]),
        "runRole": str(row["run_role"]),
        "indexVersion": str(row["index_version"]),
        "indexManifestHash": str(row["index_manifest_hash"]),
        "knowledgeSnapshotHash": str(row["knowledge_snapshot_hash"]),
        "retrievalPolicyVersion": str(row["retrieval_policy_version"]),
        "runtimeVersion": str(row["runtime_version"]),
        "metrics": json.loads(str(row["metrics_json"])),
        "caseResults": json.loads(str(row["case_results_json"])),
        "judgeEvidence": json.loads(str(row["judge_evidence_json"])),
        "llmJudgeSoleReleaseAuthority": False,
        "retrievalEvalSeparatedFromAnswerEval": True,
        "createdAt": str(row["created_at"]),
    }


def list_eval_runs(*, limit: int = 100) -> list[Dict[str, Any]]:
    ensure_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT eval_run_hash FROM rag_eval_runs ORDER BY created_at DESC, eval_run_hash DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [eval_run(str(row["eval_run_hash"])) for row in rows]


def compare_base_target(*, base_run_hash: str, target_run_hash: str) -> Dict[str, Any]:
    ensure_tables()
    base = eval_run(base_run_hash)
    target = eval_run(target_run_hash)
    if not base or not target:
        raise ValueError("BASE and TARGET EvalRuns are required")
    if base["runRole"] != "BASE" or target["runRole"] != "TARGET":
        raise ValueError("comparison requires BASE then TARGET roles")
    if base["evalSetHash"] != target["evalSetHash"]:
        raise ValueError("BASE and TARGET must use the same immutable EvalSet")
    b = dict(base["metrics"])
    t = dict(target["metrics"])
    findings: list[str] = []

    if b.get("hitAt3") is not None and t.get("hitAt3") is not None:
        if float(t["hitAt3"]) < float(b["hitAt3"]) - MAX_HIT_AT3_REGRESSION:
            findings.append("hit_at_3_regression")
    if b.get("zeroHitRate") is not None and t.get("zeroHitRate") is not None:
        if float(t["zeroHitRate"]) > float(b["zeroHitRate"]) + MAX_ZERO_HIT_RATE_INCREASE:
            findings.append("zero_hit_rate_regression")
    if int(t.get("staleLeakCount") or 0) > 0:
        findings.append("stale_revision_leak")
    if int(t.get("supersededLeakCount") or 0) > 0:
        findings.append("superseded_revision_leak")

    material = {
        "schema": REGRESSION_SCHEMA,
        "version": VERSION,
        "baseRunHash": base["evalRunHash"],
        "targetRunHash": target["evalRunHash"],
        "evalSetHash": base["evalSetHash"],
        "thresholds": {
            "maximumHitAt3Regression": MAX_HIT_AT3_REGRESSION,
            "maximumZeroHitRateIncrease": MAX_ZERO_HIT_RATE_INCREASE,
            "maximumStaleLeakCount": 0,
            "maximumSupersededLeakCount": 0,
        },
        "baseMetrics": b,
        "targetMetrics": t,
        "findings": findings,
        "verified": not findings,
        "failClosed": True,
        "llmJudgeSoleReleaseAuthority": False,
    }
    comparison_hash = hash_value(material)
    created_at = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO rag_eval_regression_comparisons(
                comparison_hash, base_run_hash, target_run_hash, verified,
                findings_json, comparison_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comparison_hash,
                base["evalRunHash"],
                target["evalRunHash"],
                int(material["verified"]),
                _canonical(findings),
                _canonical(material),
                created_at,
            ),
        )
        conn.commit()
    return {**material, "comparisonHash": comparison_hash, "createdAt": created_at}
