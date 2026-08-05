from __future__ import annotations

import pytest

from src.services import agent_runtime_hard_interface_v225_service as hard
from src.services.agent_input_contract_v225_service import AGENT2_MAX_ITEM_CHARS
from src.services.agent_input_transport_v225_service import (
    AGENT_INPUT_TRANSPORT_VERSION,
    AgentInputProjectionError,
    compile_agent2_draft_envelope,
)


def _contains_key(value: object, target: str) -> bool:
    if isinstance(value, dict):
        return target in value or any(_contains_key(item, target) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, target) for item in value)
    return False


def _repeated_source() -> dict:
    long_parts = {
        f"reasonPart{index}": (f"经营判断{index}-" + "转化承接证据" * 45)
        for index in range(10)
    }
    diagnostic = [
        {
            "metric": f"metric-{index}",
            "reason": "只保留一次的诊断扩展" * 20,
        }
        for index in range(5)
    ]
    decision_ir = {
        "decisionType": "act",
        "decisionSummary": "流量稳定但支付转化率连续下降",
        "selectedActionFamily": "conversion_repair",
        "diagnosticExtensions": diagnostic,
        **long_parts,
    }
    return {
        "dataVersion": "DV-1",
        "itemId": "PI-1",
        "packageId": "PKG-1",
        "productId": "P10008",
        "storeId": "STORE-1",
        "productTitle": "厨房多功能收纳架",
        "productIdentity": {
            "productId": "P10008",
            "storeId": "STORE-1",
            "productTitle": "厨房多功能收纳架",
            "platform": "京东",
            "verticalCategory": "家居收纳",
        },
        "decisionType": "act",
        "actionFamily": "conversion_repair",
        "lockedActionFamily": "conversion_repair",
        "agent1DecisionIR": decision_ir,
        "agent1OperatingJudgment": {
            "decisionType": "act",
            "primaryBusinessSignal": "支付转化率下降",
            "actionFamilyLock": {
                "selectedActionFamily": "conversion_repair",
                "lockedByAgent1": True,
            },
            "agent1DecisionIR": decision_ir,
            "diagnosticExtensions": diagnostic,
            "judgmentReason": "Agent1独有经营判断" * 80,
        },
        "matrixDispatch": {
            "selectedActionFamily": "conversion_repair",
            "lockedByAgent1": True,
        },
        "actionParameterPack": {
            "permissionBounds": {
                "operatorCanExecute": True,
                "managerApprovalRequired": False,
            },
            "executionObjects": [
                {"targetType": "product_page", "targetId": "P10008"}
            ],
            "numericBounds": {"testDays": [3, 5]},
        },
        "recentFiveOrLatestFacts": [
            {"metric": "conversionRate", "trend": "down", "value": 0.031}
        ],
        "ragContextSnapshot": {
            "positiveCases": [
                {"caseId": "CASE-1", "summary": "详情页首屏信任承接修复"}
            ]
        },
        "diagnosticExtensions": diagnostic,
    }


def test_agent2_transport_removes_repeated_agent1_handoff_without_raising_budget() -> None:
    envelope = compile_agent2_draft_envelope(
        _repeated_source(),
        source_ref="ART-TEST-CAPABILITY",
        source_content_hash="source-hash",
    )

    payload = envelope["payload"]
    audit = envelope["projectionAudit"]

    assert payload["lockedActionFamily"] == "conversion_repair"
    assert "actionFamily" not in payload
    assert "selectedActionFamily" not in payload
    assert "decisionType" not in payload
    assert "title" not in payload
    assert not _contains_key(payload["agent1OperatingJudgment"], "agent1DecisionIR")
    assert not _contains_key(payload["agent1OperatingJudgment"], "diagnosticExtensions")
    assert not _contains_key(payload["agent1DecisionIR"], "diagnosticExtensions")
    assert payload["diagnosticExtensions"]
    assert payload["inputContract"]["agent1FullArtifactAuditOnly"] is True
    assert payload["inputContract"]["transportDeduplicated"] is True
    assert audit["transportVersion"] == "22.5.1"
    assert audit["transportDeduplicated"] is True
    assert audit["projectedChars"] <= AGENT2_MAX_ITEM_CHARS
    assert audit["agent1Handoff"]["uniqueAgent1HandoffChars"] < AGENT2_MAX_ITEM_CHARS
    assert "fieldChars" in audit
    assert audit["largestField"]


