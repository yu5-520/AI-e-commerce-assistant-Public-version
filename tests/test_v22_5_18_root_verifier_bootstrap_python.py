from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_release_verifier.sh"


def _source() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_root_verifier_installer_selects_exact_bootstrap_python() -> None:
    source = _source()
    for marker in (
        'EXPECTED_PYTHON_VERSION="${AI_RELEASE_EXPECTED_PYTHON_VERSION:-3.11.9}"',
        "select_bootstrap_python()",
        '"${AI_BOOTSTRAP_PYTHON:-}"',
        '"${AI_RELEASE_PYTHON:-}"',
        '"/opt/ai-runtime/python/current/bin/python3.11"',
        'BOOTSTRAP_PYTHON="$(select_bootstrap_python)"',
        'export AI_BOOTSTRAP_PYTHON="$BOOTSTRAP_PYTHON"',
    ):
        assert marker in source


def test_root_verifier_checks_always_use_selected_bootstrap() -> None:
    source = _source()
    assert source.count('"$BOOTSTRAP_PYTHON" "$TARGET" --help') == 2
    assert source.count('"$BOOTSTRAP_PYTHON" "$TEMP_TARGET" --help') == 1
    for forbidden in (
        'python3 "$TARGET" --help',
        'python3 "$TEMP_TARGET" --help',
        'python3.11 "$TARGET" --help',
        'python3.11 "$TEMP_TARGET" --help',
    ):
        assert forbidden not in source


def test_bootstrap_fix_does_not_weaken_root_trust_rotation() -> None:
    source = _source()
    for marker in (
        "AI_RELEASE_VERIFIER_ROTATE",
        "AI_RELEASE_VERIFIER_EXPECTED_OLD_SHA256",
        "ordinary release deployment cannot rotate root trust",
        "Expected old verifier SHA256 does not match",
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
