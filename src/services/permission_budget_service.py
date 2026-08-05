"""V6.5 permission budget and approval-chain service.

High-risk tasks that passed V6.4 trend gates still cannot execute directly.
V6.5 decides what the current role can apply for, who must approve the action,
and whether the task is execution, application, or approval-only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads

PERMISSION_BUDGET_VERSION = "6.5.0"

DEFAULT_LIMITS: List[Dict[str, Any]] = [
    {
        "roleId": "operator",
        "roleName": "运营",
        "maxAdBudgetApply": 3000,
        "maxStockPurchaseApply": 5000,
        "maxCrossStoreAction": 0,
        "approvalAbove": 0,
        "approvalChain": ["manager"],
        "rule": "运营只能提交额度内申请，不能直接审批高风险投产。",
    },
    {
        "roleId": "manager",
        "roleName": "店群总管",
        "maxAdBudgetApply": 20000,
        "maxStockPurchaseApply": 50000,
        "maxCrossStoreAction": 3,
        "approvalAbove": 20000,
        "approvalChain": ["owner"],
        "rule": "总管可审批中等额度，超额进入老板审批。",
    },
    {
        "roleId": "finance",
        "roleName": "财务",
        "maxAdBudgetApply": 15000,
        "maxStockPurchaseApply": 30000,
        "maxCrossStoreAction": 0,
        "approvalAbove": 15000,
        "approvalChain": ["owner"],
        "rule": "财务侧重利润和预算复核。",
    },
    {
        "roleId": "owner",
        "roleName": "老板",
        "maxAdBudgetApply": 100000,
        "maxStockPurchaseApply": 200000,
        "maxCrossStoreAction": 99,
        "approvalAbove": 100000,
        "approvalChain": [],
        "rule": "老板可复核并审批高额度资源配置。",
    },
]

ROLE_ORDER = ["operator", "manager", "finance", "owner"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}".upper()


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "").replace("¥", "").strip())
    except (TypeError, ValueError):
        return default


def ensure_permission_budget_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS permission_budget_limits_v6 (
                role_id TEXT PRIMARY KEY,
                role_name TEXT,
                max_ad_budget_apply REAL DEFAULT 0,
                max_stock_purchase_apply REAL DEFAULT 0,
                max_cross_store_action INTEGER DEFAULT 0,
                approval_above REAL DEFAULT 0,
                approval_chain TEXT,
                rule TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS permission_budget_checks_v6 (
                check_id TEXT PRIMARY KEY,
                product_id TEXT,
                store_id TEXT,
                data_version TEXT,
                risk_level TEXT,
                role_id TEXT,
                status TEXT NOT NULL,
                suggested_ad_budget REAL DEFAULT 0,
                suggested_stock_budget REAL DEFAULT 0,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        now = now_iso()
        for item in DEFAULT_LIMITS:
            conn.execute(
                """
                INSERT OR IGNORE INTO permission_budget_limits_v6 (
                    role_id, role_name, max_ad_budget_apply, max_stock_purchase_apply,
                    max_cross_store_action, approval_above, approval_chain, rule, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["roleId"], item["roleName"], item["maxAdBudgetApply"], item["maxStockPurchaseApply"],
                    item["maxCrossStoreAction"], item["approvalAbove"], dumps(item["approvalChain"]), item["rule"], now, now,
                ),
            )
        conn.commit()


def _load_limit(role_id: str) -> Dict[str, Any]:
    ensure_permission_budget_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM permission_budget_limits_v6 WHERE role_id = ?", (role_id,)).fetchone()
    if not row:
        return _load_limit("operator")
    return {
        "roleId": row["role_id"],
        "roleName": row["role_name"],
        "maxAdBudgetApply": _as_float(row["max_ad_budget_apply"]),
        "maxStockPurchaseApply": _as_float(row["max_stock_purchase_apply"]),
        "maxCrossStoreAction": int(row["max_cross_store_action"] or 0),
        "approvalAbove": _as_float(row["approval_above"]),
        "approvalChain": loads(row["approval_chain"]),
        "rule": row["rule"],
    }


def _suggest_amounts(constraints: Dict[str, Any], high_risk_gate: Dict[str, Any] | None) -> Dict[str, float]:
    targets = constraints.get("targets") or {}
    current = constraints.get("currentMetrics") or {}
    safety_stock = _as_float(targets.get("safetyStock"), 0)
    current_stock = _as_float(targets.get("currentStock") or current.get("stock") or current.get("available_stock"), 0)
    cost = _as_float(current.get("cost_price"), 20)
    ad_floor = 3000 if high_risk_gate and high_risk_gate.get("applicationAllowed") else 0
    min_roi = _as_float(targets.get("minRoi"), 1.6)
    stock_gap = max(0.0, safety_stock - current_stock)
    stock_budget = round(stock_gap * max(cost, 1.0), 2)
    ad_budget = round(max(ad_floor, min_roi * 1200), 2) if ad_floor else 0
    return {"suggestedAdBudget": ad_budget, "suggestedStockBudget": stock_budget}


