"""Persisted gray/production module receipts for V23 beta.2.

Receipts prove which registered module contract Hashes were loaded by one checked-out
release. Beta.2 keeps this as a soft gate: receipts are written and compared, but no
production deployment is blocked yet.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Set

from .compile_registry import sha256_value
from .module_contracts import build_module_contracts

RUNTIME_RECEIPT_VERSION = "23.0.0-beta.2"
_ALLOWED_ENVIRONMENTS = {"repository_validation", "gray", "production"}


class RuntimeReceiptError(RuntimeError):
    """Raised when a runtime receipt set cannot be created or loaded."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _strings(values: Iterable[Any]) -> List[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _receipt_material(receipt: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: receipt.get(key)
        for key in (
            "schema",
            "version",
            "environment",
            "releaseCommit",
            "capturedAt",
            "moduleId",
            "registryRootHash",
            "moduleContractRootHash",
            "moduleContractHash",
            "runner",
            "implementationContentHashes",
            "fieldDefinitionHashes",
            "schemaContractHashes",
            "loadStatus",
            "source",
        )
    }


def _receipt_set_material(receipt_set: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: receipt_set.get(key)
        for key in (
            "schema",
            "version",
            "environment",
            "releaseCommit",
            "capturedAt",
            "registryRootHash",
            "moduleContractRootHash",
            "requiredModules",
            "receipts",
        )
    }


def build_runtime_receipt_set(
    root: Path | None = None,
    *,
    environment: str,
    release_commit: str,
    modules: Iterable[str] = (),
    captured_at: str | None = None,
    source: str = "checked_out_repository",
) -> Dict[str, Any]:
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    normalized_environment = str(environment or "").strip().lower()
    if normalized_environment not in _ALLOWED_ENVIRONMENTS:
        raise RuntimeReceiptError(f"runtime_environment_invalid:{normalized_environment}")
    normalized_release = str(release_commit or "").strip()
    if not normalized_release:
        raise RuntimeReceiptError("release_commit_required")

    contracts = build_module_contracts(repository)
    all_contracts = dict(contracts.get("moduleContracts") or {})
    required_modules = _strings(modules) or sorted(all_contracts)
    missing = [module_id for module_id in required_modules if module_id not in all_contracts]
    if missing:
        raise RuntimeReceiptError("receipt_module_not_registered:" + ",".join(missing))

    captured = str(captured_at or _utc_now()).strip()
    receipts: List[Dict[str, Any]] = []
    for module_id in required_modules:
        contract = dict(all_contracts[module_id])
        receipt: Dict[str, Any] = {
            "schema": "registry.runtime_module_receipt.v1",
            "version": RUNTIME_RECEIPT_VERSION,
            "environment": normalized_environment,
            "releaseCommit": normalized_release,
            "capturedAt": captured,
            "moduleId": module_id,
            "registryRootHash": contracts.get("registryRootHash"),
            "moduleContractRootHash": contracts.get("moduleContractRootHash"),
            "moduleContractHash": contract.get("moduleContractHash"),
            "runner": dict(contract.get("definition") or {}).get("runner"),
            "implementationContentHashes": dict(
                contract.get("implementationContentHashes") or {}
            ),
            "fieldDefinitionHashes": dict(contract.get("fieldDefinitionHashes") or {}),
            "schemaContractHashes": dict(contract.get("schemaContractHashes") or {}),
            "loadStatus": "loaded",
            "source": str(source or "checked_out_repository"),
        }
        receipt["receiptHash"] = sha256_value(_receipt_material(receipt))
        receipts.append(receipt)

    receipt_set: Dict[str, Any] = {
        "schema": "registry.runtime_receipt_set.v1",
        "version": RUNTIME_RECEIPT_VERSION,
        "mode": "soft_gate",
        "environment": normalized_environment,
        "releaseCommit": normalized_release,
        "capturedAt": captured,
        "registryRootHash": contracts.get("registryRootHash"),
        "moduleContractRootHash": contracts.get("moduleContractRootHash"),
        "requiredModules": required_modules,
        "receipts": receipts,
        "deploymentBlocked": False,
    }
    receipt_set["receiptSetHash"] = sha256_value(_receipt_set_material(receipt_set))
    return receipt_set


