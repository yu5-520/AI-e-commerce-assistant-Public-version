"""V19.4 Task Pool Admission Bridge.

The bridge admits only operator_growth decisions that contain selectedActionFamily
and the matching family-specific SOP contract. It preserves backend route trace
while exposing operatorJudgmentView and selected action family for frontend use.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List
import uuid

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.lifecycle_task_v183_service import create_lifecycle_task_from_snapshot
from src.services.operator_action_family_v194_service import action_family_contract_ok, action_family_public_label
from src.services.task_generation_run_service import record_task_generation_run
from src.services.task_snapshot_station_service import create_task_snapshot

TASK_POOL_ADMISSION_BRIDGE_VERSION = "19.4"
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
    return [item for item in [_load(row["payload"]) for row in rows] if item.get("decision") in FORMAL_DECISIONS]


def _decision_contract_ok(decision: Dict[str, Any]) -> bool:
    evidence = decision.get("taskMappingAgentEvidence") if isinstance(decision.get("taskMappingAgentEvidence"), dict) else {}
    plan = decision.get("taskPlan") if isinstance(decision.get("taskPlan"), dict) else {}
    return bool(evidence.get("source") == "real_task_mapping_agent" and evidence.get("businessEventRouter") == "v19.4_action_family_router" and plan.get("taskResponsibility") == "operator_growth" and plan.get("departmentTaskType") == "operator_growth" and plan.get("selectedActionFamily") and plan.get("businessEventId") and plan.get("productIdentity") and plan.get("sopSource") == "llm_agent_action_family_dynamic_sop" and _trace_ok(plan) and _view_ok(plan) and action_family_contract_ok(plan) and len(plan.get("sopSteps") or []) >= 4 and len(plan.get("evidenceRequirements") or []) >= 2)


def _ensure_task_pool_tables() -> None:
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_pool_entries (
                pool_entry_id TEXT PRIMARY KEY,
                task_snapshot_id TEXT NOT NULL,
                task_id TEXT,
                data_version TEXT,
                status TEXT NOT NULL,
                decision TEXT,
                task_layer TEXT,
                assignee_id TEXT,
                reviewer_id TEXT,
                dedupe_key TEXT,
                reason TEXT,
                payload TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        ensure_columns(conn, "task_pool_entries", {"task_id": "TEXT", "data_version": "TEXT", "decision": "TEXT", "task_layer": "TEXT", "assignee_id": "TEXT", "reviewer_id": "TEXT", "dedupe_key": "TEXT", "reason": "TEXT", "payload": "TEXT", "created_by": "TEXT"})
        conn.commit()


def _snapshot_body(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = decision.get("taskPlan") if isinstance(decision.get("taskPlan"), dict) else {}
    package = decision.get("productJudgmentPackage") if isinstance(decision.get("productJudgmentPackage"), dict) else {}
    product = plan.get("productIdentity") if isinstance(plan.get("productIdentity"), dict) else {}
    family = str(plan.get("selectedActionFamily") or "")
    view = plan.get("operatorJudgmentView") if isinstance(plan.get("operatorJudgmentView"), dict) else {}
    view.setdefault("selectedActionFamilyLabel", action_family_public_label(family))
    return {"dataVersion": decision.get("dataVersion"), "decision": decision.get("decision"), "confidence": 0.78, "entityType": "product", "entityId": decision.get("productId") or plan.get("productId") or product.get("productId"), "productId": decision.get("productId") or plan.get("productId") or product.get("productId"), "storeId": decision.get("storeId") or plan.get("storeId") or product.get("storeId"), "signalRef": decision.get("packageId"), "bundleRef": decision.get("packageId"), "agentJudgment": {"decision": decision.get("decision"), "status": "v19_4_action_family_task_decision", "taskResponsibility": "operator_growth", "businessEventId": plan.get("businessEventId"), "selectedActionFamily": family, "operatorJudgmentView": view}, "taskPlan": {**plan, "operatorJudgmentView": view}, "businessEventId": plan.get("businessEventId"), "taskResponsibility": "operator_growth", "departmentTaskType": "operator_growth", "selectedActionFamily": family, "selectedActionFamilyLabel": action_family_public_label(family), "agentJudgmentTrace": plan.get("agentJudgmentTrace") or {}, "operatorJudgmentView": view, "actionFamilyContract": plan.get("actionFamilyContract") or {}, "titleVariants": plan.get("titleVariants") or [], "mainImageStructures": plan.get("mainImageStructures") or [], "activityPlan": plan.get("activityPlan") or [], "activityEligibilityChecklist": plan.get("activityEligibilityChecklist") or [], "activityMaterialChecklist": plan.get("activityMaterialChecklist") or [], "budgetAdjustmentPlan": plan.get("budgetAdjustmentPlan") or [], "campaignSelectionRule": plan.get("campaignSelectionRule") or [], "stopLossRule": plan.get("stopLossRule") or [], "cutBudgetPlan": plan.get("cutBudgetPlan") or [], "lowEfficiencyPlanList": plan.get("lowEfficiencyPlanList") or [], "preserveTrafficRule": plan.get("preserveTrafficRule") or [], "conversionBlockers": plan.get("conversionBlockers") or [], "detailPageChecklist": plan.get("detailPageChecklist") or [], "priceOrCouponPlan": plan.get("priceOrCouponPlan") or [], "comparisonProducts": plan.get("comparisonProducts") or [], "trafficSplitPlan": plan.get("trafficSplitPlan") or [], "testMetric": plan.get("testMetric") or [], "systemFacts": {"sceneDataJudgmentPackage": package, "taskGenerationDecision": decision}, "taskMappingAgentEvidence": decision.get("taskMappingAgentEvidence") or {}, "productIdentity": product, "source": "v19_4_action_family_task_pool_admission_bridge"}


def _admit_decision(decision: Dict[str, Any], *, created_by: str | None = None, force_new_snapshot: bool = False) -> Dict[str, Any]:
    if not _decision_contract_ok(decision):
        return {"ok": False, "status": "rejected_invalid_v19_4_action_family_decision", "createdSnapshotCount": 0, "createdTaskCount": 0, "decisionId": decision.get("decisionId"), "packageId": decision.get("packageId"), "reason": "decision_must_include_selected_action_family_and_matching_contract"}
    snapshot = create_task_snapshot(_snapshot_body(decision), created_by=created_by, force=True)
    task = create_lifecycle_task_from_snapshot(snapshot, created_by=created_by)
    _ensure_task_pool_tables()
    now = datetime.now().isoformat()
    entry_id = f"TPE-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    with connect() as conn:
        conn.execute("""
            INSERT INTO task_pool_entries (pool_entry_id, task_snapshot_id, task_id, data_version, status, decision, task_layer, assignee_id, reviewer_id, dedupe_key, reason, payload, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (entry_id, snapshot.get("taskSnapshotId"), task.get("id"), decision.get("dataVersion"), "entered_task_pool", decision.get("decision"), task.get("taskLayer"), task.get("assigneeId"), task.get("reviewerId"), task.get("dedupeKey"), "V19.4动作族任务已进入任务池。", dumps({"snapshot": snapshot, "task": task, "source": "v19_4_action_family_bridge"}), created_by, now, now))
        conn.commit()
    return {"ok": True, "status": "entered_task_pool", "decisionId": decision.get("decisionId"), "packageId": decision.get("packageId"), "taskSnapshotId": snapshot.get("taskSnapshotId"), "taskId": task.get("id"), "createdSnapshotCount": 1, "createdTaskCount": 1}


