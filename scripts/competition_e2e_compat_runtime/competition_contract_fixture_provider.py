#!/usr/bin/env python3
"""Compatibility entry for the deterministic competition fixture provider.

The base fixture already generates the business content for Agent1/2/3. The active
V22.5.9 Agent1 contract additionally requires the provider to echo the exact
``itemExecutionId + inputContentHash`` pair from every projected product. This
entry adds only that transport identity and leaves all business fixture content in
the base provider unchanged.
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
