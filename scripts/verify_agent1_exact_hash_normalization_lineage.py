#!/usr/bin/env python3
"""Unified-Registry + TARGET Hash-Lineage verifier for Agent1 exact normalization.

The verifier proves that execution identity stays owned by the registered
``itemExecutionId + inputContentHash`` contract after the provider response is
accepted. It recalls every TARGET runtime consumer of the registered fields, checks
the active Agent1 Registry module contract, and fail-closes if V22.5.9 re-introduces
legacy identity rematching or an old hard-coded action-family whitelist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping

SCHEMA = "competition.agent1_exact_hash_normalization_lineage.report.v1"
FIELD_KEYS = (
    "fieldId",
    "canonicalPath",
    "dataType",
    "ownerModule",
    "readers",
    "writers",
)
TEXT_SUFFIXES = {".py", ".js", ".json", ".html", ".css", ".sh", ".md"}


def _stable(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _runtime_paths(graph: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            str(node.get("path"))
            for node in (graph.get("nodes") or [])
            if isinstance(node, dict)
            and node.get("type") == "file"
            and node.get("path")
        }
    )


def _snake_case(value: str) -> str:
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).lower()


def _field_aliases(canonical: str) -> list[str]:
    aliases = [canonical]
    snake = _snake_case(canonical)
    if snake and snake != canonical:
        aliases.append(snake)
    return aliases


def verify(
    root: Path,
    *,
    lineage_graph: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> Dict[str, Any]:
    root = root.resolve()
    findings: list[str] = []

    overlay_material = {key: value for key, value in overlay.items() if key != "overlayHash"}
    overlay_actual = _hash(overlay_material)
    overlay_expected = str(overlay.get("overlayHash") or "")
    if overlay_actual != overlay_expected:
        findings.append(
            f"OVERLAY_HASH_MISMATCH:expected={overlay_expected}:actual={overlay_actual}"
        )

    registry_manifest = _object(root / "contracts/registry/registry-manifest.json")
    source_root = str(registry_manifest.get("registryRootHash") or "")
    expected_source_root = str(overlay.get("sourceRegistryManifestRoot") or "")
    if source_root != expected_source_root:
        findings.append(
            f"SOURCE_REGISTRY_ROOT_MISMATCH:expected={expected_source_root}:actual={source_root}"
        )

    runtime_registry = _object(root / "config/v23_registry_runtime.json")
    runtime_root = str(runtime_registry.get("registryRootHash") or "")
    expected_runtime_root = str(overlay.get("sealedRuntimeRegistryRoot") or "")
    if runtime_root != expected_runtime_root:
        findings.append(
            f"SEALED_RUNTIME_REGISTRY_ROOT_MISMATCH:expected={expected_runtime_root}:actual={runtime_root}"
        )
    if overlay.get("rootRotationAllowed") is not False:
        findings.append("REGISTRY_ROOT_ROTATION_MUST_REMAIN_DISABLED")

    runtime_paths = _runtime_paths(lineage_graph)
    runtime_set = set(runtime_paths)
    for anchor_raw in overlay.get("requiredRuntimeAnchors") or []:
        anchor = str(anchor_raw)
        if anchor not in runtime_set:
            findings.append(f"REQUIRED_RUNTIME_ANCHOR_MISSING:{anchor}")

    readable: Dict[str, tuple[str, str]] = {}
    for rel in runtime_paths:
        path = root / rel
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            findings.append(f"RUNTIME_FILE_UNREADABLE:{rel}:{type(exc).__name__}")
            continue
        readable[rel] = (text, _file_hash(path))

    canonical_fields = _object(root / "contracts/registry/fields.json").get("fields") or []
    field_index = {
        str(item.get("fieldId")): item
        for item in canonical_fields
        if isinstance(item, dict) and item.get("fieldId")
    }
    field_lineage: Dict[str, Any] = {}
    consumer_files: set[str] = set()

    for declared_raw in overlay.get("fields") or []:
        if not isinstance(declared_raw, dict):
            findings.append("OVERLAY_FIELD_OBJECT_REQUIRED")
            continue
        declared = dict(declared_raw)
        field_id = str(declared.get("fieldId") or "")
        canonical = str(declared.get("canonicalPath") or "")
        actual = field_index.get(field_id)
        if actual is None:
            findings.append(f"FIELD_NOT_IN_UNIFIED_REGISTRY:{field_id}")
            continue

        for key in FIELD_KEYS:
            if actual.get(key) != declared.get(key):
                findings.append(f"FIELD_REGISTRY_MISMATCH:{field_id}:{key}")

        definition_material = {key: declared.get(key) for key in FIELD_KEYS}
        definition_hash = _hash(definition_material)
        if definition_hash != str(declared.get("fieldDefinitionHash") or ""):
            findings.append(
                f"FIELD_DEFINITION_HASH_MISMATCH:{field_id}:"
                f"expected={declared.get('fieldDefinitionHash')}:actual={definition_hash}"
            )

        aliases = _field_aliases(canonical)
        hits = []
        for rel, (text, content_hash) in readable.items():
            alias_hits = {
                alias: text.count(alias)
                for alias in aliases
                if alias and text.count(alias)
            }
            if not alias_hits:
                continue
            hits.append(
                {
                    "path": rel,
                    "contentHash": content_hash,
                    "aliases": alias_hits,
                    "occurrenceCount": sum(alias_hits.values()),
                }
            )
            consumer_files.add(rel)
        if not hits:
            findings.append(f"REGISTERED_FIELD_HAS_NO_TARGET_RUNTIME_LINEAGE:{field_id}:{canonical}")
        field_lineage[field_id] = {
            "canonicalPath": canonical,
            "aliases": aliases,
            "fieldDefinitionHash": declared.get("fieldDefinitionHash"),
            "runtimeHitCount": len(hits),
            "runtimeHits": hits,
        }

    modules = _object(root / "contracts/registry/modules.json").get("modules") or []
    module_index = {
        str(item.get("moduleId")): item
        for item in modules
        if isinstance(item, dict) and item.get("moduleId")
    }
    module_contracts: Dict[str, Any] = {}
    for expected_raw in overlay.get("requiredModuleContracts") or []:
        if not isinstance(expected_raw, dict):
            findings.append("OVERLAY_MODULE_CONTRACT_OBJECT_REQUIRED")
            continue
        expected = dict(expected_raw)
        module_id = str(expected.get("moduleId") or "")
        actual = module_index.get(module_id)
        if actual is None:
            findings.append(f"MODULE_NOT_IN_UNIFIED_REGISTRY:{module_id}")
            continue
        for key in ("runner", "reads", "writes"):
            if actual.get(key) != expected.get(key):
                findings.append(f"MODULE_CONTRACT_MISMATCH:{module_id}:{key}")
        module_contracts[module_id] = {
            "runner": actual.get("runner"),
            "reads": actual.get("reads") or [],
            "writes": actual.get("writes") or [],
            "status": actual.get("status"),
        }

    predicate_evidence: list[Dict[str, Any]] = []
    for rule_raw in overlay.get("requiredPathPredicates") or []:
        if not isinstance(rule_raw, dict):
            continue
        rule_id = str(rule_raw.get("id") or "unknown")
        rel = str(rule_raw.get("path") or "")
        literal = str(rule_raw.get("literal") or "")
        current = readable.get(rel)
        present = bool(current and literal and literal in current[0])
        predicate_evidence.append(
            {
                "predicateId": rule_id,
                "path": rel,
                "contentHash": current[1] if current else None,
                "required": True,
                "present": present,
            }
        )
        if not present:
            findings.append(f"REQUIRED_AGENT1_PREDICATE_MISSING:{rule_id}:{rel}")

    for rule_raw in overlay.get("forbiddenPathPredicates") or []:
        if not isinstance(rule_raw, dict):
            continue
        rule_id = str(rule_raw.get("id") or "unknown")
        rel = str(rule_raw.get("path") or "")
        literal = str(rule_raw.get("literal") or "")
        current = readable.get(rel)
        present = bool(current and literal and literal in current[0])
        predicate_evidence.append(
            {
                "predicateId": rule_id,
                "path": rel,
                "contentHash": current[1] if current else None,
                "required": False,
                "present": present,
            }
        )
        if present:
            findings.append(f"FORBIDDEN_AGENT1_PREDICATE_PRESENT:{rule_id}:{rel}")

    legacy_path = "src/services/real_product_judgment_agent_v196_service.py"
    legacy_source = readable.get(legacy_path)
    legacy_whitelist_evidence = {
        "path": legacy_path,
        "contentHash": legacy_source[1] if legacy_source else None,
        "hardCodedWhitelistStillExistsInLegacy": bool(
            legacy_source and "ALLOWED_ACTION_FAMILIES" in legacy_source[0]
        ),
        "note": (
            "Legacy whitelist may remain for legacy callers; the repair requires that "
            "the exact-hash V22.5.10 path never delegates deletion authority to it."
        ),
    }

    material = {
        "schema": SCHEMA,
        "overlayHash": overlay_expected,
        "sourceRegistryRootHash": source_root,
        "sealedRuntimeRegistryRootHash": runtime_root,
        "targetLineageGraphHash": lineage_graph.get("graphHash"),
        "runtimeFileCount": len(runtime_paths),
        "registeredFieldCount": len(overlay.get("fields") or []),
        "registeredModuleContractCount": len(overlay.get("requiredModuleContracts") or []),
        "fieldLineage": field_lineage,
        "moduleContracts": module_contracts,
        "consumerRuntimeFiles": sorted(consumer_files),
        "predicateEvidence": predicate_evidence,
        "legacyWhitelistEvidence": legacy_whitelist_evidence,
        "findings": sorted(set(findings)),
    }
    return {
        **material,
        "verified": not findings,
        "verificationHash": _hash(material),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=None)
    parser.add_argument(
        "--lineage-graph",
        default="dist/competition-agent1-normalization-lineage/lineage-graph.json",
    )
    parser.add_argument(
        "--overlay",
        default="governance/agent1_exact_hash_normalization_field_semantics_v1.json",
    )
    parser.add_argument(
        "--output",
        default="dist/agent1-exact-hash-normalization-lineage-report.json",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    graph = _object(root / args.lineage_graph)
    overlay = _object(root / args.overlay)
    report = verify(root, lineage_graph=graph, overlay=overlay)

    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "registeredFieldCount": report["registeredFieldCount"],
                "registeredModuleContractCount": report["registeredModuleContractCount"],
                "consumerRuntimeFileCount": len(report["consumerRuntimeFiles"]),
                "verificationHash": report["verificationHash"],
                "findings": report["findings"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["verify"]
