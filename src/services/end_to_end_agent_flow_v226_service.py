"""V22.2.6 end-to-end Agent flow contract.

This module closes the three remaining runtime breaks after V22.2.5:

1. deterministic evidence decides whether Agent1 may inspect a product; the old
   numeric score is priority only and cannot silently turn valid evidence into an
   observation;
2. Agent1 resolves each product's immutable ``signalRef`` directly and never reads
   or updates Signal Pool;
3. the unified worker really advances ``agent1_pending`` before later Agent stages.

It also projects batch-station truth and product-item truth as separate layers so
one batch token is never added to product counts.
"""
from __future__ import annotations

import copy
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Set

from src.repositories.sqlite_repository import connect

END_TO_END_AGENT_FLOW_VERSION = "22.2.6"
AGENT1_ARTIFACT_RUNTIME_VERSION = "22.2.6"
SIGNAL_ADMISSION_POLICY_VERSION = "22.2.6"
PIPELINE_TRUTH_READ_MODEL_VERSION = "22.2.6"

_PRODUCT_NODE_ORDER = [
    "信号引擎",
    "Agent 研判",
    "动作矩阵",
    "Agent2 动作方案",
    "SOP 生成",
    "任务池",
    "任务闭环",
]
_PRODUCT_STAGE_NODE = {
    "observed_soft_gate": "信号引擎",
    "signal_admitted": "信号引擎",
    "agent1_pending": "Agent 研判",
    "agent1_running": "Agent 研判",
    "agent1_failed": "Agent 研判",
    "agent1_output_invalid": "Agent 研判",
    "agent1_completed": "Agent 研判",
    "action_pack_ready": "动作矩阵",
    "action_pack_invalid": "动作矩阵",
    "agent2_running": "Agent2 动作方案",
    "agent2_failed": "Agent2 动作方案",
    "agent2_output_invalid": "Agent2 动作方案",
    "agent2_completed": "Agent2 动作方案",
    "sop_mapped": "SOP 生成",
    "task_admitted": "任务池",
    "read_model_ready": "任务闭环",
    "task_loop_ready": "任务闭环",
}
_PRODUCT_STAGE_LABELS = {
    "observed_soft_gate": "观察沉淀",
    "signal_admitted": "信号准入",
    "agent1_pending": "Agent1排队",
    "agent1_running": "Agent1运行",
    "agent1_failed": "Agent1失败",
    "agent1_output_invalid": "Agent1输出异常",
    "agent1_completed": "Agent1完成",
    "action_pack_ready": "动作能力完成",
    "action_pack_invalid": "动作能力异常",
    "agent2_running": "Agent2运行",
    "agent2_failed": "Agent2失败",
    "agent2_output_invalid": "Agent2输出异常",
    "agent2_completed": "Agent2完成",
    "sop_mapped": "SOP完成",
    "task_admitted": "任务入池",
    "read_model_ready": "读模型完成",
    "task_loop_ready": "任务闭环",
}
_FAILED_STAGES = {
    "agent1_failed",
    "agent1_output_invalid",
    "action_pack_invalid",
    "agent2_failed",
    "agent2_output_invalid",
}
_RUNNING_STAGES = {"agent1_running", "agent2_running"}
_COMPLETED_STAGES = {
    "observed_soft_gate",
    "signal_admitted",
    "agent1_completed",
    "action_pack_ready",
    "agent2_completed",
    "sop_mapped",
    "task_admitted",
    "read_model_ready",
    "task_loop_ready",
}
_BOUND = False
_ORIGINAL_PIPELINE_LIVE_READER: Any = None


def _cross_validation(signal: Dict[str, Any]) -> Dict[str, Any]:
    value = signal.get("crossValidation")
    return value if isinstance(value, dict) else {}


def _decision(signal: Dict[str, Any]) -> Dict[str, Any]:
    value = _cross_validation(signal).get("decision")
    return value if isinstance(value, dict) else {}


