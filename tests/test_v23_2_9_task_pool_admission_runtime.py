from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.services.agent_runtime_contract_v225_service import build_task_mapping_decision
from src.services.pipeline_task_mapping_v225_service import (
    TASK_ADMISSION_FAILED_STAGE,
    TASK_ADMITTED_STAGE,
    _compile_current_task_mapping_decision,
    _current_task_mapping_missing,
    _task_mapping_artifact_input,
    run_task_pool_admission_microbatch_v225,
)

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "src/services/pipeline_task_mapping_v225_service.py"
SEALED_PATHS = [
    "src/services/agent_runtime_contract_v225_service.py",
    "src/services/lifecycle_task_v183_service.py",
    "src/services/pipeline_artifact_contract_service.py",
    "src/services/task_pool_admission_core_v20_service.py",
]


def _package() -> dict:
    return {
        "dataVersion": "DV-TEST",
        "packageId": "PKG-P10001",
        "itemId": "PI-P10001",
        "productId": "P10001",
        "storeId": "DY-SH-003",
        "lockedActionFamily": "title_image_test",
        "productIdentity": {
            "productId": "P10001",
            "storeId": "DY-SH-003",
            "productTitle": "轻薄防晒衣女夏季户外通勤",
        },
        "agent1OperatingJudgment": {
            "decisionType": "act",
            "businessHypothesis": "点击率与转化率同步下降，需要进行标题主图测试。",
        },
        "agent2ActionDraft": {
            "packageId": "PKG-P10001",
            "productId": "P10001",
            "storeId": "DY-SH-003",
            "actionFamily": "title_image_test",
            "draftStatus": "draft_ready",
            "differentiationReason": "分别测试通勤轻薄和户外高倍防晒。",
            "agent2DraftExecutionProof": {"verified": True},
        },
        "agent3Sop": {
            "packageId": "PKG-P10001",
            "productId": "P10001",
            "storeId": "DY-SH-003",
            "actionFamily": "title_image_test",
            "sopStatus": "sop_ready",
            "finalTaskTitle": "6小时内完成P10001标题主图双方向测试上线",
            "executionObjective": "恢复点击率并验证两种创意方向的真实转化差异。",
            "executionObject": {"targetId": "P10001"},
            "operatorActionSteps": ["整理素材", "建立双版本", "记录上线证据"],
            "submissionEvidence": ["上线前截图", "版本编号"],
            "reviewMetrics": ["clickRate", "conversionRate"],
            "verificationPeriod": "6小时",
            "stopConditions": ["点击率继续显著下降"],
            "rollbackConditions": ["恢复原标题主图"],
            "companyStyleReason": "保持品牌轻薄通勤定位。",
        },
        "agent3Provider": {
            "providerStatus": "ok",
            "providerCallExecuted": True,
            "actualCalls": 1,
            "itemProvenance": {
                "PKG-P10001": {
                    "itemExecutionId": "A3-EXEC-1",
                    "inputContentHash": "sha256:test",
                }
            },
        },
        "agent3ExecutionProof": {
            "verified": True,
            "providerCallExecuted": True,
            "itemExecutionId": "A3-EXEC-1",
            "inputContentHash": "sha256:test",
        },
    }


def _compiled_decision() -> dict:
    package = _package()
    return _compile_current_task_mapping_decision(
        package,
        build_task_mapping_decision(package, pipeline_item_id="PI-P10001"),
    )


def test_shared_adapter_projects_system_title_reason_and_package() -> None:
    decision = _compiled_decision()
    plan = decision["taskPlan"]
    assert plan["title"] == _package()["agent3Sop"]["finalTaskTitle"]
    assert plan["taskTitle"] == plan["title"]
    assert plan["finalTaskTitle"] == plan["title"]
    assert plan["admissionReason"] == _package()["agent3Sop"]["executionObjective"]
    assert plan["reason"] == plan["admissionReason"]
    assert decision["admissionReason"] == plan["admissionReason"]
    assert decision["productJudgmentPackage"]["agent1OperatingJudgment"]
    assert _current_task_mapping_missing(decision) == []


def test_current_contract_rejects_missing_reason() -> None:
    decision = _compiled_decision()
    decision["admissionReason"] = ""
    decision["reason"] = ""
    decision["taskPlan"]["admissionReason"] = ""
    decision["taskPlan"]["reason"] = ""
    assert "taskPlan.admissionReason" in _current_task_mapping_missing(decision)


