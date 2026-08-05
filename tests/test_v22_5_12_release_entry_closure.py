from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.generate_release_manifest import expand
from src.services import runtime_database_prepare_v22511_service as prepare

ROOT = Path(__file__).resolve().parents[1]
MODULE = "src.services.runtime_database_prepare_v22511_service"
MODULE_PATH = "src/services/runtime_database_prepare_v22511_service.py"
REMOVED_SCRIPT = "scripts/prepare_runtime_database.py"
DEPLOY_WRAPPER_PATH = "scripts/deploy_release.sh"
DEPLOY_CORE_PATH = "src/deployment/deploy_release_core_v22516.sh"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _policy() -> dict:
    return json.loads(_read("release/release-policy.json"))


def _sealed_runtime_paths() -> set[str]:
    policy = _policy()
    return {
        path.relative_to(ROOT).as_posix()
        for path in expand(ROOT, policy["runtimeGlobs"], policy["excludeGlobs"])
    }


def _deployer_core() -> str:
    return _read(DEPLOY_CORE_PATH)


def test_schema_preparation_module_is_covered_by_existing_runtime_glob() -> None:
    policy = _policy()
    assert "src/**/*" in policy["runtimeGlobs"]
    assert REMOVED_SCRIPT not in policy["runtimeGlobs"]
    assert MODULE_PATH in _sealed_runtime_paths()
    assert DEPLOY_CORE_PATH in _sealed_runtime_paths()
    assert not (ROOT / REMOVED_SCRIPT).exists()


def test_deployer_wrapper_invokes_the_sealed_core() -> None:
    wrapper = _read(DEPLOY_WRAPPER_PATH)
    assert DEPLOY_CORE_PATH in wrapper
    assert 'BOOTSTRAP_PYTHON="$(select_bootstrap_python)"' in wrapper
    assert '/bin/bash "$CORE" "$@"' in wrapper


def test_deployer_core_invokes_the_sealed_module_directly() -> None:
    deployer = _deployer_core()
    assert '"$PYTHON" -m' in deployer
    assert MODULE in deployer
    assert "--verify-idempotent" in deployer
    assert "$TARGET/scripts/prepare_runtime_database.py" not in deployer
    assert REMOVED_SCRIPT not in deployer


def test_every_remaining_target_script_reference_in_deployer_is_sealed() -> None:
    paths = _sealed_runtime_paths()
    deployer = _deployer_core()
    referenced = {
        f"scripts/{name}"
        for name in re.findall(r'\$TARGET/scripts/([A-Za-z0-9_.-]+)', deployer)
    }
    assert referenced
    assert referenced <= paths, {
        "missingFromRelease": sorted(referenced - paths),
        "referenced": sorted(referenced),
    }


def test_canonical_module_exposes_cli_without_touching_database(
    monkeypatch,
    capsys,
) -> None:
    received: list[bool] = []

    def fake_prepare(*, verify_idempotent: bool = False) -> dict:
        received.append(verify_idempotent)
        return {
            "schema": "runtime_database_schema.prepare.v22511",
            "version": "22.5.11",
            "entryVersion": "22.5.12",
            "entryModule": MODULE,
            "verified": True,
            "idempotent": True,
            "preparedSchemaHash": "sha256:test",
            "standaloneScriptRequired": False,
        }

    monkeypatch.setattr(prepare, "prepare_runtime_database_schema", fake_prepare)
    assert prepare.main(["--verify-idempotent"]) == 0
    assert received == [True]
    payload = json.loads(capsys.readouterr().out)
    assert payload["entryVersion"] == "22.5.12"
    assert payload["entryModule"] == MODULE
    assert payload["standaloneScriptRequired"] is False


def test_release_policy_stays_pinned_to_the_root_verifier_contract() -> None:
    import importlib.util

    verifier_path = ROOT / "scripts/release_verifier.py"
    spec = importlib.util.spec_from_file_location("v22512_root_verifier", verifier_path)
    assert spec and spec.loader
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)

    policy = _policy()
    assert set(policy["runtimeGlobs"]) == verifier.EXPECTED_RUNTIME_GLOBS
    assert policy["rules"] == verifier.EXPECTED_POLICY_RULES
    assert verifier.EXPECTED_POLICY_RULES["rootVerifierOrdinaryRotationAllowed"] is False


def test_failed_v22511_artifact_cannot_recur() -> None:
    assert not (ROOT / REMOVED_SCRIPT).exists()
    assert (ROOT / MODULE_PATH).is_file()
    assert (ROOT / DEPLOY_CORE_PATH).is_file()
    deployer = _deployer_core()
    assert MODULE in deployer
    assert REMOVED_SCRIPT not in deployer