def _field_signals(signal: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = signal.get("snapshotLayer") if isinstance(signal.get("snapshotLayer"), dict) else {}
    values = snapshot.get("fieldSignals") or signal.get("fieldSignals") or []
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _meaningful_change(signal: Dict[str, Any]) -> bool:
    decision = _decision(signal)
    if decision.get("taskTriggerAllowed") is True:
        return True
    cross = _cross_validation(signal)
    try:
        if int(cross.get("changedMetricCount") or 0) > 0:
            return True
    except Exception:
        pass
    return any(bool(item.get("meaningfulChange")) for item in _field_signals(signal))


def _structural_change(signal: Dict[str, Any]) -> bool:
    primary = str(signal.get("primarySignalType") or "").strip().lower()
    if primary in {
        "product_missing_from_latest",
        "product_new_in_latest",
        "new_product",
        "new_link",
        "test_link",
    }:
        return True
    # A product without a previous product snapshot inside a non-baseline batch is
    # a newly introduced product/link and deserves Agent1 context judgment.
    return bool(
        primary == "product_baseline"
        and signal.get("previousProductMetricSnapshot") is None
    )


def _agent1_eligibility(signal: Dict[str, Any], *, baseline_only: bool) -> Dict[str, Any]:
    if baseline_only or _decision(signal).get("baselineOnly") is True:
        return {
            "eligible": False,
            "reason": "batch_or_product_baseline",
            "source": "operatingEvidenceGraph.v1",
        }
    decision = _decision(signal)
    status = str(decision.get("status") or "").strip().lower()
    if status not in {"passed", "attention", ""}:
        return {
            "eligible": False,
            "reason": f"evidence_decision_{status or 'invalid'}",
            "source": "operatingEvidenceGraph.v1",
        }
    if _structural_change(signal):
        return {
            "eligible": True,
            "reason": "structural_product_or_link_change",
            "source": "operatingEvidenceGraph.v1",
        }
    if _meaningful_change(signal):
        return {
            "eligible": True,
            "reason": "meaningful_metric_change",
            "source": "operatingEvidenceGraph.v1",
        }
    return {
        "eligible": False,
        "reason": "zero_meaningful_change",
        "source": "operatingEvidenceGraph.v1",
    }


def product_signal_admission_station_v226(
    data_version: str | None,
    *,
    validated_bundle_ref: str | None,
    max_signals: int = 160,
    min_admitted: int = 0,
    max_admitted: int | None = None,
    **_: Any,
) -> Dict[str, Any]:
    """Fan out validated signals; evidence gates Agent1, score orders throughput."""
    from src.services import artifact_signal_admission_v225_service as admission
    from src.services.agent_pipeline_governance_v213_service import normalize_admission_limits
    from src.services.product_signal_admission_v197_service import score_signal

    limits = normalize_admission_limits(
        max_signals=max_signals,
        min_admitted=min_admitted,
        max_admitted=max_admitted,
    )
    payload = admission._validated_payload(validated_bundle_ref)
    signals = admission._signals(payload)
    baseline = payload.get("baseline") if isinstance(payload.get("baseline"), dict) else {}
    baseline_only = bool(payload.get("baselineNoPrevious") or baseline.get("baselineNoPrevious"))
    if baseline_only:
        return {
            "version": END_TO_END_AGENT_FLOW_VERSION,
            "stationId": "product_signal_admission_station",
            "businessOutputType": "baseline_signal_admission",
            "dataVersion": data_version,
            "validatedBundleArtifactRef": validated_bundle_ref,
            "baselineOnly": True,
            "fullSignalCount": len(signals),
            "qualifiedSignalCount": 0,
            "candidateProductCount": 0,
            "admittedSignalCount": 0,
            "observedSignalCount": len(signals),
            "agent1PendingItemCount": 0,
            "priorityScoredSignalCount": len(signals),
            "outputRef": f"business_output_pending_artifact:baseline_admission:{data_version or 'latest'}",
            "admissionPolicy": "baseline_never_enters_agent1",
            "scoreCanBlockAgent1": False,
            "rule": "First comparable report remains baseline and never enters Agent1.",
        }

    candidates: List[Dict[str, Any]] = []
    for signal in signals:
        score = score_signal(signal)
        eligibility = _agent1_eligibility(signal, baseline_only=False)
        candidates.append({"signal": signal, "score": score, "eligibility": eligibility})
    candidates.sort(
        key=lambda item: (
            int(item["score"].get("score") or 0),
            str(item["signal"].get("productId") or item["signal"].get("entityId") or ""),
        ),
        reverse=True,
    )
    eligible = [item for item in candidates if item["eligibility"]["eligible"]]
    selected_ids = {
        str(item["signal"].get("signalId") or "")
        for item in eligible[: limits["maxAdmitted"]]
    }
    admitted: List[Dict[str, Any]] = []
    observed: List[Dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for item in candidates[: limits["maxSignals"]]:
        signal_id = str(item["signal"].get("signalId") or "")
        is_admitted = signal_id in selected_ids
        reason = str(item["eligibility"].get("reason") or "unknown")
        reason_counts[reason] += 1
        summary = admission._seed_signal_item(
            data_version=data_version,
            signal=item["signal"],
            score={
                **item["score"],
                "agent1Eligible": bool(item["eligibility"]["eligible"]),
                "eligibilityReason": reason,
                "scoreRole": "priority_only",
            },
            source_artifact_ref=str(validated_bundle_ref),
            admitted=is_admitted,
        )
        summary["agent1Eligibility"] = item["eligibility"]
        (admitted if is_admitted else observed).append(summary)

    return {
        "version": END_TO_END_AGENT_FLOW_VERSION,
        "stationId": "product_signal_admission_station",
        "businessOutputType": "artifact_signal_admission",
        "dataVersion": data_version,
        "validatedBundleArtifactRef": validated_bundle_ref,
        "baselineOnly": False,
        "fullSignalCount": len(signals),
        "qualifiedSignalCount": len(eligible),
        "candidateProductCount": len(admitted),
        "admittedSignalCount": len(admitted),
        "observedSignalCount": len(observed),
        "agent1PendingItemCount": len(admitted),
        "observedItemCount": len(observed),
        "admitted": admitted,
        "observedTop": observed[:12],
        "admissionLimits": limits,
        "admissionReasonCounts": dict(reason_counts),
        "artificialMinimumApplied": False,
        "legacySignalPoolRead": False,
        "scoreCanBlockAgent1": False,
        "scoreRole": "priority_only",
        "admissionPolicy": "evidence_trigger_for_agent1_score_for_priority_only",
        "outputRef": f"business_output_pending_artifact:signal_admission:{data_version or 'latest'}",
        "rule": "Meaningful or structural evidence enters Agent1; the numeric score only orders throughput.",
    }


def _row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        return row.get(key) if isinstance(row, dict) else None


def _signal_from_item(item: Any) -> Dict[str, Any]:
    from src.services.artifact_transport_service import resolve_artifact, validate_artifact
    from src.services.pipeline_artifact_contract_service import artifact_refs_from_row

    refs = artifact_refs_from_row(item)
    signal_ref = str(refs.get("signalRef") or "").strip()
    if not signal_ref:
        raise RuntimeError("agent1_signal_ref_missing")
    validation = validate_artifact(signal_ref)
    if validation.get("ok") is not True:
        raise RuntimeError(
            f"agent1_signal_ref_invalid:{signal_ref}:{validation.get('status') or 'invalid'}"
        )
    signal = resolve_artifact(signal_ref)
    if not isinstance(signal, dict) or not signal:
        raise RuntimeError(f"agent1_signal_artifact_empty:{signal_ref}")
    expected = str(_row_value(item, "signal_id") or "").strip()
    actual = str(signal.get("signalId") or signal.get("signal_id") or "").strip()
    if expected and actual and expected != actual:
        raise RuntimeError(f"agent1_signal_identity_mismatch:{expected}:{actual}")
    return signal


def run_agent1_microbatch_v226(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 8,
) -> Dict[str, Any]:
    """Run Agent1 from product ``signalRef`` artifacts with no Signal Pool access."""
    del user_id
    from src.services import pipeline_agent1_microbatch_v20101_service as core
    from src.services.agent_runtime_contract_v2010_service import (
        AGENT_RUNTIME_CONTRACT_VERSION,
        missing_agent1_contract,
        normalize_agent1_completed_contract,
    )
    from src.services.operating_policy_context_v2028_service import (
        OPERATING_POLICY_CONTEXT_VERSION,
        build_operating_policy_context,
    )
    from src.services.pipeline_item_service import ensure_pipeline_item_tables, pipeline_item_summary

    ensure_pipeline_item_tables()
    items = core._pending_items(data_version, max(1, min(20, int(batch_size or 8))))
    if not items:
        return {
            "version": AGENT1_ARTIFACT_RUNTIME_VERSION,
            "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
            "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
            "dataVersion": data_version,
            "claimedItemCount": 0,
            "agentJudgmentCount": 0,
            "pendingItemCount": core.pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "skipped_no_pending_items", "actualCalls": 0},
            "runtimeSource": "artifactRefs.signalRef",
            "legacySignalPoolRead": False,
        }
    core._set_items_running(items)
    valid_pairs: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
    missing_signal_items: List[tuple[Dict[str, Any], str]] = []
    for item in items:
        try:
            valid_pairs.append((item, _signal_from_item(item)))
        except Exception as exc:
            missing_signal_items.append((item, str(exc)))
    for item, reason in missing_signal_items:
        core._finish_item(
            item,
            stage=core.AGENT1_FAILED_STAGE,
            status="failed",
            output_ref=f"agent1_failed:{data_version or 'latest'}:{item.get('item_id')}",
            payload={
                "reason": reason,
                "failureOwner": "artifact_transport",
                "version": AGENT1_ARTIFACT_RUNTIME_VERSION,
                "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                "legacySignalPoolRead": False,
            },
        )
    if not valid_pairs:
        return {
            "version": AGENT1_ARTIFACT_RUNTIME_VERSION,
            "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
            "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
            "dataVersion": data_version,
            "claimedItemCount": len(items),
            "agentJudgmentCount": 0,
            "failedItemCount": len(items),
            "pendingItemCount": core.pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "failed_signal_artifact_missing", "actualCalls": 0},
            "runtimeSource": "artifactRefs.signalRef",
            "legacySignalPoolRead": False,
        }

    judgments, provider = core._real_agent_judgments(
        [signal for _, signal in valid_pairs],
        data_version,
        build_operating_policy_context(),
    )
    indexed = core._index_judgments(judgments)
    completed = invalid = failed = observed_by_agent = 0
    missing_counter: Counter[str] = Counter()
    by_family: Counter[str] = Counter()
    for item, signal in valid_pairs:
        signal_id = str(item.get("signal_id") or "")
        matched = core._match(item, signal, indexed)
        if not matched:
            failed += 1
            core._finish_item(
                item,
                stage=core.AGENT1_FAILED_STAGE,
                status="failed",
                output_ref=f"agent1_failed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    "reason": "agent_returned_no_matching_judgment",
                    "providerStatus": provider.get("providerStatus"),
                    "version": AGENT1_ARTIFACT_RUNTIME_VERSION,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "legacySignalPoolRead": False,
                },
            )
            continue
        judgment = matched[0]
        decision_core = judgment.get("decisionCore") if isinstance(judgment.get("decisionCore"), dict) else {}
        decision_type = str(judgment.get("decisionType") or decision_core.get("decisionType") or "").strip().lower()
        decision_hint = str(judgment.get("decisionHint") or "").strip().lower()
        if decision_type == "observe" or decision_hint in {
            "observe_only",
            "metric_observation",
            "product_level_observation",
        }:
            observed_by_agent += 1
            core._finish_item(
                item,
                stage=core.OBSERVED_STAGE,
                status="observed",
                output_ref=f"agent1_observed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    **judgment,
                    "observationDeposited": True,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
                    "runtimeSource": "artifactRefs.signalRef",
                    "legacySignalPoolRead": False,
                },
            )
            continue
        payload = normalize_agent1_completed_contract(
            item=item,
            signal=core._signal_payload(signal),
            judgment=judgment,
            provider=provider,
            data_version=data_version,
        )
        payload.update(
            {
                "version": AGENT1_ARTIFACT_RUNTIME_VERSION,
                "agent1MicroBatchVersion": AGENT1_ARTIFACT_RUNTIME_VERSION,
                "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
                "policyContextType": "stable_operating_policy_not_dynamic_rag",
                "dynamicRagStage": "action_pack_ready",
                "rawAgent1Judgment": judgment,
                "lineageSource": "pipeline_items.artifactRefs.signalRef",
                "runtimeSource": "artifactRefs.signalRef",
                "legacySignalPoolRead": False,
                "outputContract": "V22.2.6.agent1_completed",
            }
        )
        missing = missing_agent1_contract(payload)
        if missing:
            invalid += 1
            missing_counter.update(missing)
            core._finish_item(
                item,
                stage=core.AGENT1_OUTPUT_INVALID_STAGE,
                status="failed",
                output_ref=f"agent1_output_invalid:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    "reason": "agent1_contract_missing",
                    "missing": missing,
                    "partialPayload": payload,
                    "providerStatus": provider.get("providerStatus"),
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "runtimeSource": "artifactRefs.signalRef",
                    "legacySignalPoolRead": False,
                },
            )
            continue
        completed += 1
        by_family[str(payload.get("actionFamily") or "missing")] += 1
        core._finish_item(
            item,
            stage=core.AGENT1_COMPLETED_STAGE,
            status="ready",
            output_ref=f"pipeline_items.agent1_completed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
            payload=payload,
        )
    return {
        "version": AGENT1_ARTIFACT_RUNTIME_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
        "dynamicRagUsed": False,
        "dynamicRagNextStage": "action_pack_ready",
        "dataVersion": data_version,
        "claimedItemCount": len(items),
        "validSignalArtifactCount": len(valid_pairs),
        "completedItemCount": completed,
        "observedItemCount": observed_by_agent,
        "invalidItemCount": invalid,
        "failedItemCount": failed + len(missing_signal_items),
        "missingCounter": dict(missing_counter),
        "bySelectedActionFamily": dict(by_family),
        "agentJudgmentCount": len(judgments),
        "pendingItemCount": core.pending_agent1_item_count(data_version),
        "provider": provider,
        "pipelineItemSummary": pipeline_item_summary(data_version=data_version, limit=30),
        "runtimeSource": "artifactRefs.signalRef",
        "legacySignalPoolRead": False,
        "legacySignalPoolWrite": False,
        "rule": "Agent1 resolves one immutable signalRef per product and never touches Signal Pool.",
    }


