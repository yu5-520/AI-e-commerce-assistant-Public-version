"""V21.4.1 SOP and Task Pool pipeline-item workers.

SOP mapping consumes a ready Agent2 Plan IR with item-level provenance. Task
Pool consumes that same decision, computes operation authority and preserves
proof, Plan IR and authorization in lifecycle projections.

V21.7.6 restores the local mapping helper used by the successful SOP path and
keeps that path covered as a runtime contract instead of relying on compile-only
checks.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services.agent_runtime_contract_v2141_service import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    missing_agent2_contract,
    missing_sop_contract,
    normalize_sop_mapped_contract,
    normalize_task_admitted_contract,
    payload_from_row,
)
from src.services.pipeline_item_service import (
    build_item_envelope,
    ensure_pipeline_item_tables,
    pipeline_item_summary,
    record_pipeline_item_event,
    upsert_pipeline_item,
)
from src.services.sop_builder_core_v20_service import (
    SOP_BUILDER_CORE_VERSION,
    build_sop_decision_from_package,
    save_sop_decision,
)
from src.services.task_pool_admission_core_v20_service import (
    TASK_POOL_ADMISSION_CORE_VERSION,
    admit_decision_to_task_pool,
    refresh_task_pool_views,
)
from src.services.task_pool_lifecycle_sync_v2020_service import (
    sync_task_pool_entries_to_task_status,
)

PIPELINE_SOP_TASK_POOL_VERSION = "21.4.1"
SOP_MAPPING_RUNTIME_FIX_VERSION = "21.7.6"
SOP_READY_STAGE = "agent2_completed"
SOP_MAPPED_STAGE = "sop_mapped"
TASK_ADMITTED_STAGE = "task_admitted"
DEFAULT_SOP_BATCH_SIZE = 20
DEFAULT_POOL_BATCH_SIZE = 20


def _dict(value: Any) -> Dict[str, Any]:
    """Return a mapping without importing private helpers from another module."""
    return value if isinstance(value, dict) else {}


def _pending_items(
    data_version: str | None,
    stage: str,
    limit: int,
) -> List[Dict[str, Any]]:
    ensure_pipeline_item_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM pipeline_items
            WHERE COALESCE(data_version,'')=COALESCE(?,'')
              AND current_stage=?
              AND status IN ('queued','ready','retry')
            ORDER BY priority ASC,updated_at ASC
            LIMIT ?
            """,
            (data_version, stage, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def pending_sop_item_count(data_version: str | None) -> int:
    return len(_pending_items(data_version, SOP_READY_STAGE, 100000))


def pending_task_pool_item_count(data_version: str | None) -> int:
    return len(_pending_items(data_version, SOP_MAPPED_STAGE, 100000))


def seed_sop_items_from_agent2_plans(
    data_version: str | None,
    *,
    source: str = "legacy_agent2_plan_seed_disabled",
) -> Dict[str, Any]:
    return {
        "version": PIPELINE_SOP_TASK_POOL_VERSION,
        "runtimeFixVersion": SOP_MAPPING_RUNTIME_FIX_VERSION,
        "dataVersion": data_version,
        "seededSopReadyItemCount": 0,
        "disabled": True,
        "source": source,
        "rule": "V21.4.1 reads pipeline_items.agent2_completed only.",
    }


def _finish_item(
    item: Dict[str, Any],
    *,
    stage: str,
    status: str,
    output_ref: str,
    payload: Dict[str, Any],
    decision_id: str | None = None,
    task_id: str | None = None,
    station_id: str | None = None,
) -> Dict[str, Any]:
    envelope = build_item_envelope(
        data_version=item.get("data_version"),
        item_id=item.get("item_id"),
        product_id=item.get("product_id") or payload.get("productId"),
        store_id=item.get("store_id") or payload.get("storeId"),
        signal_id=item.get("signal_id") or payload.get("signalId"),
        package_id=item.get("package_id") or payload.get("packageId"),
        decision_id=(
            decision_id
            or item.get("decision_id")
            or payload.get("decisionId")
        ),
        task_id=task_id or item.get("task_id") or payload.get("taskId"),
        action_family=(
            item.get("action_family")
            or payload.get("actionFamily")
        ),
        route=item.get("route") or payload.get("route"),
        input_ref=f"pipeline_item:{item.get('item_id')}",
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
        station_id=(
            station_id
            or (
                "task_mapping_agent_station"
                if stage == SOP_MAPPED_STAGE
                else "task_pool_admission_station"
            )
        ),
        stage=stage,
        status=status,
        output_ref=output_ref,
        payload=payload,
    )
    return envelope


def run_sop_mapping_microbatch_v206(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = DEFAULT_SOP_BATCH_SIZE,
) -> Dict[str, Any]:
    del user_id
    items = _pending_items(
        data_version,
        SOP_READY_STAGE,
        max(1, min(50, int(batch_size or DEFAULT_SOP_BATCH_SIZE))),
    )
    if not items:
        return {
            "version": SOP_BUILDER_CORE_VERSION,
            "runtimeVersion": PIPELINE_SOP_TASK_POOL_VERSION,
            "runtimeFixVersion": SOP_MAPPING_RUNTIME_FIX_VERSION,
            "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
            "dataVersion": data_version,
            "claimedItemCount": 0,
            "taskDecisionCount": 0,
            "failedItemCount": 0,
            "pendingItemCount": pending_sop_item_count(data_version),
        }

    decisions: List[Dict[str, Any]] = []
    failed = 0
    by_family: Counter[str] = Counter()
    by_failure: Counter[str] = Counter()

    for item in items:
        package = payload_from_row(item)
        package.setdefault("dataVersion", item.get("data_version"))
        package.setdefault("itemId", item.get("item_id"))
        package.setdefault(
            "packageId",
            item.get("package_id")
            or package.get("packageId")
            or item.get("item_id"),
        )
        package.setdefault(
            "productId",
            item.get("product_id") or package.get("productId"),
        )
        package.setdefault(
            "storeId",
            item.get("store_id") or package.get("storeId"),
        )
        package.setdefault(
            "actionFamily",
            item.get("action_family") or package.get("actionFamily"),
        )

        missing = missing_agent2_contract(package)
        if missing:
            failed += 1
            by_failure["missing_agent2_contract"] += 1
            _finish_item(
                item,
                stage=SOP_MAPPED_STAGE,
                status="failed",
                output_ref=(
                    f"sop_mapping_failed:{data_version or 'latest'}:"
                    f"{item.get('item_id')}"
                ),
                payload={
                    **package,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "reason": "missing_or_unready_agent2_contract",
                    "missing": missing,
                    "failureOwner": "agent2_action_plan_station",
                    "frontendFailureLabel": "Agent2方案或调用证明不完整",
                    "taskAdmissionAllowed": False,
                },
            )
            continue

        decision = build_sop_decision_from_package(
            package,
            data_version,
            pipeline_item_id=item.get("item_id"),
        )
        if not decision:
            failed += 1
            by_failure["sop_builder_rejected_agent2_payload"] += 1
            _finish_item(
                item,
                stage=SOP_MAPPED_STAGE,
                status="failed",
                output_ref=(
                    f"sop_mapping_failed:{data_version or 'latest'}:"
                    f"{item.get('item_id')}"
                ),
                payload={
                    **package,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "reason": "sop_builder_rejected_agent2_payload",
                    "failureOwner": "sop_builder_station",
                    "frontendFailureLabel": "SOP编译失败",
                    "taskAdmissionAllowed": False,
                },
            )
            continue

        save_sop_decision(decision)
        payload = normalize_sop_mapped_contract(package, decision)
        task_plan = _dict(decision.get("taskPlan"))
        family = str(
            task_plan.get("selectedActionFamily")
            or item.get("action_family")
            or payload.get("actionFamily")
            or "missing"
        )
        by_family[family] += 1
        decisions.append(decision)
        _finish_item(
            item,
            stage=SOP_MAPPED_STAGE,
            status="queued",
            output_ref=(
                f"task_generation_decision:{data_version or 'latest'}:"
                f"{payload.get('decisionId')}"
            ),
            decision_id=payload.get("decisionId"),
            payload=payload,
        )

    return {
        "version": SOP_BUILDER_CORE_VERSION,
        "runtimeVersion": PIPELINE_SOP_TASK_POOL_VERSION,
        "runtimeFixVersion": SOP_MAPPING_RUNTIME_FIX_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": data_version,
        "claimedItemCount": len(items),
        "taskDecisionCount": len(decisions),
        "failedItemCount": failed,
        "bySelectedActionFamily": dict(by_family),
        "byFailureReason": dict(by_failure),
        "pendingItemCount": pending_sop_item_count(data_version),
        "pipelineItemSummary": pipeline_item_summary(
            data_version=data_version,
            limit=50,
        ),
        "legacyDecisionTableWritten": False,
        "rule": (
            "One valid Agent2 Plan IR and item proof becomes one deterministic "
            "SOP decision."
        ),
    }


def run_sop_mapping_microbatch_loop_v206(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = DEFAULT_SOP_BATCH_SIZE,
    max_batches: int = 20,
) -> Dict[str, Any]:
    batches: List[Dict[str, Any]] = []
    for _ in range(max(1, min(50, int(max_batches or 1)))):
        result = run_sop_mapping_microbatch_v206(
            data_version=data_version,
            user_id=user_id,
            batch_size=batch_size,
        )
        if int(result.get("claimedItemCount") or 0) <= 0:
            break
        batches.append(result)
        if int(result.get("pendingItemCount") or 0) <= 0:
            break
    return {
        "version": SOP_BUILDER_CORE_VERSION,
        "runtimeVersion": PIPELINE_SOP_TASK_POOL_VERSION,
        "runtimeFixVersion": SOP_MAPPING_RUNTIME_FIX_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": data_version,
        "microBatchCount": len(batches),
        "claimedItemCount": sum(
            int(result.get("claimedItemCount") or 0)
            for result in batches
        ),
        "taskDecisionCount": sum(
            int(result.get("taskDecisionCount") or 0)
            for result in batches
        ),
        "failedItemCount": sum(
            int(result.get("failedItemCount") or 0)
            for result in batches
        ),
        "pendingItemCount": pending_sop_item_count(data_version),
        "batches": [
            {
                key: value
                for key, value in result.items()
                if key not in {"pipelineItemSummary", "batches"}
            }
            for result in batches
        ],
        "pipelineItemSummary": pipeline_item_summary(
            data_version=data_version,
            limit=50,
        ),
    }


def task_mapping_agent_station_v206(
    data_version: str | None,
    *,
    user_id: str | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    stream_mode = bool(kwargs.get("pipeline_stream_mode"))
    result = run_sop_mapping_microbatch_loop_v206(
        data_version=data_version,
        user_id=kwargs.get("userId") or kwargs.get("user_id") or user_id,
        batch_size=int(
            kwargs.get("sopMicroBatchSize")
            or kwargs.get("micro_batch_size")
            or DEFAULT_SOP_BATCH_SIZE
        ),
        max_batches=(
            1
            if stream_mode
            else int(
                kwargs.get("maxSopMicroBatches")
                or kwargs.get("max_micro_batches")
                or 20
            )
        ),
    )
    result.update(
        {
            "version": SOP_BUILDER_CORE_VERSION,
            "runtimeFixVersion": SOP_MAPPING_RUNTIME_FIX_VERSION,
            "stationId": "task_mapping_agent_station",
            "formalTaskDecisionCount": int(
                result.get("taskDecisionCount") or 0
            ),
            "taskMappingProviderStatus": (
                "deterministic_v21_4_1_plan_ir_and_provenance_compiler"
            ),
            "taskGenerationDecisionRef": (
                f"task_generation_decision_v2141:{data_version or 'latest'}"
            ),
            "outputRef": (
                f"task_generation_decision_v2141:{data_version or 'latest'}"
            ),
        }
    )
    return result


def run_task_pool_admission_microbatch_v207(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = DEFAULT_POOL_BATCH_SIZE,
    force_new_snapshot: bool = False,
) -> Dict[str, Any]:
    items = _pending_items(
        data_version,
        SOP_MAPPED_STAGE,
        max(1, min(50, int(batch_size or DEFAULT_POOL_BATCH_SIZE))),
    )
    if not items:
        sync = sync_task_pool_entries_to_task_status(data_version=data_version)
        return {
            "version": TASK_POOL_ADMISSION_CORE_VERSION,
            "runtimeVersion": PIPELINE_SOP_TASK_POOL_VERSION,
            "runtimeFixVersion": SOP_MAPPING_RUNTIME_FIX_VERSION,
            "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
            "dataVersion": data_version,
            "claimedItemCount": 0,
            "createdTaskCount": 0,
            "lifecycleSync": sync,
            "pendingItemCount": pending_task_pool_item_count(data_version),
        }

    results: List[Dict[str, Any]] = []
    for item in items:
        package = payload_from_row(item)
        decision = (
            package.get("sopDecision")
            if isinstance(package.get("sopDecision"), dict)
            else {}
        )
        missing = missing_sop_contract(package)
        result = (
            {
                "ok": False,
                "status": "missing_sop_contract",
                "createdTaskCount": 0,
                "decisionId": package.get("decisionId"),
                "packageId": package.get("packageId"),
                "reason": "missing_or_unready_sop_contract",
                "missing": missing,
            }
            if missing
            else admit_decision_to_task_pool(
                decision,
                created_by=user_id,
                force_new_snapshot=force_new_snapshot,
            )
        )
        results.append(result)
        status = str(result.get("status") or "unknown")
        success = bool(result.get("ok")) or status == "entered_task_pool"
        payload = (
            normalize_task_admitted_contract(package, result)
            if success
            else {
                **package,
                "taskAdmission": result,
                "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                "reason": result.get("reason") or status,
                "missing": result.get("missing") or [],
                "failureOwner": "task_pool_admission_station",
                "frontendFailureLabel": "任务入池合同未通过",
                "taskAdmissionAllowed": False,
            }
        )
        _finish_item(
            item,
            stage=TASK_ADMITTED_STAGE,
            status="completed" if success else "failed",
            output_ref=(
                f"task_pool:{data_version or 'latest'}:"
                f"{result.get('decisionId') or item.get('decision_id')}"
            ),
            decision_id=result.get("decisionId") or item.get("decision_id"),
            task_id=result.get("taskId"),
            payload=payload,
        )

    lifecycle_sync = sync_task_pool_entries_to_task_status(
        data_version=data_version
    )
    try:
        refresh = refresh_task_pool_views(data_version)
    except Exception as exc:
        refresh = {
            "status": "refresh_failed",
            "error": str(exc),
        }

    return {
        "version": TASK_POOL_ADMISSION_CORE_VERSION,
        "runtimeVersion": PIPELINE_SOP_TASK_POOL_VERSION,
        "runtimeFixVersion": SOP_MAPPING_RUNTIME_FIX_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": data_version,
        "claimedItemCount": len(items),
        "createdTaskCount": sum(
            int(result.get("createdTaskCount") or 0)
            for result in results
        ),
        "failedItemCount": sum(
            1
            for result in results
            if not (
                bool(result.get("ok"))
                or str(result.get("status")) == "entered_task_pool"
            )
        ),
        "lifecycleSync": lifecycle_sync,
        "results": results[:50],
        "byAdmissionStatus": dict(
            Counter(str(result.get("status")) for result in results)
        ),
        "pendingItemCount": pending_task_pool_item_count(data_version),
        "frontendRefresh": refresh,
        "pipelineItemSummary": pipeline_item_summary(
            data_version=data_version,
            limit=50,
        ),
        "legacyBridgeUsed": False,
        "rule": (
            "Task Pool consumes the same Plan IR and item proof, then applies "
            "operation authority."
        ),
    }


def run_task_pool_admission_microbatch_loop_v207(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = DEFAULT_POOL_BATCH_SIZE,
    max_batches: int = 20,
    force_new_snapshot: bool = False,
) -> Dict[str, Any]:
    batches: List[Dict[str, Any]] = []
    for _ in range(max(1, min(50, int(max_batches or 1)))):
        result = run_task_pool_admission_microbatch_v207(
            data_version=data_version,
            user_id=user_id,
            batch_size=batch_size,
            force_new_snapshot=force_new_snapshot,
        )
        if int(result.get("claimedItemCount") or 0) <= 0:
            break
        batches.append(result)
        if int(result.get("pendingItemCount") or 0) <= 0:
            break
    return {
        "version": TASK_POOL_ADMISSION_CORE_VERSION,
        "runtimeVersion": PIPELINE_SOP_TASK_POOL_VERSION,
        "runtimeFixVersion": SOP_MAPPING_RUNTIME_FIX_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": data_version,
        "microBatchCount": len(batches),
        "claimedItemCount": sum(
            int(result.get("claimedItemCount") or 0)
            for result in batches
        ),
        "createdTaskCount": sum(
            int(result.get("createdTaskCount") or 0)
            for result in batches
        ),
        "failedItemCount": sum(
            int(result.get("failedItemCount") or 0)
            for result in batches
        ),
        "pendingItemCount": pending_task_pool_item_count(data_version),
        "batches": [
            {
                key: value
                for key, value in result.items()
                if key not in {
                    "pipelineItemSummary",
                    "batches",
                    "results",
                }
            }
            for result in batches
        ],
        "pipelineItemSummary": pipeline_item_summary(
            data_version=data_version,
            limit=50,
        ),
    }


def task_pool_admission_station_v207(
    data_version: str | None,
    *,
    user_id: str | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    stream_mode = bool(kwargs.get("pipeline_stream_mode"))
    result = run_task_pool_admission_microbatch_loop_v207(
        data_version=data_version,
        user_id=kwargs.get("userId") or kwargs.get("user_id") or user_id,
        batch_size=int(
            kwargs.get("poolMicroBatchSize")
            or kwargs.get("micro_batch_size")
            or DEFAULT_POOL_BATCH_SIZE
        ),
        max_batches=(
            1
            if stream_mode
            else int(
                kwargs.get("maxPoolMicroBatches")
                or kwargs.get("max_micro_batches")
                or 20
            )
        ),
        force_new_snapshot=bool(
            kwargs.get("forceNewSnapshot")
            or kwargs.get("force_new_snapshot")
        ),
    )
    result.update(
        {
            "version": TASK_POOL_ADMISSION_CORE_VERSION,
            "runtimeFixVersion": SOP_MAPPING_RUNTIME_FIX_VERSION,
            "stationId": "task_pool_admission_station",
            "taskPoolRef": f"task_pool_v2141:{data_version or 'latest'}",
            "outputRef": f"task_pool_v2141:{data_version or 'latest'}",
        }
    )
    return result
