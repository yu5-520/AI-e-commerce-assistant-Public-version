"""V26.9 production System Review bridge with V26.9.B downstream evidence capture.

Python remains the production lifecycle writer. Java READY_NO_AUTHORITY is the one
review/revision calculator: this module freezes graph identities at task admission,
opens the review window only after execution is submitted, collects a later immutable
BusinessFacts Artifact as TARGET, calls the root-bound Java endpoint, verifies its
receipt, and only then asks the existing lifecycle writer to persist SETTLED or
ADJUSTMENT_REQUIRED.

V26.9.B may consume a completed System Review after the A lifecycle write and persist
candidate-only Experience/Evaluation evidence. That downstream write is fail-isolated:
it cannot change the A review decision, execution authority, task lifecycle, Promotion
state, or Knowledge Head. No RAG feedback, model call, current-clock substitution for
stale facts, or legacy recap metric input is allowed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, Iterable
from urllib import error as urlerror
from urllib import request as urlrequest

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services import v269_semantic_graph_service as graphs

VERSION = "26.9.0"
REVIEW_SCHEMA = "v269.system_review.registration.v1"
JAVA_RECEIPT_SCHEMA = "v269.system_review.receipt.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch_millis() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def ensure_review_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS v269_system_reviews (
                task_id TEXT PRIMARY KEY,
                store_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                semantic_contract_hash TEXT NOT NULL,
                decision_graph_hash TEXT NOT NULL,
                plan_graph_hash TEXT NOT NULL,
                operation_graph_hash TEXT NOT NULL,
                frozen_at_millis INTEGER NOT NULL,
                baseline_source_content_hash TEXT,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                last_target_content_hash TEXT,
                review_receipt TEXT,
                revision_directive TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_v269_system_reviews_scope_status "
            "ON v269_system_reviews(store_id,product_id,status,updated_at)"
        )
        conn.commit()


def _plan_review_windows(plan: Dict[str, Any]) -> list[int]:
    nodes = graphs.index(plan)
    values: list[int] = []
    for node in nodes.values():
        if node.get("kind") != "PlanActionNode":
            continue
        window = node.get("reviewWindow") or {}
        value = window.get("durationSeconds") if isinstance(window, dict) else None
        graphs.require(type(value) is int and value > 0, "review_window_invalid")
        values.append(value)
    graphs.require(bool(values), "review_plan_actions_required")
    return values


def _baseline_source_hash(chain: Dict[str, Any]) -> str | None:
    from src.services.artifact_transport_service import resolve_artifact

    executions = chain.get("executions") if isinstance(chain, dict) else None
    for execution in executions or []:
        if not isinstance(execution, dict) or execution.get("agent") != "agent1":
            continue
        ref = str(execution.get("inputArtifactRef") or "")
        if not ref.startswith("ART-"):
            continue
        try:
            envelope = resolve_artifact(ref)
        except Exception:
            continue
        payload = envelope.get("payload") if isinstance(envelope, dict) else None
        facts = payload.get("BusinessFacts") if isinstance(payload, dict) else None
        value = str((facts or {}).get("sourceContentHash") or "").strip()
        if value:
            return value
    return None


def register_review_in_conn(
    conn: Any,
    *,
    task: Dict[str, Any],
    decision: Dict[str, Any],
    chain: Dict[str, Any],
    frozen_at_millis: int | None = None,
) -> Dict[str, Any]:
    """Freeze BASE identity inside caller's task-admission transaction.

    The initial frozen time is an audit registration time only. mark_review_pending()
    replaces it with the execution-completion time before TARGET observations become
    eligible. The caller must create tables before BEGIN IMMEDIATE.
    """
    task_id = str(task.get("taskId") or task.get("id") or "").strip()
    graphs.require(bool(task_id), "review_task_id_required")
    decision_graph = decision.get("DecisionGraph")
    plan_graph = decision.get("PlanGraph")
    operation_graph = decision.get("OperationGraph")
    graphs.index(decision_graph); graphs.index(plan_graph); graphs.index(operation_graph)
    graphs.map_task(decision_graph, decision["actionAdmission"], plan_graph, operation_graph)
    fact_values = decision.get("factValues")
    graphs.require(isinstance(fact_values, dict) and fact_values, "review_baseline_facts_required")
    _plan_review_windows(plan_graph)
    registered = int(frozen_at_millis if frozen_at_millis is not None else _epoch_millis())
    contract_hash = str(plan_graph.get("contractHash") or "")
    graphs.require(contract_hash == graphs.digest(graphs.contract()), "review_semantic_contract_hash")
    baseline_source_hash = _baseline_source_hash(chain)
    graphs.require(bool(baseline_source_hash), "review_baseline_source_content_hash_required")
    payload = {
        "schema": REVIEW_SCHEMA,
        "version": VERSION,
        "taskId": task_id,
        "storeId": str(decision.get("storeId") or ""),
        "productId": str(decision.get("productId") or ""),
        "semanticContractHash": contract_hash,
        "DecisionGraph": deepcopy(decision_graph),
        "PlanGraph": deepcopy(plan_graph),
        "OperationGraph": deepcopy(operation_graph),
        "baselineFacts": deepcopy(fact_values),
        "baselineSourceContentHash": baseline_source_hash,
        "registeredAtMillis": registered,
        "frozenAtMillis": registered,
        "successfulNodeHashes": [],
        "ragFeedbackAllowed": False,
        "evaluationPlaneActive": False,
    }
    now = _now()
    conn.execute(
        """INSERT INTO v269_system_reviews(
           task_id,store_id,product_id,semantic_contract_hash,decision_graph_hash,
           plan_graph_hash,operation_graph_hash,frozen_at_millis,baseline_source_content_hash,
           status,payload,last_target_content_hash,review_receipt,revision_directive,
           created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,'AWAITING_EXECUTION',?,NULL,NULL,NULL,?,?)
           ON CONFLICT(task_id) DO UPDATE SET
             semantic_contract_hash=excluded.semantic_contract_hash,
             decision_graph_hash=excluded.decision_graph_hash,
             plan_graph_hash=excluded.plan_graph_hash,
             operation_graph_hash=excluded.operation_graph_hash,
             baseline_source_content_hash=excluded.baseline_source_content_hash,
             payload=excluded.payload,
             updated_at=excluded.updated_at
           WHERE v269_system_reviews.status IN ('AWAITING_EXECUTION','REVIEW_UNAVAILABLE')""",
        (
            task_id, payload["storeId"], payload["productId"], contract_hash,
            decision_graph["graphHash"], plan_graph["graphHash"], operation_graph["graphHash"],
            registered, baseline_source_hash, dumps(payload), now, now,
        ),
    )
    return {
        "schema": REVIEW_SCHEMA,
        "version": VERSION,
        "taskId": task_id,
        "registeredAtMillis": registered,
        "PlanGraphHash": plan_graph["graphHash"],
        "baselineSourceContentHash": baseline_source_hash,
        "status": "AWAITING_EXECUTION",
    }


def mark_review_pending(task_id: str) -> Dict[str, Any]:
    """Start PlanAction review windows exactly when execution becomes reviewable."""
    ensure_review_tables()
    now = _now()
    ready_millis = _epoch_millis()
    with connect() as conn:
        row = conn.execute(
            "SELECT status,payload,frozen_at_millis FROM v269_system_reviews WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "status": "review_not_registered", "taskId": task_id}
        if row["status"] in {"PENDING", "WAITING_TIME", "WAITING_EVIDENCE"}:
            return {
                "ok": True,
                "status": row["status"],
                "taskId": task_id,
                "reviewWindowStartMillis": int(row["frozen_at_millis"]),
                "idempotentHit": True,
            }
        if row["status"] != "AWAITING_EXECUTION":
            return {"ok": False, "status": row["status"], "taskId": task_id}
        payload = loads(row["payload"])
        payload["reviewWindowStartMillis"] = ready_millis
        payload["frozenAtMillis"] = ready_millis
        conn.execute(
            """UPDATE v269_system_reviews
               SET status='PENDING',frozen_at_millis=?,payload=?,updated_at=?
               WHERE task_id=? AND status='AWAITING_EXECUTION'""",
            (ready_millis, dumps(payload), now, task_id),
        )
        conn.commit()
    return {
        "ok": True,
        "status": "PENDING",
        "taskId": task_id,
        "reviewWindowStartMillis": ready_millis,
    }


def mark_successful_nodes(task_id: str, node_hashes: Iterable[str]) -> Dict[str, Any]:
    """Accept only explicit execution evidence supplied by a registered execution path."""
    ensure_review_tables()
    values = sorted({str(value) for value in node_hashes if str(value).startswith("sha256:")})
    with connect() as conn:
        row = conn.execute("SELECT payload,status FROM v269_system_reviews WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            return {"ok": False, "status": "review_not_registered"}
        payload = loads(row["payload"])
        all_hashes = {
            node["nodeHash"]
            for graph_name in ("DecisionGraph", "PlanGraph", "OperationGraph")
            for node in (payload.get(graph_name) or {}).get("nodes", [])
            if isinstance(node, dict) and isinstance(node.get("nodeHash"), str)
        }
        graphs.require(set(values) <= all_hashes, "review_success_node_unknown")
        payload["successfulNodeHashes"] = values
        conn.execute(
            "UPDATE v269_system_reviews SET payload=?,updated_at=? WHERE task_id=?",
            (dumps(payload), _now(), task_id),
        )
        conn.commit()
    return {"ok": True, "taskId": task_id, "successfulNodeHashes": values}


def _target_fact_candidates(fact_values: Dict[str, Any]) -> Dict[str, list[tuple[str, Dict[str, Any]]]]:
    result: Dict[str, list[tuple[str, Dict[str, Any]]]] = {}
    for ref, value in fact_values.items():
        if not isinstance(ref, str) or not isinstance(value, dict):
            continue
        metric = ""
        rank = 9
        if ref.startswith("fact:metric:"):
            metric = ref[len("fact:metric:"):]
            rank = 0
        elif ref.startswith("fact:signal:") and ref.endswith(":current"):
            metric = ref[len("fact:signal:"):-len(":current")]
            rank = 1
        if not metric:
            continue
        result.setdefault(metric.casefold(), []).append((f"{rank}:{ref}", value))
    for entries in result.values():
        entries.sort(key=lambda item: item[0])
    return result


def _target_facts(payload: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    business = candidate.get("BusinessFacts") or {}
    fact_values = business.get("factValues") if isinstance(business, dict) else None
    if not isinstance(fact_values, dict):
        fact_values = {}
    candidates = _target_fact_candidates(fact_values)
    result: Dict[str, Any] = {}
    for node in (payload.get("PlanGraph") or {}).get("nodes", []):
        if not isinstance(node, dict) or node.get("kind") != "PlanActionNode":
            continue
        for metric, baseline in (node.get("baseline") or {}).items():
            entries = candidates.get(str(metric).casefold()) or []
            if not entries:
                continue
            ranked_ref, target = entries[0]
            ref = ranked_ref.split(":", 1)[1]
            if target.get("unit") != (baseline or {}).get("unit"):
                continue
            result[str(metric)] = {
                "value": target.get("value"),
                "unit": target.get("unit"),
                "sourceRef": ref,
            }
    return result


def _java_url() -> str:
    base = str(os.getenv("V24_AUTHORITY_URL", "http://127.0.0.1:39024")).rstrip("/")
    return base + "/v1/v269/system-review"


def _call_java(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    req = urlrequest.Request(
        _java_url(), data=raw,
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=float(os.getenv("V269_REVIEW_TIMEOUT_SECONDS", "8"))) as response:
            body = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            value = json.loads(body)
        except Exception:
            value = {}
        reason = str(value.get("reason") or f"http_{exc.code}")
        if exc.code == 503:
            raise RuntimeError("v269_java_review_unavailable:" + reason) from exc
        raise ValueError("v269_java_review_rejected:" + reason) from exc
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("v269_java_review_unavailable:" + type(exc).__name__) from exc
    value = json.loads(body)
    graphs.require(isinstance(value, dict), "java_review_response_invalid")
    return value


def _verify_java_receipt(receipt: Dict[str, Any], payload: Dict[str, Any], target_facts: Dict[str, Any]) -> None:
    graphs.require(receipt.get("schema") == JAVA_RECEIPT_SCHEMA, "java_review_receipt_schema")
    graphs.require(receipt.get("version") == VERSION, "java_review_receipt_version")
    graphs.require(receipt.get("taskId") == payload.get("taskId"), "java_review_task_mismatch")
    graphs.require(receipt.get("productId") == payload.get("productId"), "java_review_product_mismatch")
    graphs.require(receipt.get("DecisionGraphHash") == payload["DecisionGraph"]["graphHash"], "java_review_decision_hash")
    graphs.require(receipt.get("PlanGraphHash") == payload["PlanGraph"]["graphHash"], "java_review_plan_hash")
    graphs.require(receipt.get("OperationGraphHash") == payload["OperationGraph"]["graphHash"], "java_review_operation_hash")
    graphs.require(receipt.get("targetFactsHash") == graphs.digest(target_facts), "java_review_target_hash")
    graphs.require(receipt.get("rootBound") is True, "java_review_not_root_bound")
    graphs.require(receipt.get("productionMutationPerformed") is False, "java_review_mutation_forbidden")
    graphs.require(receipt.get("ragFeedbackPerformed") is False, "java_review_rag_forbidden")
    declared = receipt.get("receiptHash")
    graphs.require(declared == graphs.digest({k: v for k, v in receipt.items() if k != "receiptHash"}), "java_review_receipt_hash")


def pending_review_for_scope(store_id: str, product_id: str) -> list[Dict[str, Any]]:
    ensure_review_tables()
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM v269_system_reviews
               WHERE store_id=? AND product_id=?
                 AND status IN ('PENDING','WAITING_TIME','WAITING_EVIDENCE','REVIEW_UNAVAILABLE')
               ORDER BY frozen_at_millis ASC""",
            (store_id, product_id),
        ).fetchall()
    return [dict(row) for row in rows]


