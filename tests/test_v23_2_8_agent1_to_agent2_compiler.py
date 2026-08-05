from __future__ import annotations

import ast
import json
from pathlib import Path

from src.services.agent2_action_draft_core_v225_service import (
    AGENT2_FAMILY_PAYLOAD_SCHEMA,
    AGENT2_GENERATION_COMPILER_VERSION,
    DRAFT_CONFLICT,
    DRAFT_MISSING_DATA,
    DRAFT_READY,
    DRAFT_REJECTED,
    _build_messages,
    _normalize_draft,
    missing_agent2_draft_contract,
    repairable_agent2_contract_missing,
)
from src.services.agent2_hash_proof_bridge_v22515_service import (
    build_agent2_contract_repair_envelope,
    build_agent2_generation_envelope,
)
from src.services.agent2_runtime_v22515_service import _contract_failure_reason
from src.services.agent_input_contract_v225_service import (
    AGENT2_DRAFT_INPUT_SCHEMA,
    build_projection_envelope,
    validate_agent_input_envelope,
)

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src/services/agent2_action_draft_core_v225_service.py"
RUNTIME = ROOT / "src/services/agent2_runtime_v22515_service.py"
BRIDGE = ROOT / "src/services/agent2_hash_proof_bridge_v22515_service.py"


def _lock() -> dict:
    return {
        "decisionType": "act",
        "evidenceStatus": "sufficient",
        "selectedOperatingRoute": "creative_recovery",
        "selectedActionFamily": "title_image_test",
        "primaryProblemNode": "click_rate_decline",
        "primaryAction": "replace_title_and_main_image",
        "primaryExecutionTarget": {
            "targetType": "product_creative_asset",
            "targetId": "P10001",
            "owner": "operator",
        },
        "primaryOwner": "operator",
        "decisiveFacts": [
            {"metric": "clickRate", "changeRate": -0.068},
            {"metric": "conversionRate", "changeRate": -0.084},
        ],
        "supportingCoordination": ["设计提供现有主图源文件"],
        "forbiddenActionDomains": ["roas_scale"],
    }


def _package() -> dict:
    return {
        "packageId": "PKG-P10001",
        "itemId": "PI-22249C7685E0368B",
        "dataVersion": "DV-20260727135223-a7a76f",
        "productId": "P10001",
        "storeId": "DY-SH-003",
        "productTitle": "轻薄防晒衣女夏季户外通勤",
        "productIdentity": {
            "productId": "P10001",
            "storeId": "DY-SH-003",
            "platform": "douyin",
            "category": "防晒衣",
        },
        "executionLock": _lock(),
        "lockedActionFamily": "title_image_test",
        "recentFiveOrLatestFacts": [
            {"metric": "clickRate", "current": 0.031, "changeRate": -0.068},
            {"metric": "conversionRate", "current": 0.021, "changeRate": -0.084},
        ],
        "actionParameterPack": {
            "permissionBounds": {"operatorCanExecute": True},
            "parameterBounds": {"directionCount": {"min": 2, "max": 5}},
            "validationMetrics": ["clickRate", "conversionRate"],
            "riskBoundaries": ["一次只测试一个创意方向"],
            "requiredEvidence": ["新旧标题主图截图", "测试后指标"],
        },
        "verticalActionRag": {
            "matchedCount": 0,
            "approvedCaseIds": [],
            "agentInstruction": "保持商品定位，不复用历史标题。",
        },
    }


def _direction(index: int) -> dict:
    return {
        "fullTitle": f"轻薄防晒衣女夏季户外通勤测试标题{index}",
        "mainImageStructure": {
            "scene": "通勤户外场景",
            "layout": "左侧人物右侧卖点",
            "visualFocus": "轻薄防晒",
        },
        "testFocusWords": ["轻薄", "防晒", f"方向{index}"],
        "platformFit": "适配抖音信息流首屏阅读",
        "differenceFromOthers": f"方向{index}突出不同使用场景",
    }


def _valid_raw() -> dict:
    return {
        "packageId": "PKG-P10001",
        "draftStatus": "draft_ready",
        "primaryAction": "mutated_action_must_be_ignored",
        "primaryOwner": "mutated_owner_must_be_ignored",
        "familyPayload": {
            "directions": [_direction(1), _direction(2)],
        },
    }


def _input_payload() -> dict:
    package = _package()
    lock = package.pop("executionLock")
    package["agent1OperatingJudgment"] = {
        "decisionType": "act",
        "decisionSummary": "点击率与转化率同步下降，进入标题主图测试。",
        "executionLock": lock,
    }
    package["matrixDispatch"] = {
        "selectedActionFamily": "title_image_test",
        "selectedPrimaryAction": "replace_title_and_main_image",
        "selectedExecutionTarget": lock["primaryExecutionTarget"],
        "selectedOwner": "operator",
        "dispatchStatus": "locked",
    }
    package["inputContract"] = {
        "schema": AGENT2_DRAFT_INPUT_SCHEMA,
        "version": "22.5.0",
        "fallbackAllowed": False,
    }
    return package


