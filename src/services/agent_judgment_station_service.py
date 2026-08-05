"""V14.8 Agent product diagnosis station service.

Agent consumes one fullProductBundle per product. When Agent soft-routing returns
create_task_snapshot or manager_review_required, the system immediately converts
that single judgment into a V11.8 SOP task snapshot and then into task pool. It
no longer waits for the whole worker batch to finish before handing off mature
jobs to the lifecycle system.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.llm_provider_service import generate_json
from src.services.operation_budget_service import estimate_operation_budget, reserve_budget_for_task
from src.services.rag_context_station_service import build_rag_context_snapshot, latest_rag_context
from src.services.signal_pool_service import list_signals, update_signal_status
from src.services.task_snapshot_station_service import create_task_snapshot

AGENT_JUDGMENT_STATION_VERSION = "14.8.0"
VALID_DECISIONS = {"create_task_snapshot", "create_task", "manager_review_required", "manager_review", "observe_only", "ignore_noise", "data_gap_required", "merge_candidate", "merge_existing", "evidence_only"}
SNAPSHOT_DECISIONS = {"create_task_snapshot", "manager_review_required"}


def now_iso() -> str:
    return datetime.now().isoformat()


def make_judgment_id() -> str:
    return f"AJ-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"


def ensure_agent_judgment_tables() -> None:
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_judgments_v14 (
                judgment_id TEXT PRIMARY KEY,
                data_version TEXT,
                signal_id TEXT,
                entity_type TEXT,
                entity_id TEXT,
                decision TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                status TEXT NOT NULL,
                rag_context_ref TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        ensure_columns(conn, "agent_judgments_v14", {"data_version": "TEXT", "signal_id": "TEXT", "entity_type": "TEXT", "entity_id": "TEXT", "confidence": "REAL DEFAULT 0", "rag_context_ref": "TEXT", "updated_at": "TEXT"})
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_judgments_v14_version ON agent_judgments_v14(data_version, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_judgments_v14_decision ON agent_judgments_v14(decision, status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_judgments_v14_signal ON agent_judgments_v14(signal_id, decision, status)")
        conn.commit()


def _row_to_judgment(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    return {**payload, "judgmentId": row["judgment_id"], "dataVersion": row["data_version"], "signalId": row["signal_id"], "entityType": row["entity_type"], "entityId": row["entity_id"], "decision": row["decision"], "confidence": float(row["confidence"] or 0), "status": row["status"], "ragContextRef": row["rag_context_ref"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def _existing_judgment_for_signal(signal_id: str | None) -> Dict[str, Any] | None:
    if not signal_id:
        return None
    ensure_agent_judgment_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_judgments_v14 WHERE signal_id = ? ORDER BY created_at DESC LIMIT 1", (signal_id,)).fetchone()
    return _row_to_judgment(row) if row else None


def list_agent_judgments(data_version: str | None = None, status: str | None = None, limit: int = 200) -> Dict[str, Any]:
    ensure_agent_judgment_tables()
    clauses = []
    params: List[Any] = []
    if data_version:
        clauses.append("data_version = ?")
        params.append(data_version)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM agent_judgments_v14 {where} ORDER BY created_at DESC LIMIT ?", [*params, limit]).fetchall()
    items = [_row_to_judgment(row) for row in rows]
    by_decision: Dict[str, int] = defaultdict(int)
    by_status: Dict[str, int] = defaultdict(int)
    for item in items:
        by_decision[str(item.get("decision"))] += 1
        by_status[str(item.get("status"))] += 1
    return {"version": AGENT_JUDGMENT_STATION_VERSION, "dataVersion": data_version, "judgmentCount": len(items), "byDecision": dict(by_decision), "byStatus": dict(by_status), "judgments": items}


def _canonical_decision(decision: Any) -> str:
    value = str(decision or "observe_only")
    if value == "create_task":
        return "create_task_snapshot"
    if value == "manager_review":
        return "manager_review_required"
    if value == "merge_existing":
        return "merge_candidate"
    if value == "evidence_only":
        return "observe_only"
    return value if value in VALID_DECISIONS else "observe_only"


def _profile(bundle: Dict[str, Any]) -> Dict[str, Any]:
    return bundle.get("profileLayer") if isinstance(bundle.get("profileLayer"), dict) else {}


def _metric(bundle: Dict[str, Any]) -> Dict[str, Any]:
    return bundle.get("metricLayer") if isinstance(bundle.get("metricLayer"), dict) else {}


def _risk_from_bundle(bundle: Dict[str, Any]) -> tuple[str, str]:
    strength = str(bundle.get("signalStrength") or "normal")
    primary = str(bundle.get("primarySignalType") or bundle.get("signalType") or "")
    if strength == "high" or primary.startswith("redline_"):
        return "high", "高"
    if strength == "medium":
        return "medium", "中"
    return "low", "低"


def _evidence_completeness(bundle: Dict[str, Any]) -> float:
    metric = _metric(bundle)
    core = ["paymentAmount", "roas", "roi", "adSpend", "refundRate", "inventory", "clickRate", "conversionRate", "grossMargin"]
    known = sum(1 for key in core if metric.get(key) not in {None, "", "—", "未识别"})
    return round(known / len(core), 4)


def _budget_task_type(bundle: Dict[str, Any]) -> str:
    primary = str(bundle.get("primarySignalType") or bundle.get("signalType") or "")
    metric = str(bundle.get("metricCode") or "")
    if metric in {"roas", "roi", "adSpend"} or "roas" in primary:
        return "roas_increase" if str(bundle.get("signalStrength")) in {"medium", "high"} else "roas_decrease"
    if "refund" in primary or metric == "refundRate":
        return "after_sales_check"
    if "inventory" in primary or metric == "inventory":
        return "replenishment"
    if primary.startswith("data_gap"):
        return "data_gap_fix"
    return "detail_page_test"


def _budget_payload(bundle: Dict[str, Any], risk_level: str) -> Dict[str, Any]:
    metric = _metric(bundle)
    return {"riskLevel": risk_level, "currentDailyAdSpend": metric.get("adSpend"), "adSpend": metric.get("adSpend"), "increaseRate": 0.15, "decreaseRate": 0.2, "testDays": 2, "observeDays": 2, "suggestedGoodsValue": metric.get("inventoryValue") or metric.get("paymentAmount") or 0, "expectedUnits": metric.get("paymentUnitCount") or metric.get("inventory") or 0, "campaignAdSpend": metric.get("campaignAdSpend") or 0, "trendWindows": ((bundle.get("trendWindows") or {}).get("windows") or {})}


def _soft_routing(bundle: Dict[str, Any], rag_context: Dict[str, Any]) -> Dict[str, Any]:
    strength = str(bundle.get("signalStrength") or "normal")
    metric_code = str(bundle.get("metricCode") or "all_metrics")
    cross = bundle.get("crossValidation") if isinstance(bundle.get("crossValidation"), dict) else {}
    abnormal = int(cross.get("abnormalMetricCount") or 0)
    changed = int(cross.get("changedMetricCount") or 0)
    source_count = int(cross.get("sourceVersionCount") or cross.get("sourceDatasetCount") or 0)
    evidence_score = _evidence_completeness(bundle)
    strength_score = {"high": 0.72, "medium": 0.50, "low": 0.28, "normal": 0.08}.get(strength, 0.1)
    cross_score = min(0.16, 0.04 * source_count) + min(0.12, 0.04 * abnormal) + min(0.08, 0.02 * changed)
    action_score = 0.1 if metric_code not in {"all_metrics", "product_snapshot", "product_presence"} else 0.03
    score = max(0.0, min(1.0, strength_score + cross_score + action_score + evidence_score * 0.12))
    missing = [key for key in ["paymentAmount", "roi", "roas", "refundRate", "inventory", "adSpend"] if _metric(bundle).get(key) in {None, "", "—", "未识别"}]
    low_evidence = evidence_score < 0.35
    if strength == "high" and score >= 0.74:
        decision = "manager_review_required"
    elif score >= 0.66:
        decision = "create_task_snapshot"
    elif low_evidence and strength in {"high", "medium"}:
        decision = "data_gap_required"
    elif changed > 0:
        decision = "observe_only"
    else:
        decision = "observe_only"
    return {"version": AGENT_JUDGMENT_STATION_VERSION, "mode": "agent_soft_routing_score", "decision": decision, "taskValueScore": round(score, 4), "attentionWorthiness": "high" if score >= 0.74 else "medium" if score >= 0.55 else "low", "evidenceCompleteness": evidence_score, "missingFields": missing, "missingFieldImpact": "high" if low_evidence else "medium" if missing else "none", "volatilityOutsideRange": strength in {"high", "medium"}, "crossValidated": source_count >= 2 or abnormal >= 2, "actionClarity": "clear" if action_score >= 0.1 else "weak", "routingReason": "V14.8 Agent soft-routes one fullProductBundle; signals are evidence and missing fields lower confidence without hard blocking.", "ragContextApplied": bool(rag_context)}


def _decision_template(bundle: Dict[str, Any], rag_context: Dict[str, Any]) -> Dict[str, Any]:
    profile = _profile(bundle)
    risk_level, priority = _risk_from_bundle(bundle)
    routing = _soft_routing(bundle, rag_context)
    decision = routing.get("decision") or "observe_only"
    metric_code = bundle.get("metricCode") or "经营指标"
    product_id = bundle.get("productId") or profile.get("productId") or bundle.get("entityId")
    entity_id = bundle.get("entityId") or product_id or bundle.get("bundleId") or "latest"
    deadline = "6小时内" if priority == "高" else "24小时内" if priority == "中" else "后台观察"
    budget_task_type = _budget_task_type(bundle)
    operation_budget = estimate_operation_budget(budget_task_type, _budget_payload(bundle, risk_level))
    evidence = ["商品全量信息包", "核心指标变化截图", "运营处理说明"]
    if budget_task_type in {"roas_increase", "roas_decrease"}:
        evidence = ["投放后台截图", "ROAS/ROI口径", "广告消耗变化", "点击率/转化率变化"]
    elif budget_task_type == "replenishment":
        evidence = ["库存截图", "可售天数", "供应链反馈", "补货建议"]
    elif budget_task_type == "after_sales_check":
        evidence = ["退款原因TOP5", "售后截图", "商品评价截图", "客服反馈"]
    if routing.get("missingFields"):
        evidence.append("缺失字段补充说明")
    title_metric = metric_code if metric_code not in {"all_metrics", "product_snapshot", "product_presence"} else "经营状态"
    task_type_name = "商品经营复核任务" if decision in SNAPSHOT_DECISIONS else "商品经营观察"
    reason = routing.get("routingReason") or "Agent 在RAG波动边界下完成商品全量包软路由判断。"
    task_plan = {"title": f"{task_type_name}｜{product_id or entity_id}｜{title_metric}", "subtitle": bundle.get("primarySignalType") or bundle.get("signalType") or "full_product_bundle", "entityType": bundle.get("entityType") or "product", "entityId": entity_id, "productId": product_id, "storeId": bundle.get("storeId") or profile.get("storeId"), "verticalCategory": bundle.get("verticalCategory") or profile.get("verticalCategory"), "taskType": budget_task_type, "actionType": "agent_soft_routed_operation", "priority": priority, "riskLevel": risk_level, "deadline": deadline, "riskDomain": title_metric, "operationBudget": operation_budget, "sopSteps": [f"{deadline}核查 {product_id or entity_id} 的{title_metric}变化，优先比对商品档案层、商品数据层和商品快照层。", "结合RAG波动边界判断该波动是正常范围、观察信号、数据缺口还是需要运营动作。", "整理订单、退款、库存、投放等相关截图；缺失字段以unknown说明，不因字段缺失直接阻断任务。", "提交处理结论、证据截图、数据口径和后续复盘指标。"], "evidenceRequirements": evidence, "reviewMetrics": ["支付金额", "ROAS/ROI", "广告消耗", "点击率", "转化率", "退款率", "毛利率", "库存"], "needManagerReview": decision == "manager_review_required", "reason": reason}
    return {"decision": decision, "confidence": max(0.45, min(0.92, float(routing.get("taskValueScore") or 0.5))), "reason": reason, "taskPlan": task_plan, "operationBudget": operation_budget, "evidenceRequirements": evidence, "reviewMetrics": task_plan["reviewMetrics"], "softRouting": routing, "agentDiagnosis": {"mainDiagnosis": reason, "taskValueScore": routing.get("taskValueScore"), "attentionWorthiness": routing.get("attentionWorthiness"), "missingFields": routing.get("missingFields"), "evidenceCompleteness": routing.get("evidenceCompleteness")}, "riskBoundary": ["Agent soft routing is internal metadata; formal task output stays in V11.8 SOP package.", "Signals inside fullProductBundle are evidence, not task entry points.", "System enforces permission, budget upper bound, dedupe, lifecycle and audit only."], "ragContextApplied": bool(rag_context)}


def _normalize_llm_output(output: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    decision = _canonical_decision(output.get("decision"))
    merged = {**fallback, **{key: value for key, value in output.items() if value is not None}, "decision": decision}
    try:
        merged["confidence"] = max(0.0, min(1.0, float(merged.get("confidence") or fallback.get("confidence") or 0)))
    except Exception:
        merged["confidence"] = fallback.get("confidence") or 0.5
    if not isinstance(merged.get("taskPlan"), dict):
        merged["taskPlan"] = fallback.get("taskPlan") or {}
    if not isinstance(merged.get("operationBudget"), dict):
        merged["operationBudget"] = (merged.get("taskPlan") or {}).get("operationBudget") or fallback.get("operationBudget") or {}
    if not isinstance(merged.get("evidenceRequirements"), list):
        merged["evidenceRequirements"] = fallback.get("evidenceRequirements") or []
    if not isinstance(merged.get("reviewMetrics"), list):
        merged["reviewMetrics"] = fallback.get("reviewMetrics") or []
    if not isinstance(merged.get("softRouting"), dict):
        merged["softRouting"] = fallback.get("softRouting") or {}
    merged["taskPlan"]["operationBudget"] = merged.get("operationBudget")
    merged["taskPlan"]["reason"] = merged.get("reason") or merged["taskPlan"].get("reason")
    merged["agentDiagnosis"] = merged.get("agentDiagnosis") or fallback.get("agentDiagnosis") or {}
    return merged


def _judge_signal(signal: Dict[str, Any], rag_context: Dict[str, Any]) -> Dict[str, Any]:
    fallback = _decision_template(signal, rag_context)
    llm_result = generate_json(prompt_name="v148_full_product_bundle_soft_routing", payload={"fullProductBundle": signal, "ragContext": rag_context, "fallbackDecision": fallback, "interfaceBoundary": "Agent judges one product full bundle and returns soft routing metadata. Formal tasks remain V11.8 SOP packages."}, expected_keys=["decision", "confidence", "reason", "taskPlan", "operationBudget", "softRouting"], agent_name="V14.8 Full Product Bundle Agent", schema_name="v148_full_product_bundle_soft_routing")
    output = llm_result.get("output") or {}
    judgment = _normalize_llm_output(output, fallback)
    judgment["llm"] = {"provider": llm_result.get("provider"), "model": llm_result.get("model"), "status": llm_result.get("status"), "fallbackUsed": llm_result.get("fallbackUsed"), "trace": llm_result.get("trace")}
    return judgment


def _signal_status_for_decision(decision: str) -> str:
    if decision in SNAPSHOT_DECISIONS:
        return "judged_pending_snapshot"
    if decision == "ignore_noise":
        return "ignored_noise"
    if decision in {"merge_candidate", "merge_existing"}:
        return "merge_candidate"
    if decision == "data_gap_required":
        return "data_gap_observed"
    return "observed_only"


def _save_judgment(signal: Dict[str, Any], rag_context: Dict[str, Any], judgment: Dict[str, Any]) -> Dict[str, Any]:
    existing = _existing_judgment_for_signal(signal.get("signalId"))
    if existing:
        return {**existing, "idempotentHit": True}
    ensure_agent_judgment_tables()
    judgment_id = make_judgment_id()
    created_at = now_iso()
    decision = _canonical_decision(judgment.get("decision"))
    status = "pending_task_snapshot" if decision in SNAPSHOT_DECISIONS else "judgment_recorded"
    payload = {"version": AGENT_JUDGMENT_STATION_VERSION, "judgmentId": judgment_id, "stationId": "agent_judgment_station", "dataVersion": signal.get("dataVersion"), "signalId": signal.get("signalId"), "bundleId": signal.get("bundleId"), "entityType": signal.get("entityType"), "entityId": signal.get("entityId"), "signal": signal, "fullProductBundle": signal, "ragContextRef": rag_context.get("ragContextRef") or rag_context.get("outputRef"), "ragContext": rag_context, "decision": decision, "confidence": judgment.get("confidence") or 0, "reason": judgment.get("reason"), "taskPlan": judgment.get("taskPlan") or {}, "operationBudget": judgment.get("operationBudget") or {}, "evidenceRequirements": judgment.get("evidenceRequirements") or [], "reviewMetrics": judgment.get("reviewMetrics") or [], "riskBoundary": judgment.get("riskBoundary") or [], "softRouting": judgment.get("softRouting") or {}, "agentDiagnosis": judgment.get("agentDiagnosis") or {}, "agentJudgment": {**judgment, "decision": decision}, "directInterfaceControlAllowed": False, "rule": "V14.8 Agent judgment is idempotent by fullProductBundle signalId; mature routes stream into SOP snapshot/pool."}
    with connect() as conn:
        conn.execute("""
            INSERT INTO agent_judgments_v14 (judgment_id, data_version, signal_id, entity_type, entity_id, decision, confidence, status, rag_context_ref, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (judgment_id, signal.get("dataVersion"), signal.get("signalId"), signal.get("entityType"), signal.get("entityId"), decision, float(judgment.get("confidence") or 0), status, payload["ragContextRef"], dumps(payload), created_at, created_at))
        conn.commit()
        row = conn.execute("SELECT * FROM agent_judgments_v14 WHERE judgment_id = ?", (judgment_id,)).fetchone()
    update_signal_status(signal.get("signalId"), _signal_status_for_decision(decision), {"agentJudgmentId": judgment_id, "decision": decision, "operationBudget": judgment.get("operationBudget"), "softRouting": judgment.get("softRouting")})
    return _row_to_judgment(row)


