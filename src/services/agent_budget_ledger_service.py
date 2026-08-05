"""V15 full-chain Agent budget ledger.

All Agent/API/RAG usage is tracked by dataVersion/run. The ledger is the
contract that keeps the three Agent stages from silently turning data rows,
metric judgments, or task packages into one-call-per-record API traffic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads

AGENT_BUDGET_LEDGER_VERSION = "15.0"
DEFAULT_TOTAL_AGENT_BUDGET = 8
STAGE_BUDGETS = {
    "report_schema_agent": 3,
    "product_judgment_agent": 3,
    "task_mapping_agent": 2,
}
RAG_BUDGET_PER_RUN = 3


def now_iso() -> str:
    return datetime.now().isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone())


def _safe_load(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return loads(value)
    except Exception:
        return {}


def ensure_agent_budget_tables() -> None:
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_budget_ledgers_v15 (
                ledger_id TEXT PRIMARY KEY,
                run_id TEXT,
                data_version TEXT,
                status TEXT,
                report_agent_calls INTEGER DEFAULT 0,
                product_judgment_agent_calls INTEGER DEFAULT 0,
                task_mapping_agent_calls INTEGER DEFAULT 0,
                rag_retrieval_count INTEGER DEFAULT 0,
                total_agent_calls INTEGER DEFAULT 0,
                total_agent_budget INTEGER DEFAULT 8,
                cache_hit_count INTEGER DEFAULT 0,
                fallback_count INTEGER DEFAULT 0,
                estimated_input_tokens INTEGER DEFAULT 0,
                estimated_output_tokens INTEGER DEFAULT 0,
                actual_input_tokens INTEGER DEFAULT 0,
                actual_output_tokens INTEGER DEFAULT 0,
                budget_violation INTEGER DEFAULT 0,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_call_events_v15 (
                event_id TEXT PRIMARY KEY,
                ledger_id TEXT,
                run_id TEXT,
                data_version TEXT,
                stage TEXT,
                call_type TEXT,
                requested_calls INTEGER DEFAULT 0,
                actual_calls INTEGER DEFAULT 0,
                cache_hit INTEGER DEFAULT 0,
                fallback_used INTEGER DEFAULT 0,
                rag_retrievals INTEGER DEFAULT 0,
                estimated_input_tokens INTEGER DEFAULT 0,
                estimated_output_tokens INTEGER DEFAULT 0,
                actual_input_tokens INTEGER DEFAULT 0,
                actual_output_tokens INTEGER DEFAULT 0,
                status TEXT,
                reason TEXT,
                payload TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS report_schema_mapping_cache_v15 (
                schema_fingerprint TEXT PRIMARY KEY,
                platform TEXT,
                sheet_type TEXT,
                confidence REAL DEFAULT 0,
                field_mapping TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        ensure_columns(conn, "agent_budget_ledgers_v15", {"run_id": "TEXT", "data_version": "TEXT", "status": "TEXT", "report_agent_calls": "INTEGER DEFAULT 0", "product_judgment_agent_calls": "INTEGER DEFAULT 0", "task_mapping_agent_calls": "INTEGER DEFAULT 0", "rag_retrieval_count": "INTEGER DEFAULT 0", "total_agent_calls": "INTEGER DEFAULT 0", "total_agent_budget": "INTEGER DEFAULT 8", "cache_hit_count": "INTEGER DEFAULT 0", "fallback_count": "INTEGER DEFAULT 0", "estimated_input_tokens": "INTEGER DEFAULT 0", "estimated_output_tokens": "INTEGER DEFAULT 0", "actual_input_tokens": "INTEGER DEFAULT 0", "actual_output_tokens": "INTEGER DEFAULT 0", "budget_violation": "INTEGER DEFAULT 0", "payload": "TEXT", "created_at": "TEXT", "updated_at": "TEXT"})
        ensure_columns(conn, "agent_call_events_v15", {"ledger_id": "TEXT", "run_id": "TEXT", "data_version": "TEXT", "stage": "TEXT", "call_type": "TEXT", "requested_calls": "INTEGER DEFAULT 0", "actual_calls": "INTEGER DEFAULT 0", "cache_hit": "INTEGER DEFAULT 0", "fallback_used": "INTEGER DEFAULT 0", "rag_retrievals": "INTEGER DEFAULT 0", "estimated_input_tokens": "INTEGER DEFAULT 0", "estimated_output_tokens": "INTEGER DEFAULT 0", "actual_input_tokens": "INTEGER DEFAULT 0", "actual_output_tokens": "INTEGER DEFAULT 0", "status": "TEXT", "reason": "TEXT", "payload": "TEXT", "created_at": "TEXT"})
        ensure_columns(conn, "report_schema_mapping_cache_v15", {"platform": "TEXT", "sheet_type": "TEXT", "confidence": "REAL DEFAULT 0", "field_mapping": "TEXT", "payload": "TEXT", "created_at": "TEXT", "updated_at": "TEXT"})
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_budget_ledgers_v15_version ON agent_budget_ledgers_v15(data_version, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_call_events_v15_ledger ON agent_call_events_v15(ledger_id, stage, created_at)")
        conn.commit()


def _build_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = _safe_load(row.get("payload"))
    payload.update({
        "version": AGENT_BUDGET_LEDGER_VERSION,
        "ledgerId": row.get("ledger_id"),
        "runId": row.get("run_id"),
        "dataVersion": row.get("data_version"),
        "status": row.get("status"),
        "reportAgentCalls": int(row.get("report_agent_calls") or 0),
        "productJudgmentAgentCalls": int(row.get("product_judgment_agent_calls") or 0),
        "taskMappingAgentCalls": int(row.get("task_mapping_agent_calls") or 0),
        "ragRetrievalCount": int(row.get("rag_retrieval_count") or 0),
        "totalAgentCalls": int(row.get("total_agent_calls") or 0),
        "totalAgentBudget": int(row.get("total_agent_budget") or DEFAULT_TOTAL_AGENT_BUDGET),
        "cacheHitCount": int(row.get("cache_hit_count") or 0),
        "fallbackCount": int(row.get("fallback_count") or 0),
        "budgetViolation": bool(row.get("budget_violation")),
        "estimatedInputTokens": int(row.get("estimated_input_tokens") or 0),
        "estimatedOutputTokens": int(row.get("estimated_output_tokens") or 0),
        "actualInputTokens": int(row.get("actual_input_tokens") or 0),
        "actualOutputTokens": int(row.get("actual_output_tokens") or 0),
        "updatedAt": row.get("updated_at"),
        "rule": "V15: report/product/task Agents must share one run budget; no stage may call per row, per metric, or per task without ledger approval.",
    })
    return payload


def get_or_create_agent_budget_ledger(*, data_version: str | None, run_id: str | None = None, source: str = "runtime") -> Dict[str, Any]:
    ensure_agent_budget_tables()
    with connect() as conn:
        if run_id:
            row = conn.execute("SELECT * FROM agent_budget_ledgers_v15 WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
        elif data_version:
            row = conn.execute("SELECT * FROM agent_budget_ledgers_v15 WHERE data_version=? ORDER BY created_at DESC LIMIT 1", (data_version,)).fetchone()
        else:
            row = None
        if row:
            return _build_payload(dict(row))
        now = now_iso()
        ledger_id = make_id("ABL")
        run_id = run_id or make_id("RUN")
        payload = {"version": AGENT_BUDGET_LEDGER_VERSION, "ledgerId": ledger_id, "runId": run_id, "dataVersion": data_version, "source": source, "stageBudgets": STAGE_BUDGETS, "ragBudgetPerRun": RAG_BUDGET_PER_RUN}
        conn.execute("""
            INSERT INTO agent_budget_ledgers_v15 (ledger_id, run_id, data_version, status, total_agent_budget, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (ledger_id, run_id, data_version, "open", DEFAULT_TOTAL_AGENT_BUDGET, dumps(payload), now, now))
        conn.commit()
        payload.update({"status": "open", "totalAgentCalls": 0, "totalAgentBudget": DEFAULT_TOTAL_AGENT_BUDGET, "budgetViolation": False})
        return payload


