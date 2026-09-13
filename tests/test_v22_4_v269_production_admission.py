"""V26.9.A production reservation and graph task-admission tests."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.repositories import sqlite_repository as repo
from src.services import task_pool_admission_core_v20_service as pool
from src.services import v269_production_admission_service as production
from src.services import v269_semantic_graph_service as g
from tests.test_v22_4_v269_semantic_graph import decision_raw, plan_node


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(repo, "LOG_DIR", tmp_path)
    monkeypatch.setattr(repo, "DB_PATH", tmp_path / "v269.sqlite3")
    monkeypatch.setattr(repo, "_WAL_INITIALIZED", False)
    production.ensure_graph_reservation_tables()
    return tmp_path


def graph_case(*, target_a=160, target_b=160, daily_limit=500):
    facts = {
        "fact:1": {"value": 0, "unit": "ratio"},
        "budget:a": {"value": 100, "unit": "CNY"},
        "budget:b": {"value": 100, "unit": "CNY"},
    }
    decision = g.compile_graph("DecisionGraph", decision_raw(), evidence_refs=list(facts))
    admission = g.admit_actions(decision, ["DA1", "DA2"])
    nodes = [plan_node("P1", "DA1"), plan_node("P2", "DA2")]
    for node, target, desired in zip(nodes, ["a", "b"], [target_a, target_b]):
        node["parameters"] = {
            "operationPlan": {
                "operations": [
                    {
                        "operationType": "budget_update",
                        "target": {"id": target, "type": "ad_plan"},
                        "currentValue": {"budget": 100},
                        "targetValue": {"budget": desired},
                        "currentValueRef": "budget:" + target,
                    }
                ]
            }
        }
    plan = g.compile_graph(
        "PlanGraph",
        {"nodes": nodes, "edges": [{"sourceRef": "P1", "targetRef": "P2", "relation": "depends_on"}]},
        upstream=decision,
        evidence_refs=list(facts),
        fact_values=facts,
    )
    operation = g.compile_graph(
        "OperationGraph",
        {
            "nodes": [
                {
                    "kind": "OperationStage",
                    "nodeKey": "O1",
                    "planActionRefs": ["P1", "P2"],
                    "instruction": "按冻结方案执行预算调整",
                    "owner": "运营",
                    "executionObject": "商品1",
                    "sequence": 0,
                    "rollback": "恢复原预算",
                    "stopConditionRefs": ["P1:stop"],
                    "acceptanceActions": ["核对执行记录"],
                }
            ]
        },
        upstream=plan,
    )
    policy = {
        "source": "existing_operator_action_authority",
        "enabled": True,
        "singleAdjustmentLimit": 500,
        "dailyAdjustmentLimit": daily_limit,
        "rolling24hLimit": daily_limit,
        "ownerApprovalLimit": 1000,
        "roasChangeRateLimit": 1,
        "minimumTargetRoas": 0,
        "usedToday": 0,
        "usedRolling24h": 0,
    }
    evaluation = g.evaluate_plan_authority(plan, facts, policy)
    assert evaluation["decision"] == "auto_execute"
    authorization = {
        "version": "26.9.0",
        "decision": "auto_execute",
        "approvalRequired": False,
        "operatorId": "competition_operator",
        "storeId": "store-1",
        "productId": "product-1",
        "evaluation": evaluation,
    }
    package = {
        "semanticContractVersion": "26.9.0",
        "dataVersion": "dv-1",
        "packageId": "pkg-1",
        "productId": "product-1",
        "storeId": "store-1",
        "DecisionGraph": decision,
        "actionAdmission": admission,
        "PlanGraph": plan,
        "OperationGraph": operation,
        "factValues": facts,
    }
    return package, authorization


def test_atomic_reservation_is_idempotent_and_commit_consumes_once(isolated_db):
    package, authorization = graph_case()
    first = production.reserve_plan_authority(package, authorization)
    second = production.reserve_plan_authority(package, authorization)
    assert first["reservationId"] == second["reservationId"]
    assert second["idempotentHit"] is True
    assert first["status"] == "reserved"

    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        committed = production._commit_reservation_in_conn(conn, first["reservationId"], "TASK-1")
        conn.commit()
    assert committed["status"] == "committed"
    assert committed["taskId"] == "TASK-1"

    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = production._commit_reservation_in_conn(conn, first["reservationId"], "TASK-1")
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) AS value FROM action_authority_usage WHERE task_id='TASK-1'"
        ).fetchone()["value"]
        total = conn.execute(
            "SELECT COALESCE(SUM(adjustment_amount),0) AS value FROM action_authority_usage WHERE task_id='TASK-1'"
        ).fetchone()["value"]
    assert replay["idempotentHit"] is True
    assert count == 2
    assert total == 120


def test_live_usage_is_rechecked_inside_reservation_transaction(isolated_db):
    package, authorization = graph_case(daily_limit=150)
    first = production.reserve_plan_authority(package, authorization)
    with repo.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        production._commit_reservation_in_conn(conn, first["reservationId"], "TASK-1")
        conn.commit()

    second_package, second_auth = graph_case(target_a=120, target_b=120, daily_limit=150)
    second_package["packageId"] = "pkg-2"
    # The pure evaluation was built from the earlier policy snapshot and is still
    # auto_execute; reservation must re-read committed usage and reject the race.
    assert second_auth["decision"] == "auto_execute"
    with pytest.raises(production.GraphReservationConflict, match="COMBINED_DAILY_LIMIT"):
        production.reserve_plan_authority(second_package, second_auth)


def test_release_does_not_write_usage(isolated_db):
    package, authorization = graph_case()
    reserved = production.reserve_plan_authority(package, authorization)
    result = production.release_reservation(reserved["reservationId"], reason="task_materialization_failed")
    assert result["released"] is True
    with repo.connect() as conn:
        row = conn.execute(
            "SELECT status FROM v269_graph_authority_reservations WHERE reservation_id=?",
            (reserved["reservationId"],),
        ).fetchone()
        usage = conn.execute("SELECT COUNT(*) AS value FROM action_authority_usage").fetchone()["value"]
    assert row["status"] == "released"
    assert usage == 0


def test_task_pool_dispatches_graph_contract_without_legacy_validation():
    decision = {"semanticContractVersion": "26.9.0"}
    expected = {"ok": True, "status": "graph_path"}
    with patch.object(production, "admit_graph_decision_to_task_pool", return_value=expected) as routed:
        assert pool.admit_decision_to_task_pool(decision, created_by="tester") == expected
    routed.assert_called_once_with(decision, created_by="tester", force_new_snapshot=False)
