"""V22 task-pool admission.

One valid SOP decision, item-level Agent2 proof and operation authority produces
one idempotent task admission. Step counts are not quality proxies; at least one
meaningful executable step is required and no fallback task is fabricated.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.runtime_version import VERSION
from src.services.competition_operator_context_service import default_operator
from src.services.action_authority_v214_service import (
    AUTHORIZATION_DATA_MISSING,
    AUTO_EXECUTE,
    apply_authorization_to_decision,
    record_authorization_decision,
    record_authorized_usage,
)
from src.services.action_plan_ir_v214_service import missing_action_plan_ir
from src.services.agent2_action_plan_core_v20_service import (
    provider_has_real_agent2_call,
    real_agent2_provider_missing_reason,
)
from src.services.lifecycle_task_v183_service import create_lifecycle_task_from_snapshot
from src.services.task_snapshot_station_service import create_task_snapshot

TASK_POOL_ADMISSION_CORE_VERSION = VERSION
ACTION_AUTHORITY_VERSION = VERSION
AGENT_RUNTIME_CONTRACT_VERSION = VERSION


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _proof(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = _dict(decision.get("taskPlan"))
    agent2 = _dict(decision.get("agent2ActionPlan"))
    provider = _dict(decision.get("agent2Provider"))
    return _dict(
        decision.get("agent2ExecutionProof")
        or plan.get("agent2ExecutionProof")
        or agent2.get("agent2ExecutionProof")
        or _dict(provider.get("itemProvenance")).get(
            str(decision.get("packageId") or agent2.get("packageId") or "")
        )
    )


def _chain_integrity_passed(decision: Dict[str, Any]) -> bool:
    stamp = _dict(decision.get("chainIntegrity"))
    evidence = _dict(decision.get("taskMappingAgentEvidence"))
    if stamp.get("passed") is not True:
        return False
    return all(
        [
            evidence.get("pipelineItemId") is not None,
            evidence.get("noMappingLlm") is True,
            evidence.get("noAgent2Rerun") is True,
            evidence.get("noActionPackRerun") is True,
            evidence.get("itemized") is True,
            evidence.get("noLegacyRuntimeSource") is True,
            evidence.get("agent2ProviderTracePassed") is True,
            evidence.get("fallbackAllowed") is False,
            int(evidence.get("compilerAddedStepCount") or 0) == 0,
        ]
    )


def _provider_passed(decision: Dict[str, Any]) -> tuple[bool, str | None]:
    provider = _dict(decision.get("agent2Provider"))
    proof = _proof(decision)
    agent2 = _dict(decision.get("agent2ActionPlan"))
    package_id = str(decision.get("packageId") or agent2.get("packageId") or "")
    if provider_has_real_agent2_call(provider, package_id, proof):
        return True, None
    return False, real_agent2_provider_missing_reason(provider, package_id, proof) or "agent2_item_provenance_missing"


def _validate_decision(decision: Dict[str, Any]) -> list[str]:
    failures: list[str] = []
    plan = _dict(decision.get("taskPlan"))
    agent2 = _dict(decision.get("agent2ActionPlan"))
    evidence = _dict(decision.get("taskMappingAgentEvidence"))
    chain = _dict(decision.get("chainIntegrity"))
    family = str(plan.get("selectedActionFamily") or agent2.get("actionFamily") or "")
    if decision.get("decision") not in {"create_task_snapshot", "manager_review_required"}:
        failures.append("decision_not_formal")
    if not decision.get("decisionId"):
        failures.append("decisionId")
    if not (decision.get("productId") or plan.get("productId")):
        failures.append("productId")
    if not (decision.get("storeId") or plan.get("storeId")):
        failures.append("storeId")
    if not str(decision.get("taskTitle") or plan.get("taskTitle") or "").strip():
        failures.append("taskTitle")
    if not family:
        failures.append("selectedActionFamily")
    if str(agent2.get("actionPlanStatus") or "") != "ready":
        failures.append("agent2ActionPlan.actionPlanStatus_ready")
    if agent2.get("semanticContractMissing"):
        failures.append("agent2ActionPlan.semanticContractMissing_empty")
    sop = [item for item in _arr(plan.get("operatorExecutionSop") or plan.get("sopSteps")) if str(item).strip()]
    if not sop:
        failures.append("operatorExecutionSop_required")
    if int(plan.get("compilerAddedStepCount") or decision.get("compilerAddedStepCount") or 0) != 0:
        failures.append("compilerAddedStepCount_zero")
    if not str(plan.get("sopSource") or "").startswith("v22_"):
        failures.append("sopSource_v22")
    for field in missing_action_plan_ir({**agent2, **plan}, family):
        failures.append(f"operationPlan.{field}")
    if evidence.get("noLegacyRuntimeSource") is not True:
        failures.append("noLegacyRuntimeSource")
    if evidence.get("agent2ProviderTracePassed") is not True:
        failures.append("agent2ProviderTracePassed")
    if evidence.get("fallbackAllowed") is not False:
        failures.append("fallbackAllowed_false")
    for key in (
        "taskDifferentiationPassed",
        "inventoryResponsibilityPassed",
        "ragTracePassed",
        "operationPlanPassed",
    ):
        if chain.get(key) is False:
            failures.append(f"chainIntegrity.{key}")
    return list(dict.fromkeys(failures))


def _ensure_task_pool_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_pool_entries (
                pool_entry_id TEXT PRIMARY KEY,task_snapshot_id TEXT NOT NULL,task_id TEXT,
                data_version TEXT,status TEXT NOT NULL,decision TEXT,task_layer TEXT,
                assignee_id TEXT,reviewer_id TEXT,dedupe_key TEXT,reason TEXT,payload TEXT,
                created_by TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "task_pool_entries",
            {
                "task_id": "TEXT",
                "data_version": "TEXT",
                "decision": "TEXT",
                "task_layer": "TEXT",
                "assignee_id": "TEXT",
                "reviewer_id": "TEXT",
                "dedupe_key": "TEXT",
                "reason": "TEXT",
                "payload": "TEXT",
                "created_by": "TEXT",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_pool_dedupe_v22 ON task_pool_entries(dedupe_key,status)"
        )
        conn.commit()


def _bind_operator(decision: Dict[str, Any]) -> Dict[str, Any]:
    next_decision = deepcopy(decision)
    plan = dict(_dict(next_decision.get("taskPlan")))
    if not plan.get("assignedOperatorId"):
        assigned = default_operator(plan.get("riskDomain") or plan.get("taskType")) or {}
        if assigned.get("id"):
            plan["assignedOperatorId"] = assigned.get("id")
            plan["operatorBindingSource"] = "competition_fixed_operator_context"
    next_decision["taskPlan"] = plan
    return next_decision


def _snapshot_body(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(_dict(decision.get("taskPlan")))
    product = _dict(plan.get("productIdentity"))
    package = _dict(decision.get("productJudgmentPackage"))
    authorization = _dict(decision.get("authorizationDecision"))
    proof = _proof(decision)
    operation_plan = _dict(
        plan.get("operationPlan")
        or decision.get("operationPlan")
        or _dict(decision.get("agent2ActionPlan")).get("operationPlan")
    )
    family = str(plan.get("selectedActionFamily") or "").strip()
    sop = [str(item).strip() for item in _arr(plan.get("operatorExecutionSop") or plan.get("sopSteps")) if str(item).strip()]
    title = str(plan.get("taskTitle") or plan.get("title") or decision.get("taskTitle") or "").strip()
    plan.update(
        {
            "title": title,
            "taskTitle": title,
            "productId": decision.get("productId") or plan.get("productId") or product.get("productId"),
            "storeId": decision.get("storeId") or plan.get("storeId") or product.get("storeId"),
            "productIdentity": product,
            "selectedActionFamily": family,
            "taskResponsibility": "operator_growth",
            "departmentTaskType": "operator_growth",
            "taskType": plan.get("taskType") or f"{family}_execution_task",
            "actionType": plan.get("actionType") or family,
            "operatorExecutionSop": sop,
            "sopSteps": sop,
            "steps": sop,
            "authorizationDecision": authorization,
            "actionAuthorization": authorization,
            "authorizationVersion": VERSION,
            "operationPlan": operation_plan,
            "agent2ExecutionProof": proof,
            "activeActionContract": plan.get("activeActionContract") or decision.get("activeActionContract"),
            "metricDigest": plan.get("metricDigest") or decision.get("metricDigest"),
            "compilerAddedStepCount": 0,
        }
    )
    return {
        "version": VERSION,
        "dataVersion": decision.get("dataVersion"),
        "decision": decision.get("decision"),
        "confidence": 0.9,
        "entityType": "product",
        "entityId": decision.get("productId") or plan.get("productId"),
        "productId": decision.get("productId") or plan.get("productId"),
        "storeId": decision.get("storeId") or plan.get("storeId"),
        "signalRef": decision.get("packageId") or decision.get("decisionId"),
        "bundleRef": decision.get("packageId"),
        "needManagerReview": bool(authorization.get("approvalRequired")),
        "authorizationDecision": authorization,
        "actionAuthorization": authorization,
        "authorizationVersion": VERSION,
        "operationPlan": operation_plan,
        "agent2ExecutionProof": proof,
        "taskPlan": plan,
        "operatorExecutionSop": sop,
        "sopSteps": sop,
        "reviewMetrics": plan.get("reviewMetrics") or [],
        "taskResponsibility": "operator_growth",
        "departmentTaskType": "operator_growth",
        "selectedActionFamily": family,
        "systemFacts": {
            "sceneDataJudgmentPackage": package,
            "taskGenerationDecision": decision,
            "actionParameterPack": plan.get("actionParameterPack"),
            "operationPlan": operation_plan,
            "agent2ExecutionProof": proof,
            "authorizationDecision": authorization,
        },
        "taskMappingAgentEvidence": decision.get("taskMappingAgentEvidence"),
        "productIdentity": product,
        "productJudgmentPackage": package,
        "activeActionContract": plan.get("activeActionContract"),
        "metricDigest": plan.get("metricDigest"),
        "source": "v22_authorized_task_pool_admission",
        "detailDisplayContract": "v22_single_action_contract",
        "lifecycleReady": True,
    }


def _existing_entry(dedupe_key: str) -> Dict[str, Any] | None:
    _ensure_task_pool_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM task_pool_entries WHERE dedupe_key=? AND status='entered_task_pool' ORDER BY created_at DESC LIMIT 1",
            (dedupe_key,),
        ).fetchone()
    return (
        {
            "poolEntryId": row["pool_entry_id"],
            "taskSnapshotId": row["task_snapshot_id"],
            "taskId": row["task_id"],
            "payload": loads(row["payload"]),
        }
        if row
        else None
    )


def _apply_runtime_authorization(task: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    authorization = _dict(decision.get("authorizationDecision"))
    task.update(
        {
            "authorizationDecision": authorization,
            "actionAuthorization": authorization,
            "authorizationVersion": VERSION,
            "taskPlan": decision.get("taskPlan") or task.get("taskPlan"),
            "operationPlan": decision.get("operationPlan") or _dict(decision.get("taskPlan")).get("operationPlan"),
            "agent2ExecutionProof": _proof(decision),
            "activeActionContract": decision.get("activeActionContract") or _dict(decision.get("taskPlan")).get("activeActionContract"),
        }
    )
    if authorization.get("decision") == AUTO_EXECUTE:
        operator_id = authorization.get("operatorId")
        task.update(
            {
                "decision": "create_task_snapshot",
                "taskLayer": "operator_execution",
                "assigneeId": operator_id,
                "assignee_id": operator_id,
                "status": "处理中",
                "workflowStatus": "处理中",
                "displayStatus": "处理中",
                "lifecycleStage": "accepted",
                "autoAcceptedBy": "system",
                "autoAcceptedAt": datetime.now().isoformat(),
                "autoAcceptReason": authorization.get("reason"),
                "visibleTaskActions": [
                    {"action": "submit", "label": "提交", "primary": True},
                    {"action": "detail", "label": "详情"},
                ],
            }
        )
    else:
        task.update(
            {
                "decision": "manager_review_required",
                "taskLayer": "manager_dispatch",
                "assigneeId": None,
                "assignee_id": None,
            }
        )
        task.setdefault("status", "待拆分")
        task.setdefault("workflowStatus", "待审批")
        task.setdefault("displayStatus", "待审批")
    return task


def admit_decision_to_task_pool(
    decision: Dict[str, Any],
    *,
    created_by: str | None = None,
    force_new_snapshot: bool = False,
) -> Dict[str, Any]:
    if not _chain_integrity_passed(decision):
        return {"ok": False, "status": "rejected_by_chain_integrity_gate", "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "reason": "semantic_chain_integrity_missing", "taskPoolAdmissionCoreVersion": VERSION}
    provider_ok, provider_reason = _provider_passed(decision)
    if not provider_ok:
        return {"ok": False, "status": "rejected_by_agent2_item_proof_gate", "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "reason": provider_reason, "taskPoolAdmissionCoreVersion": VERSION}
    failures = _validate_decision(decision)
    if failures:
        return {"ok": False, "status": "rejected_by_semantic_decision_contract", "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "reason": ",".join(failures), "failures": failures, "taskPoolAdmissionCoreVersion": VERSION}
    decision = apply_authorization_to_decision(_bind_operator(decision))
    authorization = _dict(decision.get("authorizationDecision"))
    if authorization.get("decision") == AUTHORIZATION_DATA_MISSING:
        return {"ok": False, "status": "rejected_by_v22_authorization_contract", "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "reason": authorization.get("reason"), "missing": authorization.get("missing") or [], "authorizationDecision": authorization, "taskPoolAdmissionCoreVersion": VERSION}
    dedupe_key = f"{decision.get('dataVersion')}:{decision.get('decisionId') or decision.get('packageId')}"
    if not force_new_snapshot:
        existing = _existing_entry(dedupe_key)
        if existing:
            return {"ok": True, "status": "entered_task_pool", "idempotentHit": True, "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "taskSnapshotId": existing.get("taskSnapshotId"), "taskId": existing.get("taskId"), "authorizationDecision": authorization, "taskPoolAdmissionCoreVersion": VERSION}
    try:
        snapshot = create_task_snapshot(_snapshot_body(decision), created_by=created_by, force=True)
        task = _apply_runtime_authorization(create_lifecycle_task_from_snapshot(snapshot, created_by=created_by), decision)
    except Exception as exc:
        return {"ok": False, "status": "rejected_by_task_materialization", "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "reason": str(exc), "taskPoolAdmissionCoreVersion": VERSION}
    _ensure_task_pool_tables()
    now = datetime.now().isoformat()
    entry_id = f"TPE-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"
    payload = {
        "snapshot": snapshot,
        "task": task,
        "source": "v22_authorized_direct_task_pool_admission",
        "contractVersion": VERSION,
        "decisionId": decision.get("decisionId"),
        "authorizationDecision": authorization,
        "operationPlan": decision.get("operationPlan") or _dict(decision.get("taskPlan")).get("operationPlan"),
        "agent2ExecutionProof": _proof(decision),
        "activeActionContract": decision.get("activeActionContract") or _dict(decision.get("taskPlan")).get("activeActionContract"),
    }
    with connect() as conn:
        conn.execute(
            """INSERT INTO task_pool_entries(pool_entry_id,task_snapshot_id,task_id,data_version,status,decision,task_layer,assignee_id,reviewer_id,dedupe_key,reason,payload,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                entry_id,
                snapshot.get("taskSnapshotId"),
                task.get("id"),
                decision.get("dataVersion"),
                "entered_task_pool",
                decision.get("decision"),
                task.get("taskLayer"),
                task.get("assigneeId"),
                task.get("reviewerId"),
                dedupe_key,
                authorization.get("reason") or "V22 authority completed.",
                dumps(payload),
                created_by,
                now,
                now,
            ),
        )
        conn.commit()
    record_authorization_decision(decision, task_id=task.get("id"))
    record_authorized_usage(task)
    return {
        "ok": True,
        "status": "entered_task_pool",
        "decisionId": decision.get("decisionId"),
        "packageId": decision.get("packageId"),
        "taskSnapshotId": snapshot.get("taskSnapshotId"),
        "taskId": task.get("id"),
        "createdSnapshotCount": 1,
        "createdTaskCount": 1,
        "authorizationDecision": authorization,
        "operationPlan": decision.get("operationPlan") or _dict(decision.get("taskPlan")).get("operationPlan"),
        "agent2ExecutionProof": _proof(decision),
        "taskPoolAdmissionCoreVersion": VERSION,
        "contractVersion": VERSION,
        "legacyBridgeUsed": False,
        "rule": "V22 validates one item proof, one action contract and one authority decision before admission.",
    }


def refresh_task_pool_views(data_version: str | None) -> Dict[str, Any]:
    try:
        from src.services.frontend_read_model_service import refresh_dashboard_view, refresh_task_views

        return {
            "status": "refreshed",
            "taskViews": refresh_task_views(data_version=data_version),
            "dashboard": refresh_dashboard_view(),
            "taskPoolAdmissionCoreVersion": VERSION,
            "contractVersion": VERSION,
        }
    except Exception as exc:
        return {"status": "refresh_failed", "error": str(exc), "taskPoolAdmissionCoreVersion": VERSION}


__all__ = [
    "TASK_POOL_ADMISSION_CORE_VERSION",
    "admit_decision_to_task_pool",
    "refresh_task_pool_views",
]
