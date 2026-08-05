from __future__ import annotations

import sys
import types

from src.services import agent_token_runtime_v230_service as runtime


def _product(store: str, product: str, correlation: str) -> dict:
    return {
        "correlationId": correlation,
        "identity": {"storeId": store, "productId": product},
    }


def _judgment(store: str, product: str, correlation: str) -> dict:
    return {
        "correlationId": correlation,
        "storeId": store,
        "productId": product,
        "decisionType": "act",
    }


def test_agent1_identity_is_store_scoped() -> None:
    products = [
        _product("S1", "P1", "S1:P1:A"),
        _product("S2", "P1", "S2:P1:B"),
    ]
    judgments = [_judgment("S1", "P1", "S1:P1:A")]

    missing = runtime._missing_agent1_products(products, judgments)

    assert len(missing) == 1
    assert missing[0]["identity"]["storeId"] == "S2"


def test_agent1_missing_batch_retries_only_missing_item(monkeypatch) -> None:
    products = [
        _product("S1", "P1", "S1:P1:A"),
        _product("S1", "P2", "S1:P2:B"),
    ]
    envelopes = [
        {"schema": "unused", "payload": products[0]},
        {"schema": "unused", "payload": products[1]},
    ]

    monkeypatch.setattr(runtime, "assert_agent_input_envelope", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "split_envelopes_by_budget", lambda values, **kwargs: [values])
    monkeypatch.setenv("AGENT1_MISSING_ITEM_RETRY_ATTEMPTS", "2")

    calls = []

    def call_json(**kwargs):
        user_payload = kwargs["cache_payload"]
        batch_products = user_payload["products"]
        calls.append(
            {
                "count": len(batch_products),
                "cache_enabled": kwargs["cache_enabled"],
            }
        )
        if len(batch_products) == 2:
            return {"judgments": [_judgment("S1", "P1", "S1:P1:A")]}, {
                "providerCallExecuted": True,
                "input": 100,
                "output": 20,
            }
        return {"judgments": [_judgment("S1", "P2", "S1:P2:B")]}, {
            "providerCallExecuted": True,
            "input": 50,
            "output": 10,
        }

    gateway = types.ModuleType("src.services.llm_gateway_v196_service")
    gateway.call_json = call_json
    monkeypatch.setitem(sys.modules, gateway.__name__, gateway)

    core = types.ModuleType("src.services.real_product_judgment_agent_v196_service")
    core._build_messages = lambda data_version, batch_products, policy: (
        [{"role": "user", "content": "{}"}],
        {"products": batch_products},
    )
    core._source_maps = lambda batch_products: {}
    core._normalize_judgments = lambda payload, source_maps, data_version: (
        payload.get("judgments", []),
        {"normalized": len(payload.get("judgments", []))},
    )
    monkeypatch.setitem(sys.modules, core.__name__, core)
    import src.services as services_package

    monkeypatch.setattr(
        services_package,
        "real_product_judgment_agent_v196_service",
        core,
        raising=False,
    )

    judgments, summary = runtime.run_agent1_projected_inputs(
        envelopes,
        data_version="DV-TEST",
        max_items_per_call=8,
    )

    assert len(judgments) == 2
    assert summary["providerStatus"] == "ok"
    assert summary["missingProductJudgmentCount"] == 0
    assert summary["recoveredMissingProductCount"] == 1
    assert summary["retryAttemptedItemCount"] == 1
    assert summary["requestCacheEnabled"] is False
    assert calls == [
        {"count": 2, "cache_enabled": False},
        {"count": 1, "cache_enabled": False},
    ]
