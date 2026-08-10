"""Unified runtime contract guards derived from the contract-lineage registry.

This module intentionally contains no business fallback.  It installs two narrow
compatibility guards while preserving the canonical owners declared in
``config/runtime_contract_lineage_registry_v1.json``:

* ``ensure_hash_directed_runtime_tables`` is owned by the hash-directed Artifact
  runtime.  A legacy consumer alias may forward to that owner, but the
  implementation is never duplicated.
* provider output identity is strict ``itemExecutionId + inputContentHash``.  Store
  and product identity are diagnostics only and can never rebind a model result.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.services import agent_token_runtime_v2259_service as token_runtime
from src.services.hash_directed_artifact_runtime_v2259_service import (
    ensure_hash_directed_runtime_tables,
)

RUNTIME_CONTRACT_GUARD_VERSION = "2026.08.11.1"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def strict_descriptor_for_raw(
    raw: Dict[str, Any],
    descriptors: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """Resolve a provider item only by its two immutable execution identities.

    Deliberately rejects hash-only, product/store and list-position recovery.  The
    caller may report those fields as diagnostics, but may not use them to bind an
    output to an execution.
    """

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


def install_runtime_contract_guards() -> Dict[str, Any]:
    """Install fail-closed compatibility aliases on the active token runtime."""

    # The interface implementation remains owned by hash_directed_artifact_runtime;
    # this is only a forwarding alias for older consumers such as Agent3 semantic
    # cache code that still imports the token runtime module object.
    token_runtime.ensure_hash_directed_runtime_tables = ensure_hash_directed_runtime_tables

    # Tighten the historical matcher in-place so downstream legacy wrappers that
    # retain a module reference inherit the same fail-closed identity rule.
    token_runtime._descriptor_for_raw = strict_descriptor_for_raw

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
    }


__all__ = [
    "RUNTIME_CONTRACT_GUARD_VERSION",
    "strict_descriptor_for_raw",
    "install_runtime_contract_guards",
]
