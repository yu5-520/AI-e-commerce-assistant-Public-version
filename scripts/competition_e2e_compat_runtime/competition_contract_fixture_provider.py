#!/usr/bin/env python3
"""Compatibility entry for the deterministic competition fixture provider.

The base fixture generates deterministic business content for Agent1/2/3. The active
runtime adds strict contracts that a structural fixture must satisfy exactly:

- Agent1: ``itemExecutionId + inputContentHash`` and the evidence-backed execution lock;
- Agent2: ``itemExecutionId + inputContentHash`` for every returned plan;
- Agent3: at least two distinct execution-evidence requirements for lifecycle admission.

These additions prove transport and pipeline contracts only. They are not presented as
real Bailian/Qwen model-quality evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import competition_contract_fixture_provider as base  # noqa: E402

_ORIGINAL_RESPONSE_PAYLOAD = base.response_payload


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _act_lock_fields(judgment: dict[str, Any], source: dict[str, Any]) -> None:
    family = _text(judgment.get("selectedActionFamilyHint"))
    product_id = _text(source.get("productId"))
    if family == "roas_scale":
        problem = "高效投放已被连续趋势验证，但当前预算未充分承接有效流量。"
        action = "对当前商品关联计划执行一次受控预算增投。"
        decisive = [
            "最近三期ROAS连续上升。",
            "支付转化率同步改善。",
            "库存仅作为承接条件，不参与ROAS因果判断。",
        ]
        forbidden = ["creative", "platform_activity", "cross_store_operation"]
    else:
        problem = "流量与点击稳定，但支付转化率连续下降，详情页首屏承接减弱。"
        action = "对当前商品详情页首屏执行一次单变量转化修复。"
        decisive = [
            "最近三期流量与点击保持稳定。",
            "最近三期支付转化率连续下降。",
            "价格与投放保持不变，可隔离验证页面承接。",
        ]
        forbidden = ["price_change", "budget_change", "cross_store_operation"]
    judgment.update(
        evidenceStatus="sufficient",
        primaryProblemNode=problem,
        primaryAction=action,
        primaryExecutionTarget={
            "targetType": "product",
            "targetId": product_id,
            "owner": "运营专员",
            "scope": "当前店铺当前商品",
        },
        primaryOwner="运营专员",
        decisiveFacts=decisive,
        supportingCoordination=[],
        forbiddenActionDomains=forbidden,
        missingEvidence=[],
    )


def _agent1_exact(payload: dict[str, Any]) -> dict[str, Any]:
    result = base._agent1(payload)
    products = [
        item for item in base._list(payload.get("products")) if isinstance(item, dict)
    ]
    by_correlation = {
        _text(item.get("correlationId")): item
        for item in products
        if _text(item.get("correlationId"))
    }
    by_business = {
        (_text(item.get("storeId")), _text(item.get("productId"))): item
        for item in products
        if _text(item.get("storeId")) and _text(item.get("productId"))
    }
    judgments = (
        result.get("judgments") if isinstance(result.get("judgments"), list) else []
    )
    for judgment in judgments:
        if not isinstance(judgment, dict):
            continue
        source = by_correlation.get(_text(judgment.get("correlationId")))
        if source is None:
            source = by_business.get(
                (_text(judgment.get("storeId")), _text(judgment.get("productId")))
            )
        if source is None:
            continue
        judgment["itemExecutionId"] = source.get("itemExecutionId")
        judgment["inputContentHash"] = source.get("inputContentHash")
        judgment["correlationId"] = source.get("correlationId")
        judgment["productId"] = source.get("productId")
        judgment["storeId"] = source.get("storeId")
        judgment["signalId"] = source.get("signalId")
        if _text(judgment.get("decisionType")).lower() == "act":
            _act_lock_fields(judgment, source)
        else:
            judgment["evidenceStatus"] = "insufficient"
            judgment.setdefault("missingEvidence", ["当前趋势未达到动作准入阈值。"])
    return result


def _agent2_exact(payload: dict[str, Any]) -> dict[str, Any]:
    result = base._agent2(payload)
    packages = [
        item for item in base._list(payload.get("packages")) if isinstance(item, dict)
    ]
    by_package = {
        _text(item.get("packageId")): item
        for item in packages
        if _text(item.get("packageId"))
    }
    plans = result.get("plans") if isinstance(result.get("plans"), list) else []
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        source = by_package.get(_text(plan.get("packageId")))
        if source is None:
            continue
        plan["itemExecutionId"] = source.get("itemExecutionId")
        plan["inputContentHash"] = source.get("inputContentHash")
        plan["packageId"] = source.get("packageId")
        plan["productId"] = source.get("productId")
        plan["storeId"] = source.get("storeId")
    return result


def _agent3_admission_evidence(result: dict[str, Any]) -> dict[str, Any]:
    sops = result.get("sops") if isinstance(result.get("sops"), list) else []
    for sop in sops:
        if not isinstance(sop, dict):
            continue
        sop["submissionEvidence"] = [
            {
                "evidenceId": "EXECUTION-BEFORE-AFTER",
                "title": "执行前后平台凭证",
                "requiredFields": [
                    "before",
                    "after",
                    "dataVersion",
                    "operator",
                    "operatedAt",
                ],
                "acceptance": "必须能核对同一店铺、同一商品及同一执行对象的操作前后状态。",
            },
            {
                "evidenceId": "METRIC-REVIEW",
                "title": "指标复盘记录",
                "requiredFields": [
                    "baseline",
                    "finalValue",
                    "metricWindow",
                    "conclusion",
                ],
                "acceptance": "必须记录验证周期、核心指标变化与继续、暂停或回滚结论。",
            },
        ]
    return result


def response_payload(request_body: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    payload = base._last_user_payload(request_body)
    if isinstance(payload.get("products"), list):
        return "product_judgment_agent", _agent1_exact(payload)
    if (
        isinstance(payload.get("packages"), list)
        and payload.get("exactOutputIdentity") == "itemExecutionId+inputContentHash"
    ):
        return "action_plan_judgment_agent", _agent2_exact(payload)

    stage, result = _ORIGINAL_RESPONSE_PAYLOAD(request_body)
    if stage == "agent3_sop_agent" and isinstance(result, dict):
        result = _agent3_admission_evidence(result)
    return stage, result


def main() -> int:
    base.response_payload = response_payload
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
