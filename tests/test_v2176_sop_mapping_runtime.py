from __future__ import annotations

from typing import Any, Dict

from src.services import pipeline_sop_task_pool_v2010_service as pipeline


def _item() -> Dict[str, Any]:
    return {
        "data_version": "DV-2176",
        "item_id": "PI-2176",
        "package_id": "PKG-2176",
        "product_id": "P-2176",
        "store_id": "S-2176",
        "action_family": "roas_scale",
        "route": "paid_traffic_efficiency",
        "priority": 20,
    }


def test_successful_sop_mapping_reads_task_plan_family_without_name_error(
    monkeypatch,
) -> None:
    item = _item()
    package = {
        "packageId": "PKG-2176",
        "productId": "P-2176",
        "storeId": "S-2176",
        "actionFamily": "roas_scale",
    }
    decision = {
        "decisionId": "TGD-2176",
        "taskPlan": {
            "selectedActionFamily": "roas_scale",
        },
    }
    normalized = {
        **package,
        "decisionId": "TGD-2176",
        "sopDecision": decision,
    }
    finished: list[Dict[str, Any]] = []

    monkeypatch.setattr(
        pipeline,
        "_pending_items",
        lambda data_version, stage, limit: [item],
    )
    monkeypatch.setattr(pipeline, "payload_from_row", lambda row: dict(package))
    monkeypatch.setattr(pipeline, "missing_agent2_contract", lambda value: [])
    monkeypatch.setattr(
        pipeline,
        "build_sop_decision_from_package",
        lambda value, data_version, pipeline_item_id=None: dict(decision),
    )
    monkeypatch.setattr(pipeline, "save_sop_decision", lambda value: None)
    monkeypatch.setattr(
        pipeline,
        "normalize_sop_mapped_contract",
        lambda value, mapped: dict(normalized),
    )
    monkeypatch.setattr(
        pipeline,
        "_finish_item",
        lambda value, **kwargs: finished.append(kwargs) or {"ok": True},
    )
    monkeypatch.setattr(pipeline, "pending_sop_item_count", lambda value: 0)
    monkeypatch.setattr(
        pipeline,
        "pipeline_item_summary",
        lambda **kwargs: {"items": []},
    )

    result = pipeline.run_sop_mapping_microbatch_v206(
        "DV-2176",
        batch_size=1,
    )

    assert result["runtimeFixVersion"] == "21.7.6"
    assert result["claimedItemCount"] == 1
    assert result["taskDecisionCount"] == 1
    assert result["failedItemCount"] == 0
    assert result["bySelectedActionFamily"] == {"roas_scale": 1}
    assert len(finished) == 1
    assert finished[0]["stage"] == pipeline.SOP_MAPPED_STAGE
    assert finished[0]["status"] == "queued"
    assert finished[0]["decision_id"] == "TGD-2176"


def test_non_mapping_task_plan_falls_back_to_item_family(monkeypatch) -> None:
    item = _item()
    package = {
        "packageId": "PKG-2176",
        "productId": "P-2176",
        "storeId": "S-2176",
        "actionFamily": "roas_scale",
    }
    decision = {
        "decisionId": "TGD-2176",
        "taskPlan": None,
    }

    monkeypatch.setattr(
        pipeline,
        "_pending_items",
        lambda data_version, stage, limit: [item],
    )
    monkeypatch.setattr(pipeline, "payload_from_row", lambda row: dict(package))
    monkeypatch.setattr(pipeline, "missing_agent2_contract", lambda value: [])
    monkeypatch.setattr(
        pipeline,
        "build_sop_decision_from_package",
        lambda value, data_version, pipeline_item_id=None: dict(decision),
    )
    monkeypatch.setattr(pipeline, "save_sop_decision", lambda value: None)
    monkeypatch.setattr(
        pipeline,
        "normalize_sop_mapped_contract",
        lambda value, mapped: {
            **package,
            "decisionId": "TGD-2176",
            "sopDecision": mapped,
        },
    )
    monkeypatch.setattr(
        pipeline,
        "_finish_item",
        lambda value, **kwargs: {"ok": True},
    )
    monkeypatch.setattr(pipeline, "pending_sop_item_count", lambda value: 0)
    monkeypatch.setattr(
        pipeline,
        "pipeline_item_summary",
        lambda **kwargs: {"items": []},
    )

    result = pipeline.run_sop_mapping_microbatch_v206(
        "DV-2176",
        batch_size=1,
    )

    assert result["failedItemCount"] == 0
    assert result["bySelectedActionFamily"] == {"roas_scale": 1}


def test_task_pool_success_path_remains_reachable(monkeypatch) -> None:
    item = {
        **_item(),
        "decision_id": "TGD-2176",
    }
    decision = {
        "decisionId": "TGD-2176",
        "taskPlan": {
            "selectedActionFamily": "roas_scale",
        },
    }
    package = {
        "packageId": "PKG-2176",
        "productId": "P-2176",
        "storeId": "S-2176",
        "actionFamily": "roas_scale",
        "decisionId": "TGD-2176",
        "sopDecision": decision,
    }
    finished: list[Dict[str, Any]] = []

    monkeypatch.setattr(
        pipeline,
        "_pending_items",
        lambda data_version, stage, limit: [item],
    )
    monkeypatch.setattr(pipeline, "payload_from_row", lambda row: dict(package))
    monkeypatch.setattr(pipeline, "missing_sop_contract", lambda value: [])
    monkeypatch.setattr(
        pipeline,
        "admit_decision_to_task_pool",
        lambda value, created_by=None, force_new_snapshot=False: {
            "ok": True,
            "status": "entered_task_pool",
            "createdTaskCount": 1,
            "decisionId": "TGD-2176",
            "taskId": "TASK-2176",
        },
    )
    monkeypatch.setattr(
        pipeline,
        "normalize_task_admitted_contract",
        lambda value, admission: {
            **value,
            "taskAdmission": admission,
            "taskId": admission["taskId"],
        },
    )
    monkeypatch.setattr(
        pipeline,
        "_finish_item",
        lambda value, **kwargs: finished.append(kwargs) or {"ok": True},
    )
    monkeypatch.setattr(
        pipeline,
        "sync_task_pool_entries_to_task_status",
        lambda data_version=None: {"synced": 1},
    )
    monkeypatch.setattr(
        pipeline,
        "refresh_task_pool_views",
        lambda data_version=None: {"status": "ok"},
    )
    monkeypatch.setattr(
        pipeline,
        "pending_task_pool_item_count",
        lambda value: 0,
    )
    monkeypatch.setattr(
        pipeline,
        "pipeline_item_summary",
        lambda **kwargs: {"items": []},
    )

    result = pipeline.run_task_pool_admission_microbatch_v207(
        "DV-2176",
        batch_size=1,
    )

    assert result["runtimeFixVersion"] == "21.7.6"
    assert result["createdTaskCount"] == 1
    assert result["failedItemCount"] == 0
    assert len(finished) == 1
    assert finished[0]["stage"] == pipeline.TASK_ADMITTED_STAGE
    assert finished[0]["status"] == "completed"
    assert finished[0]["task_id"] == "TASK-2176"
