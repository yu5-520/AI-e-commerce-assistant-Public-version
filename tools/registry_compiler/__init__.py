"""Compatibility namespace for the external Z-Century Registry compiler.

Generic Registry compiler ownership moved to ``yu5-520/Z-Century``. The local package keeps
only product Adapter extensions and exposes the exact external package path when the locked
dependency has been materialized. No generic compiler implementation is stored here.
"""
from __future__ import annotations

from pathlib import Path

from tools.z_adapter.dependency import dependency_source_dir
from tools.z_adapter.external_cli import registry_main, requirement_main

_local_path = Path(__file__).resolve().parent
_external_path = dependency_source_dir() / "tools" / "registry_compiler"
__path__ = [str(_external_path), str(_local_path)] if _external_path.is_dir() else [str(_local_path)]


def main(argv=None) -> int:
    return registry_main(argv)


__all__ = ["main", "registry_main", "requirement_main"]
