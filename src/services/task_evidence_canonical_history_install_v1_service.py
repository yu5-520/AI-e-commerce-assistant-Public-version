"""Install canonical-history repair over the frozen task evidence projection.

The legacy V21.7.8 installer remains the owner of metric-reference semantics and the
base task-detail composition.  This additive repair runs afterwards and only replaces
the history source used to materialize task evidence:

- canonical current-epoch snapshot metadata is the only history authority;
- one product is slim-read from one canonical snapshot row at a time;
- the task dataVersion/creation boundary remains fail-closed;
- old materialized task-detail snapshots are made stale by a dedicated source version;
- Agent1/2/3, task admission and evidence execution thresholds are unchanged.
"""
from __future__ import annotations

from typing import Any, Dict

from src.services import task_metric_evidence_projection_v2178_service as evidence
from src.services.task_evidence_canonical_history_v1_service import (
    task_bounded_canonical_product_snapshots,
)

TASK_EVIDENCE_CANONICAL_INSTALL_VERSION = "1.0.0"
TASK_EVIDENCE_CANONICAL_PROJECTION_VERSION = "22.4.0-canonical-history-v1"
TASK_DETAIL_CANONICAL_EVIDENCE_SOURCE_VERSION = "22.4.0-task-evidence-canonical-v1"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strip_materialized_evidence(value: Any) -> Any:
    """Remove only prior materialized Evidence fields before deterministic rebuild."""
    blocked = {
        "taskMetricEvidenceProjection",
        "taskMetricEvidenceProjectionVersion",
        "taskEvidenceStatus",
        "taskEvidenceExecutable",
        "evidenceExecutionBlocked",
        "taskEvidenceFrozen",
    }
    if isinstance(value, dict):
        return {
            key: _strip_materialized_evidence(child)
            for key, child in value.items()
            if key not in blocked
        }
    if isinstance(value, list):
        return [_strip_materialized_evidence(child) for child in value]
    return value


def build_canonical_task_metric_evidence_projection(source_task: Dict[str, Any]) -> Dict[str, Any]:
    task = _strip_materialized_evidence(dict(_dict(source_task)))
    identity = evidence._product_identity(task)
    data_version, _cutoff, raw_boundary_time = evidence._task_boundary(task)
    history = task_bounded_canonical_product_snapshots(
        identity,
        source_data_version=data_version or None,
        frozen_at=raw_boundary_time or None,
    )
    projection = evidence.build_task_metric_evidence_projection(
        task,
        snapshots=history.get("snapshots") or [],
    )
    projection = dict(_dict(projection))
    projection.update(
        version=TASK_EVIDENCE_CANONICAL_PROJECTION_VERSION,
        canonicalHistoryAdapterVersion=history.get("version"),
        snapshotAuthority=history.get("snapshotAuthority"),
        legacySnapshotFallbackUsed=False,
        wholeSnapshotRetention=False,
        historyScanMode=history.get("historyScanMode"),
        historyEpochId=history.get("historyEpochId"),
        historyEpochStartedAt=history.get("historyEpochStartedAt"),
        historyIdentityHash=history.get("historyIdentityHash"),
        historyBoundarySourceDataVersion=history.get("sourceDataVersion"),
        historyBoundaryFrozenAt=history.get("frozenAt"),
        historyMatchedProductId=history.get("matchedProductId"),
        historyCandidateSnapshotCount=history.get("candidateSnapshotCount"),
        historyMatchedSnapshotCount=history.get("matchedSnapshotCount"),
        historyResolutionReason=history.get("reason"),
    )
    return projection


def _attach_projection(snapshot: Dict[str, Any], source_task: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(_dict(snapshot))
    projection = build_canonical_task_metric_evidence_projection(source_task)

    report = dict(_dict(result.get("taskDetailReport")))
    related = dict(_dict(result.get("relatedTask")))
    plan = dict(_dict(report.get("taskPlan") or related.get("taskPlan")))

    plan["taskMetricEvidenceProjection"] = projection
    plan["taskMetricEvidenceProjectionVersion"] = TASK_EVIDENCE_CANONICAL_PROJECTION_VERSION
    report["taskPlan"] = plan
    report["taskMetricEvidenceProjection"] = projection
    report["taskMetricEvidenceProjectionVersion"] = TASK_EVIDENCE_CANONICAL_PROJECTION_VERSION
    related["taskPlan"] = plan
    related["taskMetricEvidenceProjection"] = projection
    related["taskMetricEvidenceProjectionVersion"] = TASK_EVIDENCE_CANONICAL_PROJECTION_VERSION

    display_contract = dict(_dict(result.get("detailDisplayContract")))
    display_contract.update(
        {
            "taskMetricEvidenceProjectionVersion": TASK_EVIDENCE_CANONICAL_PROJECTION_VERSION,
            "taskEvidenceFrozen": bool(projection.get("frozenAtTaskCreation")),
            "taskEvidenceStatus": projection.get("evidenceStatus"),
            "taskEvidenceRequiredForExecution": True,
            "taskEvidenceCanonicalHistoryRequired": True,
            "taskEvidenceSnapshotAuthority": projection.get("snapshotAuthority"),
            "emptyDynamicMetricChangesMeansBaseline": False,
            "taskEvidenceRule": "正式任务只展示任务创建边界内 canonical 历史冻结证据；证据不足仍 fail-closed。",
        }
    )

    result.update(
        {
            "taskDetailReport": report,
            "relatedTask": related,
            "taskMetricEvidenceProjection": projection,
            "taskMetricEvidenceProjectionVersion": TASK_EVIDENCE_CANONICAL_PROJECTION_VERSION,
            "taskEvidenceStatus": projection.get("evidenceStatus"),
            "taskEvidenceExecutable": bool(projection.get("taskExecutableFromEvidence")),
            "evidenceExecutionBlocked": not bool(projection.get("taskExecutableFromEvidence")),
            "detailDisplayContract": display_contract,
        }
    )
    return result


def install_task_evidence_canonical_history_v1() -> None:
    from src.services import task_detail_snapshot_v2024_service as task_detail

    if getattr(task_detail, "_TASK_EVIDENCE_CANONICAL_HISTORY_V1_INSTALLED", False):
        return

    original = task_detail.build_task_detail_snapshot

    def build_task_detail_snapshot(task: Dict[str, Any]) -> Dict[str, Any]:
        return _attach_projection(original(task), task)

    task_detail.build_task_detail_snapshot = build_task_detail_snapshot
    # Force old 22.4.0 evidence_missing materializations to read-through rebuild from
    # task_status without deleting task/hash/canonical facts.
    task_detail.TASK_DETAIL_SNAPSHOT_VERSION = TASK_DETAIL_CANONICAL_EVIDENCE_SOURCE_VERSION
    task_detail.TASK_METRIC_EVIDENCE_PROJECTION_VERSION = TASK_EVIDENCE_CANONICAL_PROJECTION_VERSION
    task_detail._TASK_EVIDENCE_CANONICAL_HISTORY_V1_INSTALLED = True


__all__ = [
    "TASK_DETAIL_CANONICAL_EVIDENCE_SOURCE_VERSION",
    "TASK_EVIDENCE_CANONICAL_INSTALL_VERSION",
    "TASK_EVIDENCE_CANONICAL_PROJECTION_VERSION",
    "build_canonical_task_metric_evidence_projection",
    "install_task_evidence_canonical_history_v1",
]
