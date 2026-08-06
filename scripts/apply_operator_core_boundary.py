#!/usr/bin/env python3
"""Apply the competition operator-only boundary as one atomic Git commit.

This is a one-time repository migration helper. It materializes no production state,
does not deploy, and only updates the designated competition branch through GitHub's
Git Data API after local structural validation succeeds.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class PatchError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def write_text(root: Path, relative: str, content: str, changed: set[str]) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content if content.endswith("\n") else content + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != normalized:
        path.write_text(normalized, encoding="utf-8")
        changed.add(relative)


def delete_path(root: Path, relative: str, deleted: set[str]) -> None:
    path = root / relative
    if path.is_file():
        path.unlink()
        deleted.add(relative)


def api_request(url: str, token: str, *, method: str = "GET", payload: Any | None = None) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "competition-operator-boundary-migrator",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def patch_main(root: Path, changed: set[str]) -> None:
    relative = "src/api/main.py"
    path = root / relative
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "from pathlib import Path\n", "import json\nfrom pathlib import Path\n", "main import json")
    for item in ("    accounts,\n", "    action_authority,\n", "    approvals,\n"):
        if item not in text:
            raise PatchError(f"main route import missing: {item.strip()}")
        text = text.replace(item, "", 1)
    for item in ("    accounts,\n", "    action_authority,\n", "    approvals,\n"):
        marker = "for route_module in [\n"
        start = text.index(marker)
        tail = text[start:]
        if item not in tail:
            raise PatchError(f"main mounted route missing: {item.strip()}")
        tail = tail.replace(item, "", 1)
        text = text[:start] + tail

    boundary_block = '''\nCOMPETITION_PRODUCT_BOUNDARY_FILE = ROOT_DIR / "config" / "competition_product_boundary.json"\n\n\ndef _load_competition_product_boundary() -> dict[str, Any]:\n    value = json.loads(COMPETITION_PRODUCT_BOUNDARY_FILE.read_text(encoding="utf-8"))\n    required_false = (\n        "applicationLoginEnabled",\n        "applicationAccountSystemEnabled",\n        "roleSwitchEnabled",\n        "tenantManagementEnabled",\n        "clientIdentityOverrideAllowed",\n    )\n    for field in required_false:\n        if value.get(field) is not False:\n            raise RuntimeError(f"Competition boundary requires {field}=false")\n    actor = value.get("fixedActor") or {}\n    if value.get("runtimeActorMode") != "fixed_competition_operator":\n        raise RuntimeError("Competition runtime actor mode must be fixed_competition_operator")\n    if actor.get("actorId") != "competition_operator" or actor.get("role") != "operator":\n        raise RuntimeError("Competition fixed operator identity is invalid")\n    if actor.get("serverInjected") is not True:\n        raise RuntimeError("Competition fixed operator must be server injected")\n    return value\n\n\nCOMPETITION_PRODUCT_BOUNDARY = _load_competition_product_boundary()\nCOMPETITION_FIXED_ACTOR = dict(COMPETITION_PRODUCT_BOUNDARY["fixedActor"])\n'''
    text = replace_once(
        text,
        'WEB_INDEX_FILE = WEB_DEMO_DIR / "index.html"\n',
        'WEB_INDEX_FILE = WEB_DEMO_DIR / "index.html"\n' + boundary_block,
        "main competition boundary block",
    )
    text = text.replace(
        '        "releaseIdentity": identity,\n',
        '        "releaseIdentity": identity,\n        "competitionProductBoundary": COMPETITION_PRODUCT_BOUNDARY,\n        "runtimeActor": COMPETITION_FIXED_ACTOR,\n',
        2,
    )
    endpoint = '''\n\n@app.get("/api/competition/runtime-boundary")\ndef competition_runtime_boundary() -> dict[str, Any]:\n    return {\n        "schema": "competition.runtime_boundary.v1",\n        "productBoundary": COMPETITION_PRODUCT_BOUNDARY,\n        "runtimeActor": COMPETITION_FIXED_ACTOR,\n        "authenticationOwner": "external_identity_adapter_enterprise_only",\n        "applicationLoginEnabled": False,\n        "accountSystemEnabled": False,\n    }\n'''
    text = replace_once(
        text,
        '\n\n@app.on_event("startup")\n',
        endpoint + '\n\n@app.on_event("startup")\n',
        "main runtime boundary endpoint",
    )
    if any(token in text for token in ("accounts.router", "approvals.router", "action_authority.router")):
        raise PatchError("main still mounts enterprise account/approval routers")
    write_text(root, relative, text, changed)


def patch_api_client(root: Path, changed: set[str]) -> None:
    relative = "web_demo/core/api-client.js"
    path = root / relative
    text = path.read_text(encoding="utf-8")
    old = '''  const ACCOUNT_KEY = "ai_ecommerce_v442_current_user_id";\n  const API_CLIENT_VERSION = "22.5.5";\n  const status = { source: "unknown", failures: [], lastImportSync: null, lastError: null };\n  const memoryCache = new Map();\n  const revalidateInFlight = new Map();\n  let account = null;\n\n  function getCurrentUserId() { return localStorage.getItem(ACCOUNT_KEY) || "U001"; }\n  function setCurrentUserId(userId) { localStorage.setItem(ACCOUNT_KEY, userId || "U001"); }\n  function currentUser() { return account?.currentUser || null; }\n  function currentPermissions() { return currentUser()?.permissions || []; }\n  function can(permission) { return currentPermissions().includes(permission); }\n'''
    new = '''  const API_CLIENT_VERSION = "22.5.5-competition-operator";\n  const FIXED_OPERATOR = Object.freeze({\n    id: "competition_operator",\n    actorId: "competition_operator",\n    displayName: "赛事运营工作台",\n    roleId: "operator",\n    role: "operator",\n    roleName: "运营",\n    workspaceId: "competition_demo",\n    serverInjected: true,\n  });\n  const FIXED_OPERATOR_PERMISSIONS = Object.freeze([\n    "view_managed_stores",\n    "view_own_tasks",\n    "handle_tasks",\n    "submit_tasks",\n    "view_only",\n  ]);\n  const status = { source: "unknown", failures: [], lastImportSync: null, lastError: null };\n  const memoryCache = new Map();\n  const revalidateInFlight = new Map();\n\n  function currentUser() { return FIXED_OPERATOR; }\n  function currentPermissions() { return [...FIXED_OPERATOR_PERMISSIONS]; }\n  function can(permission) { return FIXED_OPERATOR_PERMISSIONS.includes(permission); }\n'''
    text = replace_once(text, old, new, "api-client fixed operator header")
    text = replace_once(
        text,
        '  function cacheKey(path) { return `${API_CLIENT_VERSION}::${getCurrentUserId()}::${path}`; }\n',
        '  function cacheKey(path) { return `${API_CLIENT_VERSION}::competition_operator::${path}`; }\n',
        "api-client cache identity",
    )
    text = replace_once(
        text,
        'headers: { Accept: "application/json", "Content-Type": "application/json", "X-Mock-User-Id": getCurrentUserId() }',
        'headers: { Accept: "application/json", "Content-Type": "application/json" }',
        "api-client request headers",
    )
    text = replace_once(
        text,
        'headers: { Accept: "application/json", "X-Mock-User-Id": getCurrentUserId() }',
        'headers: { Accept: "application/json" }',
        "api-client upload headers",
    )
    text = re.sub(
        r'\n  async function loadAccount\(\).*?\n  async function applyAccountMutation\(.*?\n',
        "\n",
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = replace_once(
        text,
        '  function clearViewState() { ["manager_task_state_v241", "manager_task_sort_v241", "manager_selected_task_v241", "owner_review_state", "owner_dashboard_state"].forEach((key) => localStorage.removeItem(key)); }\n',
        '  function clearViewState() { ["task_detail_state", "task_submit_state"].forEach((key) => localStorage.removeItem(key)); }\n',
        "api-client enterprise view state",
    )
    text = replace_once(
        text,
        '    status, failureSummary, getCurrentUserId, setCurrentUserId, currentUser, currentPermissions, can, productFormatters, clearApiCaches, version: API_CLIENT_VERSION,\n',
        '    status, failureSummary, currentUser, currentPermissions, can, productFormatters, clearApiCaches, fixedOperator: FIXED_OPERATOR, version: API_CLIENT_VERSION,\n',
        "api-client public exports",
    )
    account_methods = re.compile(
        r'    accounts: loadAccount,\n'
        r'    me: \(\) => request\("/api/accounts/me"\),\n'
        r'    switchAccount:.*?\n'
        r'    updateUserRole:.*?\n'
        r'    updateUserStores:.*?\n'
        r'    updateStoreAssignment:.*?\n'
        r'    updateRolePermissions:.*?\n',
        re.DOTALL,
    )
    text, count = account_methods.subn("", text, count=1)
    if count != 1:
        raise PatchError(f"api-client account methods removal expected 1, found {count}")
    forbidden = ("ACCOUNT_KEY", "getCurrentUserId", "setCurrentUserId", "/api/accounts", "X-Mock-User-Id", "switchAccount", "applyAccountMutation")
    remaining = [token for token in forbidden if token in text]
    if remaining:
        raise PatchError(f"api-client forbidden account tokens remain: {remaining}")
    write_text(root, relative, text, changed)


def patch_bootstrap(root: Path, changed: set[str]) -> None:
    relative = "web_demo/bootstrap.js"
    path = root / relative
    text = path.read_text(encoding="utf-8")
    marker = "\n(async function () {\n"
    start = text.find(marker)
    if start < 0:
        raise PatchError("bootstrap application IIFE marker missing")
    prefix = text[:start]
    new_tail = r'''

(async function () {
  const ASSET_VERSION = "23.2.11-competition-operator";
  const PAGE_MANIFEST = [
    ["dashboard", "总览", "DashboardPage", "dashboard/page.js"],
    ["data-check", "AI 经营链路", "ReportPage", "report/page.js"],
    ["operating-unit", "经营", "OperatingUnitPage", "operating-unit/page.js"],
    ["business-products", "商品档案", "ProductPage", "product/page.js"],
    ["business-competitors", "竞品信号", "CompetitorPage", "competitor/page.js"],
    ["business-listing", "上新测试", "ListingPage", "listing/page.js"],
    ["business-traffic", "流量趋势", "TrafficPage", "traffic/page.js"],
    ["business-actions", "任务", "TodoPage", "todo/page.js"],
    ["task-report", "任务报告", "TaskReportPage", "task-report/page.js"],
    ["task-submit", "提交任务", "TaskSubmitPage", "task-submit/page.js"],
    ["business-report", "日志", "LogPage", "log/page.js"],
    ["system-status", "系统状态", "SystemStatusPage", "system-status/page.js"],
  ];

  function setApiBadge() {
    const badge = document.getElementById("apiModeBadge");
    if (!badge) return;
    const source = window.AppApi?.status?.source;
    const ok = source === "server";
    badge.textContent = ok ? "后端正常" : source === "unknown" ? "接口检测中" : "接口异常";
    badge.title = window.AppApi?.failureSummary?.() || "接口状态未知";
    badge.classList.toggle("warning", !ok && source !== "unknown");
  }

  PAGE_MANIFEST.forEach(([route, title, globalName, file]) => {
    AppRouter.registerLazy({ route, title, globalName, src: `/web_demo/modules/${file}?v=${ASSET_VERSION}` });
  });

  window.addEventListener("api-client-error", setApiBadge);
  window.addEventListener("api-client-status", setApiBadge);
  window.CompetitionRuntimeActor = Object.freeze({
    actorId: "competition_operator",
    role: "operator",
    workspaceId: "competition_demo",
    serverInjected: true,
  });
  AppRouter.start();
  setApiBadge();
})();
'''
    text = prefix + new_tail
    forbidden = ("accounts", "role-console", "owner", "manager", "finance", "observer", "accountSwitcher", "switchAccount")
    remaining = [token for token in forbidden if token in text]
    if remaining:
        raise PatchError(f"bootstrap forbidden account tokens remain: {remaining}")
    write_text(root, relative, text, changed)


def patch_frontend_shell(root: Path, changed: set[str]) -> None:
    relative = "web_demo/index.html"
    path = root / relative
    text = path.read_text(encoding="utf-8")
    for line in (
        '  <link rel="stylesheet" href="/web_demo/v21-authority-ui.css?v=23.2.11" />\n',
        '        <a href="#accounts" data-route="accounts">账号</a>\n',
        '  <script src="/web_demo/core/v21-authority-ui.js?v=23.2.11"></script>\n',
    ):
        if line not in text:
            raise PatchError(f"index boundary line missing: {line.strip()}")
        text = text.replace(line, "", 1)
    if any(token in text for token in ("#accounts", "role-console", "accountSwitcher", "v21-authority-ui")):
        raise PatchError("index still exposes account/authority UI")
    write_text(root, relative, text, changed)

    router_relative = "web_demo/core/router.js"
    router = (root / router_relative).read_text(encoding="utf-8")
    router = replace_once(
        router,
        '  const aliases = new Map([\n    ["risk-center", "store-overview"], ["executive-cockpit", "store-overview"], ["people-overview", "task-command"],\n  ]);\n',
        '  const aliases = new Map();\n',
        "router enterprise aliases",
    )
    write_text(root, router_relative, router, changed)


def build_product_boundary() -> dict[str, Any]:
    material = {
        "schema": "competition.product_boundary.v1",
        "version": "2026.08.06.3",
        "applicationLoginEnabled": False,
        "applicationAccountSystemEnabled": False,
        "roleSwitchEnabled": False,
        "tenantManagementEnabled": False,
        "clientIdentityOverrideAllowed": False,
        "runtimeActorMode": "fixed_competition_operator",
        "fixedActor": {
            "actorId": "competition_operator",
            "role": "operator",
            "workspaceId": "competition_demo",
            "serverInjected": True,
            "clientOverrideAllowed": False,
        },
        "organizationGovernance": "enterprise_only",
        "enterpriseIdentityProvider": "external_adapter_only",
        "publicRuntimePurpose": "single_operator_agent_business_chain",
        "forbiddenPublicRoutes": [
            "/login",
            "/register",
            "/api/accounts",
            "/api/accounts/switch",
            "/api/approvals",
            "/api/action-authority",
        ],
        "enterpriseCapabilitiesNotEnabled": [
            "owner_account",
            "manager_account",
            "department_accounts",
            "tenant_provisioning",
            "role_management",
            "organization_approval_workflow",
        ],
    }
    return {**material, "boundaryHash": canonical_hash(material)}


def interface_entry(root: Path, value: dict[str, Any]) -> dict[str, Any]:
    entry = dict(value)
    paths = [str(item) for item in entry.get("adapterPaths") or []]
    entry["implementationHashes"] = {
        path: file_hash(root / path) for path in paths if (root / path).is_file()
    }
    material = {key: item for key, item in entry.items() if key != "contractHash"}
    entry["contractHash"] = canonical_hash(material)
    return entry


def build_external_registry(root: Path) -> dict[str, Any]:
    interfaces = {
        "model.inference.aliyun_bailian": interface_entry(root, {
            "capability": "model.inference",
            "provider": "aliyun_bailian",
            "competitionStatus": "PUBLIC_RUNTIME",
            "interfaceAvailable": True,
            "bindingPresent": True,
            "executionEnabled": True,
            "networkEgress": True,
            "adapterPaths": [
                "src/services/llm_gateway_v196_service.py",
                "src/services/llm_gateway_v196_legacy_service.py",
            ],
            "upstreamRegistryModules": ["agent1_runtime", "agent2_runtime", "agent3_runtime"],
            "inputSchema": "openai_compatible.chat_completions.request.v1",
            "outputSchema": "openai_compatible.chat_completions.json_object.v1",
            "credentialSource": ["BAILIAN_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY"],
            "allowedHosts": ["dashscope.aliyuncs.com"],
            "timeoutPolicy": "stage_bounded_timeout",
            "retryPolicy": "bounded_transport_retry_no_business_rebinding",
            "auditPolicy": "provider_model_stage_latency_retry_execution_hash_without_secret",
        }),
        "model.inference.deepseek_compatible": interface_entry(root, {
            "capability": "model.inference",
            "provider": "deepseek",
            "competitionStatus": "OPTIONAL_DISABLED",
            "interfaceAvailable": True,
            "bindingPresent": False,
            "executionEnabled": False,
            "networkEgress": True,
            "adapterPaths": [
                "src/services/llm_gateway_v196_service.py",
                "src/services/llm_gateway_v196_legacy_service.py",
            ],
            "upstreamRegistryModules": [],
            "inputSchema": "openai_compatible.chat_completions.request.v1",
            "outputSchema": "openai_compatible.chat_completions.json_object.v1",
            "credentialSource": [],
            "allowedHosts": ["api.deepseek.com"],
            "timeoutPolicy": "disabled_in_competition",
            "retryPolicy": "disabled_in_competition",
            "auditPolicy": "registration_only",
        }),
        "identity.authentication.aliyun": interface_entry(root, {
            "capability": "identity.authentication",
            "provider": "aliyun_identity",
            "competitionStatus": "ENTERPRISE_ONLY",
            "interfaceAvailable": True,
            "bindingPresent": False,
            "executionEnabled": False,
            "networkEgress": True,
            "adapterPaths": [],
            "upstreamRegistryModules": [],
            "inputSchema": "external_identity_assertion.v1",
            "outputSchema": "trusted_principal_claims.v1",
            "credentialSource": [],
            "allowedHosts": [],
            "timeoutPolicy": "enterprise_configuration_required",
            "retryPolicy": "enterprise_configuration_required",
            "auditPolicy": "enterprise_configuration_required",
        }),
        "object.storage.aliyun_oss": interface_entry(root, {
            "capability": "object.storage",
            "provider": "aliyun_oss",
            "competitionStatus": "ENTERPRISE_ONLY",
            "interfaceAvailable": True,
            "bindingPresent": False,
            "executionEnabled": False,
            "networkEgress": True,
            "adapterPaths": [],
            "upstreamRegistryModules": [],
            "inputSchema": "artifact_object_write.v1",
            "outputSchema": "artifact_object_reference.v1",
            "credentialSource": [],
            "allowedHosts": [],
            "timeoutPolicy": "enterprise_configuration_required",
            "retryPolicy": "enterprise_configuration_required",
            "auditPolicy": "enterprise_configuration_required",
        }),
        "organization.directory.dingtalk": interface_entry(root, {
            "capability": "organization.directory",
            "provider": "dingtalk",
            "competitionStatus": "ENTERPRISE_ONLY",
            "interfaceAvailable": True,
            "bindingPresent": False,
            "executionEnabled": False,
            "networkEgress": True,
            "adapterPaths": [],
            "upstreamRegistryModules": [],
            "inputSchema": "organization_directory_query.v1",
            "outputSchema": "organization_principal_projection.v1",
            "credentialSource": [],
            "allowedHosts": [],
            "timeoutPolicy": "enterprise_configuration_required",
            "retryPolicy": "enterprise_configuration_required",
            "auditPolicy": "enterprise_configuration_required",
        }),
        "erp.report_import": interface_entry(root, {
            "capability": "erp.report_import",
            "provider": "customer_erp_adapter",
            "competitionStatus": "ENTERPRISE_ONLY",
            "interfaceAvailable": True,
            "bindingPresent": False,
            "executionEnabled": False,
            "networkEgress": True,
            "adapterPaths": [],
            "upstreamRegistryModules": [],
            "inputSchema": "erp_report_transport.v1",
            "outputSchema": "canonical_report_artifact.v1",
            "credentialSource": [],
            "allowedHosts": [],
            "timeoutPolicy": "customer_configuration_required",
            "retryPolicy": "customer_configuration_required",
            "auditPolicy": "customer_configuration_required",
        }),
    }
    material = {
        "schema": "competition.external_interface_registry.v1",
        "version": "2026.08.06.1",
        "defaultPolicy": "deny_unregistered_interface",
        "runtimeSelectionAuthority": "registry+hash_lineage+validation_gate",
        "interfaces": interfaces,
    }
    return {**material, "registryHash": canonical_hash(material)}


def patch_scope(root: Path, changed: set[str]) -> None:
    relative = "config/competition_runtime_scope.json"
    value = json.loads((root / relative).read_text(encoding="utf-8"))
    value["version"] = "2026.08.06.3"
    value["description"] = "比赛公开版固定运营工作台。运行文件由业务注册表、外部接口注册表、生产入口导入闭包和前端引用闭包共同确定；未注册接口、账号系统和企业组织治理文件默认拒绝。"
    value["productBoundaryPath"] = "config/competition_product_boundary.json"
    value["externalInterfaceRegistryPath"] = "config/external_interface_registry.json"
    value["seedGlobs"] = [item for item in value.get("seedGlobs", []) if item != "web_demo/**/*"]
    value["staticRoots"] = ["web_demo/index.html"]
    value["forbiddenRuntimePaths"] = [
        "src/api/routes/accounts.py",
        "src/api/routes/action_authority.py",
        "src/api/routes/approvals.py",
        "src/services/account_service.py",
        "web_demo/modules/account/",
        "web_demo/modules/manager/",
        "web_demo/modules/executive/",
        "web_demo/account-center.css",
        "web_demo/account-ui.css",
        "web_demo/v21-authority-ui.css",
        "web_demo/core/v21-authority-ui.js",
    ]
    value["forbiddenRuntimeContent"] = [
        "X-Mock-User-Id",
        "ai_ecommerce_v442_current_user_id",
        "admin123",
        "/api/accounts/switch",
        "role-console",
    ]
    value["networkCallMarkers"] = [
        "urllib.request.urlopen",
        "urllib.request.Request",
        "requests.request",
        "requests.post",
        "httpx.Client",
        "httpx.AsyncClient",
        "aiohttp.ClientSession",
    ]
    rules = value.setdefault("selectionRules", {})
    rules.update({
        "applicationLoginAllowed": False,
        "applicationAccountSystemAllowed": False,
        "clientIdentityOverrideAllowed": False,
        "unregisteredNetworkCallAllowed": False,
        "enabledInterfaceRequiresHashLineage": True,
        "disabledInterfaceMayEnterRuntimeLineage": False,
        "frontendWholeDirectorySeedAllowed": False,
    })
    write_text(root, relative, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), changed)


def interface_gate_source() -> str:
    return r'''#!/usr/bin/env python3
"""External-interface and operator-boundary gate for the competition runtime."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _path_matches(relative: str, configured: str) -> bool:
    clean = configured.rstrip("/")
    return relative == clean or relative.startswith(clean + "/")


