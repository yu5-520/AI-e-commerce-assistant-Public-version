"""Mock account, role, store-scope, responsibility, and migration service."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping

DEFAULT_USER_ID = "U001"
MANAGEMENT_PASSWORD = "admin123"
PERMISSIONS: List[Dict[str, str]] = [
    {"id": "view_all_stores", "name": "查看全部店群"},
    {"id": "view_managed_stores", "name": "查看负责店群"},
    {"id": "view_own_tasks", "name": "查看自己的任务"},
    {"id": "view_finance", "name": "查看供投财务"},
    {"id": "view_org_risk", "name": "查看组织风险"},
    {"id": "view_command", "name": "查看人员总览"},
    {"id": "dispatch_tasks", "name": "下发任务"},
    {"id": "assign_tasks", "name": "拆分派发"},
    {"id": "handle_tasks", "name": "处理任务"},
    {"id": "submit_tasks", "name": "提交处理结果"},
    {"id": "review_tasks", "name": "复核任务"},
    {"id": "manage_roles", "name": "管理角色权限"},
    {"id": "view_only", "name": "只读观察"},
]
PERMISSION_NAME_MAP = {item["id"]: item["name"] for item in PERMISSIONS}
EXECUTIVE_MODULES = ["dashboard", "store-overview", "task-command", "profit-budget", "org-efficiency", "review-audit", "accounts", "role-console"]
MANAGER_MODULES = ["dashboard", "manager-tasks", "manager-dispatch", "manager-review", "manager-modules", "manager-retrospective", "manager-reports", "operating-unit", "data-check", "business-products", "business-traffic", "business-actions", "business-report", "accounts"]
OPERATOR_MODULES = ["dashboard", "operating-unit", "data-check", "business-products", "business-competitors", "business-listing", "business-traffic", "business-actions", "business-report", "accounts"]

ROLES: List[Dict[str, Any]] = [
    {"id": "owner", "name": "老板账号", "level": 1, "scope": "全部经营单元", "insightDepth": "owner_strategy", "insightName": "老板统筹视角", "description": "看全部经营单元、店铺责任分配、人员状态、供投财务和复盘审计。", "permissions": ["view_all_stores", "view_finance", "view_org_risk", "view_command", "dispatch_tasks", "review_tasks", "manage_roles", "view_only"], "visibleModules": EXECUTIVE_MODULES, "allowedActions": ["查看店群总览", "查看人员总览", "配置店铺负责人", "查看供投财务", "查看组织效率", "查看复盘审计"], "hiddenFields": ["一线执行按钮"], "managementInsights": ["经营单元", "店铺责任", "人员状态", "供投财务", "组织效率", "复盘审计"]},
    {"id": "manager", "name": "店群总管账号", "level": 2, "scope": "负责经营单元全量", "insightDepth": "team_management", "insightName": "经营单元执行管理视角", "description": "看自己负责经营单元下全部店铺、商品、预警和运营任务。", "permissions": ["view_managed_stores", "view_finance", "assign_tasks", "review_tasks", "view_only"], "visibleModules": MANAGER_MODULES, "allowedActions": ["查看经营单元全量", "拆分任务", "派发运营", "复核运营提交", "提交周期复盘"], "hiddenFields": ["全局角色模板调整"], "managementInsights": ["经营单元", "店铺全量", "任务派发", "运营复核", "数据报表"]},
    {"id": "operator", "name": "运营账号", "level": 3, "scope": "经营单元内的店铺切片", "insightDepth": "store_operation", "insightName": "店铺运营视角", "description": "能进入共同经营单元，但只看自己负责店铺内的商品、报表、预警、任务和日志。", "permissions": ["view_managed_stores", "view_own_tasks", "handle_tasks", "submit_tasks", "view_only"], "visibleModules": OPERATOR_MODULES, "allowedActions": ["查看负责店铺", "查看店铺报表", "处理店铺任务", "提交处理结果", "提交运营日志"], "hiddenFields": ["经营单元全量", "其他运营店铺", "组织权限调整"], "managementInsights": ["我的店铺", "我的商品", "我的流量", "我的待办", "我的日志"]},
    {"id": "finance", "name": "数据 / 财务账号", "level": 3, "scope": "经营单元财务数据", "insightDepth": "finance_risk", "insightName": "财务经营视角", "description": "查看供货成本、投流消耗、ROI、退款成本、库存资金和财务汇总。", "permissions": ["view_finance", "view_only"], "visibleModules": ["dashboard", "store-overview", "profit-budget", "manager-reports", "business-report", "accounts"], "allowedActions": ["查看供投财务", "标记数据异常", "补充财务说明"], "hiddenFields": ["运营派发按钮", "角色管理"], "managementInsights": ["供货成本", "广告消耗", "ROI 可信度", "库存资金"]},
    {"id": "observer", "name": "只读观察账号", "level": 4, "scope": "授权摘要", "insightDepth": "summary_only", "insightName": "只读摘要视角", "description": "只能查看摘要、进度和日志结果。", "permissions": ["view_only"], "visibleModules": ["dashboard", "store-overview", "review-audit", "business-report", "accounts"], "allowedActions": ["查看摘要", "查看日志"], "hiddenFields": ["财务细节", "任务责任链", "操作按钮"], "managementInsights": ["平台摘要", "店铺状态", "复盘摘要", "归档结果"]},
]

STORE_GROUPS = [{"id": "G001", "name": "家居生活店铺组", "ownerId": "U001", "managerId": "U002", "storeIds": ["S001", "S002", "S003", "S004"]}]
STORES = [
    {"id": "S001", "name": "家居生活主店", "platform": "淘宝", "groupId": "G001"},
    {"id": "S002", "name": "家居百货店", "platform": "拼多多", "groupId": "G001"},
    {"id": "S003", "name": "家居好物号", "platform": "抖音小店", "groupId": "G001"},
    {"id": "S004", "name": "家清收纳店", "platform": "拼多多", "groupId": "G001"},
]
USERS = [
    {"id": "U001", "name": "老板", "roleId": "owner", "storeGroupIds": ["G001"], "storeIds": ["S001", "S002", "S003", "S004"], "status": "启用"},
    {"id": "U002", "name": "店群总管", "roleId": "manager", "storeGroupIds": ["G001"], "storeIds": ["S001", "S002", "S003", "S004"], "status": "启用"},
    {"id": "U003", "name": "运营 A", "roleId": "operator", "storeGroupIds": ["G001"], "storeIds": ["S001", "S002"], "status": "启用"},
    {"id": "U004", "name": "运营 B", "roleId": "operator", "storeGroupIds": ["G001"], "storeIds": ["S003", "S004"], "status": "启用"},
    {"id": "U005", "name": "数据财务", "roleId": "finance", "storeGroupIds": ["G001"], "storeIds": ["S001", "S002", "S003", "S004"], "status": "启用"},
    {"id": "U006", "name": "观察者", "roleId": "observer", "storeGroupIds": ["G001"], "storeIds": ["S001", "S002", "S003", "S004"], "status": "启用"},
]
STORE_ASSIGNMENTS = [
    {"storeId": "S001", "operatingUnitId": "G001", "primaryOperatorId": "U003", "assistantOperatorIds": [], "reviewerId": "U002"},
    {"storeId": "S002", "operatingUnitId": "G001", "primaryOperatorId": "U003", "assistantOperatorIds": [], "reviewerId": "U002"},
    {"storeId": "S003", "operatingUnitId": "G001", "primaryOperatorId": "U004", "assistantOperatorIds": [], "reviewerId": "U002"},
    {"storeId": "S004", "operatingUnitId": "G001", "primaryOperatorId": "U004", "assistantOperatorIds": [], "reviewerId": "U002"},
]
PENDING_STORE_MIGRATIONS: List[Dict[str, Any]] = []
ROLE_CHANGE_LOGS: List[Dict[str, Any]] = []


def clone(value: Any) -> Any: return deepcopy(value)
def permission_names(permission_ids: List[str]) -> List[str]: return [PERMISSION_NAME_MAP.get(item, item) for item in permission_ids]
def role_by_id(role_id: str | None) -> Dict[str, Any] | None: return next((role for role in ROLES if role["id"] == role_id), None)
def user_raw(user_id: str | None) -> Dict[str, Any] | None: return next((item for item in USERS if item["id"] == user_id), None) if user_id else None
def user_name(user_id: str | None, fallback: str = "未分配") -> str: user = user_raw(user_id); return fallback if not user else user["name"]
def store_raw(store_id: str | None) -> Dict[str, Any] | None: return next((item for item in STORES if item["id"] == store_id), None) if store_id else None
def assignment_for_store(store_id: str | None) -> Dict[str, Any] | None: return next((item for item in STORE_ASSIGNMENTS if item["storeId"] == store_id), None) if store_id else None
def pending_migration_for_store(store_id: str | None) -> Dict[str, Any] | None: return next((item for item in PENDING_STORE_MIGRATIONS if item.get("storeId") == store_id and item.get("status") == "待生效"), None) if store_id else None

def next_effective_date() -> str: return (date.today() + timedelta(days=1)).isoformat()
def verify_management_password(password: str | None) -> bool: return str(password or "") == MANAGEMENT_PASSWORD

def enrich_role(role: Dict[str, Any]) -> Dict[str, Any]:
    item = clone(role); item["permissionNames"] = permission_names(item.get("permissions", [])); item["permissionSummary"] = "、".join(item["permissionNames"]); return item

def enrich_migration(migration: Dict[str, Any]) -> Dict[str, Any]:
    item = clone(migration); store = store_raw(item.get("storeId")) or {}
    item.update({"storeName": store.get("name"), "platform": store.get("platform"), "oldOperatorName": user_name(item.get("oldOperatorId")), "newOperatorName": user_name(item.get("newOperatorId")), "reviewerName": user_name(item.get("reviewerId"), "未设置复核人"), "operatorName": user_name(item.get("operatorId"), "系统")})
    return item

def enrich_store(store: Dict[str, Any]) -> Dict[str, Any]:
    item = clone(store); assignment = assignment_for_store(item.get("id")) or {}; pending = pending_migration_for_store(item.get("id"))
    item.update({"operatingUnitId": item.get("groupId"), "primaryOperatorId": assignment.get("primaryOperatorId"), "primaryOperatorName": user_name(assignment.get("primaryOperatorId")), "assistantOperatorIds": assignment.get("assistantOperatorIds", []), "reviewerId": assignment.get("reviewerId"), "reviewerName": user_name(assignment.get("reviewerId"), "未设置复核人"), "pendingMigration": enrich_migration(pending) if pending else None})
    return item

def enrich_assignment(assignment: Dict[str, Any]) -> Dict[str, Any]:
    store = store_raw(assignment.get("storeId")) or {}; pending = pending_migration_for_store(assignment.get("storeId"))
    item = clone(assignment)
    item.update({"storeName": store.get("name"), "platform": store.get("platform"), "primaryOperatorName": user_name(item.get("primaryOperatorId")), "reviewerName": user_name(item.get("reviewerId"), "未设置复核人"), "pendingMigration": enrich_migration(pending) if pending else None})
    return item

def enrich_user(user: Dict[str, Any]) -> Dict[str, Any]:
    item = clone(user); role = enrich_role(role_by_id(item.get("roleId")) or {})
    item.update({"roleName": role.get("name", "未设置角色"), "roleLevel": role.get("level"), "insightDepth": role.get("insightDepth"), "insightName": role.get("insightName"), "permissions": role.get("permissions", []), "permissionNames": role.get("permissionNames", []), "visibleModules": role.get("visibleModules", []), "allowedActions": role.get("allowedActions", []), "hiddenFields": role.get("hiddenFields", []), "managementInsights": role.get("managementInsights", []), "scope": role.get("scope")})
    item["storeNames"] = [enrich_store(store_raw(store_id) or {"id": store_id, "name": store_id}).get("name") for store_id in item.get("storeIds", [])]
    return item

def list_roles() -> List[Dict[str, Any]]: return [enrich_role(role) for role in ROLES]
def list_permissions() -> List[Dict[str, str]]: return clone(PERMISSIONS)
def list_users() -> List[Dict[str, Any]]: apply_due_store_migrations(); return [enrich_user(user) for user in USERS]
def list_store_groups() -> List[Dict[str, Any]]: return clone(STORE_GROUPS)
def list_stores() -> List[Dict[str, Any]]: apply_due_store_migrations(); return [enrich_store(store) for store in STORES]
def list_store_assignments() -> List[Dict[str, Any]]: apply_due_store_migrations(); return [enrich_assignment(item) for item in STORE_ASSIGNMENTS]
def list_pending_store_migrations() -> List[Dict[str, Any]]: apply_due_store_migrations(); return [enrich_migration(item) for item in PENDING_STORE_MIGRATIONS if item.get("status") == "待生效"]
def list_role_change_logs() -> List[Dict[str, Any]]: return clone(ROLE_CHANGE_LOGS)
def get_user(user_id: str | None) -> Dict[str, Any] | None: apply_due_store_migrations(); user = user_raw(user_id); return enrich_user(user) if user else None
def resolve_user_id(user_id: str | None = None) -> str: return user_id if get_user(user_id) else DEFAULT_USER_ID
def user_id_from_headers(headers: Mapping[str, str] | None = None, fallback: str | None = None) -> str: headers = headers or {}; return resolve_user_id(headers.get("x-mock-user-id") or headers.get("X-Mock-User-Id") or fallback)
def current_user(user_id: str | None = None) -> Dict[str, Any]: return get_user(resolve_user_id(user_id)) or enrich_user(USERS[0])
def user_display(user_id: str | None, fallback: str = "未派发") -> str: user = get_user(user_id); return fallback if not user else f"{user['name']} · {user['roleName']}"
def users_by_role(role_id: str) -> List[Dict[str, Any]]: return [user for user in list_users() if user.get("roleId") == role_id]
def default_operator(risk_domain: str | None = None) -> Dict[str, Any]: return users_by_role("operator")[1] if risk_domain in {"流量", "上新"} and len(users_by_role("operator")) > 1 else users_by_role("operator")[0]
def default_reviewer() -> Dict[str, Any]: return users_by_role("manager")[0]
def user_has_permission(user_id: str | None, permission: str) -> bool: return permission in current_user(user_id).get("permissions", [])

def visible_store_ids_for_user(user_id: str | None) -> List[str]:
    apply_due_store_migrations(); user = current_user(user_id); role = user.get("roleId")
    if role in {"owner", "manager", "finance"}:
        group_ids = set(user.get("storeGroupIds") or [])
        return [store["id"] for store in STORES if store.get("groupId") in group_ids] or list(user.get("storeIds") or [])
    if role == "operator":
        return list(user.get("storeIds") or [])
    return list(user.get("storeIds") or [])

def role_view_for_user(user: Dict[str, Any]) -> Dict[str, Any]:
    pages = {"owner": {"headline": "老板统筹台", "summary": "看经营单元全量、店铺责任分配、员工状态和复盘审计。", "sections": ["店群总览", "人员总览", "供投财务", "组织效率", "复盘审计"]}, "manager": {"headline": "店群总管工作台", "summary": "看负责经营单元的全量店铺、商品、预警和任务，拆分派发并复核。", "sections": ["经营单元", "店群任务", "任务派发", "运营复核", "数据报表"]}, "operator": {"headline": "店铺运营台", "summary": "进入共同经营单元，但只看自己负责店铺切片。", "sections": ["经营单元", "报表", "商品", "流量", "待办", "日志"]}, "finance": {"headline": "数据 / 财务台", "summary": "看供货成本、投流消耗、利润、ROI、退款成本和库存资金。", "sections": ["店群总览", "供投财务", "数据报表", "日志"]}, "observer": {"headline": "只读观察台", "summary": "只看摘要、进度和归档日志。", "sections": ["总览", "店群总览", "复盘审计", "日志摘要"]}}
    return pages.get(user.get("roleId"), pages["observer"])

def _sync_operator_store_ids() -> None:
    assigned = {item.get("primaryOperatorId"): [] for item in STORE_ASSIGNMENTS if item.get("primaryOperatorId")}
    for item in STORE_ASSIGNMENTS:
        operator_id = item.get("primaryOperatorId")
        if operator_id:
            assigned.setdefault(operator_id, []).append(item["storeId"])
    for user in USERS:
        if user.get("roleId") == "operator":
            user["storeIds"] = list(dict.fromkeys(assigned.get(user["id"], user.get("storeIds", []))))

def apply_due_store_migrations() -> None:
    today = date.today().isoformat()
    changed = False
    for migration in PENDING_STORE_MIGRATIONS:
        if migration.get("status") != "待生效" or migration.get("effectiveDate") > today:
            continue
        assignment = assignment_for_store(migration.get("storeId"))
        if assignment:
            assignment["primaryOperatorId"] = migration.get("newOperatorId")
            assignment["reviewerId"] = migration.get("reviewerId") or assignment.get("reviewerId") or "U002"
            migration["status"] = "已生效"
            changed = True
    if changed:
        _sync_operator_store_ids()

def update_user_role(user_id: str, role_id: str, operator_id: str | None = None) -> Dict[str, Any] | None:
    user = user_raw(user_id); role = role_by_id(role_id)
    if not user or not role: return None
    old_role = user.get("roleId"); user["roleId"] = role_id; ROLE_CHANGE_LOGS.insert(0, {"type": "角色变更", "userId": user_id, "oldRoleId": old_role, "newRoleId": role_id, "operatorId": operator_id or DEFAULT_USER_ID}); return get_user(user_id)

def update_user_stores(user_id: str, store_ids: List[str], operator_id: str | None = None) -> Dict[str, Any] | None:
    user = user_raw(user_id); valid = {store["id"] for store in STORES}
    if not user: return None
    next_ids = [store_id for store_id in store_ids if store_id in valid]
    user["storeIds"] = next_ids
    ROLE_CHANGE_LOGS.insert(0, {"type": "店铺授权", "userId": user_id, "storeIds": next_ids, "operatorId": operator_id or DEFAULT_USER_ID}); return get_user(user_id)

def update_store_assignment(store_id: str, primary_operator_id: str | None, reviewer_id: str | None = None, operator_id: str | None = None) -> Dict[str, Any] | None:
    store = store_raw(store_id)
    if not store: return None
    assignment = assignment_for_store(store_id)
    if not assignment:
        assignment = {"storeId": store_id, "operatingUnitId": store.get("groupId"), "primaryOperatorId": None, "assistantOperatorIds": [], "reviewerId": None}; STORE_ASSIGNMENTS.append(assignment)
    if primary_operator_id and not get_user(primary_operator_id): return None
    if reviewer_id and not get_user(reviewer_id): return None
    old_operator = assignment.get("primaryOperatorId")
    assignment["primaryOperatorId"] = primary_operator_id
    assignment["reviewerId"] = reviewer_id or assignment.get("reviewerId") or "U002"
    _sync_operator_store_ids()
    ROLE_CHANGE_LOGS.insert(0, {"type": "店铺负责人", "storeId": store_id, "oldOperatorId": old_operator, "newOperatorId": primary_operator_id, "reviewerId": assignment.get("reviewerId"), "operatorId": operator_id or DEFAULT_USER_ID})
    return enrich_assignment(assignment)

def schedule_store_assignment_migration(store_id: str, primary_operator_id: str | None, reviewer_id: str | None = None, password: str | None = None, operator_id: str | None = None) -> Dict[str, Any] | None:
    if not verify_management_password(password):
        raise PermissionError("management password is invalid")
    store = store_raw(store_id)
    if not store: return None
    assignment = assignment_for_store(store_id)
    if not assignment:
        assignment = {"storeId": store_id, "operatingUnitId": store.get("groupId"), "primaryOperatorId": None, "assistantOperatorIds": [], "reviewerId": None}; STORE_ASSIGNMENTS.append(assignment)
    if primary_operator_id and not get_user(primary_operator_id): return None
    if reviewer_id and not get_user(reviewer_id): return None
    for migration in PENDING_STORE_MIGRATIONS:
        if migration.get("storeId") == store_id and migration.get("status") == "待生效":
            migration["status"] = "已替换"
    migration = {"id": f"MIG_{len(PENDING_STORE_MIGRATIONS) + 1:04d}", "type": "店铺权限迁移", "storeId": store_id, "operatingUnitId": store.get("groupId"), "oldOperatorId": assignment.get("primaryOperatorId"), "newOperatorId": primary_operator_id, "reviewerId": reviewer_id or assignment.get("reviewerId") or "U002", "effectiveDate": next_effective_date(), "status": "待生效", "operatorId": operator_id or DEFAULT_USER_ID, "impactScope": ["商品数据", "报表数据", "预警归属", "未完成待办", "运营日志", "复盘归属"], "message": "权限迁移将在次日生效；生效前当前运营可见范围不变。"}
    PENDING_STORE_MIGRATIONS.insert(0, migration)
    ROLE_CHANGE_LOGS.insert(0, {"type": "店铺权限迁移", "storeId": store_id, "oldOperatorId": migration["oldOperatorId"], "newOperatorId": primary_operator_id, "effectiveDate": migration["effectiveDate"], "status": "待生效", "operatorId": operator_id or DEFAULT_USER_ID})
    return enrich_migration(migration)

def update_role_permissions(role_id: str, permissions: List[str], operator_id: str | None = None) -> Dict[str, Any] | None:
    role = role_by_id(role_id); valid = {permission["id"] for permission in PERMISSIONS}
    if not role: return None
    role["permissions"] = [permission for permission in permissions if permission in valid]; ROLE_CHANGE_LOGS.insert(0, {"type": "权限模板", "roleId": role_id, "permissions": role["permissions"], "operatorId": operator_id or DEFAULT_USER_ID}); return enrich_role(role)

def account_summary(user_id: str | None = None) -> Dict[str, Any]:
    apply_due_store_migrations(); user = current_user(user_id)
    return {"currentUser": user, "currentRoleView": role_view_for_user(user), "roles": list_roles(), "permissions": list_permissions(), "users": list_users(), "storeGroups": list_store_groups(), "stores": list_stores(), "storeAssignments": list_store_assignments(), "pendingStoreMigrations": list_pending_store_migrations(), "visibleStoreIds": visible_store_ids_for_user(user.get("id")), "roleChangeLogs": list_role_change_logs(), "taskFlow": ["经营单元共同可见", "店铺责任决定数据范围", "权限迁移次日生效", "预警按店铺派给负责人", "运营处理提交", "总管复核归档", "老板看汇总复盘"]}
