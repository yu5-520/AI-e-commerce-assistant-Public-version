"""V16.24 scoped repository primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class UserContext:
    user_id: str = "competition_operator"
    tenant_id: str = "competition_demo"
    org_id: str = "competition_demo"
    role_id: str = "operator"
    store_group_ids: list[str] = field(default_factory=list)
    store_ids: list[str] = field(default_factory=lambda: ["COMP-STORE-1"])
    strict_scope: bool = False

    @classmethod
    def from_any(cls, value: Any | None) -> "UserContext":
        """Return the server-owned competition scope and ignore client identity."""
        if isinstance(value, cls):
            store_ids = list(value.store_ids or ["COMP-STORE-1"])
        elif isinstance(value, dict):
            store_ids = _as_list(value.get("store_ids") or value.get("storeIds") or ["COMP-STORE-1"])
        else:
            store_ids = _as_list(getattr(value, "store_ids", ["COMP-STORE-1"])) if value is not None else ["COMP-STORE-1"]
        return cls(store_ids=store_ids or ["COMP-STORE-1"])



def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item not in {None, ""}]
    return [str(value)]


@dataclass(frozen=True)
class ScopeFilter:
    tenant_id: str
    org_id: str
    store_group_ids: list[str] = field(default_factory=list)
    store_ids: list[str] = field(default_factory=list)
    role_id: str = "observer"
    include_deleted: bool = False
    strict_scope: bool = False

    @classmethod
    def from_context(cls, ctx: Any, *, include_deleted: bool = False) -> "ScopeFilter":
        ctx = UserContext.from_any(ctx)
        return cls(ctx.tenant_id, ctx.org_id, list(ctx.store_group_ids), list(ctx.store_ids), ctx.role_id, include_deleted, ctx.strict_scope)


@dataclass(frozen=True)
class ScopedQueryPlan:
    where: list[str]
    params: dict[str, Any]
    rule: str = "tenant + org + soft delete + role data scope"


def query_plan_for_context(ctx: Any, *, table_alias: str = "resource", include_deleted: bool = False) -> ScopedQueryPlan:
    ctx = UserContext.from_any(ctx)
    prefix = f"{table_alias}." if table_alias else ""
    where = [f"{prefix}tenant_id = :tenant_id", f"{prefix}org_id = :org_id"]
    params: dict[str, Any] = {"tenant_id": ctx.tenant_id, "org_id": ctx.org_id}
    if not include_deleted:
        where.append(f"{prefix}deleted_at IS NULL")
    where.append(f"{prefix}store_id IN :store_ids")
    params["store_ids"] = tuple(ctx.store_ids or ["__none__"])
    return ScopedQueryPlan(where=where, params=params)


def _pick(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if item.get(name) not in {None, ""}:
            return item.get(name)
    return None


def item_visible_to_context(
    item: dict[str, Any],
    ctx: Any,
    *,
    include_deleted: bool = False,
    tenant_fields: tuple[str, ...] = ("tenantId", "tenant_id"),
    org_fields: tuple[str, ...] = ("orgId", "org_id"),
    store_fields: tuple[str, ...] = ("storeId", "store_id"),
    store_group_fields: tuple[str, ...] = ("storeGroupId", "store_group_id", "groupId", "group_id"),
    deleted_fields: tuple[str, ...] = ("deletedAt", "deleted_at"),
) -> bool:
    ctx = UserContext.from_any(ctx)
    tenant_id = _pick(item, *tenant_fields)
    org_id = _pick(item, *org_fields)
    if ctx.strict_scope and not tenant_id:
        return False
    if tenant_id and tenant_id != ctx.tenant_id:
        return False
    if ctx.strict_scope and not org_id:
        return False
    if org_id and org_id != ctx.org_id:
        return False
    if not include_deleted and _pick(item, *deleted_fields):
        return False
    store_id = _pick(item, *store_fields)
    if ctx.strict_scope and not store_id:
        return False
    return (not ctx.strict_scope and not store_id) or store_id in set(ctx.store_ids)


def filter_visible_items(items: Iterable[dict[str, Any]], ctx: Any, *, include_deleted: bool = False) -> list[dict[str, Any]]:
    return [item for item in items if item_visible_to_context(item, ctx, include_deleted=include_deleted)]


class ScopedRepositoryBase:
    resource_name = "resource"

    def __init__(self, ctx: Any | None = None) -> None:
        self.ctx = UserContext.from_any(ctx)
        self.scope = ScopeFilter.from_context(self.ctx)

    def query_plan(self, *, table_alias: str | None = None, include_deleted: bool = False) -> ScopedQueryPlan:
        return query_plan_for_context(self.ctx, table_alias=table_alias or self.resource_name, include_deleted=include_deleted)

    def filter_demo_rows(self, rows: Iterable[dict[str, Any]], *, include_deleted: bool = False) -> list[dict[str, Any]]:
        return filter_visible_items(rows, self.ctx, include_deleted=include_deleted)
