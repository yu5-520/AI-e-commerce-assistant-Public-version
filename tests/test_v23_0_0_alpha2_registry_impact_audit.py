from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.registry_compiler.alpha2_audit import run_alpha2_audit
from tools.registry_compiler.registry_graph import build_dependency_graph, calculate_impact
from tools.registry_compiler.repository_audit import scan_repository


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def repository_scan() -> dict:
    return scan_repository(ROOT)


def test_alpha2_dependency_graph_is_deterministic() -> None:
    first = build_dependency_graph(ROOT)
    second = build_dependency_graph(ROOT)
    assert first == second
    assert first["version"] == "23.0.0-alpha.2"
    assert first["mode"] == "report_only"
    assert first["graphHash"].startswith("sha256:")
    assert "agent1_runtime" in first["fields"]["agent1.locked_action_family"]
    assert "action_pack" in first["downstream"]["agent1_runtime"]


def test_alpha2_field_change_calculates_full_downstream_impact() -> None:
    impact = calculate_impact(ROOT, changed_fields=["agent1.locked_action_family"])
    direct = set(impact["directAffectedModules"])
    affected = set(impact["theoreticalAffectedModules"])
    assert impact["mode"] == "report_only"
    assert impact["impactHash"].startswith("sha256:")
    assert {"agent1_runtime", "action_pack", "agent2_runtime", "agent3_runtime"} <= direct
    assert {"agent2_input_projection", "agent3_input_projection", "task_mapping"} <= affected
    assert {"task_pool", "frontend_view"} <= affected
    assert "artifact_transport" in set(impact["unaffectedModules"])


def test_alpha2_repository_scan_resolves_critical_runner(repository_scan: dict) -> None:
    assert repository_scan["version"] == "23.0.0-alpha.2"
    assert repository_scan["mode"] == "report_only"
    assert repository_scan["businessRuntimeMutated"] is False
    assert repository_scan["deploymentBlocked"] is False
    assert repository_scan["repositoryScanHash"].startswith("sha256:")
    assert repository_scan["summary"]["sourceFileCount"] > 0
    assert repository_scan["summary"]["registeredModuleCount"] >= 12
    audit = repository_scan["moduleAudits"]["agent1_runtime"]
    assert audit["runnerFileExists"] is True
    assert audit["runnerSymbolExists"] is True
    assert audit["runner"].endswith(":run_agent1_projected_inputs")


def test_alpha2_audit_connects_registry_graph_scan_and_impact_hashes() -> None:
    report = run_alpha2_audit(ROOT, changed_fields=["agent1.locked_action_family"])
    manifest = json.loads(
        (ROOT / "contracts" / "registry" / "registry-manifest.json").read_text(encoding="utf-8")
    )
    assert report["version"] == "23.0.0-alpha.2"
    assert report["verifiedRegistry"] is True
    assert report["businessRuntimeMutated"] is False
    assert report["databaseMutated"] is False
    assert report["providerCallsExecuted"] == 0
    assert report["deploymentBlocked"] is False
    assert report["hashLineage"]["registryRootHash"] == manifest["registryRootHash"]
    for key in ("graphHash", "repositoryScanHash", "impactHash"):
        assert report["hashLineage"][key].startswith("sha256:")
    assert report["auditRootHash"].startswith("sha256:")


def test_alpha2_preserves_sealed_release_policy_boundary() -> None:
    policy = json.loads((ROOT / "release" / "release-policy.json").read_text(encoding="utf-8"))
    assert policy["productVersion"] == "22.4.0"
    assert "contracts/registry/**/*" not in set(policy["runtimeGlobs"])
    assert "tools/registry_compiler/**/*" not in set(policy["runtimeGlobs"])
    assert policy["rules"]["rootVerifierOrdinaryRotationAllowed"] is False
