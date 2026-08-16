"""Generation-bound current-state projection for the competition pipeline.

Historical pipeline state is useful for diagnostics, but the operator-center current
view must never consult it after Reset. V22.5.16 adds the Runtime Generation Barrier:
no active imported dataVersion returns a synthetic empty projection without calling the
legacy/latest reader, and the legacy last-good memory cache is cleared whenever the
runtime generation changes.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services import pipeline_live_read_model_v2258_service as base
from src.services.runtime_generation_barrier_v1_service import (
    current_runtime_generation,
)

PIPELINE_LIVE_READ_MODEL_VERSION = "22.5.16"
THREE_AGENT_PIPELINE_VERSION = base.THREE_AGENT_PIPELINE_VERSION
ATTENTION_IDENTITY = "dataVersion+storeId+productId"
_JSON_PATH_RE = re.compile(r"\$\.[A-Za-z_][A-Za-z0-9_]*(?:\[[0-9]+\]|\.[A-Za-z_][A-Za-z0-9_]*)*")
_LAST_RUNTIME_GENERATION_HASH: str | None = None


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


def _runtime_generation() -> Dict[str, Any]:
    try:
        return current_runtime_generation()
    except Exception as exc:
        return {
            "generationSeq": None,
            "generationHash": None,
            "state": "unavailable",
            "error": str(exc)[:240],
        }


def _clear_cross_generation_memory(generation_hash: str | None) -> None:
    global _LAST_RUNTIME_GENERATION_HASH
    value = str(generation_hash or "")
    if _LAST_RUNTIME_GENERATION_HASH == value:
        return
    try:
        legacy = getattr(base, "legacy", None)
        cache = getattr(legacy, "_LAST_GOOD_SNAPSHOT", None)
        if isinstance(cache, dict):
            cache.clear()
    except Exception:
        pass
    _LAST_RUNTIME_GENERATION_HASH = value


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


def _empty_stages() -> List[Dict[str, Any]]:
    definitions = getattr(base, "_NODE_CONTRACT", []) or [
        ("agent1", "Agent1研判"),
        ("action_matrix", "动作矩阵"),
        ("agent2_draft", "Agent2草案"),
        ("agent3_sop", "Agent3 SOP"),
        ("task_mapping", "任务映射"),
        ("task_pool", "任务池"),
        ("task_loop", "任务闭环"),
    ]
    return [
        {
            "nodeCode": code,
            "node": label,
            "label": label,
            "status": "waiting",
            "total": 0,
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "observed": 0,
            "admitted": 0,
            "currentCount": 0,
            "historyCompleted": 0,
            "current": {
                "queued": 0,
                "running": 0,
                "completed": 0,
                "failed": 0,
                "observed": 0,
                "admitted": 0,
            },
            "history": {"completed": 0},
        }
        for code, label in definitions
    ]


def _empty_generation_projection(
    generation: Dict[str, Any],
    *,
    requested_data_version: str | None,
) -> Dict[str, Any]:
    """Return current empty state without touching latest/history readers."""
    return {
        "version": PIPELINE_LIVE_READ_MODEL_VERSION,
        "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
        "ready": False,
        "interfaceStatus": "ok",
        "dataVersion": None,
        "requestedDataVersion": requested_data_version,
        "activeDataVersion": None,
        "activeDataVersionGate": "closed_no_active_import_runtime",
        "displaySnapshotId": f"generation-empty:{generation.get('generationHash') or 'unknown'}",
        "headline": "等待数据接入",
        "flowStatus": "waiting",
        "snapshotStatus": "empty",
        "baselineOnly": False,
        "batchState": {},
        "summary": {
            "totalItems": 0,
            "productCount": 0,
            "productTotal": 0,
            "canonicalProductCount": 0,
            "observed": 0,
            "actionCandidates": 0,
            "productFailed": 0,
            "batchFailed": 0,
            "failed": 0,
            "baselineEstablished": 0,
            "agent1Failed": 0,
            "agent1OutputInvalid": 0,
            "agent1DecisionUnresolved": 0,
            "agent1Observed": 0,
            "agent1Current": 0,
            "taskAdmitted": 0,
        },
        "stages": _empty_stages(),
        "items": [],
        "runtimeGeneration": generation,
        "runtimeGenerationHash": generation.get("generationHash"),
        "attentionStateAuthority": "none_current_runtime",
        "attentionHistoryFallbackAllowed": False,
        "attentionIdentity": ATTENTION_IDENTITY,
        "productTruthSource": "none_current_runtime",
        "countBasis": "active imported dataVersion required for current-run projection",
        "failureProjectionContract": "structured_root_cause_first.v1",
        "historicalReaderInvoked": False,
        "crossGenerationLastGoodFallbackAllowed": False,
        "rule": (
            "Reset-empty current projection is synthesized from the active Runtime Generation "
            "and never calls latest/history pipeline readers."
        ),
    }


def read_pipeline_live_model(
    data_version: str | None = None,
    *,
    limit: int = 80,
) -> Dict[str, Any]:
    generation = _runtime_generation()
    _clear_cross_generation_memory(generation.get("generationHash"))
    active = _active_report_data_version()
    if not active:
        return _empty_generation_projection(
            generation,
            requested_data_version=data_version,
        )

    # The live operator-center follows the active import runtime, never a stale
    # caller/history dataVersion. Historical reads belong to dedicated history APIs.
    result = base.read_pipeline_live_model(data_version=active, limit=limit)
    result["items"] = _current_attention_items(active, limit)
    result["version"] = PIPELINE_LIVE_READ_MODEL_VERSION
    result["activeDataVersion"] = active
    result["activeDataVersionGate"] = "open_active_import_runtime"
    result["requestedDataVersion"] = data_version
    result["runtimeGeneration"] = generation
    result["runtimeGenerationHash"] = generation.get("generationHash")
    result["attentionStateAuthority"] = "latest_pipeline_item_row_per_current_product"
    result["attentionHistoryFallbackAllowed"] = False
    result["attentionIdentity"] = ATTENTION_IDENTITY
    result["attentionProjectionRule"] = (
        "Resolve newest row by dataVersion+storeId+productId before filtering; "
        "observed_soft_gate never falls back to older Agent1 history."
    )
    result["failureProjectionContract"] = "structured_root_cause_first.v1"
    result["crossGenerationLastGoodFallbackAllowed"] = False
    result["countBasis"] = "canonical inventory scoped to active imported dataVersion"
    return result


__all__ = [
    "PIPELINE_LIVE_READ_MODEL_VERSION",
    "THREE_AGENT_PIPELINE_VERSION",
    "ATTENTION_IDENTITY",
    "read_pipeline_live_model",
]
