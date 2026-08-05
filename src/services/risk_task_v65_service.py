"""V11.8 risk task service: ownership-first SOP task packages.

The old rule chain may still read historical tasks, but new executable tasks must
be generated from operating objects + evidence + SOP package + ownership.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services import module_task_service
from src.services.high_risk_trend_gate_service import evaluate_high_risk_trend_gate
from src.services.indicator_rag_service import resolve_indicator_constraints
from src.services.permission_budget_service import resolve_permission_budget_gate

RISK_TASK_VERSION = "11.8.0"
RISK_RANK = {"高": 1, "中": 2, "低": 3}
POSITIVE_METRICS = {"roi", "traffic", "clicks", "ctr", "conversion_rate", "gross_margin", "sales_volume", "quantity", "revenue", "actual_paid", "good_review_rate"}
BLOCKER_METRICS = {"refund_rate", "refund_amount", "refund_count", "bad_review_rate"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}".upper()


def ensure_risk_task_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_task_plans_v6 (
                plan_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                store_id TEXT,
                data_version TEXT,
                risk_level TEXT NOT NULL,
                task_type TEXT NOT NULL,
                task_id TEXT,
                status TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_risk_task_plans_product_v6 ON risk_task_plans_v6(product_id, store_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_risk_task_plans_version_v6 ON risk_task_plans_v6(data_version, created_at)")
        conn.commit()


def _payload(row: Any) -> Dict[str, Any]:
    data = loads(row["payload"])
    return data if data else dict(row)


def _load_signals(data_version: str | None = None, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_risk_task_tables()
    if data_version:
        query = "SELECT * FROM business_signals_v6 WHERE data_version = ? ORDER BY created_at DESC LIMIT ?"
        params: tuple[Any, ...] = (data_version, limit)
    else:
        query = "SELECT * FROM business_signals_v6 ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_payload(row) for row in rows]


def _product_context(product_id: str, store_id: str | None = None, data_version: str | None = None) -> Dict[str, Any]:
    with connect() as conn:
        if data_version:
            row = conn.execute(
                "SELECT * FROM operating_products WHERE product_id = ? AND latest_data_version = ? ORDER BY updated_at DESC LIMIT 1",
                (product_id, data_version),
            ).fetchone()
        elif store_id:
            row = conn.execute(
                "SELECT * FROM operating_products WHERE product_id = ? AND (normalized_store_id = ? OR store_id = ?) ORDER BY updated_at DESC LIMIT 1",
                (product_id, store_id, store_id),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM operating_products WHERE product_id = ? ORDER BY updated_at DESC LIMIT 1", (product_id,)).fetchone()
        if row:
            data = loads(row["payload"])
            return {
                **data,
                "productId": row["product_id"],
                "storeId": row["normalized_store_id"] or row["store_id"],
                "storeName": row["normalized_store_name"] or row["store_name"],
                "title": row["title"],
                "platform": row["platform"],
                "category": row["category"],
                "assignedOperatorId": row["assigned_operator_id"],
                "ownerUserId": row["owner_user_id"],
                "reviewerId": row["reviewer_id"],
                "visibleUserIds": data.get("visibleUserIds") or [],
                "visibleRoleIds": data.get("visibleRoleIds") or [],
                "dataScopeSource": row["data_scope_source"],
            }
        legacy = conn.execute("SELECT * FROM product_master_v6 WHERE product_id = ? LIMIT 1", (product_id,)).fetchone()
    if legacy:
        data = loads(legacy["payload"])
        return {**data, "productId": product_id, "storeId": store_id or legacy["store_id"], "title": data.get("title") or f"商品 {product_id}"}
    return {"productId": product_id, "storeId": store_id, "title": f"商品 {product_id}", "storeName": store_id or "导入店铺"}


def _dominant_risk(signals: Iterable[Dict[str, Any]]) -> str:
    risks = [str(item.get("riskLevel") or "低") for item in signals]
    return sorted(risks, key=lambda value: RISK_RANK.get(value, 9))[0] if risks else "低"


def _positive_count(signals: Iterable[Dict[str, Any]]) -> int:
    metrics = set()
    for item in signals:
        metric = str(item.get("sourceMetric") or "")
        direction = item.get("trendDirection")
        if metric in POSITIVE_METRICS and direction == "up":
            metrics.add(metric)
        if metric in BLOCKER_METRICS and direction == "down":
            metrics.add(metric)
    return len(metrics)


def _has_blocker(signals: Iterable[Dict[str, Any]]) -> bool:
    for item in signals:
        metric = str(item.get("sourceMetric") or "")
        direction = item.get("trendDirection")
        if metric in BLOCKER_METRICS and direction == "up":
            return True
        if metric in {"roi", "ctr", "conversion_rate", "gross_margin"} and direction == "down":
            return True
    return False


def _domain(signals: List[Dict[str, Any]], opportunity: bool = False) -> str:
    if opportunity:
        return "趋势"
    text = " ".join(f"{item.get('signalType')} {item.get('sourceMetric')}" for item in signals)
    if any(word in text for word in ["库存", "stock", "available_stock"]):
        return "库存"
    if any(word in text for word in ["ROI", "roi", "流量", "click", "ctr", "conversion"]):
        return "流量"
    if any(word in text for word in ["毛利", "gross_margin", "成本", "价格"]):
        return "利润"
    if any(word in text for word in ["售后", "退款", "差评", "refund", "bad_review"]):
        return "售后"
    return "趋势"


def _deadline(risk_level: str, constraints: Dict[str, Any]) -> str:
    if risk_level == "高":
        return "今日内"
    if risk_level == "中":
        return f"{(constraints.get('targets') or {}).get('observeDays') or 3}天内"
    return "后台观察"


def _queue_type(risk_level: str, opportunity: bool) -> str:
    if risk_level == "高":
        return "urgent_execution"
    if risk_level == "中" and opportunity:
        return "today_execution"
    if risk_level == "中":
        return "observe_candidate"
    return "backend_tag"


def _build_actions(risk_level: str, constraints: Dict[str, Any], gate: Dict[str, Any] | None, budget: Dict[str, Any]) -> List[str]:
    targets = constraints.get("targets") or {}
    actions: List[str] = []
    if targets.get("safetyStock") is not None:
        actions.append(f"6小时内核对库存，不得低于 {targets['safetyStock']} 件安全线。")
    if targets.get("minRoi") is not None:
        actions.append(f"6小时内复核 ROI，低于 {targets['minRoi']} 不得继续放量。")
    if targets.get("minCtr") is not None:
        actions.append(f"12小时内复查主图/标题点击率，点击率需保持在 {targets['minCtr'] * 100:.1f}% 以上。")
    if targets.get("minConversionRate") is not None:
        actions.append(f"12小时内复查详情页和客服承接，转化率需保持在 {targets['minConversionRate'] * 100:.1f}% 以上。")
    if targets.get("minGrossMargin") is not None:
        actions.append(f"12小时内核对售价、成本、优惠券，毛利率不得低于 {targets['minGrossMargin'] * 100:.1f}%。")
    if risk_level == "高":
        actions.append("高风险动作只能提交申请或复核结论，不能自动改预算、库存或价格。")
    if budget.get("suggestedTotalBudget"):
        chain = " → ".join(budget.get("approvalChain") or ["无需审批"])
        actions.append(f"建议申请总额度 {budget['suggestedTotalBudget']} 元；审批链路：{chain}。")
    return actions or ["6小时内补充近7日订单、退款、库存和流量数据，完成后重新生成任务。"]


def _task_type(risk_level: str, domain: str, gate: Dict[str, Any] | None) -> str:
    if risk_level == "高":
        return "高风险投产申请任务" if gate and gate.get("applicationAllowed") else "高风险趋势门控复核任务"
    if risk_level == "中":
        return f"中风险{domain}观察复核任务"
    return "低风险标签沉淀"


def _task_payload(product: Dict[str, Any], signals: List[Dict[str, Any]], data_version: str | None, risk_level: str, opportunity: bool, requester_role_id: str) -> Dict[str, Any]:
    domain = _domain(signals, opportunity)
    product_id = product.get("productId") or signals[0].get("productId")
    store_id = product.get("storeId") or signals[0].get("storeId")
    constraints = resolve_indicator_constraints(product, domain, risk_level, signals, data_version=data_version)
    gate = evaluate_high_risk_trend_gate(product, signals, constraints, data_version=data_version) if risk_level == "高" else None
    budget = resolve_permission_budget_gate(product=product, risk_level=risk_level, domain=domain, constraints=constraints, high_risk_gate=gate, requester_role_id=requester_role_id, data_version=data_version)
    application_allowed = bool(gate and gate.get("applicationAllowed"))
    task_type = _task_type(risk_level, domain, gate)
    suffix = "投产申请" if application_allowed else "趋势门控复核" if risk_level == "高" else f"{domain}观察复核" if risk_level == "中" else "标签沉淀"
    actions = _build_actions(risk_level, constraints, gate, budget)
    queue_type = _queue_type(risk_level, opportunity)
    evidence_pack = [{"type": "trend_signal", "title": item.get("signalType"), "metric": item.get("metricLabel") or item.get("sourceMetric"), "reason": item.get("reason"), "dataVersion": item.get("dataVersion")} for item in signals]
    evidence_pack.append({"type": "permission_budget", "title": "账号额度门控", "metric": f"申请额度 {budget.get('suggestedTotalBudget')} 元", "reason": budget.get("rule"), "dataVersion": data_version})
    ownership = {
        "assignedOperatorId": product.get("assignedOperatorId"),
        "ownerUserId": product.get("ownerUserId") or product.get("assignedOperatorId"),
        "reviewerId": product.get("reviewerId"),
        "visibleUserIds": list(dict.fromkeys([item for item in [product.get("assignedOperatorId"), product.get("ownerUserId"), product.get("reviewerId"), *(product.get("visibleUserIds") or [])] if item])),
        "visibleRoleIds": list(dict.fromkeys([*(product.get("visibleRoleIds") or []), "owner", "manager", "operator"])),
        "dataScopeSource": product.get("dataScopeSource") or "uploader_account",
        "rule": "任务继承商品/店铺归属，不能反向制造商品/店铺权限。",
    }
    task_card = {"title": str(product.get("title") or product_id), "subtitle": suffix, "deadline": _deadline(risk_level, constraints), "priority": risk_level, "ownerRole": "运营" if ownership.get("assignedOperatorId") else "总管"}
    detail = {
        "version": RISK_TASK_VERSION,
        "title": f"任务详情报告｜{task_card['title']} · {suffix}",
        "warningSummary": "；".join(item.get("reason") or item.get("signalType") or "趋势信号" for item in signals[:4]),
        "evidencePack": evidence_pack,
        "sopSteps": actions,
        "reviewMetrics": constraints.get("targets") or {},
        "completionGate": ["提交处理截图或数据", "补充复核指标", "总管复核后归档"],
        "failureThreshold": {"riskLevel": risk_level, "queueType": queue_type, "rule": "未达到指标阈值时不得继续扩大预算或库存。"},
        "agentBoundary": "Agent 只生成评估和 SOP，不直接改预算、库存、价格或店铺数据。",
    }
    return {
        "id": make_id("RISK"),
        "taskGenerationMode": "v11_8_sop_package",
        "title": task_card["title"],
        "taskCard": task_card,
        "taskDetailReport": detail,
        "evidencePack": evidence_pack,
        "sopSteps": actions,
        "reviewMetrics": detail["reviewMetrics"],
        "completionGate": detail["completionGate"],
        "failureThreshold": detail["failureThreshold"],
        "task": actions[0] if actions else task_type,
        "taskType": task_type,
        "priority": risk_level,
        "deadline": _deadline(risk_level, constraints),
        "timeBucket": _deadline(risk_level, constraints),
        "urgencyLevel": "urgent" if risk_level == "高" else "today" if queue_type == "today_execution" else "observe",
        "queueType": queue_type,
        "displayState": "expanded" if queue_type in {"urgent_execution", "today_execution"} else "backend_only",
        "source": "趋势中心",
        "sourceModule": "趋势中心",
        "sourceRoute": "trend-center",
        "productId": product_id,
        "entityId": product_id,
        "entityType": "商品",
        "store": product.get("storeName") or store_id or "导入店铺",
        "storeName": product.get("storeName"),
        "storeIds": [store_id] if store_id else [],
        "visibleStoreIds": [store_id] if store_id else [],
        "platform": product.get("platform") or "未知平台",
        "category": product.get("category") or "未分类",
        "riskDomain": domain,
        "actionType": "申请" if application_allowed else "复核" if risk_level == "高" else "观察" if risk_level == "中" else "标签",
        "taskLayer": "manager_dispatch" if risk_level == "高" else "operator_execution",
        "assigneeId": ownership.get("assignedOperatorId") if risk_level != "高" else None,
        "reviewerId": ownership.get("reviewerId"),
        "visibleUserIds": ownership.get("visibleUserIds"),
        "visibleRoleIds": ["owner", "manager", "finance"] if risk_level == "高" else ownership.get("visibleRoleIds"),
        "ownership": ownership,
        "sourceEvent": f"V118:{data_version or 'latest'}:{product_id}:{domain}:{risk_level}:{budget.get('status')}",
        "riskGrade": risk_level,
        "riskPolicy": {"riskMode": budget.get("status"), "requiresRagMetrics": risk_level in {"中", "高"}, "requiresTrendGate": risk_level == "高", "requiresBudgetGate": True, "requiresApproval": budget.get("needsApproval"), "approvalChain": budget.get("approvalChain") or [], "rule": "V11.8 只允许结构化 SOP 任务包进入前端任务队列。"},
        "ragIndicatorConstraints": constraints,
        "highRiskTrendGate": gate,
        "permissionBudgetGate": budget,
        "investmentApplicationAllowed": bool(application_allowed and budget.get("status") in {"application_allowed", "application_requires_escalation"}),
        "executionAllowed": bool(budget.get("executionAllowed")),
        "approvalChain": budget.get("approvalChain") or [],
        "suggestedAdBudget": budget.get("suggestedAdBudget"),
        "suggestedStockBudget": budget.get("suggestedStockBudget"),
        "suggestedTotalBudget": budget.get("suggestedTotalBudget"),
        "executionRequirements": actions,
        "judgmentTags": ["V11.8任务包", f"{risk_level}风险", domain, queue_type, budget.get("status")],
        "evidence": evidence_pack,
        "reason": detail["warningSummary"],
        "agentJudgment": {"status": "v11_8_sop_task_package", "riskLevel": risk_level, "indicatorConstraints": constraints, "highRiskTrendGate": gate, "permissionBudgetGate": budget, "executionRequirements": actions, "ownership": ownership, "boundary": "旧规则不得生成新任务；任务只继承经营对象权限。"},
        "sourceTrail": ["报表中心", "经营对象主档", "趋势中心", "RAG指标门控", "权限归属", "V11.8 SOP任务包"],
    }


def _save_plan(product_id: str, store_id: str | None, data_version: str | None, risk_level: str, task_type: str, task_id: str | None, status: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    ensure_risk_task_tables()
    plan = {"planId": make_id("RPLAN"), "productId": product_id, "StoreId": store_id, "storeId": store_id, "dataVersion": data_version, "riskLevel": risk_level, "taskType": task_type, "taskId": task_id, "status": status, "payload": payload, "createdAt": now_iso()}
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO risk_task_plans_v6 (plan_id, product_id, store_id, data_version, risk_level, task_type, task_id, status, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (plan["planId"], product_id, store_id, data_version, risk_level, task_type, task_id, status, dumps(plan), plan["createdAt"]))
        conn.commit()
    return plan


def generate_risk_tasks_for_signals(data_version: str | None = None, limit: int = 200, requester_role_id: str = "operator") -> Dict[str, Any]:
    ensure_risk_task_tables()
    signals = _load_signals(data_version=data_version, limit=limit)
    groups: Dict[tuple[str, str | None, str | None], List[Dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        product_id = signal.get("productId")
        if product_id:
            groups[(str(product_id), signal.get("storeId"), signal.get("dataVersion") or data_version)].append(signal)
    tasks: List[Dict[str, Any]] = []
    plans: List[Dict[str, Any]] = []
    skipped = 0
    tagged_only = 0
    application_candidates = 0
    for (product_id, store_id, version), items in groups.items():
        dominant = _dominant_risk(items)
        opportunity = _positive_count(items) >= 4 and not _has_blocker(items)
        risk_level = "高" if opportunity else dominant
        candidates = [item for item in items if item.get("taskCandidate")]
        if risk_level == "低":
            tagged_only += 1
            plans.append(_save_plan(product_id, store_id, version, risk_level, "低风险标签沉淀", None, "tagged_only", {"signals": items, "rule": "V11.8低风险不进入前端任务栏，沉淀为商品/店铺标签。"}))
            continue
        if not opportunity and risk_level in {"中", "高"} and not candidates:
            skipped += 1
            plans.append(_save_plan(product_id, store_id, version, risk_level, "观察候选", None, "observation_candidate", {"signals": items, "rule": "缺少任务候选门控，不进入执行队列。"}))
            continue
        payload = _task_payload(_product_context(product_id, store_id, version), items, version, risk_level, opportunity, requester_role_id)
        if payload.get("queueType") in {"backend_tag", "observe_candidate"}:
            tagged_only += 1
            plans.append(_save_plan(product_id, store_id, version, payload["riskGrade"], payload["taskType"], None, payload.get("queueType"), {"taskCandidate": payload, "signals": items}))
            continue
        if opportunity:
            application_candidates += 1
        task = module_task_service.create_task(payload)
        tasks.append(task)
        plans.append(_save_plan(product_id, store_id, version, payload["riskGrade"], payload["taskType"], task.get("id"), "created", {"task": task, "signals": items, "indicatorConstraints": payload.get("ragIndicatorConstraints"), "highRiskTrendGate": payload.get("highRiskTrendGate"), "permissionBudgetGate": payload.get("permissionBudgetGate")}))
    return {"version": RISK_TASK_VERSION, "mode": "v11_8_ownership_first_sop_task_generation", "dataVersion": data_version, "requesterRoleId": requester_role_id, "signalCount": len(signals), "groupCount": len(groups), "createdTaskCount": len(tasks), "taggedOnlyCount": tagged_only, "skippedGroupCount": skipped, "investmentApplicationCandidateCount": application_candidates, "tasks": tasks, "plans": plans, "rule": "上传账号确定经营对象归属；任务继承商品/店铺归属；旧规则不得生成新任务。"}


def risk_task_summary(limit: int = 30) -> Dict[str, Any]:
    ensure_risk_task_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM risk_task_plans_v6 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    plans = [loads(row["payload"]) for row in rows]
    by_level: Dict[str, int] = defaultdict(int)
    rag_matched = gate_passed = application_allowed = budget_checked = tagged_only = 0
    for plan in plans:
        by_level[str(plan.get("riskLevel") or "低")] += 1
        if plan.get("status") in {"tagged_only", "backend_tag"}:
            tagged_only += 1
        task = (plan.get("payload") or {}).get("task") or {}
        if (task.get("ragIndicatorConstraints") or {}).get("status") == "matched":
            rag_matched += 1
        if (task.get("highRiskTrendGate") or {}).get("gateStatus") == "passed":
            gate_passed += 1
        if task.get("investmentApplicationAllowed"):
            application_allowed += 1
        if task.get("permissionBudgetGate"):
            budget_checked += 1
    return {"version": RISK_TASK_VERSION, "total": len(plans), "byLevel": dict(by_level), "taggedOnlyCount": tagged_only, "ragMatchedCount": rag_matched, "highRiskGatePassedCount": gate_passed, "investmentApplicationAllowedCount": application_allowed, "budgetCheckedCount": budget_checked, "latestPlans": plans}
