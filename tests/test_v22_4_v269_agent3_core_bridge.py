from unittest.mock import patch

import pytest

from src.services import agent3_sop_core_v225_service as core


def test_v269_agent3_messages_delegate_to_graph_contract_without_legacy_compiler():
    packages = [{"semanticContractVersion": "26.9.0", "packageId": "pkg"}]
    sentinel = ([{"role": "system", "content": "graph"}], {"version": "26.9.0"})
    with patch(
        "src.services.v269_input_migration_service.uses_graph_contract",
        return_value=True,
    ), patch(
        "src.services.v269_input_migration_service.provider_messages",
        return_value=sentinel,
    ) as provider, patch.object(core, "compile_agent3_provider_package") as legacy:
        result = core._build_messages("dv", packages)
    assert result == sentinel
    provider.assert_called_once_with("agent3", "dv", packages)
    legacy.assert_not_called()


def test_v269_agent3_messages_fail_closed_on_mixed_contract_batch():
    packages = [
        {"semanticContractVersion": "26.9.0", "packageId": "graph"},
        {"packageId": "legacy"},
    ]

    def is_graph(package):
        return package.get("semanticContractVersion") == "26.9.0"

    with patch(
        "src.services.v269_input_migration_service.uses_graph_contract",
        side_effect=is_graph,
    ), patch(
        "src.services.v269_input_migration_service.provider_messages"
    ) as provider, patch.object(core, "compile_agent3_provider_package") as legacy:
        with pytest.raises(ValueError, match="mixed_provider_contracts"):
            core._build_messages("dv", packages)
    provider.assert_not_called()
    legacy.assert_not_called()


def test_v269_agent3_normalization_delegates_to_graph_contract():
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
        result = core._normalize_sop(raw, package, proof)
    assert result == sentinel
    normalize.assert_called_once_with("agent3", raw, package, proof)


def test_legacy_agent3_message_path_remains_on_existing_compiler():
    packages = [{"packageId": "legacy"}]
    compiled = {"packageId": "legacy", "lockedActionFamily": "conversion_repair"}
    with patch(
        "src.services.v269_input_migration_service.uses_graph_contract",
        return_value=False,
    ), patch.object(
        core, "compile_agent3_provider_package", return_value=compiled
    ) as legacy, patch(
        "src.services.v269_input_migration_service.provider_messages"
    ) as provider:
        messages, payload = core._build_messages("dv", packages)
    legacy.assert_called_once_with(packages[0])
    provider.assert_not_called()
    assert payload["version"] == core.AGENT3_SOP_CORE_VERSION
    assert payload["packages"] == [compiled]
    assert messages[-1]["role"] == "user"
