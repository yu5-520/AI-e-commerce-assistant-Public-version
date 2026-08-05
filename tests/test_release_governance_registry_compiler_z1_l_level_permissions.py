from __future__ import annotations

import json
from pathlib import Path

from tools.self_update.level_classifier import classify_level
from tools.self_update.level_gate import evaluate_level_gate
from tools.self_update.level_policy import LEVELS, load_level_policy
from tools.self_update.lineage_scope_compiler import compile_lineage_scope

ROOT = Path(__file__).resolve().parents[1]


def _runtime_report() -> dict:
    return {
        "verified": True,
        "lineageHash": "sha256:" + "1" * 64,
        "entries": [
            {
                "runtimeId": "runner:module_a",
                "kind": "runner",
                "ownerModule": "module_a",
                "entry": "src.services.module_a:run",
                "sourcePath": "src/services/module_a.py",
            },
            {
                "runtimeId": "runner:module_b",
                "kind": "runner",
                "ownerModule": "module_b",
                "entry": "src.services.module_b:run",
                "sourcePath": "src/services/module_b.py",
            },
            {
                "runtimeId": "server:example",
                "kind": "server",
                "entry": "src.api.main:app",
                "sourcePath": "src/api/main.py",
            },
        ],
    }


def _transaction(**overrides) -> dict:
    value = {
        "schema": "self_update.level_transaction.v1",
        "version": "Z1.0.5",
        "transactionId": "TEST-LTX",
        "declaredLevel": "L1",
        "changeTypes": ["IMPLEMENTATION_ONLY"],
        "changedFiles": ["src/services/module_a.py"],
        "affectedModules": ["module_a"],
        "lineageEdges": [],
        "requiredNodes": ["module:module_a"],
        "stopBoundaries": [],
        "allowedReadPaths": [],
        "allowedWritePaths": ["src/services/module_a.py"],
        "requiredWrites": ["src/services/module_a.py"],
        "actualWrites": ["src/services/module_a.py"],
        "providedTestTiers": ["unit", "module_contract"],
        "approvals": [],
        "rootTransition": "NONE",
        "revokedRuntimeNodes": [],
        "externalRoots": [],
    }
    value.update(overrides)
    return value


def test_level_policy_has_monotonic_l0_to_l5_definitions() -> None:
    policy = load_level_policy(ROOT)

    assert LEVELS == ("L0", "L1", "L2", "L3", "L4", "L5")
    assert [policy["levels"][level]["rank"] for level in LEVELS] == list(range(6))
    assert policy["levels"]["L0"]["fullActiveGraph"] is False
    assert policy["levels"]["L3"]["fullActiveGraph"] is False
    assert policy["levels"]["L4"]["fullActiveGraph"] is True
    assert policy["rules"]["levelMayEscalateOnly"] is True
    assert policy["rules"]["unrelatedNodesRetainPermission"] is True


def test_declared_l1_is_automatically_escalated_for_interface_schema_change() -> None:
    classification = classify_level(
        _transaction(
            changeTypes=["CHANGE_INTERFACE_SCHEMA"],
            changedFiles=["contracts/registry/interfaces.json"],
            affectedModules=["module_a"],
        ),
        ROOT,
    )

    assert classification["declaredLevel"] == "L1"
    assert classification["computedMinimumLevel"] == "L3"
    assert classification["effectiveLevel"] == "L3"
    assert classification["escalationReasons"]


def test_l1_scope_keeps_unrelated_runtime_nodes_excluded() -> None:
    transaction = _transaction()
    classification = classify_level(transaction, ROOT)
    scope = compile_lineage_scope(
        transaction,
        classification,
        ROOT,
        runtime_report=_runtime_report(),
    )

    assert scope["effectiveLevel"] == "L1"
    assert scope["fullActiveGraph"] is False
    assert "module:module_a" in scope["includedNodes"]
    assert "runtime:runner:module_b" in scope["excludedNodes"]
    assert "runtime:server:example" in scope["excludedNodes"]


def test_l4_scope_uses_complete_active_runtime_graph() -> None:
    transaction = _transaction(
        declaredLevel="L4",
        changeTypes=["CHANGE_RUNNER"],
        providedTestTiers=[
            "full_contract",
            "full_runtime_lineage",
            "full_regression",
            "deployment_identity",
        ],
        approvals=["owner:a", "release:b"],
        rootTransition="CANDIDATE_TO_ACTIVE",
    )
    classification = classify_level(transaction, ROOT)
    scope = compile_lineage_scope(
        transaction,
        classification,
        ROOT,
        runtime_report=_runtime_report(),
    )

    assert scope["effectiveLevel"] == "L4"
    assert scope["fullActiveGraph"] is True
    assert "runtime:runner:module_a" in scope["includedNodes"]
    assert "runtime:runner:module_b" in scope["includedNodes"]
    assert "runtime:server:example" in scope["includedNodes"]


def test_write_outside_l_scope_is_denied_and_requires_escalation() -> None:
    report = evaluate_level_gate(
        _transaction(
            actualWrites=[
                "src/services/module_a.py",
                "src/services/module_b.py",
            ]
        ),
        ROOT,
        runtime_report=_runtime_report(),
    )

    assert report["verified"] is False
    assert any(
        finding.startswith("ACCESS_DENIED:WRITE_OUTSIDE_L_SCOPE")
        for finding in report["findings"]
    )
    assert report["escalation"]["escalationRequired"] is True
    assert report["escalation"]["requiresNewScopeHash"] is True
    assert report["escalation"]["requiresReapproval"] is True


def test_z1_0_5_repository_transaction_passes_real_runtime_lineage() -> None:
    transaction = json.loads(
        (
            ROOT
            / "contracts"
            / "level-transactions"
            / "LTX-Z1.0.5-l-level-permissions.json"
        ).read_text(encoding="utf-8")
    )
    report = evaluate_level_gate(transaction, ROOT)

    assert report["verified"] is True, report["findings"]
    assert report["classification"]["computedMinimumLevel"] == "L3"
    assert report["classification"]["effectiveLevel"] == "L3"
    assert report["lineageScope"]["fullActiveGraph"] is False
    assert report["lineageScope"]["scopeHash"].startswith("sha256:")
    assert report["databaseMutated"] is False
    assert report["providerCallsExecuted"] == 0
