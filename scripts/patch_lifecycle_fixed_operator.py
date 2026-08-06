#!/usr/bin/env python3
"""Remove retired account-system semantics from the lifecycle task runtime."""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request


def request(url: str, token: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "lifecycle-fixed-operator-patcher",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.load(response)


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


def transform(source: str) -> str:
    source = replace_once(
        source,
        '''from src.services.account_service import (
    assignment_for_store,
    default_operator,
    default_reviewer,
    store_raw,
    user_display,
)
''',
        '''from src.services.competition_operator_context_service import (
    COMPETITION_OPERATOR_ID,
    COMPETITION_OPERATOR_ROLE,
    competition_store,
    operator_display,
)
''',
        "retired account import",
    )

    old_ownership = '''def _ownership_for_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    plan = _dict(snapshot.get("taskPlan"))
    store_id = _first_store_id(snapshot, plan)
    assignment = assignment_for_store(store_id) if store_id else None
    reviewer = (
        plan.get("reviewerId")
        or (assignment or {}).get("reviewerId")
        or (default_reviewer() or {}).get("id")
    )
    operator = (
        plan.get("assignedOperatorId")
        or (assignment or {}).get("primaryOperatorId")
        or (default_operator(plan.get("riskDomain") or plan.get("taskType")) or {}).get("id")
    )
    need_manager = bool(
        snapshot.get("needManagerReview")
        or snapshot.get("decision") == "manager_review_required"
        or plan.get("approvalRequired")
    )
    store_ids = plan.get("storeIds") or ([store_id] if store_id else []) or [
        "S001",
        "S002",
        "S003",
        "S004",
    ]
    visible_users = list(
        dict.fromkeys([user for user in [operator, reviewer, "U001"] if user])
    )
    return {
        "assignedOperatorId": None if need_manager else operator,
        "reviewerId": reviewer,
        "ownerUserId": "U001",
        "visibleUserIds": visible_users,
        "visibleRoleIds": ["owner", "manager", "operator"],
        "visibleStoreIds": store_ids,
        "storeIds": store_ids,
    }
'''
    new_ownership = '''def _ownership_for_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Bind every public task to the server-owned competition operator.

    Approval and department-role expansion remain enterprise-only capabilities.
    The competition runtime records that boundary but never fabricates owner,
    manager, reviewer or client-selectable identities.
    """
    plan = _dict(snapshot.get("taskPlan"))
    store_id = _first_store_id(snapshot, plan) or "COMP-STORE-1"
    store_ids = [str(item) for item in _as_list(plan.get("storeIds")) if item]
    if not store_ids:
        store_ids = [store_id]
    enterprise_approval_required = bool(
        snapshot.get("needManagerReview")
        or snapshot.get("decision") == "manager_review_required"
        or plan.get("approvalRequired")
    )
    return {
        "assignedOperatorId": COMPETITION_OPERATOR_ID,
        "reviewerId": None,
        "ownerUserId": None,
        "visibleUserIds": [COMPETITION_OPERATOR_ID],
        "visibleRoleIds": [COMPETITION_OPERATOR_ROLE],
        "visibleStoreIds": store_ids,
        "storeIds": store_ids,
        "runtimeActorMode": "fixed_competition_operator",
        "enterpriseApprovalRequired": enterprise_approval_required,
        "organizationGovernance": "enterprise_only_not_enabled",
    }
'''
    source = replace_once(source, old_ownership, new_ownership, "ownership function")

    source = replace_once(
        source,
        "    store = store_raw(store_id) if store_id else None\n",
        "    store = competition_store(store_id)\n",
        "store resolver",
    )

    old_layer = '''    need_manager = bool(
        snapshot.get("decision") == "manager_review_required"
        or plan.get("approvalRequired")
    )
    task_layer = "manager_dispatch" if need_manager else "operator_execution"
    status = "待拆分" if task_layer == "manager_dispatch" else "待接收"
'''
    new_layer = '''    enterprise_approval_required = bool(
        snapshot.get("decision") == "manager_review_required"
        or plan.get("approvalRequired")
    )
    task_layer = "operator_execution"
    status = "待接收"
'''
    source = replace_once(source, old_layer, new_layer, "task layer")

    source = replace_once(
        source,
        '''        "visibleRoleIds": ownership.get("visibleRoleIds")
        or ["owner", "manager", "operator"],
''',
        '''        "visibleRoleIds": ownership.get("visibleRoleIds")
        or [COMPETITION_OPERATOR_ROLE],
        "enterpriseApprovalRequired": enterprise_approval_required,
        "enterpriseApprovalStatus": (
            "not_enabled_in_competition"
            if enterprise_approval_required
            else "not_required"
        ),
''',
        "visible role boundary",
    )

    old_names = '''        "assigneeName": (
            user_display(ownership.get("assignedOperatorId"), "未派发")
            if task_layer == "operator_execution"
            else "未派发"
        ),
        "reviewerName": user_display(ownership.get("reviewerId"), "未设置复核人"),
        "assignedById": created_by,
        "assignedByName": (
            user_display(created_by, "系统预警") if created_by else "系统预警"
        ),
'''
    new_names = '''        "assigneeName": operator_display(
            ownership.get("assignedOperatorId"), "赛事运营工作台"
        ),
        "reviewerName": "企业组织协同版暂未开放",
        "assignedById": None,
        "assignedByName": "系统经营链路",
'''
    source = replace_once(source, old_names, new_names, "display names")

    source = replace_once(
        source,
        '''        "recapTarget": "日报" if task_layer == "operator_execution" else "周报",
''',
        '''        "recapTarget": "日报",
''',
        "recap target",
    )
    source = replace_once(
        source,
        '''        "availableActions": (
            ["report", "source", "accept", "submit"]
            if task_layer == "operator_execution"
            else ["report", "source", "assign", "review"]
        ),
''',
        '''        "availableActions": ["report", "source", "accept", "submit"],
''',
        "available actions",
    )

    forbidden = (
        "src.services.account_service",
        "assignment_for_store(",
        "default_operator(",
        "default_reviewer(",
        "store_raw(",
        "user_display(",
        '"U001"',
        '"owner"',
        '"manager"',
    )
    remaining = [token for token in forbidden if token in source]
    if remaining:
        raise RuntimeError(f"retired account semantics remain: {remaining}")
    compile(source, "src/services/lifecycle_task_v183_service.py", "exec")
    return source


def main() -> int:
    repo = os.environ["REPOSITORY"]
    branch = os.environ["TARGET_BRANCH"]
    token = os.environ["GH_TOKEN"]
    path = "src/services/lifecycle_task_v183_service.py"
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    current = request(api + "?" + urllib.parse.urlencode({"ref": branch}), token)
    source = base64.b64decode(current["content"]).decode("utf-8")
    updated = transform(source)
    result = request(
        api,
        token,
        method="PUT",
        payload={
            "message": "fix: 生命周期任务改用固定运营工作台上下文",
            "content": base64.b64encode(updated.encode("utf-8")).decode("ascii"),
            "sha": current["sha"],
            "branch": branch,
        },
    )
    print(result["commit"]["sha"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
