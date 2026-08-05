"""V14.8.3 streaming Agent bridge.

Agent judgment quality may evolve independently, but pipeline completion must be
stable. Zero formal tasks is a completed generation result, not a broken chain.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.llm_provider_service import generate_json
from src.services.rag_context_station_service import build_rag_context_snapshot, latest_rag_context
from src.services.signal_pool_service import list_signals, update_signal_status
from src.services.task_generation_run_service import record_task_generation_run
from src.services.task_pool_station_service import enter_task_pool_from_snapshot
from src.services.task_snapshot_station_service import create_task_snapshot

AGENT_JUDGMENT_STATION_V1481_VERSION = "14.8.3"
FORMAL_DECISIONS = {"create_task_snapshot", "manager_review_required"}
CORE_FIELDS = ["paymentAmount", "roi", "roas", "adSpend", "refundRate", "inventory"]
CRITICAL_FIELDS = {"paymentAmount", "inventory", "refundRate"}
BLANK_VALUES = {None, "", "—", "未识别"}
GENERIC_TITLES = {"经营任务", "商品经营观察", "任务", "商品任务", "后台观察"}


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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_judgments_v14_signal ON agent_judgments_v14(signal_id, decision, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_judgments_v14_version ON agent_judgments_v14(data_version, created_at)")
        conn.commit()


def _row_to_judgment(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    return {**payload, "judgmentId": row["judgment_id"], "dataVersion": row["data_version"], "signalId": row["signal_id"], "entityType": row["entity_type"], "entityId": row["entity_id"], "decision": row["decision"], "confidence": float(row["confidence"] or 0), "status": row["status"], "ragContextRef": row["rag_context_ref"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def _existing_completed(signal_id: str | None) -> Dict[str, Any] | None:
    if not signal_id:
        return None
    ensure_agent_judgment_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_judgments_v14 WHERE signal_id = ? AND status = 'task_snapshot_created' ORDER BY created_at DESC LIMIT 1", (signal_id,)).fetchone()
    return _row_to_judgment(row) if row else None


def _metric(bundle: Dict[str, Any]) -> Dict[str, Any]:
    return bundle.get("metricLayer") if isinstance(bundle.get("metricLayer"), dict) else {}


def _profile(bundle: Dict[str, Any]) -> Dict[str, Any]:
    return bundle.get("profileLayer") if isinstance(bundle.get("profileLayer"), dict) else {}


def _known(value: Any) -> bool:
    return value not in BLANK_VALUES


def _missing_fields(bundle: Dict[str, Any]) -> List[str]:
    metric = _metric(bundle)
    return [field for field in CORE_FIELDS if not _known(metric.get(field))]


def _score_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    strength = str(bundle.get("signalStrength") or "normal")
    cross = bundle.get("crossValidation") if isinstance(bundle.get("crossValidation"), dict) else {}
    abnormal = int(cross.get("abnormalMetricCount") or 0)
    changed = int(cross.get("changedMetricCount") or 0)
    source_count = int(cross.get("sourceVersionCount") or cross.get("sourceDatasetCount") or 0)
    missing = _missing_fields(bundle)
    evidence = round((len(CORE_FIELDS) - len(missing)) / len(CORE_FIELDS), 4)
    score = {"high": 0.72, "medium": 0.50, "low": 0.28, "normal": 0.08}.get(strength, 0.1)
    score += min(0.16, source_count * 0.04) + min(0.12, abnormal * 0.04) + min(0.08, changed * 0.02) + evidence * 0.12
    missing_set = set(missing)
    critical_gap = bool(CRITICAL_FIELDS.intersection(missing_set) or {"roi", "roas"}.issubset(missing_set))
    severe_gap = bool(critical_gap and (strength in {"high", "medium"} or evidence < 0.5 or source_count >= 1))
    return {"score": round(max(0.0, min(1.0, score)), 4), "strength": strength, "abnormal": abnormal, "changed": changed, "sourceCount": source_count, "missingFields": missing, "criticalGap": critical_gap, "severeGap": severe_gap, "evidenceCompleteness": evidence}


def _fallback_judgment(bundle: Dict[str, Any], rag_context: Dict[str, Any]) -> Dict[str, Any]:
    profile = _profile(bundle)
    metric = _metric(bundle)
    scored = _score_bundle(bundle)
    product_id = bundle.get("productId") or profile.get("productId") or bundle.get("entityId") or "PRODUCT"
    entity_id = bundle.get("entityId") or product_id
    priority = "高" if scored["strength"] == "high" else "中" if scored["strength"] == "medium" else "低"
    metric_code = bundle.get("metricCode") or "经营状态"

    if scored["severeGap"]:
        decision = "create_task_snapshot"
        priority = "中" if priority == "低" else priority
        data_gap = True
    elif scored["strength"] == "high" and scored["score"] >= 0.78 and not scored["criticalGap"]:
        decision = "manager_review_required"
        data_gap = False
    elif scored["strength"] in {"high", "medium"} and scored["score"] >= 0.68 and scored["abnormal"] >= 1:
        decision = "create_task_snapshot"
        data_gap = False
    else:
        decision = "observe_only"
        data_gap = False

    if data_gap:
        title = f"商品数据核验｜{product_id}｜指标事实补齐"
        task_type = "data_gap_fix"
        action_type = "data_gap_verification"
        deadline = "24小时内"
        reason = f"核心指标缺失：{', '.join(scored['missingFields'])}。系统生成数据核验任务，不再因为字段缺失阻断任务流水线。"
        sop_steps = [
            f"24小时内核查 {product_id} 的原始报表字段映射，确认缺失字段：{', '.join(scored['missingFields'])}。",
            "回到ERP/CRM/投放/库存报表，确认是字段未识别、真实0值，还是报表未上传。",
            "补齐字段来源、统计时间、口径说明，并上传原始报表或后台截图。",
            "补齐后重新导入或刷新读模型，让商品全量包重新进入Agent判断。",
        ]
        evidence = ["原始报表截图", "字段映射截图", "缺失字段补充说明", "数据口径确认"]
        risk_domain = "指标事实补齐"
    elif decision in FORMAL_DECISIONS:
        title = f"商品经营复核｜{product_id}｜{metric_code}"
        task_type = "after_sales_check" if metric_code == "refundRate" else "replenishment" if metric_code == "inventory" else "roas_decrease" if metric_code in {"roi", "roas", "adSpend"} else "detail_page_test"
        action_type = "agent_soft_routed_operation"
        deadline = "6小时内" if priority == "高" else "24小时内"
        reason = "Agent基于商品全量包、RAG波动边界和交叉验证完成软路由判断，达到正式任务阈值。"
        sop_steps = [f"{deadline}核查 {product_id} 的{metric_code}变化，先比对商品档案层、商品数据层和商品快照层。", "整理订单、退款、库存、投放等相关截图，确认波动是否超出RAG边界。", "提交处理结论、执行证据和后续复盘指标。"]
        evidence = ["商品全量信息包", "核心指标变化截图", "运营处理说明"]
        risk_domain = metric_code
    else:
        title = f"商品观察记录｜{product_id}｜{metric_code}"
        task_type = "observe_only"
        action_type = "backend_observation"
        deadline = "后台观察"
        reason = "该商品仅达到观察阈值，沉淀为商品标签/观察日志，不进入正式执行任务池。"
        sop_steps = []
        evidence = []
        risk_domain = metric_code

    task_plan = {"title": title, "subtitle": bundle.get("primarySignalType") or bundle.get("signalType") or "full_product_bundle", "entityType": bundle.get("entityType") or "product", "entityId": entity_id, "productId": product_id, "storeId": bundle.get("storeId") or profile.get("storeId"), "verticalCategory": bundle.get("verticalCategory") or profile.get("verticalCategory"), "taskType": task_type, "actionType": action_type, "priority": priority, "riskLevel": "high" if priority == "高" else "medium" if priority == "中" else "low", "deadline": deadline, "riskDomain": risk_domain, "operationBudget": {"taskType": task_type, "riskLevel": "medium" if data_gap else "low", "budgetUpperBound": 0, "operatorBudgetApplies": False, "requiresApproval": decision == "manager_review_required"}, "sopSteps": sop_steps, "evidenceRequirements": evidence, "reviewMetrics": ["支付金额", "ROAS/ROI", "广告消耗", "点击率", "转化率", "退款率", "毛利率", "库存"], "needManagerReview": decision == "manager_review_required", "reason": reason, "missingFields": scored["missingFields"]}
    return {"decision": decision, "confidence": max(0.45, min(0.92, scored["score"])), "reason": reason, "taskPlan": task_plan, "operationBudget": task_plan["operationBudget"], "evidenceRequirements": evidence, "reviewMetrics": task_plan["reviewMetrics"], "softRouting": {**scored, "ragContextApplied": bool(rag_context), "metricSample": {key: metric.get(key) for key in CORE_FIELDS}}, "agentDiagnosis": {"mainDiagnosis": reason, "missingFields": scored["missingFields"], "evidenceCompleteness": scored["evidenceCompleteness"]}, "ragContextApplied": bool(rag_context)}


def _generic_title(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or text in GENERIC_TITLES or text.startswith("经营任务")


def _judge(bundle: Dict[str, Any], rag_context: Dict[str, Any]) -> Dict[str, Any]:
    fallback = _fallback_judgment(bundle, rag_context)
    try:
        llm_result = generate_json(
            prompt_name="v1483_streaming_product_agent",
            payload={"fullProductBundle": bundle, "ragContext": rag_context, "fallbackDecision": fallback, "hardRule": "LLM只能丰富fallback，不能把observe_only升级为正式任务；后台观察不进入任务池；无正式任务仍要写任务生成运行快照。"},
            expected_keys=["decision", "confidence", "reason", "taskPlan", "operationBudget", "softRouting"],
            agent_name="V14.8.3 Streaming Product Agent",
            schema_name="v1483_streaming_product_agent",
        )
        output = llm_result.get("output") or {}
    except Exception as exc:
        llm_result = {"status": "fallback", "error": str(exc)}
        output = {}
    decision = str(output.get("decision") or fallback["decision"])
    if fallback["decision"] not in FORMAL_DECISIONS and decision in FORMAL_DECISIONS:
        decision = fallback["decision"]
    if fallback["decision"] in FORMAL_DECISIONS and decision not in FORMAL_DECISIONS:
        decision = fallback["decision"]
    merged = {**fallback, **{key: value for key, value in output.items() if value is not None}, "decision": decision}
    if not isinstance(merged.get("taskPlan"), dict):
        merged["taskPlan"] = fallback["taskPlan"]
    if _generic_title((merged.get("taskPlan") or {}).get("title")) and not _generic_title((fallback.get("taskPlan") or {}).get("title")):
        merged["taskPlan"] = {**(merged.get("taskPlan") or {}), **fallback["taskPlan"]}
        merged["reason"] = fallback.get("reason")
        merged["operationBudget"] = fallback.get("operationBudget")
    merged["taskPlan"].setdefault("operationBudget", merged.get("operationBudget") or fallback.get("operationBudget"))
    merged["llm"] = {"provider": llm_result.get("provider"), "model": llm_result.get("model"), "status": llm_result.get("status"), "fallbackUsed": llm_result.get("fallbackUsed"), "error": llm_result.get("error")}
    return merged


def _save_judgment(signal: Dict[str, Any], rag_context: Dict[str, Any], judgment: Dict[str, Any]) -> Dict[str, Any]:
    ensure_agent_judgment_tables()
    judgment_id = make_judgment_id()
    decision = str(judgment.get("decision") or "observe_only")
    status = "pending_task_snapshot" if decision in FORMAL_DECISIONS else "judgment_recorded"
    created_at = now_iso()
    payload = {"version": AGENT_JUDGMENT_STATION_V1481_VERSION, "judgmentId": judgment_id, "stationId": "agent_judgment_station", "dataVersion": signal.get("dataVersion"), "signalId": signal.get("signalId"), "bundleId": signal.get("bundleId"), "entityType": signal.get("entityType"), "entityId": signal.get("entityId"), "signal": signal, "fullProductBundle": signal, "ragContextRef": rag_context.get("ragContextRef") or rag_context.get("outputRef"), "ragContext": rag_context, "decision": decision, "confidence": judgment.get("confidence") or 0, "reason": judgment.get("reason"), "taskPlan": judgment.get("taskPlan") or {}, "operationBudget": judgment.get("operationBudget") or {}, "evidenceRequirements": judgment.get("evidenceRequirements") or [], "reviewMetrics": judgment.get("reviewMetrics") or [], "softRouting": judgment.get("softRouting") or {}, "agentDiagnosis": judgment.get("agentDiagnosis") or {}, "agentJudgment": {**judgment, "decision": decision}, "rule": "V14.8.3 only deterministic mature/data-gap routes enter task pool; all runs write task_generation_run."}
    with connect() as conn:
        conn.execute("""
            INSERT INTO agent_judgments_v14 (judgment_id, data_version, signal_id, entity_type, entity_id, decision, confidence, status, rag_context_ref, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (judgment_id, signal.get("dataVersion"), signal.get("signalId"), signal.get("entityType"), signal.get("entityId"), decision, float(judgment.get("confidence") or 0), status, payload["ragContextRef"], dumps(payload), created_at, created_at))
        conn.commit()
        row = conn.execute("SELECT * FROM agent_judgments_v14 WHERE judgment_id = ?", (judgment_id,)).fetchone()
    update_signal_status(signal.get("signalId"), "judged_pending_snapshot" if decision in FORMAL_DECISIONS else "observed_only", {"agentJudgmentId": judgment_id, "decision": decision, "softRouting": judgment.get("softRouting")})
    return _row_to_judgment(row)


