from __future__ import annotations

import json

from activation_gate import evaluate_stage_a_gate


def current_evidence():
    return {
        "seed_pack_frozen": True,
        "condition_isolation_passed": True,
        "v26_authority_smoke_passed": True,
        "manifest_frozen": False,
        "provider_adapter_bound": False,
        "append_store_verified": True,
        "evaluator_calibration_passed": False,
        "budget_preflight_passed": True,
        "explicit_paid_execution_opt_in": False,
    }


if __name__ == "__main__":
    gate = evaluate_stage_a_gate(current_evidence())
    expected = {
        "manifest_frozen",
        "provider_adapter_bound",
        "evaluator_calibration_passed",
    }
    if gate["formal_pilot_ready"] or gate["paid_execution_allowed"]:
        raise SystemExit("activation_gate_must_remain_blocked_before_external_prerequisites")
    if set(gate["blocked_prerequisites"]) != expected:
        raise SystemExit(f"unexpected_blocked_prerequisites:{gate['blocked_prerequisites']}")
    print(json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True))
