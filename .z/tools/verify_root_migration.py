"""Verify the AI e-commerce L5 cross-repository Root authority migration."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
TX_PATH = "contracts/level-transactions/LTX-Z1.0.5-CROSS-REPOSITORY-ROOT-MIGRATION.json"
AUTHORITY_PATH = ".z/root-authority.json"
RECEIPT_PATH = ".z/receipts/Z1.0.5_L5_ROOT_MIGRATION.json"


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def read_object(relative: str) -> Dict[str, Any]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{relative}")
    return value


def load_verifier(relative: str, name: str) -> Any:
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"VERIFIER_LOAD_FAILED:{relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(*, check_receipt: bool = False) -> Dict[str, Any]:
    findings: list[str] = []
    tx = read_object(TX_PATH)
    authority = read_object(AUTHORITY_PATH)
    install = read_object(".z/install-manifest.json")
    lock = read_object(".z/z.lock.json")
    equivalence = read_object(".z/equivalence/root-equivalence.json")

    tx_material = {
        key: value for key, value in tx.items() if key != "migrationTransactionHash"
    }
    if canonical_hash(tx_material) != tx.get("migrationTransactionHash"):
        findings.append("MIGRATION_TRANSACTION_HASH_MISMATCH")
    if tx.get("declaredLevel") != "L5":
        findings.append("L5_REQUIRED")
    if tx.get("sourceCoreHash") != tx.get("destinationCoreHash"):
        findings.append("CORE_HASH_NOT_EQUIVALENT")
    if canonical_hash(tx.get("oldScope") or {}) != tx.get("oldScopeHash"):
        findings.append("OLD_SCOPE_HASH_MISMATCH")
    if canonical_hash(tx.get("newScope") or {}) != tx.get("newScopeHash"):
        findings.append("NEW_SCOPE_HASH_MISMATCH")

    authority_material = {
        key: value for key, value in authority.items() if key != "authorityHash"
    }
    if canonical_hash(authority_material) != authority.get("authorityHash"):
        findings.append("AUTHORITY_HASH_MISMATCH")
    if authority.get("state") != "EXTERNAL_Z_AUTHORITY_ACTIVE":
        findings.append("EXTERNAL_Z_AUTHORITY_NOT_ACTIVE")
    if authority.get("authorityRepository") != tx.get("destinationRepository"):
        findings.append("AUTHORITY_REPOSITORY_MISMATCH")
    if authority.get("authorityIntegrationCommit") != tx.get(
        "destinationAuthorityIntegrationCommit"
    ):
        findings.append("AUTHORITY_INTEGRATION_COMMIT_MISMATCH")
    if authority.get("releaseCommit") != tx.get("destinationCommit"):
        findings.append("RELEASE_COMMIT_MISMATCH")
    if authority.get("coreHash") != tx.get("destinationCoreHash"):
        findings.append("AUTHORITY_CORE_HASH_MISMATCH")
    if authority.get("releaseHash") != tx.get("releaseHash"):
        findings.append("AUTHORITY_RELEASE_HASH_MISMATCH")
    if authority.get("adapterHash") != tx.get("adapterHash"):
        findings.append("AUTHORITY_ADAPTER_HASH_MISMATCH")

    release = dict(lock.get("release") or {})
    if release.get("repository") != authority.get("authorityRepository"):
        findings.append("LOCK_AUTHORITY_REPOSITORY_MISMATCH")
    if release.get("sourceCommit") != authority.get("releaseCommit"):
        findings.append("LOCK_RELEASE_COMMIT_MISMATCH")
    if release.get("releaseHash") != authority.get("releaseHash"):
        findings.append("LOCK_RELEASE_HASH_MISMATCH")
    if release.get("coreHash") != authority.get("coreHash"):
        findings.append("LOCK_CORE_HASH_MISMATCH")

    z_core = dict(equivalence.get("zCore") or {})
    if z_core.get("registryRootHash") != authority.get("registryRootHash"):
        findings.append("EQUIVALENCE_REGISTRY_ROOT_MISMATCH")
    if equivalence.get("equivalenceHash") != authority.get("equivalenceHash"):
        findings.append("EQUIVALENCE_HASH_MISMATCH")

    lock_result = dict(load_verifier(".z/tools/verify_lock.py", "z_lock_verify").verify())
    if lock_result.get("verified") is not True:
        findings.append("ADAPTER_LOCK_VERIFICATION_FAILED")
    eq_result = dict(
        load_verifier(".z/tools/verify_equivalence.py", "z_equivalence_verify").verify(
            check_receipt=True
        )
    )
    if eq_result.get("verified") is not True:
        findings.append("ROOT_EQUIVALENCE_VERIFICATION_FAILED")

    if install.get("rootAuthorityState") != authority.get("state"):
        findings.append("INSTALL_ROOT_AUTHORITY_STATE_MISMATCH")
    if install.get("rootAuthorityManifestPath") != AUTHORITY_PATH:
        findings.append("INSTALL_ROOT_AUTHORITY_PATH_MISMATCH")
    if install.get("rootAuthorityReceiptPath") != RECEIPT_PATH:
        findings.append("INSTALL_ROOT_RECEIPT_PATH_MISMATCH")
    if install.get("activationState") != "LOCKED_NOT_INSTALLED":
        findings.append("INSTALL_MUST_REMAIN_LOCKED_NOT_INSTALLED")
    if install.get("mode") != "REFERENCE_ONLY_NO_RUNTIME_SWITCH":
        findings.append("INSTALL_REFERENCE_ONLY_MODE_REQUIRED")

    for key in (
        "runtimeSwitchAuthorized",
        "serverBindingAuthorized",
        "embeddedSourceDeletionAuthorized",
    ):
        if tx.get(key) is not False or authority.get(key) is not False:
            findings.append(f"UNAUTHORIZED_SIDE_EFFECT:{key}")
    if authority.get("productRuntimeAuthority") != "AI_ECOMMERCE_PRODUCT_REPOSITORY":
        findings.append("PRODUCT_RUNTIME_AUTHORITY_CHANGED")

    material: Dict[str, Any] = {
        "schema": "z.ai_ecommerce.root_migration_receipt.v1",
        "version": "Z1.0.5",
        "status": (
            "VERIFIED_EXTERNAL_ROOT_AUTHORITY_ACTIVE_RUNTIME_NOT_SWITCHED"
            if not findings
            else "ROOT_AUTHORITY_MIGRATION_FAILED"
        ),
        "transactionId": tx.get("transactionId"),
        "migrationTransactionHash": tx.get("migrationTransactionHash"),
        "authorityHash": authority.get("authorityHash"),
        "sourceRepository": tx.get("sourceRepository"),
        "sourceCommit": tx.get("sourceCommit"),
        "destinationRepository": tx.get("destinationRepository"),
        "destinationCommit": tx.get("destinationCommit"),
        "destinationAuthorityIntegrationCommit": tx.get(
            "destinationAuthorityIntegrationCommit"
        ),
        "sourceCoreHash": tx.get("sourceCoreHash"),
        "destinationCoreHash": tx.get("destinationCoreHash"),
        "releaseHash": tx.get("releaseHash"),
        "adapterHash": tx.get("adapterHash"),
        "oldScopeHash": tx.get("oldScopeHash"),
        "newScopeHash": tx.get("newScopeHash"),
        "rollbackCommit": tx.get("rollbackCommit"),
        "verified": not findings,
        "findings": sorted(set(findings)),
        "coreHashEquivalent": tx.get("sourceCoreHash") == tx.get("destinationCoreHash"),
        "rootAuthorityTransferred": not findings,
        "runtimeSwitchAuthorized": False,
        "serverBindingAuthorized": False,
        "embeddedSourceDeletionAuthorized": False,
        "productRuntimeRemainsAuthoritative": True,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "ecsMutated": False,
    }
    result = {**material, "verificationHash": canonical_hash(material)}
    if check_receipt:
        receipt = read_object(RECEIPT_PATH)
        if receipt != result:
            findings.append("COMMITTED_RECEIPT_MISMATCH")
            material["status"] = "ROOT_AUTHORITY_MIGRATION_FAILED"
            material["verified"] = False
            material["rootAuthorityTransferred"] = False
            material["findings"] = sorted(set(findings))
            result = {**material, "verificationHash": canonical_hash(material)}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-receipt", action="store_true")
    args = parser.parse_args()
    result = verify(check_receipt=args.check_receipt)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("verified") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
