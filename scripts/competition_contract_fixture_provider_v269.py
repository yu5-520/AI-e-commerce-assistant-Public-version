#!/usr/bin/env python3
"""V26.9 graph-only deterministic provider for structural three-report E2E.

This is NOT model-quality evidence. It intentionally authors only the fields owned by
Agent1/2/3. Hashes, admission, partitions, merge, execution identity, permissions and
task admission remain owned by the real candidate runtime.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import competition_contract_fixture_provider as legacy

VERSION = "26.9.0"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _evidence_ref(item: dict[str, Any]) -> str:
    refs = [str(value) for value in _list(item.get("evidenceRefs")) if str(value)]
    fact = next((value for value in refs if value.startswith("fact:")), None)
    if fact:
        return fact
    if refs:
        return refs[0]
    raise ValueError("v269_fixture_evidence_ref_missing")


def _decision_graph(item: dict[str, Any]) -> dict[str, Any]:
    product_id = _text(item.get("productId"))
    evidence = _evidence_ref(item)
    judgment = {
        "nodeKey": "J1",
        "kind": "JudgementNode",
        "reasoning": (
            "固定三报表的当前经营事实支持本次结构化判断；夹具不注入历史经验。"
        ),
        "evidenceRefs": [evidence],
        "confidence": 0.9,
    }
    if product_id == "COMP-P-OBSERVE":
        return {"nodes": [judgment], "edges": []}
    if product_id == "COMP-P-SCALE":
        traffic = {
            "nodeKey": "DA_TRAFFIC",
            "kind": "DecisionActionNode",
            "judgementRefs": ["J1"],
            "actionType": "controlled_roas_scale",
            "actionFamily": "roas_scale",
            "priority": 0.91,
            "confidence": 0.88,
            "evidenceRefs": [evidence],
        }
        creative = {
            "nodeKey": "DA_CREATIVE",
            "kind": "DecisionActionNode",
            "judgementRefs": ["J1"],
            "actionType": "supporting_creative_test",
            "actionFamily": "title_image_test",
            "priority": 0.73,
            "confidence": 0.81,
            "evidenceRefs": [evidence],
        }
        return {
            "nodes": [judgment, traffic, creative],
            "edges": [
                {"sourceRef": "J1", "targetRef": "DA_TRAFFIC", "relation": "supports"},
                {"sourceRef": "J1", "targetRef": "DA_CREATIVE", "relation": "supports"},
                {"sourceRef": "DA_TRAFFIC", "targetRef": "DA_CREATIVE", "relation": "enables"},
            ],
        }
    conversion = {
        "nodeKey": "DA_CONVERSION",
        "kind": "DecisionActionNode",
        "judgementRefs": ["J1"],
        "actionType": "repair_conversion_handoff",
        "actionFamily": "conversion_repair",
        "priority": 0.89,
        "confidence": 0.87,
        "evidenceRefs": [evidence],
    }
    return {
        "nodes": [judgment, conversion],
        "edges": [{"sourceRef": "J1", "targetRef": "DA_CONVERSION", "relation": "supports"}],
    }


def _agent1(payload: dict[str, Any]) -> dict[str, Any]:
    judgments: list[dict[str, Any]] = []
    for item in _list(payload.get("products")):
        if not isinstance(item, dict):
            continue
        judgments.append(
            {
                "itemExecutionId": item.get("itemExecutionId"),
                "inputContentHash": item.get("inputContentHash"),
                "DecisionGraph": _decision_graph(item),
            }
        )
    return {"judgments": judgments}


def _metric_candidates(action_family: str) -> list[str]:
    if action_family == "conversion_repair":
        return ["conversion_rate", "cvr", "roi", "ctr"]
    if action_family == "roas_scale":
        return ["roi", "roas", "conversion_rate", "ctr"]
    if action_family == "title_image_test":
        return ["ctr", "conversion_rate", "roi"]
    return ["roi", "conversion_rate", "ctr"]


def _pick_fact(facts: dict[str, Any], action_family: str) -> tuple[str, str, float, str]:
    entries: list[tuple[str, dict[str, Any]]] = [
        (str(ref), value)
        for ref, value in facts.items()
        if isinstance(value, dict)
        and isinstance(value.get("value"), (int, float))
        and not isinstance(value.get("value"), bool)
        and math.isfinite(float(value.get("value")))
        and isinstance(value.get("unit"), str)
        and value.get("unit")
    ]
    if not entries:
        raise ValueError("v269_fixture_fact_values_missing")
    for metric in _metric_candidates(action_family):
        for ref, value in entries:
            normalized = ref.lower().replace("-", "_")
            if metric in normalized:
                return ref, metric, float(value["value"]), str(value["unit"])
    ref, value = sorted(entries, key=lambda item: item[0])[0]
    metric = ref.split(":")[-1] or "metric"
    if metric in {"current", "previous"} and len(ref.split(":")) >= 2:
        metric = ref.split(":")[-2]
    return ref, metric, float(value["value"]), str(value["unit"])


def _plan_node(package: dict[str, Any], action_key: str) -> dict[str, Any]:
    decision_nodes = {
        str(node.get("nodeKey")): node
        for node in _list(_dict(package.get("DecisionGraph")).get("nodes"))
        if isinstance(node, dict)
    }
    action = _dict(decision_nodes.get(action_key))
    family = _text(action.get("actionFamily"))
    ref, metric, baseline, unit = _pick_fact(_dict(package.get("factValues")), family)
    step = max(abs(baseline) * 0.08, 0.01 if unit == "ratio" else 1.0)
    expected = baseline + step
    lo = min(baseline, expected)
    hi = max(expected, baseline + step * 1.5)
    guard_floor = baseline * 0.8 if baseline >= 0 else baseline * 1.2
    return {
        "nodeKey": "P" + action_key,
        "kind": "PlanActionNode",
        "decisionActionRef": action_key,
        "judgementRefs": list(action.get("judgementRefs") or []),
        "parameters": {"mode": "controlled_fixture", "changeRatio": 0.1},
        "baseline": {metric: {"value": baseline, "unit": unit, "sourceRef": ref}},
        "expectedOutcome": {
            metric: {
                "expectedValue": expected,
                "expectedRange": [lo, hi],
                "expectedDelta": expected - baseline,
            }
        },
        "reviewWindow": {"durationSeconds": 86400},
        "guard": {
            "floor": {"metric": metric, "comparator": "GTE", "value": guard_floor}
        },
        "riskBoundary": [],
        "acceptanceCriteria": [{"metric": metric, "constraint": "expectedRange"}],
        "affectedMetrics": [metric],
    }


def _agent2(payload: dict[str, Any]) -> dict[str, Any]:
    plans: list[dict[str, Any]] = []
    for package in _list(payload.get("packages")):
        if not isinstance(package, dict):
            continue
        partition = _dict(package.get("partition"))
        action_keys = [str(value) for value in _list(partition.get("actionKeys")) if str(value)]
        if not action_keys:
            raise ValueError("v269_fixture_partition_actions_missing")
        plans.append(
            {
                "packageId": package.get("packageId"),
                "itemExecutionId": package.get("itemExecutionId"),
                "inputContentHash": package.get("inputContentHash"),
                "PlanGraph": {
                    "nodes": [_plan_node(package, key) for key in action_keys],
                    "edges": [],
                },
            }
        )
    return {"plans": plans}


def _agent3(payload: dict[str, Any]) -> dict[str, Any]:
    sops: list[dict[str, Any]] = []
    for package in _list(payload.get("packages")):
        if not isinstance(package, dict):
            continue
        plan = _dict(package.get("PlanGraph"))
        nodes = [node for node in _list(plan.get("nodes")) if isinstance(node, dict)]
        refs = [str(node.get("nodeKey")) for node in nodes if str(node.get("nodeKey") or "")]
        if not refs:
            raise ValueError("v269_fixture_plan_nodes_missing")
        stop_refs: list[str] = []
        for node in nodes:
            plan_ref = str(node.get("nodeKey") or "")
            for guard_key in sorted(_dict(node.get("guard"))):
                stop_refs.append(f"{plan_ref}:{guard_key}")
        operation = {
            "nodes": [
                {
                    "nodeKey": "O1",
                    "kind": "OperationStage",
                    "planActionRefs": refs,
                    "instruction": "按冻结 PlanGraph 执行并保留可核验操作凭证。",
                    "owner": "operator",
                    "executionObject": _text(package.get("productId")) or "current_product",
                    "sequence": 0,
                    "rollback": "恢复执行前状态并记录回滚凭证。",
                    "stopConditionRefs": stop_refs,
                    "acceptanceActions": ["核验执行记录", "核验后续经营事实"],
                }
            ],
            "edges": [],
        }
        sops.append(
            {
                "packageId": package.get("packageId"),
                "itemExecutionId": package.get("itemExecutionId"),
                "inputContentHash": package.get("inputContentHash"),
                "OperationGraph": operation,
            }
        )
    return {"sops": sops}


def response_payload(request_body: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    payload = legacy._last_user_payload(request_body)
    if payload.get("version") != VERSION:
        return legacy._original_response_payload(request_body)  # type: ignore[attr-defined]
    if isinstance(payload.get("products"), list):
        return "product_judgment_agent", _agent1(payload)
    packages = payload.get("packages")
    contract = _dict(payload.get("outputContract"))
    collection = contract.get("collection")
    if isinstance(packages, list) and collection == "sops":
        return "agent3_sop_agent", _agent3(payload)
    if isinstance(packages, list) and collection == "plans":
        return "action_plan_judgment_agent", _agent2(payload)
    raise ValueError("v269_fixture_provider_unrecognized_request")


def main(argv: Sequence[str] | None = None) -> int:
    if not hasattr(legacy, "_original_response_payload"):
        legacy._original_response_payload = legacy.response_payload  # type: ignore[attr-defined]
    legacy.response_payload = response_payload
    return legacy.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
