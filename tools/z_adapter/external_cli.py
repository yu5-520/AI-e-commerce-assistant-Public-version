"""Product-owned CLI adapters for the exact external Z-Century source.

The adapters execute the immutable dependency as a separate Python module process. No
generic Z implementation is imported or copied into the product repository.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Sequence

from .dependency import ensure_dependency, product_root


def _run(
    module: str,
    argv: Sequence[str] | None = None,
    *,
    prefix: Sequence[str] = (),
) -> int:
    root = product_root()
    source = ensure_dependency(root)
    module_path = source.joinpath(*module.split(".")).with_suffix(".py")
    if not module_path.is_file():
        raise RuntimeError(f"EXTERNAL_Z_ENTRY_MISSING:{module}:{module_path}")
    command = [
        sys.executable,
        "-m",
        module,
        "--root",
        str(root),
        *prefix,
        *(list(argv) if argv is not None else sys.argv[1:]),
    ]
    environment = dict(os.environ)
    environment["Z_CENTURY_SOURCE_DIR"] = str(source)
    environment["PYTHONPATH"] = str(source)
    completed = subprocess.run(command, cwd=source, env=environment, check=False)
    return int(completed.returncode)


def registry_main(argv: Sequence[str] | None = None) -> int:
    return _run("tools.registry_compiler.compile_registry", argv)


def self_update_main(argv: Sequence[str] | None = None) -> int:
    return _run("tools.self_update.cli", argv)


def requirement_main(argv: Sequence[str] | None = None) -> int:
    """Compatibility entry for the external unified Self-Update CLI."""
    return self_update_main(argv)


def level_gate_main(argv: Sequence[str] | None = None) -> int:
    arguments = [
        value
        for value in (list(argv) if argv is not None else sys.argv[1:])
        if value != "--fail-on-findings"
    ]
    return _run("tools.self_update.cli", arguments, prefix=("level-gate",))


if __name__ == "__main__":
    raise SystemExit(self_update_main())


__all__ = [
    "level_gate_main",
    "registry_main",
    "requirement_main",
    "self_update_main",
]
