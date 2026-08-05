from __future__ import annotations

import src  # noqa: F401
from fastapi.testclient import TestClient

from src.api.main import WEB_DEMO_DIR, app
from src.runtime_version import runtime_versions
from src.services.station_contract_service import station_contract
from src.services.station_registry_service import registry_summary
from src.services.task_detail_snapshot_v2024_service import build_task_detail_snapshot


def _task() -> dict:
    evidence = {
        "version": "21.7.8",
        "ready": True,
        "evidenceStatus": "ready",
        "taskExecutableFromEvidence": True,
        "frozenAtTaskCreation": True,
        "actionFamily": "roas_scale",
        "referencedMetricCodes": ["paymentAmount", "roi"],
        "metricDefinitions": [
            {"code": "paymentAmount", "label": "支付金额", "group": "经营结果", "kind": "money", "evidenceRole": "primary_signal", "taskUsage": "验证销售增长"},
            {"code": "roi", "label": "ROI", "group": "经营结果", "kind": "number", "evidenceRole": "risk_boundary", "taskUsage": "判断投放效率"},
        ],
        "recentSnapshots": [
            {"businessDate": "2026-06-25", "dataVersion": "DV-1", "snapshotId": "SNAP-1", "metrics": {"paymentAmount": 1000, "roi": 2.9}, "changes": {"paymentAmount": None, "roi": None}},
            {"businessDate": "2026-06-28", "dataVersion": "DV-V2177-DETAIL", "snapshotId": "SNAP-2", "metrics": {"paymentAmount": 1100, "roi": 2.8}, "changes": {"paymentAmount": 0.1, "roi": -0.0345}},
        ],
        "referenceWindow": {"snapshotCount": 2, "startBusinessDate": "2026-06-25", "endBusinessDate": "2026-06-28", "dataCompleteness": 1.0},
    }
    return {
        "taskId": "LT-V2177-DETAIL",
        "dataVersion": "DV-V2177-DETAIL",
        "title": "ROAS执行任务",
        "status": "待接收",
        "productIdentity": {"productId": "P-V2177", "storeId": "S-V2177", "productTitle": "通勤背包"},
        "agent2ActionPlan": {
            "packageId": "PKG-V2177",
            "actionFamily": "roas_scale",
            "actionPlanStatus": "ready",
            "operationPlan": {
                "version": "21.4.0",
                "schema": "operation_plan_ir.v1",
                "operations": [{
                    "operationType": "budget_update",
                    "target": {"type": "ad_plan", "id": "PLAN-1"},
                    "direction": "increase",
                    "currentValue": {"budget": 1000},
                    "targetValue": {"budget": 1200},
                    "adjustmentAmount": 200,
                }],
            },
            "budgetPlan": {"currentBudget": 1000, "targetBudget": 1200},
            "creativeTestPlan": {"groups": [{"fullTitle": "不应进入ROAS任务的标题方案"}]},
            "operatorActionSteps": ["确认计划", "修改预算", "设置止损", "记录结果"],
            "executionSteps": [{"step": 1}, {"step": 2}, {"step": 3}],
            "decisionBranches": [{"branch": "达标"}, {"branch": "未达标"}],
            "submissionEvidence": [{"type": "截图"}, {"type": "数据"}],
            "agent2ExecutionProof": {
                "semanticCallId": "CALL-V2177",
                "providerRequestId": "REQ-V2177",
                "providerCallExecuted": True,
                "exactReplayValidated": False,
                "itemCorrelationId": "PKG-V2177",
                "resultMatched": True,
                "fallbackUsed": False,
            },
        },
        "metricDigest": {
            "version": "21.7.7",
            "actionFamily": "roas_scale",
            "current": {"currentBudget": 1000, "currentROI": 2.8},
            "fullMetricEvidenceExcluded": True,
        },
        "taskMetricEvidenceProjection": evidence,
        "authorizationDecision": {"version": "21.4.0", "decision": "auto_execute", "approvalRequired": False},
        "operatorExecutionSop": ["确认计划", "修改预算", "设置止损", "记录结果"],
    }


