from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.check_release_contract import (
    _resolve_static_path_expression,
    static_path_assignments,
)
from scripts.generate_release_manifest import preserve_runtime_python_entry


def _expression(source: str) -> ast.AST:
    return ast.parse(source, mode="eval").body


@pytest.mark.parametrize(
    "source",
    (
        'Path("release") / "attestation"',
        'Path("release/attestation")',
        'PurePosixPath("release") / "attestation"',
    ),
)
def test_static_path_resolver_accepts_equivalent_path_syntax(source: str) -> None:
    resolved = _resolve_static_path_expression(_expression(source), {})
    assert resolved.as_posix() == "release/attestation"


def test_static_path_resolver_accepts_named_static_parts() -> None:
    values = {"RELEASE_DIR": _resolve_static_path_expression(_expression('Path("release")'), {})}
    resolved = _resolve_static_path_expression(
        _expression('RELEASE_DIR / "attestation"'),
        values,
    )
    assert resolved.as_posix() == "release/attestation"


def test_static_path_resolver_rejects_dynamic_calls() -> None:
    with pytest.raises(ValueError, match="unsupported_static_path_expression"):
        _resolve_static_path_expression(_expression("build_release_path()"), {})


def test_manifest_evidence_prefix_is_checked_semantically() -> None:
    assignments = static_path_assignments("scripts/generate_release_manifest.py")
    assert assignments["EVIDENCE_PREFIX"] == "release/attestation"


def test_runtime_python_entry_preserves_virtualenv_symlink(tmp_path: Path) -> None:
    base_python = tmp_path / "base-python"
    base_python.write_text("python-binary-placeholder", encoding="utf-8")
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base_python)

    entry = preserve_runtime_python_entry(str(venv_python))

    assert entry == venv_python.absolute()
    assert entry != venv_python.resolve()
    assert entry.is_symlink()
