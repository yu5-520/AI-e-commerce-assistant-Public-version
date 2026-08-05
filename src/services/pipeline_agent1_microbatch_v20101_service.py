"""V20.28 Agent1 pipeline-item worker.

This is the only Agent1 runtime entry. It reloads the full signal bundle, calls
the direct Agent1 core with stable operating policy context, and writes one
complete semantic payload to pipeline_items. Dynamic historical RAG is not used
until the later Action Pack stage.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services.agent_runtime_contract_v2010_service import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    missing_agent1_contract,
    normalize_agent1_completed_contract,
    product_id_of,
)
from src.services.operating_policy_context_v2028_service import (
    OPERATING_POLICY_CONTEXT_VERSION,
    build_operating_policy_context,
)
from src.services.pipeline_item_service import (
    build_item_envelope,
    ensure_pipeline_item_tables,
    pipeline_item_summary,
    record_pipeline_item_event,
    upsert_pipeline_item,
)
from src.services.real_product_judgment_agent_v196_service import (
    _real_agent_judgments,
    _strict_product_id,
)
from src.services.signal_pool_service import list_signals, update_signal_status

PIPELINE_AGENT1_MICROBATCH_VERSION = "20.28"
AGENT1_PENDING_STAGE = "agent1_pending"
AGENT1_RUNNING_STAGE = "agent1_running"
AGENT1_COMPLETED_STAGE = "agent1_completed"
AGENT1_FAILED_STAGE = "agent1_failed"
AGENT1_OUTPUT_INVALID_STAGE = "agent1_output_invalid"
OBSERVED_STAGE = "observed_soft_gate"
DEFAULT_AGENT1_MICRO_BATCH_SIZE = 8


def now_iso() -> str:
    return datetime.now().isoformat()


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in {"", "—", "未识别", "UNKNOWN", "null", "None"})


def _score_priority(summary: Dict[str, Any]) -> int:
    try:
        score = int(summary.get("score") or summary.get("admissionScore") or 0)
    except Exception:
        score = 0
    return max(1, min(100, 100 - score))


def _pending_items(data_version: str | None, limit: int) -> List[Dict[str, Any]]:
    ensure_pipeline_item_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM pipeline_items
            WHERE COALESCE(data_version, '') = COALESCE(?, '')
              AND current_stage = ? AND status IN ('queued', 'ready', 'retry')
            ORDER BY priority ASC, updated_at ASC LIMIT ?
            """,
            (data_version, AGENT1_PENDING_STAGE, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def pending_agent1_item_count(data_version: str | None) -> int:
    ensure_pipeline_item_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM pipeline_items WHERE COALESCE(data_version, '')=COALESCE(?, '') AND current_stage=? AND status IN ('queued','ready','retry')",
            (data_version, AGENT1_PENDING_STAGE),
        ).fetchone()
    return int(row["c"] or 0) if row else 0


def _signals_by_id(data_version: str | None, signal_ids: List[str], limit: int) -> Dict[str, Dict[str, Any]]:
    signals = list_signals(data_version=data_version, limit=max(limit * 10, 160)).get("signals") or []
    wanted = {str(value) for value in signal_ids if value}
    result: Dict[str, Dict[str, Any]] = {}
    for signal in signals:
        signal_id = str(signal.get("signalId") or signal.get("signal_id") or "")
        if signal_id in wanted:
            result[signal_id] = signal
    return result


def _set_items_running(items: List[Dict[str, Any]]) -> None:
    with connect() as conn:
        for item in items:
            conn.execute(
                "UPDATE pipeline_items SET current_stage=?, status='running', updated_at=? WHERE item_id=?",
                (AGENT1_RUNNING_STAGE, now_iso(), item.get("item_id")),
            )
        conn.commit()


def _judgment_keys(judgment: Dict[str, Any]) -> List[str]:
    return [
        str(value)
        for value in [judgment.get("signalId"), judgment.get("productId"), product_id_of(judgment)]
        if not _blank(value)
    ]


def _signal_keys(item: Dict[str, Any], signal: Dict[str, Any]) -> List[str]:
    return [
        str(value)
        for value in [
            item.get("signal_id"),
            item.get("product_id"),
            signal.get("signalId"),
            signal.get("entityId"),
            signal.get("productId"),
            _strict_product_id(signal),
        ]
        if not _blank(value)
    ]


