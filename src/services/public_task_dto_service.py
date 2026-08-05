"""V23.2.12 canonical public task DTOs.

Public product APIs expose only operator-facing fields. Agent packages, provider
proof, artifact storage metadata and duplicate compatibility aliases remain in
internal storage or operations endpoints. Task detail also publishes the already
materialized frozen metric-evidence projection without recomputing it on GET.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

PUBLIC_TASK_DTO_VERSION = "23.2.12"
PUBLIC_TASK_LIST_DTO_VERSION = "22.2.3"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return [item for item in value if item not in (None, "", {}, [])] if isinstance(value, list) else []


def _first(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value not in (None, "", [], {}, "UNKNOWN", "未识别", "—", "未提供"):
            return value
    return default


def _clean_mapping(value: Any, allowed: Iterable[str]) -> Dict[str, Any]:
    source = _dict(value)
    return {
        key: source[key]
        for key in allowed
        if source.get(key) not in (None, "", [], {})
    }


def _compact_public(value: Dict[str, Any], *, keep: Iterable[str] = ()) -> Dict[str, Any]:
    required = set(keep)
    return {
        key: item
        for key, item in value.items()
        if key in required or item not in (None, "", [], {})
    }


def _public_tree(value: Any, *, depth: int = 0) -> Any:
    """Copy derived evidence values while dropping unsupported runtime objects."""
    if depth > 8:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        result: List[Any] = []
        for item in value:
            projected = _public_tree(item, depth=depth + 1)
            if projected not in (None, "", [], {}):
                result.append(projected)
        return result
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            projected = _public_tree(item, depth=depth + 1)
            if projected not in (None, "", [], {}):
                result[str(key)] = projected
        return result
    return str(value)


def _product(value: Any) -> Dict[str, Any]:
    return _clean_mapping(
        value,
        (
            "productId",
            "productTitle",
            "title",
            "storeId",
            "storeName",
            "platform",
            "verticalCategory",
            "productRole",
            "lifecycleStage",
            "erpProductCode",
            "skuId",
            "spuId",
        ),
    )


def _metric_definition(value: Any) -> Dict[str, Any]:
    return _clean_mapping(
        value,
        (
            "code",
            "label",
            "group",
            "kind",
            "evidenceRole",
            "taskUsage",
        ),
    )


def _metric_observation(value: Any) -> Dict[str, Any]:
    source = _dict(value)
    result = _clean_mapping(
        source,
        (
            "businessDate",
            "dataVersion",
            "snapshotId",
        ),
    )
    source_versions = [str(item).strip() for item in _list(source.get("sourceDataVersions")) if str(item).strip()]
    if source_versions:
        result["sourceDataVersions"] = source_versions
    metrics = _public_tree(_dict(source.get("metrics")))
    changes = _public_tree(_dict(source.get("changes")))
    if metrics:
        result["metrics"] = metrics
    if changes:
        result["changes"] = changes
    return result


def _task_metric_evidence_projection(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    report = _dict(snapshot.get("taskDetailReport"))
    related = _dict(snapshot.get("relatedTask"))
    plan = _dict(
        _first(
            snapshot.get("taskPlan"),
            report.get("taskPlan"),
            related.get("taskPlan"),
            {},
        )
    )
    related_plan = _dict(related.get("taskPlan"))
    source = _dict(
        _first(
            snapshot.get("taskMetricEvidenceProjection"),
            report.get("taskMetricEvidenceProjection"),
            plan.get("taskMetricEvidenceProjection"),
            related.get("taskMetricEvidenceProjection"),
            related_plan.get("taskMetricEvidenceProjection"),
            {},
        )
    )
    if not source:
        return {}

    result = _clean_mapping(
        source,
        (
            "version",
            "actionFamily",
            "frozenAtTaskCreation",
            "frozenAt",
            "sourceDataVersion",
            "productId",
            "storeId",
            "historicalEvidenceReferenced",
            "source",
            "readRule",
            "ready",
            "evidenceStatus",
            "taskExecutableFromEvidence",
            "reason",
        ),
    )
    referenced_codes = [str(item).strip() for item in _list(source.get("referencedMetricCodes")) if str(item).strip()]
    if referenced_codes:
        result["referencedMetricCodes"] = referenced_codes

    definitions = [
        cleaned
        for item in _list(source.get("metricDefinitions"))
        if (cleaned := _metric_definition(item))
    ]
    if definitions:
        result["metricDefinitions"] = definitions

    snapshots = [
        cleaned
        for item in _list(source.get("recentSnapshots"))
        if (cleaned := _metric_observation(item))
    ]
    if snapshots:
        result["recentSnapshots"] = snapshots

    trends = _public_tree(_dict(source.get("metricTrends")))
    if trends:
        result["metricTrends"] = trends

    reference_window = _clean_mapping(
        source.get("referenceWindow"),
        (
            "snapshotCount",
            "startBusinessDate",
            "endBusinessDate",
            "dataCompleteness",
        ),
    )
    if reference_window:
        result["referenceWindow"] = reference_window

    observation_ids = [str(item).strip() for item in _list(source.get("sourceObservationIds")) if str(item).strip()]
    if observation_ids:
        result["sourceObservationIds"] = observation_ids
    return result


def _task_evidence_display_contract(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    report = _dict(snapshot.get("taskDetailReport"))
    related = _dict(snapshot.get("relatedTask"))
    source = _dict(
        _first(
            snapshot.get("detailDisplayContract"),
            report.get("detailDisplayContract"),
            related.get("detailDisplayContract"),
            {},
        )
    )
    return _clean_mapping(
        source,
        (
            "version",
            "readMode",
            "taskEvidenceFrozen",
            "taskEvidenceStatus",
            "taskEvidenceRequiredForExecution",
            "emptyDynamicMetricChangesMeansBaseline",
            "taskEvidenceRule",
        ),
    )


def _authorization_summary(value: Any) -> Dict[str, Any]:
    source = _dict(value)
    effective = _clean_mapping(
        source.get("effectiveLimits"),
        (
            "budgetChangeCeiling",
            "budgetChangeFloor",
            "roasTargetMin",
            "roasTargetMax",
            "discountCeiling",
            "maxDailyBudget",
        ),
    )
    result = _clean_mapping(
        source,
        (
            "decision",
            "reason",
            "approvalRequired",
            "requiredAuthorityLevel",
            "operatorId",
            "reviewerId",
        ),
    )
    if effective:
        result["effectiveLimits"] = effective
    return result


def _lifecycle(value: Any, *, status: Any = None) -> Dict[str, Any]:
    source = _dict(value)
    result = _clean_mapping(
        source,
        (
            "stage",
            "stageLabel",
            "nextExpected",
            "acceptedAt",
            "submittedAt",
            "reviewedAt",
            "completedAt",
            "reviewDueAt",
        ),
    )
    if not result and status not in (None, ""):
        result = {
            "stage": str(status),
            "stageLabel": str(status),
            "nextExpected": "查看详情",
        }
    return result


def _actions(value: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        label = str(item.get("label") or "").strip()
        if not action or not label:
            continue
        result.append(
            {
                "action": action,
                "label": label,
                "primary": bool(item.get("primary")),
            }
        )
    return result


def project_task_list_item(value: Dict[str, Any]) -> Dict[str, Any]:
    product = _product(value.get("productIdentity"))
    task_id = str(_first(value.get("taskId"), value.get("task_id"), value.get("id"), default=""))
    status = _first(value.get("status"), value.get("workflowStatus"), value.get("displayStatus"), default="待接收")
    authorization = _authorization_summary(
        value.get("authorizationDecision")
        or value.get("actionAuthorization")
        or _dict(value.get("taskPlan")).get("authorizationDecision")
    )
    result = {
        "version": PUBLIC_TASK_LIST_DTO_VERSION,
        "taskId": task_id,
        "dataVersion": _first(value.get("dataVersion"), value.get("workflowRunId"), value.get("workflow_run_id")),
        "title": _first(value.get("title"), value.get("taskTitle"), _dict(value.get("taskCard")).get("title"), product.get("productTitle"), product.get("title"), default="经营任务"),
        "status": status,
        "taskLayer": _first(value.get("taskLayer"), value.get("task_layer"), default="operator_execution"),
        "taskType": value.get("taskType"),
        "riskLevel": value.get("riskLevel"),
        "priority": _first(value.get("priority"), value.get("riskLevel"), default="中"),
        "assigneeId": value.get("assigneeId"),
        "reviewerId": value.get("reviewerId"),
        "storeId": _first(value.get("storeId"), product.get("storeId")),
        "storeName": _first(value.get("storeName"), product.get("storeName")),
        "platform": _first(value.get("platform"), product.get("platform")),
        "productId": _first(value.get("productId"), product.get("productId")),
        "productTitle": _first(value.get("productTitle"), product.get("productTitle"), product.get("title")),
        "productIdentity": product,
        "actionFamily": _first(value.get("actionFamily"), _dict(value.get("taskPlan")).get("selectedActionFamily")),
        "reason": _first(value.get("reason"), _dict(value.get("taskPlan")).get("displayReason"), _dict(value.get("taskPlan")).get("reason")),
        "executionDeadline": _first(value.get("executionDeadline"), value.get("deadline"), _dict(value.get("taskPlan")).get("executionDeadline"), default="6小时内"),
        "taskLifecycle": _lifecycle(value.get("taskLifecycle"), status=status),
        "visibleTaskActions": _actions(value.get("visibleTaskActions") or value.get("availableActions")),
        "authorizationDecision": authorization,
        "approvalRequired": bool(authorization.get("approvalRequired")),
        "updatedAt": value.get("updatedAt") or value.get("updated_at"),
        "createdAt": value.get("createdAt") or value.get("created_at"),
    }
    return _compact_public(
        result,
        keep=(
            "version",
            "taskId",
            "title",
            "status",
            "taskLayer",
            "priority",
            "executionDeadline",
            "visibleTaskActions",
            "approvalRequired",
        ),
    )


def _judgment_summary(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    operator_view = _dict(snapshot.get("operatorJudgmentView"))
    agent_view = _dict(snapshot.get("agentOperatingJudgment") or snapshot.get("agentJudgment"))
    active = _dict(snapshot.get("activeActionContract"))
    return _compact_public(
        {
            "summary": _first(operator_view.get("summary"), operator_view.get("decisionSummary"), agent_view.get("decisionSummary"), agent_view.get("finding"), snapshot.get("reason")),
            "coreProblem": _first(operator_view.get("coreProblem"), agent_view.get("coreProblem")),
            "actionFamily": _first(active.get("actionFamily"), snapshot.get("actionFamily")),
            "confidence": _first(operator_view.get("confidence"), agent_view.get("confidence")),
            "riskBoundaries": _list(operator_view.get("riskBoundaries") or agent_view.get("riskBoundaries")),
            "facts": _list(operator_view.get("facts") or agent_view.get("facts"))[:8],
        }
    )


def project_task_detail(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = dict(_dict(snapshot))
    report = _dict(snapshot.get("taskDetailReport"))
    related = _dict(snapshot.get("relatedTask"))
    plan = _dict(_first(snapshot.get("taskPlan"), report.get("taskPlan"), related.get("taskPlan"), {}))
    task_id = str(_first(snapshot.get("taskId"), snapshot.get("task_id"), snapshot.get("id"), default=""))
    status = _first(snapshot.get("taskStatus"), snapshot.get("status"), related.get("status"), default="待接收")
    product = _product(
        _first(
            snapshot.get("productIdentity"),
            report.get("productIdentity"),
            related.get("productIdentity"),
            {},
        )
    )
    active = _dict(snapshot.get("activeActionContract"))
    sop = [str(item).strip() for item in _list(snapshot.get("operatorExecutionSop")) if str(item).strip()]
    authorization = _authorization_summary(
        snapshot.get("authorizationDecision") or snapshot.get("actionAuthorization")
    )
    review = _clean_mapping(
        snapshot.get("autoReviewPlan") or snapshot.get("autoRecapPlan"),
        (
            "reviewCycle",
            "reviewAt",
            "reviewDueAt",
            "reviewMetrics",
            "successCriteria",
            "stopConditions",
            "observationWindows",
        ),
    )
    evidence_requirements = _list(
        snapshot.get("evidenceRequirements")
        or report.get("evidenceRequirements")
        or _dict(report.get("taskPlan")).get("evidenceRequirements")
    )
    evidence = _task_metric_evidence_projection(snapshot)
    evidence_version = _first(
        snapshot.get("taskMetricEvidenceProjectionVersion"),
        report.get("taskMetricEvidenceProjectionVersion"),
        plan.get("taskMetricEvidenceProjectionVersion"),
        related.get("taskMetricEvidenceProjectionVersion"),
        evidence.get("version"),
    )
    evidence_status = _first(
        snapshot.get("taskEvidenceStatus"),
        report.get("taskEvidenceStatus"),
        related.get("taskEvidenceStatus"),
        evidence.get("evidenceStatus"),
    )
    evidence_executable = _first(
        snapshot.get("taskEvidenceExecutable"),
        report.get("taskEvidenceExecutable"),
        related.get("taskEvidenceExecutable"),
        evidence.get("taskExecutableFromEvidence"),
    )
    evidence_blocked = _first(
        snapshot.get("evidenceExecutionBlocked"),
        report.get("evidenceExecutionBlocked"),
        related.get("evidenceExecutionBlocked"),
    )
    if evidence_blocked is None and evidence_executable is not None:
        evidence_blocked = not bool(evidence_executable)
    evidence_display_contract = _task_evidence_display_contract(snapshot)

    result = {
        "version": PUBLIC_TASK_DTO_VERSION,
        "ready": bool(snapshot.get("ready", True) and task_id),
        "taskId": task_id,
        "dataVersion": snapshot.get("dataVersion"),
        "title": _first(snapshot.get("title"), related.get("title"), default="任务详情"),
        "taskStatus": status,
        "productIdentity": product,
        "judgmentSummary": _judgment_summary(snapshot),
        "activeActionContract": active,
        "operatorExecutionSop": sop,
        "evidenceRequirements": evidence_requirements,
        "taskMetricEvidenceProjection": evidence,
        "taskMetricEvidenceProjectionVersion": evidence_version,
        "taskEvidenceStatus": evidence_status,
        "taskEvidenceExecutable": evidence_executable,
        "evidenceExecutionBlocked": evidence_blocked,
        "detailDisplayContract": evidence_display_contract,
        "authorizationDecision": authorization,
        "approvalRequired": bool(authorization.get("approvalRequired")),
        "metricDigest": _dict(snapshot.get("metricDigest")),
        "autoReviewPlan": review,
        "taskLifecycle": _lifecycle(snapshot.get("taskLifecycle"), status=status),
        "snapshotUpdatedAt": snapshot.get("snapshotUpdatedAt") or snapshot.get("lifecycleUpdatedAt"),
        "publicContract": {
            "version": PUBLIC_TASK_DTO_VERSION,
            "canonicalIdField": "taskId",
            "canonicalStatusField": "taskStatus",
            "canonicalActionField": "activeActionContract",
            "canonicalEvidenceField": "taskMetricEvidenceProjection",
            "taskMetricEvidenceProjectionReturned": bool(evidence),
            "taskMetricEvidenceProjectionVersion": evidence_version,
            "evidenceRecomputedOnRead": False,
            "internalAgentPayloadReturned": False,
            "providerProofReturned": False,
            "artifactMetadataReturned": False,
        },
    }
    return _compact_public(
        result,
        keep=(
            "version",
            "ready",
            "taskId",
            "title",
            "taskStatus",
            "productIdentity",
            "judgmentSummary",
            "activeActionContract",
            "operatorExecutionSop",
            "evidenceRequirements",
            "taskMetricEvidenceProjection",
            "taskMetricEvidenceProjectionVersion",
            "taskEvidenceStatus",
            "taskEvidenceExecutable",
            "evidenceExecutionBlocked",
            "detailDisplayContract",
            "authorizationDecision",
            "approvalRequired",
            "metricDigest",
            "autoReviewPlan",
            "taskLifecycle",
            "publicContract",
        ),
    )


def project_task_list_response(result: Dict[str, Any]) -> Dict[str, Any]:
    items = [project_task_list_item(item) for item in _list(result.get("items")) if isinstance(item, dict)]
    response = {
        "version": PUBLIC_TASK_LIST_DTO_VERSION,
        "ready": bool(result.get("ready", True)),
        "count": len(items),
        "items": items,
        "currentDataVersion": result.get("currentDataVersion"),
        "detailEndpoint": "/api/view/tasks/{task_id}",
        "publicContract": {
            "version": PUBLIC_TASK_LIST_DTO_VERSION,
            "duplicateIdAliases": False,
            "duplicateStatusAliases": False,
            "duplicateActionAliases": False,
            "heavyPayloadReturned": False,
        },
    }
    return _compact_public(
        response,
        keep=("version", "ready", "count", "items", "detailEndpoint", "publicContract"),
    )


__all__ = [
    "PUBLIC_TASK_DTO_VERSION",
    "PUBLIC_TASK_LIST_DTO_VERSION",
    "project_task_list_item",
    "project_task_detail",
    "project_task_list_response",
]
