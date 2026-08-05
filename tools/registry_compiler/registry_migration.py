"""Hash-bound Registry Root migration contracts for V23.2.3."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from .compile_registry import sha256_value

REGISTRY_MIGRATION_VERSION = "23.2.3"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GLOB_TOKENS = ("*", "?", "[")


class RegistryMigrationError(RuntimeError):
    """Raised when a Registry migration plan is malformed or stale."""


def _strings(values: Iterable[Any]) -> list[str]:
    return sorted(
        {
            str(value).strip().replace("\\", "/")
            for value in values
            if str(value).strip()
        }
    )


def _valid_hash(value: str) -> bool:
    return bool(_HASH_RE.fullmatch(str(value or "")))


def _valid_migration_path(path: str) -> bool:
    """Accept only exact Registry documents or Registry-compiler governance files."""

    normalized = str(path or "").strip().replace("\\", "/")
    parts = Path(normalized).parts
    registry_document = normalized.startswith("contracts/registry/")
    registry_governance = (
        normalized.startswith("tools/registry_compiler/")
        and normalized.endswith(".py")
    )
    return bool(
        (registry_document or registry_governance)
        and not normalized.endswith("/")
        and ".." not in parts
        and not any(token in normalized for token in _GLOB_TOKENS)
    )


def migration_plan_material(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the canonical material covered by ``migrationPlanHash``."""

    return {
        "baseRegistryRootHash": str(plan.get("baseRegistryRootHash") or "").strip(),
        "allowedRegistryPaths": _strings(plan.get("allowedRegistryPaths") or []),
        "targetModules": _strings(plan.get("targetModules") or []),
        "migrationReason": str(plan.get("migrationReason") or "").strip(),
        "prohibitedPaths": _strings(plan.get("prohibitedPaths") or []),
    }


def calculate_migration_plan_hash(plan: Mapping[str, Any]) -> str:
    return sha256_value(migration_plan_material(plan))


def build_registry_migration_plan(
    *,
    base_registry_root_hash: str,
    allowed_registry_paths: Iterable[str],
    target_modules: Iterable[str],
    migration_reason: str,
    prohibited_paths: Iterable[str] = (),
) -> Dict[str, Any]:
    material = migration_plan_material(
        {
            "baseRegistryRootHash": base_registry_root_hash,
            "allowedRegistryPaths": list(allowed_registry_paths),
            "targetModules": list(target_modules),
            "migrationReason": migration_reason,
            "prohibitedPaths": list(prohibited_paths),
        }
    )
    return {**material, "migrationPlanHash": sha256_value(material)}


def validate_registry_migration_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    errors: list[str] = []
    if not isinstance(plan, Mapping):
        return {
            "schema": "self_update.registry_migration_plan.validation.v1",
            "version": REGISTRY_MIGRATION_VERSION,
            "valid": False,
            "errors": ["registry_migration_plan_object_required"],
            "plan": {},
            "expectedMigrationPlanHash": "",
        }

    material = migration_plan_material(plan)
    base_root = material["baseRegistryRootHash"]
    allowed_paths = material["allowedRegistryPaths"]
    target_modules = material["targetModules"]
    reason = material["migrationReason"]
    prohibited = material["prohibitedPaths"]
    supplied_hash = str(plan.get("migrationPlanHash") or "").strip()
    expected_hash = sha256_value(material)

    if not _valid_hash(base_root):
        errors.append("registry_migration_base_root_invalid")
    if not allowed_paths:
        errors.append("registry_migration_allowed_paths_required")
    invalid_paths = [path for path in allowed_paths if not _valid_migration_path(path)]
    if invalid_paths:
        errors.extend(
            f"registry_migration_path_invalid:{path}" for path in invalid_paths
        )
    if not target_modules:
        errors.append("registry_migration_target_modules_required")
    if not reason:
        errors.append("registry_migration_reason_required")
    if not _valid_hash(supplied_hash):
        errors.append("registry_migration_plan_hash_invalid")
    elif supplied_hash != expected_hash:
        errors.append("registry_migration_plan_hash_mismatch")
    overlapping = sorted(set(allowed_paths) & set(prohibited))
    if overlapping:
        errors.extend(
            f"registry_migration_path_prohibited:{path}" for path in overlapping
        )

    normalized = {**material, "migrationPlanHash": supplied_hash}
    return {
        "schema": "self_update.registry_migration_plan.validation.v1",
        "version": REGISTRY_MIGRATION_VERSION,
        "valid": not errors,
        "errors": errors,
        "plan": normalized,
        "expectedMigrationPlanHash": expected_hash,
    }


def require_registry_migration_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    validation = validate_registry_migration_plan(plan)
    if validation["valid"] is not True:
        raise RegistryMigrationError(
            "registry_migration_plan_invalid:" + ",".join(validation["errors"])
        )
    return dict(validation["plan"])


def file_content_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def registry_file_hashes(
    repository: Path, paths: Iterable[str]
) -> Dict[str, str | None]:
    root = repository.resolve()
    result: Dict[str, str | None] = {}
    for relative in _strings(paths):
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise RegistryMigrationError(
                f"registry_migration_path_outside_repository:{relative}"
            )
        result[relative] = file_content_hash(candidate)
    return result


__all__ = [
    "REGISTRY_MIGRATION_VERSION",
    "RegistryMigrationError",
    "build_registry_migration_plan",
    "calculate_migration_plan_hash",
    "file_content_hash",
    "migration_plan_material",
    "registry_file_hashes",
    "require_registry_migration_plan",
    "validate_registry_migration_plan",
]
