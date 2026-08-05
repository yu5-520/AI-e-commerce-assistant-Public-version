"""Verify layered Z1.0.5 and AI e-commerce Registry Root equivalence.

The verifier is standard-library-only, read-only and intentionally does not authorize a
runtime switch. Unequal Roots are accepted only when their exact roles and composition
rules match the committed equivalence contract.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parents[2]
RECEIPT_PATH = ".z/receipts/Z1.0.5_AI_ROOT_EQUIVALENCE.json"
EXPECTED_Z_MODULES = {
    "bootstrap_installer",
    "level_gate",
    "lineage_compiler",
    "registry_compiler",
    "release_governance",
    "self_update_runtime",
}
EXPECTED_MAPPINGS = [
    {
        "mappingMode": "DIRECT_OWNER_EQUIVALENCE",
        "productModuleIds": ["registry_compiler"],
        "zModuleIds": ["registry_compiler"],
    },
    {
        "mappingMode": "LEGACY_COLLAPSED_OWNERSHIP_EQUIVALENCE",
        "productModuleIds": ["registry_compiler"],
        "zModuleIds": ["self_update_runtime", "lineage_compiler", "level_gate"],
    },
    {
        "mappingMode": "DIRECT_OWNER_EQUIVALENCE",
        "productModuleIds": ["release_governance"],
        "zModuleIds": ["release_governance"],
    },
    {
        "mappingMode": "LOCKED_INSTALL_ARTIFACT_ONLY",
        "productArtifacts": [".z/install-manifest.json"],
        "zModuleIds": ["bootstrap_installer"],
    },
]
ROOT_RE = re.compile(r"Registry Root Hash:\s*(sha256:[0-9a-f]{64})")


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


def read_object(relative: str) -> Dict[str, Any]:
    path = ROOT / relative
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{relative}")
    return value


def load_adapter_lock_verifier() -> Any:
    path = ROOT / ".z/tools/verify_lock.py"
    spec = importlib.util.spec_from_file_location("z_adapter_lock_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ADAPTER_LOCK_VERIFIER_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_result(
    contract: Mapping[str, Any],
    adapter_result: Mapping[str, Any],
    registry_document_count: int,
    findings: list[str],
) -> Dict[str, Any]:
    product_layers = dict(contract.get("productLayers") or {})
    z_core = dict(contract.get("zCore") or {})
    root_roles = {
        "zCoreRegistryRootHash": z_core.get("registryRootHash"),
        "productRegistryRootHash": dict(
            product_layers.get("authoritativeFullRegistry") or {}
        ).get("registryRootHash"),
        "activeRuntimeCompatibilityRootHash": dict(
            product_layers.get("activeRuntimeCompatibility") or {}
        ).get("registryRootHash"),
        "historicalGovernanceRootHash": dict(
            product_layers.get("historicalGovernanceDocument") or {}
        ).get("registryRootHash"),
    }
    material: Dict[str, Any] = {
        "schema": "z.ai_ecommerce.root_equivalence_receipt.v1",
        "version": "Z1.0.5",
        "status": (
            "VERIFIED_LAYERED_EQUIVALENCE_RUNTIME_SWITCH_NOT_AUTHORIZED"
            if not findings
            else "FAILED_LAYERED_EQUIVALENCE"
        ),
        "applicationId": contract.get("applicationId"),
        "equivalenceMode": contract.get("equivalenceMode"),
        "equivalenceHash": contract.get("equivalenceHash"),
        "rootRoles": root_roles,
        "zRelease": {
            key: z_core.get(key)
            for key in (
                "repository",
                "releaseRef",
                "sourceCommit",
                "releaseHash",
                "archiveHash",
            )
        },
        "registryDocumentCount": registry_document_count,
        "moduleMappingHash": canonical_hash(contract.get("moduleMappings") or []),
        "runtimeBoundaryHash": canonical_hash(contract.get("runtimeBoundary") or {}),
        "rootRolesHash": canonical_hash(
            {"zCore": z_core, "productLayers": product_layers}
        ),
        "adapterLockVerificationHash": adapter_result.get("verificationHash"),
        "warnings": [],
        "findings": sorted(set(findings)),
        "verified": not findings,
        "runtimeSwitchAuthorized": False,
        "serverBindingAuthorized": False,
        "embeddedSourceDeletionAuthorized": False,
        "productMainDeploymentAuthorized": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "ecsMutated": False,
    }
    return {**material, "verificationHash": canonical_hash(material)}


def verify(*, check_receipt: bool = False) -> Dict[str, Any]:
    findings: list[str] = []
    contract = read_object(".z/equivalence/root-equivalence.json")
    contract_material = {
        key: value for key, value in contract.items() if key != "equivalenceHash"
    }
    if canonical_hash(contract_material) != contract.get("equivalenceHash"):
        findings.append("EQUIVALENCE_HASH_MISMATCH")
    if contract.get("equivalenceMode") != "LAYERED_ROOT_COMPOSITION":
        findings.append("LAYERED_ROOT_COMPOSITION_REQUIRED")
    if contract.get("activationState") != (
        "EQUIVALENCE_VERIFIED_RUNTIME_SWITCH_NOT_AUTHORIZED"
    ):
        findings.append("EQUIVALENCE_ACTIVATION_STATE_INVALID")

    rules = dict(contract.get("compositionRules") or {})
    required_false = (
        "literalRootEqualityRequired",
        "crossLayerRootSubstitutionAllowed",
        "productRegistryRootRotated",
        "runtimeSwitchAuthorized",
        "serverBindingAuthorized",
        "embeddedSourceDeletionAuthorized",
        "productMainDeploymentAuthorized",
    )
    for key in required_false:
        if rules.get(key) is not False:
            findings.append(f"COMPOSITION_RULE_MUST_BE_FALSE:{key}")
    if rules.get("productExtensionsRemainProductOwned") is not True:
        findings.append("PRODUCT_EXTENSIONS_MUST_REMAIN_PRODUCT_OWNED")

    adapter_module = load_adapter_lock_verifier()
    adapter_result = dict(adapter_module.verify())
    if adapter_result.get("verified") is not True:
        findings.append("ADAPTER_LOCK_VERIFICATION_FAILED")
    expected_adapter_warning = "REGISTRY_ROOT_LAYERING_REQUIRES_STEP9_EQUIVALENCE"
    if expected_adapter_warning not in set(adapter_result.get("warnings") or []):
        findings.append("STEP8_LAYERING_WARNING_MISSING")
    adapter_receipt = read_object(".z/receipts/Z1.0.5_AI_ADAPTER_LOCK.json")
    receipt_lock_hash = dict(adapter_receipt.get("verification") or {}).get(
        "verificationHash"
    )
    if receipt_lock_hash != adapter_result.get("verificationHash"):
        findings.append("ADAPTER_LOCK_RECEIPT_VERIFICATION_HASH_MISMATCH")

    manifest = read_object("contracts/registry/registry-manifest.json")
    computed_entries: list[Dict[str, str]] = []
    for raw in manifest.get("registryFiles") or []:
        entry = dict(raw)
        relative = str(entry.get("path") or "")
        document = read_object(relative)
        actual_hash = canonical_hash(document)
        if actual_hash != entry.get("contentHash"):
            findings.append(f"PRODUCT_REGISTRY_DOCUMENT_HASH_MISMATCH:{relative}")
        computed_entries.append({"path": relative, "contentHash": actual_hash})
    computed_entries.sort(key=lambda item: item["path"])
    computed_product_root = canonical_hash(computed_entries)
    if computed_product_root != manifest.get("registryRootHash"):
        findings.append("PRODUCT_REGISTRY_ROOT_RECOMPUTE_MISMATCH")

    layers = dict(contract.get("productLayers") or {})
    product_layer = dict(layers.get("authoritativeFullRegistry") or {})
    runtime_layer = dict(layers.get("activeRuntimeCompatibility") or {})
    historical_layer = dict(layers.get("historicalGovernanceDocument") or {})
    if product_layer.get("registryRootHash") != manifest.get("registryRootHash"):
        findings.append("AUTHORITATIVE_PRODUCT_ROOT_ROLE_MISMATCH")

    runtime_projection = read_object("config/v23_registry_runtime.json")
    if runtime_layer.get("registryRootHash") != runtime_projection.get(
        "registryRootHash"
    ):
        findings.append("ACTIVE_RUNTIME_COMPATIBILITY_ROOT_MISMATCH")

    governance_text = (ROOT / "GOVERNANCE_VERSION.md").read_text(encoding="utf-8")
    match = ROOT_RE.search(governance_text)
    governance_root = match.group(1) if match else None
    if historical_layer.get("registryRootHash") != governance_root:
        findings.append("HISTORICAL_GOVERNANCE_ROOT_MISMATCH")

    z_core = dict(contract.get("zCore") or {})
    lock = read_object(".z/z.lock.json")
    release = dict(lock.get("release") or {})
    for key in ("repository", "releaseRef", "sourceCommit", "releaseHash", "archiveHash"):
        if z_core.get(key) != release.get(key):
            findings.append(f"Z_RELEASE_IDENTITY_MISMATCH:{key}")
    if z_core.get("registryRootHash") != (
        "sha256:21078bf8228c3ca3a4cb755015023001d962c9062d0a48e8a92e99f8fdb48360"
    ):
        findings.append("Z_CORE_REGISTRY_ROOT_MISMATCH")
    if z_core.get("registryManifestGitBlobSha") != (
        "4dd906b48fd658d381f0dcf2e933196f60eba7e2"
    ):
        findings.append("Z_CORE_MANIFEST_GIT_BLOB_MISMATCH")
    if set(z_core.get("moduleIds") or []) != EXPECTED_Z_MODULES:
        findings.append("Z_CORE_MODULE_SET_MISMATCH")
    if z_core.get("productRuntimeEnabled") is not False:
        findings.append("Z_CORE_PRODUCT_RUNTIME_MUST_BE_DISABLED")
    if z_core.get("serverBindingEnabled") is not False:
        findings.append("Z_CORE_SERVER_BINDING_MUST_BE_DISABLED")

    observed = dict(
        read_object(".z/adapter/registry-layout.json").get("observedLayeredRoots")
        or {}
    )
    if observed.get("sourceManifestRoot") != product_layer.get("registryRootHash"):
        findings.append("ADAPTER_SOURCE_MANIFEST_ROOT_MISMATCH")
    if observed.get("runtimeProjectionDeclaredRoot") != runtime_layer.get(
        "registryRootHash"
    ):
        findings.append("ADAPTER_RUNTIME_PROJECTION_ROOT_MISMATCH")
    if observed.get("governanceDocumentDeclaredRoot") != historical_layer.get(
        "registryRootHash"
    ):
        findings.append("ADAPTER_GOVERNANCE_DOCUMENT_ROOT_MISMATCH")

    roots = {
        z_core.get("registryRootHash"),
        product_layer.get("registryRootHash"),
        runtime_layer.get("registryRootHash"),
        historical_layer.get("registryRootHash"),
    }
    if None in roots or len(roots) != 4:
        findings.append("ROOT_ROLES_MUST_REMAIN_DISTINCT")

    mappings = contract.get("moduleMappings") or []
    if mappings != EXPECTED_MAPPINGS:
        findings.append("MODULE_MAPPING_CONTRACT_MISMATCH")
    product_modules_document = read_object("contracts/registry/modules.json")
    product_modules = {
        str(item.get("moduleId") or "")
        for item in product_modules_document.get("modules") or []
        if isinstance(item, dict)
    }
    for required in ("registry_compiler", "release_governance"):
        if required not in product_modules:
            findings.append(f"PRODUCT_MAPPING_MODULE_MISSING:{required}")
    if "bootstrap_installer" in product_modules:
        findings.append("BOOTSTRAP_INSTALLER_MUST_NOT_BE_PRODUCT_RUNTIME_MODULE")

    install = read_object(".z/install-manifest.json")
    if install.get("activationState") != "LOCKED_NOT_INSTALLED":
        findings.append("INSTALL_MUST_REMAIN_LOCKED_NOT_INSTALLED")
    if install.get("mode") != "REFERENCE_ONLY_NO_RUNTIME_SWITCH":
        findings.append("INSTALL_REFERENCE_ONLY_MODE_REQUIRED")

    runtime_boundary = dict(contract.get("runtimeBoundary") or {})
    for key in (
        "bootstrapInstallerActive",
        "zCoreProductRuntimeEnabled",
        "zCoreServerBindingEnabled",
    ):
        if runtime_boundary.get(key) is not False:
            findings.append(f"RUNTIME_BOUNDARY_MUST_BE_FALSE:{key}")
    if runtime_boundary.get("productRuntimeRemainsAuthoritative") is not True:
        findings.append("PRODUCT_RUNTIME_AUTHORITY_MUST_REMAIN_TRUE")

    product_registration = read_object(
        str(runtime_boundary.get("productRegistrationPath") or "")
    )
    adapter_registration = read_object(
        str(runtime_boundary.get("adapterRegistrationPath") or "")
    )
    product_active_modules = set(
        dict(product_registration.get("nonHttp") or {}).get(
            "registeredActiveModules"
        )
        or []
    )
    adapter_active_modules = set(adapter_registration.get("activeModules") or [])
    if product_active_modules != adapter_active_modules:
        findings.append("PRODUCT_ADAPTER_ACTIVE_MODULE_SET_MISMATCH")
    if "bootstrap_installer" in adapter_active_modules:
        findings.append("BOOTSTRAP_INSTALLER_MUST_NOT_BE_ACTIVE")

    result = build_result(contract, adapter_result, len(computed_entries), findings)
    if check_receipt:
        receipt_path = ROOT / RECEIPT_PATH
        if not receipt_path.is_file():
            findings.append("EQUIVALENCE_RECEIPT_MISSING")
        else:
            receipt = read_object(RECEIPT_PATH)
            if receipt != result:
                findings.append("EQUIVALENCE_RECEIPT_MISMATCH")
        result = build_result(contract, adapter_result, len(computed_entries), findings)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-receipt",
        action="store_true",
        help="Require the committed deterministic receipt to equal computed truth.",
    )
    args = parser.parse_args()
    result = verify(check_receipt=args.check_receipt)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