def test_provider_payload_is_compiled_to_family_specific_slot() -> None:
    messages, payload = _build_messages("DV-1", [_package()])

    assert payload["version"] == AGENT2_GENERATION_COMPILER_VERSION
    assert payload["schema"] == AGENT2_FAMILY_PAYLOAD_SCHEMA
    compiled = payload["packages"][0]
    assert set(compiled) == {
        "packageId",
        "productId",
        "storeId",
        "immutableContext",
        "actionContext",
        "familyContract",
    }
    assert compiled["immutableContext"]["primaryAction"] == "replace_title_and_main_image"
    assert compiled["familyContract"]["contractId"] == "title_image_test.v1"
    assert compiled["familyContract"]["minDirections"] == 2
    assert "draftStatus" not in compiled
    assert "permissionBoundary" not in compiled

    system_prompt = messages[0]["content"]
    assert "只生成familyPayload" in system_prompt
    assert "禁止返回draftStatus" in system_prompt
    assert "系统会自动注入" in system_prompt
    assert "每个结果必须包含packageId,productId" not in system_prompt


def test_system_assembles_locks_and_computes_ready_status() -> None:
    draft = _normalize_draft(_valid_raw(), _package())

    assert draft["draftStatus"] == DRAFT_READY
    assert draft["systemComputedDraftStatus"] is True
    assert draft["modelDeclaredDraftStatus"] == DRAFT_READY
    assert draft["primaryAction"] == "replace_title_and_main_image"
    assert draft["primaryOwner"] == "operator"
    assert draft["primaryExecutionTarget"]["targetId"] == "P10001"
    assert draft["executionTargets"] == [draft["primaryExecutionTarget"]]
    assert draft["creativeDraft"]["directionCount"] == 2
    assert draft["familyPayload"] == draft["creativeDraft"]
    assert draft["permissionBoundary"] == {"operatorCanExecute": True}
    assert missing_agent2_draft_contract(draft) == []


def test_model_declared_ready_without_family_payload_is_rejected_precisely() -> None:
    draft = _normalize_draft(
        {
            "packageId": "PKG-P10001",
            "draftStatus": "draft_ready",
        },
        _package(),
    )
    missing = missing_agent2_draft_contract(draft)

    assert draft["draftStatus"] == ""
    assert draft["modelDeclaredDraftStatus"] == DRAFT_READY
    assert "agent2_output_channel_missing" in missing
    assert "agent2_title_image_creative_draft_missing" in missing
    assert repairable_agent2_contract_missing(missing) is True
    assert _contract_failure_reason(missing) == "agent2_title_image_creative_draft_missing"


def test_title_image_contract_reports_exact_missing_fields() -> None:
    draft = _normalize_draft(
        {
            "packageId": "PKG-P10001",
            "familyPayload": {
                "directions": [
                    {
                        "fullTitle": "只有标题",
                        "mainImageStructure": {},
                        "testFocusWords": [],
                    }
                ]
            },
        },
        _package(),
    )
    missing = missing_agent2_draft_contract(draft)

    assert "agent2_title_image_main_image_structure_missing" in missing
    assert "agent2_title_image_test_focus_words_missing" in missing
    assert "agent2_title_image_platform_fit_missing" in missing
    assert "agent2_title_image_difference_missing" in missing
    assert "agent2_title_image_directions_insufficient" in missing
    assert repairable_agent2_contract_missing(missing) is True


def test_system_computes_business_hold_statuses_without_family_payload() -> None:
    missing_data = _normalize_draft(
        {"packageId": "PKG-P10001", "missingData": ["缺少当前主图源文件"]},
        _package(),
    )
    conflict = _normalize_draft(
        {"packageId": "PKG-P10001", "conflictReasons": ["执行锁与平台规则冲突"]},
        _package(),
    )
    rejected = _normalize_draft(
        {"packageId": "PKG-P10001", "rejectedReason": "当前动作违反平台规则"},
        _package(),
    )

    assert missing_data["draftStatus"] == DRAFT_MISSING_DATA
    assert conflict["draftStatus"] == DRAFT_CONFLICT
    assert rejected["draftStatus"] == DRAFT_REJECTED
    assert missing_agent2_draft_contract(missing_data) == []
    assert missing_agent2_draft_contract(conflict) == []
    assert missing_agent2_draft_contract(rejected) == []


def test_conflicting_output_channels_are_not_accepted() -> None:
    draft = _normalize_draft(
        {
            "packageId": "PKG-P10001",
            "familyPayload": {"directions": [_direction(1), _direction(2)]},
            "missingData": ["同时声称缺数据"],
        },
        _package(),
    )

    assert draft["draftStatus"] == ""
    assert draft["outcomeChannelConflict"] is True
    assert missing_agent2_draft_contract(draft) == ["agent2_outcome_channel_conflict"]
    assert repairable_agent2_contract_missing(missing_agent2_draft_contract(draft)) is False