def _write_review_status(task_id: str, status: str, source_hash: str, *, receipt: Dict[str, Any] | None = None, directive: Dict[str, Any] | None = None) -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE v269_system_reviews
               SET status=?,last_target_content_hash=?,review_receipt=?,revision_directive=?,updated_at=?
               WHERE task_id=?""",
            (
                status,
                source_hash,
                dumps(receipt) if receipt is not None else None,
                dumps(directive) if directive is not None else None,
                _now(),
                task_id,
            ),
        )
        conn.commit()


def _write_v269b_evaluation_status(task_id: str, evidence: Dict[str, Any]) -> None:
    """Attach B evidence to the existing review payload without changing A status."""
    with connect() as conn:
        row = conn.execute(
            "SELECT payload FROM v269_system_reviews WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if not row:
            return
        payload = loads(row["payload"])
        payload["v269bExperienceEvaluation"] = deepcopy(evidence)
        conn.execute(
            "UPDATE v269_system_reviews SET payload=?,updated_at=? WHERE task_id=?",
            (dumps(payload), _now(), task_id),
        )
        conn.commit()


def _v269b_runtime_enabled() -> bool:
    """Use the B manifest as the only activation switch; env is candidate-only."""
    try:
        from src.services import v269_experience_store_service as experience_store
        cfg = experience_store.manifest()
    except Exception:
        return False
    if cfg.get("rolloutStatus") == "active":
        return True
    env = str(cfg.get("candidateRuntimeEnv") or "V269B_CANDIDATE_RUNTIME")
    return str(os.getenv(env, "")).strip().lower() in {"1", "true", "yes", "on"}


def _record_v269b_evaluation(
    *,
    payload: Dict[str, Any],
    target: Dict[str, Any],
    source_content_hash: str,
    receipt: Dict[str, Any],
    review_status: str,
) -> Dict[str, Any]:
    """Downstream-only B capture. Never throw into the A review/lifecycle path."""
    if review_status not in {"SETTLED", "ADJUSTMENT_REQUIRED"}:
        return {"status": "NOT_APPLICABLE", "promotionPerformed": False, "knowledgeHeadMutated": False}
    if not _v269b_runtime_enabled():
        evidence = {
            "status": "NOT_ACTIVE",
            "version": "26.9.B.1",
            "promotionPerformed": False,
            "knowledgeHeadMutated": False,
        }
        _write_v269b_evaluation_status(str(payload.get("taskId") or ""), evidence)
        return evidence
    try:
        from src.services import v269_evaluation_plane_service as evaluation
        package = {
            "taskId": payload["taskId"],
            "storeId": payload["storeId"],
            "productId": payload["productId"],
            "semanticContractVersion": VERSION,
            "DecisionGraph": deepcopy(payload["DecisionGraph"]),
            "PlanGraph": deepcopy(payload["PlanGraph"]),
            "OperationGraph": deepcopy(payload["OperationGraph"]),
            "evidenceRefs": sorted({
                value for value in (
                    str(payload.get("baselineSourceContentHash") or ""),
                    str(source_content_hash or ""),
                ) if value
            }),
        }
        candidate_receipt = evaluation.record_system_review_candidate(
            package,
            target_facts=deepcopy(target),
            target_source_content_hash=source_content_hash,
            review_receipt=deepcopy(receipt),
            review_status=review_status,
        )
        evidence = {
            "status": "RECORDED",
            "version": "26.9.B.1",
            "receipt": candidate_receipt,
            "promotionPerformed": False,
            "knowledgeHeadMutated": False,
        }
    except Exception as exc:
        evidence = {
            "status": "FAILED",
            "version": "26.9.B.1",
            "reason": str(exc)[:800],
            "promotionPerformed": False,
            "knowledgeHeadMutated": False,
        }
    _write_v269b_evaluation_status(str(payload.get("taskId") or ""), evidence)
    return evidence


def observe_candidate_facts(
    candidate: Dict[str, Any],
    *,
    source_content_hash: str,
    observed_at_millis: int | None = None,
) -> Dict[str, Any]:
    """Use only a genuinely later immutable BusinessFacts Artifact as TARGET."""
    store_id = str(candidate.get("storeId") or "")
    product_id = str(candidate.get("productId") or "")
    if not store_id or not product_id:
        return {"status": "NO_SCOPE"}
    rows = pending_review_for_scope(store_id, product_id)
    rows = [row for row in rows if str(row.get("baseline_source_content_hash") or "") != str(source_content_hash or "")]
    if not rows:
        return {"status": "NO_PENDING_REVIEW"}
    if len(rows) != 1:
        return {"status": "AMBIGUOUS_PENDING_REVIEWS", "taskIds": [row["task_id"] for row in rows]}
    row = rows[0]
    observed = int(observed_at_millis or 0)
    if observed <= 0:
        return {"status": "TARGET_TIME_NOT_RECORDED", "taskId": row["task_id"]}
    if observed <= int(row.get("frozen_at_millis") or 0):
        return {
            "status": "TARGET_NOT_AFTER_EXECUTION",
            "taskId": row["task_id"],
            "observedAtMillis": observed,
            "reviewWindowStartMillis": int(row.get("frozen_at_millis") or 0),
        }
    payload = loads(row["payload"])
    target = _target_facts(payload, candidate)
    if not target:
        _write_review_status(row["task_id"], "WAITING_EVIDENCE", source_content_hash)
        return {"status": "WAITING_EVIDENCE", "taskId": row["task_id"], "reason": "target_metrics_not_present"}
    if row.get("last_target_content_hash") == source_content_hash:
        receipt = loads(row["review_receipt"]) if row.get("review_receipt") else None
        return {
            "status": row["status"],
            "idempotentHit": True,
            "receipt": receipt,
            "experienceEvaluation": deepcopy(payload.get("v269bExperienceEvaluation")),
        }
    request_payload = {
        "schema": "v269.system_review.request.v1",
        "taskId": payload["taskId"],
        "productId": payload["productId"],
        "semanticContractHash": payload["semanticContractHash"],
        "DecisionGraph": payload["DecisionGraph"],
        "PlanGraph": payload["PlanGraph"],
        "OperationGraph": payload["OperationGraph"],
        "baselineFacts": payload["baselineFacts"],
        "targetFacts": target,
        "frozenAtMillis": int(payload["frozenAtMillis"]),
        "observedAtMillis": observed,
        "successfulNodeHashes": list(payload.get("successfulNodeHashes") or []),
    }
    target_hash = graphs.digest(target)
    try:
        receipt = _call_java(request_payload)
        _verify_java_receipt(receipt, payload, target)
    except ValueError as exc:
        _write_review_status(row["task_id"], "REVIEW_REJECTED", source_content_hash)
        return {"status": "REVIEW_REJECTED", "taskId": row["task_id"], "reason": str(exc)[:500]}
    except Exception as exc:
        _write_review_status(row["task_id"], "REVIEW_UNAVAILABLE", source_content_hash)
        return {"status": "REVIEW_UNAVAILABLE", "taskId": row["task_id"], "reason": str(exc)[:500]}

    status = str(receipt.get("aggregateDecision") or "")
    directive = receipt.get("revisionDirective") if status == "ADJUSTMENT_REQUIRED" else None
    _write_review_status(row["task_id"], status, source_content_hash, receipt=receipt, directive=directive)

    lifecycle = None
    experience_evaluation = None
    if status in {"SETTLED", "ADJUSTMENT_REQUIRED"}:
        from src.services.task_lifecycle_state_machine_service import transition_lifecycle_task
        lifecycle = transition_lifecycle_task(
            row["task_id"],
            "system_review_settled" if status == "SETTLED" else "system_review_adjustment",
            actor_user_id="system:v269-review",
            payload={
                "reviewHash": receipt.get("receiptHash"),
                "reviewDecision": status,
                "targetSourceContentHash": source_content_hash,
                "revisionHash": (directive or {}).get("revisionHash") if directive else None,
            },
        )
        # B is intentionally after the A lifecycle writer. Any B failure is recorded
        # as evidence status and cannot roll back or reinterpret the A review result.
        experience_evaluation = _record_v269b_evaluation(
            payload=payload,
            target=target,
            source_content_hash=source_content_hash,
            receipt=receipt,
            review_status=status,
        )
    return {
        "status": status,
        "taskId": row["task_id"],
        "receipt": receipt,
        "revisionDirective": directive,
        "parentDecisionGraph": deepcopy(payload["DecisionGraph"]) if directive else None,
        "lifecycle": lifecycle,
        "targetFactsHash": target_hash,
        "experienceEvaluation": experience_evaluation,
        "ragFeedbackPerformed": False,
    }


__all__ = [
    "VERSION",
    "ensure_review_tables",
    "register_review_in_conn",
    "mark_review_pending",
    "mark_successful_nodes",
    "pending_review_for_scope",
    "observe_candidate_facts",
]
