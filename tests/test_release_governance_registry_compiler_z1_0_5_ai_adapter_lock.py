from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.registry_compiler.module_contracts import build_module_contracts
from tools.registry_compiler.post_codegen_gate import build_test_plan

ROOT = Path(__file__).resolve().parents[1]
TEST_PATH = "tests/test_release_governance_registry_compiler_z1_0_5_ai_adapter_lock.py"
RELEASE_COMMIT = "c924e646673a41857cca5e39650e022f9ce8c0a5"
RELEASE_HASH = "sha256:1f0aacabc028b777d47513f81538525b37fda5ff840e25660b45e38285e2dabd"
MIGRATION_SOURCE_COMMIT = "f1574c8825ddd57bde33ef9b5694b8318f29ef6c"
Z_CORE_REGISTRY_ROOT = "sha256:21078bf8228c3ca3a4cb755015023001d962c9062d0a48e8a92e99f8fdb48360"
LTX_PATH = "contracts/level-transactions/LTX-Z1.0.5-AI-ECOMMERCE-ADAPTER-LOCK.json"
SOURCE_RECEIPTS = {
    "migration/Z1.0.5_SOURCE_MANIFEST.json",
    "migration/Z1.0.5_PROTOCOL_MIGRATION_RECEIPT.json",
    "migration/Z1.0.5_COMPILER_BOOTSTRAP_RECEIPT.json",
}


def _read(relative: str) -> dict:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_exact_release_adapter_and_install_lock_verify_without_side_effects() -> None:
    completed = subprocess.run(
        [sys.executable, ".z/tools/verify_lock.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    receipt = json.loads(completed.stdout)
    assert receipt["verified"] is True
    assert receipt["findings"] == []
    assert receipt["releaseSourceCommit"] == RELEASE_COMMIT
    assert receipt["releaseHash"] == RELEASE_HASH
    assert receipt["databaseMutated"] is False
    assert receipt["providerCallsExecuted"] == 0
    assert receipt["ecsMutated"] is False

    install = _read(".z/install-manifest.json")
    lock = _read(".z/z.lock.json")
    adapter = _read(".z/adapter/adapter-manifest.json")
    assert install["activationState"] == "LOCKED_NOT_INSTALLED"
    assert install["mode"] == "REFERENCE_ONLY_NO_RUNTIME_SWITCH"
    assert lock["resolutionMode"] == "EXACT_COMMIT_AND_HASH"
    assert lock["mutableReferencesAllowed"] is False
    assert lock["latestAliasAllowed"] is False
    assert lock["release"]["sourceCommit"] == RELEASE_COMMIT
    assert lock["release"]["releaseHash"] == RELEASE_HASH
    assert adapter["requiredRelease"]["sourceCommit"] == RELEASE_COMMIT
    assert adapter["requiredRelease"]["releaseHash"] == RELEASE_HASH

    source_manifest = _read("migration/Z1.0.5_SOURCE_MANIFEST.json")
    assert source_manifest["schema"] == "z.migration_source_manifest.v1"
    assert source_manifest["migrationLevel"] == "L5"
    assert source_manifest["state"] == "PROTOCOLS_MIGRATED_RUNTIME_NOT_ACTIVATED"
    assert source_manifest["source"]["commit"] == MIGRATION_SOURCE_COMMIT
    assert source_manifest["source"]["zVersion"] == "Z1.0.5"
    assert source_manifest["target"]["repository"] == "yu5-520/Z-Century"
    assert source_manifest["invariants"]["runtimeActivatedInTarget"] is False
    assert source_manifest["invariants"]["ecsModified"] is False
    assert source_manifest["step4Acceptance"]["requiredExactFileCount"] == 20
    assert source_manifest["step4Acceptance"]["copyMode"] == "BYTE_IDENTICAL_GIT_BLOB"

    protocol_receipt = _read("migration/Z1.0.5_PROTOCOL_MIGRATION_RECEIPT.json")
    assert protocol_receipt["schema"] == "z.protocol_migration_receipt.v1"
    assert protocol_receipt["status"] == "PASS"
    assert protocol_receipt["source"]["commit"] == MIGRATION_SOURCE_COMMIT
    assert protocol_receipt["target"]["repository"] == "yu5-520/Z-Century"
    assert protocol_receipt["verification"]["exactFilesCopied"] == 20
    assert protocol_receipt["verification"]["gitBlobHashesMatched"] == 20
    assert protocol_receipt["verification"]["gitBlobHashesMismatched"] == 0
    assert protocol_receipt["verification"]["targetRuntimeEntryInstalled"] is False
    assert protocol_receipt["verification"]["ecsModified"] is False

    compiler_receipt = _read("migration/Z1.0.5_COMPILER_BOOTSTRAP_RECEIPT.json")
    assert compiler_receipt["schema"] == "z.compiler_bootstrap_receipt.v1"
    assert compiler_receipt["version"] == "Z1.0.5"
    assert compiler_receipt["status"] == "PASS"
    assert compiler_receipt["source"]["commit"] == MIGRATION_SOURCE_COMMIT
    assert compiler_receipt["target"]["repository"] == "yu5-520/Z-Century"
    assert compiler_receipt["hashes"]["registryRootHash"] == Z_CORE_REGISTRY_ROOT
    assert compiler_receipt["verification"]["workflowConclusion"] == "success"
    assert compiler_receipt["verification"]["findings"] == []
    assert compiler_receipt["verification"]["databaseMutated"] is False
    assert compiler_receipt["verification"]["providerCallsExecuted"] == 0
    assert compiler_receipt["safety"]["ecsModified"] is False
    assert compiler_receipt["safety"]["aiEcommerceRuntimeReplaced"] is False


def test_l5_adapter_transaction_is_release_governance_owned_and_fail_closed() -> None:
    transaction = _read(LTX_PATH)
    assert transaction["declaredLevel"] == "L5"
    assert transaction["rootTransition"] == "NONE"
    assert transaction["externalRoots"] == [
        f"repository:yu5-520/Z-Century@release/z1.0.5-bootstrap-source#{RELEASE_COMMIT}"
    ]
    assert set(transaction["actualWrites"]).issubset(transaction["allowedWritePaths"])
    assert set(transaction["requiredWrites"]).issubset(transaction["actualWrites"])
    assert SOURCE_RECEIPTS.issubset(transaction["actualWrites"])
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
    assert LTX_PATH in release_paths
    assert release_paths.issuperset(
        {
            *SOURCE_RECEIPTS,
            ".z/adapter/adapter-manifest.json",
            ".z/install-manifest.json",
            ".z/z.lock.json",
            ".z/tools/verify_lock.py",
            ".github/workflows/z-adapter-lock.yml",
        }
    )


def test_post_codegen_uses_this_changed_test_for_both_direct_modules() -> None:
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