def test_task_mapping_ref_is_only_pool_business_input(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = _compiled_decision()
    artifact = {"taskMappingDecision": decision, "outputContract": "V23.2.9.task_mapped"}
    item = {
        "item_id": "PI-P10001",
        "artifact_refs_json": json.dumps({"taskMappingRef": "ART-MAPPING-1"}),
        "payload": json.dumps({"taskMappingDecision": {"decisionId": "STALE"}}),
    }
    monkeypatch.setattr(
        "src.services.pipeline_task_mapping_v225_service.resolve_artifact",
        lambda ref: artifact,
    )
    payload, resolved, ref = _task_mapping_artifact_input(item)
    assert ref == "ART-MAPPING-1"
    assert resolved["decisionId"] == decision["decisionId"]
    assert payload["taskMappingDecision"]["decisionId"] != "STALE"


def _run_pool(monkeypatch: pytest.MonkeyPatch, admission_result: dict) -> tuple[dict, list[dict]]:
    decision = _compiled_decision()
    item = {
        "item_id": "PI-P10001",
        "data_version": "DV-TEST",
        "product_id": "P10001",
        "store_id": "DY-SH-003",
        "package_id": "PKG-P10001",
        "decision_id": decision["decisionId"],
        "action_family": "title_image_test",
        "current_stage": "task_mapped",
        "status": "queued",
        "priority": 50,
        "artifact_refs_json": json.dumps({"taskMappingRef": "ART-MAPPING-1"}),
    }
    captured: list[dict] = []
    monkeypatch.setattr(
        "src.services.pipeline_task_mapping_v225_service._pending_items",
        lambda *args, **kwargs: [item],
    )
    monkeypatch.setattr(
        "src.services.pipeline_task_mapping_v225_service._task_mapping_artifact_input",
        lambda value: ({"taskMappingDecision": decision}, decision, "ART-MAPPING-1"),
    )
    monkeypatch.setattr(
        "src.services.pipeline_task_mapping_v225_service.admit_decision_to_task_pool",
        lambda *args, **kwargs: admission_result,
    )
    monkeypatch.setattr(
        "src.services.pipeline_task_mapping_v225_service._finish_item",
        lambda item, **kwargs: captured.append(kwargs) or kwargs,
    )
    monkeypatch.setattr(
        "src.services.pipeline_task_mapping_v225_service.sync_task_pool_entries_to_task_status",
        lambda **kwargs: {},
    )
    monkeypatch.setattr(
        "src.services.pipeline_task_mapping_v225_service.refresh_task_pool_views",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "src.services.pipeline_task_mapping_v225_service.pipeline_item_summary",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "src.services.pipeline_task_mapping_v225_service.pending_task_pool_item_count",
        lambda *args, **kwargs: 0,
    )
    return run_task_pool_admission_microbatch_v225("DV-TEST"), captured


def test_entered_without_task_id_is_explicit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    result, captured = _run_pool(
        monkeypatch,
        {
            "ok": True,
            "status": "entered_task_pool",
            "createdTaskCount": 1,
            "decisionId": _compiled_decision()["decisionId"],
            "taskId": None,
        },
    )
    assert result["createdTaskCount"] == 0
    assert result["failedItemCount"] == 1
    assert captured[0]["stage"] == TASK_ADMISSION_FAILED_STAGE
    assert captured[0]["status"] == "failed"
    assert captured[0]["task_id"] is None
    assert captured[0]["ref_key"] == "taskAdmissionFailureRef"


def test_real_task_id_is_written_before_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    result, captured = _run_pool(
        monkeypatch,
        {
            "ok": True,
            "status": "entered_task_pool",
            "createdTaskCount": 1,
            "decisionId": _compiled_decision()["decisionId"],
            "taskId": "LT-TEST-001",
        },
    )
    assert result["createdTaskCount"] == 1
    assert result["failedItemCount"] == 0
    assert captured[0]["stage"] == TASK_ADMITTED_STAGE
    assert captured[0]["status"] == "completed"
    assert captured[0]["task_id"] == "LT-TEST-001"
    assert captured[0]["ref_key"] == "taskAdmissionRef"


def test_sealed_legacy_services_are_unchanged_from_main() -> None:
    for path in SEALED_PATHS:
        working = subprocess.check_output(["git", "hash-object", path], cwd=ROOT, text=True).strip()
        main_bytes = subprocess.check_output(["git", "show", f"origin/main:{path}"], cwd=ROOT)
        main_hash = subprocess.check_output(["git", "hash-object", "--stdin"], cwd=ROOT, input=main_bytes).decode().strip()
        assert working == main_hash, path
    source = PIPELINE.read_text(encoding="utf-8")
    assert "taskMappingRef_only" in source
    assert "TASK_ADMISSION_FAILED_STAGE" in source
