"""V15 Agent-specific LLM gateway budget contract.

This wrapper sits above provider-specific gateways. It is intentionally stage and
budget oriented: report schema, product judgment, and task mapping Agents must
request budget here before any real model provider is allowed.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Dict

from src.repositories.sqlite_repository import dumps
from src.services.agent_budget_ledger_service import DEFAULT_TOTAL_AGENT_BUDGET, get_or_create_agent_budget_ledger, register_agent_event

AGENT_LLM_GATEWAY_VERSION = "15.0"
REAL_PROVIDER_ENABLED = False

STAGE_POLICIES = {
    "report_schema_agent": {"maxCalls": 3, "unit": "unknown_report_or_sheet", "fallback": "schema_dictionary_and_manual_confirm"},
    "product_judgment_agent": {"maxCalls": 3, "unit": "product_batch", "fallback": "local_metric_expansion"},
    "task_mapping_agent": {"maxCalls": 2, "unit": "candidate_package_batch", "fallback": "permission_sop_template"},
}


def stable_cache_key(stage: str, payload: Dict[str, Any]) -> str:
    text = dumps({"stage": stage, "payload": payload})
    return sha256(text.encode("utf-8")).hexdigest()


def estimate_token_budget(payload: Dict[str, Any]) -> int:
    return max(1, len(dumps(payload)) // 3)


def request_agent_call(*, stage: str, data_version: str | None, run_id: str | None = None, purpose: str, payload: Dict[str, Any] | None = None, requested_calls: int = 1, cache_hit: bool = False, fallback_allowed: bool = True, actual_calls: int | None = None, rag_retrievals: int = 0) -> Dict[str, Any]:
    payload = payload or {}
    policy = STAGE_POLICIES.get(stage, {"maxCalls": DEFAULT_TOTAL_AGENT_BUDGET, "unit": "unknown", "fallback": "local_rule"})
    ledger = get_or_create_agent_budget_ledger(data_version=data_version, run_id=run_id, source="agent_llm_gateway")
    if actual_calls is None:
        actual_calls = 0 if (cache_hit or fallback_allowed or not REAL_PROVIDER_ENABLED) else requested_calls
    fallback_used = bool(fallback_allowed and actual_calls == 0 and not cache_hit)
    event = register_agent_event(ledger_id=ledger["ledgerId"], run_id=ledger.get("runId"), data_version=data_version, stage=stage, call_type="agent_llm_gateway", requested_calls=requested_calls, actual_calls=actual_calls, cache_hit=cache_hit, fallback_used=fallback_used, rag_retrievals=rag_retrievals, estimated_input_tokens=estimate_token_budget(payload), estimated_output_tokens=512 if requested_calls else 0, reason=purpose, payload={"policy": policy, "cacheKey": stable_cache_key(stage, payload), "realProviderEnabled": REAL_PROVIDER_ENABLED})
    return {"version": AGENT_LLM_GATEWAY_VERSION, "stage": stage, "purpose": purpose, "policy": policy, "ledgerId": ledger["ledgerId"], "runId": ledger.get("runId"), "requestedCalls": requested_calls, "actualCalls": actual_calls, "cacheHit": cache_hit, "fallbackUsed": fallback_used, "event": event, "result": None, "rule": "V15 gateway: no Agent may call a model provider outside a budgeted ledger event."}
