"""V19.3 Task Pool Admission Bridge.

Only operator_growth task decisions with real Agent multi-route judgment are
converted into TaskSnapshot. The backend trace is preserved for audit; the
frontend reads only operatorJudgmentView.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads
from src.services.task_generation_run_service import record_task_generation_run
from src.services.task_pool_station_service import enter_task_pool_from_snapshot
from src.services.task_snapshot_station_service import create_task_snapshot

TASK_POOL_ADMISSION_BRIDGE_VERSION = "19.3"
FORMAL_DECISIONS = {"create_task_snapshot", "manager_review_required"}


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _table(conn: Any, name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _creative_contract_ok(plan: Dict[str, Any]) -> bool:
    return bool(len(str(plan.get("businessHypothesis") or "")) >= 18 and plan.get("operatingScenario") and _list_len(plan.get("titleVariants")) >= 2 and _list_len(plan.get("mainImageStructures")) >= 2 and _list_len(plan.get("testVariables")) >= 1 and _list_len(plan.get("successCriteria")) >= 1 and _list_len(plan.get("failureCriteria")) >= 1 and _list_len(plan.get("submissionConclusionOptions")) >= 2 and plan.get("sopSource") == "llm_agent_multi_route_dynamic_creative")


def _trace_ok(plan: Dict[str, Any]) -> bool:
    trace = plan.get("agentJudgmentTrace") if isinstance(plan.get("agentJudgmentTrace"), dict) else {}
    selected = trace.get("selectedRoute") if isinstance(trace.get("selectedRoute"), dict) else {}
    return bool(_list_len(trace.get("metricRouteCandidates")) >= 3 and selected.get("routeId") and selected.get("routeName") and _list_len(trace.get("rejectedRoutes")) >= 2 and trace.get("platformRead") and trace.get("categoryRead") and trace.get("metricRead") and len(str(trace.get("businessHypothesis") or "")) >= 18)


def _view_ok(plan: Dict[str, Any]) -> bool:
    view = plan.get("operatorJudgmentView") if isinstance(plan.get("operatorJudgmentView"), dict) else {}
    return all(str(view.get(key) or "").strip() for key in ["selectedDirection", "displayReason", "testFocus", "recapBasis"])


def _load_decisions(data_version: str | None) -> List[Dict[str, Any]]:
    if not data_version:
        return []
    with connect() as conn:
        if not _table(conn, "task_generation_decisions_v15"):
            return []
        rows = conn.execute("SELECT payload FROM task_generation_decisions_v15 WHERE data_version = ? ORDER BY created_at ASC", (data_version,)).fetchall()
    decisions = [_load(row["payload"]) for row in rows]
    return [item for item in decisions if item.get("decision") in FORMAL_DECISIONS]


def _existing_snapshot(data_version: str | None, package_id: str | None, decision: str | None, responsibility: str | None) -> Dict[str, Any] | None:
    if not data_version or not package_id or not decision:
        return None
    with connect() as conn:
        if not _table(conn, "task_snapshots"):
            return None
        rows = conn.execute("""
            SELECT task_snapshot_id, task_pool_status, payload
            FROM task_snapshots
            WHERE data_version = ? AND signal_ref = ? AND decision = ?
            ORDER BY created_at DESC
            LIMIT 5
        """, (data_version, package_id, decision)).fetchall()
    for row in rows:
        payload = _load(row["payload"])
        plan = payload.get("taskPlan") if isinstance(payload.get("taskPlan"), dict) else {}
        if not responsibility or plan.get("taskResponsibility") == responsibility:
            return {"taskSnapshotId": row["task_snapshot_id"], "taskPoolStatus": row["task_pool_status"], "payload": payload}
    return None


def _decision_has_real_agent_contract(decision: Dict[str, Any]) -> bool:
    evidence = decision.get("taskMappingAgentEvidence") if isinstance(decision.get("taskMappingAgentEvidence"), dict) else {}
    plan = decision.get("taskPlan") if isinstance(decision.get("taskPlan"), dict) else {}
    return bool(
        evidence.get("source") == "real_task_mapping_agent"
        and evidence.get("businessEventRouter") == "v19.3_multi_route_judgment"
        and decision.get("decision") in FORMAL_DECISIONS
        and plan.get("productIdentity")
        and plan.get("taskType") != "observation_task"
        and plan.get("title")
        and plan.get("reason")
        and plan.get("businessEventId")
        and plan.get("taskResponsibility") == "operator_growth"
        and plan.get("departmentTaskType") == "operator_growth"
        and len(plan.get("sopSteps") or []) >= 4
        and len(plan.get("evidenceRequirements") or []) >= 2
        and _trace_ok(plan)
        and _view_ok(plan)
        and _creative_contract_ok(plan)
    )


def _snapshot_body_from_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = decision.get("taskPlan") if isinstance(decision.get("taskPlan"), dict) else {}
    package = decision.get("productJudgmentPackage") if isinstance(decision.get("productJudgmentPackage"), dict) else {}
    rag_context = package.get("ragPermissionContext") if isinstance(package.get("ragPermissionContext"), dict) else {}
    product = plan.get("productIdentity") if isinstance(plan.get("productIdentity"), dict) else {}
    try:
        confidence = float(package.get("relationConfidence") or package.get("packageConfidence") or package.get("confidence") or 0.72)
    except Exception:
        confidence = 0.72
    return {"dataVersion": decision.get("dataVersion"), "decision": decision.get("decision"), "confidence": confidence, "entityType": "product", "entityId": decision.get("productId") or plan.get("productId") or product.get("productId"), "productId": decision.get("productId") or plan.get("productId") or product.get("productId"), "storeId": decision.get("storeId") or plan.get("storeId") or product.get("storeId"), "signalRef": decision.get("packageId"), "bundleRef": decision.get("packageId"), "ragContext": rag_context, "agentJudgment": {"decision": decision.get("decision"), "confidence": confidence, "reason": decision.get("reason") or plan.get("reason"), "status": "v19_3_multi_route_judgment_task_decision", "permissionDecision": plan.get("permissionDecision"), "taskResponsibility": "operator_growth", "businessEventId": plan.get("businessEventId"), "operatorJudgmentView": plan.get("operatorJudgmentView")}, "taskPlan": plan, "businessEventId": plan.get("businessEventId"), "parentEventId": plan.get("parentEventId"), "taskResponsibility": "operator_growth", "departmentTaskType": "operator_growth", "businessEvent": plan.get("businessEvent") or {}, "agentJudgmentTrace": plan.get("agentJudgmentTrace") or {}, "operatorJudgmentView": plan.get("operatorJudgmentView") or {}, "businessHypothesis": plan.get("businessHypothesis"), "operatingScenario": plan.get("operatingScenario"), "trafficGapType": plan.get("trafficGapType"), "titleVariants": plan.get("titleVariants") or [], "mainImageStructures": plan.get("mainImageStructures") or [], "testVariables": plan.get("testVariables") or [], "successCriteria": plan.get("successCriteria") or [], "failureCriteria": plan.get("failureCriteria") or [], "submissionConclusionOptions": plan.get("submissionConclusionOptions") or [], "creativeContextPack": plan.get("creativeContextPack") or {}, "platformStyleProfile": plan.get("platformStyleProfile") or {}, "verticalCategoryProfile": plan.get("verticalCategoryProfile") or {}, "futureDepartmentHooks": plan.get("futureDepartmentHooks") or [], "companyCapacityReminder": plan.get("companyCapacityReminder"), "operationBudget": plan.get("operationBudget") or {}, "evidenceRequirements": plan.get("evidenceRequirements") or [], "systemFacts": {"sceneDataJudgmentPackage": package, "taskGenerationDecision": decision, "ragPermissionContext": rag_context}, "taskMappingAgentEvidence": decision.get("taskMappingAgentEvidence") or {}, "fallbackForbidden": bool(decision.get("fallbackForbidden", True)), "businessNoTaskForbidden": bool(decision.get("businessNoTaskForbidden", True)), "productJudgmentPackage": package, "productIdentity": product, "source": "v19_3_multi_route_task_pool_admission_bridge"}


def _admit_decision(decision: Dict[str, Any], *, created_by: str | None = None, force_new_snapshot: bool = False) -> Dict[str, Any]:
    try:
        if not _decision_has_real_agent_contract(decision):
            return {"ok": False, "status": "rejected_invalid_v19_3_multi_route_decision", "createdSnapshotCount": 0, "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "packageId": decision.get("packageId"), "reason": "decision_must_include_multi_route_trace_operator_view_and_creative_sop"}
        plan = decision.get("taskPlan") if isinstance(decision.get("taskPlan"), dict) else {}
        existing = None if force_new_snapshot else _existing_snapshot(decision.get("dataVersion"), decision.get("packageId"), decision.get("decision"), plan.get("taskResponsibility"))
        snapshot_created = 0
        if existing and existing.get("taskSnapshotId"):
            snapshot_id = existing.get("taskSnapshotId")
            snapshot_status = "reused_existing_operator_snapshot"
        else:
            snapshot = create_task_snapshot(_snapshot_body_from_decision(decision), created_by=created_by, force=True)
            snapshot_id = snapshot.get("taskSnapshotId")
            snapshot_created = 1
            snapshot_status = "created_multi_route_operator_snapshot"
        pool = enter_task_pool_from_snapshot(str(snapshot_id), created_by=created_by, force=False) if snapshot_id else {"ok": False, "status": "missing_snapshot_id"}
        return {"ok": bool(pool.get("ok")) and pool.get("status") in {"entered_task_pool", "idempotent"}, "status": pool.get("status"), "snapshotStatus": snapshot_status, "decisionId": decision.get("decisionId"), "packageId": decision.get("packageId"), "taskSnapshotId": snapshot_id, "taskId": (pool.get("task") or {}).get("id") or (pool.get("poolEntry") or {}).get("taskId"), "createdSnapshotCount": snapshot_created, "createdTaskCount": int(pool.get("createdTaskCount") or 0), "poolResult": pool}
    except Exception as exc:
        return {"ok": False, "status": "bridge_exception", "createdSnapshotCount": 0, "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "packageId": decision.get("packageId"), "reason": str(exc)[:500]}


def _refresh_views(data_version: str | None) -> Dict[str, Any]:
    try:
        from src.services.frontend_read_model_service import refresh_task_views, refresh_dashboard_view
        return {"status": "refreshed", "taskViews": refresh_task_views(data_version=data_version), "dashboard": refresh_dashboard_view()}
    except Exception as exc:
        return {"status": "refresh_failed", "error": str(exc)}


def task_pool_admission_station_v182(data_version: str | None, *, user_id: str | None = None, force_new_snapshot: bool = False, **_: Any) -> Dict[str, Any]:
    decisions = _load_decisions(data_version)
    results = [_admit_decision(decision, created_by=user_id, force_new_snapshot=force_new_snapshot) for decision in decisions]
    by_status = Counter(str(item.get("status")) for item in results)
    by_decision = Counter(str(item.get("decision")) for item in decisions)
    by_task_type = Counter(str((item.get("taskPlan") or {}).get("taskType")) for item in decisions)
    by_responsibility = Counter(str((item.get("taskPlan") or {}).get("taskResponsibility")) for item in decisions)
    by_scenario = Counter(str((item.get("taskPlan") or {}).get("operatingScenario")) for item in decisions)
    by_selected_route = Counter(str((((item.get("taskPlan") or {}).get("agentJudgmentTrace") or {}).get("selectedRoute") or {}).get("routeId")) for item in decisions)
    created_snapshots = sum(int(item.get("createdSnapshotCount") or 0) for item in results)
    created_tasks = sum(int(item.get("createdTaskCount") or 0) for item in results)
    admitted_or_existing = sum(1 for item in results if item.get("status") in {"entered_task_pool", "idempotent"})
    rejected = [item for item in results if item.get("status") not in {"entered_task_pool", "idempotent"}]
    refresh = _refresh_views(data_version)
    status = "completed" if decisions and admitted_or_existing == len(decisions) else "failed" if decisions and admitted_or_existing == 0 else "partial" if decisions else "no_formal_decisions"
    zero_reasons = []
    if decisions and admitted_or_existing == 0:
        zero_reasons = ["V19.3 bridge found formal decisions but admitted zero multi-route creative tasks."] + [str(item.get("reason") or item.get("status")) for item in rejected[:8]]
    try:
        record_task_generation_run(data_version=data_version, input_bundle_count=0, agent_judgment_count=0, product_judgment_package_count=0, identity_gap_count=0, task_decision_count=len(decisions), by_decision=dict(by_decision), streamed_task_snapshot_count=created_snapshots, task_pool_created_count=created_tasks, skipped_formal_count=len(rejected), zero_task_reasons=zero_reasons, agent1_api_call_count=0, rag_retrieval_count=0, api_budget_violation=False, agent_budget_summary={"source": "v19_3_multi_route_lifecycle_bridge", "byResponsibility": dict(by_responsibility), "byScenario": dict(by_scenario), "bySelectedRoute": dict(by_selected_route)}, total_agent_call_count=0, total_agent_budget=0, source="v19_3_task_pool_admission_bridge")
    except Exception:
        pass
    return {"version": TASK_POOL_ADMISSION_BRIDGE_VERSION, "stationId": "task_pool_admission_station", "dataVersion": data_version, "status": status, "formalDecisionCount": len(decisions), "taskDecisionCount": len(decisions), "createdSnapshotCount": created_snapshots, "admittedOrExistingTaskCount": admitted_or_existing, "createdTaskCount": created_tasks, "streamedTaskPoolCount": created_tasks, "rejectedCount": len(rejected), "byDecision": dict(by_decision), "byTaskType": dict(by_task_type), "byResponsibility": dict(by_responsibility), "byScenario": dict(by_scenario), "bySelectedRoute": dict(by_selected_route), "byAdmissionStatus": dict(by_status), "results": results[:80], "refresh": refresh, "taskPoolRef": f"task_pool:{data_version or 'latest'}", "outputRef": f"task_pool:{data_version or 'latest'}", "rule": "V19.3 bridge: TaskSnapshot preserves backend trace but frontend reads only operatorJudgmentView."}
