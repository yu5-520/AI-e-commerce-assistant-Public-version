from __future__ import annotations

from pathlib import Path

import pytest


def _projection() -> dict:
    return {
        "version": "21.7.8",
        "actionFamily": "title_image_test",
        "frozenAtTaskCreation": True,
        "frozenAt": "2026-07-30T10:24:10",
        "sourceDataVersion": "DV-20260730",
        "productId": "P10001",
        "storeId": "DY-SH-003",
        "referencedMetricCodes": ["clickRate", "conversionRate", "paymentAmount"],
        "metricDefinitions": [
            {
                "code": "clickRate",
                "label": "点击率",
                "group": "流量与承接",
                "kind": "percent",
                "evidenceRole": "primary_signal",
                "taskUsage": "确认标题主图是否需要提高点击承接",
                "internalDefinitionId": "SECRET",
            }
        ],
        "recentSnapshots": [
            {
                "businessDate": "2026-07-29",
                "dataVersion": "DV-1",
                "snapshotId": "SNAP-1",
                "sourceDataVersions": ["DV-1"],
                "metrics": {"clickRate": 0.031, "conversionRate": 0.082, "paymentAmount": 1180.0},
                "changes": {"clickRate": None},
                "rawItem": {"private": "SECRET"},
            },
            {
                "businessDate": "2026-07-30",
                "dataVersion": "DV-2",
                "snapshotId": "SNAP-2",
                "sourceDataVersions": ["DV-2"],
                "metrics": {"clickRate": 0.026, "conversionRate": 0.079, "paymentAmount": 1060.0},
                "changes": {"clickRate": -0.1613},
            },
        ],
        "metricTrends": {"clickRate": {"previousDelta": -0.1613, "slope5": -0.004}},
        "historicalEvidenceReferenced": False,
        "referenceWindow": {
            "snapshotCount": 2,
            "startBusinessDate": "2026-07-29",
            "endBusinessDate": "2026-07-30",
            "dataCompleteness": 1.0,
            "internalWindowKey": "SECRET",
        },
        "sourceObservationIds": ["SNAP-1", "SNAP-2"],
        "source": "task_creation_frozen_product_observations",
        "readRule": "Only frozen evidence is returned.",
        "ready": True,
        "evidenceStatus": "ready",
        "taskExecutableFromEvidence": True,
        "reason": None,
        "providerRequest": {"secret": True},
    }


def _snapshot() -> dict:
    projection = _projection()
    return {
        "version": "21.7.8",
        "ready": True,
        "taskId": "LT-V23212-001",
        "dataVersion": "DV-20260730",
        "title": "轻薄防晒外套标题主图测试",
        "taskStatus": "待审批",
        "productIdentity": {
            "productId": "P10001",
            "productTitle": "轻薄防晒外套",
            "storeId": "DY-SH-003",
            "storeName": "抖音官方店",
            "platform": "抖音",
        },
        "operatorJudgmentView": {
            "summary": "点击率连续下降，需要验证标题主图承接。",
            "coreProblem": "点击承接下降",
            "facts": ["点击率 3.1%→2.6%"],
        },
        "activeActionContract": {"actionFamily": "title_image_test", "target": "P10001"},
        "operatorExecutionSop": ["制作两组标题主图方案", "保持流量条件一致进行测试"],
        "taskDetailReport": {
            "taskMetricEvidenceProjection": projection,
            "taskMetricEvidenceProjectionVersion": "21.7.8",
            "taskEvidenceStatus": "ready",
            "taskEvidenceExecutable": True,
            "evidenceExecutionBlocked": False,
            "evidenceRequirements": ["提交两组测试截图"],
            "providerProof": {"secret": True},
        },
        "detailDisplayContract": {
            "version": "21.7.8",
            "readMode": "materialized_snapshot",
            "taskEvidenceFrozen": True,
            "taskEvidenceStatus": "ready",
            "taskEvidenceRequiredForExecution": True,
            "emptyDynamicMetricChangesMeansBaseline": False,
            "taskEvidenceRule": "正式任务只展示创建时冻结证据。",
            "internalContractField": "SECRET",
        },
        "authorizationDecision": {
            "decision": "manager_review_required",
            "approvalRequired": True,
            "requiredAuthorityLevel": "manager",
        },
        "metricDigest": {"clickRate": {"current": 0.026, "previous": 0.031}},
        "taskLifecycle": {"stage": "pending_review", "stageLabel": "待审批"},
        "systemFacts": {"secret": True},
        "artifactRefs": {"taskMappingRef": "ART-SECRET"},
    }


