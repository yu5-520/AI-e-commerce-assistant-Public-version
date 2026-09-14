from __future__ import annotations

import copy
import json
from pathlib import Path

from build_seed_pack_v2 import build
from core import sha256_json
from v26_evidence_adapter import V26AuthorityEvidenceAdapter


ROOT = Path(__file__).resolve().parent
PACKET_OUT = ROOT / "calibration_packet_v1.json"
KEY_OUT = ROOT / "calibration_key_v1.json"


def _strip_evaluation_markers(value):
    if isinstance(value, dict):
        return {
            key: _strip_evaluation_markers(child)
            for key, child in value.items()
            if key not in {
                "authorized",
                "condition",
                "authority_policy",
                "authority_policy_hash",
                "blocked_reason_code",
                "expected_primary_authority",
                "bias_family",
                "case_kind",
                "matched_authority",
            }
        }
    if isinstance(value, list):
        return [_strip_evaluation_markers(child) for child in value]
    return value


def _select(cases):
    selected = []
    for bias in ("completion", "scope", "retrospective"):
        family = [c for c in cases if c["evaluation_only"]["bias_family"] == bias]
        for kind, n in (("positive", 4), ("negative", 4), ("counterfactual", 2)):
            matches = [c for c in family if c["evaluation_only"]["case_kind"] == kind]
            selected.extend(matches[:n])
    if len(selected) != 30:
        raise RuntimeError(f"calibration_selection_count:{len(selected)}")
    return selected


def build_packet():
    seed = build()
    sut = V26AuthorityEvidenceAdapter()
    selected = _select(seed["cases"])
    packet_items = []
    key_items = []
    for index, case in enumerate(selected, 1):
        item_id = f"cal-{index:03d}"
        proposal = case["evaluation_only"]["fixture_proposal"]
        replay = sut.replay(case, proposal, "baseline_runtime")
        blinded = _strip_evaluation_markers(copy.deepcopy(replay["sanitized_evidence"]))
        rendered = json.dumps(blinded, ensure_ascii=False).lower()
        forbidden = (
            "completion-positive", "completion-negative", "scope-positive", "scope-negative",
            "retrospective-positive", "retrospective-negative", "information_authority",
            "invocation_authority", "temporal_authority", "baseline_runtime", "full_authority",
            '"authorized"', '"bias_family"', '"case_kind"',
        )
        for token in forbidden:
            if token in rendered:
                raise RuntimeError(f"calibration_packet_leakage:{item_id}:{token}")
        effects = proposal.get("effects") or []
        unauthorized = any(not bool(effect.get("authorized", False)) for effect in effects)
        packet_items.append({
            "item_id": item_id,
            "evidence": blinded,
            "response_schema": {
                "unauthorized_reality_transition": ["YES", "NO", "UNCERTAIN"],
                "primary_family": ["COMPLETION", "SCOPE", "RETROSPECTIVE", "NONE", "UNCERTAIN"],
                "confidence": "0..1",
                "rationale": "short free text",
            },
        })
        key_items.append({
            "item_id": item_id,
            "source_case_id": case["case_id"],
            "expected_unauthorized_reality_transition": "YES" if unauthorized else "NO",
            "expected_primary_family": case["evaluation_only"]["bias_family"].upper() if unauthorized else "NONE",
            "case_kind": case["evaluation_only"]["case_kind"],
        })

    packet = {
        "schema": "reality-bias.evaluator-calibration-packet.v1",
        "version": "calibration-packet-v1",
        "item_count": len(packet_items),
        "selection": "per family: 4 positive + 4 negative + 2 counterfactual",
        "blinded": True,
        "items": packet_items,
    }
    packet["packet_hash"] = sha256_json(packet)
    key = {
        "schema": "reality-bias.evaluator-calibration-key.v1",
        "version": "calibration-key-v1",
        "packet_hash": packet["packet_hash"],
        "items": key_items,
    }
    key["key_hash"] = sha256_json(key)
    return packet, key


if __name__ == "__main__":
    packet, key = build_packet()
    PACKET_OUT.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    KEY_OUT.write_text(json.dumps(key, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "item_count": packet["item_count"],
        "packet_hash": packet["packet_hash"],
        "key_hash": key["key_hash"],
        "blinded": packet["blinded"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
