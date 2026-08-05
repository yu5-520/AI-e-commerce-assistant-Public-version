"""LLM Gateway control layer: quota, cache, circuit breaker, schema validation."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Dict, List

from src.core.context import UserContext
from src.repositories.sqlite_repository import connect, dumps, init_db, loads
from src.services.llm_provider_service import generate_json, llm_status
from src.services.tech_log_service import write_tech_log
from src.services.trace_audit_service import resolve_trace_id, write_audit_log

LLM_CONTROL_VERSION = "5.2.8"
DAILY_QUOTA = int(os.getenv("LLM_TENANT_DAILY_QUOTA", "200"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("LLM_TENANT_RATE_LIMIT_PER_MINUTE", "30"))
BREAKER_FAILURE_THRESHOLD = int(os.getenv("LLM_BREAKER_FAILURE_THRESHOLD", "3"))
CACHE_TTL_SECONDS = int(os.getenv("LLM_CACHE_TTL_SECONDS", "1800"))


def ensure_llm_gateway_tables() -> None:
    init_db()
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_gateway_events (
                event_id TEXT PRIMARY KEY, trace_id TEXT, tenant_id TEXT DEFAULT 'tenant_demo',
                org_id TEXT DEFAULT 'org_demo', user_id TEXT, event_type TEXT NOT NULL,
                provider TEXT, model TEXT, prompt_name TEXT, schema_name TEXT,
                status TEXT NOT NULL, cache_key TEXT, request_hash TEXT,
                latency_ms INTEGER DEFAULT 0, payload TEXT, created_at TEXT NOT NULL,
                deleted_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_gateway_cache (
                cache_key TEXT PRIMARY KEY, tenant_id TEXT DEFAULT 'tenant_demo',
                prompt_name TEXT, schema_name TEXT, request_hash TEXT, response TEXT,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, deleted_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_circuit_breakers (
                breaker_key TEXT PRIMARY KEY, tenant_id TEXT DEFAULT 'tenant_demo',
                provider TEXT, model TEXT, status TEXT NOT NULL, failure_count INTEGER DEFAULT 0,
                opened_at TEXT, updated_at TEXT NOT NULL, deleted_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_events_tenant_time ON llm_gateway_events(tenant_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_events_trace ON llm_gateway_events(trace_id, created_at)")
        conn.commit()


def _event_id() -> str:
    return f"LLMEVT_{hashlib.sha1(str(time.time()).encode()).hexdigest()[:12]}".upper()


def _hash(data: Dict[str, Any]) -> str:
    return hashlib.sha256(dumps(data).encode("utf-8")).hexdigest()


def _cache_key(ctx: UserContext, prompt_name: str, schema_name: str, payload: Dict[str, Any], expected: List[str]) -> tuple[str, str]:
    request_hash = _hash({"promptName": prompt_name, "schemaName": schema_name, "payload": payload, "expectedKeys": expected})
    return _hash({"tenantId": ctx.tenant_id, "requestHash": request_hash}), request_hash


def _provider() -> Dict[str, Any]:
    return llm_status()


def _write_event(ctx: UserContext, trace_id: str, event_type: str, status: str, **payload: Any) -> None:
    ensure_llm_gateway_tables()
    provider = _provider()
    with connect() as conn:
        conn.execute("""
            INSERT INTO llm_gateway_events (
                event_id, trace_id, tenant_id, org_id, user_id, event_type, provider, model,
                prompt_name, schema_name, status, cache_key, request_hash, latency_ms,
                payload, created_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), NULL)
        """, (
            _event_id(), trace_id, ctx.tenant_id, ctx.org_id, ctx.user_id, event_type,
            provider.get("providerName"), provider.get("model"), payload.get("promptName"),
            payload.get("schemaName"), status, payload.get("cacheKey"), payload.get("requestHash"),
            int(payload.get("latencyMs") or 0), dumps(payload),
        ))
        conn.commit()
    write_tech_log(ctx, trace_id=trace_id, logger="llm-gateway", event_type=f"llm.{event_type}", message="llm gateway event", payload={"status": status, **payload})


def _quota(ctx: UserContext) -> Dict[str, Any]:
    ensure_llm_gateway_tables()
    with connect() as conn:
        day = conn.execute("SELECT COUNT(*) AS total FROM llm_gateway_events WHERE tenant_id = ? AND event_type = 'llm_generate' AND deleted_at IS NULL AND created_at >= datetime('now', '-1 day')", (ctx.tenant_id,)).fetchone()["total"]
        minute = conn.execute("SELECT COUNT(*) AS total FROM llm_gateway_events WHERE tenant_id = ? AND event_type = 'llm_generate' AND deleted_at IS NULL AND created_at >= datetime('now', '-1 minute')", (ctx.tenant_id,)).fetchone()["total"]
    return {"dailyUsed": day, "dailyQuota": DAILY_QUOTA, "minuteUsed": minute, "minuteLimit": RATE_LIMIT_PER_MINUTE, "allowed": day < DAILY_QUOTA and minute < RATE_LIMIT_PER_MINUTE}


def _breaker_key(ctx: UserContext) -> str:
    p = _provider()
    return f"{ctx.tenant_id}:{p.get('providerName')}:{p.get('model')}"


def _breaker(ctx: UserContext) -> Dict[str, Any]:
    ensure_llm_gateway_tables()
    key = _breaker_key(ctx)
    with connect() as conn:
        row = conn.execute("SELECT * FROM llm_circuit_breakers WHERE breaker_key = ? AND deleted_at IS NULL", (key,)).fetchone()
    if not row:
        return {"breakerKey": key, "status": "closed", "failureCount": 0, "open": False}
    return {"breakerKey": key, "status": row["status"], "failureCount": row["failure_count"], "open": row["status"] == "open"}


def _set_breaker(ctx: UserContext, failed: bool) -> Dict[str, Any]:
    ensure_llm_gateway_tables()
    key = _breaker_key(ctx)
    p = _provider()
    with connect() as conn:
        old = conn.execute("SELECT failure_count FROM llm_circuit_breakers WHERE breaker_key = ?", (key,)).fetchone()
        failures = (int(old["failure_count"]) if old else 0) + 1 if failed else 0
        status = "open" if failures >= BREAKER_FAILURE_THRESHOLD else "closed"
        conn.execute("""
            INSERT OR REPLACE INTO llm_circuit_breakers (
                breaker_key, tenant_id, provider, model, status, failure_count,
                opened_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ? = 'open' THEN datetime('now') ELSE NULL END, datetime('now'), NULL)
        """, (key, ctx.tenant_id, p.get("providerName"), p.get("model"), status, failures, status))
        conn.commit()
    return _breaker(ctx)


def _get_cache(ctx: UserContext, cache_key: str) -> Dict[str, Any] | None:
    ensure_llm_gateway_tables()
    with connect() as conn:
        row = conn.execute("SELECT response FROM llm_gateway_cache WHERE cache_key = ? AND tenant_id = ? AND deleted_at IS NULL AND expires_at > datetime('now')", (cache_key, ctx.tenant_id)).fetchone()
    return loads(row["response"]) if row else None


def _put_cache(ctx: UserContext, cache_key: str, prompt_name: str, schema_name: str, request_hash: str, response: Dict[str, Any]) -> None:
    ensure_llm_gateway_tables()
    with connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO llm_gateway_cache (
                cache_key, tenant_id, prompt_name, schema_name, request_hash,
                response, created_at, expires_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now', ?), NULL)
        """, (cache_key, ctx.tenant_id, prompt_name, schema_name, request_hash, dumps(response), f"+{CACHE_TTL_SECONDS} seconds"))
        conn.commit()


