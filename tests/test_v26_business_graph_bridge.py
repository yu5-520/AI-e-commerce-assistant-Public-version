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
# only the exact V26 modules under test.
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
LINEAGE = _load_module(
    "src.services.v26_node_edge_lineage_service",
    SERVICES_ROOT / "v26_node_edge_lineage_service.py",
)

ACTION_GRAPH_SCHEMA = GRAPH.ACTION_GRAPH_SCHEMA
JUDGEMENT_GRAPH_SCHEMA = GRAPH.JUDGEMENT_GRAPH_SCHEMA
OPERATION_GRAPH_SCHEMA = GRAPH.OPERATION_GRAPH_SCHEMA
compile_action_graph = LINEAGE.compile_action_graph
compile_judgement_graph = LINEAGE.compile_judgement_graph
compile_operation_graph = LINEAGE.compile_operation_graph
FieldAuthorityViolation = FIELD.FieldAuthorityViolation
V26FieldAuthorityContract = FIELD.V26FieldAuthorityContract


def _judgement_graph() -> dict:
    normalized = {
        "itemExecutionId": "A1-MULTI",
        "inputContentHash": "sha256:input",
        "productId": "P1",
        "storeId": "S1",
        "artifactRefs": {"snapshot": "ART-SNAPSHOT-1"},
        "confidence": 0.83,
        "finding": "点击提升但转化没有同步改善",
        "selectedActionFamilyHint": "title_image_test",
        "selectedOperatingRoute": "creative_validation",
        "agent1DecisionIR": {
            "coreProblem": "点击提升未转化为成交提升",
            "decisionSummary": "同时检查素材承接与流量质量。",
            "facts": [{"metric": "CTR", "delta": "+18%"}],
        },
    }
    raw = {
        "decisionSummary": "保留兼容主判断。",
        "judgementNodes": [
            {
                "nodeKey": "J-CREATIVE",
                "reasoning": "点击已上升但成交未同步，先检查素材承诺与承接一致性。",
                "primaryIssue": "素材承诺与详情承接可能不一致",
                "evidenceRefs": ["ART-SNAPSHOT-1"],
                "recommendedDirection": "优化标题主图与详情承接",
                "actionFamilyHint": "title_image_test",
                "priorityWeight": 0.82,
                "confidence": 0.84,
                "relations": [
                    {"targetRef": "J-TRAFFIC", "relation": "related_to"}
                ],
            },
            {
                "nodeKey": "J-TRAFFIC",
                "reasoning": "素材变化后需要同时观察流量质量，避免只看CTR。",
                "primaryIssue": "流量质量可能限制成交改善",
                "evidenceRefs": ["ART-SNAPSHOT-1"],
                "recommendedDirection": "小规模投流验证",
                "actionFamilyHint": "traffic_test",
                "priorityWeight": 0.61,
                "confidence": 0.72,
                "relations": [],
            },
        ],
    }
    return compile_judgement_graph(normalized, raw)


def _action_graph(judgement_graph: dict) -> dict:
    normalized = {
        "itemExecutionId": "A2-MULTI",
        "inputContentHash": "sha256:a2",
        "productId": "P1",
        "storeId": "S1",
        "actionFamily": "title_image_test",
        "actionIntent": "先修素材，再小规模投流验证",
        "validationMetrics": ["CTR", "CVR", "ROAS"],
        "familyPayload": {
            "strategySummary": "兼容主投影仍保留单动作族。",
            "dailyBudget": 600,
            "targetRoas": 3.2,
        },
    }
    raw = {
        "familyPayload": normalized["familyPayload"],
        "actionNodes": [
            {
                "actionKey": "A-CONTENT",
                "actionFamily": "title_image_test",
                "judgementRefs": ["J-CREATIVE"],
                "strategySummary": "先完成标题主图与承接一致性修改。",
                "parameterPack": {"variantCount": 2},
                "affectedFields": ["title", "main_image"],
                "affectedMetrics": ["CTR", "CVR"],
                "dependencies": [],
                "conflicts": [],
            },
            {
                "actionKey": "A-TRAFFIC",
                "actionFamily": "traffic_test",
                "judgementRefs": ["J-CREATIVE", "J-TRAFFIC"],
                "strategySummary": "素材上线后以小预算验证有效流量。",
                "parameterPack": {"dailyBudget": 600, "targetRoas": 3.2},
                "dailyBudget": 600,
                "targetRoas": 3.2,
                "reviewWindow": "72h",
                "dependencies": ["A-CONTENT"],
                "conflicts": [],
                "affectedFields": ["traffic_budget"],
                "affectedMetrics": ["ROAS", "CVR"],
            },
        ],
    }
    return compile_action_graph(
        normalized,
        raw,
        {"v26JudgementGraph": judgement_graph},
    )


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
    assert first["version"] == "26.5.0"
    assert first["graphHash"] == second["graphHash"]
    assert first["legacyBusinessFieldsAuthoritative"] is False
    assert first["authorityHeaders"]["judgement.priority_weight"] == 0.78
    assert "refund_rate" in first["authorityHeaders"]["judgement.ignored_signals"][0]
    assert first["nodeCount"] == 1
    assert first["compatibilityNodeProjection"] is True
    assert first["nodes"][0]["nodeKey"] == "J1"
    assert str(first["nodes"][0]["nodeHash"]).startswith("sha256:")