def compile_interface_governance(
    root: Path,
    *,
    scope: Mapping[str, Any],
    runtime_paths: Sequence[str] | set[str],
) -> dict[str, Any]:
    findings: list[str] = []
    warnings: list[str] = []
    runtime_set = {str(item) for item in runtime_paths}
    boundary_path = root / str(scope.get("productBoundaryPath") or "")
    registry_path = root / str(scope.get("externalInterfaceRegistryPath") or "")
    if not boundary_path.is_file():
        findings.append(f"COMPETITION_PRODUCT_BOUNDARY_MISSING:{boundary_path}")
        boundary: dict[str, Any] = {}
    else:
        boundary = _read(boundary_path)
    if not registry_path.is_file():
        findings.append(f"EXTERNAL_INTERFACE_REGISTRY_MISSING:{registry_path}")
        registry: dict[str, Any] = {"interfaces": {}}
    else:
        registry = _read(registry_path)

    for field in (
        "applicationLoginEnabled",
        "applicationAccountSystemEnabled",
        "roleSwitchEnabled",
        "tenantManagementEnabled",
        "clientIdentityOverrideAllowed",
    ):
        if boundary.get(field) is not False:
            findings.append(f"PRODUCT_BOUNDARY_REQUIRES_FALSE:{field}")
    actor = boundary.get("fixedActor") or {}
    if boundary.get("runtimeActorMode") != "fixed_competition_operator":
        findings.append("FIXED_OPERATOR_MODE_REQUIRED")
    if actor.get("actorId") != "competition_operator" or actor.get("role") != "operator":
        findings.append("FIXED_OPERATOR_IDENTITY_INVALID")
    if actor.get("serverInjected") is not True or actor.get("clientOverrideAllowed") is not False:
        findings.append("FIXED_OPERATOR_SERVER_BOUNDARY_INVALID")
    boundary_material = {key: value for key, value in boundary.items() if key != "boundaryHash"}
    boundary_hash = _canonical_hash(boundary_material)
    if boundary.get("boundaryHash") != boundary_hash:
        findings.append("PRODUCT_BOUNDARY_HASH_MISMATCH")

    interfaces = registry.get("interfaces")
    if not isinstance(interfaces, dict):
        findings.append("EXTERNAL_INTERFACE_OBJECT_REQUIRED")
        interfaces = {}
    registry_material = {key: value for key, value in registry.items() if key != "registryHash"}
    registry_hash = _canonical_hash(registry_material)
    if registry.get("registryHash") != registry_hash:
        findings.append("EXTERNAL_INTERFACE_REGISTRY_HASH_MISMATCH")

    nodes: list[dict[str, Any]] = []
    edges: list[tuple[str, str, str]] = []
    enabled_adapter_paths: set[str] = set()
    enabled_count = 0
    disabled_count = 0
    for interface_id in sorted(interfaces):
        raw = interfaces.get(interface_id)
        if not isinstance(raw, dict):
            findings.append(f"EXTERNAL_INTERFACE_RECORD_INVALID:{interface_id}")
            continue
        material = {key: value for key, value in raw.items() if key != "contractHash"}
        contract_hash = _canonical_hash(material)
        if raw.get("contractHash") != contract_hash:
            findings.append(f"EXTERNAL_INTERFACE_CONTRACT_HASH_MISMATCH:{interface_id}")
        execution_enabled = raw.get("executionEnabled") is True
        binding_present = raw.get("bindingPresent") is True
        interface_available = raw.get("interfaceAvailable") is True
        adapter_paths = [str(item) for item in raw.get("adapterPaths") or []]
        implementation_hashes = raw.get("implementationHashes") or {}
        if execution_enabled:
            enabled_count += 1
            if not interface_available or not binding_present:
                findings.append(f"ENABLED_INTERFACE_BINDING_INVALID:{interface_id}")
            if not adapter_paths:
                findings.append(f"ENABLED_INTERFACE_ADAPTER_REQUIRED:{interface_id}")
            for relative in adapter_paths:
                path = root / relative
                if not path.is_file():
                    findings.append(f"INTERFACE_ADAPTER_MISSING:{interface_id}:{relative}")
                    continue
                if relative not in runtime_set:
                    findings.append(f"ENABLED_INTERFACE_OUTSIDE_RUNTIME_LINEAGE:{interface_id}:{relative}")
                actual_hash = _file_hash(path)
                if implementation_hashes.get(relative) != actual_hash:
                    findings.append(f"INTERFACE_IMPLEMENTATION_HASH_MISMATCH:{interface_id}:{relative}")
                enabled_adapter_paths.add(relative)
                edges.append((f"interface:{interface_id}", f"file:{relative}", "IMPLEMENTED_BY"))
            if raw.get("networkEgress") is True and not raw.get("allowedHosts"):
                findings.append(f"NETWORK_INTERFACE_HOST_ALLOWLIST_REQUIRED:{interface_id}")
            for module_id in raw.get("upstreamRegistryModules") or []:
                edges.append((f"registry:{module_id}", f"interface:{interface_id}", "USES_INTERFACE"))
        else:
            disabled_count += 1
            if binding_present:
                findings.append(f"DISABLED_INTERFACE_BINDING_PRESENT:{interface_id}")
            if raw.get("competitionStatus") == "PUBLIC_RUNTIME":
                findings.append(f"PUBLIC_RUNTIME_INTERFACE_DISABLED:{interface_id}")
        nodes.append({
            "id": f"interface:{interface_id}",
            "type": "external_interface",
            "interfaceId": interface_id,
            "capability": raw.get("capability"),
            "provider": raw.get("provider"),
            "executionEnabled": execution_enabled,
            "contractHash": contract_hash,
        })

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
        for token in scope.get("forbiddenRuntimeContent") or []:
            if str(token) in text:
                findings.append(f"FORBIDDEN_RUNTIME_CONTENT:{relative}:{token}")
        if relative.startswith("src/") and relative.endswith(".py"):
            markers = [str(item) for item in scope.get("networkCallMarkers") or []]
            if any(marker in text for marker in markers) and relative not in enabled_adapter_paths:
                findings.append(f"UNREGISTERED_NETWORK_CALL:{relative}")

    material = {
        "schema": "competition.interface_governance.v1",
        "productBoundaryPath": str(scope.get("productBoundaryPath") or ""),
        "productBoundaryHash": boundary_hash,
        "externalInterfaceRegistryPath": str(scope.get("externalInterfaceRegistryPath") or ""),
        "externalInterfaceRegistryHash": registry_hash,
        "enabledInterfaceCount": enabled_count,
        "disabledInterfaceCount": disabled_count,
        "enabledAdapterPaths": sorted(enabled_adapter_paths),
        "nodes": nodes,
        "edges": [
            {"from": source, "to": target, "type": edge_type}
            for source, target, edge_type in sorted(set(edges))
        ],
        "findings": sorted(set(findings)),
        "warnings": sorted(set(warnings)),
    }
    return {**material, "verified": not findings, "governanceHash": _canonical_hash(material)}
