import importlib
from unittest.mock import patch

import pytest

from src.services import agent3_sop_core_v225_service as core


def _fresh_core():
    # The full regression suite installs runtime patches into shared service modules.
    # These bridge tests verify the registered source seam itself, so reload the
    # read-only runtime module before each assertion instead of depending on suite order.
    return importlib.reload(core)


def test_v269_agent3_messages_delegate_to_graph_contract_without_legacy_compiler():
    module = _fresh_core()
    packages = [{"semanticContractVersion": "26.9.0", "packageId": "pkg"}]
    sentinel = ([{"role": "system", "content": "graph"}], {"version": "26.9.0"})
    with patch(
        "src.services.v269_input_migration_service.uses_graph_contract",
        return_value=True,
    ), patch(
        "src.services.v269_input_migration_service.provider_messages",
        return_value=sentinel,
    ) as provider, patch.object(module, "compile_agent3_provider_package") as legacy:
        result = module._build_messages("dv", packages)
    assert result == sentinel
    provider.assert_called_once_with("agent3", "dv", packages)
    legacy.assert_not_called()


def test_v269_agent3_messages_fail_closed_on_mixed_contract_batch():
    module = _fresh_core()
    packages = [
        {"semanticContractVersion": "26.9.0", "packageId": "graph"},
        {"packageId": "legacy"},
    ]
    with patch(
        "src.services.v269_input_migration_service.provider_messages"
    ) as provider, patch.object(module, "compile_agent3_provider_package") as legacy:
        with pytest.raises(ValueError, match="mixed_provider_contracts"):
            module._build_messages("dv", packages)
    provider.assert_not_called()
    legacy.assert_not_called()


def test_v269_agent3_normalization_delegates_to_graph_contract():
    module = _fresh_core()
    package = {"semanticContractVersion": "26.9.0", "packageId": "pkg"}
    raw = {"packageId": "pkg", "OperationGraph": {"nodes": [], "edges": []}}
    proof = {"passed": True}
    sentinel = {"sopStatus": "sop_ready", "OperationGraph": {"kind": "OperationGraph"}}
    with patch(
        "src.services.v269_input_migration_service.uses_graph_contract",
        return_value=True,
    ), patch(
        "src.services.v269_input_migration_service.normalize_output",
        return_value=sentinel,
    ) as normalize:
        result = module._normalize_sop(raw, package, proof)
    assert result == sentinel
    normalize.assert_called_once_with("agent3", raw, package, proof)


def test_legacy_agent3_message_path_remains_on_existing_compiler():
    module = _fresh_core()
    packages = [{"packageId": "legacy"}]
    compiled = {"packageId": "legacy", "lockedActionFamily": "conversion_repair"}
    with patch(
        "src.services.v269_input_migration_service.uses_graph_contract",
        return_value=False,
    ), patch.object(
        module, "compile_agent3_provider_package", return_value=compiled
    ) as legacy, patch(
        "src.services.v269_input_migration_service.provider_messages"
    ) as provider:
        messages, payload = module._build_messages("dv", packages)
    legacy.assert_called_once_with(packages[0])
    provider.assert_not_called()
    assert payload["version"] == module.AGENT3_SOP_CORE_VERSION
    assert payload["packages"] == [compiled]
    assert messages[-1]["role"] == "user"
