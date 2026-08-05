from __future__ import annotations

from pathlib import Path

from tools.registry_compiler import module_contracts
from tools.self_update import impact_bundle


def test_module_contract_snapshot_is_reused_from_persisted_cache(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    cache_directory = tmp_path / "cache"

    documents = {
        "fields.json": {
            "fields": [
                {
                    "fieldId": "entity.example_id",
                    "ownerModule": "example_runtime",
                }
            ]
        },
        "schemas.json": {
            "schemas": [
                {
                    "schemaId": "example.input.v1",
                    "requiredFields": ["entity.example_id"],
                    "optionalFields": [],
                }
            ]
        },
        "modules.json": {
            "modules": [
                {
                    "moduleId": "example_runtime",
                    "runner": "src.services.example:run",
                    "reads": ["entity.example_id"],
                    "writes": [],
                    "inputSchemas": ["example.input.v1"],
                    "outputSchemas": [],
                }
            ]
        },
    }

    monkeypatch.setenv("SELF_UPDATE_CACHE_DIR", str(cache_directory))
    monkeypatch.setattr(module_contracts, "_git_revision", lambda _repository: "abc123")
    monkeypatch.setattr(
        module_contracts,
        "_file_hash",
        lambda path: f"sha256:{path.name}",
    )
    monkeypatch.setattr(
        module_contracts,
        "load_registry_documents",
        lambda _repository: documents,
    )
    monkeypatch.setattr(
        module_contracts,
        "_registry_root_hash",
        lambda _repository: "sha256:registry-root",
    )
    monkeypatch.setattr(
        module_contracts,
        "_implementation_hashes",
        lambda _repository, _module_id, _runner_path: {},
    )

    module_contracts.clear_module_contract_cache()
    first = module_contracts.build_module_contracts(repository)
    assert first["cacheIdentity"]
    assert first["moduleContracts"]["example_runtime"]["moduleContractHash"]

    module_contracts.clear_module_contract_cache()
    monkeypatch.setattr(
        module_contracts,
        "load_registry_documents",
        lambda _repository: (_ for _ in ()).throw(
            AssertionError("persisted cache was not reused")
        ),
    )
    second = module_contracts.build_module_contracts(repository)

    assert second == first


def test_impact_bundle_passes_one_contract_snapshot_to_active_resolution(
    tmp_path: Path, monkeypatch
) -> None:
    contracts = {
        "registryRootHash": "sha256:registry-root",
        "moduleContractRootHash": "sha256:module-root",
        "cacheIdentity": "cache-identity",
        "moduleContracts": {
            "example_runtime": {
                "runnerPath": "src/services/example.py",
                "implementationContentHashes": {
                    "src/services/example.py": "sha256:example"
                },
            }
        },
    }
    captured = {}

    monkeypatch.setattr(
        impact_bundle,
        "resolve_requirement",
        lambda _requirement, _repository: {
            "requirementId": "REQ-Z0.9-TEST",
            "requirementIrHash": "sha256:req",
            "impactHash": "sha256:impact",
            "registryRootHash": "sha256:registry-root",
            "directModules": ["example_runtime"],
            "transitiveModules": [],
            "findings": [],
            "productImpact": [],
            "state": "WAITING_FOR_USER_APPROVAL",
        },
    )
    monkeypatch.setattr(
        impact_bundle,
        "build_module_contracts",
        lambda _repository: contracts,
    )

    def resolve_active(module_ids, repository, *, contracts_snapshot=None):
        captured["module_ids"] = list(module_ids)
        captured["repository"] = repository
        captured["snapshot"] = contracts_snapshot
        return {
            "resolved": True,
            "findings": [],
            "activeCallChain": ["src.services.example"],
            "moduleContractSnapshotReused": contracts_snapshot is not None,
        }

    monkeypatch.setattr(impact_bundle, "resolve_active_modules", resolve_active)
    monkeypatch.setattr(impact_bundle, "_test_patterns", lambda _module_id: [])

    bundle = impact_bundle.build_impact_bundle({}, tmp_path)

    assert captured["snapshot"] is contracts
    assert captured["module_ids"] == ["example_runtime"]
    assert bundle["moduleContractSnapshot"]["reusedByActiveResolver"] is True
    assert bundle["moduleContractSnapshot"]["cacheIdentity"] == "cache-identity"


def test_requirement_workflow_uses_changed_only_selection() -> None:
    workflow = Path(".github/workflows/v23.1-requirement-self-update.yml").read_text(
        encoding="utf-8"
    )

    assert "Resolve changed Requirement IR files only" in workflow
    assert "git diff --name-only --diff-filter=AM" in workflow
    assert "SELF_UPDATE_TRANSACTION_ID: Z090-" in workflow
    assert "Restore Z0.9 self-update intermediate cache" in workflow
    assert "files=(contracts/requirements/REQ-*.json)" not in workflow
    assert "files=(contracts/approvals/*.json)" not in workflow
    assert "selecting zero Requirement files" in workflow
    assert "selecting zero approval files" in workflow


def test_post_codegen_workflow_reuses_intermediates_and_is_targeted_first() -> None:
    workflow = Path(".github/workflows/v23.1-post-codegen-pr-gate.yml").read_text(
        encoding="utf-8"
    )

    assert "Restore Z0.9 verification intermediates" in workflow
    assert "Run targeted-first fail-closed post-codegen verification" in workflow
    assert "SELF_UPDATE_TRANSACTION_ID: Z093-" in workflow