def _schema(output: Dict[str, Any], expected: List[str]) -> Dict[str, Any]:
    missing = [key for key in expected if key not in output]
    return {"valid": not missing, "missingKeys": missing, "expectedKeys": expected}


def gateway_generate_json(ctx: UserContext, *, prompt_name: str, payload: Dict[str, Any], expected_keys: List[str] | None = None, agent_name: str = "LLM Gateway", schema_name: str = "generic_json", use_cache: bool = True) -> Dict[str, Any]:
    expected = expected_keys or []
    trace_id = resolve_trace_id(payload, "LLMTRACE")
    cache_key, request_hash = _cache_key(ctx, prompt_name, schema_name, payload, expected)
    quota = _quota(ctx)
    breaker = _breaker(ctx)
    if not quota["allowed"] or breaker["open"]:
        blocked = "blocked_quota" if not quota["allowed"] else "blocked_circuit_open"
        _write_event(ctx, trace_id, "llm_generate", blocked, promptName=prompt_name, schemaName=schema_name, cacheKey=cache_key, requestHash=request_hash, quota=quota, breaker=breaker)
        write_audit_log(ctx, trace_id=trace_id, event_type=f"llm.{blocked}", resource_type="llm_gateway", resource_id=cache_key, action=prompt_name, status="blocked", payload={"quota": quota, "breaker": breaker})
        return {"version": LLM_CONTROL_VERSION, "traceId": trace_id, "status": blocked, "fallbackUsed": True, "quota": quota, "breaker": breaker, "output": {"llmSummary": "LLM 网关已降级，核心流程不受影响。"}}
    if use_cache:
        cached = _get_cache(ctx, cache_key)
        if cached:
            _write_event(ctx, trace_id, "llm_cache_hit", "cache_hit", promptName=prompt_name, schemaName=schema_name, cacheKey=cache_key, requestHash=request_hash)
            return {**cached, "version": LLM_CONTROL_VERSION, "traceId": trace_id, "cache": {"hit": True, "cacheKey": cache_key}, "quota": quota, "breaker": breaker}
    start = time.time()
    result = generate_json(prompt_name=prompt_name, payload={**payload, "traceId": trace_id}, expected_keys=expected, agent_name=agent_name, schema_name=schema_name)
    latency = int((time.time() - start) * 1000)
    validation = _schema(result.get("output") or {}, expected)
    status = result.get("status") or "unknown"
    failed = status in {"fallback_error", "error"} or not validation["valid"]
    breaker = _set_breaker(ctx, failed)
    controlled = {**result, "version": LLM_CONTROL_VERSION, "traceId": trace_id, "cache": {"hit": False, "cacheKey": cache_key, "ttlSeconds": CACHE_TTL_SECONDS}, "quota": quota, "breaker": breaker, "schemaValidation": validation}
    if not failed:
        _put_cache(ctx, cache_key, prompt_name, schema_name, request_hash, controlled)
    _write_event(ctx, trace_id, "llm_generate", status, promptName=prompt_name, schemaName=schema_name, cacheKey=cache_key, requestHash=request_hash, latencyMs=latency, schema=validation, fallbackUsed=controlled.get("fallbackUsed"))
    write_audit_log(ctx, trace_id=trace_id, event_type="llm.generated", resource_type="llm_gateway", resource_id=cache_key, action=prompt_name, status=status, payload={"schema": validation, "fallbackUsed": controlled.get("fallbackUsed")})
    return controlled


def llm_gateway_control_summary(ctx: UserContext) -> Dict[str, Any]:
    ensure_llm_gateway_tables()
    with connect() as conn:
        cache_count = conn.execute("SELECT COUNT(*) AS total FROM llm_gateway_cache WHERE tenant_id = ? AND deleted_at IS NULL AND expires_at > datetime('now')", (ctx.tenant_id,)).fetchone()["total"]
        event_count = conn.execute("SELECT COUNT(*) AS total FROM llm_gateway_events WHERE tenant_id = ? AND deleted_at IS NULL", (ctx.tenant_id,)).fetchone()["total"]
    return {"version": LLM_CONTROL_VERSION, "provider": llm_status(), "quota": _quota(ctx), "breaker": _breaker(ctx), "cache": {"activeEntries": cache_count, "ttlSeconds": CACHE_TTL_SECONDS}, "events": {"total": event_count}, "controls": {"rateLimitPerMinute": RATE_LIMIT_PER_MINUTE, "dailyQuota": DAILY_QUOTA, "breakerFailureThreshold": BREAKER_FAILURE_THRESHOLD, "schemaValidation": "expectedKeys"}, "rule": "LLM 只增强草稿，核心任务生命周期和审批保持确定性。"}
