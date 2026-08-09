"""V22.5.10 active-dataVersion gate for the pipeline-live projection.

The underlying V22.5.9 model can read historical latest state when called without a
dataVersion. That behavior is useful for generic history/debug reads, but it is not
valid for the operator-center *current run* projection after Reset. This facade
binds the live view to the active imported-report runtime:

- no active imported dataVersion => current product/Signal/Agent counts are zero;
- active imported dataVersion => delegate to V22.5.9 with that exact dataVersion;
- historical canonical snapshots remain preserved for audit/history and cannot leak
  into the empty current-run UI.
"""
from __future__ import annotations

from typing import Any, Dict

from src.repositories.sqlite_repository import connect
from src.services import pipeline_live_read_model_v2258_service as base

PIPELINE_LIVE_READ_MODEL_VERSION = "22.5.10"
THREE_AGENT_PIPELINE_VERSION = base.THREE_AGENT_PIPELINE_VERSION


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
    )


def _active_report_data_version() -> str | None:
    """Return the newest dataVersion from the active imported-report runtime only."""
    try:
        with connect() as conn:
            if not _table_exists(conn, "imported_report_rows"):
                return None
            row = conn.execute(
                """
                SELECT data_version, MAX(rowid) AS last_rowid
                FROM imported_report_rows
                WHERE data_version IS NOT NULL AND TRIM(data_version) != ''
                GROUP BY data_version
                ORDER BY last_rowid DESC
                LIMIT 1
                """
            ).fetchone()
    except Exception:
        return None
    return str(row["data_version"]) if row and row["data_version"] else None


def _zero_current_projection(result: Dict[str, Any]) -> Dict[str, Any]:
    """Close the current-run projection without deleting historical snapshots."""
    output = dict(result or {})
    summary = dict(output.get("summary") or {})
    for key in [
        "totalItems",
        "productCount",
        "productTotal",
        "canonicalProductCount",
        "observed",
        "actionCandidates",
        "productFailed",
        "batchFailed",
        "failed",
        "baselineEstablished",
        "agent1Failed",
        "agent1OutputInvalid",
        "agent1DecisionUnresolved",
        "agent1Observed",
        "agent1Current",
    ]:
        summary[key] = 0

    stages = []
    for raw in output.get("stages") or []:
        item = dict(raw) if isinstance(raw, dict) else {}
        for key in ["queued", "running", "completed", "failed", "observed", "admitted", "currentCount"]:
            if key in item:
                item[key] = 0
        item["current"] = {
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "observed": 0,
            "admitted": 0,
        }
        stages.append(item)

    for key in ["totalItems", "productCount", "productTotal", "canonicalProductCount"]:
        if key in output:
            output[key] = 0

    output.update(
        version=PIPELINE_LIVE_READ_MODEL_VERSION,
        dataVersion=None,
        summary=summary,
        stages=stages,
        items=[],
        activeDataVersion=None,
        activeDataVersionGate="closed_no_active_import_runtime",
        productTruthSource="none_current_runtime",
        countBasis="active imported dataVersion required for current-run projection",
        rule=(
            "Historical canonical snapshots are preserved, but current operator-center "
            "counts are zero until imported_report_rows establishes an active dataVersion."
        ),
    )
    return output


def read_pipeline_live_model(
    data_version: str | None = None,
    *,
    limit: int = 80,
) -> Dict[str, Any]:
    active = _active_report_data_version()
    if not active:
        return _zero_current_projection(
            base.read_pipeline_live_model(data_version=None, limit=limit)
        )

    # The live operator-center follows the active import runtime, never a stale
    # caller/history dataVersion. Historical reads belong to dedicated history APIs.
    result = base.read_pipeline_live_model(data_version=active, limit=limit)
    result["version"] = PIPELINE_LIVE_READ_MODEL_VERSION
    result["activeDataVersion"] = active
    result["activeDataVersionGate"] = "open_active_import_runtime"
    result["requestedDataVersion"] = data_version
    result["countBasis"] = "canonical inventory scoped to active imported dataVersion"
    return result


__all__ = [
    "PIPELINE_LIVE_READ_MODEL_VERSION",
    "THREE_AGENT_PIPELINE_VERSION",
    "read_pipeline_live_model",
]
