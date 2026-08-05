from __future__ import annotations

import json
from pathlib import Path

from tools.registry_compiler.compile_registry import (
    REGISTRY_VERSION,
    audit_registry,
    build_manifest,
    verify_committed_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts" / "registry"


def test_v23_alpha1_registry_is_read_only_and_internally_consistent() -> None:
    audit = audit_registry(ROOT)
    assert REGISTRY_VERSION == "23.0.0-alpha.1"
    assert audit["verified"] is True
    assert audit["mode"] == "report_only"
    assert audit["businessRuntimeMutated"] is False
    assert audit["deploymentBlocked"] is False
    assert audit["errors"] == []
    assert audit["counts"]["fields"] >= 20
    assert audit["counts"]["schemas"] >= 6
    assert audit["counts"]["modules"] >= 12


def test_v23_alpha1_committed_manifest_matches_registry_documents() -> None:
    verification = verify_committed_manifest(ROOT)
    assert verification["verified"] is True
    assert verification["expectedRegistryRootHash"].startswith("sha256:")
    assert verification["expectedRegistryRootHash"] == verification["committedRegistryRootHash"]
    assert build_manifest(ROOT) == json.loads(
        (REGISTRY / "registry-manifest.json").read_text(encoding="utf-8")
    )


def test_v23_alpha1_registers_execution_writeback_authority() -> None:
    fields = json.loads((REGISTRY / "fields.json").read_text(encoding="utf-8"))
    indexed = {item["fieldId"]: item for item in fields["fields"]}
    binding = indexed["execution.pipeline_item_id"]
    assert binding["identityRole"] == "writeback_authority"
    assert binding["writers"] == ["pipeline_runtime"]
    assert indexed["entity.product_id"]["identityRole"] == "business_reference"


def test_v23_alpha1_does_not_rotate_the_sealed_release_policy() -> None:
    policy = json.loads((ROOT / "release" / "release-policy.json").read_text(encoding="utf-8"))
    runtime = set(policy["runtimeGlobs"])
    attested = set(policy["attestedGlobs"])
    assert policy["productVersion"] == "22.4.0"
    assert "contracts/registry/**/*" not in runtime
    assert "tools/registry_compiler/**/*" not in runtime
    assert "contracts/registry/**/*" not in attested
    assert "tools/registry_compiler/**/*" not in attested
