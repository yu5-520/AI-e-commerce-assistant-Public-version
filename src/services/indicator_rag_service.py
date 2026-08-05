"""V6.3 RAG indicator constraint service.

This service is the demo-stage company rule/RAG layer. It stores indicator rules
in SQLite, matches them by category / domain / risk level, and calculates concrete
execution boundaries for risk tasks. The LLM/Agent is not allowed to invent
thresholds for medium/high-risk tasks; it must consume these resolved indicators
or return a review-only task.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads

INDICATOR_RAG_VERSION = "6.3.0"

DEFAULT_RULES: List[Dict[str, Any]] = [
    {
        "ruleId": "RAG_RULE_STOCK_GENERAL",
        "domain": "库存",
        "category": "default",
        "riskLevel": "中",
        "ruleName": "通用库存安全线",
        "formula": "近7日销量 × 补货周期 × 安全系数",
        "thresholds": {"safetyFactor": 1.5, "replenishmentDays": 5, "minSafetyStock": 30},
        "sourceTitle": "公司RAG · 库存安全线SOP",
        "summary": "中风险库存任务必须给出安全库存线、补货时限和未达标升级条件。",
    },
    {
        "ruleId": "RAG_RULE_TRAFFIC_GENERAL",
        "domain": "流量",
        "category": "default",
        "riskLevel": "中",
        "ruleName": "通用投放修复边界",
        "formula": "ROI、点击率、转化率、毛利率同时监控；未达标先降投而非加投",
        "thresholds": {"minRoi": 1.6, "minCtr": 0.025, "minConversionRate": 0.03, "minGrossMargin": 0.25, "observeDays": 3, "reduceSpendRatio": 0.2},
        "sourceTitle": "公司RAG · 投放修复SOP",
        "summary": "中风险流量任务必须给出 ROI、点击率、转化率和毛利率边界。",
    },
    {
        "ruleId": "RAG_RULE_PROFIT_GENERAL",
        "domain": "利润",
        "category": "default",
        "riskLevel": "中",
        "ruleName": "通用利润保护线",
        "formula": "毛利率低于推流线时，不允许继续扩大投放",
        "thresholds": {"minGrossMargin": 0.25, "minRoi": 1.6, "observeDays": 3},
        "sourceTitle": "公司RAG · 利润保护SOP",
        "summary": "利润类任务必须明确毛利率底线和停止加投条件。",
    },
    {
        "ruleId": "RAG_RULE_AFTERSALE_GENERAL",
        "domain": "售后",
        "category": "default",
        "riskLevel": "中",
        "ruleName": "通用售后风险线",
        "formula": "退款率、差评率超过风险线时进入排查，禁止扩大投产",
        "thresholds": {"maxRefundRate": 0.08, "maxBadReviewRate": 0.03, "observeDays": 3},
        "sourceTitle": "公司RAG · 售后风控SOP",
        "summary": "售后风险必须给出退款率和差评率警戒线。",
    },
    {
        "ruleId": "RAG_RULE_HIGH_RISK_GATE",
        "domain": "趋势",
        "category": "default",
        "riskLevel": "高",
        "ruleName": "高风险投产门控",
        "formula": "至少4项关键指标在7天或30天窗口稳定向好，才允许进入加投/加库存审批",
        "thresholds": {"minPositiveMetricCount": 4, "minWindowDays": 7, "preferredWindowDays": 30, "minRoi": 1.8, "minGrossMargin": 0.28, "maxRefundRate": 0.06, "maxBadReviewRate": 0.025},
        "sourceTitle": "公司RAG · 高风险投产门控SOP",
        "summary": "高风险任务只能生成复核/审批候选，未通过趋势门控不得扩大预算或库存。",
    },
]


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


def _pct(value: float | None) -> str:
    if value is None:
        return "未命中"
    return f"{value * 100:.1f}%"


def ensure_indicator_rag_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_indicator_rules_v6 (
                rule_id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                category TEXT DEFAULT 'default',
                risk_level TEXT NOT NULL,
                rule_name TEXT NOT NULL,
                formula TEXT,
                thresholds TEXT,
                source_title TEXT,
                summary TEXT,
                enabled INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_indicator_matches_v6 (
                match_id TEXT PRIMARY KEY,
                product_id TEXT,
                store_id TEXT,
                risk_level TEXT,
                domain TEXT,
                data_version TEXT,
                rule_ids TEXT,
                constraints TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_indicator_rules_domain_v6 ON rag_indicator_rules_v6(domain, category, risk_level)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_indicator_matches_product_v6 ON risk_indicator_matches_v6(product_id, store_id, created_at)")
        now = now_iso()
        for rule in DEFAULT_RULES:
            conn.execute(
                """
                INSERT OR IGNORE INTO rag_indicator_rules_v6 (
                    rule_id, domain, category, risk_level, rule_name, formula, thresholds,
                    source_title, summary, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    rule["ruleId"], rule["domain"], rule.get("category") or "default", rule["riskLevel"], rule["ruleName"],
                    rule.get("formula"), dumps(rule.get("thresholds") or {}), rule.get("sourceTitle"), rule.get("summary"), now, now,
                ),
            )
        conn.commit()


