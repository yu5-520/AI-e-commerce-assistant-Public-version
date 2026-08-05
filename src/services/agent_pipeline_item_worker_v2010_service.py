"""V20.28 unified Agent pipeline-item worker.

The worker advances one current pipeline_items payload through Action Pack,
real Agent2, SOP structuring and Task Pool admission. It also repairs only the
narrow class of V20.27->V20.28 Action Pack failures whose business payload was
already valid and whose missing fields were version-string gates.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services.action_pack_core_v20_service import (
    ACTION_PACK_CORE_VERSION,
    enrich_package_with_action_parameters,
    select_action_parameter_pack,
)
from src.services.agent_runtime_contract_v2010_service import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    missing_agent1_contract,
    missing_action_pack_contract,
    normalize_action_pack_ready_contract,
    payload_from_row,
)
from src.services.pipeline_action_microbatch_v205_service import (
    ACTION_PACK_INVALID_STAGE,
    ACTION_PACK_READY_STAGE,
    pending_agent2_item_count,
    run_agent2_microbatch_v205,
)
from src.services.pipeline_item_service import (
    build_item_envelope,
    ensure_pipeline_item_tables,
    pipeline_item_summary,
    record_pipeline_item_event,
    upsert_pipeline_item,
)
from src.services.pipeline_sop_task_pool_v2010_service import (
    pending_sop_item_count,
    pending_task_pool_item_count,
    run_sop_mapping_microbatch_v206,
    run_task_pool_admission_microbatch_v207,
)
from src.services.route_action_department_matrix_v1915_service import (
    MATRIX_DISPATCH_VERSION,
    attach_matrix_dispatch,
    selected_family,
)

AGENT_PIPELINE_ITEM_WORKER_VERSION = "20.28"
AGENT1_COMPLETED_STAGE = "agent1_completed"
AGENT1_OUTPUT_INVALID_STAGE = "agent1_output_invalid"
DEFAULT_ACTION_PACK_BATCH_SIZE = 8
DEFAULT_AGENT2_BATCH_SIZE = 5
DEFAULT_SOP_BATCH_SIZE = 8
DEFAULT_POOL_BATCH_SIZE = 8

_VERSION_ONLY_ACTION_PACK_FAILURES = {
    "actionParameterPack.version_20_27",
    "actionPackCoreVersion_20_27",
    "matrixDispatch.version_20_27",
    "actionParameterPack.version_20_28",
    "actionPackCoreVersion_20_28",
    "matrixDispatch.version_20_28",
}
_DOWNSTREAM_REBUILD_FIELDS = {
    "actionParameterPack",
    "actionParameterPacks",
    "actionPackStatus",
    "actionPackCoreVersion",
    "responsibilityContractVersion",
    "ragContextSnapshot",
    "ragContextSummary",
    "ragRetrievalCount",
    "dynamicRagStatus",
    "agent2ActionPlan",
    "agent2Provider",
    "agent2Source",
    "plan",
    "sopDecision",
    "taskAdmission",
    "decisionId",
    "taskId",
    "reason",
    "blockedReason",
    "missing",
    "failureOwner",
    "frontendFailureLabel",
    "taskAdmissionAllowed",
}


def _table(conn: Any, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    )


def _row_get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        return row.get(key) if isinstance(row, dict) else None


def latest_data_version() -> str | None:
    ensure_pipeline_item_tables()
    with connect() as conn:
        if not _table(conn, "pipeline_items"):
            return None
        row = conn.execute(
            """
            SELECT data_version
            FROM pipeline_items
            WHERE data_version IS NOT NULL AND data_version!=''
            GROUP BY data_version
            ORDER BY MAX(updated_at) DESC
            LIMIT 1
            """
        ).fetchone()
    return str(row["data_version"]) if row and row["data_version"] else None


def _stage_counts(data_version: str | None) -> Dict[str, int]:
    ensure_pipeline_item_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT current_stage,status,COUNT(*) AS cnt
            FROM pipeline_items
            WHERE COALESCE(data_version,'')=COALESCE(?,'')
            GROUP BY current_stage,status
            ORDER BY current_stage,status
            """,
            (data_version,),
        ).fetchall()
    return {
        f"{row['current_stage']}:{row['status']}": int(row["cnt"] or 0)
        for row in rows
    }


