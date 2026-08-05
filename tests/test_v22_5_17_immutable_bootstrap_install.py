from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "config" / "deployment" / "install_deploy_bootstrap.sh"
TRANSPORT_WRAPPER = ROOT / "scripts" / "deploy_github_artifact.sh"
TRANSPORT_CORE = ROOT / "src" / "deployment" / "deploy_github_artifact_core_v22516.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_transport_wrapper_allows_an_explicit_immutable_core_path() -> None:
    source = _read(TRANSPORT_WRAPPER)
    assert "AI_DEPLOY_GITHUB_ARTIFACT_CORE" in source
    assert "src/deployment/deploy_github_artifact_core_v22516.sh" in source
    assert '[ -f "$CORE" ]' in source
    assert '/bin/bash "$CORE" "$@"' in source


def test_installer_persists_wrapper_core_and_launcher_together() -> None:
    source = _read(INSTALLER)
    for marker in (
        'TRANSPORT_WRAPPER_PATH="$LIBEXEC_DIR/deploy-github-artifact"',
        'TRANSPORT_CORE_PATH="$LIBEXEC_DIR/deploy-github-artifact-core-v22516.sh"',
        'deploy_github_artifact_core_v22516.sh missing from sealed release',
        'install -m 700 "$SOURCE_ROOT/scripts/deploy_github_artifact.sh"',
        'install -m 700 "$SOURCE_ROOT/src/deployment/deploy_github_artifact_core_v22516.sh"',
        'export AI_DEPLOY_GITHUB_ARTIFACT_CORE=',
        'exec "$TRANSPORT_WRAPPER_PATH"',
        'mv -f "$TEMP_WRAPPER" "$TRANSPORT_WRAPPER_PATH"',
        'mv -f "$TEMP_CORE" "$TRANSPORT_CORE_PATH"',
        'mv -f "$TEMP_BOOTSTRAP" "$BOOTSTRAP_PATH"',
    ):
        assert marker in source

    assert 'install -m 700 "$SOURCE_ROOT/scripts/deploy_github_artifact.sh" "$BOOTSTRAP_PATH"' not in source
    assert '"$ROOT/current/scripts/deploy_github_artifact.sh"' not in source


def test_installed_bootstrap_remains_independent_of_current_release() -> None:
    source = _read(INSTALLER)
    bootstrap_block = source.split('cat > "$TEMP_BOOTSTRAP" <<EOF', 1)[1].split("EOF", 1)[0]
    assert "/usr/local/libexec/ai-ecommerce" not in bootstrap_block
    assert "$TRANSPORT_CORE_PATH" in bootstrap_block
    assert "$TRANSPORT_WRAPPER_PATH" in bootstrap_block
    assert "$ROOT/current" not in bootstrap_block


def test_changed_shell_entries_are_syntax_valid() -> None:
    for path in (INSTALLER, TRANSPORT_WRAPPER, TRANSPORT_CORE):
        completed = subprocess.run(
            ["bash", "-n", str(path)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, f"{path}: {completed.stderr}"
