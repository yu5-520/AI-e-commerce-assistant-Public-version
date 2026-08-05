from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "release" / "release-policy.json"
MODULE_PATH = ROOT / "src" / "services" / "rerun_agent3_as_test_task_cli_v23217.py"
RUNNER_PATH = ROOT / "src" / "services" / "agent3_test_task_runner_v23217_service.py"
LEGACY_SCRIPT_PATH = "scripts/rerun_agent3_as_test_task.py"


def _policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_agent3_test_task_cli_is_covered_by_existing_sealed_src_runtime() -> None:
    policy = _policy()
    runtime_globs = set(policy.get("runtimeGlobs") or [])

    assert "src/**/*" in runtime_globs
    assert MODULE_PATH.is_file()
    assert RUNNER_PATH.is_file()
    assert LEGACY_SCRIPT_PATH not in runtime_globs


def test_agent3_test_task_cli_does_not_rotate_root_release_policy() -> None:
    policy = _policy()
    attested_globs = set(policy.get("attestedGlobs") or [])

    assert LEGACY_SCRIPT_PATH not in attested_globs
    assert policy["rules"]["rootVerifierOrdinaryRotationAllowed"] is False


def test_module_cli_exposes_formal_python_m_entrypoint() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert "def build_parser()" in source
    assert "def main(" in source
    assert 'if __name__ == "__main__"' in source
    assert "rerun_agent3_as_test_task(" in source