def _load_rules(domain: str, category: str | None, risk_level: str) -> List[Dict[str, Any]]:
    ensure_indicator_rag_tables()
    wanted = [domain, "趋势"] if risk_level == "高" and domain != "趋势" else [domain]
    placeholders = ",".join("?" for _ in wanted)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM rag_indicator_rules_v6
            WHERE enabled = 1
              AND domain IN ({placeholders})
              AND risk_level IN (?, '中')
              AND category IN (?, 'default')
            ORDER BY CASE WHEN category = ? THEN 0 ELSE 1 END, CASE WHEN risk_level = ? THEN 0 ELSE 1 END
            """,
            [*wanted, risk_level, category or "default", category or "default", risk_level],
        ).fetchall()
    result = []
    seen = set()
    for row in rows:
        if row["rule_id"] in seen:
            continue
        seen.add(row["rule_id"])
        result.append({
            "ruleId": row["rule_id"],
            "domain": row["domain"],
            "category": row["category"],
            "riskLevel": row["risk_level"],
            "ruleName": row["rule_name"],
            "formula": row["formula"],
            "thresholds": loads(row["thresholds"]),
            "sourceTitle": row["source_title"],
            "summary": row["summary"],
        })
    return result


def _latest_metrics(product_id: str, store_id: str | None = None) -> Dict[str, float]:
    if store_id:
        query = "SELECT metrics FROM product_snapshots_v6 WHERE product_id = ? AND (store_id = ? OR store_id IS NULL) ORDER BY snapshot_at DESC LIMIT 1"
        params: tuple[Any, ...] = (product_id, store_id)
    else:
        query = "SELECT metrics FROM product_snapshots_v6 WHERE product_id = ? ORDER BY snapshot_at DESC LIMIT 1"
        params = (product_id,)
    with connect() as conn:
        row = conn.execute(query, params).fetchone()
    return loads(row["metrics"]) if row else {}


def _trend_counts(product_id: str, store_id: str | None = None, data_version: str | None = None) -> Dict[str, Any]:
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
        rows = conn.execute(f"SELECT metric_name, trend_direction, change_rate FROM metric_trends_v6 WHERE {where} ORDER BY created_at DESC LIMIT 50", params).fetchall()
    positive = []
    negative = []
    for row in rows:
        metric = row["metric_name"]
        direction = row["trend_direction"]
        change_rate = _as_float(row["change_rate"], 0) or 0
        if direction == "up" and metric not in {"refund_rate", "refund_amount", "refund_count", "bad_review_rate"}:
            positive.append(metric)
        if (direction == "down" and metric in {"roi", "traffic", "ctr", "conversion_rate", "gross_margin", "sales_volume", "revenue"}) or (direction == "up" and metric in {"refund_rate", "bad_review_rate", "refund_count"}):
            negative.append(metric)
    return {"positiveMetricCount": len(set(positive)), "negativeMetricCount": len(set(negative)), "positiveMetrics": sorted(set(positive)), "negativeMetrics": sorted(set(negative))}


def _merge_thresholds(rules: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for rule in rules:
        merged.update(rule.get("thresholds") or {})
    return merged


def _build_constraints(product: Dict[str, Any], domain: str, risk_level: str, rules: List[Dict[str, Any]], signals: List[Dict[str, Any]], data_version: str | None) -> Dict[str, Any]:
    thresholds = _merge_thresholds(rules)
    product_id = product.get("productId") or (signals[0].get("productId") if signals else None)
    store_id = product.get("storeId") or (signals[0].get("storeId") if signals else None)
    metrics = _latest_metrics(str(product_id), store_id) if product_id else {}
    trend_gate = _trend_counts(str(product_id), store_id, data_version) if product_id else {}
    sales = _as_float(metrics.get("sales_volume") or metrics.get("quantity") or metrics.get("revenue"), 0) or 0
    current_stock = _as_float(metrics.get("stock") or metrics.get("available_stock"), 0) or 0
    replenish_days = int(thresholds.get("replenishmentDays") or 5)
    safety_factor = float(thresholds.get("safetyFactor") or 1.5)
    min_safety_stock = float(thresholds.get("minSafetyStock") or 30)
    # Demo fallback: if no 7-day sales is available, use sales field as current report volume.
    safety_stock = max(min_safety_stock, sales * replenish_days * safety_factor) if domain == "库存" or risk_level in {"中", "高"} else None
    min_roi = _as_float(thresholds.get("minRoi"))
    min_ctr = _as_float(thresholds.get("minCtr"))
    min_conversion = _as_float(thresholds.get("minConversionRate"))
    min_margin = _as_float(thresholds.get("minGrossMargin"))
    max_refund = _as_float(thresholds.get("maxRefundRate"))
    max_bad_review = _as_float(thresholds.get("maxBadReviewRate"))
    min_positive = int(thresholds.get("minPositiveMetricCount") or 4)
    status = "matched"
    missing: List[str] = []
    if risk_level in {"中", "高"} and not rules:
        status = "missing_rules"
        missing.append("RAG指标规则")
    if risk_level in {"中", "高"} and min_roi is None and domain in {"流量", "利润", "趋势"}:
        status = "missing_indicators"
        missing.append("ROI红线")
    if risk_level == "高" and trend_gate.get("positiveMetricCount", 0) < min_positive:
        status = "gate_review_only"
    constraints = {
        "version": INDICATOR_RAG_VERSION,
        "status": status,
        "domain": domain,
        "riskLevel": risk_level,
        "ruleIds": [rule["ruleId"] for rule in rules],
        "ragSources": [{"ruleId": rule["ruleId"], "sourceTitle": rule.get("sourceTitle"), "summary": rule.get("summary"), "formula": rule.get("formula")} for rule in rules],
        "missing": missing,
        "currentMetrics": metrics,
        "targets": {
            "safetyStock": round(safety_stock, 2) if safety_stock is not None else None,
            "currentStock": current_stock,
            "replenishmentDays": replenish_days,
            "safetyFactor": safety_factor,
            "minRoi": min_roi,
            "minCtr": min_ctr,
            "minConversionRate": min_conversion,
            "minGrossMargin": min_margin,
            "maxRefundRate": max_refund,
            "maxBadReviewRate": max_bad_review,
            "observeDays": int(thresholds.get("observeDays") or 3),
            "reduceSpendRatio": _as_float(thresholds.get("reduceSpendRatio")),
            "minPositiveMetricCount": min_positive if risk_level == "高" else None,
        },
        "trendGate": trend_gate,
        "executionLines": [
            f"库存安全线：{round(safety_stock, 2)} 件" if safety_stock is not None else None,
            f"ROI 下限：{min_roi:.2f}" if min_roi is not None else None,
            f"点击率下限：{_pct(min_ctr)}" if min_ctr is not None else None,
            f"转化率下限：{_pct(min_conversion)}" if min_conversion is not None else None,
            f"毛利率下限：{_pct(min_margin)}" if min_margin is not None else None,
            f"退款率上限：{_pct(max_refund)}" if max_refund is not None else None,
            f"差评率上限：{_pct(max_bad_review)}" if max_bad_review is not None else None,
        ],
        "gateConclusion": "指标命中，可生成带边界任务。" if status == "matched" else "指标不足或趋势门控不足，只能生成复核/观察任务。",
    }
    constraints["executionLines"] = [line for line in constraints["executionLines"] if line]
    return constraints


def save_indicator_match(product_id: str | None, store_id: str | None, domain: str, risk_level: str, data_version: str | None, constraints: Dict[str, Any]) -> Dict[str, Any]:
    ensure_indicator_rag_tables()
    match = {
        "matchId": make_id("IMATCH"),
        "productId": product_id,
        "storeId": store_id,
        "domain": domain,
        "riskLevel": risk_level,
        "dataVersion": data_version,
        "ruleIds": constraints.get("ruleIds") or [],
        "constraints": constraints,
        "status": constraints.get("status") or "matched",
        "createdAt": now_iso(),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO risk_indicator_matches_v6 (match_id, product_id, store_id, risk_level, domain, data_version, rule_ids, constraints, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (match["matchId"], product_id, store_id, risk_level, domain, data_version, dumps(match["ruleIds"]), dumps(constraints), match["status"], match["createdAt"]),
        )
        conn.commit()
    return match


def resolve_indicator_constraints(product: Dict[str, Any], domain: str, risk_level: str, signals: List[Dict[str, Any]], data_version: str | None = None) -> Dict[str, Any]:
    """Resolve concrete RAG indicator boundaries for a risk task."""
    category = product.get("category") or "default"
    rules = _load_rules(domain=domain, category=category, risk_level=risk_level)
    if not rules and domain != "趋势":
        rules = _load_rules(domain="趋势", category=category, risk_level=risk_level)
    constraints = _build_constraints(product, domain, risk_level, rules, signals, data_version)
    save_indicator_match(product.get("productId"), product.get("storeId"), domain, risk_level, data_version, constraints)
    return constraints


def indicator_rule_summary(limit: int = 50) -> Dict[str, Any]:
    ensure_indicator_rag_tables()
    with connect() as conn:
        rules = conn.execute("SELECT * FROM rag_indicator_rules_v6 WHERE enabled = 1 ORDER BY domain, risk_level, rule_name LIMIT ?", (limit,)).fetchall()
        matches = conn.execute("SELECT * FROM risk_indicator_matches_v6 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    by_domain: Dict[str, int] = defaultdict(int)
    rule_items = []
    for row in rules:
        by_domain[row["domain"]] += 1
        rule_items.append({
            "ruleId": row["rule_id"], "domain": row["domain"], "category": row["category"], "riskLevel": row["risk_level"],
            "ruleName": row["rule_name"], "formula": row["formula"], "thresholds": loads(row["thresholds"]), "sourceTitle": row["source_title"], "summary": row["summary"],
        })
    match_items = []
    for row in matches:
        match_items.append({"matchId": row["match_id"], "productId": row["product_id"], "storeId": row["store_id"], "riskLevel": row["risk_level"], "domain": row["domain"], "dataVersion": row["data_version"], "status": row["status"], "constraints": loads(row["constraints"]), "createdAt": row["created_at"]})
    return {"version": INDICATOR_RAG_VERSION, "ruleCount": len(rule_items), "byDomain": dict(by_domain), "rules": rule_items, "latestMatches": match_items}
