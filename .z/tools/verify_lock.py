"""Verify the AI e-commerce Z Adapter and exact Z Release lock without runtime mutation."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode("utf-8")
    return hashlib.sha1(header + payload).hexdigest()


def read_object(relative: str) -> Dict[str, Any]:
    path = ROOT / relative
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{relative}")
    return value


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in str(value).strip().split("."))


def source_descriptors(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, dict):
        if value.get("path") and value.get("gitBlobSha"):
            yield value
        for child in value.values():
            yield from source_descriptors(child)
    elif isinstance(value, list):
        for child in value:
            yield from source_descriptors(child)


def verify() -> Dict[str, Any]:
    findings: list[str] = []
    warnings: list[str] = []
    adapter_manifest = read_object(".z/adapter/adapter-manifest.json")
    install_manifest = read_object(".z/install-manifest.json")
    lock = read_object(".z/z.lock.json")

    adapter_documents: list[Dict[str, Any]] = []
    for raw in adapter_manifest.get("documents") or []:
        entry = dict(raw)
        path = str(entry.get("path") or "")
        document = read_object(path)
        actual_hash = canonical_hash(document)
        if actual_hash != entry.get("sha256"):
            findings.append(f"ADAPTER_DOCUMENT_HASH_MISMATCH:{path}")
        adapter_documents.append(document)

    adapter_material = {
        key: value for key, value in adapter_manifest.items() if key != "adapterHash"
    }
    actual_adapter_hash = canonical_hash(adapter_material)
    if actual_adapter_hash != adapter_manifest.get("adapterHash"):
        findings.append("ADAPTER_HASH_MISMATCH")

    for document in adapter_documents:
        for descriptor in source_descriptors(document):
            relative = str(descriptor.get("path") or "")
            path = ROOT / relative
            if not path.is_file():
                findings.append(f"ADAPTER_SOURCE_MISSING:{relative}")
                continue
            if git_blob_sha(path) != descriptor.get("gitBlobSha"):
                findings.append(f"ADAPTER_SOURCE_GIT_BLOB_MISMATCH:{relative}")

    registry_layout = read_object(".z/adapter/registry-layout.json")
    registry_manifest = read_object("contracts/registry/registry-manifest.json")
    expected_registry_root = dict(registry_layout.get("manifest") or {}).get(
        "registryRootHash"
    )
    if registry_manifest.get("registryRootHash") != expected_registry_root:
        findings.append("PRODUCT_REGISTRY_ROOT_MISMATCH")
    observed_roots = set(
        str(value)
        for value in dict(registry_layout.get("observedLayeredRoots") or {}).values()
    )
    if len(observed_roots) > 1:
        warnings.append("REGISTRY_ROOT_LAYERING_REQUIRES_STEP9_EQUIVALENCE")
        if install_manifest.get("activationState") != "LOCKED_NOT_INSTALLED":
            findings.append("ROOT_DRIFT_REQUIRES_LOCKED_NOT_INSTALLED")

    actual_installed_hash = canonical_hash(install_manifest.get("installMaterial") or {})
    if actual_installed_hash != install_manifest.get("installedHash"):
        findings.append("INSTALLED_HASH_MISMATCH")

    lock_material = {key: value for key, value in lock.items() if key != "lockHash"}
    actual_lock_hash = canonical_hash(lock_material)
    if actual_lock_hash != lock.get("lockHash"):
        findings.append("LOCK_HASH_MISMATCH")

    if dict(lock.get("adapter") or {}).get("adapterHash") != actual_adapter_hash:
        findings.append("LOCK_ADAPTER_HASH_MISMATCH")
    if dict(lock.get("installation") or {}).get("installedHash") != actual_installed_hash:
        findings.append("LOCK_INSTALLED_HASH_MISMATCH")
    if install_manifest.get("release") != lock.get("release"):
        findings.append("LOCK_RELEASE_MATERIAL_MISMATCH")
    if dict(adapter_manifest.get("requiredRelease") or {}).get("releaseHash") != dict(
        lock.get("release") or {}
    ).get("releaseHash"):
        findings.append("ADAPTER_RELEASE_HASH_MISMATCH")
    if dict(adapter_manifest.get("requiredRelease") or {}).get("sourceCommit") != dict(
        lock.get("release") or {}
    ).get("sourceCommit"):
        findings.append("ADAPTER_RELEASE_COMMIT_MISMATCH")

    release = dict(lock.get("release") or {})
    for key in (
        "releaseHash",
        "archiveHash",
        "artifactTransportHash",
        "coreHash",
        "protocolHash",
        "schemaHash",
        "compilerHash",
        "gateHash",
        "registryHash",
        "runtimeHash",
        "identityHash",
    ):
        if not HASH_RE.fullmatch(str(release.get(key) or "")):
            findings.append(f"LOCK_RELEASE_HASH_INVALID:{key}")
    if not COMMIT_RE.fullmatch(str(release.get("sourceCommit") or "")):
        findings.append("LOCK_RELEASE_COMMIT_INVALID")
    release_ref = str(release.get("releaseRef") or "")
    if not release_ref.startswith("release/"):
        findings.append("LOCK_RELEASE_REF_INVALID")
    if any(token in release_ref.lower() for token in ("latest", "main", "master")):
        findings.append("MUTABLE_RELEASE_REF_FORBIDDEN")
    if lock.get("mutableReferencesAllowed") is not False:
        findings.append("MUTABLE_REFERENCES_MUST_BE_DISABLED")
    if lock.get("latestAliasAllowed") is not False:
        findings.append("LATEST_ALIAS_MUST_BE_DISABLED")
    if lock.get("resolutionMode") != "EXACT_COMMIT_AND_HASH":
        findings.append("EXACT_COMMIT_AND_HASH_REQUIRED")

    adapter_version = str(dict(lock.get("adapter") or {}).get("version") or "0")
    minimum_adapter = str(release.get("minimumAdapterVersion") or "0")
    if version_tuple(adapter_version) < version_tuple(minimum_adapter):
        findings.append("ADAPTER_VERSION_BELOW_RELEASE_MINIMUM")

    if install_manifest.get("mode") != "REFERENCE_ONLY_NO_RUNTIME_SWITCH":
        findings.append("STEP8_REFERENCE_ONLY_MODE_REQUIRED")
    prohibited = set(str(value) for value in install_manifest.get("prohibitedActions") or [])
    for required in (
        "DELETE_EMBEDDED_Z_RUNTIME",
        "MUTATE_ECS",
        "MUTATE_PRODUCT_DATABASE",
        "CALL_PROVIDER",
        "ACTIVATE_SERVER_BINDING",
    ):
        if required not in prohibited:
            findings.append(f"STEP8_PROHIBITION_MISSING:{required}")

    material = {
        "adapterHash": actual_adapter_hash,
        "installedHash": actual_installed_hash,
        "lockHash": actual_lock_hash,
        "releaseHash": release.get("releaseHash"),
        "releaseSourceCommit": release.get("sourceCommit"),
        "warnings": sorted(set(warnings)),
        "findings": sorted(set(findings)),
    }
    return {
        "schema": "z.ai_ecommerce.adapter_lock_verification.v1",
        **material,
        "verified": not findings,
        "verificationHash": canonical_hash(material),
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "ecsMutated": False,
    }


def main() -> int:
    result = verify()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
