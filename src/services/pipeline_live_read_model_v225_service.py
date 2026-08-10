"""V22.5.12 active-dataVersion/current-state gate for the pipeline-live projection.

The underlying V22.5.9 model can read historical latest state when called without a
dataVersion. That behavior is useful for generic history/debug reads, but it is not
valid for the operator-center *current run* projection after Reset. This facade
binds the live view to the active imported-report runtime and also closes a stale
attention fallback. V22.5.13 additionally projects structured Agent3 contract root
causes instead of collapsing every ``output_invalid`` state into a generic format
error label.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services import pipeline_live_read_model_v2258_service as base

PIPELINE_LIVE_READ_MODEL_VERSION = "22.5.13"
THREE_AGENT_PIPELINE_VERSION = base.THREE_AGENT_PIPELINE_VERSION
ATTENTION_IDENTITY = "dataVersion+storeId+productId"
_JSON_PATH_RE = re.compile(r"\$\.[A-Za-z_][A-Za-z0-9_]*(?:\[[0-9]+\]|\.[A-Za-z_][A-Za-z0-9_]*)*")


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
    """Resolve exactly one newest persisted row for each current product identity."""
    rows = base._current_rows(data_version)
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        latest.setdefault(base._identity(row), row)
    return list(latest.values())


def _row_business_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("payload")
    if isinstance(raw, dict):
        wrapper = raw
    else:
        try:
            wrapper = json.loads(str(raw or "{}"))
        except Exception:
            wrapper = {}
    if not isinstance(wrapper, dict):
        return {}
    payload = wrapper.get("payload")
    return payload if isinstance(payload, dict) else wrapper


def _contract_violations(row: Dict[str, Any]) -> List[str]:
    payload = _row_business_payload(row)
    sop = payload.get("agent3Sop") if isinstance(payload.get("agent3Sop"), dict) else {}
    validation = sop.get("contractValidation") if isinstance(sop.get("contractValidation"), dict) else {}
    values = (
        payload.get("systemContractViolations")
        or sop.get("systemContractViolations")
        or validation.get("missing")
        or sop.get("semanticContractMissing")
        or []
    )
    return [str(item) for item in values if str(item)] if isinstance(values, list) else []


def _failure_projection(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return one machine class + human label from the persisted root cause."""
    violations = _contract_violations(row)
    classes: List[str] = []
    paths: List[str] = []
    for value in violations:
        if value.startswith("agent3_sop_cross_family_contamination:"):
            classes.append("cross_family_contamination")
        if value.startswith("agent3_system_fact_converted_to_action:"):
            classes.append("system_fact_converted_to_action")
        for path in _JSON_PATH_RE.findall(value):
            if path not in paths:
                paths.append(path)

    explicit = str(row.get("failure_class") or "").strip()
    if explicit and explicit not in classes:
        classes.append(explicit)
    error_text = " ".join(
        str(value or "")
        for value in (
            row.get("last_error_code"),
            row.get("error_reason"),
        )
    ).lower()

    if "cross_family_contamination" in classes and "system_fact_converted_to_action" in classes:
        failure_class = "agent3_semantic_contract_violation"
        label = "SOP语义合同未通过：动作域越界且系统事实被重复转为人工动作"
    elif "cross_family_contamination" in classes:
        failure_class = "cross_family_contamination"
        label = "SOP动作域越界"
    elif "system_fact_converted_to_action" in classes:
        failure_class = "system_fact_converted_to_action"
        label = "系统事实被重复转为人工动作"
    elif explicit in {"transient_provider_or_protocol", "provider_failure"} or any(
        marker in error_text for marker in ("provider", "timeout", "http", "429", "500", "502", "503", "504")
    ):
        failure_class = explicit or "provider_failure"
        label = "模型接口失败"
    elif violations:
        failure_class = explicit or "agent3_semantic_contract_violation"
        label = "SOP语义合同未通过"
    else:
        return {}

    return {
        "failureClass": failure_class,
        "failureType": label,
        "failurePaths": paths,
        "systemContractViolations": violations,
        "failureOwner": "agent3_system_contract" if violations else None,
    }


def _current_attention_items(data_version: str, limit: int) -> List[Dict[str, Any]]:
    """Project attention from current identity rows and preserve structured failures."""
    current = _latest_current_rows(data_version)
    by_identity = {base._identity(row): row for row in current}
    items = base._deduplicated_attention(current, limit)
    result: List[Dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        row = by_identity.get(str(item.get("identityKey") or "")) or {}
        projection = _failure_projection(row)
        if projection:
            item.update({key: value for key, value in projection.items() if value is not None})
            item["stageLabel"] = projection["failureType"]
        result.append(item)
    return result


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
        failureProjectionContract="structured_root_cause_first.v1",
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
    result["failureProjectionContract"] = "structured_root_cause_first.v1"
    result["countBasis"] = "canonical inventory scoped to active imported dataVersion"
    return result


__all__ = [
    "PIPELINE_LIVE_READ_MODEL_VERSION",
    "THREE_AGENT_PIPELINE_VERSION",
    "ATTENTION_IDENTITY",
    "read_pipeline_live_model",
]
