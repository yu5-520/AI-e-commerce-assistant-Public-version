#!/usr/bin/env python3
"""Verify the V25 production knowledge projection against governance originals."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict

VERSION = "25.9.0"


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("v25_knowledge_runtime_projection_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("v25_runtime_projection_module_spec_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_fields(source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema": "rag.unified_field_runtime_projection.v1",
        "version": VERSION,
        "sourceSchema": source.get("schema"),
        "sourceVersion": source.get("version"),
        "defaultDecision": source.get("defaultDecision"),
        "systemContractExclusions": source.get("systemContractExclusions") or [],
        "fields": [
            {
                "canonicalField": item.get("canonicalField"),
                "fieldHash": item.get("fieldHash"),
                "domains": item.get("domains") or [],
                "consumers": item.get("consumers") or [],
                "preferredRetrieval": item.get("preferredRetrieval") or [],
            }
            for item in source.get("fields") or []
            if isinstance(item, dict)
        ],
    }


def project_composition(source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema": "rag.knowledge_composition_runtime_projection.v1",
        "version": source.get("version"),
        "runtimeProjectionVersion": VERSION,
        "sourceSchema": source.get("schema"),
        "defaultDecision": source.get("defaultDecision"),
        "allowedPredicateOps": source.get("allowedPredicateOps") or [],
        "systemContractExclusions": source.get("systemContractExclusions") or [],
        "compositions": source.get("compositions") or [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default="dist/v25-phase3/knowledge-runtime-projection-verification.json")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output = (root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    field_path = root / "governance" / "v25" / "rag-field-registry-v25.json"
    composition_path = root / "governance" / "v25" / "knowledge-composition-table-v25.json"
    runtime_path = root / "src" / "services" / "v25_knowledge_runtime_projection.py"
    installer_path = root / "src" / "services" / "v25_knowledge_runtime_projection_install_service.py"
    bootstrap_path = root / "src" / "__init__.py"

    fields = json.loads(field_path.read_text(encoding="utf-8"))
    composition = json.loads(composition_path.read_text(encoding="utf-8"))
    module = load_module(runtime_path)

    expected_fields = project_fields(fields)
    expected_composition = project_composition(composition)
    actual_fields = module.rag_field_registry_v25()
    actual_composition = module.knowledge_composition_table_v25()

    if actual_fields != expected_fields:
        raise AssertionError("v25_runtime_field_projection_drift")
    if actual_composition != expected_composition:
        raise AssertionError("v25_runtime_composition_projection_drift")

    installer = installer_path.read_text(encoding="utf-8")
    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    assert "knowledge._field_registry = rag_field_registry_v25" in installer
    assert "knowledge._composition_table = knowledge_composition_table_v25" in installer
    assert '"governanceFilesystemReadRequired": False' in installer
    assert '"runtimePackageGovernanceExpansionRequired": False' in installer
    projection_pos = bootstrap.index("install_v25_knowledge_runtime_projection()")
    unified_pos = bootstrap.index("install_v25_unified_agent_knowledge()")
    ingress_pos = bootstrap.index("install_v25_agent_input_ingress()")
    assert projection_pos < unified_pos < ingress_pos

    material = {
        "schema": "v25.knowledge_runtime_projection_verification.v1",
        "version": VERSION,
        "verified": True,
        "fieldProjectionExact": True,
        "compositionProjectionExact": True,
        "registeredFieldCount": len(actual_fields.get("fields") or []),
        "registeredCompositionCount": len(actual_composition.get("compositions") or []),
        "governanceFilesystemReadRequiredAtRuntime": False,
        "runtimePackageGovernanceExpansionRequired": False,
        "bootstrapProjectionBeforeKnowledgeIngress": True,
        "governanceFieldRegistryHash": canonical_hash(fields),
        "governanceCompositionTableHash": canonical_hash(composition),
        "runtimeFieldProjectionHash": canonical_hash(actual_fields),
        "runtimeCompositionProjectionHash": canonical_hash(actual_composition),
    }
    report = {**material, "verificationHash": canonical_hash(material)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
