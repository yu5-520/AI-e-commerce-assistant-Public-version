"""LLM trace service.

Trace records are intentionally small and local. They help verify which Agent used
LLM enrichment, which provider was selected, whether fallback was used, and which
schemas were expected. Sensitive API keys are never stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads

TRACE_VERSION = "4.5.0"


def now_iso() -> str:
    return datetime.now().isoformat()


def ensure_llm_trace_table() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_traces (
                trace_id TEXT PRIMARY KEY,
                agent_name TEXT,
                provider TEXT,
                model TEXT,
                prompt_name TEXT,
                schema_name TEXT,
                status TEXT NOT NULL,
                fallback_used INTEGER,
                latency_ms INTEGER,
                token_usage TEXT,
                request_meta TEXT,
                response_meta TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_traces_created ON llm_traces(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_traces_agent ON llm_traces(agent_name, status)")
        conn.commit()


def record_llm_trace(
    *,
    agent_name: str,
    provider: str,
    model: str,
    prompt_name: str,
    schema_name: str,
    status: str,
    fallback_used: bool = False,
    latency_ms: int | None = None,
    token_usage: Dict[str, Any] | None = None,
    request_meta: Dict[str, Any] | None = None,
    response_meta: Dict[str, Any] | None = None,
    error_message: str = "",
) -> Dict[str, Any]:
    ensure_llm_trace_table()
    trace = {
        "traceId": f"LLM-{uuid4().hex[:12].upper()}",
        "agentName": agent_name,
        "provider": provider,
        "model": model,
        "promptName": prompt_name,
        "schemaName": schema_name,
        "status": status,
        "fallbackUsed": fallback_used,
        "latencyMs": latency_ms,
        "tokenUsage": token_usage or {},
        "requestMeta": request_meta or {},
        "responseMeta": response_meta or {},
        "errorMessage": error_message,
        "createdAt": now_iso(),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO llm_traces(trace_id, agent_name, provider, model, prompt_name, schema_name, status, fallback_used, latency_ms, token_usage, request_meta, response_meta, error_message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace["traceId"],
                agent_name,
                provider,
                model,
                prompt_name,
                schema_name,
                status,
                1 if fallback_used else 0,
                latency_ms,
                dumps(token_usage or {}),
                dumps(request_meta or {}),
                dumps(response_meta or {}),
                error_message,
                trace["createdAt"],
            ),
        )
        conn.commit()
    return trace


def list_llm_traces(limit: int = 50, agent_name: str | None = None) -> List[Dict[str, Any]]:
    ensure_llm_trace_table()
    query = "SELECT * FROM llm_traces"
    params: List[Any] = []
    if agent_name:
        query += " WHERE agent_name = ?"
        params.append(agent_name)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [
        {
            "traceId": row["trace_id"],
            "agentName": row["agent_name"],
            "provider": row["provider"],
            "model": row["model"],
            "promptName": row["prompt_name"],
            "schemaName": row["schema_name"],
            "status": row["status"],
            "fallbackUsed": bool(row["fallback_used"]),
            "latencyMs": row["latency_ms"],
            "tokenUsage": loads(row["token_usage"]),
            "requestMeta": loads(row["request_meta"]),
            "responseMeta": loads(row["response_meta"]),
            "errorMessage": row["error_message"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def llm_trace_summary() -> Dict[str, Any]:
    ensure_llm_trace_table()
    with connect() as conn:
        rows = conn.execute("SELECT status, fallback_used, COUNT(*) AS count FROM llm_traces GROUP BY status, fallback_used").fetchall()
    return {
        "version": TRACE_VERSION,
        "items": [
            {"status": row["status"], "fallbackUsed": bool(row["fallback_used"]), "count": row["count"]}
            for row in rows
        ],
        "boundary": "Trace 只记录调用元数据和结构化摘要，不保存 API Key。",
    }