'''


def patch_compiler(root: Path, changed: set[str]) -> None:
    relative = "scripts/compile_competition_lineage.py"
    text = (root / relative).read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from typing import Any, Mapping, Sequence\n",
        "from typing import Any, Mapping, Sequence\n\nfrom competition_interface_gate import compile_interface_governance\n",
        "compiler interface gate import",
    )
    text = replace_once(
        text,
        "    add_package_initializers(root, runtime_paths)\n\n    for relative in sorted(runtime_paths):\n",
        "    add_package_initializers(root, runtime_paths)\n\n    interface_governance = compile_interface_governance(\n        root, scope=scope, runtime_paths=runtime_paths\n    )\n    findings.extend(interface_governance.get(\"findings\") or [])\n    warnings.extend(interface_governance.get(\"warnings\") or [])\n    for edge in interface_governance.get(\"edges\") or []:\n        edges.add((str(edge[\"from\"]), str(edge[\"to\"]), str(edge[\"type\"])))\n\n    for relative in sorted(runtime_paths):\n",
        "compiler governance invocation",
    )
    text = replace_once(
        text,
        "    graph_material = {\n        \"nodes\": sorted(file_nodes + registry_nodes, key=lambda item: item[\"id\"]),\n        \"edges\": edge_records,\n    }\n",
        "    interface_nodes = list(interface_governance.get(\"nodes\") or [])\n    graph_material = {\n        \"nodes\": sorted(file_nodes + registry_nodes + interface_nodes, key=lambda item: item[\"id\"]),\n        \"edges\": edge_records,\n    }\n",
        "compiler interface graph nodes",
    )
    text = text.replace(
        '        "runtimeHash": runtime_hash,\n',
        '        "runtimeHash": runtime_hash,\n        "productBoundaryHash": interface_governance.get("productBoundaryHash"),\n        "externalInterfaceRegistryHash": interface_governance.get("externalInterfaceRegistryHash"),\n        "interfaceGovernanceHash": interface_governance.get("governanceHash"),\n        "enabledInterfaceCount": interface_governance.get("enabledInterfaceCount"),\n        "disabledInterfaceCount": interface_governance.get("disabledInterfaceCount"),\n',
        3,
    )
    text = replace_once(
        text,
        '        "evidenceManifest": evidence_manifest,\n    }\n',
        '        "evidenceManifest": evidence_manifest,\n        "interfaceGovernance": interface_governance,\n    }\n',
        "compiler return interface evidence",
    )
    text = replace_once(
        text,
        '    write_json(output_dir / "evidence-manifest.json", compiled["evidenceManifest"])\n',
        '    write_json(output_dir / "evidence-manifest.json", compiled["evidenceManifest"])\n    write_json(output_dir / "interface-governance.json", compiled["interfaceGovernance"])\n',
        "compiler write interface evidence",
    )
    ast.parse(text, filename=relative)
    write_text(root, relative, text, changed)


def schema_source() -> str:
    return json.dumps({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "competition.external_interface_registry.v1",
        "title": "Competition External Interface Registry",
        "type": "object",
        "required": ["schema", "version", "defaultPolicy", "interfaces", "registryHash"],
        "properties": {
            "schema": {"const": "competition.external_interface_registry.v1"},
            "version": {"type": "string"},
            "defaultPolicy": {"const": "deny_unregistered_interface"},
            "interfaces": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "required": [
                        "capability", "provider", "competitionStatus",
                        "interfaceAvailable", "bindingPresent", "executionEnabled",
                        "networkEgress", "adapterPaths", "implementationHashes",
                        "inputSchema", "outputSchema", "contractHash",
                    ],
                },
            },
            "registryHash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        },
        "additionalProperties": True,
    }, ensure_ascii=False, sort_keys=True, indent=2)


def boundary_test_source() -> str:
    return r'''from __future__ import annotations

