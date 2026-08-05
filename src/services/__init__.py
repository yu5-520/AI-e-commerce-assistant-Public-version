"""Service layer for product-oriented API routes.

V19.12.2 installs a write-boundary guard as soon as the service package is
imported. This prevents any legacy worker or route from writing removed V19.9
SOP/template decisions into task_generation_decisions_v15.
"""

from __future__ import annotations

try:
    from src.services.decision_write_guard_v19122_service import install_decision_write_guard

    DECISION_WRITE_GUARD = install_decision_write_guard()
except Exception as exc:  # pragma: no cover - guard must not break package import
    DECISION_WRITE_GUARD = {"version": "19.12.2", "installed": False, "error": str(exc)}