def _create_snapshot_from_judgment(item: Dict[str, Any], *, created_by: str | None = None, stream_to_pool: bool = False) -> Dict[str, Any]:
    decision = _canonical_decision(item.get("decision"))
    if decision not in SNAPSHOT_DECISIONS:
        return {"ok": False, "skipped": True, "reason": "soft_route_not_for_formal_task", "decision": decision, "judgmentId": item.get("judgmentId")}
    plan = item.get("taskPlan") or {}
    plan["operationBudget"] = item.get("operationBudget") or plan.get("operationBudget") or {}
    full_bundle = item.get("fullProductBundle") or item.get("signal") or {}
    snapshot = create_task_snapshot({"dataVersion": item.get("dataVersion"), "decision": decision, "confidence": item.get("confidence"), "entityType": item.get("entityType"), "entityId": item.get("entityId"), "productId": plan.get("productId") or full_bundle.get("productId"), "storeId": plan.get("storeId") or full_bundle.get("storeId"), "riskLevel": plan.get("riskLevel") or (item.get("operationBudget") or {}).get("riskLevel"), "operationBudget": item.get("operationBudget") or {}, "signalRef": item.get("signalId"), "bundleRef": item.get("bundleId"), "ragContext": item.get("ragContext") or {}, "agentJudgment": item.get("agentJudgment") or {}, "taskPlan": plan, "evidenceRequirements": item.get("evidenceRequirements") or [], "systemFacts": {"fullProductBundle": full_bundle, "judgmentId": item.get("judgmentId"), "softRouting": item.get("softRouting") or {}, "agentDiagnosis": item.get("agentDiagnosis") or {}, "operationBudget": item.get("operationBudget") or {}}, "source": "agent_judgment_station"}, created_by=created_by)
    budget_ledger = reserve_budget_for_task({**snapshot, "operationBudget": item.get("operationBudget") or {}, "riskLevel": plan.get("riskLevel") or (item.get("operationBudget") or {}).get("riskLevel"), "storeId": plan.get("storeId") or full_bundle.get("storeId"), "productId": plan.get("productId") or full_bundle.get("productId")}, user_id=created_by)
    pool_result = None
    if stream_to_pool:
        try:
            from src.services.task_pool_station_service import enter_task_pool_from_snapshot
            pool_result = enter_task_pool_from_snapshot(str(snapshot.get("taskSnapshotId")), created_by=created_by, force=False)
        except Exception as exc:
            pool_result = {"ok": False, "status": "pool_stream_failed", "error": str(exc)}
    with connect() as conn:
        payload = dict(item)
        payload["taskSnapshotId"] = snapshot.get("taskSnapshotId")
        payload["budgetLedger"] = budget_ledger
        payload["streamedTaskPool"] = pool_result
        conn.execute("UPDATE agent_judgments_v14 SET status = ?, payload = ?, updated_at = ? WHERE judgment_id = ?", ("task_snapshot_created", dumps(payload), now_iso(), item.get("judgmentId")))
        conn.commit()
    update_signal_status(item.get("signalId"), "task_snapshot_created", {"taskSnapshotId": snapshot.get("taskSnapshotId"), "judgmentId": item.get("judgmentId"), "budgetLedger": budget_ledger, "streamedTaskPool": pool_result})
    return {"ok": True, "snapshot": snapshot, "budgetLedger": budget_ledger, "poolResult": pool_result, "createdTaskCount": int((pool_result or {}).get("createdTaskCount") or 0)}


