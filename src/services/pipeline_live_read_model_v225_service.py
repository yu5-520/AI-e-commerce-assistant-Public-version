"""V22.5.12 active-dataVersion/current-state gate for the pipeline-live projection.

The underlying V22.5.9 model can read historical latest state when called without a
dataVersion. That behavior is useful for generic history/debug reads, but it is not
valid for the operator-center *current run* projection after Reset. This facade
binds the live view to the active imported-report runtime and also closes a stale
attention fallback:

- no active imported dataVersion => current product/Signal/Agent counts are zero;
- active imported dataVersion => delegate to V22.5.9 with that exact dataVersion;
- attention resolves one newest pipeline row per dataVersion+storeId+productId before
  deciding whether the row is actionable;
- an observed_soft_gate current row is observation sediment only and can never expose
  an older Agent1 queued/running row as the current attention state;
- historical canonical snapshots remain preserved for audit/history and cannot leak
  into the empty current-run UI.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services import pipeline_live_read_model_v2258_service as base

PIPELINE_LIVE_READ_MODEL_VERSION = "22.5.12"
THREE_AGENT_PIPELINE_VERSION = base.THREE_AGENT_PIPELINE_VERSION
ATTENTION_IDENTITY = "dataVersion+storeId+productId"


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


def _latest_current_rows(data_version: str) -> List[Dict[str, Any]]:
    """Resolve exactly one newest persisted row for each current product identity.

    ``base._current_rows`` is ordered newest-first. Identity is therefore claimed by
    the first row only. This must happen *before* attention filtering; otherwise an
    observed current row can be skipped and an older Agent1 row can reappear.
    """
    rows = base._current_rows(data_version)
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        latest.setdefault(base._identity(row), row)
    return list(latest.values())


def _current_attention_items(data_version: str, limit: int) -> List[Dict[str, Any]]:
    """Project attention from current identity rows only; history fallback is forbidden."""
    current = _latest_current_rows(data_version)
    return base._deduplicated_attention(current, limit)


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
        attentionStateAuthority="none_current_runtime",
        attentionHistoryFallbackAllowed=False,
        attentionIdentity=ATTENTION_IDENTITY,
        productTruthSource="none_current_runtime",
        countBasis="active imported dataVersion required for current-run projection",
        rule=(
            "Historical canonical snapshots are preserved, but current operator-center "
            "counts and attention are zero until imported_report_rows establishes an active dataVersion."
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

    # The base V22.5.9 summary already resolves latest rows correctly, but its
    # attention list used raw history rows. Re-project the list from the same newest
    # identity rows so observation cannot reveal an older Agent1 state.
    result["items"] = _current_attention_items(active, limit)
    result["version"] = PIPELINE_LIVE_READ_MODEL_VERSION
    result["activeDataVersion"] = active
    result["activeDataVersionGate"] = "open_active_import_runtime"
    result["requestedDataVersion"] = data_version
    result["attentionStateAuthority"] = "latest_pipeline_item_row_per_current_product"
    result["attentionHistoryFallbackAllowed"] = False
    result["attentionIdentity"] = ATTENTION_IDENTITY
    result["attentionProjectionRule"] = (
        "Resolve newest row by dataVersion+storeId+productId before filtering; "
        "observed_soft_gate never falls back to older Agent1 history."
    )
    result["countBasis"] = "canonical inventory scoped to active imported dataVersion"
    return result


__all__ = [
    "PIPELINE_LIVE_READ_MODEL_VERSION",
    "THREE_AGENT_PIPELINE_VERSION",
    "ATTENTION_IDENTITY",
    "read_pipeline_live_model",
]
