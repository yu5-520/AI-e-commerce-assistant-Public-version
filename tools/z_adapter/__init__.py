"""AI e-commerce Adapter helpers for the external Z-Century dependency."""

from .dependency import (
    DependencyError,
    dependency_package_path,
    ensure_dependency,
    materialize_runtime_projection,
    verify_dependency_identity,
)

__all__ = [
    "DependencyError",
    "dependency_package_path",
    "ensure_dependency",
    "materialize_runtime_projection",
    "verify_dependency_identity",
]