def persist_runtime_receipt_set(
    receipt_set: Mapping[str, Any], path: Path, root: Path | None = None
) -> Path:
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    target = path.resolve()
    if repository not in target.parents:
        raise RuntimeReceiptError("receipt_output_must_be_inside_repository")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dict(receipt_set), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_runtime_receipt_set(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeReceiptError(f"runtime_receipt_read_failed:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeReceiptError("runtime_receipt_set_must_be_object")
    return value


def verify_runtime_receipt_set(
    receipt_set: Mapping[str, Any],
    root: Path | None = None,
    *,
    expected_environment: str | None = None,
    expected_release_commit: str | None = None,
    required_modules: Iterable[str] = (),
) -> Dict[str, Any]:
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    contracts = build_module_contracts(repository)
    approved = dict(contracts.get("moduleContracts") or {})
    errors: List[str] = []
    warnings: List[str] = []

    environment = str(receipt_set.get("environment") or "").strip().lower()
    release_commit = str(receipt_set.get("releaseCommit") or "").strip()
    if receipt_set.get("schema") != "registry.runtime_receipt_set.v1":
        errors.append("receipt_set_schema_invalid")
    if environment not in _ALLOWED_ENVIRONMENTS:
        errors.append(f"receipt_set_environment_invalid:{environment}")
    if expected_environment and environment != str(expected_environment).strip().lower():
        errors.append(
            f"receipt_set_environment_mismatch:{environment}:{str(expected_environment).strip().lower()}"
        )
    if expected_release_commit and release_commit != str(expected_release_commit).strip():
        errors.append("receipt_set_release_commit_mismatch")
    if receipt_set.get("registryRootHash") != contracts.get("registryRootHash"):
        errors.append("receipt_set_registry_root_hash_mismatch")
    if receipt_set.get("moduleContractRootHash") != contracts.get("moduleContractRootHash"):
        errors.append("receipt_set_module_contract_root_hash_mismatch")

    expected_set_hash = sha256_value(_receipt_set_material(receipt_set))
    if receipt_set.get("receiptSetHash") != expected_set_hash:
        errors.append("receipt_set_hash_invalid")

    indexed: MutableMapping[str, Dict[str, Any]] = {}
    duplicate_modules: Set[str] = set()
    receipt_errors: MutableMapping[str, List[str]] = {}
    for raw in receipt_set.get("receipts") or []:
        if not isinstance(raw, dict):
            errors.append("receipt_record_must_be_object")
            continue
        module_id = str(raw.get("moduleId") or "").strip()
        if not module_id:
            errors.append("receipt_module_id_required")
            continue
        if module_id in indexed:
            duplicate_modules.add(module_id)
        indexed[module_id] = dict(raw)
        local: List[str] = []
        if raw.get("schema") != "registry.runtime_module_receipt.v1":
            local.append("schema_invalid")
        if str(raw.get("environment") or "").lower() != environment:
            local.append("environment_mismatch")
        if str(raw.get("releaseCommit") or "") != release_commit:
            local.append("release_commit_mismatch")
        if raw.get("registryRootHash") != contracts.get("registryRootHash"):
            local.append("registry_root_hash_mismatch")
        if raw.get("moduleContractRootHash") != contracts.get("moduleContractRootHash"):
            local.append("module_contract_root_hash_mismatch")
        if raw.get("receiptHash") != sha256_value(_receipt_material(raw)):
            local.append("receipt_hash_invalid")
        contract = approved.get(module_id)
        if not contract:
            local.append("module_not_registered")
        else:
            if raw.get("moduleContractHash") != contract.get("moduleContractHash"):
                local.append("module_contract_hash_mismatch")
            if dict(raw.get("implementationContentHashes") or {}) != dict(
                contract.get("implementationContentHashes") or {}
            ):
                local.append("implementation_hash_mismatch")
            if dict(raw.get("schemaContractHashes") or {}) != dict(
                contract.get("schemaContractHashes") or {}
            ):
                local.append("schema_contract_hash_mismatch")
        if raw.get("loadStatus") != "loaded":
            local.append("module_not_loaded")
        if local:
            receipt_errors[module_id] = local

    if duplicate_modules:
        errors.append("duplicate_module_receipts:" + ",".join(sorted(duplicate_modules)))

    requested = _strings(required_modules) or _strings(receipt_set.get("requiredModules") or [])
    missing_modules = sorted(set(requested) - set(indexed))
    unexpected_modules = sorted(set(indexed) - set(requested)) if requested else []
    if missing_modules:
        errors.append("required_module_receipts_missing:" + ",".join(missing_modules))
    if unexpected_modules:
        warnings.append("unexpected_module_receipts:" + ",".join(unexpected_modules))

    verified = not errors and not receipt_errors
    material = {
        "receiptSetHash": receipt_set.get("receiptSetHash"),
        "registryRootHash": contracts.get("registryRootHash"),
        "moduleContractRootHash": contracts.get("moduleContractRootHash"),
        "environment": environment,
        "releaseCommit": release_commit,
        "requiredModules": requested,
        "missingModules": missing_modules,
        "unexpectedModules": unexpected_modules,
        "receiptErrors": {key: value for key, value in sorted(receipt_errors.items())},
        "errors": errors,
        "warnings": warnings,
    }
    return {
        "schema": "registry.runtime_receipt_verification.v1",
        "version": RUNTIME_RECEIPT_VERSION,
        "mode": "soft_gate",
        "verified": verified,
        "softGateStatus": "PASS" if verified else "WARN",
        "deploymentBlocked": False,
        "environment": environment,
        "releaseCommit": release_commit,
        "requiredModules": requested,
        "missingModules": missing_modules,
        "unexpectedModules": unexpected_modules,
        "receiptErrors": material["receiptErrors"],
        "errors": errors,
        "warnings": warnings,
        "verificationHash": sha256_value(material),
    }


def compare_environment_receipts(
    gray_receipt_set: Mapping[str, Any],
    production_receipt_set: Mapping[str, Any],
    root: Path | None = None,
    *,
    required_modules: Iterable[str] = (),
) -> Dict[str, Any]:
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    required = _strings(required_modules)
    gray = verify_runtime_receipt_set(
        gray_receipt_set,
        repository,
        expected_environment="gray",
        required_modules=required,
    )
    production = verify_runtime_receipt_set(
        production_receipt_set,
        repository,
        expected_environment="production",
        required_modules=required,
    )

    gray_index = {
        str(item.get("moduleId")): dict(item)
        for item in gray_receipt_set.get("receipts") or []
        if isinstance(item, dict) and item.get("moduleId")
    }
    production_index = {
        str(item.get("moduleId")): dict(item)
        for item in production_receipt_set.get("receipts") or []
        if isinstance(item, dict) and item.get("moduleId")
    }
    modules = required or sorted(set(gray_index) | set(production_index))
    parity_mismatches: Dict[str, List[str]] = {}
    for module_id in modules:
        local: List[str] = []
        gray_receipt = gray_index.get(module_id)
        production_receipt = production_index.get(module_id)
        if not gray_receipt:
            local.append("gray_receipt_missing")
        if not production_receipt:
            local.append("production_receipt_missing")
        if gray_receipt and production_receipt:
            for key in (
                "registryRootHash",
                "moduleContractRootHash",
                "moduleContractHash",
                "runner",
                "implementationContentHashes",
                "schemaContractHashes",
            ):
                if gray_receipt.get(key) != production_receipt.get(key):
                    local.append(f"{key}_mismatch")
        if local:
            parity_mismatches[module_id] = local

    release_commit_match = gray_receipt_set.get("releaseCommit") == production_receipt_set.get(
        "releaseCommit"
    )
    parity_passed = bool(
        gray.get("verified") is True
        and production.get("verified") is True
        and release_commit_match
        and not parity_mismatches
    )
    material = {
        "grayVerificationHash": gray.get("verificationHash"),
        "productionVerificationHash": production.get("verificationHash"),
        "grayReceiptSetHash": gray_receipt_set.get("receiptSetHash"),
        "productionReceiptSetHash": production_receipt_set.get("receiptSetHash"),
        "requiredModules": modules,
        "releaseCommitMatch": release_commit_match,
        "parityMismatches": parity_mismatches,
    }
    return {
        "schema": "registry.environment_receipt_comparison.v1",
        "version": RUNTIME_RECEIPT_VERSION,
        "mode": "soft_gate",
        "softGateStatus": "PASS" if parity_passed else "WARN",
        "softGatePassed": parity_passed,
        "deploymentBlocked": False,
        "gray": gray,
        "production": production,
        "releaseCommitMatch": release_commit_match,
        "requiredModules": modules,
        "parityMismatches": parity_mismatches,
        "comparisonHash": sha256_value(material),
    }
