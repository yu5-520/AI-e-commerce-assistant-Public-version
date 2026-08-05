"""Machine-readable change manifest validation for V23 beta.1."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from .compile_registry import load_registry_documents, sha256_value

CHANGE_MANIFEST_VERSION = "23.0.0-beta.1"
_ALLOWED_APPROVAL = {"DRAFT", "APPROVED", "REJECTED", "CLOSED"}
_CHANGE_KEYS = ("fields", "schemas", "modules", "interfaces", "stations")


class ChangeManifestError(RuntimeError):
    """Raised when a registry change manifest is not internally valid."""


def _records(document: Mapping[str, Any], key: str, identity: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for raw in document.get(key) or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get(identity) or "").strip()
        if item_id:
            result[item_id] = dict(raw)
    return result


def _strings(values: Iterable[Any]) -> List[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def load_change_manifest(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ChangeManifestError(f"change_manifest_read_failed:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise ChangeManifestError("change_manifest_must_be_object")
    return value


def validate_change_manifest(
    manifest: Mapping[str, Any], root: Path | None = None
) -> Dict[str, Any]:
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    documents = load_registry_documents(repository)
    indexes = {
        "fields": _records(documents["fields.json"], "fields", "fieldId"),
        "schemas": _records(documents["schemas.json"], "schemas", "schemaId"),
        "modules": _records(documents["modules.json"], "modules", "moduleId"),
        "interfaces": _records(documents["interfaces.json"], "interfaces", "interfaceId"),
        "stations": _records(documents["stations.json"], "stations", "stationId"),
    }

    errors: List[str] = []
    warnings: List[str] = []
    change_id = str(manifest.get("changeId") or "").strip()
    if not change_id:
        errors.append("change_id_required")
    if str(manifest.get("schema") or "") != "registry.change_manifest.v1":
        errors.append("change_schema_invalid")

    approval = dict(manifest.get("approval") or {})
    approval_status = str(approval.get("status") or "DRAFT").upper()
    if approval_status not in _ALLOWED_APPROVAL:
        errors.append(f"approval_status_invalid:{approval_status}")
    if approval_status == "APPROVED" and not str(approval.get("approvedBy") or "").strip():
        errors.append("approved_change_requires_approved_by")

    raw_changes = dict(manifest.get("changes") or {})
    changes: Dict[str, List[str]] = {}
    for key in _CHANGE_KEYS:
        changes[key] = _strings(raw_changes.get(key) or [])
        for item_id in changes[key]:
            if item_id not in indexes[key]:
                errors.append(f"change_target_not_registered:{key}:{item_id}")

    expected_implementation = _strings(manifest.get("expectedImplementationModules") or [])
    approved_no_code = _strings(manifest.get("approvedNoCodeChangeModules") or [])
    for module_id in [*expected_implementation, *approved_no_code]:
        if module_id not in indexes["modules"]:
            errors.append(f"change_module_not_registered:{module_id}")

    changed_paths = _strings(manifest.get("changedPaths") or [])
    path_module_hints: Dict[str, List[str]] = {}
    for path, module_ids in dict(manifest.get("pathModuleHints") or {}).items():
        normalized_path = str(path).strip().replace("\\", "/")
        if not normalized_path:
            continue
        path_module_hints[normalized_path] = _strings(module_ids or [])
        for module_id in path_module_hints[normalized_path]:
            if module_id not in indexes["modules"]:
                errors.append(f"path_hint_module_not_registered:{normalized_path}:{module_id}")

    if not any(changes.values()):
        warnings.append("change_has_no_registered_targets")
    if not changed_paths:
        warnings.append("change_has_no_changed_paths")

    normalized = {
        "schema": "registry.change_manifest.v1",
        "version": str(manifest.get("version") or CHANGE_MANIFEST_VERSION),
        "mode": "soft_gate",
        "changeId": change_id,
        "description": str(manifest.get("description") or "").strip(),
        "changes": changes,
        "expectedImplementationModules": expected_implementation,
        "approvedNoCodeChangeModules": approved_no_code,
        "changedPaths": changed_paths,
        "pathModuleHints": path_module_hints,
        "approval": {
            "status": approval_status,
            "approvedBy": str(approval.get("approvedBy") or "").strip(),
            "approvedAt": str(approval.get("approvedAt") or "").strip(),
            "semanticReviewRequired": bool(approval.get("semanticReviewRequired", False)),
        },
    }
    return {
        "schema": "registry.change_manifest.validation.v1",
        "version": CHANGE_MANIFEST_VERSION,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "manifest": normalized,
        "changeManifestHash": sha256_value(normalized),
    }
