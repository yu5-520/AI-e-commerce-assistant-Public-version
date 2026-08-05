"""V22.5 deterministic Agent3-SOP mapping and task-pool admission."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services.artifact_transport_service import resolve_artifact
from src.services.agent_runtime_contract_v225_service import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    build_task_mapping_decision,
    missing_agent3_sop_completed_contract,
    missing_task_mapping_contract,
    payload_from_row,
)
from src.services.pipeline_artifact_contract_service import (
    artifact_refs_from_row,
    attach_pipeline_artifact_ref,
)
from src.services.pipeline_item_service import (
    build_item_envelope,
    pipeline_item_summary,
    record_pipeline_item_event,
    upsert_pipeline_item,
)
from src.services.task_pool_admission_core_v20_service import (
    admit_decision_to_task_pool,
    refresh_task_pool_views,
)
from src.services.task_pool_lifecycle_sync_v2020_service import (
    sync_task_pool_entries_to_task_status,
)

THREE_AGENT_PIPELINE_VERSION = "22.5.0"
PIPELINE_TASK_MAPPING_VERSION = "23.2.9"
AGENT3_SOP_READY_STAGE = "agent3_sop_ready"
TASK_MAPPED_STAGE = "task_mapped"
TASK_MAPPING_FAILED_STAGE = "task_mapping_failed"
TASK_ADMITTED_STAGE = "task_admitted"
TASK_ADMISSION_FAILED_STAGE = "task_admission_failed"
DEFAULT_MAPPING_BATCH_SIZE = 12
DEFAULT_POOL_BATCH_SIZE = 12


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pending_items(data_version: str | None, stage: str, limit: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM pipeline_items
            WHERE COALESCE(data_version,'')=COALESCE(?,'')
              AND current_stage=?
              AND status IN ('queued','ready','retry')
            ORDER BY priority ASC,updated_at ASC
            LIMIT ?
            """,
            (data_version, stage, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def pending_task_mapping_item_count(data_version: str | None) -> int:
    return len(_pending_items(data_version, AGENT3_SOP_READY_STAGE, 100000))


def pending_task_pool_item_count(data_version: str | None) -> int:
    return len(_pending_items(data_version, TASK_MAPPED_STAGE, 100000))


def _finish_item(
    item: Dict[str, Any],
    *,
    stage: str,
    status: str,
    output_ref: str,
    payload: Dict[str, Any],
    station_id: str,
    ref_key: str | None = None,
    decision_id: str | None = None,
    task_id: str | None = None,
) -> Dict[str, Any]:
    envelope = build_item_envelope(
        data_version=item.get("data_version"),
        item_id=item.get("item_id"),
        product_id=item.get("product_id") or payload.get("productId"),
        store_id=item.get("store_id") or payload.get("storeId"),
        signal_id=item.get("signal_id") or payload.get("signalId"),
        package_id=item.get("package_id") or payload.get("packageId"),
        decision_id=decision_id or item.get("decision_id") or payload.get("decisionId"),
        task_id=task_id or item.get("task_id") or payload.get("taskId"),
        action_family=item.get("action_family") or payload.get("actionFamily") or payload.get("selectedActionFamily"),
        route=item.get("route") or payload.get("route"),
        input_ref=f"pipeline_items:{item.get('current_stage')}:{item.get('item_id')}",
        output_ref=output_ref,
        stage=stage,
        artifact_refs=artifact_refs_from_row(item),
    )
    envelope = upsert_pipeline_item(
        envelope,
        stage=stage,
        status=status,
        priority=int(item.get("priority") or 50),
        output_ref=output_ref,
        payload=payload,
    )
    artifact_ref = str(envelope.get("payloadArtifactRef") or "")
    if ref_key and artifact_ref.startswith("ART-"):
        attach_pipeline_artifact_ref(str(item.get("item_id")), ref_key, artifact_ref)
    record_pipeline_item_event(
        envelope,
        station_id=station_id,
        stage=stage,
        status=status,
        output_ref=output_ref,
        payload=payload,
    )
    return envelope


def _system_admission_reason(
    package: Dict[str, Any],
    decision: Dict[str, Any],
) -> str:
    plan = _dict(decision.get("taskPlan"))
    sop = _dict(decision.get("agent3Sop")) or _dict(package.get("agent3Sop"))
    draft = _dict(decision.get("agent2ActionDraft")) or _dict(
        package.get("agent2ActionDraft")
    )
    return str(
        plan.get("admissionReason")
        or plan.get("reason")
        or decision.get("admissionReason")
        or sop.get("executionObjective")
        or sop.get("companyStyleReason")
        or draft.get("differentiationReason")
        or ""
    ).strip()


def _compile_current_task_mapping_decision(
    package: Dict[str, Any],
    decision: Dict[str, Any],
) -> Dict[str, Any]:
    compiled = dict(decision)
    plan = dict(_dict(compiled.get("taskPlan")))
    sop = _dict(compiled.get("agent3Sop")) or _dict(package.get("agent3Sop"))
    draft = _dict(compiled.get("agent2ActionDraft")) or _dict(
        package.get("agent2ActionDraft")
    )
    title = str(
        plan.get("title")
        or plan.get("taskTitle")
        or plan.get("finalTaskTitle")
        or compiled.get("taskTitle")
        or sop.get("finalTaskTitle")
        or ""
    ).strip()
    reason = _system_admission_reason(package, compiled)
    plan.update(
        {
            "title": title,
            "taskTitle": title,
            "finalTaskTitle": title,
            "admissionReason": reason,
            "reason": reason,
            "trendJudgment": reason,
        }
    )
    product = _dict(plan.get("productIdentity")) or _dict(
        package.get("productIdentity")
    )
    judgment_package = _dict(compiled.get("productJudgmentPackage")) or {
        "productId": compiled.get("productId") or package.get("productId"),
        "storeId": compiled.get("storeId") or package.get("storeId"),
        "productIdentity": product,
        "agent1OperatingJudgment": _dict(
            package.get("agent1OperatingJudgment")
        ),
    }
    compatibility_agent2 = dict(_dict(compiled.get("agent2ActionPlan")))
    if reason:
        compatibility_agent2.setdefault("reason", reason)
        compatibility_agent2.setdefault("differentiationReason", reason)
    compiled.update(
        {
            "taskTitle": title,
            "admissionReason": reason,
            "reason": reason,
            "taskPlan": plan,
            "productJudgmentPackage": judgment_package,
            "agent2ActionPlan": compatibility_agent2,
            "agent2ActionDraft": draft,
            "agent3Sop": sop,
            "taskAdmissionAdapterVersion": "23.2.9",
        }
    )
    return compiled


def _current_task_mapping_missing(decision: Dict[str, Any]) -> List[str]:
    missing = list(missing_task_mapping_contract(decision))
    plan = _dict(decision.get("taskPlan"))
    if not str(
        plan.get("title")
        or plan.get("taskTitle")
        or plan.get("finalTaskTitle")
        or decision.get("taskTitle")
        or ""
    ).strip():
        missing.append("taskPlan.title")
    if not str(
        plan.get("admissionReason")
        or plan.get("reason")
        or decision.get("admissionReason")
        or ""
    ).strip():
        missing.append("taskPlan.admissionReason")
    return list(dict.fromkeys(missing))


def _task_mapping_artifact_input(
    item: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any], str]:
    refs = artifact_refs_from_row(item)
    artifact_ref = str(refs.get("taskMappingRef") or "").strip()
    if not artifact_ref.startswith("ART-"):
        raise ValueError("task_mapping_ref_missing")
    value = resolve_artifact(artifact_ref)
    if not isinstance(value, dict) or not value:
        raise ValueError("task_mapping_artifact_empty_or_not_object")
    artifact_payload = dict(value)
    nested = _dict(artifact_payload.get("payload"))
    decision = _dict(
        artifact_payload.get("taskMappingDecision")
        or artifact_payload.get("sopDecision")
        or nested.get("taskMappingDecision")
        or nested.get("sopDecision")
    )
    if not decision:
        raise ValueError("task_mapping_decision_missing_in_artifact")
    return artifact_payload, decision, artifact_ref