import json
from pathlib import Path

from src.api.main import app, competition_runtime_boundary

ROOT = Path(__file__).resolve().parents[1]


def test_competition_routes_exclude_account_and_department_governance() -> None:
    paths = set((app.openapi().get("paths") or {}).keys())
    forbidden = {
        "/api/accounts",
        "/api/accounts/me",
        "/api/accounts/switch",
        "/api/approvals",
        "/api/action-authority",
        "/login",
        "/register",
    }
    assert not (paths & forbidden), sorted(paths & forbidden)
    assert "/api/competition/runtime-boundary" in paths


def test_fixed_operator_is_server_owned_and_not_a_login_system() -> None:
    value = competition_runtime_boundary()
    boundary = value["productBoundary"]
    actor = value["runtimeActor"]
    assert boundary["applicationLoginEnabled"] is False
    assert boundary["applicationAccountSystemEnabled"] is False
    assert boundary["roleSwitchEnabled"] is False
    assert boundary["tenantManagementEnabled"] is False
    assert boundary["clientIdentityOverrideAllowed"] is False
    assert actor == {
        "actorId": "competition_operator",
        "role": "operator",
        "workspaceId": "competition_demo",
        "serverInjected": True,
        "clientOverrideAllowed": False,
    }


def test_frontend_has_no_mock_identity_or_account_switching() -> None:
    combined = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "web_demo/index.html",
            "web_demo/bootstrap.js",
            "web_demo/core/api-client.js",
        )
    )
    for token in (
        "X-Mock-User-Id",
        "ai_ecommerce_v442_current_user_id",
        "/api/accounts",
        "switchAccount",
        "role-console",
        "admin123",
    ):
        assert token not in combined


