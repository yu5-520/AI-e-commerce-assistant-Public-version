from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_release_manifest import expand

ROOT = Path(__file__).resolve().parents[1]
START_SERVER = "scripts/start_server.sh"
DEPLOY_WRAPPER = "scripts/deploy_release.sh"
DEPLOY_CORE = "src/deployment/deploy_release_core_v22516.sh"
ARTIFACT_WRAPPER = "scripts/deploy_github_artifact.sh"
ARTIFACT_CORE = "src/deployment/deploy_github_artifact_core_v22516.sh"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _sealed_runtime_paths() -> set[str]:
    policy = json.loads(_read("release/release-policy.json"))
    return {
        path.relative_to(ROOT).as_posix()
        for path in expand(ROOT, policy["runtimeGlobs"], policy["excludeGlobs"])
    }


def test_startup_uses_explicit_bootstrap_before_venv_activation() -> None:
    source = _read(START_SERVER)
    assert 'BOOTSTRAP_PYTHON="$(select_bootstrap_python)"' in source
    assert '"$BOOTSTRAP_PYTHON" "$AI_RELEASE_VERIFIER_PATH"' in source
    assert source.index('BOOTSTRAP_PYTHON="$(select_bootstrap_python)"') < source.index(
        "source .venv/bin/activate"
    )
    assert '"$ROOT_DIR/.venv/bin/python"' in source
    assert 'export AI_BOOTSTRAP_PYTHON="$BOOTSTRAP_PYTHON"' in source


def test_startup_never_executes_an_unqualified_python3_command() -> None:
    source = _read(START_SERVER)
    # Candidate names such as ``python3.11`` are legal inputs to the resolver.
    # The forbidden condition is executing them directly instead of using the
    # selected BOOTSTRAP_PYTHON or the activated application ``python``.
    for forbidden in (
        'python3 "$AI_RELEASE_VERIFIER_PATH"',
        "python3.11 \"$AI_RELEASE_VERIFIER_PATH\"",
        "| python3 -c",
        "| python3.11 -c",
        "exec python3 ",
        "exec python3.11 ",
    ):
        assert forbidden not in source
    assert "readlink -f \"$CANDIDATE\"" not in source
    assert "readlink -f \"$candidate\"" not in source
    assert "ACTUAL_PIP_FREEZE_HASH=" in source
    assert "| python -c" in source


def test_deployment_wrapper_provides_a_temporary_python3_contract() -> None:
    wrapper = _read(DEPLOY_WRAPPER)
    assert DEPLOY_CORE in wrapper
    assert 'BOOTSTRAP_PYTHON="$(select_bootstrap_python)"' in wrapper
    assert 'SHIM_DIR="$(mktemp -d' in wrapper
    assert 'export PATH="$SHIM_DIR:' in wrapper
    assert 'exec "$BOOTSTRAP_PYTHON"' in wrapper
    assert '/bin/bash "$CORE" "$@"' in wrapper
    assert "/usr/local/bin/python3" not in wrapper
    assert "ln -s" not in wrapper


def test_github_artifact_wrapper_accepts_exact_commit_without_global_python3() -> None:
    wrapper = _read(ARTIFACT_WRAPPER)
    assert ARTIFACT_CORE in wrapper
    assert 'BOOTSTRAP_PYTHON="$(select_bootstrap_python)"' in wrapper
    assert "AI_RELEASE_SOURCE_COMMIT" in wrapper
    assert 'export AI_RELEASE_PYTHON="${AI_RELEASE_PYTHON:-$BOOTSTRAP_PYTHON}"' in wrapper
    assert '/bin/bash "$CORE" "$@"' in wrapper
    assert 'BOOTSTRAP_PYTHON="${AI_BOOTSTRAP_PYTHON:-python3}"' not in wrapper


def test_preserved_deployment_cores_are_covered_by_existing_src_glob() -> None:
    paths = _sealed_runtime_paths()
    assert DEPLOY_CORE in paths
    assert ARTIFACT_CORE in paths
    assert "src/**/*" in json.loads(_read("release/release-policy.json"))["runtimeGlobs"]


def test_release_policy_and_root_verifier_are_not_rotated() -> None:
    policy = json.loads(_read("release/release-policy.json"))
    assert policy["rules"]["rootVerifierOrdinaryRotationAllowed"] is False
    assert policy["releasePythonVersion"] == "3.11.9"
    assert policy["runtimeMode"] == "single_release_sealed_runtime"