def test_generation_compiler_versions_execution_identity_without_timestamp(monkeypatch) -> None:
    stored = []

    def fake_store_artifact(**kwargs):
        stored.append(kwargs)
        return {"artifactId": "ART-COMPILED-1", "contentHash": "sha256:compiled"}

    monkeypatch.setattr(
        "src.services.agent2_hash_proof_bridge_v22515_service.store_artifact",
        fake_store_artifact,
    )
    envelope = build_projection_envelope(
        schema=AGENT2_DRAFT_INPUT_SCHEMA,
        payload=_input_payload(),
        source_artifact_refs=["ART-CANONICAL-1"],
        source_content_hash="sha256:source",
    )
    first = build_agent2_generation_envelope(
        envelope,
        canonical_input_ref="ART-CANONICAL-1",
    )
    second = build_agent2_generation_envelope(
        envelope,
        canonical_input_ref="ART-CANONICAL-1",
    )

    compiler = first["envelope"]["payload"]["diagnosticExtensions"]["agent2GenerationCompiler"]
    assert compiler["version"] == AGENT2_GENERATION_COMPILER_VERSION
    assert compiler["schema"] == AGENT2_FAMILY_PAYLOAD_SCHEMA
    assert compiler["systemComputedDraftStatus"] is True
    assert first["semanticInputHash"] == second["semanticInputHash"]
    assert first["envelope"] == second["envelope"]
    assert first["semanticInputHash"] != envelope["projectedContentHash"]
    assert "createdAt" not in compiler
    assert stored[0]["parent_refs"] == ["ART-CANONICAL-1"]
    assert validate_agent_input_envelope(
        first["envelope"],
        expected_schema=AGENT2_DRAFT_INPUT_SCHEMA,
    )["ok"] is True

def test_repair_envelope_keeps_canonical_input_and_adds_one_scoped_attempt(monkeypatch) -> None:
    stored = {}

    def fake_store_artifact(**kwargs):
        stored.update(kwargs)
        return {"artifactId": "ART-REPAIR-1", "contentHash": "sha256:repair"}

    monkeypatch.setattr(
        "src.services.agent2_hash_proof_bridge_v22515_service.store_artifact",
        fake_store_artifact,
    )
    envelope = build_projection_envelope(
        schema=AGENT2_DRAFT_INPUT_SCHEMA,
        payload=_input_payload(),
        source_artifact_refs=["ART-CANONICAL-1"],
        source_content_hash="sha256:source",
    )
    repair = build_agent2_contract_repair_envelope(
        envelope,
        canonical_input_ref="ART-CANONICAL-1",
        source_execution_hash="EXEC-OLD",
        previous_output={
            "modelDeclaredDraftStatus": DRAFT_READY,
            "familyPayload": {},
            "outcomeChannel": None,
        },
        missing=[
            "agent2_output_channel_missing",
            "agent2_title_image_creative_draft_missing",
        ],
    )

    repair_payload = repair["envelope"]["payload"]
    context = repair_payload["diagnosticExtensions"]["agent2ContractRepair"]
    assert context["attemptNo"] == 1
    assert context["repairScope"] == "family_payload_only"
    assert context["agent1Rerun"] is False
    assert context["actionPackRerun"] is False
    assert repair["runtimeInputArtifactRef"] == "ART-REPAIR-1"
    assert repair["runtimeAttemptId"].startswith("A2REPAIR-")
    assert repair["envelope"]["payload"]["agent1OperatingJudgment"] == envelope["payload"]["agent1OperatingJudgment"]
    assert validate_agent_input_envelope(
        repair["envelope"],
        expected_schema=AGENT2_DRAFT_INPUT_SCHEMA,
    )["ok"] is True
    assert stored["parent_refs"] == ["ART-CANONICAL-1"]


def test_runtime_source_seals_single_bounded_repair() -> None:
    for path in (CORE, RUNTIME, BRIDGE):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "contract_repair_count += 1" in runtime
    assert "max_items_per_call=1" in runtime
    assert "repairable_agent2_contract_missing" in runtime
    assert "build_agent2_contract_repair_envelope" in runtime
    assert "build_agent2_generation_envelope" in runtime
    assert "generation_compilation_by_package" in runtime
    assert '"compiledGenerationInputCount": len(generation_compilation_by_package)' in runtime
    assert '"contractRepairAttemptCount": contract_repair_count' in runtime
    repair_start = runtime.index("and repairable_agent2_contract_missing(direct_missing)")
    repair_end = runtime.index("should_finalize = (", repair_start)
    repair_block = runtime[repair_start:repair_end]
    assert "while" not in repair_block
    assert repair_block.count("run_agent2_draft_projected_inputs(") == 1

    core = CORE.read_text(encoding="utf-8")
    assert "禁止返回draftStatus" in core
    assert "systemComputedDraftStatus" in core
    assert "agent2_title_image_creative_draft_missing" in core