def test_external_interfaces_are_default_deny_and_only_bailian_is_enabled() -> None:
    registry = json.loads((ROOT / "config/external_interface_registry.json").read_text(encoding="utf-8"))
    assert registry["defaultPolicy"] == "deny_unregistered_interface"
    enabled = [
        interface_id
        for interface_id, item in registry["interfaces"].items()
        if item["executionEnabled"] is True
    ]
    assert enabled == ["model.inference.aliyun_bailian"]
    for interface_id, item in registry["interfaces"].items():
        if interface_id != "model.inference.aliyun_bailian":
            assert item["bindingPresent"] is False
            assert item["executionEnabled"] is False
'''


def patch_docs(root: Path, changed: set[str]) -> None:
    additions = {
        "README.md": '''\n## 比赛版身份与外部接口边界\n\n公开比赛运行环境不提供应用内登录、账号密码、角色切换或租户创建。页面直接进入由服务端固定注入的运营工作台；`competition_operator` 只是比赛运行身份标签，不宣称具备企业级账号安全或多租户隔离。老板、主管、多部门账号、审批与组织权限属于企业组织协同增值能力。\n\n外部能力执行采用默认拒绝：接口必须先登记到 `config/external_interface_registry.json`，绑定实现 Hash、输入输出合同、凭证来源与允许的网络目标，再进入哈希血缘和发布验证门。当前唯一启用的外部推理接口是阿里云百炼通义千问；身份代理、OSS、钉钉组织目录和 ERP 接入仅登记为企业扩展，比赛运行链路未绑定、未启用。\n''',
        "docs/COMPETITION_CAPABILITY_MATRIX.md": '''\n## 固定运营工作台与企业组织能力边界\n\n| 能力 | 比赛版 | 企业商业交付 |\n|---|---|---|\n| 应用内登录与账号密码 | 不建设、不开放 | 接入云身份服务或客户 SSO/IAM |\n| 固定运营工作台 | 服务端固定注入 `competition_operator` | 标准运营核心 |\n| 老板、主管与多部门角色 | 不进入公开运行链路 | 企业组织协同增值模块 |\n| 多租户、部门权限与审批 | 不宣称已具备安全隔离 | 企业年费或私有化配置 |\n| 外部接口治理 | 注册表、实现 Hash、血缘和门阀全部通过后才可运行 | 统一 Provider / Enterprise Adapter 体系 |\n''',
        "docs/COMPETITION_RELEASE_PLAN.md": '''\n## 运营核心边界收口门\n\n最终比赛包必须满足：应用内登录关闭、Mock 账号系统退出运行闭包、前端不发送用户或租户伪造 Header、老板/主管/审批路由不存在；固定运营身份只能由服务端配置注入。所有出站调用必须具有外部接口注册记录、实现 Hash、允许主机、上游模块和执行状态，未注册网络调用直接使哈希血缘编译失败。\n''',
    }
    for relative, addition in additions.items():
        path = root / relative
        text = path.read_text(encoding="utf-8")
        heading = addition.strip().splitlines()[0]
        if heading not in text:
            text = text.rstrip() + "\n\n" + addition.strip() + "\n"
            write_text(root, relative, text, changed)


