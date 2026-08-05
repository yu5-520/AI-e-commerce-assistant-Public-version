"""V21.3 scheduling with V21.4.2/V21.4.3 Agent2 resilience overlay.

Fair dataVersion scheduling remains V21.3. Before runnable selection, expired
Agent2 leases are recovered. Backoff items are not runnable until retry_after is
due, so a transient provider failure cannot create a hot retry loop.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from src.repositories.sqlite_repository import connect
from src.services.agent2_runtime_resilience_v2143_service import (
    AGENT2_FAILURE_GOVERNANCE_VERSION,
    AGENT2_LEASE_VERSION,
    agent2_resilience_summary,
    ensure_agent2_runtime_columns,
    recover_stale_agent2_claims,
)

AGENT_PIPELINE_GOVERNANCE_VERSION = "21.3"

RUNNABLE_STAGES = (
    "agent1_pending",
    "agent1_completed",
    "action_pack_ready",
    "agent2_completed",
    "sop_mapped",
)
RUNNABLE_STATUSES = ("queued", "ready", "retry", "completed")


def normalize_admission_limits(
    *,
    max_signals: int = 160,
    min_admitted: int | None = None,
    max_admitted: int | None = None,
) -> Dict[str, int]:
    signal_limit = max(1, min(5000, int(max_signals or 160)))
    minimum = max(0, int(min_admitted or 0))
    maximum = int(max_admitted or signal_limit)
    maximum = max(1, min(signal_limit, maximum))
    minimum = min(minimum, maximum)
    return {
        "maxSignals": signal_limit,
        "minAdmitted": minimum,
        "maxAdmitted": maximum,
    }


def _table_exists(conn: Any, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    )


def _due_clause() -> str:
    return "(current_stage!='action_pack_ready' OR retry_after IS NULL OR retry_after<=?)"


def _recover_expired_leases() -> None:
    # Idempotent and bounded. Running this before selection makes a crashed item
    # visible to the same fair scheduler tick instead of waiting for a manual job.
    recover_stale_agent2_claims(limit=200)


def has_runnable_items(data_version: str | None) -> bool:
    if not data_version:
        return False
    ensure_agent2_runtime_columns()
    stage_marks = ",".join("?" for _ in RUNNABLE_STAGES)
    status_marks = ",".join("?" for _ in RUNNABLE_STATUSES)
    now = datetime.now().isoformat()
    with connect() as conn:
        if not _table_exists(conn, "pipeline_items"):
            return False
        row = conn.execute(
            f"""
            SELECT 1
            FROM pipeline_items
            WHERE data_version=?
              AND current_stage IN ({stage_marks})
              AND status IN ({status_marks})
              AND {_due_clause()}
            LIMIT 1
            """,
            (data_version, *RUNNABLE_STAGES, *RUNNABLE_STATUSES, now),
        ).fetchone()
    return bool(row)


def select_runnable_data_version(preferred: str | None = None) -> str | None:
    _recover_expired_leases()
    if preferred and has_runnable_items(preferred):
        return preferred

    ensure_agent2_runtime_columns()
    stage_marks = ",".join("?" for _ in RUNNABLE_STAGES)
    status_marks = ",".join("?" for _ in RUNNABLE_STATUSES)
    now = datetime.now().isoformat()
    with connect() as conn:
        if not _table_exists(conn, "pipeline_items"):
            return None
        row = conn.execute(
            f"""
            SELECT data_version,
                   MIN(COALESCE(priority,50)) AS min_priority,
                   MIN(COALESCE(updated_at,created_at)) AS oldest_at
            FROM pipeline_items
            WHERE data_version IS NOT NULL
              AND TRIM(data_version)!=''
              AND current_stage IN ({stage_marks})
              AND status IN ({status_marks})
              AND {_due_clause()}
            GROUP BY data_version
            ORDER BY min_priority ASC,oldest_at ASC,data_version ASC
            LIMIT 1
            """,
            (*RUNNABLE_STAGES, *RUNNABLE_STATUSES, now),
        ).fetchone()
    return str(row["data_version"]) if row and row["data_version"] else None


def runtime_governance_summary() -> Dict[str, Any]:
    selected = select_runnable_data_version()
    try:
        resilience = agent2_resilience_summary(selected)
    except Exception as exc:
        resilience = {
            "version": AGENT2_FAILURE_GOVERNANCE_VERSION,
            "error": str(exc),
        }
    return {
        "version": AGENT_PIPELINE_GOVERNANCE_VERSION,
        "selectedRunnableDataVersion": selected,
        "runnableStages": list(RUNNABLE_STAGES),
        "runnableStatuses": list(RUNNABLE_STATUSES),
        "admissionPolicy": normalize_admission_limits(),
        "forceNewSnapshotPerWorkerTick": False,
        "agent2LeaseVersion": AGENT2_LEASE_VERSION,
        "agent2FailureGovernanceVersion": AGENT2_FAILURE_GOVERNANCE_VERSION,
        "agent2Resilience": resilience,
        "retryScheduling": "retry_after_due_only",
        "staleLeaseRecovery": "before_runnable_selection",
        "rule": (
            "V21.3 keeps fair dataVersion scheduling; V21.4.2/V21.4.3 recover "
            "expired claims and exclude backoff items until due."
        ),
    }
