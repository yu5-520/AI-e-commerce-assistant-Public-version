"""V14 RAG Context Station service.

This station turns wide signals into operating context. It does not create tasks,
change lifecycle state, or approve execution. RAG is the experience layer: company
rules, category baselines, historical recaps, SOP cards, and risk boundaries are
collected here so Agent judgment can stay flexible without controlling interfaces.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.indicator_rag_service import indicator_rule_summary
from src.services.signal_pool_service import list_signals

RAG_CONTEXT_STATION_VERSION = "14.0.0"

V14_BASELINE_CARDS: List[Dict[str, Any]] = [
    {
        "cardId": "V14_SIGNAL_WIDE_IN_TASK_STRICT_OUT",
        "sourceTitle": "V14主链原则 · 信号宽进任务严出",
        "summary": "所有数据变化、数据缺口、红线和权重波动都应进入信号池；是否生成任务由RAG增强后的Agent判断。",
        "policy": "不得用是否满足任务门槛来判断信号是否存在。",
    },
    {
        "cardId": "V14_BASELINE_AS_CONTEXT_NOT_GATE",
        "sourceTitle": "V14主链原则 · 基线不是硬拦截",
        "summary": "首份报表、不可比日期、历史不足，只代表动作强度默认降低，不代表不能生成补数、红线复核或人工确认任务。",
        "policy": "baselineMode/comparisonReady只能作为Agent上下文，不能在代码层直接阻断任务判断。",
    },
    {
        "cardId": "V14_AGENT_INTERFACE_BOUNDARY",
        "sourceTitle": "V14安全边界 · Agent不控制接口",
        "summary": "Agent只能输出判断、任务草案、证据要求和复核指标；站点接口、权限、预算、生命周期、审计和危险动作拦截由代码控制。",
        "policy": "Agent不得直接改价、改预算、改库存、自动发布、自动退款或绕过复核。",
    },
]


def now_iso() -> str:
    return datetime.now().isoformat()


def make_context_id() -> str:
    return f"RAGCTX-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"


def ensure_rag_context_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_context_snapshots_v14 (
                context_id TEXT PRIMARY KEY,
                data_version TEXT,
                signal_ref TEXT,
                matched_context_count INTEGER DEFAULT 0,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "rag_context_snapshots_v14",
            {
                "data_version": "TEXT",
                "signal_ref": "TEXT",
                "matched_context_count": "INTEGER DEFAULT 0",
                "updated_at": "TEXT",
            },
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_context_v14_version ON rag_context_snapshots_v14(data_version, created_at)")
        conn.commit()


def _row_to_context(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    return {
        **payload,
        "contextId": row["context_id"],
        "dataVersion": row["data_version"],
        "signalRef": row["signal_ref"],
        "matchedContextCount": int(row["matched_context_count"] or 0),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def latest_rag_context(data_version: str | None = None) -> Dict[str, Any] | None:
    ensure_rag_context_tables()
    with connect() as conn:
        if data_version:
            row = conn.execute("SELECT * FROM rag_context_snapshots_v14 WHERE data_version = ? ORDER BY created_at DESC LIMIT 1", (data_version,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM rag_context_snapshots_v14 ORDER BY created_at DESC LIMIT 1").fetchone()
    return _row_to_context(row) if row else None


def _domain_for_signal(signal: Dict[str, Any]) -> str:
    signal_type = str(signal.get("signalType") or "")
    metric = str(signal.get("metricCode") or "")
    if "inventory" in signal_type or metric in {"inventory_qty", "sellable_days"}:
        return "库存"
    if "refund" in signal_type or metric in {"refund_rate", "refund_amount"}:
        return "售后"
    if "margin" in signal_type or metric == "gross_margin_rate":
        return "利润"
    if metric in {"roi", "ad_spend", "click_rate", "visitor_count", "paid_visitor_count", "organic_visitor_count", "payment_conversion_rate"}:
        return "流量"
    if "data_gap" in signal_type:
        return "数据质量"
    return "趋势"


def _matched_indicator_cards(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        summary = indicator_rule_summary(limit=80)
    except Exception:
        summary = {"rules": []}
    rules = summary.get("rules") or []
    wanted = {_domain_for_signal(signal) for signal in signals}
    wanted.add("趋势")
    cards = []
    for rule in rules:
        if rule.get("domain") in wanted or rule.get("domain") == "趋势":
            cards.append(
                {
                    "cardId": rule.get("ruleId"),
                    "sourceTitle": rule.get("sourceTitle") or rule.get("ruleName"),
                    "domain": rule.get("domain"),
                    "riskLevel": rule.get("riskLevel"),
                    "summary": rule.get("summary"),
                    "formula": rule.get("formula"),
                    "thresholds": rule.get("thresholds") or {},
                    "usage": "作为Agent判断参考，不作为代码硬拦截。",
                }
            )
    return cards


def _signal_summary(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, int] = defaultdict(int)
    by_strength: Dict[str, int] = defaultdict(int)
    by_domain: Dict[str, int] = defaultdict(int)
    for signal in signals:
        by_type[str(signal.get("signalType"))] += 1
        by_strength[str(signal.get("signalStrength"))] += 1
        by_domain[_domain_for_signal(signal)] += 1
    return {"signalCount": len(signals), "byType": dict(by_type), "byStrength": dict(by_strength), "byDomain": dict(by_domain)}


def build_rag_context_snapshot(data_version: str | None = None, *, signal_ref: str | None = None, limit: int = 200) -> Dict[str, Any]:
    ensure_rag_context_tables()
    signal_result = list_signals(data_version=data_version, limit=limit)
    signals = signal_result.get("signals") or []
    indicator_cards = _matched_indicator_cards(signals)
    context_id = make_context_id()
    matched_context = [*V14_BASELINE_CARDS, *indicator_cards]
    payload = {
        "version": RAG_CONTEXT_STATION_VERSION,
        "contextId": context_id,
        "dataVersion": data_version,
        "stationId": "rag_context_station",
        "signalRef": signal_ref or signal_result.get("outputRef") or f"signal_pool:{data_version or 'latest'}",
        "signalSummary": _signal_summary(signals),
        "matchedContextCount": len(matched_context),
        "contextCards": matched_context,
        "agentInstruction": {
            "role": "结合指标事实、信号池和RAG经验，判断信号去向。",
            "allowedDecisions": ["create_task_snapshot", "manager_review_required", "observe_only", "ignore_noise"],
            "decisionOutputs": ["taskPlan", "evidenceRequirements", "reviewMetrics", "riskBoundary", "reason", "confidence"],
            "boundary": "Agent不控制接口、不越权执行，只输出判断结论和任务草案。",
        },
        "rule": "V14：经营经验进入RAG上下文；代码不再把报表周期、ROI/GMV象限、SOP经验写成任务硬拦截。",
    }
    created_at = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO rag_context_snapshots_v14 (
                context_id, data_version, signal_ref, matched_context_count,
                payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (context_id, data_version, payload["signalRef"], len(matched_context), dumps(payload), created_at, created_at),
        )
        conn.commit()
    return {
        **payload,
        "ok": True,
        "outputRef": f"rag_context:{context_id}",
        "ragContextRef": f"rag_context:{context_id}",
    }


def rag_context_summary(limit: int = 30) -> Dict[str, Any]:
    ensure_rag_context_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM rag_context_snapshots_v14 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    contexts = [_row_to_context(row) for row in rows]
    return {"version": RAG_CONTEXT_STATION_VERSION, "contextCount": len(contexts), "latest": contexts[0] if contexts else None, "contexts": contexts}
