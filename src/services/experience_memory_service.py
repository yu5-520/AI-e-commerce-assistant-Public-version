"""RAG-style operation experience memory service.

Demo/MVP keeps a structured RAG baseline in SQLite. Formal vector RAG should
index and rerank these cards instead of replacing the baseline knowledge layer.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.account_service import current_user, user_display
from src.services.demo_rag_seed_data import DEMO_RAG_MIN_SEED_COUNT, DEMO_RAG_SEED_VERSION, category_profiles, seed_cards
from src.services.module_task_service import find_task

MEMORY_VERSION = "10.11.0"
APPROVED_STATUSES = {"approved", "seed_approved"}
VISIBLE_STATUSES = {"pending_review", "approved", "seed_approved", "rejected"}
SEED_CATEGORY_PROFILES: List[Dict[str, Any]] = category_profiles()
SEED_PLAYBOOKS: List[Dict[str, Any]] = seed_cards()


def now_iso() -> str:
    return datetime.now().isoformat()


def make_id(prefix: str = "CASE") -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"


def ensure_memory_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_experience_cards (
                case_id TEXT PRIMARY KEY,
                case_type TEXT NOT NULL,
                level TEXT NOT NULL,
                status TEXT NOT NULL,
                category_id TEXT,
                platform TEXT,
                store_id TEXT,
                problem_type TEXT,
                operator_style TEXT,
                quality_score REAL,
                effective INTEGER,
                source_task_id TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_cases_problem ON rag_experience_cards(problem_type, category_id, platform)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_cases_status ON rag_experience_cards(status, level, quality_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_cases_type ON rag_experience_cards(case_type, problem_type)")
        conn.commit()


def _row_to_case(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    payload.update(
        {
            "caseId": row["case_id"],
            "caseType": row["case_type"],
            "level": row["level"],
            "status": row["status"],
            "categoryId": row["category_id"],
            "platform": row["platform"],
            "storeId": row["store_id"],
            "problemType": row["problem_type"],
            "operatorStyle": row["operator_style"],
            "qualityScore": float(row["quality_score"] or 0),
            "effective": bool(row["effective"]),
            "sourceTaskId": row["source_task_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
    )
    return payload


def _protect_approved_case(existing: Dict[str, Any], incoming: Dict[str, Any], now: str) -> Dict[str, Any]:
    protected = deepcopy(existing)
    history = list(protected.get("feedbackDraftHistory") or [])
    history.append(
        {
            "draftAt": now,
            "sourceTaskId": incoming.get("sourceTaskId"),
            "draftStatus": incoming.get("status"),
            "draftLevel": incoming.get("level"),
            "draftQualityScore": incoming.get("qualityScore"),
            "draft": incoming,
            "rule": "approved_case_protected_from_auto_draft_overwrite",
        }
    )
    protected["latestFeedbackDraft"] = incoming
    protected["feedbackDraftHistory"] = history[-10:]
    protected["protectedApprovedCase"] = True
    protected["protectionRule"] = "已批准经验卡不能被自动回流草案降级；如需修改，必须走人工复核状态变更。"
    protected["updatedAt"] = now
    return protected


def upsert_case(card: Dict[str, Any]) -> Dict[str, Any]:
    ensure_memory_tables()
    item = deepcopy(card)
    allow_status_overwrite = bool(item.pop("_allowStatusOverwrite", False))
    case_id = item.get("caseId") or make_id("CASE")
    item["caseId"] = case_id
    item.setdefault("caseType", "operation_solution")
    item.setdefault("level", "L1")
    item.setdefault("status", "pending_review")
    item.setdefault("categoryId", "home_living_goods")
    item.setdefault("platform", "通用")
    item.setdefault("storeId", "global")
    item.setdefault("problemType", "general_operation")
    item.setdefault("operatorStyle", "待判断")
    item.setdefault("qualityScore", 0.5)
    item.setdefault("effective", False)
    item.setdefault("sourceTaskId", None)
    now = now_iso()
    item.setdefault("createdAt", now)
    item["updatedAt"] = now
    with connect() as conn:
        existing_row = conn.execute("SELECT * FROM rag_experience_cards WHERE case_id = ?", (case_id,)).fetchone()
        if existing_row:
            existing = _row_to_case(existing_row)
            if existing.get("status") in APPROVED_STATUSES and item.get("status") not in APPROVED_STATUSES and not allow_status_overwrite:
                item = _protect_approved_case(existing, item, now)
        conn.execute(
            """
            INSERT INTO rag_experience_cards(case_id, case_type, level, status, category_id, platform, store_id, problem_type, operator_style, quality_score, effective, source_task_id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
                case_type=excluded.case_type,
                level=excluded.level,
                status=excluded.status,
                category_id=excluded.category_id,
                platform=excluded.platform,
                store_id=excluded.store_id,
                problem_type=excluded.problem_type,
                operator_style=excluded.operator_style,
                quality_score=excluded.quality_score,
                effective=excluded.effective,
                source_task_id=excluded.source_task_id,
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (
                case_id,
                item["caseType"],
                item["level"],
                item["status"],
                item["categoryId"],
                item["platform"],
                item["storeId"],
                item["problemType"],
                item["operatorStyle"],
                float(item["qualityScore"] or 0),
                1 if item.get("effective") else 0,
                item.get("sourceTaskId"),
                dumps(item),
                item["createdAt"],
                item["updatedAt"],
            ),
        )
        conn.commit()
    return item


