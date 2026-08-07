"""V21 action authority matrix.

The Agent chain decides what to do and the concrete parameters.  This module
only decides who may execute the action.  High risk controls validation depth;
it never implies manager approval by itself.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List

from fastapi import HTTPException

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.competition_operator_context_service import (
    COMPETITION_OPERATOR_ID,
    competition_operator,
)

ACTION_AUTHORITY_VERSION = "21.0"
ROAS_FAMILIES = {"roas_scale", "roas_guard"}
AUTO_EXECUTE = "auto_execute"
MANAGER_APPROVAL = "manager_approval_required"
OWNER_APPROVAL = "owner_approval_required"
AUTHORIZATION_DATA_MISSING = "authorization_data_missing"


def assignment_for_store(store_id: str | None) -> Dict[str, Any]:
    """Return the server-owned competition operator binding for action safety."""
    return {
        "storeId": store_id,
        "primaryOperatorId": COMPETITION_OPERATOR_ID,
        "source": "competition_fixed_operator_context",
    }


def current_user(_: str | None = None) -> Dict[str, Any]:
    """Compatibility view over the fixed runtime actor; no client identity is read."""
    return competition_operator()


def user_raw(_: str | None = None) -> Dict[str, Any]:
    return competition_operator()

DEFAULT_OPERATOR_AUTHORITY = {
    "authorityLevel": "operator_l2",
    "singleAdjustmentLimit": 8000.0,
    "dailyAdjustmentLimit": 12000.0,
    "rolling24hLimit": 15000.0,
    "roasChangeRateLimit": 0.12,
    "minimumTargetRoas": 1.60,
    "ownerApprovalLimit": 30000.0,
    "enabled": True,
}

DEFAULT_STORE_POLICY = {
    "storeWeight": "middle",
    "storeStability": "stable",
    "budgetLimitMultiplier": 1.0,
    "roasChangeMultiplier": 1.0,
    "ownerApprovalMultiplier": 1.0,
    "enabled": True,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any) -> float | None:
    if value in {None, "", "—", "未识别", "UNKNOWN"}:
        return None
    try:
        return float(str(value).replace("¥", "").replace("￥", "").replace(",", "").replace("元", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _first_number(sources: Iterable[Dict[str, Any]], keys: Iterable[str]) -> float | None:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = _float(source.get(key))
            if value is not None:
                return value
    return None


def ensure_action_authority_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_action_authority (
                user_id TEXT NOT NULL,
                action_family TEXT NOT NULL,
                authority_level TEXT NOT NULL,
                single_adjustment_limit REAL NOT NULL,
                daily_adjustment_limit REAL NOT NULL,
                rolling_24h_limit REAL NOT NULL,
                roas_change_rate_limit REAL NOT NULL,
                minimum_target_roas REAL NOT NULL,
                owner_approval_limit REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_by TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, action_family)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS store_action_policy (
                store_id TEXT PRIMARY KEY,
                store_weight TEXT NOT NULL,
                store_stability TEXT NOT NULL,
                budget_limit_multiplier REAL NOT NULL,
                roas_change_multiplier REAL NOT NULL,
                owner_approval_multiplier REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_by TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_authority_usage (
                usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                store_id TEXT,
                product_id TEXT,
                action_family TEXT NOT NULL,
                task_id TEXT,
                adjustment_amount REAL NOT NULL DEFAULT 0,
                current_budget REAL,
                target_budget REAL,
                current_roas REAL,
                target_roas REAL,
                status TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_authority_decisions (
                decision_id TEXT PRIMARY KEY,
                task_id TEXT,
                data_version TEXT,
                user_id TEXT,
                store_id TEXT,
                product_id TEXT,
                action_family TEXT,
                decision TEXT NOT NULL,
                approval_required INTEGER NOT NULL,
                reason TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_authority_usage_user_time ON action_authority_usage(user_id, occurred_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_authority_usage_store_time ON action_authority_usage(store_id, occurred_at)")
        conn.commit()


def _row_dict(row: Any) -> Dict[str, Any]:
    return dict(row) if row else {}


def get_operator_authority(user_id: str, action_family: str = "roas_scale") -> Dict[str, Any]:
    ensure_action_authority_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM operator_action_authority WHERE user_id=? AND action_family=?",
            (user_id, action_family),
        ).fetchone()
    if not row and action_family == "roas_guard":
        return get_operator_authority(user_id, "roas_scale")
    if not row:
        return {"userId": user_id, "actionFamily": action_family, **DEFAULT_OPERATOR_AUTHORITY, "source": "v21_default"}
    item = _row_dict(row)
    return {
        "userId": item.get("user_id"),
        "actionFamily": item.get("action_family"),
        "authorityLevel": item.get("authority_level"),
        "singleAdjustmentLimit": float(item.get("single_adjustment_limit") or 0),
        "dailyAdjustmentLimit": float(item.get("daily_adjustment_limit") or 0),
        "rolling24hLimit": float(item.get("rolling_24h_limit") or 0),
        "roasChangeRateLimit": float(item.get("roas_change_rate_limit") or 0),
        "minimumTargetRoas": float(item.get("minimum_target_roas") or 0),
        "ownerApprovalLimit": float(item.get("owner_approval_limit") or 0),
        "enabled": bool(item.get("enabled")),
        "updatedBy": item.get("updated_by"),
        "updatedAt": item.get("updated_at"),
        "source": "operator_action_authority",
    }


def update_operator_authority(user_id: str, action_family: str, body: Dict[str, Any], *, updated_by: str) -> Dict[str, Any]:
    ensure_action_authority_tables()
    current = get_operator_authority(user_id, action_family)
    values = {
        "authorityLevel": str(body.get("authorityLevel") or current.get("authorityLevel") or "operator_l2"),
        "singleAdjustmentLimit": max(0.0, _float(body.get("singleAdjustmentLimit")) if body.get("singleAdjustmentLimit") is not None else float(current.get("singleAdjustmentLimit") or 0)),
        "dailyAdjustmentLimit": max(0.0, _float(body.get("dailyAdjustmentLimit")) if body.get("dailyAdjustmentLimit") is not None else float(current.get("dailyAdjustmentLimit") or 0)),
        "rolling24hLimit": max(0.0, _float(body.get("rolling24hLimit")) if body.get("rolling24hLimit") is not None else float(current.get("rolling24hLimit") or 0)),
        "roasChangeRateLimit": max(0.0, _float(body.get("roasChangeRateLimit")) if body.get("roasChangeRateLimit") is not None else float(current.get("roasChangeRateLimit") or 0)),
        "minimumTargetRoas": max(0.0, _float(body.get("minimumTargetRoas")) if body.get("minimumTargetRoas") is not None else float(current.get("minimumTargetRoas") or 0)),
        "ownerApprovalLimit": max(0.0, _float(body.get("ownerApprovalLimit")) if body.get("ownerApprovalLimit") is not None else float(current.get("ownerApprovalLimit") or 0)),
        "enabled": bool(body.get("enabled", current.get("enabled", True))),
    }
    if values["dailyAdjustmentLimit"] < values["singleAdjustmentLimit"]:
        raise HTTPException(status_code=400, detail="dailyAdjustmentLimit cannot be lower than singleAdjustmentLimit")
    if values["rolling24hLimit"] < values["dailyAdjustmentLimit"]:
        raise HTTPException(status_code=400, detail="rolling24hLimit cannot be lower than dailyAdjustmentLimit")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO operator_action_authority (
                user_id, action_family, authority_level, single_adjustment_limit,
                daily_adjustment_limit, rolling_24h_limit, roas_change_rate_limit,
                minimum_target_roas, owner_approval_limit, enabled, updated_by, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, action_family) DO UPDATE SET
                authority_level=excluded.authority_level,
                single_adjustment_limit=excluded.single_adjustment_limit,
                daily_adjustment_limit=excluded.daily_adjustment_limit,
                rolling_24h_limit=excluded.rolling_24h_limit,
                roas_change_rate_limit=excluded.roas_change_rate_limit,
                minimum_target_roas=excluded.minimum_target_roas,
                owner_approval_limit=excluded.owner_approval_limit,
                enabled=excluded.enabled,
                updated_by=excluded.updated_by,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                action_family,
                values["authorityLevel"],
                values["singleAdjustmentLimit"],
                values["dailyAdjustmentLimit"],
                values["rolling24hLimit"],
                values["roasChangeRateLimit"],
                values["minimumTargetRoas"],
                values["ownerApprovalLimit"],
                1 if values["enabled"] else 0,
                updated_by,
                now_iso(),
            ),
        )
        conn.commit()
    return get_operator_authority(user_id, action_family)


def get_store_policy(store_id: str) -> Dict[str, Any]:
    ensure_action_authority_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM store_action_policy WHERE store_id=?", (store_id,)).fetchone()
    if not row:
        return {"storeId": store_id, **DEFAULT_STORE_POLICY, "source": "v21_default"}
    item = _row_dict(row)
    return {
        "storeId": item.get("store_id"),
        "storeWeight": item.get("store_weight"),
        "storeStability": item.get("store_stability"),
        "budgetLimitMultiplier": float(item.get("budget_limit_multiplier") or 0),
        "roasChangeMultiplier": float(item.get("roas_change_multiplier") or 0),
        "ownerApprovalMultiplier": float(item.get("owner_approval_multiplier") or 0),
        "enabled": bool(item.get("enabled")),
        "updatedBy": item.get("updated_by"),
        "updatedAt": item.get("updated_at"),
        "source": "store_action_policy",
    }


def update_store_policy(store_id: str, body: Dict[str, Any], *, updated_by: str) -> Dict[str, Any]:
    ensure_action_authority_tables()
    current = get_store_policy(store_id)
    weight = str(body.get("storeWeight") or current.get("storeWeight") or "middle")
    stability = str(body.get("storeStability") or current.get("storeStability") or "stable")
    budget_multiplier = _float(body.get("budgetLimitMultiplier")) if body.get("budgetLimitMultiplier") is not None else float(current.get("budgetLimitMultiplier") or 1)
    roas_multiplier = _float(body.get("roasChangeMultiplier")) if body.get("roasChangeMultiplier") is not None else float(current.get("roasChangeMultiplier") or 1)
    owner_multiplier = _float(body.get("ownerApprovalMultiplier")) if body.get("ownerApprovalMultiplier") is not None else float(current.get("ownerApprovalMultiplier") or 1)
    if min(budget_multiplier or 0, roas_multiplier or 0, owner_multiplier or 0) <= 0:
        raise HTTPException(status_code=400, detail="authority multipliers must be greater than zero")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO store_action_policy (
                store_id, store_weight, store_stability, budget_limit_multiplier,
                roas_change_multiplier, owner_approval_multiplier, enabled, updated_by, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_id) DO UPDATE SET
                store_weight=excluded.store_weight,
                store_stability=excluded.store_stability,
                budget_limit_multiplier=excluded.budget_limit_multiplier,
                roas_change_multiplier=excluded.roas_change_multiplier,
                owner_approval_multiplier=excluded.owner_approval_multiplier,
                enabled=excluded.enabled,
                updated_by=excluded.updated_by,
                updated_at=excluded.updated_at
            """,
            (
                store_id,
                weight,
                stability,
                float(budget_multiplier),
                float(roas_multiplier),
                float(owner_multiplier),
                1 if bool(body.get("enabled", current.get("enabled", True))) else 0,
                updated_by,
                now_iso(),
            ),
        )
        conn.commit()
    return get_store_policy(store_id)