def resolve_permission_budget_gate(
    *,
    product: Dict[str, Any],
    risk_level: str,
    domain: str,
    constraints: Dict[str, Any],
    high_risk_gate: Dict[str, Any] | None = None,
    requester_role_id: str = "operator",
    data_version: str | None = None,
) -> Dict[str, Any]:
    """Resolve role quota and approval chain for a risk task."""
    ensure_permission_budget_tables()
    limit = _load_limit(requester_role_id)
    amounts = _suggest_amounts(constraints, high_risk_gate)
    suggested_ad = amounts["suggestedAdBudget"]
    suggested_stock = amounts["suggestedStockBudget"]
    total = suggested_ad + suggested_stock
    high_risk = risk_level == "高"
    application_allowed = bool(high_risk_gate and high_risk_gate.get("applicationAllowed")) if high_risk else False
    within_ad = suggested_ad <= limit["maxAdBudgetApply"]
    within_stock = suggested_stock <= limit["maxStockPurchaseApply"]
    within_role = within_ad and within_stock
    needs_approval = bool(high_risk or total > limit["approvalAbove"] or not within_role)
    if not high_risk:
        status = "execution_allowed" if not needs_approval else "needs_approval"
    elif application_allowed and within_role:
        status = "application_allowed"
    elif application_allowed and not within_role:
        status = "application_requires_escalation"
    else:
        status = "review_only"
    approval_chain = list(limit.get("approvalChain") or [])
    if high_risk and "manager" not in approval_chain and requester_role_id == "operator":
        approval_chain.insert(0, "manager")
    if (not within_role or total > limit["approvalAbove"]) and "owner" not in approval_chain and requester_role_id != "owner":
        approval_chain.append("owner")
    result = {
        "version": PERMISSION_BUDGET_VERSION,
        "checkId": make_id("PBUDGET"),
        "productId": product.get("productId"),
        "storeId": product.get("storeId"),
        "dataVersion": data_version,
        "requesterRoleId": requester_role_id,
        "riskLevel": risk_level,
        "domain": domain,
        "status": status,
        "roleLimit": limit,
        "suggestedAdBudget": suggested_ad,
        "suggestedStockBudget": suggested_stock,
        "suggestedTotalBudget": round(total, 2),
        "withinRoleLimit": within_role,
        "needsApproval": needs_approval,
        "approvalChain": approval_chain,
        "applicationAllowedByTrendGate": application_allowed,
        "executionAllowed": status == "execution_allowed",
        "rule": "V6.5 只决定可申请额度和审批链路；高风险即使通过门控，也不能自动执行。",
        "createdAt": now_iso(),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO permission_budget_checks_v6 (
                check_id, product_id, store_id, data_version, risk_level, role_id, status,
                suggested_ad_budget, suggested_stock_budget, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["checkId"], result["productId"], result["storeId"], data_version, risk_level, requester_role_id,
                status, suggested_ad, suggested_stock, dumps(result), result["createdAt"],
            ),
        )
        conn.commit()
    return result


def permission_budget_summary(limit: int = 30) -> Dict[str, Any]:
    ensure_permission_budget_tables()
    with connect() as conn:
        limits = conn.execute("SELECT * FROM permission_budget_limits_v6 ORDER BY max_ad_budget_apply ASC").fetchall()
        checks = conn.execute("SELECT * FROM permission_budget_checks_v6 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    limit_items = [
        {
            "roleId": row["role_id"],
            "roleName": row["role_name"],
            "maxAdBudgetApply": _as_float(row["max_ad_budget_apply"]),
            "maxStockPurchaseApply": _as_float(row["max_stock_purchase_apply"]),
            "maxCrossStoreAction": int(row["max_cross_store_action"] or 0),
            "approvalAbove": _as_float(row["approval_above"]),
            "approvalChain": loads(row["approval_chain"]),
            "rule": row["rule"],
        }
        for row in limits
    ]
    check_items = [loads(row["payload"]) for row in checks]
    by_status: Dict[str, int] = defaultdict(int)
    for item in check_items:
        by_status[str(item.get("status") or "unknown")] += 1
    return {
        "version": PERMISSION_BUDGET_VERSION,
        "limitCount": len(limit_items),
        "limits": limit_items,
        "checkCount": len(check_items),
        "byStatus": dict(by_status),
        "latestChecks": check_items,
    }
