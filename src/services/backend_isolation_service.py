# Competition fixed-workspace data-scope helpers.
# The public runtime accepts no client-selected identity and pins the demo namespace.
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
