"""Unified runtime contract guards derived from registered runtime contracts.

The historical compatibility guards remain unchanged. V26.1 adds a registered
field-header authority layer to the same guard surface: header ownership, value type
and references are fail-closed, while SEMANTIC business wording is intentionally
opaque to authority validation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

from src.services import agent_token_runtime_v2259_service as token_runtime
from src.services.hash_directed_artifact_runtime_v2259_service import (
    ensure_hash_directed_runtime_tables,
)
from src.services.v26_field_authority_contract_service import (
    V26_FIELD_AUTHORITY_VERSION,
    V26FieldAuthorityContract,
)

RUNTIME_CONTRACT_GUARD_VERSION = "2026.09.10.1"

_FIELD_AUTHORITY: V26FieldAuthorityContract | None = None


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def strict_descriptor_for_raw(
    raw: Dict[str, Any],
    descriptors: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """Resolve a provider item only by its two immutable execution identities."""

    item_execution_id = _text(raw.get("itemExecutionId"))
    input_content_hash = _text(raw.get("inputContentHash"))
    if not item_execution_id or not input_content_hash:
        return None
    matches = [
        item
        for item in descriptors
        if _text(item.get("itemExecutionId")) == item_execution_id
        and _text(item.get("inputContentHash")) == input_content_hash
    ]
    return matches[0] if len(matches) == 1 else None


def field_authority_contract() -> V26FieldAuthorityContract:
    global _FIELD_AUTHORITY
    if _FIELD_AUTHORITY is None:
        _FIELD_AUTHORITY = V26FieldAuthorityContract()
    return _FIELD_AUTHORITY


def assert_field_write(actor: str, header: str, value: Any) -> Dict[str, Any]:
    """Fail closed on field-header authority; never inspect semantic wording."""
    return field_authority_contract().assert_write(actor, header, value)


def assert_field_read(actor: str, header: str) -> Dict[str, Any]:
    return field_authority_contract().assert_read(actor, header)


def assert_payload_write(actor: str, payload: Mapping[str, Any]) -> None:
    field_authority_contract().assert_payload_write(actor, payload)


def assert_stage_write(actor: str, stage_kind: str) -> None:
    field_authority_contract().assert_stage_write(actor, stage_kind)


def install_runtime_contract_guards() -> Dict[str, Any]:
    """Install fail-closed compatibility aliases and activate V26.1 authority."""

    # The interface implementation remains owned by hash_directed_artifact_runtime;
    # this is only a forwarding alias for older consumers such as Agent3 semantic
    # cache code that still imports the token runtime module object.
    token_runtime.ensure_hash_directed_runtime_tables = ensure_hash_directed_runtime_tables

    # Tighten the historical matcher in-place so downstream legacy wrappers that
    # retain a module reference inherit the same fail-closed identity rule.
    token_runtime._descriptor_for_raw = strict_descriptor_for_raw

    authority_receipt = field_authority_contract().receipt()
    return {
        "version": RUNTIME_CONTRACT_GUARD_VERSION,
        "hashTableInterfaceOwner": (
            "src.services.hash_directed_artifact_runtime_v2259_service:"
            "ensure_hash_directed_runtime_tables"
        ),
        "legacyHashTableAliasInstalled": True,
        "providerOutputIdentity": "itemExecutionId+inputContentHash",
        "hashOnlyFallbackAllowed": False,
        "productStoreFallbackAllowed": False,
        "fallbackAllowed": False,
        "fieldAuthorityVersion": V26_FIELD_AUTHORITY_VERSION,
        "fieldAuthority": authority_receipt,
    }


__all__ = [
    "RUNTIME_CONTRACT_GUARD_VERSION",
    "strict_descriptor_for_raw",
    "field_authority_contract",
    "assert_field_write",
    "assert_field_read",
    "assert_payload_write",
    "assert_stage_write",
    "install_runtime_contract_guards",
]
