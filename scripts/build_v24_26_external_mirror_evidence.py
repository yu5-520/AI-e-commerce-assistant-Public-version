#!/usr/bin/env python3
"""Build sealed V24.26 external production mirror evidence from append-only JSONL receipts.

The builder does not decide parity. It only groups immutable receipts into windows and attaches
an operator/control-plane proof. Java remains the fail-closed verifier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

DOMAINS = {"INFORMATION", "INVOCATION", "TEMPORAL", "MUTATION"}
REQUIRED_SAMPLE_KEYS = {
    "windowId",
    "generationSeq",
    "generationHash",
    "fencingToken",
    "sampleId",
    "domain",
    "inputHash",
    "productionResultHash",
    "shadowResultHash",
    "shadowWriteAttempted",
    "productionOwnerUnchanged",
}
REQUIRED_CONTROL_KEYS = {
    "inFlightDrainVerified",
    "staleGenerationBlocked",
    "freshGenerationAdmissible",
    "rollbackWindowVerified",
    "preparedGenerationInvalidAfterRollback",
    "productionOwnerBoundaryStable",
    "productionMutationAllowed",
}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def require_hash(value: Any, name: str) -> str:
    text = str(value or "")
    if len(text) != 71 or not text.startswith("sha256:"):
        raise ValueError(f"{name}_invalid")
    int(text[7:], 16)
    return text


def load_receipts(path: Path) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        item = json.loads(raw)
        missing = sorted(REQUIRED_SAMPLE_KEYS - set(item))
        if missing:
            raise ValueError(f"receipt_line_{line_no}_missing:{','.join(missing)}")
        if item["domain"] not in DOMAINS:
            raise ValueError(f"receipt_line_{line_no}_unknown_domain:{item['domain']}")
        require_hash(item["generationHash"], f"receipt_line_{line_no}_generationHash")
        require_hash(item["inputHash"], f"receipt_line_{line_no}_inputHash")
        require_hash(item["productionResultHash"], f"receipt_line_{line_no}_productionResultHash")
        require_hash(item["shadowResultHash"], f"receipt_line_{line_no}_shadowResultHash")
        if item["shadowWriteAttempted"] is not False:
            raise ValueError(f"receipt_line_{line_no}_shadow_write_attempted")
        if item["productionOwnerUnchanged"] is not True:
            raise ValueError(f"receipt_line_{line_no}_production_owner_changed")
        receipts.append(item)
    if not receipts:
        raise ValueError("external_mirror_receipts_empty")
    return receipts


def load_control(path: Path) -> dict[str, Any]:
    control = json.loads(path.read_text(encoding="utf-8"))
    missing = sorted(REQUIRED_CONTROL_KEYS - set(control))
    if missing:
        raise ValueError("control_proof_missing:" + ",".join(missing))
    return control


def build(receipts: list[dict[str, Any]], control: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    identity: dict[str, tuple[int, str, int]] = {}
    seen_sample_ids: set[str] = set()

    for item in receipts:
        sample_id = str(item["sampleId"])
        if sample_id in seen_sample_ids:
            raise ValueError(f"duplicate_sample_id:{sample_id}")
        seen_sample_ids.add(sample_id)
        window_id = str(item["windowId"])
        current_identity = (
            int(item["generationSeq"]),
            str(item["generationHash"]),
            int(item["fencingToken"]),
        )
        previous = identity.setdefault(window_id, current_identity)
        if previous != current_identity:
            raise ValueError(f"mixed_generation_inside_window:{window_id}")
        grouped[window_id].append({
            "sampleId": sample_id,
            "domain": item["domain"],
            "inputHash": item["inputHash"],
            "productionResultHash": item["productionResultHash"],
            "shadowResultHash": item["shadowResultHash"],
            "shadowWriteAttempted": False,
            "productionOwnerUnchanged": True,
            "receiptHash": item.get("receiptHash") or sha256({
                "sampleId": sample_id,
                "domain": item["domain"],
                "inputHash": item["inputHash"],
                "productionResultHash": item["productionResultHash"],
                "shadowResultHash": item["shadowResultHash"],
                "shadowWriteAttempted": False,
                "productionOwnerUnchanged": True,
            }),
        })

    windows: list[dict[str, Any]] = []
    for window_id in sorted(grouped):
        generation_seq, generation_hash, fencing_token = identity[window_id]
        samples = sorted(grouped[window_id], key=lambda row: (row["domain"], row["sampleId"]))
        windows.append({
            "windowId": window_id,
            "sealed": True,
            "generationSeq": generation_seq,
            "generationHash": generation_hash,
            "fencingToken": fencing_token,
            "samples": samples,
            "sampleSetHash": sha256(samples),
        })

    evidence: dict[str, Any] = {
        "schema": "v24.production_mirror_evidence.v1",
        "version": "24.26.0",
        "evidenceSource": "EXTERNAL_PRODUCTION_MIRROR",
        "windows": windows,
    }
    for key in sorted(REQUIRED_CONTROL_KEYS):
        evidence[key] = control[key]
    if "productionOwnerHash" in control:
        evidence["productionOwnerHash"] = control["productionOwnerHash"]
    evidence["evidenceHash"] = sha256(evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipts", required=True, help="append-only JSONL mirror receipts")
    parser.add_argument("--control-proof", required=True, help="JSON drain/fencing/rollback proof")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    evidence = build(load_receipts(Path(args.receipts)), load_control(Path(args.control_proof)))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical(evidence) + "\n", encoding="utf-8")
    print(canonical({
        "verified": True,
        "evidenceSource": evidence["evidenceSource"],
        "windowCount": len(evidence["windows"]),
        "sampleCount": sum(len(window["samples"]) for window in evidence["windows"]),
        "evidenceHash": evidence["evidenceHash"],
    }))


if __name__ == "__main__":
    main()