def _load_agent1_completed_items(data_version: str | None, limit: int) -> List[Any]:
    if not data_version:
        return []
    ensure_pipeline_item_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM pipeline_items
            WHERE data_version=?
              AND current_stage=?
              AND status IN ('ready','completed','retry')
            ORDER BY priority ASC, updated_at ASC
            LIMIT ?
            """,
            (
                data_version,
                AGENT1_COMPLETED_STAGE,
                max(1, min(100, int(limit or DEFAULT_ACTION_PACK_BATCH_SIZE))),
            ),
        ).fetchall()
    return list(rows)


def _finish_invalid(
    row: Any,
    stage: str,
    reason: str,
    missing: List[str],
    partial: Dict[str, Any],
) -> None:
    payload = {
        **partial,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "source": "agent_pipeline_item_worker_v20_28",
        "reason": reason,
        "missing": missing,
        "fallbackAllowed": False,
        "taskAdmissionAllowed": False,
    }
    envelope = build_item_envelope(
        data_version=_row_get(row, "data_version"),
        item_id=_row_get(row, "item_id"),
        product_id=_row_get(row, "product_id") or partial.get("productId"),
        store_id=_row_get(row, "store_id") or partial.get("storeId"),
        signal_id=_row_get(row, "signal_id") or partial.get("signalId"),
        package_id=_row_get(row, "package_id") or partial.get("packageId"),
        action_family=_row_get(row, "action_family")
        or partial.get("actionFamily"),
        route=_row_get(row, "route") or partial.get("route"),
        input_ref=_row_get(row, "output_ref"),
        output_ref=f"{stage}:{_row_get(row, 'item_id')}",
        stage=stage,
    )
    envelope = upsert_pipeline_item(
        envelope,
        stage=stage,
        status="failed",
        priority=1,
        output_ref=envelope.get("outputRef"),
        payload=payload,
    )
    record_pipeline_item_event(
        envelope,
        station_id="action_parameter_enrichment_station",
        stage=stage,
        status="failed",
        input_ref=_row_get(row, "output_ref"),
        output_ref=envelope.get("outputRef"),
        payload=payload,
    )


def _priority(package: Dict[str, Any]) -> int:
    agent1 = (
        package.get("agent1OperatingJudgment")
        if isinstance(package.get("agent1OperatingJudgment"), dict)
        else {}
    )
    try:
        confidence = float(package.get("confidence") or agent1.get("confidence") or 0.5)
    except Exception:
        confidence = 0.5
    return max(1, min(100, int(round(100 - confidence * 100))))


def _clean_for_action_pack_retry(package: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {
        key: value
        for key, value in package.items()
        if key not in _DOWNSTREAM_REBUILD_FIELDS
    }
    lineage = (
        cleaned.get("lineage") if isinstance(cleaned.get("lineage"), dict) else {}
    )
    completed = [
        stage
        for stage in list(lineage.get("completedStages") or [])
        if stage == AGENT1_COMPLETED_STAGE
    ]
    if AGENT1_COMPLETED_STAGE not in completed:
        completed.append(AGENT1_COMPLETED_STAGE)
    cleaned.update(
        version=AGENT_RUNTIME_CONTRACT_VERSION,
        contractVersion=AGENT_RUNTIME_CONTRACT_VERSION,
        outputContract="V20.28.agent1_completed",
        fallbackAllowed=False,
        lineage={
            **lineage,
            "currentStage": AGENT1_COMPLETED_STAGE,
            "completedStages": completed,
            "source": "pipeline_items.payload_only",
        },
        contractRecovery={
            "version": AGENT_PIPELINE_ITEM_WORKER_VERSION,
            "reason": "requeued_after_v20_27_version_only_action_pack_gate",
            "businessPayloadRecomputed": False,
            "actionPackWillBeRebuilt": True,
        },
    )
    return cleaned


def recover_version_only_action_pack_invalid(
    data_version: str | None = None,
    *,
    limit: int = 100,
) -> Dict[str, Any]:
    """Requeue only proven version-gate failures; never revive business failures."""
    resolved = data_version or latest_data_version()
    if not resolved:
        return {
            "version": AGENT_PIPELINE_ITEM_WORKER_VERSION,
            "dataVersion": None,
            "recoveredItemCount": 0,
        }
    ensure_pipeline_item_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM pipeline_items
            WHERE data_version=?
              AND current_stage=?
              AND status='failed'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (resolved, ACTION_PACK_INVALID_STAGE, max(1, min(500, int(limit)))),
        ).fetchall()

    recovered = 0
    skipped_business_failure = 0
    for row in rows:
        package = dict(payload_from_row(row))
        reason = str(package.get("reason") or "")
        missing = package.get("missing") if isinstance(package.get("missing"), list) else []
        missing_set = {str(item) for item in missing if str(item).strip()}
        if (
            reason != "missing_or_unready_action_pack_contract"
            or not missing_set
            or not missing_set.issubset(_VERSION_ONLY_ACTION_PACK_FAILURES)
            or missing_agent1_contract(package)
        ):
            skipped_business_failure += 1
            continue

        payload = _clean_for_action_pack_retry(package)
        item_id = _row_get(row, "item_id")
        envelope = build_item_envelope(
            data_version=resolved,
            item_id=item_id,
            product_id=_row_get(row, "product_id") or payload.get("productId"),
            store_id=_row_get(row, "store_id") or payload.get("storeId"),
            signal_id=_row_get(row, "signal_id") or payload.get("signalId"),
            package_id=_row_get(row, "package_id") or payload.get("packageId"),
            action_family=_row_get(row, "action_family")
            or payload.get("actionFamily"),
            route=_row_get(row, "route") or payload.get("route"),
            input_ref=_row_get(row, "output_ref"),
            output_ref=f"pipeline_items.agent1_completed:recovered:{item_id}",
            stage=AGENT1_COMPLETED_STAGE,
        )
        envelope = upsert_pipeline_item(
            envelope,
            stage=AGENT1_COMPLETED_STAGE,
            status="retry",
            priority=int(_row_get(row, "priority") or 1),
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        record_pipeline_item_event(
            envelope,
            station_id="agent_contract_recovery_v2028",
            stage=AGENT1_COMPLETED_STAGE,
            status="retry",
            input_ref=_row_get(row, "output_ref"),
            output_ref=envelope.get("outputRef"),
            payload={
                **payload,
                "recoveredMissing": sorted(missing_set),
            },
        )
        recovered += 1

    return {
        "version": AGENT_PIPELINE_ITEM_WORKER_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": resolved,
        "recoveredItemCount": recovered,
        "skippedBusinessFailureCount": skipped_business_failure,
        "rule": "Only version-string-only Action Pack failures are requeued; business and semantic failures stay failed.",
    }


def seed_action_pack_from_agent1_items(
    data_version: str | None = None,
    *,
    batch_size: int = DEFAULT_ACTION_PACK_BATCH_SIZE,
    source: str = "agent_pipeline_item_worker_v20_28",
) -> Dict[str, Any]:
    resolved = data_version or latest_data_version()
    rows = _load_agent1_completed_items(resolved, batch_size)
    if not rows:
        return {
            "version": AGENT_PIPELINE_ITEM_WORKER_VERSION,
            "stage": "agent1_completed_to_action_pack_ready",
            "dataVersion": resolved,
            "ran": False,
            "claimedItemCount": 0,
            "createdItemCount": 0,
            "rule": "No Agent1-completed items are waiting for Action Pack.",
        }
    created = invalid_agent1 = invalid_pack = 0
    by_family: Counter[str] = Counter()
    missing_counter: Counter[str] = Counter()
    for row in rows:
        package = dict(payload_from_row(row))
        package.setdefault("dataVersion", _row_get(row, "data_version"))
        package.setdefault("itemId", _row_get(row, "item_id"))
        package.setdefault("productId", _row_get(row, "product_id"))
        package.setdefault("storeId", _row_get(row, "store_id"))
        package.setdefault("signalId", _row_get(row, "signal_id"))
        package["contractVersion"] = AGENT_RUNTIME_CONTRACT_VERSION
        missing_agent1 = missing_agent1_contract(package)
        if missing_agent1:
            invalid_agent1 += 1
            missing_counter.update(missing_agent1)
            _finish_invalid(
                row,
                AGENT1_OUTPUT_INVALID_STAGE,
                "missing_agent1_completed_contract",
                missing_agent1,
                package,
            )
            continue
        item = attach_matrix_dispatch(package)
        family = selected_family(item)
        item = enrich_package_with_action_parameters(item)
        pack = select_action_parameter_pack(item, family)
        item = normalize_action_pack_ready_contract(item, pack)
        missing_pack = missing_action_pack_contract(item)
        if missing_pack:
            invalid_pack += 1
            missing_counter.update(missing_pack)
            _finish_invalid(
                row,
                ACTION_PACK_INVALID_STAGE,
                "missing_or_unready_action_pack_contract",
                missing_pack,
                item,
            )
            continue
        by_family[family] += 1
        envelope = build_item_envelope(
            data_version=resolved,
            item_id=_row_get(row, "item_id"),
            product_id=item.get("productId"),
            store_id=item.get("storeId"),
            signal_id=item.get("signalId"),
            package_id=item.get("packageId"),
            action_family=family,
            route=item.get("route"),
            input_ref=f"pipeline_items.agent1_completed:{_row_get(row, 'item_id')}",
            output_ref=f"pipeline_items.action_pack_ready:{_row_get(row, 'item_id')}",
            stage=ACTION_PACK_READY_STAGE,
        )
        payload = {
            **item,
            "source": source,
            "inputSource": "pipeline_items.agent1_completed_only",
            "version": AGENT_PIPELINE_ITEM_WORKER_VERSION,
            "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
            "outputContract": "V20.28.action_pack_ready",
            "legacyRuntimeSourceUsed": False,
        }
        envelope = upsert_pipeline_item(
            envelope,
            stage=ACTION_PACK_READY_STAGE,
            status="ready",
            priority=_priority(item),
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        record_pipeline_item_event(
            envelope,
            station_id="action_parameter_enrichment_station",
            stage=ACTION_PACK_READY_STAGE,
            status="ready",
            input_ref=envelope.get("inputRef"),
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        created += 1
    return {
        "version": AGENT_PIPELINE_ITEM_WORKER_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "matrixDispatchVersion": MATRIX_DISPATCH_VERSION,
        "stage": "agent1_completed_to_action_pack_ready",
        "dataVersion": resolved,
        "ran": created > 0 or invalid_agent1 > 0 or invalid_pack > 0,
        "claimedItemCount": len(rows),
        "createdItemCount": created,
        "invalidAgent1ItemCount": invalid_agent1,
        "invalidActionPackItemCount": invalid_pack,
        "missingCounter": dict(missing_counter),
        "byActionFamily": dict(by_family),
        "actionPackCoreVersion": ACTION_PACK_CORE_VERSION,
        "pipelineItemSummary": pipeline_item_summary(data_version=resolved, limit=60),
        "rule": "V20.28 Action Pack consumes current Agent1 payloads and validates semantic readiness rather than cross-module version equality.",
    }


def run_agent_pipeline_tick(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    worker_id: str | None = None,
    action_pack_batch_size: int = DEFAULT_ACTION_PACK_BATCH_SIZE,
    agent2_batch_size: int = DEFAULT_AGENT2_BATCH_SIZE,
    sop_batch_size: int = DEFAULT_SOP_BATCH_SIZE,
    pool_batch_size: int = DEFAULT_POOL_BATCH_SIZE,
    force_new_snapshot: bool = False,
) -> Dict[str, Any]:
    resolved = data_version or latest_data_version()
    if not resolved:
        return {
            "version": AGENT_PIPELINE_ITEM_WORKER_VERSION,
            "ran": False,
            "reason": "no_data_version",
        }

    recovery = recover_version_only_action_pack_invalid(resolved)
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
    else:
        result = seed_action_pack_from_agent1_items(
            resolved,
            batch_size=action_pack_batch_size,
        )
        selected = "agent1_completed_to_action_pack_ready"
    return {
        "version": AGENT_PIPELINE_ITEM_WORKER_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "ran": bool(result.get("ran"))
        if "ran" in result
        else int(result.get("claimedItemCount") or 0) > 0,
        "workerId": worker_id,
        "selectedStage": selected,
        "dataVersion": resolved,
        "contractRecovery": recovery,
        "result": result,
    }


def run_agent_pipeline_loop(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    max_ticks: int = 40,
    action_pack_batch_size: int = DEFAULT_ACTION_PACK_BATCH_SIZE,
    agent2_batch_size: int = DEFAULT_AGENT2_BATCH_SIZE,
    sop_batch_size: int = DEFAULT_SOP_BATCH_SIZE,
    pool_batch_size: int = DEFAULT_POOL_BATCH_SIZE,
    force_new_snapshot: bool = False,
) -> Dict[str, Any]:
    resolved = data_version or latest_data_version()
    ticks: List[Dict[str, Any]] = []
    for _ in range(max(1, min(100, int(max_ticks or 1)))):
        tick = run_agent_pipeline_tick(
            resolved,
            user_id=user_id,
            worker_id="manual-loop",
            action_pack_batch_size=action_pack_batch_size,
            agent2_batch_size=agent2_batch_size,
            sop_batch_size=sop_batch_size,
            pool_batch_size=pool_batch_size,
            force_new_snapshot=force_new_snapshot,
        )
        ticks.append(
            {key: value for key, value in tick.items() if key != "result"}
            | {
                "resultSummary": {
                    key: value
                    for key, value in (tick.get("result") or {}).items()
                    if key not in {"pipelineItemSummary", "batches", "results"}
                }
            }
        )
        if not tick.get("ran"):
            break
    return {
        "version": AGENT_PIPELINE_ITEM_WORKER_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": resolved,
        "tickCount": len(ticks),
        "ranCount": sum(1 for tick in ticks if tick.get("ran")),
        "ticks": ticks,
        "status": agent_pipeline_status(resolved),
        "rule": "V20.28 manual loop uses one semantic pipeline-items protocol from Agent1 through Task Pool.",
    }


def agent_pipeline_status(data_version: str | None = None) -> Dict[str, Any]:
    resolved = data_version or latest_data_version()
    return {
        "version": AGENT_PIPELINE_ITEM_WORKER_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": resolved,
        "stageCounts": _stage_counts(resolved),
        "pending": {
            "agent1CompletedForActionPack": len(
                _load_agent1_completed_items(resolved, 100000)
            ),
            "actionPackReadyForAgent2": pending_agent2_item_count(resolved),
            "agent2CompletedForSop": pending_sop_item_count(resolved),
            "sopMappedForTaskPool": pending_task_pool_item_count(resolved),
        },
        "runtimeSource": "pipeline_items.payload",
        "legacyRuntimeSourceUsed": False,
    }
