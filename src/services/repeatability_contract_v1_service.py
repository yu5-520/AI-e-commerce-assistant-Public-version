"""Repeatability fingerprint for the competition task set.

The fingerprint proves business-repeatability, not execution identity. It therefore
projects each admitted task to the business contract that an operator actually
receives: product identity, selected action/problem/target, responsibility, relative
time contract, SOP steps, evidence requirements, review metrics, permission/budget
and visible task language. Runtime ids, absolute timestamps, dataVersion, Artifact
refs/hashes and Provider/Execution identity never enter the TaskSetSemanticHash.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, loads

REPEATABILITY_CONTRACT_VERSION = "1.1.0"
REPEATABILITY_SCHEMA = "competition.task_set_semantic_hash.v1"

_EXCLUDED_KEYS = {
    "id",
    "taskId",
    "task_id",
    "poolEntryId",
    "pool_entry_id",
    "taskSnapshotId",
    "task_snapshot_id",
    "snapshotId",
    "snapshot_id",
    "dataVersion",
    "data_version",
    "sourceDataVersion",
    "sourceDataVersions",
    "executionHash",
    "ExecutionHash",
    "itemExecutionId",
    "inputContentHash",
    "outputContentHash",
    "sourceContentHash",
    "sourceHash",
    "contentHash",
    "artifactRefs",
    "sourceArtifactRefs",
    "taskRef",
    "inputRef",
    "outputRef",
    "createdAt",
    "updatedAt",
    "created_at",
    "updated_at",
    "admittedAt",
    "submittedAt",
    "reviewedAt",
    "deadlineAt",
    "dueAt",
    "correlationId",
    "signalId",
    "signal_id",
    "packageId",
    "package_id",
    "pipelineItemId",
    "sourceEvent",
    "dedupeKey",
    "businessEventId",
}


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value not in (None, ""):
        return [value]
    return []


def _clean_dict(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: child
        for key, child in value.items()
        if child not in (None, "", [], {}) or child in (0, False)
    }


def semantic_projection(value: Any) -> Any:
    """Remove known execution identity while preserving business list order."""
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if name in _EXCLUDED_KEYS:
                continue
            projected = semantic_projection(child)
            if projected in (None, "", [], {}) and child not in (0, False):
                continue
            result[name] = projected
        return result
    if isinstance(value, list):
        # SOP/evidence order is business meaning, so do not sort nested lists.
        return [semantic_projection(item) for item in value]
    return value


def task_business_projection(value: Dict[str, Any]) -> Dict[str, Any]:
    """Project one pool payload/task DTO to stable operator-facing business semantics."""
    wrapper = _dict(value)
    task = _dict(wrapper.get("task")) or wrapper
    snapshot = _dict(wrapper.get("snapshot"))
    detail = _dict(task.get("taskDetailReport"))
    plan = (
        _dict(detail.get("taskPlan"))
        or _dict(task.get("taskPlan"))
        or _dict(snapshot.get("taskPlan"))
    )
    active = (
        _dict(task.get("activeActionContract"))
        or _dict(plan.get("activeActionContract"))
    )
    product = (
        _dict(task.get("productIdentity"))
        or _dict(detail.get("productIdentity"))
        or _dict(plan.get("productIdentity"))
    )
    ownership = _dict(task.get("ownership"))
    judgment = (
        _dict(task.get("agentOperatingJudgment"))
        or _dict(task.get("agentJudgment"))
    )
    completion = _dict(task.get("completionGate"))
    execution_sop = (
        _dict(task.get("operatorExecutionSop"))
        or _dict(detail.get("operatorExecutionSop"))
    )

    sop_steps = (
        _list(task.get("sopSteps"))
        or _list(task.get("executionRequirements"))
        or _list(plan.get("sopSteps"))
        or _list(plan.get("steps"))
        or _list(execution_sop.get("executionSteps"))
    )
    evidence_requirements = (
        _list(completion.get("requiredEvidence"))
        or _list(plan.get("evidenceRequirements"))
        or _list(task.get("evidenceRequirements"))
    )
    review_metrics = _list(task.get("reviewMetrics")) or _list(plan.get("reviewMetrics"))

    product_projection = _clean_dict(
        {
            "productId": product.get("productId") or task.get("productId") or task.get("entityId"),
            "storeId": product.get("storeId") or (task.get("storeIds") or [None])[0] if isinstance(task.get("storeIds"), list) else product.get("storeId"),
            "platform": product.get("platform") or task.get("platform"),
            "skuId": product.get("skuId"),
            "platformItemId": product.get("platformItemId"),
            "verticalCategory": product.get("verticalCategory"),
        }
    )

    action_projection = _clean_dict(
        {
            "actionFamily": (
                active.get("actionFamily")
                or plan.get("actionFamily")
                or plan.get("selectedActionFamily")
                or task.get("actionFamily")
            ),
            "taskType": task.get("taskType") or plan.get("taskType"),
            "actionType": plan.get("actionType") or task.get("actionType"),
            "riskDomain": task.get("riskDomain") or plan.get("riskDomain"),
            "primaryProblem": (
                active.get("primaryProblem")
                or plan.get("primaryProblem")
                or plan.get("primaryOperatingGap")
                or judgment.get("primaryOperatingGap")
            ),
            "executionTarget": (
                active.get("executionTarget")
                or plan.get("executionTarget")
                or product_projection.get("productId")
            ),
            "selectedOperatingRoute": (
                active.get("selectedOperatingRoute")
                or plan.get("selectedOperatingRoute")
            ),
        }
    )

    language_projection = _clean_dict(
        {
            "title": task.get("title") or plan.get("title"),
            "subtitle": task.get("subtitle") or plan.get("subtitle"),
            "reason": (
                task.get("reason")
                or detail.get("warningSummary")
                or plan.get("reason")
                or judgment.get("judgment")
            ),
            "businessHypothesis": plan.get("businessHypothesis"),
            "operatingScenario": plan.get("operatingScenario"),
        }
    )

    authority_projection = _clean_dict(
        {
            "taskLayer": task.get("taskLayer"),
            "assigneeId": task.get("assigneeId") or ownership.get("assignedOperatorId"),
            "runtimeActorMode": ownership.get("runtimeActorMode"),
            "permissionDecision": task.get("permissionDecision") or plan.get("permissionDecision"),
            "enterpriseApprovalRequired": task.get("enterpriseApprovalRequired"),
        }
    )

    time_projection = _clean_dict(
        {
            "priority": task.get("priority") or plan.get("priority"),
            "deadline": task.get("deadline") or plan.get("deadline"),
            "executionDeadline": task.get("executionDeadline") or plan.get("executionDeadline"),
            "deadlineMinutes": task.get("deadlineMinutes"),
            "followUpDeadline": task.get("followUpDeadline") or plan.get("followUpDeadline"),
            "reviewCycle": task.get("reviewCycle") or plan.get("reviewCycle"),
            "recapCycle": task.get("recapCycle") or plan.get("recapCycle"),
        }
    )

    return semantic_projection(
        _clean_dict(
            {
                "schema": "competition.task_business_projection.v1",
                "decision": task.get("decision") or snapshot.get("decision"),
                "product": product_projection,
                "action": action_projection,
                "language": language_projection,
                "authority": authority_projection,
                "time": time_projection,
                "sopSource": task.get("sopSource") or plan.get("sopSource"),
                "sopSteps": sop_steps,
                "evidenceRequirements": evidence_requirements,
                "reviewMetrics": review_metrics,
                "operationBudget": task.get("operationBudget") or plan.get("operationBudget"),
            }
        )
    )


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
    )


def _task_pool_payloads(data_version: str | None = None) -> List[Dict[str, Any]]:
    with connect() as conn:
        if not _table_exists(conn, "task_pool_entries"):
            return []
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(task_pool_entries)").fetchall()
        }
        where = ""
        params: List[Any] = []
        if data_version and "data_version" in columns:
            where = " WHERE data_version=?"
            params.append(data_version)
        order_col = "updated_at" if "updated_at" in columns else "rowid"
        rows = conn.execute(
            f"SELECT * FROM task_pool_entries{where} ORDER BY {order_col} ASC",
            tuple(params),
        ).fetchall()

    result: List[Dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        payload: Any = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = loads(payload)
            except Exception:
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
        result.append(payload if isinstance(payload, dict) else row)
    return result


def task_set_semantic_hash(
    *,
    data_version: str | None = None,
    tasks: Iterable[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    source_items = (
        [item for item in tasks if isinstance(item, dict)]
        if tasks is not None
        else _task_pool_payloads(data_version=data_version)
    )
    items = [task_business_projection(item) for item in source_items]
    # Task-set order is not business meaning; individual SOP/evidence list order is.
    items = sorted(items, key=_stable_json)
    digest = _sha256(
        {
            "schema": REPEATABILITY_SCHEMA,
            "version": REPEATABILITY_CONTRACT_VERSION,
            "tasks": items,
        }
    )
    return {
        "schema": REPEATABILITY_SCHEMA,
        "version": REPEATABILITY_CONTRACT_VERSION,
        "dataVersion": data_version,
        "taskCount": len(items),
        "taskSetSemanticHash": digest,
        "identityExcluded": sorted(_EXCLUDED_KEYS),
        "projectionSchema": "competition.task_business_projection.v1",
        "taskSemantics": items,
        "rule": (
            "Task count and operator-facing business semantic hash must both match "
            "across clean runs of the same three reports."
        ),
    }


def compare_repeatability(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    equal_count = int(left.get("taskCount") or 0) == int(right.get("taskCount") or 0)
    equal_hash = str(left.get("taskSetSemanticHash") or "") == str(
        right.get("taskSetSemanticHash") or ""
    )
    return {
        "version": REPEATABILITY_CONTRACT_VERSION,
        "passed": bool(equal_count and equal_hash),
        "taskCountMatch": equal_count,
        "taskSetSemanticHashMatch": equal_hash,
        "left": {
            "taskCount": left.get("taskCount"),
            "taskSetSemanticHash": left.get("taskSetSemanticHash"),
        },
        "right": {
            "taskCount": right.get("taskCount"),
            "taskSetSemanticHash": right.get("taskSetSemanticHash"),
        },
    }


__all__ = [
    "REPEATABILITY_CONTRACT_VERSION",
    "REPEATABILITY_SCHEMA",
    "semantic_projection",
    "task_business_projection",
    "task_set_semantic_hash",
    "compare_repeatability",
]
