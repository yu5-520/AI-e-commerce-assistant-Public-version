"""V21.4.3 composed pipeline runtime recovery.

The established V20.28 protocol repairs remain intact. V21.4.2 stale Agent2
leases are recovered first so fair scheduling can see the requeued item in the
same worker tick. V21.4.3 reports dead-letter and retry outcomes explicitly.
"""

from __future__ import annotations

from typing import Any, Dict

from src.services.agent2_runtime_resilience_v2143_service import (
    AGENT2_FAILURE_GOVERNANCE_VERSION,
    AGENT2_LEASE_VERSION,
    agent2_resilience_summary,
    recover_stale_agent2_claims,
)
from src.services.pipeline_runtime_recovery_v2028_service import (
    recover_pipeline_runtime_breakpoints as recover_legacy_breakpoints,
)

PIPELINE_RUNTIME_RECOVERY_VERSION = "21.4.3"


def recover_pipeline_runtime_breakpoints(
    data_version: str | None = None,
    *,
    limit: int = 500,
) -> Dict[str, Any]:
    agent2 = recover_stale_agent2_claims(data_version, limit=limit)
    legacy = recover_legacy_breakpoints(data_version, limit=limit)
    resolved = data_version or legacy.get("dataVersion") or agent2.get("dataVersion")
    recovered = int(agent2.get("requeuedCount") or 0) + int(
        agent2.get("deadLetteredCount") or 0
    ) + int(legacy.get("recoveredItemCount") or 0)
    return {
        "version": PIPELINE_RUNTIME_RECOVERY_VERSION,
        "leaseVersion": AGENT2_LEASE_VERSION,
        "failureGovernanceVersion": AGENT2_FAILURE_GOVERNANCE_VERSION,
        "dataVersion": resolved,
        "ran": bool(agent2.get("ran") or legacy.get("ran")),
        "recoveredItemCount": recovered,
        "agent2LeaseRecovery": agent2,
        "legacyProtocolRecovery": legacy,
        "resilienceSummary": agent2_resilience_summary(resolved),
        "rule": (
            "V21.4.2 recovers expired Agent2 leases; V21.4.3 retries only "
            "transient failures and keeps semantic failures terminal."
        ),
    }
