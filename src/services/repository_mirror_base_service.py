"""Shared helpers for SQLite-first PostgreSQL mirror services.

Business services keep their domain-specific repository calls. This module only
centralizes common mirror control flow: mode check, event-loop guard, skipped
response, failed response, and summary shape.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Dict, List

REPOSITORY_MIRROR_BASE_VERSION = "5.3.8"
SUPPORTED_REPOSITORY_MODES = {"sqlite", "postgres", "hybrid"}


def repository_mode() -> str:
    mode = os.getenv("DB_REPOSITORY_MODE", "sqlite").lower()
    return mode if mode in SUPPORTED_REPOSITORY_MODES else "sqlite"


def mirror_enabled() -> bool:
    return repository_mode() in {"hybrid", "postgres"}


def mirror_skipped(action: str, *, reason: str = "DB_REPOSITORY_MODE=sqlite", version: str = REPOSITORY_MIRROR_BASE_VERSION) -> Dict[str, Any]:
    return {"version": version, "action": action, "mode": repository_mode(), "mirrored": False, "status": "skipped", "reason": reason}


def mirror_failed(action: str, exc: Exception, *, version: str = REPOSITORY_MIRROR_BASE_VERSION, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    mode = repository_mode()
    payload: Dict[str, Any] = {"version": version, "action": action, "mode": mode, "mirrored": False, "status": "failed", "error": str(exc), "fallback": mode == "hybrid"}
    if extra:
        payload.update(extra)
    return payload


def run_mirror(coro: Awaitable[Dict[str, Any]], action: str, *, version: str = REPOSITORY_MIRROR_BASE_VERSION) -> Dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return {"version": version, "action": action, "mirrored": False, "status": "skipped", "reason": "event_loop_running; use async mirror path later"}


def mirror_summary(*, name: str, resources: List[str], version: str = REPOSITORY_MIRROR_BASE_VERSION, rule: str | None = None, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    mode = repository_mode()
    payload: Dict[str, Any] = {"version": version, "name": name, "mode": mode, "enabled": mode in {"hybrid", "postgres"}, "sqliteFirst": True, "resources": resources, "rule": rule or "SQLite write succeeds first; PostgreSQL mirror failure never breaks Demo runtime in hybrid mode."}
    if extra:
        payload.update(extra)
    return payload