def run_agent1_microbatch_loop_v226(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 8,
    max_batches: int = 20,
) -> Dict[str, Any]:
    from src.services import pipeline_agent1_microbatch_v20101_service as core
    from src.services.agent_runtime_contract_v2010_service import AGENT_RUNTIME_CONTRACT_VERSION
    from src.services.operating_policy_context_v2028_service import OPERATING_POLICY_CONTEXT_VERSION
    from src.services.pipeline_item_service import pipeline_item_summary

    batches: List[Dict[str, Any]] = []
    for _ in range(max(1, min(50, int(max_batches or 1)))):
        result = run_agent1_microbatch_v226(
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
        "version": AGENT1_ARTIFACT_RUNTIME_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
        "dataVersion": data_version,
        "microBatchCount": len(batches),
        "claimedItemCount": sum(int(item.get("claimedItemCount") or 0) for item in batches),
        "agentJudgmentCount": sum(int(item.get("agentJudgmentCount") or 0) for item in batches),
        "completedItemCount": sum(int(item.get("completedItemCount") or 0) for item in batches),
        "observedItemCount": sum(int(item.get("observedItemCount") or 0) for item in batches),
        "pendingItemCount": core.pending_agent1_item_count(data_version),
        "provider": provider,
        "batches": [
            {key: value for key, value in item.items() if key not in {"pipelineItemSummary", "batches"}}
            for item in batches
        ],
        "pipelineItemSummary": pipeline_item_summary(data_version=data_version, limit=50),
        "runtimeSource": "artifactRefs.signalRef",
        "legacySignalPoolRead": False,
        "rule": "Agent1 loop consumes only product signalRef artifacts.",
    }


