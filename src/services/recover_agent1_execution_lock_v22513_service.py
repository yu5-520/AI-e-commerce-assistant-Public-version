"""V22.5.13 zero-Provider recovery for Agent1 execution-lock fail-close.

The command reads immutable V22.5.9 Agent1 execution lineage, selects only raw ``act``
outputs whose current item is ``observed_soft_gate``, re-runs deterministic
normalization under the repaired lock, and advances only complete locks. Dry-run is
the default. Accepted model-output Artifacts and the execution index are never changed.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from src.repositories.sqlite_repository import DB_PATH, connect, loads
from src.services.agent_execution_lock_v2255_service import (
    EXECUTION_LOCK_HOTFIX_VERSION,
    execution_lock_from,
    missing_execution_lock,
)
from src.services.artifact_transport_service import resolve_artifact
from src.services.frontend_view_artifact_v2259_service import (
    materialize_frontend_views_v2259,
)

AGENT1_EXECUTION_LOCK_RECOVERY_VERSION = "22.5.13"
ACT_ALIASES = {
    "act",
    "action",
    "execute",
    "execution",
    "intervention",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 500) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return " ".join(str(value or "").split())[:limit]


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        parsed = loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        try:
            parsed = json.loads(str(value))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


def _decision_type(value: Dict[str, Any]) -> str:
    return str(
        value.get("decisionType")
        or value.get("decisionHint")
        or ""
    ).strip().lower()


def _raw_for_execution(
    raw_batch: Dict[str, Any],
    *,
    item_execution_id: str,
    input_content_hash: str,
    store_id: str,
    product_id: str,
) -> Tuple[Dict[str, Any], int]:
    payload = _dict(raw_batch.get("providerPayload"))
    matches: List[Dict[str, Any]] = []
    for current in _arr(payload.get("judgments")):
        if not isinstance(current, dict):
            continue
        execution_match = (
            item_execution_id
            and str(current.get("itemExecutionId") or "")
            == item_execution_id
        )
        hash_match = (
            input_content_hash
            and str(current.get("inputContentHash") or "")
            == input_content_hash
        )
        identity_match = (
            str(current.get("storeId") or "") == store_id
            and str(current.get("productId") or "") == product_id
        )
        if execution_match or hash_match or identity_match:
            matches.append(dict(current))
    unique: Dict[str, Dict[str, Any]] = {}
    for current in matches:
        key = "|".join(
            [
                str(current.get("itemExecutionId") or ""),
                str(current.get("inputContentHash") or ""),
                str(current.get("storeId") or ""),
                str(current.get("productId") or ""),
            ]
        )
        unique.setdefault(key, current)
    values = list(unique.values())
    return (values[0] if len(values) == 1 else {}), len(values)


def _pipeline_rows(data_version: str, limit: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM pipeline_items
            WHERE data_version=?
              AND current_stage='observed_soft_gate'
              AND status='observed'
              AND product_id IS NOT NULL
              AND TRIM(product_id)!=''
            ORDER BY updated_at,item_id
            LIMIT ?
            """,
            (data_version, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def _execution_rows(data_version: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                e.*,
                a.tenant_id,
                a.store_id,
                a.product_id,
                a.data_version
            FROM artifact_execution_index_v2259 e
            JOIN artifact_registry a
              ON a.artifact_id=e.input_artifact_ref
            WHERE e.stage='product_judgment_agent'
              AND e.status='accepted'
              AND a.data_version=?
            ORDER BY e.updated_at DESC
            """,
            (data_version,),
        ).fetchall()
    result: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        current = dict(row)
        key = (
            str(current.get("store_id") or ""),
            str(current.get("product_id") or ""),
        )
        result.setdefault(key, current)
    return result


def _product_for_execution(
    execution: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    input_ref = str(execution.get("input_artifact_ref") or "")
    envelope = _dict(resolve_artifact(input_ref))
    product = dict(_dict(envelope.get("payload")))
    product["_hashExecution"] = {
        "stage": execution.get("stage"),
        "itemExecutionId": execution.get("item_execution_id"),
        "executionHash": execution.get("execution_hash"),
        "inputArtifactRef": input_ref,
        "inputContentHash": execution.get("input_content_hash"),
        "inputSchema": execution.get("input_schema"),
        "projectionVersion": execution.get("projection_version"),
        "promptVersion": execution.get("prompt_version"),
        "policyHash": execution.get("policy_hash"),
        "provider": execution.get("provider"),
        "model": execution.get("model"),
        "tenantId": execution.get("tenant_id"),
        "storeId": product.get("storeId") or execution.get("store_id"),
        "productId": product.get("productId") or execution.get("product_id"),
        "dataVersion": product.get("dataVersion") or execution.get("data_version"),
    }
    return product, envelope


def _renormalize(
    *,
    data_version: str,
    execution: Dict[str, Any],
    raw: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    from src.services import real_product_judgment_agent_v2259_service as core

    product, envelope = _product_for_execution(execution)
    normalized, diagnostics = core._normalize_judgments(
        {"judgments": [raw]},
        [product],
        data_version,
    )
    judgment = dict(normalized[0]) if len(normalized) == 1 else {}
    return judgment, diagnostics, envelope, product


def _backup_database(data_version: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", data_version)
    target_dir = Path(DB_PATH).resolve().parent / "recovery_backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (
        "product_workbench-pre-v22513-"
        f"{safe}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
    )
    with connect() as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    return str(target)


def _completed_payload(
    *,
    item: Dict[str, Any],
    product: Dict[str, Any],
    raw: Dict[str, Any],
    judgment: Dict[str, Any],
    execution: Dict[str, Any],
    envelope: Dict[str, Any],
) -> Dict[str, Any]:
    from src.services.agent_runtime_contract_v2010_service import (
        AGENT_RUNTIME_CONTRACT_VERSION,
        normalize_agent1_completed_contract,
    )

    refs = _load(item.get("artifact_refs_json"))
    refs.update(
        {
            "agentExecutionInputRef": execution.get("input_artifact_ref"),
            "agentExecutionOutputRef": execution.get("accepted_output_ref"),
            "agentRawBatchOutputRef": execution.get("raw_batch_output_ref"),
            "agent1ExecutionLockRecoverySourceRef": execution.get(
                "accepted_output_ref"
            ),
        }
    )
    provider = {
        "providerStatus": "accepted_raw_output_replayed",
        "actualCalls": 0,
        "provider": execution.get("provider"),
        "model": execution.get("model"),
        "itemExecutionId": execution.get("item_execution_id"),
        "executionHash": execution.get("execution_hash"),
        "acceptedOutputRef": execution.get("accepted_output_ref"),
        "rawBatchOutputRef": execution.get("raw_batch_output_ref"),
        "recoveryVersion": AGENT1_EXECUTION_LOCK_RECOVERY_VERSION,
    }
    payload = normalize_agent1_completed_contract(
        item=item,
        signal=product,
        judgment=judgment,
        provider=provider,
        data_version=str(item.get("data_version") or ""),
    )
    lock = execution_lock_from(judgment)
    payload.update(
        version=AGENT1_EXECUTION_LOCK_RECOVERY_VERSION,
        contractVersion=AGENT_RUNTIME_CONTRACT_VERSION,
        rawAgent1Judgment=raw,
        recoveredAgent1Judgment=judgment,
        executionLock=lock,
        evidenceStatus=lock.get("evidenceStatus"),
        primaryProblemNode=lock.get("primaryProblemNode"),
        primaryAction=lock.get("primaryAction"),
        primaryExecutionTarget=lock.get("primaryExecutionTarget"),
        primaryOwner=lock.get("primaryOwner"),
        decisiveFacts=lock.get("decisiveFacts") or [],
        supportingCoordination=lock.get("supportingCoordination") or [],
        forbiddenActionDomains=lock.get("forbiddenActionDomains") or [],
        runtimeSource="accepted_agent1_raw_output_re_normalized",
        agent1InputRef=execution.get("input_artifact_ref"),
        sourceArtifactRefs=envelope.get("sourceArtifactRefs"),
        inputProjectionAudit=envelope.get("projectionAudit"),
        outputContract="V22.5.13.agent1_execution_lock_recovery",
        executionLockContract="one_problem_one_action_one_owner_one_target",
        executionLockHotfixVersion=EXECUTION_LOCK_HOTFIX_VERSION,
        recoveryProviderCalls=0,
        recoveredFromObservedSoftGate=True,
        originalAcceptedOutputRef=execution.get("accepted_output_ref"),
        originalRawBatchOutputRef=execution.get("raw_batch_output_ref"),
        artifactRefs=refs,
        taskAdmissionAllowed=True,
        observationOnly=False,
        diagnosticHold=False,
        diagnosticHoldReason=None,
    )
    return payload


def recover_agent1_execution_locks_v22513(
    *,
    data_version: str,
    apply: bool = False,
    limit: int = 500,
    refresh_views: bool = True,
) -> Dict[str, Any]:
    if not str(data_version or "").strip():
        raise ValueError("data_version_required")
    data_version = str(data_version)
    items = _pipeline_rows(data_version, max(1, min(int(limit), 5000)))
    executions = _execution_rows(data_version)
    categories: Counter[str] = Counter()
    candidates: List[Dict[str, Any]] = []
    recovered: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    backup_path = _backup_database(data_version) if apply and items else None

    for item in items:
        key = (
            str(item.get("store_id") or ""),
            str(item.get("product_id") or ""),
        )
        execution = executions.get(key)
        if not execution:
            categories["execution_missing"] += 1
            continue
        raw_ref = str(execution.get("raw_batch_output_ref") or "")
        if not raw_ref.startswith("ART-"):
            categories["raw_batch_ref_missing"] += 1
            continue
        raw, match_count = _raw_for_execution(
            _dict(resolve_artifact(raw_ref)),
            item_execution_id=str(execution.get("item_execution_id") or ""),
            input_content_hash=str(execution.get("input_content_hash") or ""),
            store_id=key[0],
            product_id=key[1],
        )
        if match_count != 1:
            categories[
                "raw_match_ambiguous" if match_count > 1 else "raw_match_missing"
            ] += 1
            continue
        raw_type = _decision_type(raw)
        if raw_type not in ACT_ALIASES:
            categories["native_observation"] += 1
            continue

        try:
            judgment, diagnostics, envelope, product = _renormalize(
                data_version=data_version,
                execution=execution,
                raw=raw,
            )
            lock = execution_lock_from(judgment)
            lock_missing = missing_execution_lock(lock)
            eligible = (
                _decision_type(judgment) == "act"
                and lock.get("locked") is True
                and not lock_missing
            )
            candidate = {
                "itemId": item.get("item_id"),
                "storeId": key[0],
                "productId": key[1],
                "itemExecutionId": execution.get("item_execution_id"),
                "rawDecisionType": raw_type,
                "replayedDecisionType": _decision_type(judgment),
                "selectedOperatingRoute": lock.get("selectedOperatingRoute"),
                "selectedActionFamily": lock.get("selectedActionFamily"),
                "primaryExecutionTarget": lock.get("primaryExecutionTarget"),
                "evidenceStatus": lock.get("evidenceStatus"),
                "evidenceBasis": lock.get("evidenceBasis"),
                "advisoryMissingEvidence": lock.get(
                    "advisoryMissingEvidence"
                ),
                "hardEvidenceBlockers": lock.get("hardEvidenceBlockers"),
                "missingExecutionLock": lock_missing,
                "eligible": eligible,
                "diagnostics": diagnostics,
            }
            candidates.append(candidate)
            if not eligible:
                categories["still_blocked_after_repair"] += 1
                continue
            categories["eligible_raw_act"] += 1
            if not apply:
                continue

            from src.services import (
                pipeline_agent1_microbatch_v20101_service as pipeline_core,
            )
            from src.services.agent_runtime_hard_interface_v2255_service import (
                _finish_agent1,
            )

            payload = _completed_payload(
                item=item,
                product=product,
                raw=raw,
                judgment=judgment,
                execution=execution,
                envelope=envelope,
            )
            finish = _finish_agent1(
                pipeline_core,
                item,
                stage=pipeline_core.AGENT1_COMPLETED_STAGE,
                status="ready",
                output_ref=(
                    f"agent1_lock_recovery:{data_version}:{item.get('item_id')}"
                ),
                payload=payload,
            )
            recovered.append({**candidate, "pipelineFinish": finish})
            categories["recovered"] += 1
        except Exception as exc:
            categories["recovery_failure"] += 1
            failures.append(
                {
                    "itemId": item.get("item_id"),
                    "storeId": key[0],
                    "productId": key[1],
                    "error": f"{type(exc).__name__}:{exc}"[:1000],
                }
            )

    view_result: Dict[str, Any] | None = None
    if apply and recovered and refresh_views:
        try:
            view_result = materialize_frontend_views_v2259(
                data_version=data_version,
                view_key="operator-center",
                user_id="competition_operator",
            )
        except Exception as exc:
            view_result = {
                "status": "failed",
                "error": f"{type(exc).__name__}:{exc}"[:500],
            }

    return {
        "schema": "agent1.execution-lock-recovery.v22513",
        "version": AGENT1_EXECUTION_LOCK_RECOVERY_VERSION,
        "executionLockHotfixVersion": EXECUTION_LOCK_HOTFIX_VERSION,
        "dataVersion": data_version,
        "mode": "apply" if apply else "dry_run",
        "providerCallsExecuted": 0,
        "observedRowsScanned": len(items),
        "rawActCandidateCount": sum(
            1
            for value in candidates
            if value.get("rawDecisionType") in ACT_ALIASES
        ),
        "eligibleCount": sum(
            1 for value in candidates if value.get("eligible") is True
        ),
        "recoveredCount": len(recovered),
        "categories": dict(categories),
        "candidates": candidates,
        "recovered": recovered,
        "failures": failures,
        "databaseBackup": backup_path,
        "viewRefresh": view_result,
        "immutableAcceptedOutputsModified": False,
        "executionIndexModified": False,
        "nativeObservationsTouched": False,
        "fallbackAllowed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay accepted Agent1 raw acts through the V22.5.13 "
            "deterministic execution-lock repair."
        )
    )
    parser.add_argument("--data-version", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--no-refresh-views", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = recover_agent1_execution_locks_v22513(
        data_version=args.data_version,
        apply=bool(args.apply),
        limit=int(args.limit),
        refresh_views=not bool(args.no_refresh_views),
    )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=str,
        )
    )
    return 2 if result.get("failures") else 0


__all__ = [
    "AGENT1_EXECUTION_LOCK_RECOVERY_VERSION",
    "recover_agent1_execution_locks_v22513",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
