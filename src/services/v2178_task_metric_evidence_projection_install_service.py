"""Install V21.7.8 frozen task metric evidence on the canonical task-detail writer.

This is not a read-time fallback. Every new or rebuilt materialized task-detail
snapshot receives one immutable ``taskMetricEvidenceProjection``. Existing
V21.7.7 snapshots become stale through ``TASK_DETAIL_PROJECTION_VERSION`` and are
rebuilt from task_status on first read or explicit backfill.

Metric references are admitted only from metric-shaped keys and explicit evidence
text fields. Values such as ``actionFamily=roas_scale`` never fabricate an ROI
reference merely because the family name contains ``roas``. Snapshot rows are
cached by the database count/max-update stamp so a 500-task backfill parses the
same product history once, while a newly imported report invalidates the cache.
"""

from __future__ import annotations

from threading import Lock
from typing import Any, Dict, Iterable, List, Tuple

from src.repositories.sqlite_repository import connect
from src.services import task_metric_evidence_projection_v2178_service as evidence
from src.services.task_metric_evidence_projection_v2178_service import (
    TASK_METRIC_EVIDENCE_PROJECTION_VERSION,
    build_task_metric_evidence_projection,
)

_METRIC_TEXT_FIELDS = {
    "metric",
    "metricCode",
    "metricName",
    "code",
    "name",
    "label",
    "reviewMetric",
    "reviewMetrics",
    "referencedMetricCodes",
    "primaryBusinessSignal",
    "primaryOperatingGap",
    "judgmentBasis",
    "judgmentBasisText",
    "evidenceFacts",
    "summary",
    "fact",
    "reason",
    "finding",
    "text",
    "value",
    "businessHypothesis",
    "displayReason",
    "riskBoundary",
    "executionFocus",
    "testFocus",
}

_ORIGINAL_SNAPSHOT_ROWS = evidence._snapshot_rows
_SNAPSHOT_CACHE_LOCK = Lock()
_SNAPSHOT_CACHE: Dict[str, Any] = {"stamp": None, "limit": None, "rows": None}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_values(value: Any, *, depth: int = 0) -> Iterable[str]:
    if depth > 6:
        return
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, list):
        for child in value[:60]:
            yield from _string_values(child, depth=depth + 1)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _string_values(child, depth=depth + 1)


def strict_referenced_metric_codes(source: Dict[str, Any]) -> List[str]:
    found: List[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        code = evidence._code_from_token(value)
        if code and code not in seen:
            seen.add(code)
            found.append(code)

    for key, value in evidence._walk(source):
        # Metric-shaped keys such as currentROI, gmv, adSpend and availableDays
        # are valid references even when their values are numeric.
        add(key)
        # Free-form strings are inspected only when their parent field is an
        # explicit judgment/evidence field. actionFamily and route values are
        # deliberately excluded.
        if key in _METRIC_TEXT_FIELDS:
            for token in _string_values(value):
                add(token)
    return found


def _snapshot_table_stamp() -> Tuple[int, str]:
    try:
        with connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_product_snapshots_v14'"
            ).fetchone()
            if not exists:
                return (0, "none")
            row = conn.execute(
                "SELECT COUNT(*) AS row_count, MAX(COALESCE(updated_at,created_at,'')) AS latest_stamp "
                "FROM system_product_snapshots_v14"
            ).fetchone()
            return (int(row["row_count"] or 0), str(row["latest_stamp"] or "none"))
    except Exception:
        return (-1, "unavailable")


def cached_task_snapshot_rows(limit: int = 120) -> List[Dict[str, Any]]:
    stamp = _snapshot_table_stamp()
    with _SNAPSHOT_CACHE_LOCK:
        if (
            _SNAPSHOT_CACHE.get("rows") is not None
            and _SNAPSHOT_CACHE.get("stamp") == stamp
            and int(_SNAPSHOT_CACHE.get("limit") or 0) >= int(limit)
        ):
            return _SNAPSHOT_CACHE["rows"][: int(limit)]

        rows = _ORIGINAL_SNAPSHOT_ROWS(limit=max(int(limit), 120))
        _SNAPSHOT_CACHE.update(stamp=stamp, limit=max(int(limit), 120), rows=rows)
        return rows[: int(limit)]


def attach_task_metric_evidence(snapshot: Dict[str, Any], source_task: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(_dict(snapshot))
    projection = build_task_metric_evidence_projection(source_task)

    report = dict(_dict(result.get("taskDetailReport")))
    related = dict(_dict(result.get("relatedTask")))
    plan = dict(_dict(report.get("taskPlan") or related.get("taskPlan")))

    plan["taskMetricEvidenceProjection"] = projection
    plan["taskMetricEvidenceProjectionVersion"] = TASK_METRIC_EVIDENCE_PROJECTION_VERSION
    report["taskPlan"] = plan
    report["taskMetricEvidenceProjection"] = projection
    report["taskMetricEvidenceProjectionVersion"] = TASK_METRIC_EVIDENCE_PROJECTION_VERSION
    related["taskPlan"] = plan
    related["taskMetricEvidenceProjection"] = projection
    related["taskMetricEvidenceProjectionVersion"] = TASK_METRIC_EVIDENCE_PROJECTION_VERSION

    display_contract = dict(_dict(result.get("detailDisplayContract")))
    display_contract.update(
        {
            "taskMetricEvidenceProjectionVersion": TASK_METRIC_EVIDENCE_PROJECTION_VERSION,
            "taskEvidenceFrozen": bool(projection.get("frozenAtTaskCreation")),
            "taskEvidenceStatus": projection.get("evidenceStatus"),
            "taskEvidenceRequiredForExecution": True,
            "emptyDynamicMetricChangesMeansBaseline": False,
            "taskEvidenceRule": "正式任务只展示任务创建时冻结且被判断实际引用的指标；证据缺失时任务不可执行。",
        }
    )

    result.update(
        {
            "taskDetailReport": report,
            "relatedTask": related,
            "taskMetricEvidenceProjection": projection,
            "taskMetricEvidenceProjectionVersion": TASK_METRIC_EVIDENCE_PROJECTION_VERSION,
            "taskEvidenceStatus": projection.get("evidenceStatus"),
            "taskEvidenceExecutable": bool(projection.get("taskExecutableFromEvidence")),
            "evidenceExecutionBlocked": not bool(projection.get("taskExecutableFromEvidence")),
            "detailDisplayContract": display_contract,
        }
    )
    return result


def install_v2178_task_metric_evidence_projection() -> None:
    from src.services import task_detail_snapshot_v2024_service as task_detail

    if getattr(task_detail, "_V2178_TASK_METRIC_EVIDENCE_INSTALLED", False):
        return

    evidence._referenced_codes = strict_referenced_metric_codes
    evidence._snapshot_rows = cached_task_snapshot_rows
    original = task_detail.build_task_detail_snapshot

    def build_task_detail_snapshot(task: Dict[str, Any]) -> Dict[str, Any]:
        return attach_task_metric_evidence(original(task), task)

    task_detail.build_task_detail_snapshot = build_task_detail_snapshot
    task_detail.TASK_METRIC_EVIDENCE_PROJECTION_VERSION = TASK_METRIC_EVIDENCE_PROJECTION_VERSION
    task_detail._V2178_TASK_METRIC_EVIDENCE_INSTALLED = True