def run_agent_pipeline_tick_v226(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    worker_id: str | None = None,
    agent1_batch_size: int = 8,
    action_pack_batch_size: int = 8,
    agent2_batch_size: int = 5,
    sop_batch_size: int = 8,
    pool_batch_size: int = 8,
    force_new_snapshot: bool = False,
) -> Dict[str, Any]:
    """Advance the nearest-to-completion stage, including Agent1 pending items."""
    from src.services import agent_pipeline_item_worker_v2010_service as worker
    from src.services.pipeline_action_microbatch_v205_service import (
        pending_agent2_item_count,
        run_agent2_microbatch_v205,
    )
    from src.services.pipeline_agent1_microbatch_v20101_service import pending_agent1_item_count
    from src.services.pipeline_sop_task_pool_v2010_service import (
        pending_sop_item_count,
        pending_task_pool_item_count,
        run_sop_mapping_microbatch_v206,
        run_task_pool_admission_microbatch_v207,
    )

    resolved = data_version or worker.latest_data_version()
    if not resolved:
        return {"version": END_TO_END_AGENT_FLOW_VERSION, "ran": False, "reason": "no_data_version"}
    recovery = worker.recover_version_only_action_pack_invalid(resolved)
    if pending_task_pool_item_count(resolved) > 0:
        result = run_task_pool_admission_microbatch_v207(
            data_version=resolved,
            user_id=user_id,
            batch_size=pool_batch_size,
            force_new_snapshot=force_new_snapshot,
        )
        selected = "sop_mapped_to_task_admitted"
    elif pending_sop_item_count(resolved) > 0:
        result = run_sop_mapping_microbatch_v206(
            data_version=resolved,
            user_id=user_id,
            batch_size=sop_batch_size,
        )
        selected = "agent2_completed_to_sop_mapped"
    elif pending_agent2_item_count(resolved) > 0:
        result = run_agent2_microbatch_v205(
            data_version=resolved,
            user_id=user_id,
            batch_size=agent2_batch_size,
        )
        selected = "action_pack_ready_to_agent2_completed"
    elif worker._load_agent1_completed_items(resolved, 1):
        result = worker.seed_action_pack_from_agent1_items(
            resolved,
            batch_size=action_pack_batch_size,
        )
        selected = "agent1_completed_to_action_pack_ready"
    elif pending_agent1_item_count(resolved) > 0:
        result = run_agent1_microbatch_v226(
            data_version=resolved,
            user_id=user_id,
            batch_size=agent1_batch_size,
        )
        selected = "agent1_pending_to_agent1_completed_or_observed"
    else:
        result = {
            "ran": False,
            "claimedItemCount": 0,
            "reason": "no_runnable_agent_pipeline_items",
        }
        selected = "idle"
    ran = bool(result.get("ran")) if "ran" in result else int(result.get("claimedItemCount") or 0) > 0
    return {
        "version": END_TO_END_AGENT_FLOW_VERSION,
        "contractVersion": END_TO_END_AGENT_FLOW_VERSION,
        "ran": ran,
        "workerId": worker_id,
        "selectedStage": selected,
        "dataVersion": resolved,
        "contractRecovery": recovery,
        "result": result,
        "agent1PendingHandled": selected == "agent1_pending_to_agent1_completed_or_observed",
        "runtimeSource": "pipeline_items.artifact_refs_json",
    }


