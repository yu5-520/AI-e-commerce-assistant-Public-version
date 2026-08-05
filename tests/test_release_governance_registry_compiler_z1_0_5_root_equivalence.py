from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tools.registry_compiler.module_contracts import build_module_contracts
from tools.registry_compiler.post_codegen_gate import build_test_plan

ROOT = Path(__file__).resolve().parents[1]
TEST_PATH = "tests/test_release_governance_registry_compiler_z1_0_5_root_equivalence.py"
LTX_PATH = "contracts/level-transactions/LTX-Z1.0.5-AI-ROOT-EQUIVALENCE.json"
EQUIVALENCE_HASH = "sha256:e1f4d679bd49ed2f2d266ac071256127e4c18ec07410ea47f90f786a43d4e1f7"
VERIFICATION_HASH = "sha256:5f3147fa99cba09feae411a04213b3bce86b60f3199e90c26366d4aaae20d2e5"


def _read(relative: str) -> dict:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _verifier_module():
    path = ROOT / ".z/tools/verify_equivalence.py"
    spec = importlib.util.spec_from_file_location("z_root_equivalence_verifier_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_layered_root_equivalence_receipt_is_exact_and_side_effect_free() -> None:
    result = _verifier_module().verify(check_receipt=True)

    assert result["verified"] is True
    assert result["findings"] == []
    assert result["equivalenceHash"] == EQUIVALENCE_HASH
    assert result["verificationHash"] == VERIFICATION_HASH
    assert result["rootRoles"] == {
        "activeRuntimeCompatibilityRootHash": (
            "sha256:c6308a05333fadc9467413cb7a68099d2e6958bceca0b265b764a4407b4eb0ac"
        ),
        "historicalGovernanceRootHash": (
            "sha256:ff37e43cd374986b1edf1ff735e97d6b19c9635efd2a0167e68f2943444dcdbd"
        ),
        "productRegistryRootHash": (
            "sha256:e1bbee3ef7b78805ed32a917f304c5585e2cca3195e59856e973a051e2a713b0"
        ),
        "zCoreRegistryRootHash": (
            "sha256:21078bf8228c3ca3a4cb755015023001d962c9062d0a48e8a92e99f8fdb48360"
        ),
    }
    assert len(set(result["rootRoles"].values())) == 4
    assert result["runtimeSwitchAuthorized"] is False
    assert result["serverBindingAuthorized"] is False
    assert result["embeddedSourceDeletionAuthorized"] is False
    assert result["productMainDeploymentAuthorized"] is False
    assert result["databaseMutated"] is False
    assert result["providerCallsExecuted"] == 0
    assert result["ecsMutated"] is False


def test_equivalence_contract_composes_roots_without_literal_substitution() -> None:
    contract = _read(".z/equivalence/root-equivalence.json")
    rules = contract["compositionRules"]
    install = _read(".z/install-manifest.json")
    product_registration = _read("contracts/runtime/active-runtime-registration.json")
    adapter_registration = _read(".z/adapter/runtime-registration.json")

    assert contract["equivalenceMode"] == "LAYERED_ROOT_COMPOSITION"
    assert rules["literalRootEqualityRequired"] is False
    assert rules["crossLayerRootSubstitutionAllowed"] is False
    assert rules["productRegistryRootRotated"] is False
    assert rules["productExtensionsRemainProductOwned"] is True
    assert rules["runtimeSwitchAuthorized"] is False
    assert rules["serverBindingAuthorized"] is False
    assert install["activationState"] == "LOCKED_NOT_INSTALLED"
    assert install["mode"] == "REFERENCE_ONLY_NO_RUNTIME_SWITCH"
    assert set(adapter_registration["activeModules"]) == set(
        product_registration["nonHttp"]["registeredActiveModules"]
    )
    assert "bootstrap_installer" not in adapter_registration["activeModules"]


def test_l5_transaction_and_release_governance_own_exact_equivalence_scope() -> None:
    transaction = _read(LTX_PATH)
    assert transaction["declaredLevel"] == "L5"
    assert transaction["rootTransition"] == "NONE"
    assert transaction["externalRoots"] == [
        "repository:yu5-520/Z-Century@release/z1.0.5-bootstrap-source#"
        "c924e646673a41857cca5e39650e022f9ce8c0a5"
    ]
    assert set(transaction["actualWrites"]).issubset(transaction["allowedWritePaths"])
    assert set(transaction["requiredWrites"]).issubset(transaction["actualWrites"])
    assert {
        "cross_root_contract",
        "migration",
        "full_regression",
        "rollback",
        "release_group",
    }.issubset(transaction["providedTestTiers"])

    contracts = build_module_contracts(ROOT, use_cache=False)
    release_paths = set(
        contracts["moduleContracts"]["release_governance"]
        ["implementationContentHashes"]
    )
    assert release_paths.issuperset(
        {
            ".z/equivalence/root-equivalence.json",
            ".z/tools/verify_equivalence.py",
            ".z/receipts/Z1.0.5_AI_ROOT_EQUIVALENCE.json",
            ".github/workflows/z-root-equivalence.yml",
            LTX_PATH,
        }
    )


def test_post_codegen_selects_one_changed_test_for_both_direct_modules() -> None:
    program = {
        "programHash": "sha256:" + "0" * 64,
        "codegenRequests": [
            {
                "requestId": "EDIT-001",
                "moduleId": "registry_compiler",
                "allowedTestPatterns": [
                    "tests/test_*registry*compiler*.py",
                    "tests/test_v23_1_*.py",
                    "tests/test_v23_2_*.py",
                    "tests/test_v24_*.py",
                ],
            },
            {
                "requestId": "EDIT-002",
                "moduleId": "release_governance",
                "allowedTestPatterns": [
                    "tests/test_*release*governance*.py",
                    "tests/test_v23_1_*.py",
                    "tests/test_v23_2_*.py",
                    "tests/test_v24_*.py",
                ],
            },
        ],
        "verificationRequests": [],
    }
    plan = build_test_plan(program, ROOT, changed_paths=[TEST_PATH])

    assert plan["ready"] is True
    assert plan["tests"] == [TEST_PATH]
    assert [item["selectionMode"] for item in plan["modules"]] == [
        "changed_targeted",
        "changed_targeted",
    ]
    assert all(item["matchedTests"] == [TEST_PATH] for item in plan["modules"])