def seed_memory_if_empty() -> None:
    """Ensure the Demo/MVP RAG baseline exists even when an old DB already has a few cards."""
    ensure_memory_tables()
    seeds = seed_cards()
    seed_ids = [card["caseId"] for card in seeds]
    if not seed_ids:
        return
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM rag_experience_cards WHERE case_id IN ({','.join(['?'] * len(seed_ids))})",
            tuple(seed_ids),
        ).fetchall()
    existing = {row["case_id"]: _row_to_case(row) for row in rows}
    for card in seeds:
        current = existing.get(card["caseId"])
        if not current or current.get("status") == "seed_approved":
            upsert_case(card)


def list_category_profiles() -> List[Dict[str, Any]]:
    return category_profiles()


def list_cases(status: str | None = None, level: str | None = None, limit: int = 50) -> List[Dict[str, Any]]:
    seed_memory_if_empty()
    query = "SELECT * FROM rag_experience_cards"
    params: List[Any] = []
    clauses: List[str] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if level:
        clauses.append("level = ?")
        params.append(level)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY quality_score DESC, updated_at DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [_row_to_case(row) for row in rows]


def _text_blob(card: Dict[str, Any]) -> str:
    values: List[str] = []
    for key in ["title", "initialJudgment", "resultSummary", "problemType", "operatorStyle", "platform", "categoryId", "caseType"]:
        if card.get(key):
            values.append(str(card[key]))
    for key in ["effectiveActions", "applicableConditions", "notApplicableConditions", "judgmentTags", "evidenceRequired", "crossValidationRules"]:
        values.extend(str(item) for item in card.get(key) or [])
    return " ".join(values).lower()


def _score_case(card: Dict[str, Any], filters: Dict[str, Any], query: str | None = None) -> float:
    score = float(card.get("qualityScore") or 0)
    if card.get("status") in APPROVED_STATUSES:
        score += 0.2
    if card.get("caseType") == "cross_validation_rule":
        score += 0.12
    if card.get("caseType") == "acceptance_rule":
        score += 0.08
    if filters.get("categoryId") and card.get("categoryId") == filters["categoryId"]:
        score += 0.25
    if filters.get("problemType") and card.get("problemType") == filters["problemType"]:
        score += 0.35
    if filters.get("platform") and card.get("platform") in {filters["platform"], "通用"}:
        score += 0.15
    if filters.get("storeId") and card.get("storeId") in {filters["storeId"], "global"}:
        score += 0.1
    if filters.get("operatorStyle") and card.get("operatorStyle") == filters["operatorStyle"]:
        score += 0.15
    if query:
        blob = _text_blob(card)
        tokens = [token.lower() for token in str(query).replace("/", " ").replace("，", " ").split() if token]
        score += min(0.36, 0.06 * sum(1 for token in tokens if token in blob))
    return round(score, 4)