def _usage(user_id: str, store_id: str | None, action_family: str) -> Dict[str, float]:
    ensure_action_authority_tables()
    now = datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
    rolling_start = (now - timedelta(hours=24)).isoformat()
    with connect() as conn:
        day = conn.execute(
            """SELECT COALESCE(SUM(ABS(adjustment_amount)),0) AS value
               FROM action_authority_usage
               WHERE user_id=? AND action_family=? AND status IN ('authorized','executed') AND occurred_at>=?""",
            (user_id, action_family, day_start),
        ).fetchone()
        rolling = conn.execute(
            """SELECT COALESCE(SUM(ABS(adjustment_amount)),0) AS value
               FROM action_authority_usage
               WHERE user_id=? AND action_family=? AND status IN ('authorized','executed') AND occurred_at>=?""",
            (user_id, action_family, rolling_start),
        ).fetchone()
        store_day = conn.execute(
            """SELECT COALESCE(SUM(ABS(adjustment_amount)),0) AS value
               FROM action_authority_usage
               WHERE user_id=? AND COALESCE(store_id,'')=COALESCE(?,'') AND action_family=?
                 AND status IN ('authorized','executed') AND occurred_at>=?""",
            (user_id, store_id, action_family, day_start),
        ).fetchone()
    return {
        "usedToday": float((day or {"value": 0})["value"] or 0),
        "usedRolling24h": float((rolling or {"value": 0})["value"] or 0),
        "usedStoreToday": float((store_day or {"value": 0})["value"] or 0),
    }