def delete_enterprise_surface(root: Path, deleted: set[str]) -> None:
    explicit = [
        "src/api/routes/accounts.py",
        "src/api/routes/action_authority.py",
        "src/api/routes/approvals.py",
        "src/services/account_service.py",
        "web_demo/account-center.css",
        "web_demo/account-ui.css",
        "web_demo/v21-authority-ui.css",
        "web_demo/core/v21-authority-ui.js",
    ]
    for relative in explicit:
        delete_path(root, relative, deleted)
    for base in (
        root / "web_demo/modules/account",
        root / "web_demo/modules/manager",
        root / "web_demo/modules/executive",
    ):
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    delete_path(root, path.relative_to(root).as_posix(), deleted)
    for test in sorted((root / "tests").glob("test_*.py")):
        if test.name == "test_competition_operator_boundary.py":
            continue
        content = test.read_text(encoding="utf-8", errors="ignore")
        if any(token in content for token in (
            "src.services.account_service",
            "src.api.routes.accounts",
            '"/api/accounts',
            "role-console",
            "MANAGEMENT_PASSWORD",
        )):
            delete_path(root, test.relative_to(root).as_posix(), deleted)


def validate(root: Path, changed: set[str], deleted: set[str]) -> None:
    for relative in changed:
        path = root / relative
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        elif path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
    for relative in deleted:
        if (root / relative).exists():
            raise PatchError(f"deleted path still exists: {relative}")
    subprocess.run(
        [sys.executable, "scripts/compile_competition_lineage.py", "--source-commit", os.getenv("EXPECTED_HEAD", "local"), "--output-dir", "dist/operator-boundary-lineage"],
        cwd=root,
        check=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    node = next((candidate for candidate in (
        "/opt/actions-runner-public/externals/node24/bin/node",
        "/opt/actions-runner-public/externals/node20/bin/node",
        "/usr/bin/node",
    ) if Path(candidate).is_file()), None)
    if node:
        for relative in ("web_demo/core/api-client.js", "web_demo/core/router.js", "web_demo/bootstrap.js"):
            subprocess.run([node, "--check", relative], cwd=root, check=True)


def commit_atomic(root: Path, repo: str, branch: str, token: str, expected_head: str, changed: set[str], deleted: set[str]) -> str:
    api = f"https://api.github.com/repos/{repo}"
    ref = api_request(f"{api}/git/ref/heads/{urllib.parse.quote(branch, safe='/')}", token)
    actual_head = ref["object"]["sha"]
    if actual_head != expected_head:
        raise PatchError(f"branch head moved: expected {expected_head}, actual {actual_head}")
    commit = api_request(f"{api}/git/commits/{actual_head}", token)
    base_tree = commit["tree"]["sha"]
    entries: list[dict[str, Any]] = []
    for relative in sorted(changed):
        blob = api_request(
            f"{api}/git/blobs",
            token,
            method="POST",
            payload={"content": base64.b64encode((root / relative).read_bytes()).decode("ascii"), "encoding": "base64"},
        )
        entries.append({"path": relative, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    for relative in sorted(deleted):
        entries.append({"path": relative, "mode": "100644", "type": "blob", "sha": None})
    tree = api_request(f"{api}/git/trees", token, method="POST", payload={"base_tree": base_tree, "tree": entries})
    new_commit = api_request(
        f"{api}/git/commits",
        token,
        method="POST",
        payload={
            "message": "competition: 收口无账号运营核心与外部接口治理",
            "tree": tree["sha"],
            "parents": [actual_head],
        },
    )
    api_request(
        f"{api}/git/refs/heads/{urllib.parse.quote(branch, safe='/')}",
        token,
        method="PATCH",
        payload={"sha": new_commit["sha"], "force": False},
    )
    return new_commit["sha"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    os.environ["EXPECTED_HEAD"] = args.expected_head
    changed: set[str] = set()
    deleted: set[str] = set()

    patch_main(root, changed)
    patch_api_client(root, changed)
    patch_bootstrap(root, changed)
    patch_frontend_shell(root, changed)
    patch_scope(root, changed)
    write_text(root, "config/competition_product_boundary.json", json.dumps(build_product_boundary(), ensure_ascii=False, sort_keys=True, indent=2), changed)
    write_text(root, "config/external_interface_registry.json", json.dumps(build_external_registry(root), ensure_ascii=False, sort_keys=True, indent=2), changed)
    write_text(root, "contracts/registry/external-interface-registry.schema.json", schema_source(), changed)
    write_text(root, "scripts/competition_interface_gate.py", interface_gate_source(), changed)
    patch_compiler(root, changed)
    write_text(root, "tests/test_competition_operator_boundary.py", boundary_test_source(), changed)
    patch_docs(root, changed)
    delete_enterprise_surface(root, deleted)
    validate(root, changed, deleted)
    commit_sha = commit_atomic(root, args.repo, args.branch, args.token, args.expected_head, changed, deleted)
    print(json.dumps({
        "commit": commit_sha,
        "changed": sorted(changed),
        "deleted": sorted(deleted),
        "changedCount": len(changed),
        "deletedCount": len(deleted),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"operator boundary migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