def run_task_mapping_microbatch_v225(
    data_version: str | None,
    *,
    batch_size: int = DEFAULT_MAPPING_BATCH_SIZE,
) -> Dict[str, Any]:
    items = _pending_items(
        data_version,
        AGENT3_SOP_READY_STAGE,
        max(1, min(50, int(batch_size or DEFAULT_MAPPING_BATCH_SIZE))),
    )
    mapped = failed = 0
    by_failure: Counter[str] = Counter()
    for item in items:
        package = dict(payload_from_row(item))
        upstream_missing = missing_agent3_sop_completed_contract(package)
        if upstream_missing:
            failed += 1
            by_failure["missing_agent3_sop_contract"] += 1
            _finish_item(
                item,
                stage=TASK_MAPPING_FAILED_STAGE,
                status="failed",
                output_ref=f"task_mapping_failed:{data_version or 'latest'}:{item.get('item_id')}",
                payload={
                    **package,
                    "reason": "missing_or_unready_agent3_sop_contract",
                    "missing": upstream_missing,
                    "failureOwner": "agent3_sop_station",
                    "frontendFailureLabel": "Agent3 SOP不完整",
                    "taskAdmissionAllowed": False,
                    "fallbackAllowed": False,
                },
                station_id="task_mapping_station",
                ref_key="taskMappingFailureRef",
            )
            continue
        decision = _compile_current_task_mapping_decision(
            package,
            build_task_mapping_decision(
                package,
                pipeline_item_id=str(item.get("item_id") or ""),
            ),
        )
        missing = _current_task_mapping_missing(decision)
        if missing:
            failed += 1
            by_failure["task_mapping_contract_invalid"] += 1
            _finish_item(
                item,
                stage=TASK_MAPPING_FAILED_STAGE,
                status="failed",
                output_ref=f"task_mapping_failed:{data_version or 'latest'}:{item.get('item_id')}",
                payload={
                    **package,
                    "taskMappingDecision": decision,
                    "reason": "task_mapping_contract_invalid",
                    "missing": missing,
                    "failureOwner": "task_mapping_station",
                    "frontendFailureLabel": "任务映射失败",
                    "taskAdmissionAllowed": False,
                    "fallbackAllowed": False,
                },
                station_id="task_mapping_station",
                ref_key="taskMappingFailureRef",
            )
            continue
        payload = {
            **package,
            "decisionId": decision.get("decisionId"),
            "sopDecision": decision,
            "taskMappingDecision": decision,
            "taskMappingMode": "deterministic_agent3_projection_only",
            "compilerAddedStepCount": 0,
            "taskAdmissionAllowed": True,
            "fallbackAllowed": False,
            "outputContract": "V23.2.9.task_mapped",
            "taskMappingContractVersion": "23.2.9",
        }
        _finish_item(
            item,
            stage=TASK_MAPPED_STAGE,
            status="queued",
            output_ref=f"task_mapping:{data_version or 'latest'}:{decision.get('decisionId')}",
            payload=payload,
            decision_id=decision.get("decisionId"),
            station_id="task_mapping_station",
            ref_key="taskMappingRef",
        )
        mapped += 1
    return {
        "version": PIPELINE_TASK_MAPPING_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": data_version,
        "ran": bool(items),
        "claimedItemCount": len(items),
        "taskMappedCount": mapped,
        "failedItemCount": failed,
        "byFailureReason": dict(by_failure),
        "pendingItemCount": pending_task_mapping_item_count(data_version),
        "pipelineItemSummary": pipeline_item_summary(data_version=data_version, limit=50),
        "mappingMode": "deterministic_agent3_projection_only",
        "compilerAddedStepCount": 0,
        "fallbackAllowed": False,
    }