def _index_judgments(judgments: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {}
    for judgment in judgments:
        for key in _judgment_keys(judgment):
            result.setdefault(key, []).append(judgment)
    return result


def _match(
    item: Dict[str, Any],
    signal: Dict[str, Any],
    indexed: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    for key in _signal_keys(item, signal):
        if key in indexed:
            return indexed[key]
    return []


def _signal_payload(signal: Dict[str, Any]) -> Dict[str, Any]:
    raw = signal.get("payload") if isinstance(signal.get("payload"), dict) else signal
    profile = raw.get("profileLayer") if isinstance(raw.get("profileLayer"), dict) else {}
    identity = raw.get("productIdentity") if isinstance(raw.get("productIdentity"), dict) else {}
    metric = raw.get("metricLayer") if isinstance(raw.get("metricLayer"), dict) else {}
    snapshot = raw.get("snapshotLayer") if isinstance(raw.get("snapshotLayer"), dict) else {}
    dynamic = raw.get("dynamicMetrics") if isinstance(raw.get("dynamicMetrics"), dict) else {}
    product_id = signal.get("entityId") or signal.get("productId") or raw.get("productId") or identity.get("productId") or profile.get("productId") or _strict_product_id(signal)
    store_id = signal.get("storeId") or raw.get("storeId") or identity.get("storeId") or profile.get("storeId") or "GLOBAL"
    title = raw.get("productTitle") or raw.get("title") or identity.get("productTitle") or identity.get("title") or profile.get("title") or profile.get("shortName")
    return {
        **raw,
        "dataVersion": signal.get("dataVersion") or raw.get("dataVersion"),
        "productId": product_id,
        "storeId": store_id,
        "signalId": signal.get("signalId") or signal.get("signal_id") or raw.get("signalId"),
        "productTitle": title,
        "title": title,
        "productIdentity": {
            **profile,
            **identity,
            "productId": product_id,
            "storeId": store_id,
            "productTitle": title,
            "title": title,
        },
        "metricLayer": {**metric, **snapshot, **dynamic},
        "metricEvidence": {**metric, **snapshot, **dynamic},
        "systemFacts": raw,
        "signalEvidence": signal,
    }


def _finish_item(
    item: Dict[str, Any],
    *,
    stage: str,
    status: str,
    output_ref: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    envelope = build_item_envelope(
        data_version=item.get("data_version") or payload.get("dataVersion"),
        item_id=item.get("item_id") or payload.get("itemId"),
        product_id=item.get("product_id") or payload.get("productId"),
        store_id=item.get("store_id") or payload.get("storeId"),
        signal_id=item.get("signal_id") or payload.get("signalId"),
        package_id=item.get("package_id") or payload.get("packageId"),
        action_family=item.get("action_family") or payload.get("actionFamily") or payload.get("selectedActionFamilyHint"),
        route=item.get("route") or payload.get("route") or payload.get("selectedOperatingRoute"),
        input_ref=item.get("input_ref") or f"pipeline_item:{item.get('item_id')}",
        output_ref=output_ref,
        stage=stage,
    )
    envelope = upsert_pipeline_item(
        envelope,
        stage=stage,
        status=status,
        priority=int(item.get("priority") or 50),
        output_ref=output_ref,
        payload=payload,
    )
    record_pipeline_item_event(
        envelope,
        station_id="product_judgment_agent_station",
        stage=stage,
        status=status,
        output_ref=output_ref,
        payload=payload,
    )
    return envelope


def seed_agent1_pipeline_items_from_admission(
    data_version: str | None,
    *,
    admitted: List[Dict[str, Any]] | None = None,
    observed: List[Dict[str, Any]] | None = None,
    source: str = "product_signal_admission_station",
) -> Dict[str, Any]:
    ensure_pipeline_item_tables()
    admitted = admitted or []
    observed = observed or []
    seeded = observed_count = 0
    for summary in admitted:
        signal_id = summary.get("signalId") or summary.get("signal_id")
        product_id = summary.get("productId") or summary.get("entityId")
        store_id = summary.get("storeId")
        envelope = build_item_envelope(
            data_version=data_version,
            product_id=product_id,
            store_id=store_id,
            signal_id=signal_id,
            input_ref=f"signal:{signal_id or product_id or data_version}",
            output_ref=f"pipeline_item:{data_version or 'latest'}:{signal_id or product_id or 'unknown'}",
            stage=AGENT1_PENDING_STAGE,
        )
        payload = {
            "source": source,
            "admissionSummary": summary,
            "agent1MicroBatchVersion": PIPELINE_AGENT1_MICROBATCH_VERSION,
            "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
            "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
            "runtimeRule": "pending item is a handle; Agent1 reloads the full signal bundle",
        }
        envelope = upsert_pipeline_item(
            envelope,
            stage=AGENT1_PENDING_STAGE,
            status="queued",
            priority=_score_priority(summary),
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        record_pipeline_item_event(
            envelope,
            station_id="product_signal_admission_station",
            stage=AGENT1_PENDING_STAGE,
            status="queued",
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        seeded += 1
    for summary in observed[:500]:
        signal_id = summary.get("signalId") or summary.get("signal_id")
        product_id = summary.get("productId") or summary.get("entityId")
        store_id = summary.get("storeId")
        envelope = build_item_envelope(
            data_version=data_version,
            product_id=product_id,
            store_id=store_id,
            signal_id=signal_id,
            input_ref=f"signal:{signal_id or product_id or data_version}",
            output_ref=f"pipeline_item_observed:{data_version or 'latest'}:{signal_id or product_id or 'unknown'}",
            stage=OBSERVED_STAGE,
        )
        payload = {
            "source": source,
            "admissionSummary": summary,
            "observationDeposited": True,
            "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        }
        envelope = upsert_pipeline_item(
            envelope,
            stage=OBSERVED_STAGE,
            status="observed",
            priority=100,
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        record_pipeline_item_event(
            envelope,
            station_id="product_signal_admission_station",
            stage=OBSERVED_STAGE,
            status="observed",
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        observed_count += 1
    return {
        "version": PIPELINE_AGENT1_MICROBATCH_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
        "dataVersion": data_version,
        "seededAgent1PendingCount": seeded,
        "observedItemCount": observed_count,
        "pipelineItemSummary": pipeline_item_summary(data_version=data_version, limit=30),
        "rule": "V20.28 admitted signals enter Agent1 with stable policy; observed signals are deposited outside the task queue.",
    }


def run_agent1_microbatch_v20101(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = DEFAULT_AGENT1_MICRO_BATCH_SIZE,
) -> Dict[str, Any]:
    del user_id
    ensure_pipeline_item_tables()
    items = _pending_items(data_version, max(1, min(20, int(batch_size or DEFAULT_AGENT1_MICRO_BATCH_SIZE))))
    if not items:
        return {
            "version": PIPELINE_AGENT1_MICROBATCH_VERSION,
            "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
            "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
            "dataVersion": data_version,
            "claimedItemCount": 0,
            "agentJudgmentCount": 0,
            "pendingItemCount": pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "skipped_no_pending_items", "actualCalls": 0},
        }
    _set_items_running(items)
    signals_by_id = _signals_by_id(data_version, [str(item.get("signal_id") or "") for item in items], len(items))
    pairs = [(item, signals_by_id.get(str(item.get("signal_id") or ""))) for item in items]
    valid_pairs = [(item, signal) for item, signal in pairs if isinstance(signal, dict)]
    missing_signal_items = [item for item, signal in pairs if not isinstance(signal, dict)]
    for item in missing_signal_items:
        _finish_item(
            item,
            stage=AGENT1_FAILED_STAGE,
            status="failed",
            output_ref=f"agent1_failed:{data_version or 'latest'}:{item.get('item_id')}",
            payload={
                "reason": "signal_payload_missing",
                "version": PIPELINE_AGENT1_MICROBATCH_VERSION,
                "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
            },
        )
    if not valid_pairs:
        return {
            "version": PIPELINE_AGENT1_MICROBATCH_VERSION,
            "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
            "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
            "dataVersion": data_version,
            "claimedItemCount": len(items),
            "agentJudgmentCount": 0,
            "failedItemCount": len(items),
            "pendingItemCount": pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "failed_signal_payload_missing", "actualCalls": 0},
        }

    judgments, provider = _real_agent_judgments(
        [signal for _, signal in valid_pairs],
        data_version,
        build_operating_policy_context(),
    )
    indexed = _index_judgments(judgments)
    completed = invalid = failed = observed_by_agent = 0
    missing_counter: Counter[str] = Counter()
    by_family: Counter[str] = Counter()
    for item, signal in valid_pairs:
        signal_id = str(item.get("signal_id") or "")
        matched = _match(item, signal, indexed)
        if not matched:
            failed += 1
            _finish_item(
                item,
                stage=AGENT1_FAILED_STAGE,
                status="failed",
                output_ref=f"agent1_failed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    "reason": "agent_returned_no_matching_judgment",
                    "providerStatus": provider.get("providerStatus"),
                    "version": PIPELINE_AGENT1_MICROBATCH_VERSION,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                },
            )
            continue
        judgment = matched[0]
        if str(judgment.get("decisionHint") or "") in {"observe_only", "metric_observation", "product_level_observation"}:
            observed_by_agent += 1
            _finish_item(
                item,
                stage=OBSERVED_STAGE,
                status="observed",
                output_ref=f"agent1_observed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    **judgment,
                    "observationDeposited": True,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
                },
            )
            continue
        payload = normalize_agent1_completed_contract(
            item=item,
            signal=_signal_payload(signal),
            judgment=judgment,
            provider=provider,
            data_version=data_version,
        )
        payload.update(
            {
                "version": PIPELINE_AGENT1_MICROBATCH_VERSION,
                "agent1MicroBatchVersion": PIPELINE_AGENT1_MICROBATCH_VERSION,
                "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
                "policyContextType": "stable_operating_policy_not_dynamic_rag",
                "dynamicRagStage": "action_pack_ready",
                "rawAgent1Judgment": judgment,
                "lineageSource": "pipeline_items.agent1_pending",
                "outputContract": "V20.28.agent1_completed",
            }
        )
        missing = missing_agent1_contract(payload)
        if missing:
            invalid += 1
            missing_counter.update(missing)
            _finish_item(
                item,
                stage=AGENT1_OUTPUT_INVALID_STAGE,
                status="failed",
                output_ref=f"agent1_output_invalid:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    "reason": "agent1_contract_missing",
                    "missing": missing,
                    "partialPayload": payload,
                    "providerStatus": provider.get("providerStatus"),
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                },
            )
            continue
        completed += 1
        by_family[str(payload.get("actionFamily") or "missing")] += 1
        _finish_item(
            item,
            stage=AGENT1_COMPLETED_STAGE,
            status="ready",
            output_ref=f"pipeline_items.agent1_completed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
            payload=payload,
        )
        if signal_id:
            update_signal_status(
                signal_id,
                "agent1_route_judgment_completed",
                {
                    "version": PIPELINE_AGENT1_MICROBATCH_VERSION,
                    "providerStatus": provider.get("providerStatus"),
                    "pipelineItemId": item.get("item_id"),
                    "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
                },
            )
    return {
        "version": PIPELINE_AGENT1_MICROBATCH_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
        "dynamicRagUsed": False,
        "dynamicRagNextStage": "action_pack_ready",
        "dataVersion": data_version,
        "claimedItemCount": len(items),
        "validSignalCount": len(valid_pairs),
        "completedItemCount": completed,
        "observedItemCount": observed_by_agent,
        "invalidItemCount": invalid,
        "failedItemCount": failed + len(missing_signal_items),
        "missingCounter": dict(missing_counter),
        "bySelectedActionFamily": dict(by_family),
        "agentJudgmentCount": len(judgments),
        "pendingItemCount": pending_agent1_item_count(data_version),
        "provider": provider,
        "pipelineItemSummary": pipeline_item_summary(data_version=data_version, limit=30),
        "legacyJudgmentTableWritten": False,
        "rule": "V20.28 Agent1 uses stable policy only; dynamic approved-experience RAG begins after the action family is locked.",
    }


def run_agent1_microbatch_loop_v20101(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = DEFAULT_AGENT1_MICRO_BATCH_SIZE,
    max_batches: int = 20,
) -> Dict[str, Any]:
    batches: List[Dict[str, Any]] = []
    for _ in range(max(1, min(50, int(max_batches or 1)))):
        result = run_agent1_microbatch_v20101(
            data_version=data_version,
            user_id=user_id,
            batch_size=batch_size,
        )
        if int(result.get("claimedItemCount") or 0) <= 0:
            break
        batches.append(result)
        if int(result.get("pendingItemCount") or 0) <= 0:
            break
    provider = {
        "providerStatus": "completed" if batches else "skipped_no_pending_items",
        "actualCalls": sum(int((item.get("provider") or {}).get("actualCalls") or 0) for item in batches),
        "inputTokens": sum(int((item.get("provider") or {}).get("inputTokens") or 0) for item in batches),
        "outputTokens": sum(int((item.get("provider") or {}).get("outputTokens") or 0) for item in batches),
    }
    return {
        "version": PIPELINE_AGENT1_MICROBATCH_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
        "dataVersion": data_version,
        "microBatchCount": len(batches),
        "claimedItemCount": sum(int(item.get("claimedItemCount") or 0) for item in batches),
        "agentJudgmentCount": sum(int(item.get("agentJudgmentCount") or 0) for item in batches),
        "completedItemCount": sum(int(item.get("completedItemCount") or 0) for item in batches),
        "observedItemCount": sum(int(item.get("observedItemCount") or 0) for item in batches),
        "pendingItemCount": pending_agent1_item_count(data_version),
        "provider": provider,
        "batches": [{key: value for key, value in item.items() if key not in {"pipelineItemSummary", "batches"}} for item in batches],
        "pipelineItemSummary": pipeline_item_summary(data_version=data_version, limit=50),
        "rule": "V20.28 Agent1 loop uses stable operating policy and leaves dynamic experience retrieval to Action Pack.",
    }


run_agent1_microbatch_v203 = run_agent1_microbatch_v20101
run_agent1_microbatch_loop_v203 = run_agent1_microbatch_loop_v20101
