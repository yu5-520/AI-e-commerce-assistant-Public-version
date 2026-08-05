"""Verify the AI e-commerce external Z dependency mode."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.z_adapter.dependency import (  # noqa: E402
    canonical_hash,
    dependency_manifest,
    verify_dependency_identity,
)

RECEIPT_PATH = ".z/receipts/Z1.0.5_AI_DEPENDENCY_MODE.json"


def read_object(relative: str) -> Dict[str, Any]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{relative}")
    return value


def verify(*, source_dir: Path | None = None, check_receipt: bool = False) -> Dict[str, Any]:
    findings: list[str] = []
    manifest = dependency_manifest(ROOT)
    dependency = dict(manifest.get("dependency") or {})
    adapter = dict(manifest.get("productAdapter") or {})
    embedded = dict(manifest.get("embeddedGenericSource") or {})
    boundary = dict(manifest.get("runtimeBoundary") or {})

    for relative in embedded.get("forbiddenImplementationPaths") or []:
        if (ROOT / str(relative)).exists():
            findings.append(f"EMBEDDED_GENERIC_IMPLEMENTATION_PRESENT:{relative}")

    for relative in embedded.get("removedProtocolRoots") or []:
        if (ROOT / str(relative)).exists():
            findings.append(f"EMBEDDED_GENERIC_PROTOCOL_PRESENT:{relative}")

    for relative in embedded.get("allowedShimPaths") or []:
        path = ROOT / str(relative)
        if not path.is_file():
            findings.append(f"DEPENDENCY_SHIM_MISSING:{relative}")
            continue
        text = path.read_text(encoding="utf-8")
        if "tools.z_adapter" not in text:
            findings.append(f"DEPENDENCY_SHIM_NOT_ADAPTER_BOUND:{relative}")

    retained = [
        str(value) for value in manifest.get("retainedEvidence") or [] if str(value)
    ]
    retained.extend(
        str(value)
        for value in (
            adapter.get("adapterManifest"),
            adapter.get("lockPath"),
            adapter.get("installManifest"),
            adapter.get("rootAuthority"),
            adapter.get("productCapabilities"),
        )
        if str(value)
    )
    for relative in sorted(set(retained)):
        if not (ROOT / relative).is_file():
            findings.append(f"RETAINED_PRODUCT_OR_EVIDENCE_FILE_MISSING:{relative}")

    authority = read_object(str(adapter.get("rootAuthority") or ".z/root-authority.json"))
    install = read_object(str(adapter.get("installManifest") or ".z/install-manifest.json"))
    lock = read_object(str(adapter.get("lockPath") or ".z/z.lock.json"))
    lock_release = dict(lock.get("release") or {})

    if authority.get("state") != "EXTERNAL_Z_AUTHORITY_ACTIVE":
        findings.append("ROOT_AUTHORITY_NOT_EXTERNAL_ACTIVE")
    if authority.get("authorityRepository") != dependency.get("repository"):
        findings.append("ROOT_AUTHORITY_REPOSITORY_MISMATCH")
    if authority.get("releaseCommit") != dependency.get("sourceCommit"):
        findings.append("ROOT_AUTHORITY_COMMIT_MISMATCH")
    if authority.get("releaseHash") != dependency.get("releaseHash"):
        findings.append("ROOT_AUTHORITY_RELEASE_HASH_MISMATCH")
    if authority.get("coreHash") != dependency.get("coreHash"):
        findings.append("ROOT_AUTHORITY_CORE_HASH_MISMATCH")
    if authority.get("registryRootHash") != dependency.get("registryRootHash"):
        findings.append("ROOT_AUTHORITY_REGISTRY_ROOT_MISMATCH")
    if authority.get("productRuntimeAuthority") != adapter.get("productRuntimeAuthority"):
        findings.append("PRODUCT_RUNTIME_AUTHORITY_MISMATCH")

    if install.get("activationState") != "LOCKED_NOT_INSTALLED":
        findings.append("INSTALL_ACTIVATION_STATE_CHANGED")
    if install.get("mode") != "REFERENCE_ONLY_NO_RUNTIME_SWITCH":
        findings.append("INSTALL_RUNTIME_MODE_CHANGED")
    if lock_release.get("repository") != dependency.get("repository"):
        findings.append("LOCK_REPOSITORY_MISMATCH")
    if lock_release.get("sourceCommit") != dependency.get("sourceCommit"):
        findings.append("LOCK_SOURCE_COMMIT_MISMATCH")
    if lock_release.get("releaseHash") != dependency.get("releaseHash"):
        findings.append("LOCK_RELEASE_HASH_MISMATCH")
    if lock_release.get("coreHash") != dependency.get("coreHash"):
        findings.append("LOCK_CORE_HASH_MISMATCH")

    for key in (
        "productRuntimeChanged",
        "serverBindingChanged",
        "databaseMutationAuthorized",
        "providerCallsAuthorized",
        "ecsMutationAuthorized",
        "secondZRuntimeAuthorized",
    ):
        if boundary.get(key) is not False:
            findings.append(f"RUNTIME_BOUNDARY_INVALID:{key}")

    dependency_identity = None
    if source_dir is not None:
        dependency_identity = verify_dependency_identity(
            ROOT,
            source_dir=source_dir,
            require_git_commit=True,
        )
        if dependency_identity.get("verified") is not True:
            findings.extend(dependency_identity.get("findings") or [])

    material: Dict[str, Any] = {
        "schema": "z.ai_ecommerce.dependency_mode_receipt.v1",
        "version": "Z1.0.5",
        "status": "DEPENDENCY_MODE_VERIFIED" if not findings else "DEPENDENCY_MODE_REJECTED",
        "dependencyManifestHash": manifest.get("dependencyManifestHash"),
        "sourceRepository": dependency.get("repository"),
        "sourceCommit": dependency.get("sourceCommit"),
        "releaseRef": dependency.get("releaseRef"),
        "releaseHash": dependency.get("releaseHash"),
        "coreHash": dependency.get("coreHash"),
        "registryRootHash": dependency.get("registryRootHash"),
        "adapterHash": adapter.get("adapterHash"),
        "rollbackCommit": manifest.get("rollbackCommit"),
        "embeddedGenericSourceRemoved": not any(
            value.startswith("EMBEDDED_GENERIC_") for value in findings
        ),
        "externalDependencyVerified": (
            dependency_identity.get("verified") if dependency_identity is not None else None
        ),
        "productRuntimeChanged": False,
        "serverBindingChanged": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "ecsMutated": False,
        "secondZRuntimeStarted": False,
        "findings": sorted(set(str(value) for value in findings)),
        "verified": not findings,
    }
    result = {**material, "verificationHash": canonical_hash(material)}

    if check_receipt:
        receipt = read_object(RECEIPT_PATH)
        if receipt != result:
            material["status"] = "DEPENDENCY_MODE_REJECTED"
            material["verified"] = False
            material["findings"] = sorted(
                set([*material["findings"], "COMMITTED_RECEIPT_MISMATCH"])
            )
            result = {**material, "verificationHash": canonical_hash(material)}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir")
    parser.add_argument("--check-receipt", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    source = Path(args.source_dir).resolve() if args.source_dir else None
    result = verify(source_dir=source, check_receipt=args.check_receipt)
    if args.output:
        path = ROOT / args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("verified") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
