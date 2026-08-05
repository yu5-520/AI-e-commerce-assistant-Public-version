"""V20.3 product judgment Agent1 station.

Consumes V20.2 agent1_pending pipelineItems in microbatches. The station remains
compatible with the existing dataVersion mainline, but it no longer clears the
whole dataVersion before judging. Each microbatch deletes/replaces judgments only
for the signals it processes and writes item-level state.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.agent_budget_ledger_service import get_or_create_agent_budget_ledger, register_agent_event
from src.services.metric_trigger_expansion_v171_service import is_first_report_baseline
from src.services.pipeline_agent1_microbatch_v203_service import (
    DEFAULT_AGENT1_MICRO_BATCH_SIZE,
    pending_agent1_item_count,
    run_agent1_microbatch_loop_v203,
    seed_agent1_pipeline_items_from_admission,
)
from src.services.product_signal_admission_v197_service import ADMITTED_STATUS, product_signal_admission_station_v197
from src.services.signal_pool_service import list_signals

REAL_PRODUCT_AGENT_V197_VERSION = "20.3"
COVERAGE_THRESHOLD = 0.8


def _admitted_signals(data_version: str | None, limit: int) -> List[Dict[str, Any]]:
    return (list_signals(data_version=data_version, status=ADMITTED_STATUS, limit=limit).get("signals") or [])[:limit]


def _seed_from_existing_admitted(data_version: str | None, limit: int) -> Dict[str, Any]:
    signals = _admitted_signals(data_version, limit)
    admitted = [{"signalId": signal.get("signalId"), "productId": signal.get("entityId"), "storeId": signal.get("storeId"), "score": ((signal.get("admissionScore") or {}).get("score") if isinstance(signal.get("admissionScore"), dict) else 50)} for signal in signals]
    return seed_agent1_pipeline_items_from_admission(data_version, admitted=admitted, observed=[], source="agent1_station_seed_existing_admitted") if admitted else {"seededAgent1PendingCount": 0}


def product_judgment_agent_station_v197(
    data_version: str | None,
    *,
    user_id: str | None = None,
    max_signals: int = 160,
    force: bool = False,
    micro_batch_size: int = DEFAULT_AGENT1_MICRO_BATCH_SIZE,
    max_micro_batches: int | None = None,
    pipeline_stream_mode: bool = False,
    **_: Any,
) -> Dict[str, Any]:
    baseline = is_first_report_baseline(data_version)
    if baseline.get("isFirstReportBaseline"):
        return {"version": REAL_PRODUCT_AGENT_V197_VERSION, "stationId": "product_judgment_agent_station", "dataVersion": data_version, "baselineMode": "first_report", "baselineNoPrevious": True, "inputBundleCount": int(baseline.get("productSignalPackageCount") or 0), "agentJudgmentCount": 0, "coverageStatus": "baseline_skipped", "productAgentProviderStatus": "skipped_first_report_baseline", "rule": "V20.3 first report builds baseline only; no Agent1 call."}

    admission = None
    if pending_agent1_item_count(data_version) <= 0:
        admission = product_signal_admission_station_v197(data_version=data_version, user_id=user_id, max_signals=max_signals, force=force)
    if pending_agent1_item_count(data_version) <= 0:
        seed = _seed_from_existing_admitted(data_version, max_signals)
    else:
        seed = {"seededAgent1PendingCount": pending_agent1_item_count(data_version)}

    # Existing station mainline keeps compatibility by draining all currently
    # pending Agent1 items in microbatches. Later V20.x can set
    # pipeline_stream_mode=True to run only one microbatch per worker tick.
    batch_size = max(1, min(20, int(micro_batch_size or DEFAULT_AGENT1_MICRO_BATCH_SIZE)))
    if max_micro_batches is None:
        max_micro_batches = 1 if pipeline_stream_mode else max(1, int((max_signals + batch_size - 1) / batch_size))
    result = run_agent1_microbatch_loop_v203(data_version=data_version, user_id=user_id, batch_size=batch_size, max_batches=max_micro_batches)
    provider = result.get("provider") or {}
    ledger = get_or_create_agent_budget_ledger(data_version=data_version, source="v20_3_agent1_microbatch_items")
    register_agent_event(
        ledger_id=ledger["ledgerId"],
        data_version=data_version,
        stage="product_judgment_agent_station",
        call_type="v20_3_agent1_pipeline_item_microbatch",
        requested_calls=int(result.get("microBatchCount") or 0),
        actual_calls=int(provider.get("actualCalls") or 0),
        fallback_used=False,
        rag_retrievals=0,
        actual_input_tokens=int(provider.get("inputTokens") or 0),
        actual_output_tokens=int(provider.get("outputTokens") or 0),
        reason="V20.3: Agent1 consumes agent1_pending pipelineItems in microbatches and writes item state per signal.",
        payload={"admission": admission, "seed": seed, "microbatch": result},
    )
    pending = int(result.get("pendingItemCount") or 0)
    input_count = int(result.get("claimedItemCount") or 0)
    judged_count = int(result.get("judgedProductCount") or 0)
    input_products = int(result.get("inputProductCount") or input_count or 0)
    coverage = round(judged_count / input_products, 4) if input_products else 0
    status = "partial" if pending > 0 and int(result.get("agentJudgmentCount") or 0) > 0 else "passed" if input_products and coverage >= COVERAGE_THRESHOLD else "waiting" if not input_products else "failed"
    return {
        "version": REAL_PRODUCT_AGENT_V197_VERSION,
        "stationId": "product_judgment_agent_station",
        "dataVersion": data_version,
        "baselineMode": "normal_delta",
        "baselineNoPrevious": False,
        "inputBundleCount": input_count,
        "candidateProductCount": input_products,
        "resolvedProductCount": input_products,
        "agentJudgmentCount": int(result.get("agentJudgmentCount") or 0),
        "formalJudgmentCount": int(result.get("agentJudgmentCount") or 0),
        "agent1RouteLockCount": 0,
        "bySelectedActionFamily": {},
        "observeOnlyJudgmentCount": 0,
        "judgedProductCount": judged_count,
        "coverageRate": coverage,
        "coverageStatus": status,
        "pendingItemCount": pending,
        "microBatchCount": int(result.get("microBatchCount") or 0),
        "claimedItemCount": input_count,
        "agent1ApiCallCount": int(provider.get("actualCalls") or 0),
        "productAgentProviderStatus": provider.get("providerStatus"),
        "productAgentProvider": provider,
        "pipelineItemSummary": result.get("pipelineItemSummary"),
        "agentJudgmentRef": f"agent1_operating_judgment_v203:{data_version or 'latest'}",
        "outputRef": f"agent1_operating_judgment_v203:{data_version or 'latest'}",
        "rule": "V20.3: Agent1 runs as pipelineItem microbatches; current station drains pending items for compatibility unless pipeline_stream_mode=True.",
    }
