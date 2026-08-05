"""V21.4.2/V21.4.3 Action Pack and Agent2 pipeline worker.

Action Packs are claimed with finite leases. Transient provider/protocol
failures use bounded backoff; permanent provider failures enter an explicit
Agent2 dead-letter stage. Semantic output failures remain agent2_output_invalid
and are never disguised as retryable infrastructure failures.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services.action_pack_core_v20_service import ACTION_PACK_CORE_VERSION
from src.services.agent2_action_plan_core_v20_service import (
    AGENT2_ACTION_PLAN_CORE_VERSION,
    attach_agent2_action_plans,
    call_agent2_action_plans,
    provider_has_real_agent2_call,
    real_agent2_provider_missing_reason,
)
from src.services.agent2_runtime_resilience_v2143_service import (
    AGENT2_DEAD_LETTER_STAGE,
    AGENT2_FAILURE_GOVERNANCE_VERSION,
    AGENT2_LEASE_VERSION,
    claim_agent2_items,
    clear_agent2_runtime_control,
    ensure_agent2_runtime_columns,
    schedule_agent2_failure,
)
from src.services.agent_runtime_contract_v2141_service import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    missing_action_pack_contract,
    missing_agent2_contract,
    normalize_agent2_completed_contract,
    payload_from_row,
    product_title_of,
)
from src.services.pipeline_item_service import (
    STAGE_ORDER,
    build_item_envelope,
    pipeline_item_summary,
    record_pipeline_item_event,
    upsert_pipeline_item,
)

PIPELINE_ACTION_MICROBATCH_VERSION = "21.4.3"
ACTION_PACK_READY_STAGE = "action_pack_ready"
ACTION_PACK_INVALID_STAGE = "action_pack_invalid"
AGENT2_RUNNING_STAGE = "agent2_running"
AGENT2_COMPLETED_STAGE = "agent2_completed"
AGENT2_FAILED_STAGE = "agent2_failed"
AGENT2_OUTPUT_INVALID_STAGE = "agent2_output_invalid"
DEFAULT_AGENT2_MICRO_BATCH_SIZE = 5

STAGE_ORDER.setdefault(AGENT2_OUTPUT_INVALID_STAGE, 77)
STAGE_ORDER.setdefault(AGENT2_DEAD_LETTER_STAGE, 78)


def seed_action_pack_items_from_packages(
    data_version: str | None,
    *,
    source: str = "legacy_package_seed_disabled",
    limit: int | None = None,
) -> Dict[str, Any]:
    del limit
    return {
        "version": PIPELINE_ACTION_MICROBATCH_VERSION,
        "dataVersion": data_version,
        "packageCount": 0,
        "actionPackReadyItemCount": 0,
        "disabled": True,
        "source": source,
        "replacement": "agent_pipeline_item_worker_v2010_service.seed_action_pack_from_agent1_items",
    }


def run_action_microbatch_v205(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 8,
    **_: Any,
) -> Dict[str, Any]:
    del user_id
    from src.services.agent_pipeline_item_worker_v2010_service import (
        seed_action_pack_from_agent1_items,
    )

    result = seed_action_pack_from_agent1_items(
        data_version=data_version,
        batch_size=batch_size,
        source="run_action_microbatch_v205",
    )
    result["version"] = PIPELINE_ACTION_MICROBATCH_VERSION
    result["compatibilityEntry"] = "run_action_microbatch_v205"
    return result


def _pending_action_items(
    data_version: str | None,
    limit: int,
    action_family: str | None = None,
) -> List[Dict[str, Any]]:
    ensure_agent2_runtime_columns()
    where = [
        "COALESCE(data_version,'')=COALESCE(?,'')",
        "current_stage=?",
        "status IN ('queued','ready','retry')",
        "(retry_after IS NULL OR retry_after<=CURRENT_TIMESTAMP OR retry_after<=?)",
    ]
    params: List[Any] = [data_version, ACTION_PACK_READY_STAGE, __import__("datetime").datetime.now().isoformat()]
    if action_family:
        where.append("action_family=?")
        params.append(action_family)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_items WHERE {' AND '.join(where)} "
            "ORDER BY priority ASC, updated_at ASC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def pending_agent2_item_count(data_version: str | None) -> int:
    return len(_pending_action_items(data_version, 100000))


def _choose_next_family(data_version: str | None) -> str | None:
    ensure_agent2_runtime_columns()
    now = __import__("datetime").datetime.now().isoformat()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT action_family,COUNT(*) AS c,MIN(priority) AS p
            FROM pipeline_items
            WHERE COALESCE(data_version,'')=COALESCE(?,'')
              AND current_stage=?
              AND status IN ('queued','ready','retry')
              AND (retry_after IS NULL OR retry_after<=CURRENT_TIMESTAMP OR retry_after<=?)
            GROUP BY action_family
            ORDER BY p ASC,c DESC
            LIMIT 1
            """,
            (data_version, ACTION_PACK_READY_STAGE, now),
        ).fetchone()
    return row["action_family"] if row else None


