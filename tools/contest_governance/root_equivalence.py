"""Verify product Registry roots through the installed layered-equivalence contract."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional


def _read_declared_root(
    path: Path,
    expected_root: str,
    read_object: Callable[[Path], Dict[str, Any]],
) -> Optional[str]:
    if not path.exists():
        return None
    if path.suffix.lower() == ".json":
        try:
            return str(read_object(path).get("registryRootHash") or "")
        except Exception:
            return None
    content = path.read_text(encoding="utf-8")
    return expected_root if expected_root and expected_root in content else None


def evaluate_root_equivalence(
    root: Path,
    adapter_layout: Mapping[str, Any],
    equivalence_contract: Mapping[str, Any],
    read_object: Callable[[Path], Dict[str, Any]],
) -> Dict[str, Any]:
    """Return deterministic evidence for layered Registry root composition.

    Distinct roots are valid when the installed contract declares layered composition,
    each product layer contains its declared root, and the adapter observes the same
    root set. Literal equality is enforced only when the contract explicitly requires it.
    """
    repository = root.resolve()
    observed_roots = dict(adapter_layout.get("observedLayeredRoots") or {})
    product_layers = dict(equivalence_contract.get("productLayers") or {})

    layer_checks = []
    for layer_id, raw_layer in sorted(product_layers.items()):
        layer = dict(raw_layer or {})
        relative_path = str(layer.get("path") or "")
        expected_root = str(layer.get("registryRootHash") or "")
        actual_root = _read_declared_root(
            repository / relative_path,
            expected_root,
            read_object,
        ) if relative_path else None
        layer_checks.append(
            {
                "layerId": str(layer_id),
                "role": layer.get("role"),
                "path": relative_path,
                "expectedRegistryRootHash": expected_root,
                "actualRegistryRootHash": actual_root,
                "matches": bool(expected_root) and actual_root == expected_root,
            }
        )

    composition_rules = dict(equivalence_contract.get("compositionRules") or {})
    literal_required = bool(composition_rules.get("literalRootEqualityRequired"))
    contract_values = sorted(
        {
            str(item.get("expectedRegistryRootHash") or "")
            for item in layer_checks
            if str(item.get("expectedRegistryRootHash") or "")
        }
    )
    observed_values = sorted(
        {str(value) for value in observed_roots.values() if str(value)}
    )
    literal_equal = len(contract_values) <= 1
    layer_declarations_match = bool(layer_checks) and all(
        bool(item.get("matches")) for item in layer_checks
    )
    observed_roots_match_contract = observed_values == contract_values
    activation_state = str(equivalence_contract.get("activationState") or "")
    contract_state_verified = activation_state.startswith("EQUIVALENCE_VERIFIED")

    verified = (
        contract_state_verified
        and layer_declarations_match
        and observed_roots_match_contract
        and (literal_equal if literal_required else True)
    )

    return {
        "schema": equivalence_contract.get("schema"),
        "version": equivalence_contract.get("version"),
        "activationState": activation_state,
        "equivalenceMode": equivalence_contract.get("equivalenceMode"),
        "literalRootEqualityRequired": literal_required,
        "literalRootEquality": literal_equal,
        "layerDeclarationsMatch": layer_declarations_match,
        "observedRootsMatchContract": observed_roots_match_contract,
        "observedLayeredRoots": observed_roots,
        "verified": verified,
        "layerChecks": layer_checks,
    }


__all__ = ["evaluate_root_equivalence"]
