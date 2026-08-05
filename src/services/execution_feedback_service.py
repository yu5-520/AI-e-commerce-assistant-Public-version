"""V6.8 execution feedback and result backflow service.

V6.7 made approval actions available in the frontend. V6.8 closes the next loop:
once an approved flow creates an execution task, the operator can submit actual
execution results such as final spend, stock purchase amount, evidence, and
outcome. The result is stored separately and linked back to the approval flow;
it does not mutate business data automatically.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads

EXECUTION_FEEDBACK_VERSION = "6.8.0"


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


def ensure_execution_feedback_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_results_v6 (
                result_id TEXT PRIMARY KEY,
                approval_flow_id TEXT,
                execution_task_id TEXT,
                product_id TEXT,
                store_id TEXT,
                result_status TEXT NOT NULL,
                actual_ad_spend REAL DEFAULT 0,
                actual_stock_purchase REAL DEFAULT 0,
                evidence TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_results_flow_v6 ON execution_results_v6(approval_flow_id, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_results_task_v6 ON execution_results_v6(execution_task_id, updated_at)")
        conn.commit()


def _load_flow(flow_id: str | None) -> Dict[str, Any] | None:
    if not flow_id:
        return None
    with connect() as conn:
        row = conn.execute("SELECT payload FROM approval_flows_v6 WHERE flow_id = ?", (flow_id,)).fetchone()
    return loads(row["payload"]) if row else None


def _update_flow_execution_result(flow_id: str | None, result: Dict[str, Any]) -> None:
    if not flow_id:
        return
    flow = _load_flow(flow_id)
    if not flow:
        return
    flow["executionResultId"] = result["resultId"]
    flow["executionStatus"] = result["resultStatus"]
    flow["updatedAt"] = now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE approval_flows_v6 SET payload=?, updated_at=? WHERE flow_id=?",
            (dumps(flow), flow["updatedAt"], flow_id),
        )
        conn.commit()


def submit_execution_result(body: Dict[str, Any], actor_role_id: str = "operator") -> Dict[str, Any]:
    """Submit execution feedback for an approved execution task."""
    ensure_execution_feedback_tables()
    approval_flow_id = body.get("approvalFlowId") or body.get("approval_flow_id")
    execution_task_id = body.get("executionTaskId") or body.get("execution_task_id")
    flow = _load_flow(str(approval_flow_id)) if approval_flow_id else None
    source_task = (flow or {}).get("sourceTask") or {}
    result_status = body.get("resultStatus") or body.get("result_status") or "submitted"
    evidence = body.get("evidence") or body.get("attachments") or []
    result = {
        "version": EXECUTION_FEEDBACK_VERSION,
        "resultId": make_id("ERES"),
        "approvalFlowId": approval_flow_id,
        "executionTaskId": execution_task_id or (flow or {}).get("executionTaskId"),
        "productId": body.get("productId") or body.get("product_id") or (flow or {}).get("productId") or source_task.get("productId"),
        "storeId": body.get("storeId") or body.get("store_id") or (flow or {}).get("storeId"),
        "resultStatus": str(result_status),
        "actualAdSpend": _as_float(body.get("actualAdSpend") or body.get("actual_ad_spend")),
        "actualStockPurchase": _as_float(body.get("actualStockPurchase") or body.get("actual_stock_purchase")),
        "note": body.get("note") or "执行结果已回写。",
        "evidence": evidence if isinstance(evidence, list) else [evidence],
        "actorRoleId": actor_role_id,
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "rule": "V6.8 执行回写只记录结果和证据，不自动修改经营数据；后续版本再进入复盘和RAG沉淀。",
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO execution_results_v6 (
                result_id, approval_flow_id, execution_task_id, product_id, store_id, result_status,
                actual_ad_spend, actual_stock_purchase, evidence, payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["resultId"], result.get("approvalFlowId"), result.get("executionTaskId"), result.get("productId"), result.get("storeId"),
                result["resultStatus"], result["actualAdSpend"], result["actualStockPurchase"], dumps(result["evidence"]), dumps(result),
                result["createdAt"], result["updatedAt"],
            ),
        )
        conn.commit()
    _update_flow_execution_result(str(approval_flow_id) if approval_flow_id else None, result)
    return result


def execution_feedback_summary(limit: int = 30) -> Dict[str, Any]:
    ensure_execution_feedback_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM execution_results_v6 ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    items: List[Dict[str, Any]] = [loads(row["payload"]) for row in rows]
    by_status: Dict[str, int] = defaultdict(int)
    total_spend = 0.0
    total_stock = 0.0
    for item in items:
        by_status[str(item.get("resultStatus") or "submitted")] += 1
        total_spend += _as_float(item.get("actualAdSpend"))
        total_stock += _as_float(item.get("actualStockPurchase"))
    return {
        "version": EXECUTION_FEEDBACK_VERSION,
        "total": len(items),
        "byStatus": dict(by_status),
        "actualAdSpendTotal": round(total_spend, 2),
        "actualStockPurchaseTotal": round(total_stock, 2),
        "latestResults": items,
    }