def _stream_to_task_pool(judgment: Dict[str, Any], *, created_by: str | None = None) -> Dict[str, Any]:
    decision = str(judgment.get("decision") or "observe_only")
    if decision not in FORMAL_DECISIONS:
        return {"ok": False, "skipped": True, "reason": "decision_not_formal_task", "decision": decision, "judgmentId": judgment.get("judgmentId")}
    plan = judgment.get("taskPlan") or {}
    if plan.get("deadline") == "后台观察" or plan.get("taskType") == "observe_only":
        return {"ok": False, "skipped": True, "reason": "backend_observation_not_task_pool", "decision": decision, "judgmentId": judgment.get("judgmentId")}
    full_bundle = judgment.get("fullProductBundle") or judgment.get("signal") or {}
    snapshot = create_task_snapshot({"dataVersion": judgment.get("dataVersion"), "decision": decision, "confidence": judgment.get("confidence"), "entityType": judgment.get("entityType"), "entityId": judgment.get("entityId"), "productId": plan.get("productId") or full_bundle.get("productId"), "storeId": plan.get("storeId") or full_bundle.get("storeId"), "signalRef": judgment.get("signalId"), "bundleRef": judgment.get("bundleId"), "ragContext": judgment.get("ragContext") or {}, "agentJudgment": judgment.get("agentJudgment") or {}, "taskPlan": plan, "operationBudget": judgment.get("operationBudget") or plan.get("operationBudget") or {}, "evidenceRequirements": judgment.get("evidenceRequirements") or plan.get("evidenceRequirements") or [], "systemFacts": {"fullProductBundle": full_bundle, "judgmentId": judgment.get("judgmentId"), "softRouting": judgment.get("softRouting") or {}, "agentDiagnosis": judgment.get("agentDiagnosis") or {}, "operationBudget": judgment.get("operationBudget") or {}}, "source": "agent_judgment_station_v1483"}, created_by=created_by)
    pool = enter_task_pool_from_snapshot(str(snapshot.get("taskSnapshotId")), created_by=created_by, force=False)
    with connect() as conn:
        payload = dict(judgment)
        payload["taskSnapshotId"] = snapshot.get("taskSnapshotId")
        payload["streamedTaskPool"] = pool
        conn.execute("UPDATE agent_judgments_v14 SET status = ?, payload = ?, updated_at = ? WHERE judgment_id = ?", ("task_snapshot_created", dumps(payload), now_iso(), judgment.get("judgmentId")))
        conn.commit()
    update_signal_status(judgment.get("signalId"), "task_snapshot_created", {"taskSnapshotId": snapshot.get("taskSnapshotId"), "streamedTaskPool": pool})
    return {"ok": True, "snapshot": snapshot, "poolResult": pool, "createdTaskCount": int((pool or {}).get("createdTaskCount") or 0)}


