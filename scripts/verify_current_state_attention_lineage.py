#!/usr/bin/env python3
"""Unified-Registry + TARGET Hash-Lineage verifier for current attention state.

This verifier is intentionally scoped to the current-state attention repair. It uses
canonical Registry records as semantic authority, then recalls every active runtime
file that carries the registered identity fields (including their snake_case storage
aliases) from the compiled TARGET Hash Lineage. It does not assume every Registry
field owner/reader is itself a runtime module record; that would incorrectly reject
identity authorities such as data_platform/product_identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping

SCHEMA = "competition.current_state_attention_lineage.report.v1"
FIELD_KEYS = (
    "fieldId",
    "canonicalPath",
    "dataType",
    "ownerModule",
    "readers",
    "writers",
)
INTERFACE_KEYS = (
    "interfaceId",
    "method",
    "ownerModule",
    "path",
    "requestSchema",
    "responseSchema",
    "status",
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
    for anchor in overlay.get("requiredRuntimeAnchors") or []:
        rel = str(anchor)
        if rel not in runtime_set:
            findings.append(f"REQUIRED_RUNTIME_ANCHOR_MISSING:{rel}")

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

    canonical_interfaces = _object(root / "contracts/registry/interfaces.json").get("interfaces") or []
    interface_index = {
        str(item.get("interfaceId")): item
        for item in canonical_interfaces
        if isinstance(item, dict) and item.get("interfaceId")
    }
    interface_lineage: Dict[str, Any] = {}
    for declared_raw in overlay.get("interfaces") or []:
        if not isinstance(declared_raw, dict):
            findings.append("OVERLAY_INTERFACE_OBJECT_REQUIRED")
            continue
        declared = dict(declared_raw)
        interface_id = str(declared.get("interfaceId") or "")
        actual = interface_index.get(interface_id)
        if actual is None:
            findings.append(f"INTERFACE_NOT_IN_UNIFIED_REGISTRY:{interface_id}")
            continue
        for key in INTERFACE_KEYS:
            if actual.get(key) != declared.get(key):
                findings.append(f"INTERFACE_REGISTRY_MISMATCH:{interface_id}:{key}")

        anchors = []
        for rel_raw in declared.get("runtimeAnchors") or []:
            rel = str(rel_raw)
            if rel not in runtime_set:
                findings.append(f"INTERFACE_ANCHOR_NOT_IN_TARGET_LINEAGE:{interface_id}:{rel}")
                continue
            path = root / rel
            if not path.is_file():
                findings.append(f"INTERFACE_ANCHOR_FILE_MISSING:{interface_id}:{rel}")
                continue
            anchors.append({"path": rel, "contentHash": _file_hash(path)})

        anchor_source = "\n".join(
            (root / item["path"]).read_text(encoding="utf-8", errors="replace")
            for item in anchors
        )
        literals = []
        for literal_raw in declared.get("runtimeLiterals") or []:
            literal = str(literal_raw)
            present = literal in anchor_source
            literals.append({"literal": literal, "present": present})
            if not present:
                findings.append(f"INTERFACE_RUNTIME_LITERAL_MISSING:{interface_id}:{literal}")
        interface_lineage[interface_id] = {
            "registry": {key: actual.get(key) for key in INTERFACE_KEYS},
            "runtimeAnchors": anchors,
            "runtimeLiterals": literals,
        }

    stale_hits = []
    for rule_raw in overlay.get("forbiddenStalePredicates") or []:
        if not isinstance(rule_raw, dict):
            continue
        rule_id = str(rule_raw.get("id") or "unknown")
        literal = str(rule_raw.get("literal") or "")
        if not literal:
            continue
        for rel, (text, content_hash) in readable.items():
            count = text.count(literal)
            if not count:
                continue
            stale_hits.append(
                {
                    "predicateId": rule_id,
                    "literal": literal,
                    "path": rel,
                    "contentHash": content_hash,
                    "occurrenceCount": count,
                }
            )
            findings.append(f"STALE_CURRENT_STATE_PREDICATE:{rule_id}:{rel}")

    material = {
        "schema": SCHEMA,
        "overlayHash": overlay_expected,
        "sourceRegistryRootHash": source_root,
        "sealedRuntimeRegistryRootHash": runtime_root,
        "targetLineageGraphHash": lineage_graph.get("graphHash"),
        "runtimeFileCount": len(runtime_paths),
        "registeredFieldCount": len(overlay.get("fields") or []),
        "registeredInterfaceCount": len(overlay.get("interfaces") or []),
        "fieldLineage": field_lineage,
        "interfaceLineage": interface_lineage,
        "consumerRuntimeFiles": sorted(consumer_files),
        "stalePredicateHits": stale_hits,
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
        default="dist/competition-current-state-attention-lineage/lineage-graph.json",
    )
    parser.add_argument(
        "--overlay",
        default="governance/current_state_attention_field_semantics_v1.json",
    )
    parser.add_argument(
        "--output",
        default="dist/current-state-attention-lineage-report.json",
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
                "registeredInterfaceCount": report["registeredInterfaceCount"],
                "consumerRuntimeFileCount": len(report["consumerRuntimeFiles"]),
                "stalePredicateHitCount": len(report["stalePredicateHits"]),
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
