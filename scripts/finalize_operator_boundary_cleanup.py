#!/usr/bin/env python3
from __future__ import annotations
import base64, json, os, re, urllib.parse, urllib.request

REPO=os.environ['REPOSITORY']; BRANCH=os.environ['TARGET_BRANCH']; TOKEN=os.environ['GH_TOKEN']
BASE=f'https://api.github.com/repos/{REPO}/contents/'
HEADERS={'Authorization':f'Bearer {TOKEN}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'operator-boundary-finalizer'}

def req(url, method='GET', payload=None):
    h=dict(HEADERS); data=None
    if payload is not None:
        data=json.dumps(payload).encode(); h['Content-Type']='application/json'
    r=urllib.request.Request(url,data=data,method=method,headers=h)
    with urllib.request.urlopen(r,timeout=180) as x: return json.load(x)

def load(path):
    x=req(BASE+path+'?'+urllib.parse.urlencode({'ref':BRANCH}))
    return x,base64.b64decode(x['content']).decode()

def put(path,current,text):
    compile(text,path,'exec') if path.endswith('.py') else None
    x=req(BASE+path,'PUT',{'message':f'fix: {path} 收口固定运营边界','content':base64.b64encode(text.encode()).decode(),'sha':current['sha'],'branch':BRANCH})
    print(path,x['commit']['sha'])

def once(t,a,b,label):
    if t.count(a)!=1: raise RuntimeError(f'{label}: {t.count(a)}')
    return t.replace(a,b,1)

def transform(path,t):
    if 'src.services.account_service' in t:
        t=t.replace('src.services.account_service','src.services.competition_operator_context_service')
    if path.startswith('web_demo/') and path.endswith('.js'):
        t=re.sub(r'^\s*(?:const|function)\s+userId\s*=.*?;\s*\n','',t,flags=re.M)
        t=re.sub(r'^\s*function\s+userHeader\s*\([^)]*\)\s*\{[^\n]*\}\s*\n','',t,flags=re.M)
        t=re.sub(r',\s*"X-Mock-User-Id"\s*:\s*[^,}\n]+','',t)
        t=re.sub(r'"X-Mock-User-Id"\s*:\s*[^,}\n]+,\s*','',t)
        t=t.replace('AppApi.getCurrentUserId()','"competition_operator"').replace('window.AppApi?.getCurrentUserId?.() || "U001"','"competition_operator"').replace('AppApi?.getCurrentUserId?.() || "U001"','"competition_operator"')
    if path=='src/repositories/scoped_repository.py':
        t=t.replace('user_id: str = "U001"','user_id: str = "competition_operator"').replace('tenant_id: str = "tenant_demo"','tenant_id: str = "competition_demo"').replace('org_id: str = "org_demo"','org_id: str = "competition_demo"').replace('role_id: str = "owner"','role_id: str = "operator"').replace('field(default_factory=lambda: ["G001"])','field(default_factory=list)').replace('field(default_factory=lambda: ["S001", "S002", "S003", "S004"])','field(default_factory=lambda: ["COMP-STORE-1"])')
        s=t.index('    @classmethod\n    def from_any'); e=t.index('\n\n\ndef _as_list',s)
        t=t[:s]+'''    @classmethod
    def from_any(cls, value: Any | None) -> "UserContext":
        """Return the server-owned competition scope and ignore client identity."""
        if isinstance(value, cls):
            store_ids = list(value.store_ids or ["COMP-STORE-1"])
        elif isinstance(value, dict):
            store_ids = _as_list(value.get("store_ids") or value.get("storeIds") or ["COMP-STORE-1"])
        else:
            store_ids = _as_list(getattr(value, "store_ids", ["COMP-STORE-1"])) if value is not None else ["COMP-STORE-1"]
        return cls(store_ids=store_ids or ["COMP-STORE-1"])
'''+t[e:]
        t=once(t,'''    if ctx.role_id == "manager":
        where.append(f"{prefix}store_group_id IN :store_group_ids")
        params["store_group_ids"] = tuple(ctx.store_group_ids or ["__none__"])
    elif ctx.role_id in {"operator", "finance", "observer"}:
        where.append(f"{prefix}store_id IN :store_ids")
        params["store_ids"] = tuple(ctx.store_ids or ["__none__"])
    elif ctx.role_id != "owner":
        where.append("1 = 0")
''','''    where.append(f"{prefix}store_id IN :store_ids")
    params["store_ids"] = tuple(ctx.store_ids or ["__none__"])
''','scope query')
        t=once(t,'''    if ctx.role_id == "owner":
        return True
    store_group_id = _pick(item, *store_group_fields)
    store_id = _pick(item, *store_fields)
    if ctx.strict_scope and not store_group_id and not store_id:
        return False
    if ctx.role_id == "manager":
        if store_group_id:
            return store_group_id in set(ctx.store_group_ids)
        return (not ctx.strict_scope and not store_id) or store_id in set(ctx.store_ids)
    return (not ctx.strict_scope and not store_id) or store_id in set(ctx.store_ids)
''','''    store_id = _pick(item, *store_fields)
    if ctx.strict_scope and not store_id:
        return False
    return (not ctx.strict_scope and not store_id) or store_id in set(ctx.store_ids)
''','scope item')
    elif path=='src/services/frontend_view_artifact_v2259_service.py':
        t=t.replace("user_id.strip() or 'U001'","user_id.strip() or 'competition_operator'").replace('user_id: str = "U001"','user_id: str = "competition_operator"')
    elif path=='src/services/operation_budget_service.py':
        t=t.replace('user_id = user_id or "U001"','user_id = "competition_operator"').replace('manager_review_not_operator_budget','enterprise_review_not_enabled_in_competition').replace('status = "manager_review"','status = "enterprise_review_not_enabled_in_competition"')
    elif path in {'src/services/recover_agent1_execution_lock_v22513_service.py','src/services/agent_runtime_hard_interface_v2255_service.py'}:
        t=t.replace('user_id="U001"','user_id="competition_operator"')
    elif path=='src/services/permission_stamp_service.py':
        a='from src.repositories.sqlite_repository import connect, loads\n'; t=t.replace(a,a+'from src.services.competition_operator_context_service import COMPETITION_OPERATOR_ID, COMPETITION_OPERATOR_ROLE\n',1)
        s=t.index('def make_permission_stamp('); e=t.index('\n\n\ndef apply_permission_stamp',s)
        t=t[:s]+'''def make_permission_stamp(*, uploaded_by_user_id: str | None, uploader_role_id: str | None = None, data_version: str | None = None, source: str | None = None, import_batch_id: str | None = None, row: Dict[str, Any] | None = None) -> Dict[str, Any]:
    stamp_id = f"PMS-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"
    return {"version": PERMISSION_STAMP_VERSION, "permissionStampId": stamp_id, "permissionSource": "competition_fixed_operator", "uploadedByUserId": COMPETITION_OPERATOR_ID, "uploaderRoleId": COMPETITION_OPERATOR_ROLE, "ownerUserId": COMPETITION_OPERATOR_ID, "assignedOperatorId": COMPETITION_OPERATOR_ID, "visibleUserIds": [COMPETITION_OPERATOR_ID], "visibleRoleIds": [COMPETITION_OPERATOR_ROLE], "dataVersion": data_version, "importBatchId": import_batch_id or data_version, "source": source, "overrideAllowed": False, "enterpriseOwnershipInputIgnored": bool(explicit_erp_owner(row or {})), "rule": "Competition runtime uses one server-owned operator; ERP ownership is enterprise-only."}
'''+t[e:]
        t=once(t,'''    row_owner = explicit_erp_owner(next_row)
    effective = dict(stamp)
    if row_owner and row_owner != stamp.get("ownerUserId"):
        visible = list(dict.fromkeys([row_owner, stamp.get("uploadedByUserId"), "U001"]))
        effective.update({"permissionSource": "erp_owner", "ownerUserId": row_owner, "assignedOperatorId": row_owner, "visibleUserIds": [item for item in visible if item]})
''','''    effective = dict(stamp)
    effective.update({"permissionSource": "competition_fixed_operator", "uploadedByUserId": COMPETITION_OPERATOR_ID, "uploaderRoleId": COMPETITION_OPERATOR_ROLE, "ownerUserId": COMPETITION_OPERATOR_ID, "assignedOperatorId": COMPETITION_OPERATOR_ID, "visibleUserIds": [COMPETITION_OPERATOR_ID], "visibleRoleIds": [COMPETITION_OPERATOR_ROLE], "overrideAllowed": False})
''','permission apply')
        s=t.index('def permission_stamp_allows('); t=t[:s]+'''def permission_stamp_allows(row: Dict[str, Any], user_id: str | None, role_id: str | None = None) -> bool:
    _ = row, role_id
    return user_id in {None, "", COMPETITION_OPERATOR_ID}
'''
    elif path=='src/services/task_pool_lifecycle_sync_v2020_service.py':
        t=t.replace('from src.services.task_detail_snapshot_v2024_service import (','from src.services.competition_operator_context_service import COMPETITION_OPERATOR_ID, COMPETITION_OPERATOR_ROLE\nfrom src.services.task_detail_snapshot_v2024_service import (',1).replace('DEFAULT_MANAGER_ID = "U002"\nDEFAULT_OPERATOR_ID = "U003"','DEFAULT_OPERATOR_ID = COMPETITION_OPERATOR_ID').replace('    if task_layer == "manager_dispatch":\n        return "待派发"\n    return "待接收"','    return "待接收"').replace('    if task_layer == "manager_dispatch" or "派发" in status or "复核" in status:\n        return [{"action": "review", "label": "复核", "primary": True}, {"action": "detail", "label": "详情"}]\n','').replace('    task_layer = _first(row["task_layer"], task.get("taskLayer"), "operator_execution")','    task_layer = "operator_execution"').replace('    reviewer_id = _first(row["reviewer_id"], task.get("reviewerId"), ownership.get("reviewerId"), DEFAULT_MANAGER_ID)\n    assignee_id = _first(row["assignee_id"], task.get("assigneeId"), ownership.get("assignedOperatorId"))\n    if not assignee_id:\n        assignee_id = reviewer_id if task_layer == "manager_dispatch" else DEFAULT_OPERATOR_ID','    reviewer_id = None\n    assignee_id = DEFAULT_OPERATOR_ID').replace('"visibleUserIds": list(dict.fromkeys([x for x in [assignee_id, reviewer_id, "U001"] if x])),','"visibleUserIds": [COMPETITION_OPERATOR_ID],').replace('"visibleRoleIds": task.get("visibleRoleIds") or ["owner", "manager", "operator"],','"visibleRoleIds": [COMPETITION_OPERATOR_ROLE],').replace('"pending" if task.get("taskLayer") == "manager_dispatch" else "not_required",','"not_required",').replace('0 if task.get("taskLayer") == "manager_dispatch" else 1,','1,')
    elif path=='src/services/task_evidence_audit_service.py':
        t=t.replace('user_id=actor_id or task.get("assignedTo") or task.get("createdBy") or "U001",','user_id="competition_operator",').replace('role_id=str(task.get("roleId") or "operator"),','role_id="operator",').replace('"""Write a manager evidence review to task_evidence and task_logs."""','"""Write an evidence review record; department review is enterprise-only."""')
    elif path=='src/services/operator_growth_projection_v218_service.py':
        s=t.index('_PROFILE_METADATA:'); e=t.index('\n\n_LEVELS',s); t=t[:s]+'''_PROFILE_METADATA: Dict[str, Dict[str, str]] = {"competition_operator": {"displayName": "赛事运营工作台", "positionTitle": "运营", "employmentStartDate": "2026-08-01"}}
'''+t[e:]
        t=t.replace('''    role = _text(user.get("roleId"))
    if role == "manager":
        review = {
            _text(task.get("reviewerId")),
            _text(task.get("reviewerUserId")),
            _text(task.get("reviewerName")),
        } - {""}
        return bool(identities & review)
    return False
''','    return False\n')
    elif path=='src/services/task_acceptance_assignment_station_service.py':
        t=t.replace('actor_user_id=actor_user_id or ("system" if auto else "U003"),','actor_user_id="system" if auto else "competition_operator",').replace('result["rule"] = "只自动接收运营权限内、无需主管/老板确认的任务；待拆分/需复核任务留给总管派发站。"','result["rule"] = "比赛版只自动接收固定运营工作台可执行任务；企业组织审批任务不在公开运行链路执行。"')
        s=t.index('def assign_task('); e=t.index('\n\n\ndef acceptance_assignment_summary',s); t=t[:s]+'''def assign_task(task_id: str, *, actor_user_id: str | None = None, assignee_id: str | None = None, reviewer_id: str | None = None, note: str | None = None, split: bool = False) -> Dict[str, Any]:
    _ = actor_user_id, assignee_id, reviewer_id, note, split
    return {"version": TASK_ACCEPT_ASSIGN_STATION_VERSION, "ok": False, "stationId": "task_assignment_station", "taskId": task_id, "error": "enterprise_organization_collaboration_not_enabled", "message": "老板、主管、部门派发与审批属于企业组织协同增值能力，比赛版暂未开放。"}
'''+t[e:]
        t=t.replace('"waitingAssignment": len([task for task in tasks if task.get("status") == "待拆分" or task.get("taskLayer") == "manager_dispatch"]),','"waitingAssignment": 0,').replace('"rule": "V13.7：任务入池后，接收和派发通过独立站点写统一生命周期状态机。",','"rule": "比赛版仅开放固定运营工作台接收与执行；组织派发为企业增值能力。",')
    elif path=='src/services/task_submission_review_station_service.py':
        t=t.replace('actor_user_id=submitter_id or body.get("submitterId") or evidence_task.get("assigneeId") or "U003",','actor_user_id="competition_operator",').replace('actor_user_id=reviewer_id or body.get("reviewerId") or reviewed.get("reviewerId") or "U002",','actor_user_id="competition_operator",').replace('"rule": "复核站记录复核证据，再通过统一生命周期状态机推进到退回或自动复盘周期。",','"rule": "比赛版只记录运营复核结果；企业部门审批与复核账号暂未开放。", "enterpriseDepartmentReview": "not_enabled_in_competition",')
    elif path=='src/services/task_report_service.py':
        s=t.index('ROLE_INSIGHTS = {'); e=t.index('\n\n\ndef _now',s); t=t[:s]+'''ROLE_INSIGHTS = {"operator": {"title": "运营工作台", "summary": "查看执行材料、任务状态和系统复盘。", "focus": ["提交材料", "执行状态", "复盘周期"], "hidden": ["企业组织审批", "部门角色视图"]}}
'''+t[e:]
        s=t.index('def _apply_role_insight('); e=t.index('\n\n\ndef _task_lookup',s); t=t[:s]+'''def _apply_role_insight(report: Dict[str, Any], user_id: str | None = None) -> Dict[str, Any]:
    user = get_user(user_id) or get_user(None) or {}
    report["viewer"] = {"userId": user.get("id") or "competition_operator", "name": user.get("name") or "赛事运营工作台", "roleName": "运营", "roleId": "operator", "permissionNames": user.get("permissionNames", [])}
    report["roleInsight"] = ROLE_INSIGHTS["operator"]
    report["enterpriseOrganizationCapabilities"] = "暂未开放"
    return report
'''+t[e:]
        t=t.replace('等待系统自动复盘或总管复核','等待系统自动复盘').replace('task.get("reviewerName") or "店群总管"','task.get("reviewerName") or "企业组织协同版暂未开放"')
    elif path=='src/services/alert_detail_service.py':
        t=t.replace('"reviewerName": "店群总管"','"reviewerName": "企业组织协同版暂未开放"').replace('store.get("reviewerName") or "店群总管"','"企业组织协同版暂未开放"')
    elif path=='src/services/pending_authority_migration_v21_service.py':
        t=t.replace('"decision": "manager_review_required",\n                        "taskLayer": "manager_dispatch",\n                        "assigneeId": None,\n                        "status": "待审批",\n                        "workflowStatus": "待审批",','"decision": "enterprise_review_required",\n                        "taskLayer": "operator_execution",\n                        "assigneeId": "competition_operator",\n                        "status": "企业审批能力暂未开放",\n                        "workflowStatus": "企业审批能力暂未开放",\n                        "enterpriseOrganizationCapability": "not_enabled_in_competition",')
    return t

FILES=['src/services/alert_detail_service.py','src/services/creative_vertical_agent_service.py','src/services/module_agent_service.py','src/services/pending_authority_migration_v21_service.py','src/services/task_report_service.py','web_demo/core/report-task-sync.js','web_demo/modules/config-audit/page.js','web_demo/modules/operation-centers-v310.js','web_demo/modules/product/page.js','web_demo/modules/release-alerts/page.js','web_demo/modules/release-governance/page.js','web_demo/modules/report/page.js','web_demo/modules/report/report-runtime.js','web_demo/modules/system-status/page.js','web_demo/modules/tenant-config/page.js','web_demo/modules/weight-center/page.js','src/repositories/scoped_repository.py','src/services/frontend_view_artifact_v2259_service.py','src/services/operation_budget_service.py','src/services/recover_agent1_execution_lock_v22513_service.py','src/services/agent_runtime_hard_interface_v2255_service.py','src/services/permission_stamp_service.py','src/services/task_pool_lifecycle_sync_v2020_service.py','src/services/task_evidence_audit_service.py','src/services/operator_growth_projection_v218_service.py','src/services/task_acceptance_assignment_station_service.py','src/services/task_submission_review_station_service.py']
for p in FILES:
    cur,t=load(p); u=transform(p,t)
    if u==t: raise RuntimeError(f'no change: {p}')
    put(p,cur,u)
print(json.dumps({'updated':len(FILES),'branch':BRANCH}))
