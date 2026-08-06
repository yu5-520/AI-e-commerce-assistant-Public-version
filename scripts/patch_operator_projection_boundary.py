#!/usr/bin/env python3
"""Remove the retired account-service dependency from runtime projections."""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request


def api_request(url: str, token: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "operator-projection-boundary-patcher",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


def patch_projection(source: str) -> str:
    source = replace_once(
        source,
        "from src.services.account_service import current_user, list_stores, visible_store_ids_for_user\n",
        "from src.services.competition_operator_context_service import COMPETITION_OPERATOR_ID, competition_stores\n",
        "projection account import",
    )
    source = replace_once(
        source,
        "from src.services.permission_stamp_service import permission_stamp_allows, row_permission_stamp\n",
        "from src.services.permission_stamp_service import row_permission_stamp\n",
        "projection permission import",
    )
    source = replace_once(
        source,
        "    for store in list_stores():\n",
        "    for store in competition_stores():\n",
        "projection store catalog",
    )
    source = replace_once(
        source,
        "\n\ndef _visible_store_ids(user_id: str | None) -> set[str]:\n    return set(visible_store_ids_for_user(user_id)) if user_id else set()\n",
        "",
        "projection visible store helper",
    )
    old = '''def _row_visible(row: Dict[str, Any], user_id: str | None) -> bool:
    store_id = row.get("storeId") or row.get("store_id") or _resolve_store_id(row)
    if store_id:
        row.setdefault("storeId", store_id)
    if strict_data_scope_enabled():
        decision = _scope_decision(row, store_id)
        if decision.get("status") != "ok":
            row["scopeStatus"] = "quarantined"
            row["scopeMissing"] = decision.get("missing", [])
            row["scopeErrors"] = decision.get("errors", [])
            return False
    if not user_id:
        return True
    role = current_user(user_id).get("roleId")
    if role in {"owner", "manager", "finance"}:
        return True
    if permission_stamp_allows(row, user_id, role):
        row["permissionStampAccepted"] = True
        return True
    if not store_id:
        return True
    return store_id in _visible_store_ids(user_id)
'''
    new = '''def _row_visible(row: Dict[str, Any], user_id: str | None) -> bool:
    """Project rows inside the fixed competition workspace.

    ``user_id`` is retained only for call compatibility and is never trusted as
    client identity. The public runtime has no application account system.
    """
    _ = user_id
    store_id = row.get("storeId") or row.get("store_id") or _resolve_store_id(row)
    if store_id:
        row.setdefault("storeId", store_id)
    if strict_data_scope_enabled():
        decision = _scope_decision(row, store_id)
        if decision.get("status") != "ok":
            row["scopeStatus"] = "quarantined"
            row["scopeMissing"] = decision.get("missing", [])
            row["scopeErrors"] = decision.get("errors", [])
            return False
    row["runtimeActorId"] = COMPETITION_OPERATOR_ID
    row["workspaceId"] = DEFAULT_TENANT_ID
    return True
'''
    source = replace_once(source, old, new, "projection fixed operator visibility")
    for token in (
        "src.services.account_service",
        "current_user(",
        "visible_store_ids_for_user",
        "permission_stamp_allows",
    ):
        if token in source:
            raise RuntimeError(f"forbidden account projection token remains: {token}")
    compile(source, "src/services/module_projection_service.py", "exec")
    return source


def patch_isolation(source: str) -> str:
    old = '''    row_tenant = row_tenant_id(row)
    row_org = row_org_id(row)
    row_store = store_id or row_store_id(row)
    missing: list[str] = []
    if not row_tenant:
        missing.append("tenant_id")
    if not row_org:
        missing.append("org_id")
    if require_store and not row_store:
        missing.append("store_id")
'''
    new = '''    # Missing tenant/org values are assigned to the server-owned competition
    # workspace. This is a namespace stamp, not a claim of enterprise tenant isolation.
    row_tenant = row_tenant_id(row) or tenant_id
    row_org = row_org_id(row) or org_id
    row_store = store_id or row_store_id(row)
    missing: list[str] = []
    if require_store and not row_store:
        missing.append("store_id")
'''
    source = replace_once(source, old, new, "fixed workspace scope defaults")
    source = replace_once(
        source,
        '        "dataRule": "competition namespace is fixed; this is not presented as enterprise tenant isolation",\n',
        '        "dataRule": "missing tenant/org fields are server-stamped to competition_demo; explicit mismatches quarantine; no enterprise isolation claim",\n',
        "isolation summary",
    )
    compile(source, "src/services/backend_isolation_service.py", "exec")
    return source


def update_file(repo: str, branch: str, token: str, path: str, message: str, transform) -> str:
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    current = api_request(api + "?" + urllib.parse.urlencode({"ref": branch}), token)
    source = base64.b64decode(current["content"]).decode("utf-8")
    updated = transform(source)
    if updated == source:
        raise RuntimeError(f"no changes produced for {path}")
    result = api_request(
        api,
        token,
        method="PUT",
        payload={
            "message": message,
            "content": base64.b64encode(updated.encode("utf-8")).decode("ascii"),
            "sha": current["sha"],
            "branch": branch,
        },
    )
    return result["commit"]["sha"]


def main() -> int:
    repo = os.environ["REPOSITORY"]
    branch = os.environ["TARGET_BRANCH"]
    token = os.environ["GH_TOKEN"]
    first = update_file(
        repo,
        branch,
        token,
        "src/services/module_projection_service.py",
        "fix: 投影层改用固定运营工作台上下文",
        patch_projection,
    )
    second = update_file(
        repo,
        branch,
        token,
        "src/services/backend_isolation_service.py",
        "fix: 固定比赛命名空间由服务端补齐",
        patch_isolation,
    )
    print(json.dumps({"projectionCommit": first, "isolationCommit": second}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
