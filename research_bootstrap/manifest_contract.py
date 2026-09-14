from __future__ import annotations

import copy
import re
from typing import Any, Dict

from core import sha256_json


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_CONDITIONS = {
    "baseline_runtime",
    "information_authority",
    "invocation_authority",
    "temporal_authority",
    "full_authority",
}
REQUIRED_DECODING = {"temperature", "top_p", "max_output_tokens"}


class ManifestContractError(ValueError):
    pass


def validate_manifest(manifest: Dict[str, Any], *, require_concrete_provider: bool = False) -> None:
    required = {
        "experiment_id", "research_commit", "sut_commit", "provider", "model_id",
        "model_version", "adapter_version", "decoding", "budget", "conditions",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ManifestContractError(f"manifest_missing:{','.join(missing)}")
    for key in ("research_commit", "sut_commit"):
        if not SHA_RE.fullmatch(str(manifest.get(key) or "")):
            raise ManifestContractError(f"manifest_invalid_commit:{key}")
    conditions = manifest.get("conditions")
    if not isinstance(conditions, list) or not conditions or len(conditions) != len(set(conditions)):
        raise ManifestContractError("manifest_conditions_invalid")
    unknown = set(conditions) - ALLOWED_CONDITIONS
    if unknown:
        raise ManifestContractError(f"manifest_condition_unknown:{sorted(unknown)}")
    decoding = manifest.get("decoding")
    if not isinstance(decoding, dict) or not REQUIRED_DECODING.issubset(decoding):
        raise ManifestContractError("manifest_decoding_incomplete")
    budget = manifest.get("budget")
    if not isinstance(budget, dict) or float(budget.get("max_cost", -1)) < 0 or int(budget.get("max_tokens", 0)) <= 0:
        raise ManifestContractError("manifest_budget_invalid")
    if require_concrete_provider:
        provider = str(manifest.get("provider") or "").strip().lower()
        model_id = str(manifest.get("model_id") or "").strip().lower()
        version = str(manifest.get("model_version") or "").strip()
        if provider in {"", "fixture", "template", "provider-neutral"}:
            raise ManifestContractError("manifest_provider_not_concrete")
        if model_id in {"", "fixture-neutral", "model-placeholder", "template"}:
            raise ManifestContractError("manifest_model_not_concrete")
        if not version:
            raise ManifestContractError("manifest_model_version_unfrozen")


def freeze_manifest(manifest: Dict[str, Any], *, require_concrete_provider: bool = False) -> Dict[str, Any]:
    validate_manifest(manifest, require_concrete_provider=require_concrete_provider)
    frozen = copy.deepcopy(manifest)
    frozen["manifest_contract_version"] = "rb-manifest-v1"
    frozen["status"] = "frozen"
    body = {key: value for key, value in frozen.items() if key != "manifest_hash"}
    frozen["manifest_hash"] = sha256_json(body)
    return frozen


def _assert_self_hash(manifest: Dict[str, Any], *, label: str) -> None:
    declared = str(manifest.get("manifest_hash") or "")
    if not declared:
        raise ManifestContractError(f"{label}_manifest_hash_missing")
    body = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    if sha256_json(body) != declared:
        raise ManifestContractError(f"{label}_manifest_tampered")


def assert_manifest_immutable(existing: Dict[str, Any], requested: Dict[str, Any]) -> None:
    if existing.get("status") != "frozen":
        raise ManifestContractError("existing_manifest_not_frozen")
    if requested.get("status") != "frozen":
        raise ManifestContractError("requested_manifest_not_frozen")
    _assert_self_hash(existing, label="existing")
    _assert_self_hash(requested, label="requested")
    if existing.get("manifest_hash") != requested.get("manifest_hash"):
        raise ManifestContractError("frozen_manifest_hash_mismatch")
