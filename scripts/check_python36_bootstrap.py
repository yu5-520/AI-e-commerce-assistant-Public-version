#!/usr/bin/env python3
"""Statically verify that bootstrap code remains parseable by ECS Python 3.6."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY36 = (3, 6)


def parse_as_python36(source: str, label: str) -> None:
    try:
        ast.parse(source, filename=label, feature_version=PY36)
    except SyntaxError as exc:
        raise AssertionError(f"python36_syntax_error:{label}:{exc}") from exc


def embedded_python_blocks(shell_source: str) -> list[str]:
    return re.findall(r"<<'PY'\n(.*?)\nPY(?:\n|$)", shell_source, flags=re.DOTALL)


def main() -> None:
    verifier_path = ROOT / "scripts" / "release_verifier.py"
    guard_path = ROOT / "scripts" / "runtime_exclusivity_guard.sh"
    verifier = verifier_path.read_text(encoding="utf-8")
    guard = guard_path.read_text(encoding="utf-8")

    parse_as_python36(verifier, "scripts/release_verifier.py")
    blocks = embedded_python_blocks(guard)
    assert len(blocks) >= 2, "python36_embedded_cleanup_blocks_missing"
    for index, block in enumerate(blocks, 1):
        parse_as_python36(block, f"scripts/runtime_exclusivity_guard.sh#python-{index}")

    forbidden_fragments = (
        "from __future__ import annotations",
        "list[",
        "dict[",
        "set[",
        "tuple[",
        ".unlink(missing_ok=",
        ".is_relative_to(",
        "capture_output=",
    )
    combined = verifier + "\n" + "\n".join(blocks)
    present = [fragment for fragment in forbidden_fragments if fragment in combined]
    assert not present, f"python36_unsupported_bootstrap_fragments:{present}"

    print(
        json.dumps(
            {
                "schema": "release.python36-bootstrap-check.v1",
                "verified": True,
                "rootVerifier": "scripts/release_verifier.py",
                "embeddedCleanupBlockCount": len(blocks),
                "pythonFeatureVersion": "3.6",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