def run_task_pool_admission_microbatch_v225(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = DEFAULT_POOL_BATCH_SIZE,
    force_new_snapshot: bool = False,
) -> Dict[str, Any]:
    items = _pending_items(
        data_version,
        TASK_MAPPED_STAGE,
        max(1, min(50, int(batch_size or DEFAULT_POOL_BATCH_SIZE))),
    )
    results: List[Dict[str, Any]] = []
    for item in items:
        artifact_payload: Dict[str, Any] = {}
        decision: Dict[str, Any] = {}
        task_mapping_ref = ""
        try:
            artifact_payload, raw_decision, task_mapping_ref = _task_mapping_artifact_input(
                item
            )
            decision = _compile_current_task_mapping_decision(
                artifact_payload,
                raw_decision,
            )
            missing = _current_task_mapping_missing(decision)
            result = (
                {
                    "ok": False,
                    "status": "missing_task_mapping_contract",
                    "createdTaskCount": 0,
                    "decisionId": decision.get("decisionId"),
                    "packageId": decision.get("packageId"),
                    "reason": "missing_or_unready_task_mapping_contract",
                    "missing": missing,
                    "taskMappingRef": task_mapping_ref,
                }
                if missing
                else admit_decision_to_task_pool(
                    decision,
                    created_by=user_id,
                    force_new_snapshot=force_new_snapshot,
                )
            )
        except Exception as exc:
            result = {
                "ok": False,
                "status": "task_mapping_artifact_invalid",
                "createdTaskCount": 0,
                "decisionId": item.get("decision_id"),
                "packageId": item.get("package_id"),
                "reason": str(exc),
                "missing": [str(exc)],
                "taskMappingRef": task_mapping_ref or None,
            }
        status = str(result.get("status") or "unknown")
        task_id = str(result.get("taskId") or "").strip()
        success = (
            result.get("ok") is True
            and status == "entered_task_pool"
            and bool(task_id)
        )
        if result.get("ok") is True and status == "entered_task_pool" and not task_id:
            result = {
                **result,
                "ok": False,
                "status": "task_materialization_task_id_missing",
                "createdTaskCount": 0,
                "reason": "task_materialization_task_id_missing",
                "missing": ["taskId"],
            }
            status = "task_materialization_task_id_missing"
            success = False
        results.append(result)
        payload = {
            **artifact_payload,
            "taskMappingDecision": decision,
            "taskMappingSourceRef": task_mapping_ref or None,
            "taskAdmission": result,
            "taskId": task_id if success else None,
            "taskAdmissionAllowed": success,
            "reason": None if success else result.get("reason") or status,
            "missing": [] if success else result.get("missing") or [],
            "failureOwner": None if success else "task_pool_admission_station",
            "frontendFailureLabel": None if success else "任务入池合同未通过",
            "fallbackAllowed": False,
            "admissionInputContract": "taskMappingRef_only",
            "outputContract": (
                "V23.2.9.task_admitted"
                if success
                else "V23.2.9.task_admission_failed"
            ),
        }
        _finish_item(
            item,
            stage=TASK_ADMITTED_STAGE if success else TASK_ADMISSION_FAILED_STAGE,
            status="completed" if success else "failed",
            output_ref=(
                f"task_pool:{data_version or 'latest'}:{result.get('decisionId') or item.get('decision_id')}"
                if success
                else f"task_pool_admission_failed:{data_version or 'latest'}:{result.get('decisionId') or item.get('decision_id')}"
            ),
            payload=payload,
            decision_id=result.get("decisionId") or item.get("decision_id"),
            task_id=task_id if success else None,
            station_id="task_pool_admission_station",
            ref_key="taskAdmissionRef" if success else "taskAdmissionFailureRef",
        )
    lifecycle_sync = sync_task_pool_entries_to_task_status(data_version=data_version)
    try:
        refresh = refresh_task_pool_views(data_version)
    except Exception as exc:
        refresh = {"status": "refresh_failed", "error": str(exc)}
    return {
        "version": PIPELINE_TASK_MAPPING_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": data_version,
        "ran": bool(items),
        "claimedItemCount": len(items),
        "createdTaskCount": sum(
            int(result.get("createdTaskCount") or 0)
            for result in results
            if result.get("ok") is True
            and str(result.get("status") or "") == "entered_task_pool"
            and bool(result.get("taskId"))
        ),
        "failedItemCount": sum(
            1
            for result in results
            if not (
                result.get("ok") is True
                and str(result.get("status") or "") == "entered_task_pool"
                and bool(result.get("taskId"))
            )
        ),
        "byAdmissionStatus": dict(
            Counter(str(result.get("status")) for result in results)
        ),
        "pendingItemCount": pending_task_pool_item_count(data_version),
        "lifecycleSync": lifecycle_sync,
        "frontendRefresh": refresh,
        "pipelineItemSummary": pipeline_item_summary(
            data_version=data_version,
            limit=50,
        ),
        "admissionInputContract": "taskMappingRef_only",
        "fallbackAllowed": False,
    }


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "PIPELINE_TASK_MAPPING_VERSION",
    "AGENT3_SOP_READY_STAGE",
    "TASK_MAPPED_STAGE",
    "TASK_MAPPING_FAILED_STAGE",
    "TASK_ADMITTED_STAGE",
    "TASK_ADMISSION_FAILED_STAGE",
    "_compile_current_task_mapping_decision",
    "_current_task_mapping_missing",
    "_task_mapping_artifact_input",
    "pending_task_mapping_item_count",
    "pending_task_pool_item_count",
    "run_task_mapping_microbatch_v225",
    "run_task_pool_admission_microbatch_v225",
]