@pytest.mark.parametrize(
    "placement",
    ["root", "report", "root_plan", "related", "related_plan"],
)
def test_public_detail_selects_frozen_projection_from_all_canonical_paths(placement: str) -> None:
    from src.services.public_task_dto_service import project_task_detail

    snapshot = _snapshot()
    projection = snapshot["taskDetailReport"].pop("taskMetricEvidenceProjection")
    snapshot["taskDetailReport"].pop("taskMetricEvidenceProjectionVersion")

    if placement == "root":
        snapshot["taskMetricEvidenceProjection"] = projection
    elif placement == "report":
        snapshot["taskDetailReport"]["taskMetricEvidenceProjection"] = projection
    elif placement == "root_plan":
        snapshot["taskPlan"] = {"taskMetricEvidenceProjection": projection}
    elif placement == "related":
        snapshot["relatedTask"] = {"taskMetricEvidenceProjection": projection}
    else:
        snapshot["relatedTask"] = {"taskPlan": {"taskMetricEvidenceProjection": projection}}

    detail = project_task_detail(snapshot)
    evidence = detail["taskMetricEvidenceProjection"]

    assert detail["version"] == "23.2.12"
    assert evidence["evidenceStatus"] == "ready"
    assert evidence["referenceWindow"]["snapshotCount"] == 2
    assert evidence["referencedMetricCodes"] == [
        "clickRate",
        "conversionRate",
        "paymentAmount",
    ]
    assert detail["taskEvidenceStatus"] == "ready"
    assert detail["taskEvidenceExecutable"] is True
    assert detail["evidenceExecutionBlocked"] is False
    assert detail["publicContract"]["evidenceRecomputedOnRead"] is False


def test_public_evidence_projection_is_operator_safe_and_complete() -> None:
    from src.services.public_task_dto_service import project_task_detail

    detail = project_task_detail(_snapshot())
    evidence = detail["taskMetricEvidenceProjection"]

    assert evidence["version"] == "21.7.8"
    assert evidence["sourceObservationIds"] == ["SNAP-1", "SNAP-2"]
    assert evidence["recentSnapshots"][1]["metrics"]["clickRate"] == 0.026
    assert evidence["metricDefinitions"][0]["taskUsage"]
    assert detail["detailDisplayContract"] == {
        "version": "21.7.8",
        "readMode": "materialized_snapshot",
        "taskEvidenceFrozen": True,
        "taskEvidenceStatus": "ready",
        "taskEvidenceRequiredForExecution": True,
        "emptyDynamicMetricChangesMeansBaseline": False,
        "taskEvidenceRule": "正式任务只展示创建时冻结证据。",
    }

    serialized = __import__("json").dumps(detail, ensure_ascii=False)
    for forbidden in (
        "rawItem",
        "internalDefinitionId",
        "internalWindowKey",
        "providerRequest",
        '"providerProof":',
        "internalContractField",
        "systemFacts",
        "artifactRefs",
        "taskDetailReport",
        "relatedTask",
        "agent2ActionPlan",
        "agent2ExecutionProof",
        "operationPlan",
    ):
        assert forbidden not in serialized


def test_task_detail_route_preserves_materialized_evidence_without_recompute(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import frontend_views

    snapshot = _snapshot()
    monkeypatch.setattr(frontend_views, "read_task_detail_snapshot", lambda task_id, data_version=None: snapshot)
    monkeypatch.setattr(frontend_views, "_overlay_current_task_status", lambda result, task_id: result)

    detail = frontend_views.task_detail_view("LT-V23212-001")

    assert detail["taskId"] == "LT-V23212-001"
    assert detail["taskMetricEvidenceProjection"]["referenceWindow"]["snapshotCount"] == 2
    assert detail["taskMetricEvidenceProjection"]["evidenceStatus"] == "ready"
    assert detail["taskEvidenceExecutable"] is True
    assert detail["evidenceExecutionBlocked"] is False

    source = Path("src/services/public_task_dto_service.py").read_text(encoding="utf-8")
    assert "build_task_metric_evidence_projection" not in source
    assert "system_product_snapshots_v14" not in source
