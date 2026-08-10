#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", default="governance/runtime_callable_authority_v1.json")
    parser.add_argument("--lineage-graph", required=True)
    parser.add_argument("--output", default="dist/runtime-callable-authority-report.json")
    args = parser.parse_args()

    spec = json.loads((ROOT / args.spec).read_text(encoding="utf-8"))
    graph = json.loads((ROOT / args.lineage_graph).read_text(encoding="utf-8"))

    findings: list[str] = []
    file_hashes: dict[str, str] = {}

    graph_paths = {
        str(node.get("path"))
        for node in (graph.get("nodes") or [])
        if isinstance(node, dict) and node.get("path")
    }

    for path in spec.get("requiredRuntimeAnchors") or []:
        p = ROOT / path
        if not p.is_file():
            findings.append(f"missing_runtime_anchor:{path}")
            continue
        file_hashes[path] = _sha256(p)
        if path not in graph_paths:
            findings.append(f"anchor_not_in_target_lineage:{path}")

    for item in spec.get("callables") or []:
        owner_path = str(item["ownerPath"])
        bridge_path = "src/services/hard_interface_bridge_v2301_service.py"
        owner_source = _text(owner_path)
        bridge_source = _text(bridge_path)
        contract_source = _text(str(item["contractAuthorityPath"]))
        business_source = _text(str(item["businessValidatorPath"]))

        owner_function = f"def {item['ownerFunction']}("
        if owner_function not in owner_source:
            findings.append(f"owner_function_missing:{item['callableId']}:{owner_path}")

        for literal in item.get("forbiddenRebinds") or []:
            if literal in bridge_source:
                findings.append(f"forbidden_runtime_rebind:{item['callableId']}:{literal}")

        if str(item.get("allowedProducerHook") or ""):
            hook_literal = f"admission.{item['allowedProducerHook']} ="
            if hook_literal not in bridge_source:
                findings.append(f"registered_private_hook_missing:{item['callableId']}:{hook_literal}")

        if (
            'signalAdmissionOwner": "artifact_signal_admission_v225_service.product_signal_admission_station_v225"'
            not in bridge_source
        ):
            findings.append(f"runtime_owner_status_missing:{item['callableId']}")

        for field in item.get("requiredOutputFields") or []:
            quoted = f'"{field}"'
            if quoted not in contract_source:
                findings.append(f"contract_field_missing:{item['callableId']}:{field}")
            if field in {"businessOutputType", "generatedSignalCount"} and quoted not in owner_source:
                findings.append(f"owner_output_field_missing:{item['callableId']}:{field}")

        for output_type in item.get("allowedBusinessOutputTypes") or []:
            if output_type not in owner_source:
                findings.append(f"owner_output_type_missing:{item['callableId']}:{output_type}")
            if output_type not in business_source:
                findings.append(f"business_validator_output_type_missing:{item['callableId']}:{output_type}")

    legacy = spec.get("legacyOverlay") or {}
    overlay_path = str(legacy.get("path") or "")
    overlay_source = _text(overlay_path)
    forbidden_mutation = str(legacy.get("forbiddenMutationLiteral") or "")
    if legacy.get("mutationAllowed") is False and forbidden_mutation and forbidden_mutation in overlay_source:
        findings.append(f"legacy_contract_mutation_present:{overlay_path}:{forbidden_mutation}")
    if "verification-only" not in overlay_source and "verification-only" not in overlay_source.lower():
        findings.append(f"legacy_overlay_not_verification_only:{overlay_path}")

    report: dict[str, Any] = {
        "schema": "competition.runtime_callable_authority.report.v1",
        "verified": not findings,
        "authorityMode": spec.get("authorityMode"),
        "callableCount": len(spec.get("callables") or []),
        "anchorCount": len(spec.get("requiredRuntimeAnchors") or []),
        "fileHashes": file_hashes,
        "findings": findings,
    }
    report["verificationHash"] = "sha256:" + hashlib.sha256(
        json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