def test_v265_multi_judgement_nodes_and_internal_edges_are_addressable() -> None:
    first = _judgement_graph()
    second = _judgement_graph()
    assert first["nodeCount"] == 2
    assert first["edgeCount"] == 1
    assert first["compatibilityNodeProjection"] is False
    assert first["graphHash"] == second["graphHash"]
    assert {node["nodeKey"] for node in first["nodes"]} == {"J-CREATIVE", "J-TRAFFIC"}
    assert len({node["nodeHash"] for node in first["nodes"]}) == 2
    edge = first["edges"][0]
    assert edge["relation"] == "related_to"
    assert edge["sourceKey"] == "J-CREATIVE"
    assert edge["targetKey"] == "J-TRAFFIC"
    assert str(edge["edgeHash"]).startswith("sha256:")
    assert first["modelMayWriteNodeHashes"] is False
    assert first["modelMayWriteEdgeHashes"] is False


def test_v265_judgement_relation_orphan_fails_closed() -> None:
    normalized = {
        "itemExecutionId": "A1-ORPHAN",
        "artifactRefs": {"snapshot": "ART-SNAPSHOT-1"},
    }
    raw = {
        "judgementNodes": [
            {
                "nodeKey": "J1",
                "reasoning": "x",
                "primaryIssue": "x",
                "evidenceRefs": ["ART-SNAPSHOT-1"],
                "relations": [{"targetRef": "J404", "relation": "supports"}],
            }
        ]
    }
    with pytest.raises(ValueError, match="v26_judgement_relation_orphan"):
        compile_judgement_graph(normalized, raw)


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
    graph = compile_action_graph(normalized, normalized, {})
    assert graph["schema"] == ACTION_GRAPH_SCHEMA
    assert graph["version"] == "26.5.0"
    headers = graph["authorityHeaders"]
    assert headers["plan.daily_budget"] == 800
    assert headers["plan.target_roas"] == 3.4
    assert headers["plan.selected_strategy"]["id"] == "A"
    assert graph["nodeCount"] == 1
    assert graph["compatibilityNodeProjection"] is True

    authority = V26FieldAuthorityContract()
    with pytest.raises(FieldAuthorityViolation, match="v26_field_write_forbidden"):
        authority.assert_write("agent3", "plan.daily_budget", 1200)


def test_v265_multi_action_graph_links_judgements_and_dependencies() -> None:
    judgement = _judgement_graph()
    action = _action_graph(judgement)
    assert action["nodeCount"] == 2
    assert action["compatibilityNodeProjection"] is False
    assert action["judgementGraphHash"] == judgement["graphHash"]
    assert {node["nodeKey"] for node in action["nodes"]} == {"A-CONTENT", "A-TRAFFIC"}
    relations = [edge["relation"] for edge in action["edges"]]
    assert relations.count("supports_action") == 3
    assert relations.count("depends_on") == 1
    assert all(str(node["nodeHash"]).startswith("sha256:") for node in action["nodes"])


def test_v265_action_orphan_judgement_ref_fails_closed() -> None:
    judgement = _judgement_graph()
    normalized = {
        "itemExecutionId": "A2-ORPHAN",
        "familyPayload": {"strategySummary": "x"},
    }
    raw = {
        "familyPayload": normalized["familyPayload"],
        "actionNodes": [
            {
                "actionKey": "A1",
                "actionFamily": "traffic_test",
                "judgementRefs": ["J404"],
                "strategySummary": "x",
            }
        ],
    }
    with pytest.raises(ValueError, match="v26_action_node_orphan_judgement_ref"):
        compile_action_graph(normalized, raw, {"v26JudgementGraph": judgement})


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
    assert graph["version"] == "26.5.0"
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
        assert stage["nodeHash"] == stage["stageHash"]


def test_v265_operation_graph_links_action_nodes_and_stage_dependencies() -> None:
    judgement = _judgement_graph()
    action = _action_graph(judgement)
    normalized = {
        "itemExecutionId": "A3-MULTI",
        "inputContentHash": "sha256:a3-multi",
        "executionObjective": "素材修改后小规模投流",
        "executionSteps": [
            {"stepId": "CONTENT", "instruction": "修改素材"},
            {"stepId": "TRAFFIC", "instruction": "启动投流"},
        ],
    }
    raw = {
        "operationStages": [
            {
                "stageId": "CONTENT",
                "stageName": "素材修改",
                "objective": "完成素材修改",
                "dependencies": [],
                "actionRefs": ["A-CONTENT"],
                "steps": [{"instruction": "修改主图"}],
            },
            {
                "stageId": "TRAFFIC",
                "stageName": "小规模投流",
                "objective": "验证素材与流量组合",
                "dependencies": ["CONTENT"],
                "actionRefs": ["A-TRAFFIC"],
                "steps": [{"instruction": "启动小预算投流"}],
            },
        ]
    }
    package = {"agent2ActionDraft": {"v26ActionGraph": action}}
    graph = compile_operation_graph(normalized, raw, package)
    assert graph["nodeCount"] == 2
    assert graph["actionGraphHash"] == action["graphHash"]
    assert graph["compatibilityActionRefProjection"] is False
    relations = [edge["relation"] for edge in graph["edges"]]
    assert relations.count("implemented_by") == 2
    assert relations.count("depends_on_stage") == 1


def test_v265_operation_orphan_action_ref_fails_closed() -> None:
    judgement = _judgement_graph()
    action = _action_graph(judgement)
    normalized = {"itemExecutionId": "A3-ORPHAN", "executionObjective": "x"}
    raw = {
        "operationStages": [
            {
                "stageId": "S1",
                "stageName": "x",
                "actionRefs": ["A404"],
                "steps": [{"instruction": "x"}],
            }
        ]
    }
    with pytest.raises(ValueError, match="v26_operation_stage_orphan_action_ref"):
        compile_operation_graph(
            normalized,
            raw,
            {"agent2ActionDraft": {"v26ActionGraph": action}},
        )


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
