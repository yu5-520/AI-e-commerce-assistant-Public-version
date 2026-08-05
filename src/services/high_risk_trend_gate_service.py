"""V6.4 high-risk historical trend gate.

High-risk actions such as increasing inventory, expanding ad budget, and pushing
a product as a hit item must not be generated only from one alert. This service
checks whether a product has enough stable positive metric evidence and no hard
risk blockers before the system may create an application-type task.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads

HIGH_RISK_GATE_VERSION = "6.4.0"
POSITIVE_METRICS = {"roi", "traffic", "clicks", "ctr", "conversion_rate", "gross_margin", "sales_volume", "quantity", "revenue", "actual_paid", "good_review_rate"}
NEGATIVE_METRICS = {"refund_rate", "refund_amount", "refund_count", "bad_review_rate"}
HARD_DROP_METRICS = {"roi", "ctr", "conversion_rate", "gross_margin", "sales_volume", "revenue"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}".upper()


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def ensure_high_risk_gate_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS high_risk_trend_gates_v6 (
                gate_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                store_id TEXT,
                data_version TEXT,
                gate_status TEXT NOT NULL,
                positive_metric_count INTEGER DEFAULT 0,
                blocker_count INTEGER DEFAULT 0,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_high_risk_gate_product_v6 ON high_risk_trend_gates_v6(product_id, store_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_high_risk_gate_status_v6 ON high_risk_trend_gates_v6(gate_status, created_at)")
        conn.commit()


def _load_recent_trends(product_id: str, store_id: str | None = None, data_version: str | None = None, limit: int = 120) -> List[Dict[str, Any]]:
    clauses = ["product_id = ?"]
    params: List[Any] = [product_id]
    if store_id:
        clauses.append("(store_id = ? OR store_id IS NULL)")
        params.append(store_id)
    if data_version:
        clauses.append("data_version = ?")
        params.append(data_version)
    where = " AND ".join(clauses)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT metric_name, metric_label, previous_value, current_value, change_rate, trend_direction, window_type, data_version, created_at
            FROM metric_trends_v6
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows]


def _latest_snapshot_count(product_id: str, store_id: str | None = None) -> int:
    if store_id:
        query = "SELECT COUNT(*) AS count FROM product_snapshots_v6 WHERE product_id = ? AND (store_id = ? OR store_id IS NULL)"
        params: tuple[Any, ...] = (product_id, store_id)
    else:
        query = "SELECT COUNT(*) AS count FROM product_snapshots_v6 WHERE product_id = ?"
        params = (product_id,)
    with connect() as conn:
        row = conn.execute(query, params).fetchone()
    return int(row["count"] if row else 0)


def _trend_evidence(trends: List[Dict[str, Any]]) -> Dict[str, Any]:
    positive: Dict[str, Dict[str, Any]] = {}
    blockers: Dict[str, Dict[str, Any]] = {}
    for item in trends:
        metric = item.get("metric_name")
        direction = item.get("trend_direction")
        change_rate = _as_float(item.get("change_rate"), 0) or 0
        current = _as_float(item.get("current_value"))
        previous = _as_float(item.get("previous_value"))
        payload = {
            "metricName": metric,
            "metricLabel": item.get("metric_label") or metric,
            "previousValue": previous,
            "currentValue": current,
            "changeRate": change_rate,
            "trendDirection": direction,
            "dataVersion": item.get("data_version"),
        }
        if metric in POSITIVE_METRICS and direction == "up" and change_rate >= 0.03:
            positive.setdefault(str(metric), payload)
        if metric in NEGATIVE_METRICS and direction == "down" and abs(change_rate) >= 0.03:
            positive.setdefault(str(metric), payload)
        if metric in HARD_DROP_METRICS and direction == "down" and change_rate <= -0.03:
            blockers.setdefault(str(metric), payload)
        if metric in NEGATIVE_METRICS and direction == "up" and change_rate >= 0.03:
            blockers.setdefault(str(metric), payload)
    return {
        "positiveMetrics": list(positive.values()),
        "blockerMetrics": list(blockers.values()),
        "positiveMetricCount": len(positive),
        "blockerCount": len(blockers),
    }


def _target_metric_blockers(current_metrics: Dict[str, Any], targets: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks = [
        ("roi", "minRoi", ">="),
        ("ctr", "minCtr", ">="),
        ("conversion_rate", "minConversionRate", ">="),
        ("gross_margin", "minGrossMargin", ">="),
        ("refund_rate", "maxRefundRate", "<="),
        ("bad_review_rate", "maxBadReviewRate", "<="),
    ]
    blockers: List[Dict[str, Any]] = []
    for metric, target_key, op in checks:
        current = _as_float(current_metrics.get(metric))
        target = _as_float(targets.get(target_key))
        if current is None or target is None:
            continue
        if op == ">=" and current < target:
            blockers.append({"metricName": metric, "currentValue": current, "targetValue": target, "reason": f"{metric} 低于RAG门控线"})
        if op == "<=" and current > target:
            blockers.append({"metricName": metric, "currentValue": current, "targetValue": target, "reason": f"{metric} 高于RAG风险线"})
    return blockers


def evaluate_high_risk_trend_gate(product: Dict[str, Any], signals: List[Dict[str, Any]], constraints: Dict[str, Any], data_version: str | None = None) -> Dict[str, Any]:
    """Evaluate whether high-risk investment/application task is allowed."""
    ensure_high_risk_gate_tables()
    product_id = str(product.get("productId") or (signals[0].get("productId") if signals else ""))
    store_id = product.get("storeId") or (signals[0].get("storeId") if signals else None)
    targets = constraints.get("targets") or {}
    min_positive = int(targets.get("minPositiveMetricCount") or 4)
    snapshot_count = _latest_snapshot_count(product_id, store_id)
    trends = _load_recent_trends(product_id, store_id, data_version=data_version)
    evidence = _trend_evidence(trends)
    metric_blockers = _target_metric_blockers(constraints.get("currentMetrics") or {}, targets)
    blocker_count = evidence["blockerCount"] + len(metric_blockers)
    rag_status = constraints.get("status") or "missing_rules"
    passed = evidence["positiveMetricCount"] >= min_positive and blocker_count == 0 and rag_status in {"matched", "gate_review_only"}
    if snapshot_count < 2:
        passed = False
    gate_status = "passed" if passed else "blocked"
    application_allowed = bool(passed)
    result = {
        "version": HIGH_RISK_GATE_VERSION,
        "gateId": make_id("HGATE"),
        "productId": product_id,
        "storeId": store_id,
        "dataVersion": data_version,
        "gateStatus": gate_status,
        "applicationAllowed": application_allowed,
        "executionAllowed": False,
        "snapshotCount": snapshot_count,
        "requiredPositiveMetricCount": min_positive,
        "positiveMetricCount": evidence["positiveMetricCount"],
        "blockerCount": blocker_count,
        "positiveMetrics": evidence["positiveMetrics"],
        "blockerMetrics": [*evidence["blockerMetrics"], *metric_blockers],
        "ragStatus": rag_status,
        "requiredWindow": f"至少{targets.get('minWindowDays') or 7}天，优先{targets.get('preferredWindowDays') or 30}天",
        "decision": "允许生成加大库存/投放申请任务，但仍需审批，不能自动执行。" if application_allowed else "未通过高风险趋势门控，只能生成复核/观察任务。",
        "rule": "高风险投产必须至少4项关键指标稳定向好，且没有ROI、转化、毛利、退款、差评等硬阻断。",
        "createdAt": now_iso(),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO high_risk_trend_gates_v6 (
                gate_id, product_id, store_id, data_version, gate_status, positive_metric_count, blocker_count, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (result["gateId"], product_id, store_id, data_version, gate_status, result["positiveMetricCount"], blocker_count, dumps(result), result["createdAt"]),
        )
        conn.commit()
    return result


def high_risk_gate_summary(limit: int = 30) -> Dict[str, Any]:
    ensure_high_risk_gate_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM high_risk_trend_gates_v6 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    items = [loads(row["payload"]) for row in rows]
    by_status: Dict[str, int] = defaultdict(int)
    for item in items:
        by_status[str(item.get("gateStatus") or "blocked")] += 1
    return {"version": HIGH_RISK_GATE_VERSION, "total": len(items), "byStatus": dict(by_status), "latestGates": items}
