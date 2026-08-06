#!/usr/bin/env python3
"""Compatibility entry for the deterministic competition fixture provider.

The base fixture already generates the business content for Agent1/2/3. The active
V22.5.9 Agent1 contract additionally requires both the exact transport identity
(``itemExecutionId + inputContentHash``) and a complete evidence-backed execution
lock for every ``act`` result. This entry adds those contract fields without
changing the product runtime or pretending to be a real model-quality proof.
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
    products = [item for item in base._list(payload.get("products")) if isinstance(item, dict)]
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
    judgments = result.get("judgments") if isinstance(result.get("judgments"), list) else []
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


def response_payload(request_body: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    payload = base._last_user_payload(request_body)
    if isinstance(payload.get("products"), list):
        return "product_judgment_agent", _agent1_exact(payload)
    return _ORIGINAL_RESPONSE_PAYLOAD(request_body)


def main() -> int:
    base.response_payload = response_payload
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
