from __future__ import annotations

import os
from typing import Any, Dict

from manifest_contract import assert_manifest_immutable, validate_manifest


class ProviderBindingError(RuntimeError):
    pass


def validate_provider_binding(
    *,
    frozen_manifest: Dict[str, Any],
    adapter,
    require_secret: bool = True,
) -> Dict[str, Any]:
    """Validate a concrete provider binding without making any network request.

    The frozen manifest is self-verified first. Adapter identity, proposal protocol,
    decoding parameters, provider-specific request options and endpoint hash must exactly
    match the frozen contract. Secret presence is checked by environment-variable name
    only; the secret value is never returned or hashed here.
    """
    validate_manifest(frozen_manifest, require_concrete_provider=True)
    assert_manifest_immutable(frozen_manifest, frozen_manifest)

    fragment = adapter.manifest_fragment()
    fields = (
        "provider",
        "model_id",
        "model_version",
        "adapter_version",
        "proposal_protocol_version",
        "decoding",
        "request_options",
        "endpoint_hash",
    )
    mismatches = []
    for field in fields:
        if frozen_manifest.get(field) != fragment.get(field):
            mismatches.append(field)
    if mismatches:
        raise ProviderBindingError(f"provider_binding_mismatch:{','.join(mismatches)}")

    secret_name = str(getattr(adapter, "api_key_env", "") or "").strip()
    if not secret_name:
        raise ProviderBindingError("provider_secret_env_name_missing")
    secret_present = bool(os.environ.get(secret_name))
    if require_secret and not secret_present:
        raise ProviderBindingError(f"provider_secret_missing:{secret_name}")

    return {
        "status": "PASS",
        "provider": fragment["provider"],
        "model_id": fragment["model_id"],
        "model_version": fragment["model_version"],
        "adapter_version": fragment["adapter_version"],
        "proposal_protocol_version": fragment["proposal_protocol_version"],
        "request_options": fragment["request_options"],
        "endpoint_hash": fragment["endpoint_hash"],
        "manifest_hash": frozen_manifest["manifest_hash"],
        "secret_env": secret_name,
        "secret_present": secret_present,
        "network_request_made": False,
        "paid_model_calls": 0,
    }
