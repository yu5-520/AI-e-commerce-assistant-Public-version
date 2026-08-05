"""V6.9 execution review and RAG case memory service.

V6.8 records execution feedback. V6.9 turns that feedback into structured review
cases: what was planned, what was approved, what was executed, whether the result
met the guardrails, and what should be stored as reusable company knowledge.
This is demo-stage RAG memory: it stores structured cases locally and does not
modify the original indicator rules automatically.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads

EXECUTION_REVIEW_VERSION = "6.9.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}".upper()


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "").replace("¥", "").strip())
    except (TypeError, ValueError):
        return default


def ensure_execution_review_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_reviews_v6 (
                review_id TEXT PRIMARY KEY,
                execution_result_id TEXT,
                approval_flow_id TEXT,
                product_id TEXT,
                store_id TEXT,
                review_status TEXT NOT NULL,
                outcome_label TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_case_memory_v6 (
                case_id TEXT PRIMARY KEY,
                review_id TEXT,
                product_id TEXT,
                store_id TEXT,
                case_type TEXT NOT NULL,
                title TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_reviews_product_v6 ON execution_reviews_v6(product_id, store_id, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_case_memory_type_v6 ON rag_case_memory_v6(case_type, created_at)")
        conn.commit()


def _load_execution_result(result_id: str) -> Dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT payload FROM execution_results_v6 WHERE result_id = ?", (result_id,)).fetchone()
    return loads(row["payload"]) if row else None


def _load_approval_flow(flow_id: str | None) -> Dict[str, Any] | None:
    if not flow_id:
        return None
    with connect() as conn:
        row = conn.execute("SELECT payload FROM approval_flows_v6 WHERE flow_id = ?", (flow_id,)).fetchone()
    return loads(row["payload"]) if row else None


def _latest_execution_results(limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT payload FROM execution_results_v6 ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [loads(row["payload"]) for row in rows]


def _planned_budget(flow: Dict[str, Any] | None) -> Dict[str, float]:
    source_task = (flow or {}).get("sourceTask") or {}
    gate = source_task.get("permissionBudgetGate") or (flow or {}).get("permissionBudgetGate") or {}
    return {
        "plannedAdBudget": _as_float(source_task.get("suggestedAdBudget") or gate.get("suggestedAdBudget")),
        "plannedStockBudget": _as_float(source_task.get("suggestedStockBudget") or gate.get("suggestedStockBudget")),
        "plannedTotalBudget": _as_float(source_task.get("suggestedTotalBudget") or gate.get("suggestedTotalBudget")),
    }


def _evaluate_result(result: Dict[str, Any], flow: Dict[str, Any] | None) -> Dict[str, Any]:
    planned = _planned_budget(flow)
    actual_ad = _as_float(result.get("actualAdSpend"))
    actual_stock = _as_float(result.get("actualStockPurchase"))
    actual_total = actual_ad + actual_stock
    planned_total = planned.get("plannedTotalBudget") or 0
    over_budget = planned_total > 0 and actual_total > planned_total * 1.1
    under_reported = actual_total <= 0
    if under_reported:
        outcome = "待补充"
    elif over_budget:
        outcome = "超预算"
    else:
        outcome = "已执行"
    return {
        **planned,
        "actualAdSpend": actual_ad,
        "actualStockPurchase": actual_stock,
        "actualTotal": round(actual_total, 2),
        "overBudget": over_budget,
        "budgetUsageRate": round(actual_total / planned_total, 4) if planned_total else None,
        "outcomeLabel": outcome,
        "reviewStatus": "needs_followup" if under_reported or over_budget else "reviewed",
    }


def create_review_from_execution_result(result_id: str, actor_role_id: str = "manager", note: str | None = None) -> Dict[str, Any]:
    """Create one structured review case from an execution result."""
    ensure_execution_review_tables()
    result = _load_execution_result(result_id)
    if not result:
        raise ValueError(f"execution result not found: {result_id}")
    flow = _load_approval_flow(result.get("approvalFlowId"))
    evaluation = _evaluate_result(result, flow)
    source_task = (flow or {}).get("sourceTask") or {}
    now = now_iso()
    review = {
        "version": EXECUTION_REVIEW_VERSION,
        "reviewId": make_id("EREV"),
        "executionResultId": result_id,
        "approvalFlowId": result.get("approvalFlowId"),
        "executionTaskId": result.get("executionTaskId"),
        "productId": result.get("productId"),
        "storeId": result.get("storeId"),
        "reviewStatus": evaluation["reviewStatus"],
        "outcomeLabel": evaluation["outcomeLabel"],
        "evaluation": evaluation,
        "sourceRiskTask": source_task,
        "executionResult": result,
        "actorRoleId": actor_role_id,
        "note": note or "执行结果已进入复盘沉淀。",
        "lessons": [
            "审批、执行、结果回写已形成闭环。",
            "实际金额与申请额度需要在复盘中对齐。" if evaluation.get("budgetUsageRate") is not None else "缺少计划额度，需补齐审批额度来源。",
            "超预算执行需要升级为复核案例。" if evaluation.get("overBudget") else "当前执行金额未明显超出审批额度。",
        ],
        "createdAt": now,
        "updatedAt": now,
        "rule": "V6.9 把执行结果沉淀成复盘案例和RAG记忆，但不自动改写公司规则。",
    }
    case = {
        "version": EXECUTION_REVIEW_VERSION,
        "caseId": make_id("RCASE"),
        "reviewId": review["reviewId"],
        "productId": review.get("productId"),
        "storeId": review.get("storeId"),
        "caseType": "execution_review",
        "title": f"{review.get('productId') or '商品'} · {review['outcomeLabel']}复盘案例",
        "summary": review["note"],
        "keywords": ["执行回写", "审批复盘", review["outcomeLabel"], source_task.get("riskDomain") or "趋势"],
        "payload": review,
        "createdAt": now,
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO execution_reviews_v6 (
                review_id, execution_result_id, approval_flow_id, product_id, store_id,
                review_status, outcome_label, payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review["reviewId"], result_id, review.get("approvalFlowId"), review.get("productId"), review.get("storeId"),
                review["reviewStatus"], review["outcomeLabel"], dumps(review), now, now,
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO rag_case_memory_v6 (case_id, review_id, product_id, store_id, case_type, title, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (case["caseId"], review["reviewId"], case.get("productId"), case.get("storeId"), case["caseType"], case["title"], dumps(case), now),
        )
        conn.commit()
    return {"version": EXECUTION_REVIEW_VERSION, "review": review, "ragCase": case}


def generate_reviews_for_recent_results(limit: int = 30, actor_role_id: str = "manager") -> Dict[str, Any]:
    """Generate reviews for recent execution results that have not yet been reviewed."""
    ensure_execution_review_tables()
    results = _latest_execution_results(limit=limit)
    created = []
    skipped = 0
    with connect() as conn:
        existing = {row["execution_result_id"] for row in conn.execute("SELECT execution_result_id FROM execution_reviews_v6").fetchall()}
    for result in results:
        result_id = result.get("resultId")
        if not result_id or result_id in existing:
            skipped += 1
            continue
        created.append(create_review_from_execution_result(str(result_id), actor_role_id=actor_role_id))
    return {
        "version": EXECUTION_REVIEW_VERSION,
        "createdReviewCount": len(created),
        "skippedCount": skipped,
        "reviews": created,
        "rule": "V6.9 批量把执行回写转成复盘案例和RAG记忆。",
    }


def execution_review_summary(limit: int = 30) -> Dict[str, Any]:
    ensure_execution_review_tables()
    with connect() as conn:
        reviews = conn.execute("SELECT * FROM execution_reviews_v6 ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        cases = conn.execute("SELECT * FROM rag_case_memory_v6 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    review_items = [loads(row["payload"]) for row in reviews]
    case_items = [loads(row["payload"]) for row in cases]
    by_status: Dict[str, int] = defaultdict(int)
    by_outcome: Dict[str, int] = defaultdict(int)
    for item in review_items:
        by_status[str(item.get("reviewStatus") or "unknown")] += 1
        by_outcome[str(item.get("outcomeLabel") or "unknown")] += 1
    return {
        "version": EXECUTION_REVIEW_VERSION,
        "reviewCount": len(review_items),
        "caseCount": len(case_items),
        "byStatus": dict(by_status),
        "byOutcome": dict(by_outcome),
        "latestReviews": review_items,
        "latestCases": case_items,
    }
