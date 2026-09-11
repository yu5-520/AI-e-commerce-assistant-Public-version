from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
SERVICES_ROOT = SRC_ROOT / "services"

# These are pure authority/compiler tests. Importing the package normally executes
# src/__init__.py and installs the full production runtime (FastAPI included), which
# is intentionally outside this lightweight gate. Provide package shells and load
# only the exact V26 modules under test, matching the isolated V26.1 test pattern.
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(SRC_ROOT)]
services_pkg = types.ModuleType("src.services")
services_pkg.__path__ = [str(SERVICES_ROOT)]
sys.modules.setdefault("src", src_pkg)
sys.modules.setdefault("src.services", services_pkg)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FIELD = _load_module(
    "src.services.v26_field_authority_contract_service",
    SERVICES_ROOT / "v26_field_authority_contract_service.py",
)
GRAPH = _load_module(
    "src.services.v26_business_graph_bridge_service",
    SERVICES_ROOT / "v26_business_graph_bridge_service.py",
)

ACTION_GRAPH_SCHEMA = GRAPH.ACTION_GRAPH_SCHEMA
JUDGEMENT_GRAPH_SCHEMA = GRAPH.JUDGEMENT_GRAPH_SCHEMA
OPERATION_GRAPH_SCHEMA = GRAPH.OPERATION_GRAPH_SCHEMA
compile_action_graph = GRAPH.compile_action_graph
compile_judgement_graph = GRAPH.compile_judgement_graph
compile_operation_graph = GRAPH.compile_operation_graph
FieldAuthorityViolation = FIELD.FieldAuthorityViolation
V26FieldAuthorityContract = FIELD.V26FieldAuthorityContract


def test_judgement_graph_preserves_semantic_freedom_and_is_deterministic() -> None:
    normalized = {
        "itemExecutionId": "A1-1",
        "inputContentHash": "sha256:input",
        "productId": "P1",
        "storeId": "S1",
        "confidence": 0.83,
        "finding": "主图点击上升，但转化没有同步改善，不应为了完整性继续扩展到无关退款指标。",
        "selectedActionFamilyHint": "title_image_test",
        "selectedOperatingRoute": "creative_validation",
        "agent1DecisionIR": {
            "coreProblem": "点击提升未转化为成交提升",
            "decisionSummary": "保留强信号，缩小到素材与承接一致性验证。",
            "causalHypotheses": ["素材承诺与详情页承接不一致"],
            "rejectedHypotheses": ["库存不是当前主因"],
            "facts": [{"metric": "CTR", "delta": "+18%"}],
        },
    }
    raw = {
        "decisionSummary": "允许自由业务语言，不做关键词模板审查。",
        "signalConflicts": ["CTR上涨 / CVR未上涨"],
        "ignoredSignals": ["refund_rate：当前任务弱相关"],
        "recommendedDirection": "验证主图承诺与落地承接",
        "priorityWeight": 0.78,
    }
    first = compile_judgement_graph(normalized, raw)
    second = compile_judgement_graph(deepcopy(normalized), deepcopy(raw))
    assert first["schema"] == JUDGEMENT_GRAPH_SCHEMA
    assert first["graphHash"] == second["graphHash"]
    assert first["legacyBusinessFieldsAuthoritative"] is False
    assert first["authorityHeaders"]["judgement.priority_weight"] == 0.78
    assert "refund_rate" in first["authorityHeaders"]["judgement.ignored_signals"][0]
    assert all(key.startswith("judgement.") for key in first["authorityHeaders"])


def test_action_graph_keeps_plan_authority_on_agent2() -> None:
    normalized = {
        "itemExecutionId": "A2-1",
        "inputContentHash": "sha256:a2",
        "productId": "P1",
        "storeId": "S1",
        "actionIntent": "扩大有效流量但守住效率边界",
        "validationMetrics": ["ROAS", "CVR"],
        "riskBoundaries": ["ROAS低于下界时停止扩量"],
        "familyPayload": {
            "strategySummary": "先小幅扩量，再依据归因窗口逐步放大。",
            "candidateStrategies": [
                {"id": "A", "summary": "稳健扩量"},
                {"id": "B", "summary": "快速扩量"},
            ],
            "selectedStrategy": {"id": "A", "reason": "当前波动更低"},
            "dailyBudget": 800,
            "targetRoas": 3.4,
            "reviewWindow": "72h",
            "expectedTrend": {"roas": "stable_or_up"},
            "minimumEvidence": {"orders": 20},
        },
    }
    graph = compile_action_graph(normalized, normalized)
    assert graph["schema"] == ACTION_GRAPH_SCHEMA
    headers = graph["authorityHeaders"]
    assert headers["plan.daily_budget"] == 800
    assert headers["plan.target_roas"] == 3.4
    assert headers["plan.selected_strategy"]["id"] == "A"
    assert all(key.startswith("plan.") for key in headers)

    authority = V26FieldAuthorityContract()
    with pytest.raises(FieldAuthorityViolation, match="v26_field_write_forbidden"):
        authority.assert_write("agent3", "plan.daily_budget", 1200)