def test_projection_budget_error_contains_field_level_diagnostics() -> None:
    source = _repeated_source()
    source["actionParameterPack"] = {
        f"parameter{index}": "参数边界" * 180
        for index in range(56)
    }

    with pytest.raises(AgentInputProjectionError) as captured:
        compile_agent2_draft_envelope(
            source,
            source_ref="ART-TEST-LARGE-CAPABILITY",
            source_content_hash="large-source-hash",
        )

    error = captured.value
    assert error.code == "agent2_draft_input_projection_budget_exceeded"
    assert "projection_item_budget_exceeded" in str(error)
    assert error.audit["projectedChars"] > AGENT2_MAX_ITEM_CHARS
    assert error.audit["overByChars"] > 0
    assert error.audit["budgetStatus"] == "exceeded"
    assert error.audit["largestField"] == "actionParameterPack"
    assert error.audit["fieldChars"]["actionParameterPack"]["marginalChars"] > 0


def test_runtime_status_exposes_context_dedup_and_input_failure_stage() -> None:
    status = hard.agent_runtime_hard_interface_status()

    assert AGENT_INPUT_TRANSPORT_VERSION == "22.5.1"
    assert status["contextDedupHotfixVersion"] == "22.5.1"
    assert status["agentInputTransportVersion"] == "22.5.1"
    assert status["agent2InputFailureStage"] == "agent2_draft_input_invalid"
    assert status["agent1HandoffDeduplicated"] is True
    assert status["agent1FullArtifactDownstreamReadAllowed"] is False
    assert status["fallbackAllowed"] is False


class _Cursor:
    def __init__(self, rows=None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, row: dict) -> None:
        self.row = row
        self.updated = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params=()):
        if sql.lstrip().startswith("SELECT"):
            return _Cursor([self.row])
        if "UPDATE pipeline_items" in sql:
            self.updated = True
            return _Cursor(rowcount=1)
        raise AssertionError(sql)

    def commit(self) -> None:
        return None


def test_migration_recovers_only_budget_failure_with_valid_action_pack(monkeypatch) -> None:
    row = {
        "item_id": "PI-7953A76F45B95FB8",
        "data_version": "DV-20260723073720-6f1f0d",
        "product_id": "P10008",
        "package_id": "PKG-A40E8C08463B3518",
        "current_stage": "action_pack_invalid",
        "status": "failed",
    }
    connections: list[_Connection] = []

    def fake_connect():
        connection = _Connection(row)
        connections.append(connection)
        return connection

    monkeypatch.setattr(hard, "connect", fake_connect)
    monkeypatch.setattr(
        hard,
        "payload_from_row",
        lambda value: {
            "missing": [
                "agent2DraftInputRef",
                "agent_input_contract_invalid:['projection_item_budget_exceeded']",
            ]
        },
    )
    monkeypatch.setattr(
        hard,
        "resolve_agent2_draft_source",
        lambda value: (
            "ART-CAPABILITY",
            "hash",
            {
                "packageId": value["package_id"],
                "productId": value["product_id"],
                "storeId": "STORE-1",
                "actionFamily": "conversion_repair",
            },
        ),
    )
    monkeypatch.setattr(hard, "missing_action_pack_contract", lambda value: [])

    result = hard.migrate_misclassified_agent2_input_failures(
        "DV-20260723073720-6f1f0d"
    )

    assert result["recoveredItemCount"] == 1
    assert result["recoveredItemIds"] == ["PI-7953A76F45B95FB8"]
    assert result["agent1Rerun"] is False
    assert result["observedItemsTouched"] is False
    assert any(connection.updated for connection in connections)
