"""V16.14 audit routes.

The audit route no longer imports the deleted src.core.context module or old
trace/tech-log service fragments. It exposes a lightweight V16-safe audit view
that keeps the API shape available while the MVP task pipeline is being cleaned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, Body, Query, Request

from src.services.account_service import user_id_from_headers

router = APIRouter(prefix="/api/audit", tags=["audit"])
AUDIT_ROUTE_VERSION = "16.14"
SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "apikey", "authorization", "cookie", "set-cookie"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


def redact_sensitive_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted: Dict[str, Any] = {}
        for key, value in payload.items():
            if str(key).lower() in SENSITIVE_KEYS:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = redact_sensitive_payload(value)
        return redacted
    if isinstance(payload, list):
        return [redact_sensitive_payload(item) for item in payload]
    return payload


def resolve_trace_id(body: Dict[str, Any], prefix: str = "TRACE") -> str:
    return str(body.get("trace_id") or body.get("traceId") or f"{prefix}-{uuid4().hex[:10]}")


@router.get("/traces/{trace_id}")
def trace_timeline(request: Request, trace_id: str, limit: int = Query(default=100, ge=1, le=500)) -> Dict[str, Any]:
    """Return one V16-safe trace timeline.

    Missing persisted audit rows are represented as an empty timeline. The route
    must never recreate old mock workflow state.
    """
    return {
        "version": AUDIT_ROUTE_VERSION,
        "traceId": trace_id,
        "limit": limit,
        "events": [],
        "scope": {"userId": request_user_id(request)},
        "source": "v16_audit_route_no_legacy_context",
        "note": "No legacy src.core.context or trace service dependency is used.",
    }


@router.get("/tech-logs")
def tech_logs(
    request: Request,
    trace_id: str | None = Query(default=None),
    level: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> Dict[str, Any]:
    """Return sanitized technical logs projection."""
    return {
        "version": AUDIT_ROUTE_VERSION,
        "logs": [],
        "filters": {"traceId": trace_id, "level": level, "eventType": event_type, "limit": limit},
        "scope": {"userId": request_user_id(request)},
        "source": "v16_audit_route_no_legacy_context",
    }


@router.get("/tech-logs/summary")
def tech_logs_summary() -> Dict[str, Any]:
    """Return TechLog counts and redaction policy."""
    return {
        "version": AUDIT_ROUTE_VERSION,
        "total": 0,
        "byLevel": {},
        "redactionKeys": sorted(SENSITIVE_KEYS),
        "source": "v16_audit_route_no_legacy_context",
    }


@router.post("/tech-logs/test-redaction")
def test_redaction(request: Request, body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    """Return a redacted payload without writing to deleted legacy log stores."""
    trace_id = resolve_trace_id(body, "TECHLOGTEST")
    return {
        "version": AUDIT_ROUTE_VERSION,
        "traceId": trace_id,
        "redactedPayload": redact_sensitive_payload(body),
        "techLog": {
            "traceId": trace_id,
            "level": "info",
            "eventType": "tech_log.redaction_test",
            "message": "redaction test payload processed by V16 audit route",
            "createdAt": now_iso(),
            "userId": request_user_id(request),
        },
    }
