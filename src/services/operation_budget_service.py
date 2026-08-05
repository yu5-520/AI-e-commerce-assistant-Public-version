"""V14.3 operation budget service.

Budget is not only cash spend. It is the estimated operating impact of a task.
Agent should generate estimated budget cost and SOP. The system validates,
reserves, locks, releases and records budget lifecycle.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Dict

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads

OPERATION_BUDGET_VERSION = "14.3.0"
DEFAULT_OPERATOR_DAILY_BUDGET = {"min": 3000.0, "max": 8000.0, "default": 5000.0}
TASK_BUDGET_RULES = {
    "roas_increase": {"budgetType": "ad_spend_delta", "riskDefault": "medium"},
    "roas_decrease": {"budgetType": "operating_impact", "impactFactor": 0.5, "riskDefault": "medium"},
    "campaign_apply": {"budgetType": "campaign_margin_subsidy", "riskDefault": "medium"},
    "replenishment": {"budgetType": "supply_chain_coordination", "costFactor": 0.03, "riskDefault": "medium"},
    "title_test": {"budgetType": "creative_test", "fixedCost": 200.0, "riskDefault": "low"},
    "main_image_test": {"budgetType": "creative_test", "fixedCost": 400.0, "riskDefault": "low"},
    "detail_page_test": {"budgetType": "conversion_test", "fixedCost": 600.0, "riskDefault": "low"},
    "after_sales_check": {"budgetType": "service_review", "fixedCost": 100.0, "riskDefault": "low"},
    "data_gap_fix": {"budgetType": "data_quality", "fixedCost": 50.0, "riskDefault": "low"},
}


def now_iso() -> str:
    return datetime.now().isoformat()


def ensure_budget_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operation_budget_ledger_v14 (
                ledger_id TEXT PRIMARY KEY,
                task_snapshot_id TEXT,
                task_id TEXT,
                user_id TEXT,
                store_id TEXT,
                product_id TEXT,
                risk_level TEXT,
                budget_type TEXT,
                budget_cost REAL DEFAULT 0,
                status TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(conn, "operation_budget_ledger_v14", {"task_id": "TEXT", "risk_level": "TEXT", "budget_type": "TEXT", "budget_cost": "REAL DEFAULT 0", "updated_at": "TEXT"})
        conn.execute("CREATE INDEX IF NOT EXISTS idx_budget_v14_user_day ON operation_budget_ledger_v14(user_id, created_at, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_budget_v14_task_snapshot ON operation_budget_ledger_v14(task_snapshot_id)")
        conn.commit()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in {None, "", "—", "未识别"}:
            return default
        return float(str(value).replace("¥", "").replace(",", "").replace("%", "").strip())
    except Exception:
        return default


def _ledger_id(seed: str) -> str:
    return "BUD-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:14].upper()


def estimate_operation_budget(task_type: str | None, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    task_type = str(task_type or payload.get("taskType") or payload.get("task_type") or "data_gap_fix")
    rule = TASK_BUDGET_RULES.get(task_type, {"budgetType": "general_operation", "fixedCost": 300.0, "riskDefault": "low"})
    budget_type = str(rule.get("budgetType") or "general_operation")
    formula = "fixed baseline"
    cost = _num(rule.get("fixedCost"), 300.0)

    if task_type == "roas_increase":
        daily_spend = _num(payload.get("currentDailyAdSpend") or payload.get("adSpend") or payload.get("dailyAdSpend"))
        increase_rate = _num(payload.get("increaseRate"), 0.15)
        test_days = _num(payload.get("testDays"), 2.0)
        cost = daily_spend * increase_rate * test_days
        formula = f"currentDailyAdSpend {daily_spend} × increaseRate {increase_rate} × testDays {test_days}"
    elif task_type == "roas_decrease":
        daily_spend = _num(payload.get("currentDailyAdSpend") or payload.get("adSpend") or payload.get("dailyAdSpend"))
        decrease_rate = _num(payload.get("decreaseRate"), 0.2)
        observe_days = _num(payload.get("observeDays"), 2.0)
        impact_factor = _num(rule.get("impactFactor"), 0.5)
        cost = daily_spend * decrease_rate * observe_days * impact_factor
        formula = f"currentDailyAdSpend {daily_spend} × decreaseRate {decrease_rate} × observeDays {observe_days} × impactFactor {impact_factor}"
    elif task_type == "campaign_apply":
        expected_units = _num(payload.get("expectedUnits") or payload.get("expectedSalesUnits"))
        unit_discount = _num(payload.get("unitDiscount"))
        unit_subsidy = _num(payload.get("unitSubsidy"))
        campaign_ad_spend = _num(payload.get("campaignAdSpend"))
        inventory_risk = _num(payload.get("inventoryRiskCost"))
        cost = expected_units * (unit_discount + unit_subsidy) + campaign_ad_spend + inventory_risk
        formula = f"expectedUnits {expected_units} × (unitDiscount {unit_discount} + unitSubsidy {unit_subsidy}) + campaignAdSpend {campaign_ad_spend} + inventoryRiskCost {inventory_risk}"
    elif task_type == "replenishment":
        goods_value = _num(payload.get("suggestedGoodsValue") or payload.get("replenishmentValue") or payload.get("inventoryValue"))
        factor = _num(payload.get("budgetFactor"), _num(rule.get("costFactor"), 0.03))
        cost = goods_value * factor
        formula = f"suggestedGoodsValue {goods_value} × coordinationFactor {factor}"

    if cost <= 0:
        cost = _num(rule.get("fixedCost"), 100.0)
    risk_level = str(payload.get("riskLevel") or rule.get("riskDefault") or "low")
    if risk_level == "high":
        budget_status = "manager_review_not_operator_budget"
    else:
        budget_status = "estimated"
    return {"version": OPERATION_BUDGET_VERSION, "taskType": task_type, "riskLevel": risk_level, "budgetType": budget_type, "estimatedBudgetCost": round(cost, 2), "budgetFormula": formula, "budgetStatus": budget_status, "operatorBudgetApplies": risk_level != "high", "rule": "V14.3 Agent-generated tasks must carry estimated budget cost; high-risk review does not consume ordinary operator budget."}


def daily_budget_usage(user_id: str | None, *, day: str | None = None) -> Dict[str, Any]:
    ensure_budget_tables()
    user_id = user_id or "U001"
    day = day or date.today().isoformat()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM operation_budget_ledger_v14 WHERE user_id = ? AND substr(created_at, 1, 10) = ?", (user_id, day)).fetchall()
    entries = [row_to_ledger(row) for row in rows]
    used = sum(float(item.get("budgetCost") or 0) for item in entries if item.get("status") in {"reserved", "locked", "used"} and item.get("riskLevel") != "high")
    return {"version": OPERATION_BUDGET_VERSION, "userId": user_id, "day": day, "dailyBudget": DEFAULT_OPERATOR_DAILY_BUDGET, "usedBudget": round(used, 2), "remainingDefaultBudget": round(DEFAULT_OPERATOR_DAILY_BUDGET["default"] - used, 2), "entries": entries}


def row_to_ledger(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    return {**payload, "ledgerId": row["ledger_id"], "taskSnapshotId": row["task_snapshot_id"], "taskId": row["task_id"], "userId": row["user_id"], "storeId": row["store_id"], "productId": row["product_id"], "riskLevel": row["risk_level"], "budgetType": row["budget_type"], "budgetCost": float(row["budget_cost"] or 0), "status": row["status"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def reserve_budget_for_task(task_snapshot: Dict[str, Any], *, user_id: str | None = None, status: str = "reserved") -> Dict[str, Any]:
    ensure_budget_tables()
    budget = task_snapshot.get("operationBudget") if isinstance(task_snapshot.get("operationBudget"), dict) else estimate_operation_budget(task_snapshot.get("taskType"), task_snapshot)
    risk_level = str(budget.get("riskLevel") or task_snapshot.get("riskLevel") or "low")
    if risk_level == "high":
        status = "manager_review"
    cost = float(budget.get("estimatedBudgetCost") or 0)
    ledger_id = _ledger_id(f"{task_snapshot.get('taskSnapshotId') or task_snapshot.get('taskId') or now_iso()}|{status}")
    now = now_iso()
    payload = {"version": OPERATION_BUDGET_VERSION, "operationBudget": budget, "taskSnapshot": task_snapshot, "status": status}
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO operation_budget_ledger_v14 (ledger_id, task_snapshot_id, task_id, user_id, store_id, product_id, risk_level, budget_type, budget_cost, status, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM operation_budget_ledger_v14 WHERE ledger_id = ?), ?), ?)
            """,
            (ledger_id, task_snapshot.get("taskSnapshotId"), task_snapshot.get("taskId"), user_id or task_snapshot.get("assigneeId") or task_snapshot.get("createdBy"), task_snapshot.get("storeId"), task_snapshot.get("productId"), risk_level, budget.get("budgetType"), cost, status, dumps(payload), ledger_id, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM operation_budget_ledger_v14 WHERE ledger_id = ?", (ledger_id,)).fetchone()
    return row_to_ledger(row)


def budget_rules_summary() -> Dict[str, Any]:
    return {"version": OPERATION_BUDGET_VERSION, "operatorDailyBudget": DEFAULT_OPERATOR_DAILY_BUDGET, "rules": TASK_BUDGET_RULES, "lifecycle": ["estimated", "reserved", "locked", "used", "released", "adjusted"], "rule": "ROAS increase/decrease, campaign apply, replenishment and creative tests all reserve budget with different formulas."}