def agent_pipeline_status_v226(data_version: str | None = None) -> Dict[str, Any]:
    from src.services import agent_pipeline_item_worker_v2010_service as worker
    from src.services.pipeline_action_microbatch_v205_service import pending_agent2_item_count
    from src.services.pipeline_agent1_microbatch_v20101_service import pending_agent1_item_count
    from src.services.pipeline_sop_task_pool_v2010_service import pending_sop_item_count, pending_task_pool_item_count

    resolved = data_version or worker.latest_data_version()
    return {
        "version": END_TO_END_AGENT_FLOW_VERSION,
        "contractVersion": END_TO_END_AGENT_FLOW_VERSION,
        "dataVersion": resolved,
        "stageCounts": worker._stage_counts(resolved),
        "pending": {
            "agent1PendingForJudgment": pending_agent1_item_count(resolved),
            "agent1CompletedForActionPack": len(worker._load_agent1_completed_items(resolved, 100000)),
            "actionPackReadyForAgent2": pending_agent2_item_count(resolved),
            "agent2CompletedForSop": pending_sop_item_count(resolved),
            "sopMappedForTaskPool": pending_task_pool_item_count(resolved),
        },
        "runtimeSource": "pipeline_items.artifact_refs_json",
        "artifactInputMode": "reference_only",
        "legacyPayloadRuntimeFallbackAllowed": False,
        "legacySignalPoolFallbackAllowed": False,
        "agent1PendingIsRunnable": True,
    }


