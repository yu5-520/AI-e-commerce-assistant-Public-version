"""V26.9.A downstream graph stages inside the existing pipeline_items protocol.

No second worker or queue is introduced. The registered station worker calls these
helpers only for rows that already carry the V26.9 semantic contract. Agent3 receives
one exact persisted PlanGraph input Artifact, Task Mapping is a deterministic graph
identity projection, and task-pool admission calls the existing authority root directly
without passing through the retired single-action mapping compiler.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services import v269_input_migration_service as migration
from src.services import v269_semantic_graph_service as graphs
from src.services.agent_input_contract_v225_service import AGENT3_SOP_INPUT_SCHEMA
from src.services.agent_runtime_contract_v225_service import payload_from_row
from src.services.artifact_transport_service import inspect_artifact, resolve_artifact, store_artifact
from src.services.pipeline_artifact_contract_service import (
    artifact_refs_from_row,
    attach_pipeline_artifact_ref,
)
from src.services.pipeline_item_service import (
    build_item_envelope,
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

VERSION = "26.9.0"
AGENT2_DRAFT_READY_STAGE = "agent2_draft_ready"
AGENT3_SOP_READY_STAGE = "agent3_sop_ready"
TASK_MAPPED_STAGE = "task_mapped"
TASK_ADMITTED_STAGE = "task_admitted"
TASK_ADMISSION_FAILED_STAGE = "task_admission_failed"
GRAPH_RETRY_STAGE = "agent2_draft_ready"


def _rows(data_version: str | None, stage: str, limit: int = 8) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM pipeline_items
               WHERE COALESCE(data_version,'')=COALESCE(?,'')
                 AND current_stage=? AND status IN ('queued','ready','retry')
               ORDER BY priority ASC,updated_at ASC LIMIT ?""",
            (data_version, stage, max(1, min(50, int(limit)))),
        ).fetchall()
    result: List[Dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        try:
            package = payload_from_row(row)
        except Exception:
            continue
        if migration.uses_graph_contract(package):
            result.append(row)
    return result


def pending_graph_agent3_count(data_version: str | None) -> int:
    return len(_rows(data_version, AGENT2_DRAFT_READY_STAGE, 100000))


def pending_graph_task_mapping_count(data_version: str | None) -> int:
    return len(_rows(data_version, AGENT3_SOP_READY_STAGE, 100000))


def pending_graph_task_pool_count(data_version: str | None) -> int:
    return len(_rows(data_version, TASK_MAPPED_STAGE, 100000))


def _artifact_source(item: Dict[str, Any], *keys: str) -> tuple[str, str]:
    refs = artifact_refs_from_row(item)
    candidate = ""
    for key in keys:
        value = str(refs.get(key) or "")
        if value.startswith("ART-"):
            candidate = value
            break
    if not candidate:
        value = str(item.get("payload_artifact_ref") or refs.get("currentStageRef") or "")
        if value.startswith("ART-"):
            candidate = value
    graphs.require(candidate.startswith("ART-"), "v269_downstream_source_ref_missing")
    metadata = inspect_artifact(candidate)
    content_hash = str(metadata.get("contentHash") or metadata.get("content_hash") or "")
    graphs.require(bool(content_hash), "v269_downstream_source_hash_missing")
    return candidate, content_hash


def _finish(
    item: Dict[str, Any],
    *,
    stage: str,
    status: str,
    payload: Dict[str, Any],
    output_ref: str,
    station_id: str,
    ref_key: str | None = None,
    decision_id: str | None = None,
    task_id: str | None = None,
) -> Dict[str, Any]:
    envelope = build_item_envelope(
        data_version=item.get("data_version") or payload.get("dataVersion"),
        item_id=item.get("item_id"),
        product_id=item.get("product_id") or payload.get("productId"),
        store_id=item.get("store_id") or payload.get("storeId"),
        signal_id=item.get("signal_id") or payload.get("signalId"),
        package_id=item.get("package_id") or payload.get("packageId"),
        decision_id=decision_id or item.get("decision_id") or payload.get("decisionId"),
        task_id=task_id or item.get("task_id") or payload.get("taskId"),
        action_family=None,
        route=None,
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
        attach_pipeline_artifact_ref(str(item.get("item_id")), ref_key, artifact_ref, make_current=True)
    record_pipeline_item_event(
        envelope,
        station_id=station_id,
        stage=stage,
        status=status,
        output_ref=output_ref,
        payload=payload,
    )
    return envelope


def _store_agent3_input(
    item: Dict[str, Any],
    envelope: Dict[str, Any],
    *,
    source_ref: str,
    plan_graph_hash: str,
) -> str:
    artifact = store_artifact(
        artifact_type=AGENT3_SOP_INPUT_SCHEMA,
        value=envelope,
        schema_version=VERSION,
        tenant_id=item.get("tenant_id"),
        store_id=item.get("store_id"),
        product_id=item.get("product_id"),
        data_version=item.get("data_version"),
        created_by="v269_pipeline_downstream_service",
        parent_refs=[source_ref],
        metadata={
            "pipelineItemId": item.get("item_id"),
            "semanticContractVersion": VERSION,
            "planGraphHash": plan_graph_hash,
            "fallbackAllowed": False,
        },
    )
    artifact_id = str(artifact["artifactId"])
    attach_pipeline_artifact_ref(
        str(item.get("item_id")), "agent3SopInputRef", artifact_id, make_current=True
    )
    return artifact_id


def run_agent3_graph_microbatch(
    data_version: str | None,
    *,
    batch_size: int = 2,
) -> Dict[str, Any]:
    from src.services.agent3_runtime_v23215_service import run_agent3_sop_projected_inputs

    items = _rows(data_version, AGENT2_DRAFT_READY_STAGE, max(1, min(8, int(batch_size or 2))))
    completed = failed = 0
    details: List[Dict[str, Any]] = []
    for item in items:
        package: Dict[str, Any] = {}
        try:
            package = dict(payload_from_row(item))
            plan = package.get("PlanGraph")
            graphs.index(plan)
            source_ref, source_hash = _artifact_source(item, "agent2DraftRef")
            source = {
                "semanticContractVersion": VERSION,
                "packageId": str(package.get("packageId") or item.get("item_id") or ""),
                "productId": package["productId"],
                "storeId": package["storeId"],
                "dataVersion": package.get("dataVersion") or data_version,
                "knowledgeContext": deepcopy(package.get("knowledgeContext") or {}),
                "PlanGraph": plan,
            }
            envelope = migration.project_input(
                "agent3", source, source_ref=source_ref, source_content_hash=source_hash
            )
            input_ref = _store_agent3_input(
                item, envelope, source_ref=source_ref, plan_graph_hash=plan["graphHash"]
            )
            persisted = resolve_artifact(input_ref)
            outputs, provider = run_agent3_sop_projected_inputs(
                [persisted], data_version=data_version, max_items_per_call=1
            )
            output = outputs.get(source["packageId"])
            graphs.require(isinstance(output, dict), "v269_agent3_output_missing")
            graphs.require(output.get("sopStatus") == "sop_ready", "v269_agent3_not_ready")
            operation = output.get("OperationGraph")
            graphs.index(operation)
            graphs.require(
                operation.get("upstreamGraphHash") == plan.get("graphHash"),
                "v269_operation_parent_mismatch",
            )
            execution_hash = str(output.get("executionHash") or "")
            graphs.require(bool(execution_hash), "v269_agent3_execution_hash_missing")
            graph_refs = deepcopy(package.get("graphExecutionRefs") or {})
            graphs.require(
                isinstance(graph_refs.get("agent1"), str)
                and isinstance(graph_refs.get("agent2"), list)
                and bool(graph_refs.get("agent2")),
                "v269_upstream_execution_refs_missing",
            )
            graph_refs["agent3"] = execution_hash
            next_payload = {
                **package,
                "OperationGraph": operation,
                "graphExecutionRefs": graph_refs,
                "agent3ProviderSummary": provider,
                "agent3SopEvidence": deepcopy(output.get("sopDecisionEvidence")),
                "taskAdmissionAllowed": False,
                "fallbackAllowed": False,
                "legacyBusinessSemanticsUsed": False,
            }
            _finish(
                item,
                stage=AGENT3_SOP_READY_STAGE,
                status="ready",
                payload=next_payload,
                output_ref=f"v269_operation_graph_ready:{item.get('item_id')}:{operation['graphHash']}",
                station_id="v269_agent3_graph_station",
                ref_key="agent3SopRef",
            )
            completed += 1
            details.append(
                {
                    "itemId": item.get("item_id"),
                    "status": "operation_graph_ready",
                    "OperationGraphHash": operation["graphHash"],
                }
            )
        except Exception as exc:
            failed += 1
            if package:
                try:
                    _finish(
                        item,
                        stage=GRAPH_RETRY_STAGE,
                        status="retry",
                        payload={
                            **package,
                            "reason": str(exc)[:800],
                            "taskAdmissionAllowed": False,
                            "fallbackAllowed": False,
                            "legacyBusinessSemanticsUsed": False,
                        },
                        output_ref=f"v269_agent3_failed:{item.get('item_id')}",
                        station_id="v269_agent3_graph_station",
                    )
                except Exception:
                    pass
            details.append(
                {"itemId": item.get("item_id"), "status": "failed", "error": str(exc)[:500]}
            )
    return {
        "version": VERSION,
        "dataVersion": data_version,
        "ran": bool(items),
        "selectedItemCount": len(items),
        "operationGraphReadyCount": completed,
        "failedItemCount": failed,
        "details": details,
        "secondWorkerCreated": False,
        "fallbackAllowed": False,
    }


def run_graph_task_mapping_microbatch(
    data_version: str | None,
    *,
    batch_size: int = 8,
) -> Dict[str, Any]:
    items = _rows(data_version, AGENT3_SOP_READY_STAGE, max(1, min(20, int(batch_size or 8))))
    mapped = failed = 0
    details: List[Dict[str, Any]] = []
    for item in items:
        package: Dict[str, Any] = {}
        try:
            package = dict(payload_from_row(item))
            decision = package["DecisionGraph"]
            admission = package["actionAdmission"]
            plan = package["PlanGraph"]
            operation = package["OperationGraph"]
            mapping = graphs.map_task(decision, admission, plan, operation)
            graph_refs = package.get("graphExecutionRefs") or {}
            graphs.require(
                set(graph_refs) == {"agent1", "agent2", "agent3"},
                "v269_graph_execution_refs_incomplete",
            )
            decision_id = "TGD-V269-" + graphs.digest(
                {
                    "packageId": package.get("packageId"),
                    "mappingHash": mapping["receiptHash"],
                    "executionRefs": graph_refs,
                }
            )[-20:].upper()
            mapped_payload = {
                **package,
                "decisionId": decision_id,
                "taskGraphMapping": mapping,
                "taskMappingMode": "deterministic_v269_graph_identity",
                "compilerAddedStepCount": 0,
                "taskAdmissionAllowed": True,
                "fallbackAllowed": False,
                "legacyBusinessSemanticsUsed": False,
                "outputContract": "V26.9.task_mapped",
            }
            _finish(
                item,
                stage=TASK_MAPPED_STAGE,
                status="queued",
                payload=mapped_payload,
                output_ref=f"v269_task_mapping:{data_version or 'latest'}:{decision_id}",
                station_id="v269_task_mapping_station",
                ref_key="taskMappingRef",
                decision_id=decision_id,
            )
            mapped += 1
            details.append(
                {
                    "itemId": item.get("item_id"),
                    "status": "task_mapped",
                    "decisionId": decision_id,
                    "mappingHash": mapping["receiptHash"],
                }
            )
        except Exception as exc:
            failed += 1
            if package:
                try:
                    _finish(
                        item,
                        stage=AGENT3_SOP_READY_STAGE,
                        status="retry",
                        payload={
                            **package,
                            "reason": str(exc)[:800],
                            "taskAdmissionAllowed": False,
                            "fallbackAllowed": False,
                        },
                        output_ref=f"v269_task_mapping_failed:{item.get('item_id')}",
                        station_id="v269_task_mapping_station",
                    )
                except Exception:
                    pass
            details.append(
                {"itemId": item.get("item_id"), "status": "failed", "error": str(exc)[:500]}
            )
    return {
        "version": VERSION,
        "dataVersion": data_version,
        "ran": bool(items),
        "selectedItemCount": len(items),
        "taskMappedCount": mapped,
        "failedItemCount": failed,
        "details": details,
        "compilerAddedStepCount": 0,
        "fallbackAllowed": False,
    }


def run_graph_task_pool_microbatch(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 8,
    force_new_snapshot: bool = False,
) -> Dict[str, Any]:
    items = _rows(data_version, TASK_MAPPED_STAGE, max(1, min(20, int(batch_size or 8))))
    created = failed = 0
    results: List[Dict[str, Any]] = []
    for item in items:
        package: Dict[str, Any] = {}
        try:
            package = dict(payload_from_row(item))
            graphs.require(
                package.get("taskMappingMode") == "deterministic_v269_graph_identity",
                "v269_task_mapping_contract_missing",
            )
            result = admit_decision_to_task_pool(
                package,
                created_by=user_id,
                force_new_snapshot=force_new_snapshot,
            )
        except Exception as exc:
            result = {
                "ok": False,
                "status": "v269_task_admission_exception",
                "createdTaskCount": 0,
                "reason": str(exc),
            }
        task_id = str(result.get("taskId") or "")
        success = bool(result.get("ok") is True and result.get("status") == "entered_task_pool" and task_id)
        results.append(result)
        payload = {
            **package,
            "taskAdmission": result,
            "taskId": task_id if success else None,
            "taskAdmissionAllowed": success,
            "reason": None if success else result.get("reason") or result.get("status"),
            "fallbackAllowed": False,
            "legacyBusinessSemanticsUsed": False,
            "outputContract": "V26.9.task_admitted" if success else "V26.9.task_admission_failed",
        }
        _finish(
            item,
            stage=TASK_ADMITTED_STAGE if success else TASK_ADMISSION_FAILED_STAGE,
            status="completed" if success else "failed",
            payload=payload,
            output_ref=(
                f"v269_task_pool:{data_version or 'latest'}:{package.get('decisionId') or item.get('item_id')}"
                if success
                else f"v269_task_pool_failed:{data_version or 'latest'}:{package.get('decisionId') or item.get('item_id')}"
            ),
            station_id="v269_task_pool_admission_station",
            ref_key="taskAdmissionRef" if success else "taskAdmissionFailureRef",
            decision_id=package.get("decisionId"),
            task_id=task_id if success else None,
        )
        created += 1 if success else 0
        failed += 0 if success else 1
    lifecycle_sync = sync_task_pool_entries_to_task_status(data_version=data_version)
    try:
        refresh = refresh_task_pool_views(data_version)
    except Exception as exc:
        refresh = {"status": "refresh_failed", "error": str(exc)}
    return {
        "version": VERSION,
        "dataVersion": data_version,
        "ran": bool(items),
        "selectedItemCount": len(items),
        "createdTaskCount": created,
        "failedItemCount": failed,
        "results": results,
        "lifecycleSync": lifecycle_sync,
        "frontendRefresh": refresh,
        "secondWorkerCreated": False,
        "fallbackAllowed": False,
    }


__all__ = [
    "VERSION",
    "pending_graph_agent3_count",
    "pending_graph_task_mapping_count",
    "pending_graph_task_pool_count",
    "run_agent3_graph_microbatch",
    "run_graph_task_mapping_microbatch",
    "run_graph_task_pool_microbatch",
]