def _refresh_views(data_version: str | None) -> Dict[str, Any]:
    try:
        from src.services.frontend_read_model_service import refresh_task_views, refresh_dashboard_view
        return {"status": "refreshed", "taskViews": refresh_task_views(data_version=data_version), "dashboard": refresh_dashboard_view()}
    except Exception as exc:
        return {"status": "refresh_failed", "error": str(exc)}


def task_pool_admission_station_v194(data_version: str | None, *, user_id: str | None = None, force_new_snapshot: bool = False, **_: Any) -> Dict[str, Any]:
    decisions = _load_decisions(data_version)
    results = [_admit_decision(decision, created_by=user_id, force_new_snapshot=force_new_snapshot) for decision in decisions]
    by_status = Counter(str(item.get("status")) for item in results)
    by_family = Counter(str((item.get("taskPlan") or {}).get("selectedActionFamily")) for item in decisions)
    by_route = Counter(str((((item.get("taskPlan") or {}).get("agentJudgmentTrace") or {}).get("selectedRoute") or {}).get("routeId")) for item in decisions)
    created_snapshots = sum(int(item.get("createdSnapshotCount") or 0) for item in results)
    created_tasks = sum(int(item.get("createdTaskCount") or 0) for item in results)
    rejected = [item for item in results if item.get("status") != "entered_task_pool"]
    refresh = _refresh_views(data_version)
    status = "completed" if decisions and not rejected else "failed" if decisions and len(rejected) == len(decisions) else "partial" if decisions else "no_formal_decisions"
    try:
        record_task_generation_run(data_version=data_version, input_bundle_count=0, agent_judgment_count=0, product_judgment_package_count=0, identity_gap_count=0, task_decision_count=len(decisions), by_decision={}, streamed_task_snapshot_count=created_snapshots, task_pool_created_count=created_tasks, skipped_formal_count=len(rejected), zero_task_reasons=[str(item.get("reason") or item.get("status")) for item in rejected[:8]], agent1_api_call_count=0, rag_retrieval_count=0, api_budget_violation=False, agent_budget_summary={"source": "v19_4_action_family_bridge", "bySelectedRoute": dict(by_route), "bySelectedActionFamily": dict(by_family)}, total_agent_call_count=0, total_agent_budget=0, source="v19_4_task_pool_admission_bridge")
    except Exception:
        pass
    return {"version": TASK_POOL_ADMISSION_BRIDGE_VERSION, "stationId": "task_pool_admission_station", "dataVersion": data_version, "status": status, "formalDecisionCount": len(decisions), "createdSnapshotCount": created_snapshots, "createdTaskCount": created_tasks, "admittedOrExistingTaskCount": created_tasks, "rejectedCount": len(rejected), "bySelectedRoute": dict(by_route), "bySelectedActionFamily": dict(by_family), "byAdmissionStatus": dict(by_status), "results": results[:80], "refresh": refresh, "taskPoolRef": f"task_pool:{data_version or 'latest'}", "outputRef": f"task_pool:{data_version or 'latest'}", "rule": "V19.4 bridge: selectedActionFamily controls SOP admission contract."}