def test_task_detail_uses_one_canonical_action_contract() -> None:
    snapshot = build_task_detail_snapshot(_task())
    assert snapshot["version"] == "21.7.8"
    assert snapshot["singleActionContractVersion"] == "21.7.7"
    assert snapshot["metricDigestVersion"] == "21.7.7"
    assert snapshot["activeActionContractVersion"] == "21.7.7"
    assert snapshot["taskMetricEvidenceProjectionVersion"] == "21.7.8"
    assert snapshot["taskEvidenceExecutable"] is True
    assert snapshot["taskMetricEvidenceProjection"]["referencedMetricCodes"] == ["paymentAmount", "roi"]
    assert snapshot["agent2PlanRef"] == "agent2_plan:PKG-V2177"
    assert snapshot["activeActionContract"]["activeActionFamily"] == "roas_scale"
    assert snapshot["activeActionContract"]["activeFamilyPlan"]["targetBudget"] == 1200
    assert snapshot["activeActionContract"]["activeOperationPlan"]["operations"][0]["adjustmentAmount"] == 200
    assert snapshot["activeActionContract"]["activeAuthority"]["decision"] == "auto_execute"
    assert snapshot["operationPlan"]["operations"][0]["targetValue"]["budget"] == 1200
    assert snapshot["agent2ExecutionProof"]["providerRequestId"] == "REQ-V2177"
    assert snapshot["agent2ActionPlan"]["creativeTestPlan"] is None
    assert "creativeTestPlan" in snapshot["discardedCrossFamilyFields"]
    assert "agent2ActionPlan" not in snapshot["taskDetailReport"]["taskPlan"]
    assert snapshot["detailDisplayContract"]["canonicalField"] == "activeActionContract"
    assert snapshot["detailDisplayContract"]["crossFamilyPlansAllowed"] is False
    assert snapshot["detailDisplayContract"]["emptyDynamicMetricChangesMeansBaseline"] is False


def test_station_interfaces_publish_v2177_recommended_fields() -> None:
    agent2 = station_contract("action_plan_judgment_agent_station")
    sop = station_contract("task_mapping_agent_station")
    pool = station_contract("task_pool_admission_station")
    assert agent2["version"] == "21.6.1"
    assert agent2["singleActionInterfaceVersion"] == "21.7.7"
    assert "singleActionContractVersion" in agent2["output"]["recommended"]
    assert "discardedCrossFamilyFieldCount" in agent2["output"]["recommended"]
    assert "activeActionContract" in sop["output"]["recommended"]
    assert "agent2PlanRef" in sop["output"]["recommended"]
    assert "activeOperationPlan" in pool["output"]["recommended"]
    assert "activeAuthority" in pool["output"]["recommended"]
    summary = registry_summary()
    assert summary["version"] == "21.6.1"
    assert summary["mainlinePurity"] == "v21_6_1_single_agent_entry_owner"
    assert summary["singleActionInterfaceVersion"] == "21.7.7"
    assert summary["singleActionContract"]["version"] == "21.7.7"
    assert summary["singleActionContract"]["taskDetailSource"] == "materialized_activeActionContract"


def test_runtime_versions_are_layered_not_pinned() -> None:
    versions = runtime_versions()
    assert versions["api"] == "21.6.2"
    assert versions["product"] == "21.8.0"
    assert versions["taskDetailProjection"] == "21.7.8"
    assert versions["taskMetricEvidenceProjection"] == "21.7.8"
    assert versions["dashboardExperience"] == "21.8.0"
    assert versions["neuralOperatingUI"] == "21.8.0"
    assert versions["neuralOperatingReadModel"] == "21.8.0"
    assert versions["operatorGrowthProjection"] == "21.8.0"
    assert versions["stationInterface"] == "21.6.1"
    assert versions["singleActionInterface"] == "21.7.7"
    assert versions["interfaceDocumentation"] == "21.8.0"


def test_frontend_static_assets_are_mounted_and_served() -> None:
    assert WEB_DEMO_DIR.is_dir()
    client = TestClient(app)
    expected_assets = {
        "/": "text/html",
        "/web_demo/styles.css?v=21.8.0": "text/css",
        "/web_demo/neural-operating-ui.css?v=21.8.0": "text/css",
        "/web_demo/dashboard-v2180.css?v=21.8.0": "text/css",
        "/web_demo/task-evidence-v2178.css?v=21.8.0": "text/css",
        "/web_demo/bootstrap.js?v=21.8.0": "javascript",
        "/web_demo/core/api-client.js?v=21.8.0": "javascript",
        "/web_demo/core/router.js?v=21.8.0": "javascript",
        "/web_demo/core/metric-snapshot-table.js?v=21.8.0": "javascript",
        "/web_demo/core/neural-operating-ui.js?v=21.8.0": "javascript",
        "/web_demo/modules/dashboard/page.js?v=21.8.0": "javascript",
    }
    for path, expected_type in expected_assets.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert len(response.content) > 0, path
        assert expected_type in response.headers.get("content-type", ""), path
    version = client.get("/api/version")
    assert version.status_code == 200
    payload = version.json()
    assert payload["frontendStaticMounted"] is True
    assert payload["frontendStaticPath"] == "/web_demo"
    assert payload["runtimeVersions"]["dashboardExperience"] == "21.8.0"
    assert payload["runtimeVersions"]["neuralOperatingUI"] == "21.8.0"