def _product_identity(row: Dict[str, Any]) -> str:
    store = str(row.get("store_id") or "").strip()
    product = str(row.get("product_id") or "").strip()
    if product:
        return f"{store}::{product}" if store else product
    for key in ("package_id", "signal_id", "decision_id", "task_id", "item_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return f"{store}::{value}" if store else value
    return "unknown"


def _product_bucket(stage: str, status: Any) -> str:
    raw = str(status or "").lower()
    if raw in {"failed", "error"} or stage in _FAILED_STAGES:
        return "failed"
    if raw in {"running", "processing"} or stage in _RUNNING_STAGES:
        return "running"
    if stage in _COMPLETED_STAGES or raw in {"completed", "done", "passed", "observed"}:
        return "completed"
    return "queued"


def _product_rows(data_version: str | None) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT item_id,data_version,product_id,store_id,signal_id,package_id,
                   decision_id,task_id,current_stage,status,action_family,updated_at
            FROM pipeline_items
            WHERE COALESCE(data_version,'')=COALESCE(?,'')
            ORDER BY updated_at DESC
            """,
            (data_version,),
        ).fetchall()
    result: List[Dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if str(row.get("item_id") or "").startswith("PI-BATCH"):
            continue
        if str(row.get("current_stage") or "") == "signal_admission_completed":
            continue
        if row.get("product_id") or row.get("signal_id") or row.get("task_id"):
            result.append(row)
    return result


def _product_pipeline(data_version: str | None) -> Dict[str, Any]:
    rows = _product_rows(data_version)
    node_sets: Dict[str, Dict[str, Set[str]]] = {
        node: defaultdict(set) for node in _PRODUCT_NODE_ORDER
    }
    stage_counts: Counter[str] = Counter()
    active_items: List[Dict[str, Any]] = []
    product_ids: Set[str] = set()
    observed = admitted = agent1_pending = agent1_running = agent1_completed = 0
    task_admitted = failed = 0
    for row in rows:
        stage = str(row.get("current_stage") or "")
        node = _PRODUCT_STAGE_NODE.get(stage)
        if not node:
            continue
        identity = _product_identity(row)
        bucket = _product_bucket(stage, row.get("status"))
        product_ids.add(identity)
        node_sets[node]["total"].add(identity)
        node_sets[node][bucket].add(identity)
        stage_counts[f"{stage}:{bucket}"] += 1
        if stage == "observed_soft_gate":
            observed += 1
            node_sets[node]["observed"].add(identity)
        if stage in {"signal_admitted", "agent1_pending", "agent1_running", "agent1_completed"}:
            admitted += 1
            node_sets["信号引擎"]["admitted"].add(identity)
        if stage == "agent1_pending":
            agent1_pending += 1
        elif stage == "agent1_running":
            agent1_running += 1
        elif stage == "agent1_completed":
            agent1_completed += 1
        elif stage == "task_admitted" and row.get("task_id"):
            task_admitted += 1
        if bucket == "failed":
            failed += 1
        if bucket in {"queued", "running", "failed"}:
            active_items.append(
                {
                    "itemId": row.get("item_id"),
                    "productId": row.get("product_id"),
                    "storeId": row.get("store_id"),
                    "signalId": row.get("signal_id"),
                    "taskId": row.get("task_id"),
                    "identityKey": identity,
                    "title": row.get("product_id") or row.get("signal_id") or "商品包",
                    "kind": "任务" if row.get("task_id") else "商品包",
                    "node": node,
                    "currentStage": stage,
                    "stageLabel": _PRODUCT_STAGE_LABELS.get(stage, stage),
                    "status": row.get("status"),
                    "bucket": bucket,
                    "actionFamily": row.get("action_family") or "未锁定",
                    "updatedAt": row.get("updated_at"),
                }
            )
    stages: List[Dict[str, Any]] = []
    for node in _PRODUCT_NODE_ORDER:
        sets = node_sets[node]
        card = {
            "node": node,
            "label": node,
            "total": len(sets.get("total", set())),
            "queued": len(sets.get("queued", set())),
            "running": len(sets.get("running", set())),
            "completed": len(sets.get("completed", set())),
            "failed": len(sets.get("failed", set())),
            "observed": len(sets.get("observed", set())),
            "admitted": len(sets.get("admitted", set())),
            "countBasis": "product_identity_only",
        }
        card["currentCount"] = int(
            card["running"]
            or card["queued"]
            or card["failed"]
            or card["completed"]
            or card["observed"]
            or card["admitted"]
            or 0
        )
        card["status"] = (
            "attention"
            if card["failed"]
            else "running"
            if card["running"]
            else "queued"
            if card["queued"]
            else "completed"
            if card["completed"] or card["observed"] or card["admitted"]
            else "waiting"
        )
        stages.append(card)
    return {
        "totalItems": len(product_ids),
        "observed": observed,
        "admitted": admitted,
        "agent1Pending": agent1_pending,
        "agent1Running": agent1_running,
        "agent1Completed": agent1_completed,
        "taskAdmitted": task_admitted,
        "failed": failed,
        "stages": stages,
        "stageCounts": dict(stage_counts),
        "items": active_items,
    }


def _batch_stages(batch: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "stationId": item.get("stationId"),
            "label": item.get("label") or item.get("stationId"),
            "stage": item.get("stage"),
            "status": item.get("status"),
            "attemptCount": item.get("attemptCount"),
            "maxAttempts": item.get("maxAttempts"),
            "errorMessage": item.get("errorMessage"),
            "outputRef": item.get("outputRef"),
            "updatedAt": item.get("updatedAt"),
            "countBasis": "one_batch_station_state",
        }
        for item in (batch.get("stationJobs") or [])
        if item.get("status") != "disabled"
    ]


def read_pipeline_live_model_v226(
    data_version: str | None = None,
    *,
    limit: int = 80,
) -> Dict[str, Any]:
    if not callable(_ORIGINAL_PIPELINE_LIVE_READER):
        raise RuntimeError("pipeline_live_reader_not_bound")
    base = copy.deepcopy(_ORIGINAL_PIPELINE_LIVE_READER(data_version=data_version, limit=limit))
    resolved = data_version or base.get("dataVersion")
    product = _product_pipeline(resolved)
    batch = base.get("batchState") if isinstance(base.get("batchState"), dict) else {}
    batch_status = str(batch.get("status") or "")
    summary = dict(base.get("summary") or {})
    summary.update(
        totalItems=product["totalItems"],
        productCount=product["totalItems"],
        running=product["agent1Running"],
        queued=product["agent1Pending"],
        failed=product["failed"] + (1 if batch_status == "failed" else 0),
        observedDeposited=product["observed"],
        signalAdmitted=product["admitted"],
        agent1Pending=product["agent1Pending"],
        agent1Running=product["agent1Running"],
        agent1Completed=product["agent1Completed"],
        taskAdmitted=product["taskAdmitted"],
    )
    if batch_status == "failed":
        headline = base.get("headline") or f"批次在{batch.get('stationLabel') or '当前站点'}失败"
        flow_status = "attention"
        snapshot_status = "blocked"
    elif batch_status == "retry":
        headline = base.get("headline") or f"批次正从{batch.get('stationLabel') or '真实断点'}重试"
        flow_status = "running"
        snapshot_status = "replaying"
    elif product["agent1Pending"] or product["agent1Running"]:
        headline = (
            f"商品流水线运行中：{product['agent1Pending'] + product['agent1Running']}个进入Agent1，"
            f"{product['observed']}个观察沉淀"
        )
        flow_status = "running"
        snapshot_status = "running"
    elif product["taskAdmitted"]:
        headline = f"{product['taskAdmitted']}个任务已入池，观察沉淀{product['observed']}"
        flow_status = "completed"
        snapshot_status = "ready"
    elif product["failed"]:
        headline = f"{product['failed']}个商品在Agent链路失败"
        flow_status = "attention"
        snapshot_status = "blocked"
    elif product["totalItems"] and product["observed"] == product["totalItems"]:
        headline = f"商品准入完成：{product['observed']}个观察，0个进入Agent1"
        flow_status = "completed"
        snapshot_status = "ready"
    elif product["totalItems"]:
        headline = (
            f"商品处理完成：Agent1完成{product['agent1Completed']}，"
            f"观察沉淀{product['observed']}"
        )
        flow_status = "completed"
        snapshot_status = "ready"
    else:
        headline = base.get("headline") or "等待商品级信号"
        flow_status = base.get("flowStatus") or "waiting"
        snapshot_status = base.get("snapshotStatus") or "empty"
    base.update(
        version=PIPELINE_TRUTH_READ_MODEL_VERSION,
        headline=headline,
        flowStatus=flow_status,
        snapshotStatus=snapshot_status,
        summary=summary,
        batchStages=_batch_stages(batch),
        productStages=product["stages"],
        stages=product["stages"],
        stageCounts=product["stageCounts"],
        items=product["items"][: int(limit)],
        batchCountBasis="one_batch_station_state",
        productCountBasis="product_identity_only",
        mixedBatchAndProductCount=False,
        batchTokenAddedToProductCount=False,
        pipelineLayers={
            "batch": "pipeline_jobs+station_queue",
            "product": "pipeline_items columns+artifactRefs",
        },
        rule="Batch stations and product Agent items are displayed as separate truth layers.",
    )
    return base


def _binding_summary(*, idempotent: bool = False) -> Dict[str, Any]:
    return {
        "version": END_TO_END_AGENT_FLOW_VERSION,
        "bound": True,
        "idempotent": idempotent,
        "signalAdmissionPolicy": "evidence_trigger_for_agent1_score_for_priority_only",
        "agent1RuntimeSource": "artifactRefs.signalRef",
        "agent1PendingIsRunnable": True,
        "pipelineLiveLayers": ["batchStations", "productItems"],
        "legacySignalPoolRead": False,
        "legacySignalPoolWrite": False,
        "scoreCanBlockAgent1": False,
    }


def bind_end_to_end_agent_flow() -> Dict[str, Any]:
    global _BOUND, _ORIGINAL_PIPELINE_LIVE_READER
    if _BOUND:
        return _binding_summary(idempotent=True)

    from src.services import agent_pipeline_item_worker_v2010_service as pipeline_worker
    from src.services import artifact_signal_admission_v225_service as admission
    from src.services import pipeline_agent1_microbatch_v20101_service as agent1
    from src.services import pipeline_live_read_model_v208_service as live

    _ORIGINAL_PIPELINE_LIVE_READER = live.read_pipeline_live_model
    admission.product_signal_admission_station_v225 = product_signal_admission_station_v226
    admission.ARTIFACT_SIGNAL_ADMISSION_VERSION = SIGNAL_ADMISSION_POLICY_VERSION

    agent1.run_agent1_microbatch_v20101 = run_agent1_microbatch_v226
    agent1.run_agent1_microbatch_loop_v20101 = run_agent1_microbatch_loop_v226
    agent1.PIPELINE_AGENT1_MICROBATCH_VERSION = AGENT1_ARTIFACT_RUNTIME_VERSION

    pipeline_worker.run_agent_pipeline_tick = run_agent_pipeline_tick_v226
    pipeline_worker.agent_pipeline_status = agent_pipeline_status_v226
    pipeline_worker.AGENT_PIPELINE_ITEM_WORKER_VERSION = END_TO_END_AGENT_FLOW_VERSION

    live.read_pipeline_live_model = read_pipeline_live_model_v226
    live.PIPELINE_LIVE_READ_MODEL_VERSION = PIPELINE_TRUTH_READ_MODEL_VERSION

    route_module = sys.modules.get("src.api.routes.frontend_views")
    if route_module is not None:
        setattr(route_module, "read_pipeline_live_model", read_pipeline_live_model_v226)

    _BOUND = True
    return _binding_summary(idempotent=False)


__all__ = [
    "END_TO_END_AGENT_FLOW_VERSION",
    "SIGNAL_ADMISSION_POLICY_VERSION",
    "AGENT1_ARTIFACT_RUNTIME_VERSION",
    "PIPELINE_TRUTH_READ_MODEL_VERSION",
    "product_signal_admission_station_v226",
    "run_agent1_microbatch_v226",
    "run_agent1_microbatch_loop_v226",
    "run_agent_pipeline_tick_v226",
    "agent_pipeline_status_v226",
    "read_pipeline_live_model_v226",
    "bind_end_to_end_agent_flow",
]
