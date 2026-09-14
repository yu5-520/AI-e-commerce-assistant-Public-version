from __future__ import annotations

from typing import Any, Dict


REQUIRED_PREREQUISITES = (
    "seed_pack_frozen",
    "condition_isolation_passed",
    "v26_authority_smoke_passed",
    "manifest_frozen",
    "provider_adapter_bound",
    "append_store_verified",
    "evaluator_calibration_passed",
    "budget_preflight_passed",
)


def evaluate_stage_a_gate(evidence: Dict[str, Any]) -> Dict[str, Any]:
    missing = [key for key in REQUIRED_PREREQUISITES if evidence.get(key) is not True]
    pilot_ready = not missing
    explicit_opt_in = evidence.get("explicit_paid_execution_opt_in") is True
    return {
        "gate": "Stage-A-Activation-v1",
        "formal_pilot_ready": pilot_ready,
        "paid_execution_allowed": pilot_ready and explicit_opt_in,
        "blocked_prerequisites": missing,
        "explicit_paid_execution_opt_in": explicit_opt_in,
        "status": "READY" if pilot_ready else "BLOCKED",
    }
