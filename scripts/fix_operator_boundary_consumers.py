#!/usr/bin/env python3
"""Patch the one-time operator-boundary migrator after validation feedback."""
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
        "User-Agent": "operator-boundary-consumer-fixer",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def main() -> int:
    repo = os.environ["REPOSITORY"]
    branch = os.environ["TARGET_BRANCH"]
    token = os.environ["GH_TOKEN"]
    path = "scripts/apply_operator_core_boundary.py"
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    current = api_request(api + "?" + urllib.parse.urlencode({"ref": branch}), token)
    source = base64.b64decode(current["content"]).decode("utf-8")

    def replace_once(old: str, new: str, label: str) -> None:
        nonlocal source
        count = source.count(old)
        if count != 1:
            raise RuntimeError(f"{label}: expected one match, found {count}")
        source = source.replace(old, new, 1)

    fixed_consumers = r'''

def patch_fixed_identity_consumers(root: Path, changed: set[str]) -> None:
    frontend_relative = "src/api/routes/frontend_views.py"
    frontend = (root / frontend_relative).read_text(encoding="utf-8")
    frontend = replace_once(
        frontend,
        "from fastapi import APIRouter, Header, HTTPException, Query\n",
        "from fastapi import APIRouter, HTTPException, Query\n",
        "frontend views remove identity header import",
    )
    frontend = replace_once(
        frontend,
        'router = APIRouter(prefix="/api/view", tags=["frontend-read-model"])\n',
        'router = APIRouter(prefix="/api/view", tags=["frontend-read-model"])\nCOMPETITION_OPERATOR_ID = "competition_operator"\n',
        "frontend views fixed actor",
    )
    frontend = replace_once(
        frontend,
        '''def hash_view_head(
    view_key: str,
    dataVersion: str | None = None,
    x_mock_user_id: str = Header(default="U001", alias="X-Mock-User-Id"),
) -> Dict[str, Any]:''',
        '''def hash_view_head(
    view_key: str,
    dataVersion: str | None = None,
) -> Dict[str, Any]:''',
        "frontend head removes mock header",
    )
    frontend = frontend.replace('user_id=x_mock_user_id or "U001"', 'user_id=COMPETITION_OPERATOR_ID')
    frontend = replace_once(
        frontend,
        '''def hash_view_artifact(
    artifact_ref: str,
    viewKey: str = DEFAULT_VIEW_KEY,
    x_mock_user_id: str = Header(default="U001", alias="X-Mock-User-Id"),
) -> Dict[str, Any]:''',
        '''def hash_view_artifact(
    artifact_ref: str,
    viewKey: str = DEFAULT_VIEW_KEY,
) -> Dict[str, Any]:''',
        "frontend artifact removes mock header",
    )
    frontend = replace_once(
        frontend,
        '''def refresh_view(
    dataVersion: str | None = None,
    x_mock_user_id: str = Header(default="U001", alias="X-Mock-User-Id"),
) -> Dict[str, Any]:''',
        '''def refresh_view(
    dataVersion: str | None = None,
) -> Dict[str, Any]:''',
        "frontend refresh removes mock header",
    )
    if "X-Mock-User-Id" in frontend or "x_mock_user_id" in frontend or '"U001"' in frontend:
        raise PatchError("frontend view still accepts client mock identity")
    write_text(root, frontend_relative, frontend, changed)

    isolation_relative = "src/services/backend_isolation_service.py"
    isolation = '''"""Competition fixed-workspace data-scope helpers.

The public competition runtime has no application account system and accepts no
client-selected user, role, tenant or organization identity. These helpers preserve
the existing row-scope API while pinning the demo namespace on the server side.
"""
from __future__ import annotations

from typing import Any, Mapping

DEFAULT_TENANT_ID = "competition_demo"
DEFAULT_ORG_ID = "competition_demo"
COMPETITION_OPERATOR_ID = "competition_operator"
TENANT_HEADER = "x-tenant-id"
ORG_HEADER = "x-org-id"


def production_mode() -> bool:
    return True


def demo_account_switch_enabled() -> bool:
    return False


def demo_mock_identity_allowed() -> bool:
    return False


def strict_data_scope_enabled() -> bool:
    return True


def _headers(headers: Mapping[str, str] | None) -> Mapping[str, str]:
    return headers or {}


