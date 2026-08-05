from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_root_verifier():
    path = ROOT / "scripts" / "release_verifier.py"
    spec = importlib.util.spec_from_file_location("v224_root_policy_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_policy() -> dict:
    return json.loads(
        (ROOT / "release" / "release-policy.json").read_text(encoding="utf-8")
    )


def test_root_verifier_policy_contract_matches_release_policy_exactly() -> None:
    verifier = _load_root_verifier()
    policy = _load_policy()

    assert set(policy["runtimeGlobs"]) == verifier.EXPECTED_RUNTIME_GLOBS
    assert set(policy["attestedGlobs"]) == verifier.EXPECTED_ATTESTED_GLOBS
    assert set(policy["excludeGlobs"]) == verifier.EXPECTED_EXCLUDE_GLOBS
    assert set(policy["allowedEntrypoints"]) == verifier.EXPECTED_ENTRYPOINTS
    assert set(policy["forbiddenPaths"]) == verifier.REQUIRED_FORBIDDEN_PATHS
    assert policy["rules"] == verifier.EXPECTED_POLICY_RULES


def test_manifest_signed_policy_lists_are_canonical() -> None:
    policy = _load_policy()

    assert policy["allowedEntrypoints"] == sorted(policy["allowedEntrypoints"])
    assert policy["forbiddenPaths"] == sorted(policy["forbiddenPaths"])
    assert len(policy["allowedEntrypoints"]) == len(set(policy["allowedEntrypoints"]))
    assert len(policy["forbiddenPaths"]) == len(set(policy["forbiddenPaths"]))


def test_root_verifier_requires_sqlite_data_lineage_contract() -> None:
    verifier = _load_root_verifier()

    required_rules = {
        "validatedSqliteBackupRequiredBeforeSwitch": True,
        "sqliteDataIdentityRequiredAfterSwitch": True,
        "sqliteBackupContentHashRequired": True,
        "sqliteSchemaMustMatchDeploymentLineage": True,
    }
    for name, expected in required_rules.items():
        assert verifier.EXPECTED_POLICY_RULES[name] is expected

    assert "scripts/sqlite_backup_rotate.py" in verifier.EXPECTED_RUNTIME_GLOBS
    assert "scripts/sqlite_data_identity.py" in verifier.EXPECTED_RUNTIME_GLOBS


def test_root_verifier_requires_transport_and_test_gate_evidence() -> None:
    verifier = _load_root_verifier()

    required_attested = {
        "pytest.ini",
        "scripts/deploy_github_artifact.sh",
        ".github/workflows/release-hash-seal.yml",
        ".github/workflows/historical-contract-audit.yml",
        "docs/V22.4.0.7_GITHUB_ARTIFACT_TRANSPORT.md",
    }
    assert required_attested <= verifier.EXPECTED_ATTESTED_GLOBS
