from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TX_PATH = "contracts/level-transactions/LTX-Z1.0.5-CROSS-REPOSITORY-ROOT-MIGRATION.json"


def _read(relative: str) -> dict:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _verifier_module():
    path = ROOT / ".z/tools/verify_root_migration.py"
    spec = importlib.util.spec_from_file_location("z_root_migration_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_l5_root_migration_receipt_is_exact_and_side_effect_free() -> None:
    result = _verifier_module().verify(check_receipt=True)

    assert result["verified"] is True
    assert result["findings"] == []
    assert result["coreHashEquivalent"] is True
    assert result["rootAuthorityTransferred"] is True
    assert result["runtimeSwitchAuthorized"] is False
    assert result["serverBindingAuthorized"] is False
    assert result["embeddedSourceDeletionAuthorized"] is False
    assert result["productRuntimeRemainsAuthoritative"] is True
    assert result["databaseMutated"] is False
    assert result["providerCallsExecuted"] == 0
    assert result["ecsMutated"] is False


def test_transaction_records_required_cross_repository_fields() -> None:
    transaction = _read(TX_PATH)
    for field in (
        "sourceRepository",
        "sourceCommit",
        "destinationRepository",
        "destinationCommit",
        "sourceCoreHash",
        "destinationCoreHash",
        "releaseHash",
        "adapterHash",
        "oldScopeHash",
        "newScopeHash",
        "externalRoots",
        "allowedReadPaths",
        "allowedWritePaths",
        "requiredTests",
        "rollbackCommit",
    ):
        assert field in transaction
    assert transaction["declaredLevel"] == "L5"
    assert transaction["sourceCoreHash"] == transaction["destinationCoreHash"]
    assert transaction["rootTransition"] == "EXTERNAL_Z_ROOT_AUTHORITY_ACTIVE"


def test_root_authority_changes_source_ownership_not_product_runtime() -> None:
    authority = _read(".z/root-authority.json")
    install = _read(".z/install-manifest.json")

    assert authority["state"] == "EXTERNAL_Z_AUTHORITY_ACTIVE"
    assert authority["authorityRepository"] == "yu5-520/Z-Century"
    assert authority["authorityIntegrationCommit"] == (
        "27ccdc0a9e39a212671906c25594fa91293a2250"
    )
    assert authority["productRuntimeAuthority"] == "AI_ECOMMERCE_PRODUCT_REPOSITORY"
    assert authority["runtimeSwitchAuthorized"] is False
    assert authority["serverBindingAuthorized"] is False
    assert authority["embeddedSourceDeletionAuthorized"] is False
    assert install["activationState"] == "LOCKED_NOT_INSTALLED"
    assert install["mode"] == "REFERENCE_ONLY_NO_RUNTIME_SWITCH"
    assert install["rootAuthorityState"] == "EXTERNAL_Z_AUTHORITY_ACTIVE"
