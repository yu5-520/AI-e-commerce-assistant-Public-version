#!/usr/bin/env python3
"""Fail-closed field-semantic verifier for the competition TARGET lineage.

This verifier is deliberately field-first:

1. load the hash-anchored semantic overlay bound to the unified V23 field registry;
2. verify every field definition hash and both sealed Registry identities;
3. walk only file nodes present in the compiled TARGET competition hash-lineage graph;
4. locate every runtime producer/consumer by exact canonical field name and record the
   file SHA-256 that carried that field;
5. fail if a registered field has no runtime hit, a required runtime anchor is absent,
   or a known Signal-only stale predicate survives anywhere in the active closure.

It never selects files by filename similarity and never mutates runtime state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

SCHEMA = "competition.registry_field_lineage_verification.v1"
FIELD_HASH_KEYS = (
    "fieldId",
    "canonicalPath",
    "dataType",
    "ownerModule",
    "readers",
    "writers",
)
TEXT_SUFFIXES = {".py", ".js", ".json", ".html", ".css", ".sh", ".md"}


def _bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _runtime_paths(graph: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for raw in graph.get("nodes") or []:
        if not isinstance(raw, dict) or raw.get("type") != "file":
            continue
        path = str(raw.get("path") or "").strip()
        if path:
            result.append(path)
    return sorted(set(result))


def _registered_modules(root: Path) -> set[str]:
    modules = _object(root / "contracts/registry/modules.json").get("modules") or []
    return {
        str(item.get("moduleId"))
        for item in modules
        if isinstance(item, dict) and item.get("moduleId")
    }


def verify_field_lineage(
    root: Path,
    *,
    lineage_graph: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> Dict[str, Any]:
    root = root.resolve()
    findings: list[str] = []
    warnings: list[str] = []

    overlay_material = {key: value for key, value in overlay.items() if key != "overlayHash"}
    actual_overlay_hash = _hash(overlay_material)
    expected_overlay_hash = str(overlay.get("overlayHash") or "")
    if actual_overlay_hash != expected_overlay_hash:
        findings.append(
            f"FIELD_OVERLAY_HASH_MISMATCH:expected={expected_overlay_hash}:actual={actual_overlay_hash}"
        )

    registry_manifest = _object(root / "contracts/registry/registry-manifest.json")
    source_root = str(registry_manifest.get("registryRootHash") or "")
    if source_root != str(overlay.get("sourceRegistryManifestRoot") or ""):
        findings.append(
            "SOURCE_REGISTRY_ROOT_MISMATCH:"
            f"overlay={overlay.get('sourceRegistryManifestRoot')}:actual={source_root}"
        )

    runtime_projection = _object(root / "config/v23_registry_runtime.json")
    runtime_root = str(runtime_projection.get("registryRootHash") or "")
    if runtime_root != str(overlay.get("sealedRuntimeRegistryRoot") or ""):
        findings.append(
            "SEALED_RUNTIME_REGISTRY_ROOT_MISMATCH:"
            f"overlay={overlay.get('sealedRuntimeRegistryRoot')}:actual={runtime_root}"
        )

    if overlay.get("rootRotationAllowed") is not False:
        findings.append("FIELD_OVERLAY_MUST_FAIL_CLOSED_WITHOUT_REGISTRY_ROOT_ROTATION")

    registered_modules = _registered_modules(root)
    field_records: list[Dict[str, Any]] = []
    canonical_paths: list[str] = []
    for raw in overlay.get("fields") or []:
        if not isinstance(raw, dict):
            findings.append("FIELD_RECORD_OBJECT_REQUIRED")
            continue
        field = dict(raw)
        field_id = str(field.get("fieldId") or "")
        canonical = str(field.get("canonicalPath") or "")
        if not field_id or not canonical:
            findings.append(f"FIELD_ID_OR_CANONICAL_PATH_MISSING:{field_id or canonical or 'unknown'}")
            continue
        material = {key: field.get(key) for key in FIELD_HASH_KEYS}
        actual = _hash(material)
        expected = str(field.get("fieldDefinitionHash") or "")
        if actual != expected:
            findings.append(
                f"FIELD_DEFINITION_HASH_MISMATCH:{field_id}:expected={expected}:actual={actual}"
            )
        for module_id in [
            field.get("ownerModule"),
            *(field.get("readers") or []),
            *(field.get("writers") or []),
        ]:
            module = str(module_id or "")
            if module and module not in registered_modules:
                findings.append(f"FIELD_REFERENCES_UNREGISTERED_MODULE:{field_id}:{module}")
        field_records.append(field)
        canonical_paths.append(canonical)

    runtime_paths = _runtime_paths(lineage_graph)
    runtime_set = set(runtime_paths)
    for anchor in overlay.get("requiredRuntimeAnchors") or []:
        anchor_path = str(anchor)
        if anchor_path not in runtime_set:
            findings.append(f"REQUIRED_FIELD_RUNTIME_ANCHOR_MISSING:{anchor_path}")

    readable: Dict[str, tuple[str, str]] = {}
    for relative in runtime_paths:
        path = root / relative
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        readable[relative] = (text, _file_hash(path))

    field_lineage: Dict[str, Any] = {}
    consumer_files: set[str] = set()
    for field in field_records:
        field_id = str(field["fieldId"])
        canonical = str(field["canonicalPath"])
        hits = []
        for relative, (text, content_hash) in readable.items():
            count = text.count(canonical)
            if count:
                hits.append(
                    {
                        "path": relative,
                        "contentHash": content_hash,
                        "occurrenceCount": count,
                    }
                )
                consumer_files.add(relative)
        if not hits:
            findings.append(f"REGISTERED_FIELD_HAS_NO_TARGET_RUNTIME_LINEAGE:{field_id}:{canonical}")
        field_lineage[field_id] = {
            "canonicalPath": canonical,
            "fieldDefinitionHash": field.get("fieldDefinitionHash"),
            "runtimeHitCount": len(hits),
            "runtimeHits": hits,
        }

    stale_hits: list[Dict[str, Any]] = []
    for rule in overlay.get("forbiddenStalePredicates") or []:
        if not isinstance(rule, dict):
            continue
        literal = str(rule.get("literal") or "")
        rule_id = str(rule.get("id") or literal)
        if not literal:
            continue
        for relative, (text, content_hash) in readable.items():
            count = text.count(literal)
            if not count:
                continue
            stale_hits.append(
                {
                    "predicateId": rule_id,
                    "literal": literal,
                    "path": relative,
                    "contentHash": content_hash,
                    "occurrenceCount": count,
                }
            )
            findings.append(f"STALE_FIELD_SEMANTIC_PREDICATE:{rule_id}:{relative}")

    material = {
        "schema": SCHEMA,
        "overlayHash": expected_overlay_hash,
        "sourceRegistryRootHash": source_root,
        "sealedRuntimeRegistryRootHash": runtime_root,
        "targetLineageGraphHash": lineage_graph.get("graphHash"),
        "registeredFieldCount": len(field_records),
        "runtimeFileCount": len(runtime_paths),
        "fieldLineage": field_lineage,
        "consumerRuntimeFiles": sorted(consumer_files),
        "stalePredicateHits": stale_hits,
        "findings": sorted(set(findings)),
        "warnings": sorted(set(warnings)),
    }
    return {
        **material,
        "verified": not findings,
        "verificationHash": _hash(material),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=None)
    parser.add_argument(
        "--lineage-graph",
        default="dist/competition-lineage/lineage-graph.json",
    )
    parser.add_argument(
        "--overlay",
        default="governance/baseline_signal_field_semantics_v1.json",
    )
    parser.add_argument(
        "--output",
        default="dist/baseline-signal-field-lineage-report.json",
    )
    parser.add_argument("--allow-findings", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    graph = _object(root / args.lineage_graph)
    overlay = _object(root / args.overlay)
    report = verify_field_lineage(root, lineage_graph=graph, overlay=overlay)
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "registeredFieldCount": report["registeredFieldCount"],
                "consumerRuntimeFileCount": len(report["consumerRuntimeFiles"]),
                "stalePredicateHitCount": len(report["stalePredicateHits"]),
                "overlayHash": report["overlayHash"],
                "verificationHash": report["verificationHash"],
                "findings": report["findings"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["verified"] or args.allow_findings else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["verify_field_lineage"]
