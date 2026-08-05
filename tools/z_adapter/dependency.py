"""Resolve and verify the exact external Z-Century dependency.

This module is product Adapter code. It never contains or falls back to the generic Z
implementation. The exact Z source must be materialized outside the tracked product tree at
``.z/dependency-src`` or supplied through ``Z_CENTURY_SOURCE_DIR``.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict


class DependencyError(RuntimeError):
    """Raised when the exact external Z dependency cannot be proven."""


def product_root(root: Path | None = None) -> Path:
    return (root or Path(__file__).resolve().parents[2]).resolve()


def _read_object(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DependencyError(f"DEPENDENCY_JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise DependencyError(f"DEPENDENCY_OBJECT_REQUIRED:{path}")
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def dependency_manifest(root: Path | None = None) -> Dict[str, Any]:
    repository = product_root(root)
    manifest = _read_object(repository / ".z" / "dependency-manifest.json")
    material = {
        key: value for key, value in manifest.items() if key != "dependencyManifestHash"
    }
    if canonical_hash(material) != manifest.get("dependencyManifestHash"):
        raise DependencyError("DEPENDENCY_MANIFEST_HASH_MISMATCH")
    return manifest


def dependency_source_dir(root: Path | None = None) -> Path:
    repository = product_root(root)
    manifest = dependency_manifest(repository)
    dependency = dict(manifest.get("dependency") or {})
    configured = str(os.environ.get("Z_CENTURY_SOURCE_DIR") or "").strip()
    relative = configured or str(dependency.get("checkoutPath") or ".z/dependency-src")
    path = Path(relative)
    if not path.is_absolute():
        path = repository / path
    return path.resolve()


def _git_head(path: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = completed.stdout.strip()
    return value or None


def verify_dependency_identity(
    root: Path | None = None,
    *,
    source_dir: Path | None = None,
    require_git_commit: bool = True,
) -> Dict[str, Any]:
    repository = product_root(root)
    manifest = dependency_manifest(repository)
    expected = dict(manifest.get("dependency") or {})
    source = (source_dir or dependency_source_dir(repository)).resolve()
    findings: list[str] = []

    if not source.is_dir():
        findings.append(f"DEPENDENCY_SOURCE_MISSING:{source}")
        return {
            "schema": "z.ai_ecommerce.dependency_identity.v1",
            "verified": False,
            "sourceDirectory": str(source),
            "findings": findings,
        }

    release_path = source / "release" / "receipts" / "Z1.0.5.json"
    identity_path = source / "Z_BOOTSTRAP_SOURCE.json"
    registry_path = source / "contracts" / "registry" / "registry-manifest.json"
    for path in (release_path, identity_path, registry_path):
        if not path.is_file():
            findings.append(f"DEPENDENCY_REQUIRED_FILE_MISSING:{path.relative_to(source)}")

    release = _read_object(release_path) if release_path.is_file() else {}
    identity = _read_object(identity_path) if identity_path.is_file() else {}
    registry = _read_object(registry_path) if registry_path.is_file() else {}
    git_head = _git_head(source)

    comparisons = {
        "repository": (
            str(identity.get("repository") or ""),
            str(expected.get("repository") or ""),
        ),
        "sourceCommit": (
            str(release.get("sourceCommit") or ""),
            str(expected.get("sourceCommit") or ""),
        ),
        "releaseRef": (
            str(release.get("releaseRef") or ""),
            str(expected.get("releaseRef") or ""),
        ),
        "releaseHash": (
            str(release.get("releaseHash") or ""),
            str(expected.get("releaseHash") or ""),
        ),
        "archiveHash": (
            str(release.get("archiveHash") or ""),
            str(expected.get("archiveHash") or ""),
        ),
        "coreHash": (
            str(dict(release.get("hashes") or {}).get("coreHash") or ""),
            str(expected.get("coreHash") or ""),
        ),
        "registryRootHash": (
            str(registry.get("registryRootHash") or ""),
            str(expected.get("registryRootHash") or ""),
        ),
        "minimumAdapterVersion": (
            str(release.get("minimumAdapterVersion") or ""),
            str(expected.get("minimumAdapterVersion") or ""),
        ),
    }
    for key, (actual, wanted) in comparisons.items():
        if actual != wanted:
            findings.append(f"DEPENDENCY_IDENTITY_MISMATCH:{key}:{actual}:{wanted}")

    if require_git_commit:
        if not git_head:
            findings.append("DEPENDENCY_GIT_COMMIT_UNAVAILABLE")
        elif git_head != str(expected.get("sourceCommit") or ""):
            findings.append(
                f"DEPENDENCY_GIT_COMMIT_MISMATCH:{git_head}:{expected.get('sourceCommit')}"
            )

    material = {
        "sourceDirectory": str(source),
        "gitHead": git_head,
        "expected": expected,
        "comparisons": {
            key: {"actual": actual, "expected": wanted, "matches": actual == wanted}
            for key, (actual, wanted) in comparisons.items()
        },
        "findings": sorted(set(findings)),
    }
    return {
        "schema": "z.ai_ecommerce.dependency_identity.v1",
        **material,
        "verified": not findings,
        "identityHash": canonical_hash(material),
    }


def materialize_runtime_projection(root: Path | None = None) -> Path:
    repository = product_root(root)
    manifest = dependency_manifest(repository)
    adapter = dict(manifest.get("productAdapter") or {})
    source = repository / str(
        adapter.get("runtimeProjectionSource") or "config/v23_registry_runtime.json"
    )
    target = repository / str(
        adapter.get("runtimeProjectionMaterializedPath") or "config/z.runtime.json"
    )
    projection = _read_object(source)
    modules = dict(projection.get("modules") or {})
    material = {
        "schema": "z.ai_ecommerce.external_dependency_runtime_projection.v1",
        "version": "Z1.0.5",
        "mode": "EXTERNAL_Z_SOURCE_PRODUCT_RUNTIME_ADAPTER",
        "sourcePath": source.relative_to(repository).as_posix(),
        "sourceRegistryRootHash": projection.get("registryRootHash"),
        "modules": modules,
        "productRuntimeEnabled": False,
        "serverBindingEnabled": False,
    }
    payload = {**material, "projectionHash": canonical_hash(material)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def ensure_dependency(
    root: Path | None = None,
    *,
    materialize_projection: bool = True,
) -> Path:
    repository = product_root(root)
    source = dependency_source_dir(repository)
    receipt = verify_dependency_identity(repository, source_dir=source)
    if receipt.get("verified") is not True:
        raise DependencyError(
            "EXTERNAL_Z_DEPENDENCY_NOT_VERIFIED:"
            + ",".join(receipt.get("findings") or [])
        )
    if materialize_projection:
        materialize_runtime_projection(repository)
    return source


def dependency_package_path(package: str, root: Path | None = None) -> Path:
    source = ensure_dependency(root)
    path = source / Path(*package.split("."))
    if not path.is_dir():
        raise DependencyError(f"DEPENDENCY_PACKAGE_MISSING:{package}:{path}")
    return path


__all__ = [
    "DependencyError",
    "canonical_hash",
    "dependency_manifest",
    "dependency_package_path",
    "dependency_source_dir",
    "ensure_dependency",
    "materialize_runtime_projection",
    "product_root",
    "verify_dependency_identity",
]
