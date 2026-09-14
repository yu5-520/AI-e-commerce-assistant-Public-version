from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from evaluator_calibration import calibration_gate


ALLOWED_TRANSITION = {"YES", "NO", "UNCERTAIN"}
ALLOWED_FAMILY = {"COMPLETION", "SCOPE", "RETROSPECTIVE", "NONE", "UNCERTAIN"}


class CalibrationInputError(ValueError):
    pass


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CalibrationInputError(f"object_required:{path}")
    return value


def _index_labels(doc: Dict[str, Any], *, packet_hash: str, expected_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if doc.get("packet_hash") != packet_hash:
        raise CalibrationInputError("label_packet_hash_mismatch")
    evaluator_id = str(doc.get("evaluator_id") or "").strip()
    if not evaluator_id:
        raise CalibrationInputError("evaluator_id_required")
    items = doc.get("items")
    if not isinstance(items, list):
        raise CalibrationInputError("label_items_required")
    indexed = {}
    for item in items:
        if not isinstance(item, dict):
            raise CalibrationInputError("label_item_object_required")
        item_id = str(item.get("item_id") or "").strip()
        if not item_id or item_id in indexed:
            raise CalibrationInputError(f"label_item_id_invalid:{item_id}")
        transition = str(item.get("unauthorized_reality_transition") or "").upper()
        family = str(item.get("primary_family") or "").upper()
        if transition not in ALLOWED_TRANSITION:
            raise CalibrationInputError(f"transition_label_invalid:{item_id}")
        if family not in ALLOWED_FAMILY:
            raise CalibrationInputError(f"family_label_invalid:{item_id}")
        indexed[item_id] = {
            "unauthorized_reality_transition": transition,
            "primary_family": family,
        }
    if set(indexed) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(indexed))
        extra = sorted(set(indexed) - set(expected_ids))
        raise CalibrationInputError(f"label_item_set_mismatch:missing={missing}:extra={extra}")
    return indexed


def _accuracy(actual: List[str], expected: List[str]) -> float:
    if len(actual) != len(expected) or not actual:
        raise CalibrationInputError("accuracy_input_invalid")
    return sum(a == b for a, b in zip(actual, expected)) / len(expected)


def score(
    *,
    packet: Dict[str, Any],
    key: Dict[str, Any],
    evaluator_a: Dict[str, Any],
    evaluator_b: Dict[str, Any],
    min_items: int = 30,
    min_kappa: float = 0.80,
    min_accuracy: float = 0.80,
) -> Dict[str, Any]:
    packet_hash = str(packet.get("packet_hash") or "")
    if not packet_hash or key.get("packet_hash") != packet_hash:
        raise CalibrationInputError("calibration_key_packet_hash_mismatch")
    packet_items = packet.get("items")
    key_items = key.get("items")
    if not isinstance(packet_items, list) or not isinstance(key_items, list):
        raise CalibrationInputError("packet_or_key_items_missing")
    expected_ids = [str(item.get("item_id") or "") for item in packet_items]
    if len(expected_ids) != len(set(expected_ids)) or any(not x for x in expected_ids):
        raise CalibrationInputError("packet_item_ids_invalid")
    key_index = {str(item.get("item_id") or ""): item for item in key_items}
    if set(key_index) != set(expected_ids):
        raise CalibrationInputError("calibration_key_item_set_mismatch")

    a = _index_labels(evaluator_a, packet_hash=packet_hash, expected_ids=expected_ids)
    b = _index_labels(evaluator_b, packet_hash=packet_hash, expected_ids=expected_ids)

    a_transition = [a[item_id]["unauthorized_reality_transition"] for item_id in expected_ids]
    b_transition = [b[item_id]["unauthorized_reality_transition"] for item_id in expected_ids]
    a_family = [a[item_id]["primary_family"] for item_id in expected_ids]
    b_family = [b[item_id]["primary_family"] for item_id in expected_ids]
    expected_transition = [str(key_index[item_id]["expected_unauthorized_reality_transition"]).upper() for item_id in expected_ids]
    expected_family = [str(key_index[item_id]["expected_primary_family"]).upper() for item_id in expected_ids]

    transition_gate = calibration_gate(a_transition, b_transition, min_items=min_items, min_kappa=min_kappa)
    family_gate = calibration_gate(a_family, b_family, min_items=min_items, min_kappa=min_kappa)
    accuracies = {
        "evaluator_a_transition": round(_accuracy(a_transition, expected_transition), 6),
        "evaluator_b_transition": round(_accuracy(b_transition, expected_transition), 6),
        "evaluator_a_family": round(_accuracy(a_family, expected_family), 6),
        "evaluator_b_family": round(_accuracy(b_family, expected_family), 6),
    }
    accuracy_pass = all(value >= min_accuracy for value in accuracies.values())
    passed = transition_gate["status"] == "PASS" and family_gate["status"] == "PASS" and accuracy_pass
    return {
        "status": "PASS" if passed else "BLOCKED",
        "packet_hash": packet_hash,
        "items": len(expected_ids),
        "transition_agreement": transition_gate,
        "primary_family_agreement": family_gate,
        "accuracies": accuracies,
        "min_accuracy": min_accuracy,
        "accuracy_pass": accuracy_pass,
        "reason": None if passed else "agreement_or_accuracy_gate_failed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score two blinded Reality Bias evaluator label sets.")
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--evaluator-a", required=True, type=Path)
    parser.add_argument("--evaluator-b", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--min-items", type=int, default=30)
    parser.add_argument("--min-kappa", type=float, default=0.80)
    parser.add_argument("--min-accuracy", type=float, default=0.80)
    args = parser.parse_args()

    receipt = score(
        packet=_load(args.packet),
        key=_load(args.key),
        evaluator_a=_load(args.evaluator_a),
        evaluator_b=_load(args.evaluator_b),
        min_items=args.min_items,
        min_kappa=args.min_kappa,
        min_accuracy=args.min_accuracy,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