def search_cases(
    *,
    query: str | None = None,
    category_id: str | None = None,
    platform: str | None = None,
    store_id: str | None = None,
    problem_type: str | None = None,
    operator_style: str | None = None,
    effective_only: bool = True,
    min_quality: float = 0.0,
    limit: int = 5,
) -> Dict[str, Any]:
    cases = list_cases(limit=500)
    filters = {"categoryId": category_id, "platform": platform, "storeId": store_id, "problemType": problem_type, "operatorStyle": operator_style}
    result: List[Dict[str, Any]] = []
    for card in cases:
        if card.get("status") not in APPROVED_STATUSES:
            continue
        if effective_only and not card.get("effective"):
            continue
        if min_quality and float(card.get("qualityScore") or 0) < min_quality:
            continue
        if category_id and card.get("categoryId") not in {category_id, "global"}:
            continue
        if problem_type and card.get("problemType") != problem_type:
            continue
        if platform and card.get("platform") not in {platform, "通用"}:
            continue
        if store_id and card.get("storeId") not in {store_id, "global"}:
            continue
        item = deepcopy(card)
        item["retrievalScore"] = _score_case(item, filters, query)
        result.append(item)
    result.sort(key=lambda item: item.get("retrievalScore", 0), reverse=True)
    return {
        "version": MEMORY_VERSION,
        "seedVersion": DEMO_RAG_SEED_VERSION,
        "query": query,
        "filters": {key: value for key, value in filters.items() if value},
        "items": result[:limit],
        "totalMatched": len(result),
        "retrievalRule": "结构化标签过滤 + 质量分 + 关键词召回 + 交叉验证规则加权。当前为 Demo/MVP RAG 基线，正式版可升级为向量混合召回。",
        "upgradePath": "vector_index + metadata_filter + quality_rerank + feedback_success_rate_rerank",
    }


def infer_problem_type_from_task(task: Dict[str, Any]) -> str:
    text = " ".join(str(value) for value in [task.get("riskDomain"), task.get("taskType"), task.get("taskSignal"), task.get("task"), task.get("reason"), *(task.get("judgmentTags") or [])] if value)
    if any(word in text for word in ["库存", "补货", "活动"]):
        return "low_inventory_activity"
    if any(word in text for word in ["点击", "CTR", "主图", "标题"]):
        return "low_ctr_low_conversion"
    if any(word in text for word in ["转化率", "详情页", "承接"]):
        return "detail_page_conversion"
    if any(word in text for word in ["ROI", "ROAS", "退款", "售后", "低"]):
        return "low_roi_high_refund"
    if any(word in text for word in ["竞品", "差评"]):
        return "competitor_signal_to_test"
    if any(word in text for word in ["上新", "新品", "测款"]):
        return "listing_test_path"
    if any(word in text for word in ["报表", "字段", "同步", "ERP", "CRM"]):
        return "report_data_anomaly"
    return "general_operation"


def _metric_changed(before_metrics: Dict[str, Any], after_metrics: Dict[str, Any]) -> bool:
    return bool(before_metrics and after_metrics and set(before_metrics) & set(after_metrics))