def header_value(headers: Mapping[str, str] | None, *names: str, default: str | None = None) -> str | None:
    source = _headers(headers)
    for name in names:
        for candidate in (name, name.lower(), name.title(), name.upper()):
            if source.get(candidate):
                return str(source.get(candidate))
    return default


def mock_user_header_value(headers: Mapping[str, str] | None) -> str | None:
    return None


def trusted_user_header_value(headers: Mapping[str, str] | None) -> str | None:
    return None


def request_tenant_id(headers: Mapping[str, str] | None) -> str:
    return DEFAULT_TENANT_ID


def request_org_id(headers: Mapping[str, str] | None) -> str:
    return DEFAULT_ORG_ID


def row_scope_value(row: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value not in {None, ""}:
            return str(value).strip()
    return None


def row_tenant_id(row: Mapping[str, Any]) -> str | None:
    return row_scope_value(row, "tenantId", "tenant_id", "租户ID", "租户id")


def row_org_id(row: Mapping[str, Any]) -> str | None:
    return row_scope_value(row, "orgId", "org_id", "组织ID", "组织id", "经营单元ID", "经营单元id")


def row_store_id(row: Mapping[str, Any]) -> str | None:
    return row_scope_value(row, "storeId", "store_id", "店铺ID", "店铺id", "店铺编号", "店铺编码")


def row_scope_status(row: Mapping[str, Any], *, tenant_id: str = DEFAULT_TENANT_ID, org_id: str = DEFAULT_ORG_ID, store_id: str | None = None, require_store: bool = False) -> dict[str, Any]:
    row_tenant = row_tenant_id(row)
    row_org = row_org_id(row)
    row_store = store_id or row_store_id(row)
    missing: list[str] = []
    if not row_tenant:
        missing.append("tenant_id")
    if not row_org:
        missing.append("org_id")
    if require_store and not row_store:
        missing.append("store_id")
    errors: list[str] = []
    if row_tenant and row_tenant != tenant_id:
        errors.append("tenant_mismatch")
    if row_org and row_org != org_id:
        errors.append("org_mismatch")
    status = "ok" if not missing and not errors else "quarantine"
    return {
        "status": status,
        "tenantId": row_tenant,
        "orgId": row_org,
        "storeId": row_store,
        "missing": missing,
        "errors": errors,
        "strict": True,
    }


def isolation_runtime_summary() -> dict[str, Any]:
    return {
        "version": "competition.operator_boundary.v1",
        "runtimeActor": COMPETITION_OPERATOR_ID,
        "workspaceId": DEFAULT_TENANT_ID,
        "applicationAccountSystemEnabled": False,
        "clientIdentityOverrideAllowed": False,
        "strictDataScope": True,
        "identityRule": "server-fixed competition operator; external identity adapter is enterprise-only",
        "dataRule": "competition namespace is fixed; this is not presented as enterprise tenant isolation",
    }
'''
    write_text(root, isolation_relative, isolation, changed)

    hash_relative = "web_demo/core/hash-view-client-v2259.js"
    hash_client = (root / hash_relative).read_text(encoding="utf-8")
    hash_client = replace_once(
        hash_client,
        '''  function userId() {
    return window.AppApi?.getCurrentUserId?.() || localStorage.getItem("ai_ecommerce_v442_current_user_id") || "U001";
  }
''',
        '''  function userId() {
    return "competition_operator";
  }
''',
        "hash view fixed operator",
    )
    hash_client = replace_once(
        hash_client,
        'headers: { Accept: "application/json", "X-Mock-User-Id": userId() },',
        'headers: { Accept: "application/json" },',
        "hash view remove mock header",
    )
    write_text(root, hash_relative, hash_client, changed)

    task_relative = "web_demo/core/task-read-model-v2082.js"
    task_client = (root / task_relative).read_text(encoding="utf-8")
    task_client = replace_once(
        task_client,
        '  function currentUserId() { return window.AppApi?.getCurrentUserId?.() || localStorage.getItem("ai_ecommerce_v442_current_user_id") || "U001"; }\n',
        '  function currentUserId() { return "competition_operator"; }\n',
        "task read fixed operator",
    )
    task_client = replace_once(
        task_client,
        'headers: { Accept: "application/json", "Content-Type": "application/json", "X-Mock-User-Id": currentUserId() },',
        'headers: { Accept: "application/json", "Content-Type": "application/json" },',
        "task read remove mock header",
    )
    write_text(root, task_relative, task_client, changed)

    neural_relative = "web_demo/core/neural-operating-ui.js"
    neural = (root / neural_relative).read_text(encoding="utf-8")
    neural = neural.replace('    ["accounts", { route: "accounts", stage: "growth", label: "个人成长" }],\n', '')
    neural = neural.replace('    ["role-console", { route: "accounts", stage: "growth", label: "组织权限" }],\n', '')
    neural = neural.replace('    central: "系统正在汇总经营信号、任务与个人成长",', '    central: "系统正在汇总经营信号、任务与执行状态",')
    neural = neural.replace('    learned: "执行结果正在形成个人经验与组织记忆",', '    learned: "执行结果正在形成可复用的经营记忆",')
    neural = neural.replace('    growth: "已验证的工作痕迹正在沉淀为成长记录",\n', '')
    neural = neural.replace('    if (stage === "growth") return Number(projection?.operatorProfile?.level || 0);\n', '')
    neural = replace_once(
        neural,
        '''    const userId = window.AppApi?.getCurrentUserId?.() || "U001";
    try {
      const response = await fetch("/api/modules/neural-operating", {
        headers: { Accept: "application/json", "X-Mock-User-Id": userId },
      });''',
        '''    try {
      const response = await fetch("/api/modules/neural-operating", {
        headers: { Accept: "application/json" },
      });''',
        "neural remove mock header",
    )
    neural = neural.replace('  window.addEventListener("mock-account-change", () => scheduleRefresh("account"));\n', '')
    write_text(root, neural_relative, neural, changed)
'''
    replace_once(
        "\ndef build_product_boundary() -> dict[str, Any]:\n",
        fixed_consumers + "\ndef build_product_boundary() -> dict[str, Any]:\n",
        "insert fixed identity consumer patcher",
    )
    replace_once(
        '                "src/services/llm_gateway_v196_legacy_service.py",\n            ],\n            "upstreamRegistryModules": ["agent1_runtime", "agent2_runtime", "agent3_runtime"],',
        '                "src/services/llm_gateway_v196_legacy_service.py",\n                "src/services/llm_gateway_hash_directed_v2259_service.py",\n            ],\n            "upstreamRegistryModules": ["agent1_runtime", "agent2_runtime", "agent3_runtime"],',
        "register hash-directed provider adapter",
    )
    old_gate = '''    for relative in sorted(runtime_set):
        for forbidden in scope.get("forbiddenRuntimePaths") or []:
            if _path_matches(relative, str(forbidden)):
                findings.append(f"FORBIDDEN_RUNTIME_PATH:{relative}")
        path = root / relative
        if not path.is_file() or path.suffix.lower() not in {".py", ".js", ".html", ".css", ".json"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in scope.get("forbiddenRuntimeContent") or []:
            if str(token) in text:
                findings.append(f"FORBIDDEN_RUNTIME_CONTENT:{relative}:{token}")
'''
    new_gate = '''    policy_literal_paths = {
        str(scope.get("productBoundaryPath") or ""),
        str(scope.get("externalInterfaceRegistryPath") or ""),
        "config/competition_runtime_scope.json",
    }
    for relative in sorted(runtime_set):
        for forbidden in scope.get("forbiddenRuntimePaths") or []:
            if _path_matches(relative, str(forbidden)):
                findings.append(f"FORBIDDEN_RUNTIME_PATH:{relative}")
        path = root / relative
        if not path.is_file() or path.suffix.lower() not in {".py", ".js", ".html", ".css", ".json"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if relative not in policy_literal_paths:
            for token in scope.get("forbiddenRuntimeContent") or []:
                if str(token) in text:
                    findings.append(f"FORBIDDEN_RUNTIME_CONTENT:{relative}:{token}")
'''
    replace_once(old_gate, new_gate, "exclude policy literals from runtime code scan")
    replace_once(
        "    patch_frontend_shell(root, changed)\n    patch_scope(root, changed)\n",
        "    patch_frontend_shell(root, changed)\n    patch_fixed_identity_consumers(root, changed)\n    patch_scope(root, changed)\n",
        "call fixed identity consumer patcher",
    )
    compile(source, path, "exec")
    result = api_request(
        api,
        token,
        method="PUT",
        payload={
            "message": "fix: 固定运营身份消费者纳入边界迁移",
            "content": base64.b64encode(source.encode("utf-8")).decode("ascii"),
            "sha": current["sha"],
            "branch": branch,
        },
    )
    print(result["commit"]["sha"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
