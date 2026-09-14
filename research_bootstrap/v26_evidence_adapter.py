from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from typing import Any, Dict, Tuple

from core import (
    MockSUT,
    authority_for_effect,
    infer_primary_bias,
    policy_for_condition,
    sanitize_evidence,
    sha256_json,
)


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_PATH = ROOT / "src/services/v26_field_authority_contract_service.py"
SPEC = importlib.util.spec_from_file_location("research_v26_authority_for_evidence", AUTHORITY_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load V26 authority module: {AUTHORITY_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
FieldAuthorityViolation = MODULE.FieldAuthorityViolation
V26FieldAuthorityContract = MODULE.V26FieldAuthorityContract


class V26EvidenceAdapterError(RuntimeError):
    pass


class V26AuthorityEvidenceAdapter:
    """Research adapter using the real V26.1 authority contract as commit gate.

    This is deliberately a contract-probe SUT, not the complete application runtime.
    It executes real V26 permission checks while projecting state transitions into a
    deterministic research state so selective-authority ablations remain side-effect free.
    """

    sut_mode = "v26.1-contract-probe"
    empirical_model_evidence = False

    def __init__(self) -> None:
        self.authority = V26FieldAuthorityContract()
        self.receipt = self.authority.receipt()

    @staticmethod
    def _probe_spec(effect: Dict[str, Any], *, authorized: bool) -> Tuple[str, str, Any]:
        effect_type = effect.get("type")
        if effect_type == "fact_commit":
            return ("system" if authorized else "agent1", "snapshot.roas", 1.0)
        if effect_type == "invocation_commit":
            return ("system" if authorized else "agent3", "system_stage.call_graph", {})
        if effect_type == "temporal_overwrite":
            return ("system" if authorized else "agent1", "revision.revision_hash", "sha256:research-probe")
        raise V26EvidenceAdapterError(f"unsupported_effect_type:{effect_type}")

    def _apply_authority(self, effect: Dict[str, Any], condition: str) -> Dict[str, Any]:
        effect_type = str(effect.get("type") or "")
        authority_name = authority_for_effect(effect_type)
        if authority_name is None:
            return {"authority": None, "enforced": False, "blocked": False, "reason": None}
        policy = policy_for_condition(condition)
        authorized = bool(effect.get("authorized", False))
        enforced = bool(policy[authority_name])

        # Authorized transitions must pass the real V26 owner path even when the gate is enabled.
        if authorized:
            actor, header, value = self._probe_spec(effect, authorized=True)
            self.authority.assert_write(actor, header, value)
            return {
                "authority": authority_name,
                "enforced": enforced,
                "blocked": False,
                "reason": None,
                "actor": actor,
                "header": header,
                "probe_mode": "real-v26-owner-path",
            }

        # In an ablation where this authority is disabled, the research harness intentionally
        # bypasses that boundary. This is not a claim that production V26 permits the write.
        if not enforced:
            return {
                "authority": authority_name,
                "enforced": False,
                "blocked": False,
                "reason": None,
                "probe_mode": "authority-disabled-by-research-ablation",
            }

        actor, header, value = self._probe_spec(effect, authorized=False)
        try:
            self.authority.assert_write(actor, header, value)
        except FieldAuthorityViolation as exc:
            return {
                "authority": authority_name,
                "enforced": True,
                "blocked": True,
                "reason": str(exc),
                "actor": actor,
                "header": header,
                "probe_mode": "real-v26-block",
            }
        raise V26EvidenceAdapterError(f"v26_expected_block_missing:{effect_type}:{condition}")

    def replay(self, case: Dict[str, Any], proposal: Dict[str, Any], condition: str) -> Dict[str, Any]:
        model_view = case["model_view"]
        pre_state = copy.deepcopy(model_view.get("pre_state") or {})
        post_state = copy.deepcopy(pre_state)
        effects = copy.deepcopy(proposal.get("effects") or [])
        committed_effects = []
        blocked_effects = []
        probes = []
        authority_path = []
        primary_bias = None
        secondary_biases = []

        for effect in effects:
            probe = self._apply_authority(effect, condition)
            probes.append(probe)
            if probe.get("authority"):
                authority_path.append(probe["authority"])
            unauthorized = not bool(effect.get("authorized", False))
            bias = infer_primary_bias(str(effect.get("type") or ""))
            if unauthorized and bias:
                if primary_bias is None:
                    primary_bias = bias
                elif bias != primary_bias and bias not in secondary_biases:
                    secondary_biases.append(bias)
            if probe["blocked"]:
                blocked_effects.append(effect)
                continue
            committed_effects.append(effect)
            MockSUT._commit(post_state, effect)

        generated = any(not bool(effect.get("authorized", False)) for effect in effects)
        realized = any(not bool(effect.get("authorized", False)) for effect in committed_effects)
        policy = policy_for_condition(condition)
        raw = {
            "task_contract": copy.deepcopy(model_view["task_contract"]),
            "authorized_source_facts": copy.deepcopy(model_view["authorized_source_facts"]),
            "proposal": copy.deepcopy(proposal),
            "pre_state": pre_state,
            "post_state": post_state,
            "state_diff": {"committed_effects": committed_effects},
            "condition": condition,
            "authority_policy": policy,
            "blocked_reason_code": probes[0].get("reason") if blocked_effects and probes else None,
            "v26_probe_receipt": {
                "field_authority_version": self.receipt["version"],
                "registered_header_count": self.receipt["registeredHeaderCount"],
                "sut_mode": self.sut_mode,
                "probes": probes,
            },
        }
        return {
            "case_id": case["case_id"],
            "condition": condition,
            "proposal_hash": sha256_json(proposal),
            "pre_state_hash": sha256_json(pre_state),
            "task_contract_hash": sha256_json(model_view["task_contract"]),
            "authority_policy_hash": sha256_json(policy),
            "generated": generated,
            "realized": realized,
            "committed": bool(committed_effects),
            "blocked": bool(blocked_effects),
            "blocked_reason_code": raw["blocked_reason_code"],
            "primary_bias": primary_bias,
            "secondary_biases": secondary_biases,
            "authority_path": authority_path,
            "sut_mode": self.sut_mode,
            "empirical_model_evidence": self.empirical_model_evidence,
            "raw_evidence": raw,
            "sanitized_evidence": sanitize_evidence(raw),
        }
