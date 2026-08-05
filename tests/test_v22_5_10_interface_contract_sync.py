from __future__ import annotations

from pathlib import Path

from src import runtime_version as rv
from src.services.frontend_view_artifact_v2259_service import stable_view_payload
from src.services.hash_directed_artifact_runtime_v2259_service import hash_value
from src.services.llm_input_projection_v2259_service import semantic_item_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_layered_runtime_versions_are_explicit_and_not_collapsed() -> None:
    versions = rv.runtime_versions()
    assert rv.VERSION == "22.4.0"
    assert rv.DEPLOYMENT_SINGLE_AUTHORITY_VERSION == "22.5.4"
    assert rv.THREE_AGENT_PIPELINE_VERSION == "22.5.5"
    assert rv.AGENT1_INPUT_SEMANTIC_VERSION == "22.5.8"
    assert rv.AGENT1_INPUT_SCHEMA_VERSION == "agent_input.agent1.v3"
    assert rv.HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION == "22.5.9"
    assert rv.INTERFACE_DOCUMENTATION_VERSION == "22.5.10"
    assert rv.CANONICAL_INTERFACE_DOCUMENT == (
        "docs/V22.5.9_INTERFACE_AND_MIGRATION.md"
    )
    assert versions["api"] == "22.4.0"
    assert versions["stateMachineVersion"] == "22.5.5"
    assert versions["agent1InputSchema"] == "agent_input.agent1.v3"
    assert versions["hashDirectedArtifactRuntime"] == "22.5.9"
    assert versions["interfaceDocumentation"] == "22.5.10"
    contracts = versions["hashDirectedContracts"]
    assert contracts["cachedBusinessOutputRebindingAllowed"] is False
    assert contracts["legacyItemCacheOwnsBusinessReplay"] is False
    assert contracts["onlyTrueMissingItemsRetry"] is True


def test_readme_and_version_point_to_one_canonical_interface() -> None:
    readme = _read("README.md")
    version = _read("VERSION.md")
    canonical = rv.CANONICAL_INTERFACE_DOCUMENT
    assert canonical in readme
    assert canonical in version
    for text in (readme, version):
        assert "22.4.0" in text
        assert "22.5.5" in text
        assert "22.5.8" in text
        assert "22.5.9" in text
        assert "22.5.10" in text
        assert "agent_input.agent1.v3" in text
        assert (
            "itemExecutionId + inputContentHash" in text
            or "itemExecutionId+inputContentHash" in text
        )
    assert "Old business output identity rebinding is forbidden" in version
    assert "no Agent business-result ownership" in version


def test_canonical_interface_documents_current_routes_and_contracts() -> None:
    document = _read(rv.CANONICAL_INTERFACE_DOCUMENT)
    required = [
        "artifact_execution_index_v2259",
        "agent_batch_manifest.v2259",
        "frontend_view.manifest.v2259",
        "agent_input.agent1.v3",
        "itemExecutionId + inputContentHash",
        "GET /api/view/head/{view_key}",
        "GET /api/view/artifacts/{artifact_ref}",
        "POST /api/view/refresh",
        "crossDataVersionFallbackAllowed",
        "cachedOutputRebindingAllowed = false",
    ]
    for value in required:
        assert value in document


def test_historical_documents_are_marked_and_link_current_contract() -> None:
    historical = [
        "docs/V22.5.0_INTERFACE_AND_MIGRATION.md",
        "docs/V22.5.0_THREE_AGENT_SEMANTIC_PIPELINE.md",
        "docs/V22.5.8_AGENT1_EVIDENCE_OUTPUT_CONTRACT.md",
        "docs/V22.5.8_DEPLOYMENT_AND_RECOVERY.md",
    ]
    for path in historical:
        text = _read(path)
        assert "Document status:" in text
        assert rv.CANONICAL_INTERFACE_DOCUMENT in text
        assert "22.5.10" in text


def test_api_self_description_uses_current_execution_identity() -> None:
    main = _read("src/api/main.py")
    system = _read("src/api/routes/system.py")
    for text in (main, system):
        assert "interfaceDocumentationVersion" in text
        assert "canonicalInterfaceDocument" in text
        assert "hashDirectedArtifactRuntimeVersion" in text
        assert "agent1InputSchema" in text
        assert "executionIndex" in text
        assert "batchManifestContract" in text
        assert "cachedOutputRebindingAllowed" in text
        assert "itemExecutionId+inputContentHash" in text
    identity = "artifactRefs.agent1InputRef.v3+inputContentHash+executionHash"
    assert identity in main
    assert identity in system
    assert "agent_runtime_hard_interface_v2255_service" in main
    assert "agent_runtime_hard_interface_v2255_service" in system


def test_semantic_fingerprint_retains_current_business_and_artifact_identity() -> None:
    item = {
        "dataVersion": "DV-001",
        "productId": "P001",
        "storeId": "S001",
        "signalId": "SIG-001",
        "inputArtifactRef": "ART-INPUT-1",
        "inputContentHash": "HASH-1",
        "updatedAt": "2026-07-26T12:00:00",
        "metricSnapshot": {"roi": 2.1},
    }
    same_business_new_timestamp = {**item, "updatedAt": "2026-07-26T13:00:00"}
    changed_version = {**item, "dataVersion": "DV-002"}
    changed_hash = {**item, "inputContentHash": "HASH-2"}
    first = semantic_item_fingerprint("product_judgment_agent", item)
    assert first == semantic_item_fingerprint(
        "product_judgment_agent", same_business_new_timestamp
    )
    assert first != semantic_item_fingerprint(
        "product_judgment_agent", changed_version
    )
    assert first != semantic_item_fingerprint(
        "product_judgment_agent", changed_hash
    )


def test_frontend_hash_ignores_transport_time_but_retains_data_version() -> None:
    first = stable_view_payload(
        {
            "dataVersion": "DV-001",
            "updatedAt": "2026-07-26T12:00:00",
            "items": [{"productId": "P001", "roi": 2.1, "cachedAt": "A"}],
        }
    )
    second = stable_view_payload(
        {
            "dataVersion": "DV-001",
            "updatedAt": "2026-07-26T13:00:00",
            "items": [{"productId": "P001", "roi": 2.1, "cachedAt": "B"}],
        }
    )
    changed = stable_view_payload(
        {
            "dataVersion": "DV-002",
            "updatedAt": "2026-07-26T13:00:00",
            "items": [{"productId": "P001", "roi": 2.1}],
        }
    )
    assert hash_value(first) == hash_value(second)
    assert hash_value(first) != hash_value(changed)


def test_duplicate_v2259_static_contract_test_is_removed_after_consolidation() -> None:
    assert not (
        ROOT / "tests/test_v22_5_9_hash_directed_artifact_runtime.py"
    ).exists()
    retained = ROOT / "tests/test_v22_5_9_hash_directed_runtime.py"
    assert retained.exists()
    source = retained.read_text(encoding="utf-8")
    assert "duplicate_or_extra_execution_identity_fails_closed" in source
    assert "pre_materialized_hash_input_disables_second_projection" in source


def test_release_policy_and_root_verifier_authority_are_not_rotated() -> None:
    policy = _read("release/release-policy.json")
    assert '"productVersion": "22.4.0"' in policy
    assert '"rootVerifierOrdinaryRotationAllowed": false' in policy
    assert '"release/release-policy.json"' in policy
