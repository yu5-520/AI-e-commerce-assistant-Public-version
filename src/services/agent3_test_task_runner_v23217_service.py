"""V23.2.17 controlled Agent3 rerun that creates a separate lifecycle test task.

The runner preserves the original task and source pipeline item. It reuses the
immutable Agent2 draft Artifact and validated Agent2 execution proof, creates a
new isolated test data version and pipeline item, then invokes the registered
Agent3, task-mapping and task-pool workers. Admission always uses
``force_new_snapshot=True`` so a new lifecycle task is materialized instead of
hitting the original task's idempotency record.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.agent3_system_constraint_v23215_service import (
    AGENT2_PROOF_BRIDGE_VERSION,
    canonicalize_agent2_draft_proof,
)
from src.services.agent_runtime_contract_v225_service import (
    missing_agent2_draft_completed_contract,
)
from src.services.artifact_transport_service import resolve_artifact
from src.services.pipeline_agent3_sop_v225_service import (
    AGENT2_DRAFT_READY_STAGE,
    AGENT3_SOP_READY_STAGE,
    PIPELINE_AGENT3_SOP_VERSION,
    run_agent3_sop_microbatch_v225,
)
from src.services.pipeline_artifact_contract_service import artifact_refs_from_row
from src.services.pipeline_item_service import (
    build_item_envelope,
    ensure_pipeline_item_tables,
    make_item_id,
    record_pipeline_item_event,
    upsert_pipeline_item,
)
from src.services.pipeline_task_mapping_v225_service import (
    PIPELINE_TASK_MAPPING_VERSION,
    TASK_ADMITTED_STAGE,
    TASK_MAPPED_STAGE,
    run_task_mapping_microbatch_v225,
    run_task_pool_admission_microbatch_v225,
)
from src.services.task_pool_admission_core_v20_service import refresh_task_pool_views
from src.services.task_pool_lifecycle_sync_v2020_service import (
    sync_task_pool_entries_to_task_status,
)

AGENT3_TEST_TASK_RUNNER_VERSION = "23.2.17"
TEST_EXECUTION_MODE = "agent3_test_rerun"
TEST_DATA_VERSION_PREFIX = "DV-TEST-A3"

_DOWNSTREAM_ARTIFACT_REF_KEYS = {
    "agent3SopInputRef",
    "agent3SopRef",
    "agent3SopFailureRef",
    "taskMappingRef",
    "taskMappingFailureRef",
    "taskAdmissionRef",
    "taskAdmissionFailureRef",
    "taskRef",
    "sopRef",
    "readModelRef",
    "acceptanceRef",
    "currentStageRef",
}

_DOWNSTREAM_PAYLOAD_KEYS = {
    "agent3Sop",
    "agent3ExecutionProof",
    "agent3Provider",
    "agent3SopStatus",
    "agent3SopInputRef",
    "sopDecision",
    "taskMappingDecision",
    "taskMappingMode",
    "taskMappingContractVersion",
    "taskAdmission",
    "taskId",
    "task_id",
    "decisionId",
    "decision_id",
    "runtimeSource",
    "sourceArtifactRefs",
    "inputProjectionAudit",
    "failureOwner",
    "frontendFailureLabel",
    "reason",
    "missing",
}

_SOURCE_PIPELINE_FINGERPRINT_FIELDS = (
    "item_id",
    "data_version",
    "product_id",
    "store_id",
    "signal_id",
    "package_id",
    "decision_id",
    "task_id",
    "current_stage",
    "status",
    "output_ref",
    "payload_artifact_ref",
    "artifact_refs_json",
)

_SOURCE_TASK_FINGERPRINT_FIELDS = (
    "task_id",
    "workflow_run_id",
    "status",
    "workflow_status",
    "approval_status",
    "assignee_id",
    "reviewer_id",
    "payload",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        parsed = loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fetch_one(query: str, params: Iterable[Any]) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(query, tuple(params)).fetchone()
    return dict(row) if row else {}


def _pipeline_item(item_id: str) -> Dict[str, Any]:
    return _fetch_one("SELECT * FROM pipeline_items WHERE item_id=?", (item_id,))


def _task_status(task_id: str) -> Dict[str, Any]:
    return _fetch_one("SELECT * FROM task_status WHERE task_id=?", (task_id,))


def _fingerprint(row: Dict[str, Any], fields: Iterable[str]) -> str:
    material = {field: row.get(field) for field in fields}
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _new_test_data_version() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{TEST_DATA_VERSION_PREFIX}-{stamp}-{uuid.uuid4().hex[:8].upper()}"


def _upstream_artifact_refs(source_row: Dict[str, Any]) -> Dict[str, Any]:
    refs = artifact_refs_from_row(source_row)
    return {
        key: value
        for key, value in refs.items()
        if key not in _DOWNSTREAM_ARTIFACT_REF_KEYS
    }


def _clone_agent2_package(
    source_package: Dict[str, Any],
    *,
    test_data_version: str,
    test_item_id: str,
    source_task_id: str,
    source_pipeline_item_id: str,
    purpose: str,
) -> Dict[str, Any]:
    cloned = deepcopy(source_package)
    for key in _DOWNSTREAM_PAYLOAD_KEYS:
        cloned.pop(key, None)

    context = {
        "version": AGENT3_TEST_TASK_RUNNER_VERSION,
        "executionMode": TEST_EXECUTION_MODE,
        "isTestTask": True,
        "sourceTaskId": source_task_id,
        "sourcePipelineItemId": source_pipeline_item_id,
        "sourceDataVersion": source_package.get("dataVersion"),
        "sourcePackageId": source_package.get("packageId"),
        "testDataVersion": test_data_version,
        "testPipelineItemId": test_item_id,
        "purpose": purpose,
        "createdAt": _now(),
        "originalTaskReplacement": False,
        "rerunAgent1": False,
        "rerunActionPack": False,
        "rerunAgent2": False,
        "rerunAgent3": True,
    }

    lineage = dict(_dict(cloned.get("lineage")))
    lineage.update(
        currentStage=AGENT2_DRAFT_READY_STAGE,
        source="agent3_test_task_runner_v23217",
        sourceTaskId=source_task_id,
        sourcePipelineItemId=source_pipeline_item_id,
        sourceDataVersion=context["sourceDataVersion"],
        testDataVersion=test_data_version,
    )

    cloned.update(
        itemId=test_item_id,
        dataVersion=test_data_version,
        taskAdmissionAllowed=False,
        fallbackAllowed=False,
        outputContract="V22.5.agent2_draft_ready",
        testExecutionContext=context,
        lineage=lineage,
    )
    return canonicalize_agent2_draft_proof(cloned)


def _assert_worker_result(
    name: str,
    result: Dict[str, Any],
    *,
    completed_key: str,
) -> None:
    completed = int(result.get(completed_key) or 0)
    failed = int(result.get("failedItemCount") or 0)
    if completed != 1 or failed:
        raise RuntimeError(
            f"{name}_failed:completed={completed}:failed={failed}:"
            + json.dumps(result, ensure_ascii=False, default=str)
        )


def _annotate_materialized_test_task(
    *,
    test_data_version: str,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Attach test lineage without changing Agent3 SOP or task title."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM task_pool_entries
            WHERE data_version=? AND status='entered_task_pool'
            ORDER BY created_at DESC
            """,
            (test_data_version,),
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError(
                f"test_task_pool_entry_count_invalid:{len(rows)}:{test_data_version}"
            )
        row = rows[0]
        payload = _load_mapping(row["payload"])
        task = dict(_dict(payload.get("task")))
        snapshot = dict(_dict(payload.get("snapshot")))
        task.update(
            isTestTask=True,
            testTaskLabel="Agent3测试任务",
            testExecutionContext=context,
            sourceTaskId=context.get("sourceTaskId"),
            sourcePipelineItemId=context.get("sourcePipelineItemId"),
        )
        snapshot.update(
            isTestTask=True,
            testExecutionContext=context,
        )
        payload.update(
            task=task,
            snapshot=snapshot,
            isTestTask=True,
            testExecutionContext=context,
            source="v23_2_17_agent3_test_task_admission",
        )
        conn.execute(
            """
            UPDATE task_pool_entries
            SET payload=?,created_by=COALESCE(created_by,'agent3_test_runner'),updated_at=?
            WHERE pool_entry_id=?
            """,
            (dumps(payload), _now(), row["pool_entry_id"]),
        )

        snapshot_id = str(row["task_snapshot_id"] or "")
        if snapshot_id:
            snapshot_row = conn.execute(
                "SELECT payload,task_plan FROM task_snapshots WHERE task_snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
            if snapshot_row:
                snapshot_payload = _load_mapping(snapshot_row["payload"])
                snapshot_plan = _load_mapping(snapshot_row["task_plan"])
                snapshot_payload.update(
                    isTestTask=True,
                    testExecutionContext=context,
                )
                snapshot_plan.update(
                    isTestTask=True,
                    testExecutionContext=context,
                )
                conn.execute(
                    """
                    UPDATE task_snapshots
                    SET payload=?,task_plan=?,updated_at=?
                    WHERE task_snapshot_id=?
                    """,
                    (
                        dumps(snapshot_payload),
                        dumps(snapshot_plan),
                        _now(),
                        snapshot_id,
                    ),
                )
        conn.commit()
        return {
            "poolEntryId": row["pool_entry_id"],
            "taskSnapshotId": row["task_snapshot_id"],
            "taskId": row["task_id"],
        }


def rerun_agent3_as_test_task(
    *,
    source_task_id: str,
    source_pipeline_item_id: str,
    purpose: str = "verify_agent3_runtime",
    created_by: str | None = "agent3_test_runner",
) -> Dict[str, Any]:
    """Create one new lifecycle test task while leaving the source task unchanged."""
    ensure_pipeline_item_tables()
    source_item = _pipeline_item(source_pipeline_item_id)
    if not source_item:
        raise KeyError(f"source_pipeline_item_not_found:{source_pipeline_item_id}")
    source_task = _task_status(source_task_id)
    if not source_task:
        raise KeyError(f"source_task_not_found:{source_task_id}")
    bound_task_id = str(source_item.get("task_id") or "").strip()
    if bound_task_id and bound_task_id != source_task_id:
        raise ValueError(
            f"source_task_pipeline_binding_mismatch:{bound_task_id}:{source_task_id}"
        )

    source_item_before = _fingerprint(
        source_item,
        _SOURCE_PIPELINE_FINGERPRINT_FIELDS,
    )
    source_task_before = _fingerprint(
        source_task,
        _SOURCE_TASK_FINGERPRINT_FIELDS,
    )

    upstream_refs = _upstream_artifact_refs(source_item)
    agent2_draft_ref = str(upstream_refs.get("agent2DraftRef") or "").strip()
    if not agent2_draft_ref.startswith("ART-"):
        raise ValueError("source_agent2DraftRef_missing")

    source_package = resolve_artifact(agent2_draft_ref)
    if not isinstance(source_package, dict) or not source_package:
        raise ValueError("source_agent2_draft_artifact_empty")
    source_package = canonicalize_agent2_draft_proof(dict(source_package))
    missing = missing_agent2_draft_completed_contract(source_package)
    if missing:
        raise ValueError(
            "source_agent2_draft_contract_invalid:" + ",".join(missing)
        )

    test_data_version = _new_test_data_version()
    test_item_id = make_item_id(
        test_data_version,
        str(source_item.get("product_id") or source_package.get("productId") or ""),
        str(source_item.get("signal_id") or source_package.get("signalId") or ""),
        str(source_item.get("package_id") or source_package.get("packageId") or ""),
    )
    if _pipeline_item(test_item_id):
        raise RuntimeError(f"test_pipeline_item_collision:{test_item_id}")

    test_package = _clone_agent2_package(
        source_package,
        test_data_version=test_data_version,
        test_item_id=test_item_id,
        source_task_id=source_task_id,
        source_pipeline_item_id=source_pipeline_item_id,
        purpose=purpose,
    )
    test_missing = missing_agent2_draft_completed_contract(test_package)
    if test_missing:
        raise ValueError(
            "test_agent2_draft_contract_invalid:" + ",".join(test_missing)
        )

    output_ref = f"agent3_test_seed:{test_data_version}:{test_item_id}"
    envelope = build_item_envelope(
        data_version=test_data_version,
        item_id=test_item_id,
        product_id=source_item.get("product_id") or test_package.get("productId"),
        store_id=source_item.get("store_id") or test_package.get("storeId"),
        signal_id=source_item.get("signal_id") or test_package.get("signalId"),
        package_id=source_item.get("package_id") or test_package.get("packageId"),
        action_family=source_item.get("action_family")
        or test_package.get("lockedActionFamily")
        or test_package.get("actionFamily"),
        route=source_item.get("route") or test_package.get("route"),
        input_ref=agent2_draft_ref,
        output_ref=output_ref,
        stage=AGENT2_DRAFT_READY_STAGE,
        artifact_refs=upstream_refs,
    )
    envelope = upsert_pipeline_item(
        envelope,
        stage=AGENT2_DRAFT_READY_STAGE,
        status="ready",
        priority=1,
        output_ref=output_ref,
        payload=test_package,
    )
    record_pipeline_item_event(
        envelope,
        station_id="agent3_test_task_runner",
        stage=AGENT2_DRAFT_READY_STAGE,
        status="ready",
        input_ref=agent2_draft_ref,
        output_ref=output_ref,
        payload={
            "count": 1,
            "taskAdmissionAllowed": False,
            "testExecutionContext": test_package.get("testExecutionContext"),
        },
    )

    agent3_result = run_agent3_sop_microbatch_v225(
        test_data_version,
        user_id=created_by,
        batch_size=1,
    )
    _assert_worker_result(
        "agent3",
        agent3_result,
        completed_key="completedItemCount",
    )
    after_agent3 = _pipeline_item(test_item_id)
    if after_agent3.get("current_stage") != AGENT3_SOP_READY_STAGE:
        raise RuntimeError(
            f"agent3_test_stage_invalid:{after_agent3.get('current_stage')}"
        )

    mapping_result = run_task_mapping_microbatch_v225(
        test_data_version,
        batch_size=1,
    )
    _assert_worker_result(
        "task_mapping",
        mapping_result,
        completed_key="taskMappedCount",
    )
    after_mapping = _pipeline_item(test_item_id)
    if after_mapping.get("current_stage") != TASK_MAPPED_STAGE:
        raise RuntimeError(
            f"task_mapping_test_stage_invalid:{after_mapping.get('current_stage')}"
        )

    admission_result = run_task_pool_admission_microbatch_v225(
        test_data_version,
        user_id=created_by,
        batch_size=1,
        force_new_snapshot=True,
    )
    _assert_worker_result(
        "task_pool_admission",
        admission_result,
        completed_key="createdTaskCount",
    )
    after_admission = _pipeline_item(test_item_id)
    if after_admission.get("current_stage") != TASK_ADMITTED_STAGE:
        raise RuntimeError(
            f"task_admission_test_stage_invalid:{after_admission.get('current_stage')}"
        )

    context = dict(_dict(test_package.get("testExecutionContext")))
    materialized = _annotate_materialized_test_task(
        test_data_version=test_data_version,
        context=context,
    )
    lifecycle_sync = sync_task_pool_entries_to_task_status(
        data_version=test_data_version
    )
    frontend_refresh = refresh_task_pool_views(test_data_version)

    new_task_id = str(materialized.get("taskId") or "")
    if not new_task_id:
        raise RuntimeError("test_task_id_missing_after_admission")
    new_task = _task_status(new_task_id)
    if not new_task:
        raise RuntimeError(f"test_task_not_in_lifecycle:{new_task_id}")
    new_task_payload = _load_mapping(new_task.get("payload"))
    if new_task_payload.get("isTestTask") is not True:
        raise RuntimeError(f"test_task_marker_missing:{new_task_id}")

    source_item_after_row = _pipeline_item(source_pipeline_item_id)
    source_task_after_row = _task_status(source_task_id)
    source_item_after = _fingerprint(
        source_item_after_row,
        _SOURCE_PIPELINE_FINGERPRINT_FIELDS,
    )
    source_task_after = _fingerprint(
        source_task_after_row,
        _SOURCE_TASK_FINGERPRINT_FIELDS,
    )
    original_unchanged = (
        source_item_before == source_item_after
        and source_task_before == source_task_after
    )
    if not original_unchanged:
        raise RuntimeError("source_task_or_pipeline_item_changed_during_test_rerun")

    final_refs = artifact_refs_from_row(after_admission)
    return {
        "version": AGENT3_TEST_TASK_RUNNER_VERSION,
        "ok": True,
        "executionMode": TEST_EXECUTION_MODE,
        "purpose": purpose,
        "sourceTaskId": source_task_id,
        "sourcePipelineItemId": source_pipeline_item_id,
        "sourceDataVersion": source_item.get("data_version"),
        "sourcePackageId": source_item.get("package_id"),
        "testDataVersion": test_data_version,
        "testPipelineItemId": test_item_id,
        "testTaskId": new_task_id,
        "testTaskSnapshotId": materialized.get("taskSnapshotId"),
        "testPoolEntryId": materialized.get("poolEntryId"),
        "lifecycleStatus": new_task.get("status"),
        "lifecycleWorkflowStatus": new_task.get("workflow_status"),
        "agent2DraftRef": agent2_draft_ref,
        "agent3SopInputRef": final_refs.get("agent3SopInputRef"),
        "agent3SopRef": final_refs.get("agent3SopRef"),
        "taskMappingRef": final_refs.get("taskMappingRef"),
        "taskAdmissionRef": final_refs.get("taskAdmissionRef"),
        "agent2ProofBridgeVersion": AGENT2_PROOF_BRIDGE_VERSION,
        "agent3RuntimeVersion": PIPELINE_AGENT3_SOP_VERSION,
        "taskMappingVersion": PIPELINE_TASK_MAPPING_VERSION,
        "originalTaskUnchanged": original_unchanged,
        "reranAgent1": False,
        "reranActionPack": False,
        "reranAgent2": False,
        "reranAgent3": True,
        "forceNewSnapshot": True,
        "agent3Result": agent3_result,
        "taskMappingResult": mapping_result,
        "taskAdmissionResult": admission_result,
        "lifecycleSync": lifecycle_sync,
        "frontendRefresh": frontend_refresh,
    }


__all__ = [
    "AGENT3_TEST_TASK_RUNNER_VERSION",
    "TEST_EXECUTION_MODE",
    "rerun_agent3_as_test_task",
]