def _identity(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = _dict(decision.get("taskPlan"))
    product = _dict(plan.get("productIdentity"))
    package = _dict(decision.get("productJudgmentPackage"))
    return {
        "productId": decision.get("productId") or plan.get("productId") or product.get("productId") or package.get("productId"),
        "storeId": decision.get("storeId") or plan.get("storeId") or product.get("storeId") or package.get("storeId"),
    }


def _operator_for_store(store_id: str | None, plan: Dict[str, Any]) -> str | None:
    explicit = plan.get("assignedOperatorId") or plan.get("operatorId")
    if explicit:
        return str(explicit)
    assignment = assignment_for_store(store_id) if store_id else None
    return str((assignment or {}).get("primaryOperatorId") or "") or None


def _roas_parameters(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = _dict(decision.get("taskPlan"))
    agent2 = _dict(decision.get("agent2ActionPlan") or plan.get("agent2ActionPlan"))
    budget = _dict(plan.get("budgetPlan")) or _dict(agent2.get("budgetPlan"))
    execution = _dict(agent2.get("executionParameters"))
    action_pack = _dict(plan.get("actionParameterPack"))
    execution_object = _dict(agent2.get("executionObject") or plan.get("executionObject"))
    current_value = _dict(execution_object.get("currentValue"))
    target_value = _dict(execution_object.get("targetValue"))
    sources = [budget, execution, action_pack, current_value, target_value, agent2, plan]

    current_budget = _first_number(sources, ["currentDailyBudget", "currentBudget", "beforeBudget", "dailyBudget", "currentAdSpend"])
    target_budget = _first_number(sources, ["targetDailyBudget", "targetBudget", "afterBudget", "recommendedBudgetUpperBound"])
    amount = _first_number(sources, ["adjustmentAmount", "budgetAdjustmentAmount", "requestedBudgetAdjustment", "increaseAmount", "decreaseAmount"])
    rate = _first_number(sources, ["adjustmentRate", "budgetAdjustmentRate", "recommendedBudgetIncreaseRate", "recommendedBudgetDecreaseRate"])
    if rate is not None and abs(rate) > 2:
        rate = rate / 100
    if amount is None and current_budget is not None and target_budget is not None:
        amount = abs(target_budget - current_budget)
    if amount is None and current_budget is not None and rate is not None:
        amount = abs(current_budget * rate)
    if target_budget is None and current_budget is not None and amount is not None:
        direction = -1 if str(plan.get("selectedActionFamily")) == "roas_guard" else 1
        target_budget = max(0.0, current_budget + direction * amount)

    current_roas = _first_number(sources, ["currentTargetROAS", "currentTargetRoas", "currentROI", "currentRoas", "currentROAS"])
    target_roas = _first_number(sources, ["targetROAS", "targetRoas", "targetROI", "targetRoi"])
    safety_roas = _first_number(sources, ["minimumSafeROAS", "minimumSafeRoas", "safetyROI", "stopLossROI", "stopLossROAS", "breakEvenROI"])
    roas_change_rate = None
    if current_roas not in {None, 0} and target_roas is not None:
        roas_change_rate = abs(target_roas - current_roas) / abs(current_roas)

    return {
        "currentBudget": current_budget,
        "targetBudget": target_budget,
        "adjustmentAmount": abs(amount) if amount is not None else None,
        "adjustmentRate": abs(rate) if rate is not None else None,
        "currentTargetRoas": current_roas,
        "targetRoas": target_roas,
        "safetyRoas": safety_roas,
        "roasChangeRate": roas_change_rate,
    }


def authorize_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = _dict(decision.get("taskPlan"))
    family = str(plan.get("selectedActionFamily") or decision.get("actionFamily") or "").strip()
    identity = _identity(decision)
    store_id = str(identity.get("storeId") or "") or None
    product_id = str(identity.get("productId") or "") or None
    operator_id = _operator_for_store(store_id, plan)

    # Non-ROAS actions keep their upstream governance result in V21.  Only ROAS
    # is migrated from action-family gating to numeric authority gating here.
    if family not in ROAS_FAMILIES:
        existing = str(decision.get("decision") or "create_task_snapshot")
        approval_required = existing == "manager_review_required" or bool(plan.get("approvalRequired"))
        mapped = MANAGER_APPROVAL if approval_required else AUTO_EXECUTE
        return {
            "version": ACTION_AUTHORITY_VERSION,
            "mode": "v21_non_roas_passthrough",
            "decision": mapped,
            "lifecycleDecision": "manager_review_required" if approval_required else "create_task_snapshot",
            "approvalRequired": approval_required,
            "requiredAuthorityLevel": "existing_policy",
            "operatorId": operator_id,
            "storeId": store_id,
            "productId": product_id,
            "actionFamily": family,
            "reason": "非ROAS动作沿用现有审批边界；V21本轮只迁移ROAS金额权限。",
            "triggeredReasons": [],
            "highRiskValidation": family in {"platform_activity"},
        }

    if not operator_id:
        return {
            "version": ACTION_AUTHORITY_VERSION,
            "mode": "v21_roas_numeric_authority",
            "decision": AUTHORIZATION_DATA_MISSING,
            "lifecycleDecision": None,
            "approvalRequired": False,
            "requiredAuthorityLevel": "operator_assignment_required",
            "operatorId": None,
            "storeId": store_id,
            "productId": product_id,
            "actionFamily": family,
            "reason": "店铺没有绑定主运营，无法计算ROAS执行权限。",
            "missing": ["assignedOperatorId"],
            "highRiskValidation": True,
        }

    authority = get_operator_authority(operator_id, family)
    store_policy = get_store_policy(store_id or "GLOBAL")
    params = _roas_parameters(decision)
    missing = [key for key in ("adjustmentAmount",) if params.get(key) is None]
    if missing:
        return {
            "version": ACTION_AUTHORITY_VERSION,
            "mode": "v21_roas_numeric_authority",
            "decision": AUTHORIZATION_DATA_MISSING,
            "lifecycleDecision": None,
            "approvalRequired": False,
            "requiredAuthorityLevel": authority.get("authorityLevel"),
            "operatorId": operator_id,
            "storeId": store_id,
            "productId": product_id,
            "actionFamily": family,
            "reason": "ROAS任务缺少明确的本次预算调整金额，禁止按动作族猜测审批归属。",
            "missing": missing,
            "parameters": params,
            "authority": authority,
            "storePolicy": store_policy,
            "highRiskValidation": True,
        }

    usage = _usage(operator_id, store_id, family)
    budget_multiplier = float(store_policy.get("budgetLimitMultiplier") or 1)
    roas_multiplier = float(store_policy.get("roasChangeMultiplier") or 1)
    owner_multiplier = float(store_policy.get("ownerApprovalMultiplier") or 1)
    single_limit = float(authority.get("singleAdjustmentLimit") or 0) * budget_multiplier
    daily_limit = float(authority.get("dailyAdjustmentLimit") or 0) * budget_multiplier
    rolling_limit = float(authority.get("rolling24hLimit") or 0) * budget_multiplier
    owner_limit = float(authority.get("ownerApprovalLimit") or 0) * owner_multiplier
    roas_change_limit = float(authority.get("roasChangeRateLimit") or 0) * roas_multiplier
    minimum_roas = max(float(authority.get("minimumTargetRoas") or 0), float(params.get("safetyRoas") or 0))
    amount = float(params.get("adjustmentAmount") or 0)

    reasons: List[str] = []
    owner_reasons: List[str] = []
    if not authority.get("enabled") or not store_policy.get("enabled"):
        reasons.append("authority_disabled")
    if amount > single_limit:
        reasons.append("single_adjustment_limit_exceeded")
    if usage["usedToday"] + amount > daily_limit:
        reasons.append("daily_adjustment_limit_exceeded")
    if usage["usedRolling24h"] + amount > rolling_limit:
        reasons.append("rolling_24h_limit_exceeded")
    if params.get("roasChangeRate") is not None and float(params["roasChangeRate"]) > roas_change_limit:
        reasons.append("roas_change_rate_limit_exceeded")
    if params.get("targetRoas") is not None and float(params["targetRoas"]) < minimum_roas:
        owner_reasons.append("target_roas_below_safety_floor")
    if owner_limit and amount > owner_limit:
        owner_reasons.append("owner_approval_amount_exceeded")

    if owner_reasons:
        auth_decision = OWNER_APPROVAL
        lifecycle_decision = "manager_review_required"
        reason = "ROAS方案触碰老板级金额或安全线边界，需要高级确认。"
    elif reasons:
        auth_decision = MANAGER_APPROVAL
        lifecycle_decision = "manager_review_required"
        reason = "本次ROAS调整超出运营有效额度或累计额度，需要总管审批。"
    else:
        auth_decision = AUTO_EXECUTE
        lifecycle_decision = "create_task_snapshot"
        reason = "本次ROAS金额、累计额度、目标ROAS和店铺策略均在运营有效权限内，自动派发并接收。"

    return {
        "version": ACTION_AUTHORITY_VERSION,
        "mode": "v21_roas_numeric_authority",
        "decision": auth_decision,
        "lifecycleDecision": lifecycle_decision,
        "approvalRequired": auth_decision != AUTO_EXECUTE,
        "requiredAuthorityLevel": authority.get("authorityLevel"),
        "operatorId": operator_id,
        "storeId": store_id,
        "productId": product_id,
        "actionFamily": family,
        "reason": reason,
        "triggeredReasons": [*owner_reasons, *reasons],
        "parameters": params,
        "authority": authority,
        "storePolicy": store_policy,
        "usage": usage,
        "effectiveLimits": {
            "singleAdjustmentLimit": single_limit,
            "dailyAdjustmentLimit": daily_limit,
            "rolling24hLimit": rolling_limit,
            "ownerApprovalLimit": owner_limit,
            "roasChangeRateLimit": roas_change_limit,
            "minimumTargetRoas": minimum_roas,
            "remainingToday": max(0.0, daily_limit - usage["usedToday"]),
            "remainingRolling24h": max(0.0, rolling_limit - usage["usedRolling24h"]),
        },
        "highRiskValidation": True,
        "rule": "V21：高风险决定校验强度，结构化金额与权限矩阵决定任务归属。",
    }


def apply_authorization_to_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    next_decision = deepcopy(decision)
    authorization = authorize_decision(next_decision)
    if authorization.get("decision") == AUTHORIZATION_DATA_MISSING:
        next_decision["authorizationDecision"] = authorization
        return next_decision

    plan = dict(_dict(next_decision.get("taskPlan")))
    approval_required = bool(authorization.get("approvalRequired"))
    plan.update(
        {
            "approvalRequired": approval_required,
            "needManagerReview": approval_required,
            "assignedOperatorId": authorization.get("operatorId") if not approval_required else None,
            "authorizationDecision": authorization,
            "actionAuthorization": authorization,
            "authorizationVersion": ACTION_AUTHORITY_VERSION,
        }
    )
    next_decision.update(
        {
            "decision": authorization.get("lifecycleDecision"),
            "authorizationDecision": authorization,
            "actionAuthorization": authorization,
            "authorizationVersion": ACTION_AUTHORITY_VERSION,
            "taskPlan": plan,
        }
    )
    return next_decision


def record_authorization_decision(decision: Dict[str, Any], *, task_id: str | None = None) -> None:
    authorization = _dict(decision.get("authorizationDecision"))
    if not authorization:
        return
    ensure_action_authority_tables()
    decision_id = str(decision.get("decisionId") or f"AUTH-{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO action_authority_decisions (
                decision_id, task_id, data_version, user_id, store_id, product_id,
                action_family, decision, approval_required, reason, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                task_id,
                decision.get("dataVersion"),
                authorization.get("operatorId"),
                authorization.get("storeId"),
                authorization.get("productId"),
                authorization.get("actionFamily"),
                authorization.get("decision"),
                1 if authorization.get("approvalRequired") else 0,
                authorization.get("reason"),
                dumps(authorization),
                now_iso(),
            ),
        )
        conn.commit()


def record_authorized_usage(task: Dict[str, Any], *, status: str = "authorized") -> None:
    authorization = _dict(task.get("authorizationDecision") or task.get("actionAuthorization"))
    params = _dict(authorization.get("parameters"))
    amount = _float(params.get("adjustmentAmount"))
    if authorization.get("decision") != AUTO_EXECUTE or amount is None:
        return
    ensure_action_authority_tables()
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM action_authority_usage WHERE task_id=? AND status IN ('authorized','executed') LIMIT 1",
            (task.get("taskId") or task.get("id"),),
        ).fetchone()
        if exists:
            return
        conn.execute(
            """
            INSERT INTO action_authority_usage (
                user_id, store_id, product_id, action_family, task_id,
                adjustment_amount, current_budget, target_budget,
                current_roas, target_roas, status, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                authorization.get("operatorId"),
                authorization.get("storeId"),
                authorization.get("productId"),
                authorization.get("actionFamily"),
                task.get("taskId") or task.get("id"),
                amount,
                params.get("currentBudget"),
                params.get("targetBudget"),
                params.get("currentTargetRoas"),
                params.get("targetRoas"),
                status,
                now_iso(),
            ),
        )
        conn.commit()


def _require_authority_manager(user_id: str | None) -> Dict[str, Any]:
    user = current_user(user_id)
    if user.get("roleId") not in {"owner", "manager"}:
        raise HTTPException(status_code=403, detail="Only owner or manager may manage action authority")
    return user


def authority_summary(viewer_id: str | None = None) -> Dict[str, Any]:
    viewer = current_user(viewer_id)
    ensure_action_authority_tables()
    with connect() as conn:
        decision_rows = conn.execute(
            "SELECT decision, COUNT(*) AS count FROM action_authority_decisions GROUP BY decision"
        ).fetchall()
        usage_rows = conn.execute(
            "SELECT user_id, COALESCE(SUM(ABS(adjustment_amount)),0) AS amount FROM action_authority_usage WHERE occurred_at>=? GROUP BY user_id",
            ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),),
        ).fetchall()
    return {
        "version": ACTION_AUTHORITY_VERSION,
        "viewer": {"id": viewer.get("id"), "roleId": viewer.get("roleId")},
        "decisions": {row["decision"]: int(row["count"] or 0) for row in decision_rows},
        "rolling24hUsage": [{"userId": row["user_id"], "amount": float(row["amount"] or 0)} for row in usage_rows],
        "defaultOperatorAuthority": DEFAULT_OPERATOR_AUTHORITY,
        "defaultStorePolicy": DEFAULT_STORE_POLICY,
        "rule": "V21高风险动作先完成结构化校验，再按人员额度、店铺策略、累计用量和ROAS安全线决定任务归属。",
    }


def list_operator_authorities(viewer_id: str | None = None) -> List[Dict[str, Any]]:
    _require_authority_manager(viewer_id)
    ensure_action_authority_tables()
    with connect() as conn:
        rows = conn.execute("SELECT user_id, action_family FROM operator_action_authority ORDER BY user_id, action_family").fetchall()
    return [get_operator_authority(row["user_id"], row["action_family"]) for row in rows]


def list_store_policies(viewer_id: str | None = None) -> List[Dict[str, Any]]:
    _require_authority_manager(viewer_id)
    ensure_action_authority_tables()
    with connect() as conn:
        rows = conn.execute("SELECT store_id FROM store_action_policy ORDER BY store_id").fetchall()
    return [get_store_policy(row["store_id"]) for row in rows]


def decide_task_authorization(task_id: str, body: Dict[str, Any], *, actor_user_id: str | None) -> Dict[str, Any]:
    actor = _require_authority_manager(actor_user_id)
    action = str(body.get("decision") or "").strip()
    if action not in {"approve_as_is", "approve_modified", "reject", "regenerate"}:
        raise HTTPException(status_code=400, detail="Invalid authorization decision")
    ensure_action_authority_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM task_status WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        payload = loads(row["payload"]) if row["payload"] else {}
        payload = payload if isinstance(payload, dict) else {}
        authorization = _dict(payload.get("authorizationDecision") or payload.get("actionAuthorization"))
        plan = _dict(payload.get("taskPlan"))
        budget = _dict(plan.get("budgetPlan"))
        if action == "approve_modified":
            if body.get("approvedAdjustmentAmount") is not None:
                budget["approvedAdjustmentAmount"] = _float(body.get("approvedAdjustmentAmount"))
            if body.get("approvedTargetROAS") is not None:
                budget["approvedTargetROAS"] = _float(body.get("approvedTargetROAS"))
            plan["budgetPlan"] = budget
        if action in {"approve_as_is", "approve_modified"}:
            assignee = body.get("assigneeId") or authorization.get("operatorId") or payload.get("assigneeId")
            authorization.update({
                "decision": AUTO_EXECUTE,
                "approvalRequired": False,
                "approvedBy": actor.get("id"),
                "approvedAt": now_iso(),
                "approvalMode": action,
                "approvalNote": body.get("note"),
            })
            payload.update({
                "authorizationDecision": authorization,
                "actionAuthorization": authorization,
                "taskPlan": {**plan, "approvalRequired": False, "needManagerReview": False, "assignedOperatorId": assignee},
                "taskLayer": "operator_execution",
                "assigneeId": assignee,
                "status": "处理中",
                "workflowStatus": "处理中",
                "displayStatus": "处理中",
                "autoAcceptedBy": "system_after_manager_authorization",
                "autoAcceptedAt": now_iso(),
                "visibleTaskActions": [{"action": "submit", "label": "提交", "primary": True}, {"action": "detail", "label": "详情"}],
            })
            approval_status = "approved"
            auto_execution_allowed = 1
            status = "处理中"
        elif action == "reject":
            payload.update({"status": "已拒绝", "workflowStatus": "已拒绝", "displayStatus": "已拒绝", "authorizationRejectedBy": actor.get("id"), "authorizationRejectedAt": now_iso(), "authorizationNote": body.get("note")})
            approval_status = "rejected"
            auto_execution_allowed = 0
            status = "已拒绝"
        else:
            payload.update({"status": "待重新生成", "workflowStatus": "待重新生成", "displayStatus": "待重新生成", "authorizationRegenerateBy": actor.get("id"), "authorizationRegenerateAt": now_iso(), "authorizationNote": body.get("note")})
            approval_status = "regenerate"
            auto_execution_allowed = 0
            status = "待重新生成"
        conn.execute(
            """UPDATE task_status SET approval_status=?, status=?, workflow_status=?, assignee_id=?, auto_execution_allowed=?, payload=?, updated_at=? WHERE task_id=?""",
            (approval_status, status, status, payload.get("assigneeId"), auto_execution_allowed, dumps(payload), now_iso(), task_id),
        )
        conn.commit()
    if action in {"approve_as_is", "approve_modified"}:
        record_authorized_usage(payload)
    return {"version": ACTION_AUTHORITY_VERSION, "taskId": task_id, "decision": action, "task": payload, "actorUserId": actor.get("id")}