def test_operation_graph_supports_dynamic_business_stages_and_plan_refs_only() -> None:
    normalized = {
        "itemExecutionId": "A3-1",
        "inputContentHash": "sha256:a3",
        "productId": "P1",
        "storeId": "S1",
        "finalTaskTitle": "素材更新与投放观察",
        "executionObjective": "完成素材更新、上线、推广与观察闭环",
        "executionSteps": [
            {"stepId": "STEP-1", "instruction": "生成并审核新主图", "completionCriteria": "审核通过"},
            {"stepId": "STEP-2", "instruction": "发布新主图", "completionCriteria": "线上可见"},
        ],
    }
    package = {
        "agent2ActionDraft": {
            "v26ActionGraph": {
                "authorityHeaders": {
                    "plan.strategy_summary": "素材先行",
                    "plan.daily_budget": 600,
                    "plan.target_roas": 3.2,
                }
            }
        }
    }
    raw = {
        "finalTaskTitle": "素材更新与投放观察",
        "executionObjective": "完成素材更新、上线、推广与观察闭环",
        "operationStages": [
            {
                "stageId": "CONTENT",
                "stageName": "内容生产",
                "objective": "完成标题和主图方案",
                "dependencies": [],
                "requiredInputs": ["product snapshot"],
                "steps": [{"instruction": "生成主图候选"}],
                "toolCapabilities": ["multimodal_generation"],
                "artifacts": ["main_image_candidate"],
                "acceptanceCriteria": ["人工审核通过"],
                "affectedFields": ["title", "main_image"],
                "affectedMetrics": ["CTR"],
            },
            {
                "stageId": "PUBLISH",
                "stageName": "上架更新",
                "objective": "将审核通过素材发布",
                "dependencies": ["CONTENT"],
                "requiredInputs": ["approved asset"],
                "steps": [{"instruction": "替换并发布"}],
                "artifacts": ["publish_receipt"],
                "acceptanceCriteria": ["线上版本一致"],
            },
            {
                "stageId": "OBSERVE",
                "stageName": "观察与复盘入口",
                "objective": "等待指标成熟并进入系统复盘",
                "dependencies": ["PUBLISH"],
                "requiredInputs": ["post-change metrics"],
                "steps": [{"instruction": "等待观察窗口"}],
                "affectedMetrics": ["CTR", "CVR", "ROAS"],
                "reviewEntry": "满足最小样本或到达review window后进入System Review",
            },
        ],
    }
    graph = compile_operation_graph(normalized, raw, package)
    assert graph["schema"] == OPERATION_GRAPH_SCHEMA
    assert graph["operationStageCount"] == 3
    assert graph["compatibilityStageProjection"] is False
    assert graph["systemStageMutationAllowed"] is False
    assert set(graph["authorityHeaders"]["operation.plan_refs"]) == {
        "plan.daily_budget",
        "plan.strategy_summary",
        "plan.target_roas",
    }
    assert not any(key.startswith("plan.") for key in graph["authorityHeaders"])
    for stage in graph["operationStages"]:
        assert all(key.startswith("operation_stage.") for key in stage["authorityHeaders"])


def test_operation_graph_projects_old_exact_replay_without_second_llm_call() -> None:
    normalized = {
        "itemExecutionId": "OLD-A3",
        "inputContentHash": "sha256:old",
        "executionObjective": "执行已验真的旧SOP",
        "executionSteps": [
            {
                "stepId": "STEP-1",
                "actionType": "content_prepare",
                "instruction": "准备素材",
                "completionCriteria": "素材就绪",
            },
            {
                "stepId": "STEP-2",
                "actionType": "publish",
                "instruction": "发布素材",
                "completionCriteria": "线上可见",
            },
        ],
    }
    graph = compile_operation_graph(normalized, {}, {})
    assert graph["compatibilityStageProjection"] is True
    assert graph["operationStageCount"] == 2
    assert graph["operationStages"][0]["authorityHeaders"]["operation_stage.stage_id"] == "STEP-1"


def test_system_stage_remains_java_owned() -> None:
    authority = V26FieldAuthorityContract()
    with pytest.raises(FieldAuthorityViolation, match="v26_system_stage_write_forbidden"):
        authority.assert_stage_write("agent3", "systemStage")
    authority.assert_stage_write("agent3", "operationStage")