def _stage_column(stage: str) -> str | None:
    return {
        "report_schema_agent": "report_agent_calls",
        "product_judgment_agent": "product_judgment_agent_calls",
        "task_mapping_agent": "task_mapping_agent_calls",
    }.get(stage)


def register_agent_event(*, ledger_id: str | None = None, run_id: str | None = None, data_version: str | None = None, stage: str, call_type: str = "agent", requested_calls: int = 0, actual_calls: int = 0, cache_hit: bool = False, fallback_used: bool = False, rag_retrievals: int = 0, estimated_input_tokens: int = 0, estimated_output_tokens: int = 0, actual_input_tokens: int = 0, actual_output_tokens: int = 0, reason: str = "", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ensure_agent_budget_tables()
    ledger = None
    with connect() as conn:
        if ledger_id:
            row = conn.execute("SELECT * FROM agent_budget_ledgers_v15 WHERE ledger_id=?", (ledger_id,)).fetchone()
            ledger = _build_payload(dict(row)) if row else None
    ledger = ledger or get_or_create_agent_budget_ledger(data_version=data_version, run_id=run_id, source="agent_event")
    ledger_id = ledger["ledgerId"]
    run_id = ledger.get("runId") or run_id
    data_version = ledger.get("dataVersion") or data_version
    stage_budget = STAGE_BUDGETS.get(stage, DEFAULT_TOTAL_AGENT_BUDGET)
    total_before = int(ledger.get("totalAgentCalls") or 0)
    current_stage_calls = int(ledger.get({"report_schema_agent": "reportAgentCalls", "product_judgment_agent": "productJudgmentAgentCalls", "task_mapping_agent": "taskMappingAgentCalls"}.get(stage, "totalAgentCalls")) or 0)
    budget_violation = bool(ledger.get("budgetViolation")) or (current_stage_calls + actual_calls > stage_budget) or (total_before + actual_calls > DEFAULT_TOTAL_AGENT_BUDGET) or (int(ledger.get("ragRetrievalCount") or 0) + rag_retrievals > RAG_BUDGET_PER_RUN)
    status = "budget_violation" if budget_violation else "recorded"
    event_id = make_id("ACE")
    now = now_iso()
    event_payload = {"version": AGENT_BUDGET_LEDGER_VERSION, "stage": stage, "callType": call_type, "requestedCalls": requested_calls, "actualCalls": actual_calls, "cacheHit": cache_hit, "fallbackUsed": fallback_used, "ragRetrievals": rag_retrievals, "reason": reason, **(payload or {})}
    with connect() as conn:
        conn.execute("""
            INSERT INTO agent_call_events_v15 (event_id, ledger_id, run_id, data_version, stage, call_type, requested_calls, actual_calls, cache_hit, fallback_used, rag_retrievals, estimated_input_tokens, estimated_output_tokens, actual_input_tokens, actual_output_tokens, status, reason, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (event_id, ledger_id, run_id, data_version, stage, call_type, int(requested_calls or 0), int(actual_calls or 0), 1 if cache_hit else 0, 1 if fallback_used else 0, int(rag_retrievals or 0), int(estimated_input_tokens or 0), int(estimated_output_tokens or 0), int(actual_input_tokens or 0), int(actual_output_tokens or 0), status, reason, dumps(event_payload), now))
        update = {
            "total_agent_calls": int(ledger.get("totalAgentCalls") or 0) + int(actual_calls or 0),
            "rag_retrieval_count": int(ledger.get("ragRetrievalCount") or 0) + int(rag_retrievals or 0),
            "cache_hit_count": int(ledger.get("cacheHitCount") or 0) + (1 if cache_hit else 0),
            "fallback_count": int(ledger.get("fallbackCount") or 0) + (1 if fallback_used else 0),
            "estimated_input_tokens": int(ledger.get("estimatedInputTokens") or 0) + int(estimated_input_tokens or 0),
            "estimated_output_tokens": int(ledger.get("estimatedOutputTokens") or 0) + int(estimated_output_tokens or 0),
            "actual_input_tokens": int(ledger.get("actualInputTokens") or 0) + int(actual_input_tokens or 0),
            "actual_output_tokens": int(ledger.get("actualOutputTokens") or 0) + int(actual_output_tokens or 0),
            "budget_violation": 1 if budget_violation else 0,
            "status": "attention_budget_violation" if budget_violation else "open",
        }
        stage_col = _stage_column(stage)
        if stage_col:
            reverse_key = {"report_agent_calls": "reportAgentCalls", "product_judgment_agent_calls": "productJudgmentAgentCalls", "task_mapping_agent_calls": "taskMappingAgentCalls"}[stage_col]
            update[stage_col] = int(ledger.get(reverse_key) or 0) + int(actual_calls or 0)
        set_clause = ", ".join([f"{key}=?" for key in update.keys()]) + ", updated_at=?"
        conn.execute(f"UPDATE agent_budget_ledgers_v15 SET {set_clause} WHERE ledger_id=?", tuple(update.values()) + (now, ledger_id))
        conn.commit()
    return {"eventId": event_id, "ledgerId": ledger_id, "runId": run_id, "dataVersion": data_version, "stage": stage, "status": status, "budgetViolation": budget_violation, "actualCalls": actual_calls, "totalAgentCalls": update["total_agent_calls"], "totalAgentBudget": DEFAULT_TOTAL_AGENT_BUDGET, "rule": "V15 ledger event recorded before/around every Agent/RAG stage."}


def read_agent_budget_summary(*, ledger_id: str | None = None, data_version: str | None = None) -> Dict[str, Any]:
    ensure_agent_budget_tables()
    with connect() as conn:
        if ledger_id:
            row = conn.execute("SELECT * FROM agent_budget_ledgers_v15 WHERE ledger_id=?", (ledger_id,)).fetchone()
        elif data_version:
            row = conn.execute("SELECT * FROM agent_budget_ledgers_v15 WHERE data_version=? ORDER BY created_at DESC LIMIT 1", (data_version,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM agent_budget_ledgers_v15 ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return {"version": AGENT_BUDGET_LEDGER_VERSION, "status": "empty", "totalAgentCalls": 0, "totalAgentBudget": DEFAULT_TOTAL_AGENT_BUDGET, "budgetViolation": False}
        ledger = _build_payload(dict(row))
        events = conn.execute("SELECT stage, status, actual_calls, rag_retrievals, cache_hit, fallback_used, created_at FROM agent_call_events_v15 WHERE ledger_id=? ORDER BY created_at", (ledger["ledgerId"],)).fetchall()
        ledger["events"] = [dict(item) for item in events]
        return ledger


def save_report_schema_mapping_cache(*, schema_fingerprint: str, platform: str | None, sheet_type: str | None, confidence: float, field_mapping: Dict[str, Any], payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ensure_agent_budget_tables()
    now = now_iso()
    record = {"version": AGENT_BUDGET_LEDGER_VERSION, "schemaFingerprint": schema_fingerprint, "platform": platform, "sheetType": sheet_type, "confidence": confidence, "fieldMapping": field_mapping, **(payload or {})}
    with connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO report_schema_mapping_cache_v15 (schema_fingerprint, platform, sheet_type, confidence, field_mapping, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM report_schema_mapping_cache_v15 WHERE schema_fingerprint=?), ?), ?)
        """, (schema_fingerprint, platform, sheet_type, float(confidence or 0), dumps(field_mapping), dumps(record), schema_fingerprint, now, now))
        conn.commit()
    return record


def get_report_schema_mapping_cache(schema_fingerprint: str) -> Dict[str, Any] | None:
    ensure_agent_budget_tables()
    with connect() as conn:
        row = conn.execute("SELECT payload FROM report_schema_mapping_cache_v15 WHERE schema_fingerprint=?", (schema_fingerprint,)).fetchone()
    return _safe_load(row["payload"]) if row else None