def run_agent_judgment_station(data_version: str | None = None, *, rag_context_ref: str | None = None, max_signals: int = 32, created_by: str | None = None, stream_to_pool: bool = True) -> Dict[str, Any]:
    ensure_agent_judgment_tables()
    rag_context = latest_rag_context(data_version) or build_rag_context_snapshot(data_version=data_version)
    signals = (list_signals(data_version=data_version, status="pending_rag_agent", limit=max_signals).get("signals") or [])[:max_signals]
    judgments: List[Dict[str, Any]] = []
    streamed: List[Dict[str, Any]] = []
    for signal in signals:
        saved = _save_judgment(signal, rag_context, _judge_signal(signal, rag_context))
        judgments.append(saved)
        if saved.get("decision") in SNAPSHOT_DECISIONS:
            streamed.append(_create_snapshot_from_judgment(saved, created_by=created_by, stream_to_pool=stream_to_pool))
    by_decision: Dict[str, int] = defaultdict(int)
    for item in judgments:
        by_decision[str(item.get("decision"))] += 1
    try:
        from src.services.frontend_read_model_service import refresh_dashboard_view, refresh_task_views
        if streamed:
            refresh_task_views()
        else:
            refresh_dashboard_view()
    except Exception:
        pass
    ref = f"agent_judgment:{data_version or 'latest'}"
    return {"version": AGENT_JUDGMENT_STATION_VERSION, "mode": "full_product_bundle_rag_soft_routing_streaming_agent", "dataVersion": data_version, "ragContextRef": rag_context_ref or rag_context.get("ragContextRef") or rag_context.get("outputRef"), "agentJudgmentRef": ref, "outputRef": ref, "signalCount": len(signals), "judgmentCount": len(judgments), "pendingTaskSnapshotCount": 0, "streamedTaskSnapshotCount": sum(1 for item in streamed if item.get("ok")), "streamedTaskPoolCount": sum(int(item.get("createdTaskCount") or 0) for item in streamed), "byDecision": dict(by_decision), "judgments": judgments, "streamed": streamed, "rule": "V14.8 streams each mature Agent judgment directly into SOP task snapshot and task pool; observe/data-gap/evidence routes stay out of formal task pool."}


def materialize_task_snapshots_from_judgments(data_version: str | None = None, *, created_by: str | None = None, limit: int = 50) -> Dict[str, Any]:
    result = list_agent_judgments(data_version=data_version, status="pending_task_snapshot", limit=limit)
    snapshots: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    budget_ledgers: List[Dict[str, Any]] = []
    for item in result.get("judgments") or []:
        created = _create_snapshot_from_judgment(item, created_by=created_by, stream_to_pool=False)
        if not created.get("ok"):
            skipped.append(created)
            continue
        snapshots.append(created.get("snapshot") or {})
        budget_ledgers.append(created.get("budgetLedger") or {})
    return {"version": AGENT_JUDGMENT_STATION_VERSION, "dataVersion": data_version, "taskSnapshotCount": len(snapshots), "snapshots": snapshots, "budgetLedgers": budget_ledgers, "skipped": skipped, "outputRef": f"task_snapshot:{data_version or 'latest'}", "rule": "V14.8 compatibility bulk materializer; normal path streams per mature judgment during Agent station."}