def _package_from_action_item(row: Dict[str, Any]) -> Dict[str, Any]:
    package = dict(payload_from_row(row))
    package.setdefault("dataVersion", row.get("data_version"))
    package.setdefault("itemId", row.get("item_id"))
    package.setdefault("productId", row.get("product_id"))
    package.setdefault("storeId", row.get("store_id"))
    package.setdefault("signalId", row.get("signal_id"))
    package.setdefault("packageId", row.get("package_id") or row.get("item_id"))
    package.setdefault("actionFamily", row.get("action_family"))
    if not package.get("productTitle") and product_title_of(package):
        package["productTitle"] = product_title_of(package)
        package["title"] = product_title_of(package)
    package["inputSource"] = "pipeline_items.action_pack_ready"
    package["contractVersion"] = AGENT_RUNTIME_CONTRACT_VERSION
    package["agent2LeaseVersion"] = AGENT2_LEASE_VERSION
    package["agent2FailureGovernanceVersion"] = AGENT2_FAILURE_GOVERNANCE_VERSION
    return package


def _finish_item(
    item: Dict[str, Any],
    *,
    stage: str,
    status: str,
    output_ref: str,
    payload: Dict[str, Any],
    station_id: str = "action_plan_judgment_agent_station",
) -> Dict[str, Any]:
    envelope = build_item_envelope(
        data_version=item.get("data_version"),
        item_id=item.get("item_id"),
        product_id=item.get("product_id") or payload.get("productId"),
        store_id=item.get("store_id") or payload.get("storeId"),
        signal_id=item.get("signal_id") or payload.get("signalId"),
        package_id=item.get("package_id") or payload.get("packageId"),
        action_family=item.get("action_family") or payload.get("actionFamily"),
        route=item.get("route") or payload.get("route"),
        input_ref=f"pipeline_items:{item.get('current_stage')}:{item.get('item_id')}",
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
    clear_agent2_runtime_control(item.get("item_id"))
    record_pipeline_item_event(
        envelope,
        station_id=station_id,
        stage=stage,
        status=status,
        output_ref=output_ref,
        payload=payload,
    )
    return envelope


def _mark_action_pack_invalid(
    item: Dict[str, Any],
    missing: List[str],
    package: Dict[str, Any],
) -> None:
    _finish_item(
        item,
        stage=ACTION_PACK_INVALID_STAGE,
        status="failed",
        output_ref=f"action_pack_invalid:{item.get('data_version') or 'latest'}:{item.get('item_id')}",
        station_id="action_parameter_enrichment_station",
        payload={
            **package,
            "reason": "missing_or_unready_action_pack_contract",
            "missing": missing,
            "failureOwner": "action_parameter_enrichment_station",
            "frontendFailureLabel": "动作补包不完整",
            "taskAdmissionAllowed": False,
            "fallbackAllowed": False,
        },
    )


def _mark_agent2_output_invalid(
    item: Dict[str, Any],
    package: Dict[str, Any],
    plan: Dict[str, Any],
    provider: Dict[str, Any],
    missing: List[str] | None = None,
) -> None:
    package_id = str(
        package.get("packageId")
        or item.get("package_id")
        or item.get("item_id")
        or ""
    )
    missing = missing or (
        plan.get("semanticContractMissing")
        if isinstance(plan.get("semanticContractMissing"), list)
        else []
    )
    _finish_item(
        item,
        stage=AGENT2_OUTPUT_INVALID_STAGE,
        status="failed",
        output_ref=f"agent2_output_invalid:{item.get('data_version') or 'latest'}:{package_id}",
        payload={
            **package,
            "agent2ActionPlan": plan,
            "operationPlan": plan.get("operationPlan"),
            "agent2ExecutionProof": plan.get("agent2ExecutionProof"),
            "agent2Provider": provider,
            "agent2Source": "llm_provider_call_invalid_output",
            "actionPlanStatus": plan.get("actionPlanStatus"),
            "reason": plan.get("reason")
            or plan.get("conflictReason")
            or "agent2_output_contract_invalid",
            "missing": missing,
            "fallbackAllowed": False,
            "taskAdmissionAllowed": False,
            "failureOwner": "agent2_action_plan_station",
            "frontendFailureLabel": "Agent2方案不完整",
            "agent2RetryPolicy": {
                "version": AGENT2_FAILURE_GOVERNANCE_VERSION,
                "failureClass": "semantic_output",
                "failureCode": "agent2_semantic_output_invalid",
                "retryEligible": False,
                "terminal": True,
            },
        },
    )


def run_agent2_microbatch_v205(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = DEFAULT_AGENT2_MICRO_BATCH_SIZE,
    action_family: str | None = None,
) -> Dict[str, Any]:
    ensure_agent2_runtime_columns()
    family = action_family or _choose_next_family(data_version)
    selected = _pending_action_items(
        data_version,
        max(1, min(12, int(batch_size or DEFAULT_AGENT2_MICRO_BATCH_SIZE))),
        family,
    )
    if not selected:
        return {
            "version": PIPELINE_ACTION_MICROBATCH_VERSION,
            "dataVersion": data_version,
            "claimedItemCount": 0,
            "actionPlanCount": 0,
            "pendingItemCount": pending_agent2_item_count(data_version),
            "provider": {
                "providerStatus": "skipped_no_due_action_pack_ready_items",
                "actualCalls": 0,
            },
        }

    valid: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
    invalid_missing: Counter[str] = Counter()
    for item in selected:
        package = _package_from_action_item(item)
        missing = missing_action_pack_contract(package)
        if missing:
            invalid_missing.update(missing)
            _mark_action_pack_invalid(item, missing, package)
        else:
            valid.append((item, package))

    if not valid:
        return {
            "version": PIPELINE_ACTION_MICROBATCH_VERSION,
            "dataVersion": data_version,
            "claimedItemCount": len(selected),
            "validActionPackCount": 0,
            "invalidActionPackCount": len(selected),
            "invalidMissing": dict(invalid_missing),
            "completedItemCount": 0,
            "failedItemCount": len(selected),
            "pendingItemCount": pending_agent2_item_count(data_version),
            "provider": {
                "providerStatus": "skipped_invalid_action_pack_contract",
                "actualCalls": 0,
            },
        }

    claimed = claim_agent2_items(
        [item for item, _ in valid],
        worker_id=user_id,
    )
    claimed_by_id = {str(item.get("item_id")): item for item in claimed}
    valid = [
        (claimed_by_id[str(item.get("item_id"))], package)
        for item, package in valid
        if str(item.get("item_id")) in claimed_by_id
    ]
    if not valid:
        return {
            "version": PIPELINE_ACTION_MICROBATCH_VERSION,
            "dataVersion": data_version,
            "claimedItemCount": 0,
            "failedItemCount": 0,
            "pendingItemCount": pending_agent2_item_count(data_version),
            "provider": {"providerStatus": "claim_conflict", "actualCalls": 0},
        }

    packages = [package for _, package in valid]
    plans, provider = call_agent2_action_plans(packages, data_version)
    enriched = attach_agent2_action_plans(packages, plans)
    plan_by_package = {
        str(package.get("packageId") or package.get("itemId")): package.get(
            "agent2ActionPlan"
        )
        for package in enriched
        if package.get("packageId") or package.get("itemId")
    }

    completed = invalid_output = retry_scheduled = dead_lettered = proof_failed = 0
    by_status: Counter[str] = Counter()
    by_failure_class: Counter[str] = Counter()

    for item, package in valid:
        package_id = str(
            package.get("packageId")
            or item.get("package_id")
            or item.get("item_id")
            or ""
        )
        plan = plan_by_package.get(package_id)
        if not isinstance(plan, dict) or not plan:
            outcome = schedule_agent2_failure(
                item,
                package,
                provider,
                real_agent2_provider_missing_reason(provider, package_id)
                or "agent2_returned_no_plan",
            )
            by_failure_class[str(outcome.get("failureClass"))] += 1
            retry_scheduled += 1 if outcome.get("status") == "retry" else 0
            dead_lettered += 1 if outcome.get("terminal") else 0
            continue

        proof = (
            plan.get("agent2ExecutionProof")
            if isinstance(plan.get("agent2ExecutionProof"), dict)
            else {}
        )
        if not provider_has_real_agent2_call(provider, package_id, proof):
            proof_failed += 1
            outcome = schedule_agent2_failure(
                item,
                package,
                provider,
                real_agent2_provider_missing_reason(provider, package_id, proof)
                or "agent2_item_provenance_missing",
            )
            by_failure_class[str(outcome.get("failureClass"))] += 1
            retry_scheduled += 1 if outcome.get("status") == "retry" else 0
            dead_lettered += 1 if outcome.get("terminal") else 0
            continue

        status = str(plan.get("actionPlanStatus") or "missing")
        by_status[status] += 1
        candidate = normalize_agent2_completed_contract(package, plan, provider)
        missing = missing_agent2_contract(candidate)
        if status != "ready" or missing:
            invalid_output += 1
            _mark_agent2_output_invalid(item, package, plan, provider, missing)
            continue

        completed += 1
        candidate["agent2Source"] = plan.get("agent2Source")
        candidate["fallbackAllowed"] = False
        candidate["taskAdmissionAllowed"] = True
        candidate["agent2LeaseVersion"] = AGENT2_LEASE_VERSION
        candidate["agent2FailureGovernanceVersion"] = (
            AGENT2_FAILURE_GOVERNANCE_VERSION
        )
        _finish_item(
            item,
            stage=AGENT2_COMPLETED_STAGE,
            status="ready",
            output_ref=f"agent2_action_plan:{data_version or 'latest'}:{package_id}",
            payload=candidate,
        )

    return {
        "version": PIPELINE_ACTION_MICROBATCH_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "agent2LeaseVersion": AGENT2_LEASE_VERSION,
        "agent2FailureGovernanceVersion": AGENT2_FAILURE_GOVERNANCE_VERSION,
        "dataVersion": data_version,
        "actionFamily": family,
        "claimedItemCount": len(valid),
        "validActionPackCount": len(valid),
        "invalidActionPackCount": len(selected) - len(valid),
        "invalidMissing": dict(invalid_missing),
        "completedItemCount": completed,
        "invalidOutputItemCount": invalid_output,
        "retryScheduledItemCount": retry_scheduled,
        "deadLetteredItemCount": dead_lettered,
        "proofFailedItemCount": proof_failed,
        "failedItemCount": invalid_output + dead_lettered,
        "actionPlanCount": len(plans),
        "byActionPlanStatus": dict(by_status),
        "byFailureClass": dict(by_failure_class),
        "pendingItemCount": pending_agent2_item_count(data_version),
        "provider": provider,
        "actionPackCoreVersion": ACTION_PACK_CORE_VERSION,
        "agent2ActionPlanCoreVersion": AGENT2_ACTION_PLAN_CORE_VERSION,
        "pipelineItemSummary": pipeline_item_summary(
            data_version=data_version,
            limit=50,
        ),
        "rule": (
            "V21.4.2 finite leases prevent stranded running items; V21.4.3 "
            "retries only transient provider/protocol failures and never retries "
            "semantic output failures."
        ),
    }


def run_agent2_microbatch_loop_v205(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = DEFAULT_AGENT2_MICRO_BATCH_SIZE,
    max_batches: int = 20,
) -> Dict[str, Any]:
    batches: List[Dict[str, Any]] = []
    for _ in range(max(1, min(50, int(max_batches or 1)))):
        result = run_agent2_microbatch_v205(
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
        "version": PIPELINE_ACTION_MICROBATCH_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "agent2LeaseVersion": AGENT2_LEASE_VERSION,
        "agent2FailureGovernanceVersion": AGENT2_FAILURE_GOVERNANCE_VERSION,
        "dataVersion": data_version,
        "microBatchCount": len(batches),
        "claimedItemCount": sum(
            int(item.get("claimedItemCount") or 0) for item in batches
        ),
        "completedItemCount": sum(
            int(item.get("completedItemCount") or 0) for item in batches
        ),
        "invalidOutputItemCount": sum(
            int(item.get("invalidOutputItemCount") or 0) for item in batches
        ),
        "retryScheduledItemCount": sum(
            int(item.get("retryScheduledItemCount") or 0) for item in batches
        ),
        "deadLetteredItemCount": sum(
            int(item.get("deadLetteredItemCount") or 0) for item in batches
        ),
        "actionPlanCount": sum(
            int(item.get("actionPlanCount") or 0) for item in batches
        ),
        "pendingItemCount": pending_agent2_item_count(data_version),
        "provider": {
            "providerStatus": "completed" if batches else "skipped_no_due_items",
            "actualCalls": sum(
                int((item.get("provider") or {}).get("actualCalls") or 0)
                for item in batches
            ),
            "idempotentReplays": sum(
                int((item.get("provider") or {}).get("idempotentReplays") or 0)
                for item in batches
            ),
        },
        "batches": [
            {
                key: value
                for key, value in item.items()
                if key not in {"pipelineItemSummary", "batches"}
            }
            for item in batches
        ],
        "pipelineItemSummary": pipeline_item_summary(
            data_version=data_version,
            limit=50,
        ),
    }
