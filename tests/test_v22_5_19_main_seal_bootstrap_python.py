from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_release_verifier.sh"


def _source() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_main_seal_sudo_can_resolve_setup_python_runtime() -> None:
    source = _source()
    for marker in (
        'HOSTED_TOOLCACHE_ROOT="${RUNNER_TOOL_CACHE:-/opt/hostedtoolcache}"',
        'HOSTED_PYTHON_DIR="$HOSTED_TOOLCACHE_ROOT/Python/$EXPECTED_PYTHON_VERSION/x64/bin"',
        '"$HOSTED_PYTHON_DIR/python"',
        '"$HOSTED_PYTHON_DIR/python3.11"',
    ):
        assert marker in source


def test_production_bootstrap_candidates_still_precede_ci_fallback() -> None:
    source = _source()
    assert source.index('"${AI_BOOTSTRAP_PYTHON:-}"') < source.index(
        '"$HOSTED_PYTHON_DIR/python"'
    )
    assert source.index('"/opt/ai-runtime/python/current/bin/python3.11"') < source.index(
        '"$HOSTED_PYTHON_DIR/python"'
    )


def test_exact_patch_version_and_root_trust_remain_fail_closed() -> None:
    source = _source()
    assert '[ "$version" = "$EXPECTED_PYTHON_VERSION" ] || continue' in source
    for marker in (
        "ordinary release deployment cannot rotate root trust",
        "AI_RELEASE_VERIFIER_EXPECTED_OLD_SHA256",
        "Installed verifier differs from the root-pinned SHA256",
        'mv -f "$TEMP_TARGET" "$TARGET"',
        'mv -f "$TEMP_HASH" "$HASH_FILE"',
    ):
        assert marker in source


def test_installer_shell_syntax_is_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(INSTALLER)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