def run_agent_judgment_station_v1481(data_version: str | None = None, *, rag_context_ref: str | None = None, max_signals: int = 32, created_by: str | None = None, stream_to_pool: bool = True) -> Dict[str, Any]:
    ensure_agent_judgment_tables()
    rag_context = latest_rag_context(data_version) or build_rag_context_snapshot(data_version=data_version)
    signals = (list_signals(data_version=data_version, status="pending_rag_agent", limit=max_signals).get("signals") or [])[:max_signals]
    judgments: List[Dict[str, Any]] = []
    streamed: List[Dict[str, Any]] = []
    idempotent = 0
    for signal in signals:
        existing = _existing_completed(signal.get("signalId"))
        if existing:
            judgments.append({**existing, "idempotentHit": True})
            idempotent += 1
            continue
        saved = _save_judgment(signal, rag_context, _judge(signal, rag_context))
        judgments.append(saved)
        if stream_to_pool and saved.get("decision") in FORMAL_DECISIONS:
            streamed.append(_stream_to_task_pool(saved, created_by=created_by))
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
    streamed_snapshots = sum(1 for item in streamed if item.get("ok"))
    skipped = [item for item in streamed if item.get("skipped")]
    zero_task_reasons = [item.get("reason") for item in judgments if item.get("decision") not in FORMAL_DECISIONS][:20]
    task_pool_created_count = sum(int(item.get("createdTaskCount") or 0) for item in streamed)
    generation_run = record_task_generation_run(data_version=data_version, input_bundle_count=len(signals), agent_judgment_count=len(judgments), by_decision=dict(by_decision), streamed_task_snapshot_count=streamed_snapshots, task_pool_created_count=task_pool_created_count, skipped_formal_count=len(skipped), zero_task_reasons=zero_task_reasons, source="agent_judgment_station_v1483")
    return {"version": AGENT_JUDGMENT_STATION_V1481_VERSION, "mode": "v1483_full_product_bundle_streaming_agent_chain_contract", "dataVersion": data_version, "ragContextRef": rag_context_ref or rag_context.get("ragContextRef") or rag_context.get("outputRef"), "agentJudgmentRef": ref, "outputRef": ref, "signalCount": len(signals), "judgmentCount": len(judgments), "idempotentJudgmentCount": idempotent, "pendingTaskSnapshotCount": 0, "streamedTaskSnapshotCount": streamed_snapshots, "streamedTaskPoolCount": task_pool_created_count, "skippedFormalCount": len(skipped), "byDecision": dict(by_decision), "zeroTaskReasons": zero_task_reasons, "taskGenerationRun": generation_run, "judgments": judgments, "streamed": streamed, "rule": "V14.8.3: task generation run is always recorded; observe-only/background observation never enters task pool; zero formal task is a completed business result."}