def _quality_score(*, task: Dict[str, Any], operator_submission: str, manager_review: str, before_metrics: Dict[str, Any], after_metrics: Dict[str, Any]) -> float:
    score = 0.35
    if operator_submission and len(operator_submission) >= 10:
        score += 0.15
    if manager_review and len(manager_review) >= 8:
        score += 0.18
    if _metric_changed(before_metrics, after_metrics):
        score += 0.18
    if task.get("status") in {"已完成", "已通过", "已写入复盘"} or task.get("workflowStatus") in {"已归档", "已通过"}:
        score += 0.08
    if task.get("riskDomain"):
        score += 0.04
    return min(0.96, round(score, 2))


def build_experience_card_from_task(
    task_id: str,
    *,
    operator_submission: str = "",
    manager_review: str = "",
    before_metrics: Dict[str, Any] | None = None,
    after_metrics: Dict[str, Any] | None = None,
    user_id: str | None = None,
) -> Dict[str, Any] | None:
    task = find_task(task_id)
    if not task:
        return None
    before_metrics = before_metrics or {}
    after_metrics = after_metrics or {}
    problem_type = infer_problem_type_from_task(task)
    score = _quality_score(task=task, operator_submission=operator_submission, manager_review=manager_review, before_metrics=before_metrics, after_metrics=after_metrics)
    actor = current_user(user_id)
    level = "L3" if score >= 0.85 and manager_review and _metric_changed(before_metrics, after_metrics) else "L2" if score >= 0.7 and manager_review else "L1"
    source_store_ids = task.get("storeIds") or task.get("visibleStoreIds") or ["global"]
    return {
        "caseId": f"CASE-{task_id}",
        "caseType": "operation_solution",
        "level": level,
        "status": "pending_review",
        "categoryId": task.get("categoryId") or "home_living_goods",
        "platform": task.get("platform") or "通用",
        "storeId": source_store_ids[0] if source_store_ids else "global",
        "problemType": problem_type,
        "operatorStyle": "稳健型" if problem_type in {"low_roi_high_refund", "low_inventory_activity"} else "测试型",
        "title": f"{task.get('productShort') or task.get('title') or task_id}处理经验",
        "initialJudgment": task.get("reason") or task.get("task") or "任务处理经验待补充。",
        "effectiveActions": [value for value in [operator_submission or task.get("task"), task.get("submissionNote"), task.get("reviewNote")] if value],
        "applicableConditions": ["同类目", "同平台或通用平台", "问题指标相近", "人工复核通过后优先复用"],
        "notApplicableConditions": ["指标组合不同", "目标从稳健切换为清库存", "没有结果指标支撑"],
        "resultSummary": manager_review or "待总管复核后确认是否可复用。",
        "beforeMetrics": before_metrics,
        "afterMetrics": after_metrics,
        "qualityScore": score,
        "effective": score >= 0.7 and bool(manager_review),
        "reviewStatus": "manager_review_pending" if not manager_review else "manager_reviewed",
        "sourceTaskId": task_id,
        "sourceTaskTitle": task.get("title"),
        "sourceTaskStatus": task.get("status"),
        "sourceReportIds": [value for value in [task.get("reportId"), task.get("alertId")] if value],
        "createdByUserId": actor.get("id"),
        "createdByName": user_display(actor.get("id"), "系统"),
        "validUntil": (datetime.now() + timedelta(days=90)).date().isoformat(),
        "lastUsedAt": None,
        "successRateAfterReuse": None,
        "writeRule": "只有复核通过、动作清楚、结果指标明确的经验才进入正式 RAG 召回。",
    }


