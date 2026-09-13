"""V26.9.A production System Review bridge.

Python remains the production lifecycle writer. Java READY_NO_AUTHORITY is the one
review/revision calculator: this module freezes graph identities at task admission,
collects later BusinessFacts as TARGET observations, calls the root-bound Java endpoint,
verifies its immutable receipt, and only then asks the existing task lifecycle state
machine to persist SETTLED or ADJUSTMENT_REQUIRED.

No RAG feedback, Evaluation Plane, model call, or legacy recap metric input is allowed.
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
    value = str(chain.get("baselineSourceContentHash") or "").strip()
    return value or None


def register_review_in_conn(
    conn: Any,
    *,
    task: Dict[str, Any],
    decision: Dict[str, Any],
    chain: Dict[str, Any],
    frozen_at_millis: int | None = None,
) -> Dict[str, Any]:
    """Register review identity inside the caller's task-admission transaction."""
    ensure_review_tables()
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
    frozen = int(frozen_at_millis if frozen_at_millis is not None else _epoch_millis())
    contract_hash = str(plan_graph.get("contractHash") or "")
    graphs.require(contract_hash == graphs.digest(graphs.contract()), "review_semantic_contract_hash")
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
        "baselineSourceContentHash": _baseline_source_hash(chain),
        "frozenAtMillis": frozen,
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
           ) VALUES(?,?,?,?,?,?,?,?,?,'PENDING',?,NULL,NULL,NULL,?,?)
           ON CONFLICT(task_id) DO UPDATE SET
             semantic_contract_hash=excluded.semantic_contract_hash,
             decision_graph_hash=excluded.decision_graph_hash,
             plan_graph_hash=excluded.plan_graph_hash,
             operation_graph_hash=excluded.operation_graph_hash,
             payload=excluded.payload,
             updated_at=excluded.updated_at
           WHERE v269_system_reviews.status IN ('PENDING','WAITING_TIME','WAITING_EVIDENCE','REVIEW_UNAVAILABLE')""",
        (
            task_id, payload["storeId"], payload["productId"], contract_hash,
            decision_graph["graphHash"], plan_graph["graphHash"], operation_graph["graphHash"],
            frozen, payload["baselineSourceContentHash"], dumps(payload), now, now,
        ),
    )
    return {
        "schema": REVIEW_SCHEMA,
        "version": VERSION,
        "taskId": task_id,
        "frozenAtMillis": frozen,
        "PlanGraphHash": plan_graph["graphHash"],
        "baselineSourceContentHash": payload["baselineSourceContentHash"],
        "status": "PENDING",
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


def observe_candidate_facts(
    candidate: Dict[str, Any],
    *,
    source_content_hash: str,
    observed_at_millis: int | None = None,
) -> Dict[str, Any]:
    """Use a later real BusinessFacts projection as TARGET for one active graph task.

    The same report/content hash that created BASE is never accepted as TARGET. If more
    than one active task exists for the same product, review fails closed instead of
    guessing which task owns the observation.
    """
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
    payload = loads(row["payload"])
    target = _target_facts(payload, candidate)
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
        "observedAtMillis": int(observed_at_millis if observed_at_millis is not None else _epoch_millis()),
        "successfulNodeHashes": list(payload.get("successfulNodeHashes") or []),
    }
    target_hash = graphs.digest(target)
    if row.get("last_target_content_hash") == source_content_hash:
        receipt = loads(row["review_receipt"]) if row.get("review_receipt") else None
        return {"status": row["status"], "idempotentHit": True, "receipt": receipt}
    try:
        receipt = _call_java(request_payload)
        _verify_java_receipt(receipt, payload, target)
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                "UPDATE v269_system_reviews SET status='REVIEW_UNAVAILABLE',last_target_content_hash=?,updated_at=? WHERE task_id=?",
                (source_content_hash, _now(), row["task_id"]),
            )
            conn.commit()
        return {"status": "REVIEW_UNAVAILABLE", "taskId": row["task_id"], "reason": str(exc)[:500]}

    status = str(receipt.get("aggregateDecision") or "")
    directive = receipt.get("revisionDirective") if status == "ADJUSTMENT_REQUIRED" else None
    with connect() as conn:
        conn.execute(
            """UPDATE v269_system_reviews
               SET status=?,last_target_content_hash=?,review_receipt=?,revision_directive=?,updated_at=?
               WHERE task_id=?""",
            (status, source_content_hash, dumps(receipt), dumps(directive) if directive else None, _now(), row["task_id"]),
        )
        conn.commit()

    lifecycle = None
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
    return {
        "status": status,
        "taskId": row["task_id"],
        "receipt": receipt,
        "revisionDirective": directive,
        "parentDecisionGraph": deepcopy(payload["DecisionGraph"]) if directive else None,
        "lifecycle": lifecycle,
        "targetFactsHash": target_hash,
        "ragFeedbackPerformed": False,
    }


__all__ = [
    "VERSION",
    "ensure_review_tables",
    "register_review_in_conn",
    "mark_successful_nodes",
    "pending_review_for_scope",
    "observe_candidate_facts",
]