def draft_experience_from_task(task_id: str, *, operator_submission: str = "", manager_review: str = "", before_metrics: Dict[str, Any] | None = None, after_metrics: Dict[str, Any] | None = None, user_id: str | None = None) -> Dict[str, Any] | None:
    card = build_experience_card_from_task(task_id, operator_submission=operator_submission, manager_review=manager_review, before_metrics=before_metrics, after_metrics=after_metrics, user_id=user_id)
    if not card:
        return None
    saved = upsert_case(card)
    return {"version": MEMORY_VERSION, "shouldWriteToRag": bool(saved.get("effective") and saved.get("qualityScore", 0) >= 0.7), "needsHumanReviewBeforeWrite": True, "experienceCard": saved, "ragWriteTarget": "historical_cases" if saved.get("effective") else "pending_experience", "qualityGate": {"managerReviewRequired": True, "metricChangeRequiredForL3": True, "minQualityScore": 0.7, "currentQualityScore": saved.get("qualityScore")}, "protection": "approved_case_preserved" if saved.get("protectedApprovedCase") else "pending_review_draft"}


def update_case_status(case_id: str, *, status: str, reviewer_id: str | None = None, reason: str = "") -> Dict[str, Any] | None:
    seed_memory_if_empty()
    case = next((item for item in list_cases(limit=800) if item.get("caseId") == case_id), None)
    if not case:
        return None
    reviewer = current_user(reviewer_id)
    case.update({"status": status, "reviewerId": reviewer.get("id"), "reviewerName": reviewer.get("name"), "reviewReason": reason, "_allowStatusOverwrite": True})
    if status == "approved":
        case["effective"] = bool(case.get("qualityScore", 0) >= 0.7)
        case["level"] = "L3" if case.get("qualityScore", 0) >= 0.85 else "L2" if case.get("qualityScore", 0) >= 0.7 else case.get("level", "L1")
    elif status == "rejected":
        case["effective"] = False
        case["level"] = "L0"
    return upsert_case(case)


def approve_case(case_id: str, *, reviewer_id: str | None = None, reason: str = "") -> Dict[str, Any] | None:
    return update_case_status(case_id, status="approved", reviewer_id=reviewer_id, reason=reason)


def reject_case(case_id: str, *, reviewer_id: str | None = None, reason: str = "") -> Dict[str, Any] | None:
    return update_case_status(case_id, status="rejected", reviewer_id=reviewer_id, reason=reason)


def memory_summary() -> Dict[str, Any]:
    seed_memory_if_empty()
    cases = list_cases(limit=800)
    cross_rules = [item for item in cases if item.get("caseType") == "cross_validation_rule"]
    acceptance_rules = [item for item in cases if item.get("caseType") == "acceptance_rule"]
    return {
        "version": MEMORY_VERSION,
        "seedVersion": DEMO_RAG_SEED_VERSION,
        "memoryMode": "structured_experience_cards_with_demo_baseline",
        "total": len(cases),
        "approved": len([item for item in cases if item.get("status") in APPROVED_STATUSES]),
        "pendingReview": len([item for item in cases if item.get("status") == "pending_review"]),
        "negativeCases": len([item for item in cases if item.get("caseType") == "negative_case" or item.get("level") == "L4"]),
        "crossValidationRules": len(cross_rules),
        "acceptanceRules": len(acceptance_rules),
        "baselineSeedCount": len([item for item in cases if item.get("seedVersion") == DEMO_RAG_SEED_VERSION]),
        "baselineSeedMinimum": DEMO_RAG_MIN_SEED_COUNT,
        "categoryProfiles": list_category_profiles(),
        "levels": {"L0": "原始日志 / 拒绝入库", "L1": "经验草案", "L2": "已复核经验", "L3": "高质量经验", "L4": "失败案例 / 避坑边界"},
        "demoRagPrinciple": "Demo/MVP 阶段必须有结构化 RAG 经验库；正式上线只是升级为向量混合召回，不是从 0 建库。",
        "writeBoundary": "日报、周报和任务日志必须先提炼为经验卡，复核后才能进入正式 RAG 召回。",
        "protectionRule": "已批准经验卡不会被自动 feedbackDraft 降级覆盖。",
        "vectorUpgradePath": "SQLite structured cards -> vector index -> metadata filter -> quality rerank -> success-rate rerank。",
    }
