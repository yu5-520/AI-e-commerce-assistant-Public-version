"""V22.5.9 content-addressed frontend view Artifacts.

Backend state changes materialize immutable module documents, then atomically publish
one page manifest head. Frontends compare hashes and download only changed modules.
A previous dataVersion may remain explicitly visible while a new head is building,
but it is never relabelled as the new dataVersion.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Dict

from src.repositories.sqlite_repository import connect
from src.services.artifact_transport_service import resolve_artifact, store_artifact, validate_artifact

FRONTEND_VIEW_ARTIFACT_VERSION = "22.5.9"
DEFAULT_VIEW_KEY = "operator-center"

_VOLATILE_VIEW_KEYS = {
    "generatedAt",
    "updatedAt",
    "cachedAt",
    "lifecycleUpdatedAt",
    "cacheAgeMs",
    "refreshing",
    "lastTickAt",
    "latencyMs",
    "providerRequestId",
    "startedAt",
    "finishedAt",
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now().isoformat()


def _scope_key(view_key: str, user_id: str) -> str:
    return f"{view_key.strip() or DEFAULT_VIEW_KEY}::{user_id.strip() or 'competition_operator'}"


def stable_view_payload(value: Any) -> Any:
    """Remove transport timestamps only; retain dataVersion and business identity."""
    if isinstance(value, list):
        return [stable_view_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): stable_view_payload(item)
            for key, item in sorted(value.items())
            if key not in _VOLATILE_VIEW_KEYS
        }
    return value


def ensure_frontend_view_artifact_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS frontend_view_head_v2259 (
                scope_key TEXT PRIMARY KEY,
                view_key TEXT NOT NULL,
                user_id TEXT NOT NULL,
                data_version TEXT,
                pending_data_version TEXT,
                manifest_ref TEXT,
                manifest_hash TEXT,
                status TEXT NOT NULL,
                previous_manifest_ref TEXT,
                previous_manifest_hash TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_frontend_view_head_scope_v2259 ON frontend_view_head_v2259(user_id,view_key,data_version,status)"
        )
        conn.commit()


def _latest_data_version() -> str | None:
    with connect() as conn:
        candidates = []
        for table in ("pipeline_items", "pipeline_jobs", "frontend_product_view"):
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                continue
            column = "data_version"
            row = conn.execute(
                f"""
                SELECT {column} AS data_version,MAX(updated_at) AS latest_at
                FROM {table}
                WHERE {column} IS NOT NULL AND TRIM({column})!=''
                GROUP BY {column}
                ORDER BY latest_at DESC,{column} DESC LIMIT 1
                """
            ).fetchone()
            if row and row["data_version"]:
                candidates.append((str(row["latest_at"] or ""), str(row["data_version"])))
    return max(candidates)[1] if candidates else None


def _module_builders(data_version: str | None) -> Dict[str, Callable[[], Dict[str, Any]]]:
    from src.services.frontend_read_model_service import (
        read_dashboard_view,
        read_product_views,
        read_system_status_view,
    )
    from src.services.pipeline_live_read_model_v225_service import read_pipeline_live_model
    from src.services.public_task_dto_service import project_task_list_response
    from src.services.task_fast_read_model_v2021_service import read_task_fast_views_v2021
    from src.services.task_generation_run_service import read_data_line_status

    return {
        "dashboard": lambda: read_dashboard_view(),
        "products": lambda: read_product_views(data_version=data_version, limit=300),
        "tasks": lambda: project_task_list_response(
            read_task_fast_views_v2021(data_version=data_version, limit=200)
        ),
        "pipeline": lambda: read_pipeline_live_model(data_version=data_version, limit=100),
        "dataLine": lambda: read_data_line_status(),
        "systemStatus": lambda: read_system_status_view(),
    }


def _head_row(scope_key: str) -> Dict[str, Any]:
    ensure_frontend_view_artifact_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM frontend_view_head_v2259 WHERE scope_key=?",
            (scope_key,),
        ).fetchone()
    return dict(row) if row else {}


def _write_building_head(
    *,
    scope_key: str,
    view_key: str,
    user_id: str,
    data_version: str | None,
    previous: Dict[str, Any],
) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO frontend_view_head_v2259 (
                scope_key,view_key,user_id,data_version,pending_data_version,
                manifest_ref,manifest_hash,status,previous_manifest_ref,
                previous_manifest_hash,error,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(scope_key) DO UPDATE SET
                pending_data_version=excluded.pending_data_version,status='building',
                previous_manifest_ref=frontend_view_head_v2259.manifest_ref,
                previous_manifest_hash=frontend_view_head_v2259.manifest_hash,
                error=NULL,updated_at=excluded.updated_at
            """,
            (
                scope_key,
                view_key,
                user_id,
                previous.get("data_version"),
                data_version,
                previous.get("manifest_ref"),
                previous.get("manifest_hash"),
                "building",
                previous.get("manifest_ref"),
                previous.get("manifest_hash"),
                None,
                now,
                now,
            ),
        )
        conn.commit()


def materialize_frontend_views_v2259(
    *,
    data_version: str | None = None,
    view_key: str = DEFAULT_VIEW_KEY,
    user_id: str = "competition_operator",
) -> Dict[str, Any]:
    ensure_frontend_view_artifact_tables()
    resolved_version = data_version or _latest_data_version()
    scope_key = _scope_key(view_key, user_id)
    previous = _head_row(scope_key)
    _write_building_head(
        scope_key=scope_key,
        view_key=view_key,
        user_id=user_id,
        data_version=resolved_version,
        previous=previous,
    )
    try:
        modules: Dict[str, Dict[str, Any]] = {}
        module_refs = []
        for module_key, builder in _module_builders(resolved_version).items():
            payload = stable_view_payload(builder())
            document = {
                "schema": "frontend_view.module.v2259",
                "version": FRONTEND_VIEW_ARTIFACT_VERSION,
                "viewKey": view_key,
                "userId": user_id,
                "scopeKey": scope_key,
                "moduleKey": module_key,
                "dataVersion": resolved_version,
                "payload": payload,
                "businessContentHash": _hash(payload),
            }
            artifact = store_artifact(
                artifact_type="frontend_view.module.v2259",
                value=document,
                schema_version=FRONTEND_VIEW_ARTIFACT_VERSION,
                tenant_id=user_id,
                data_version=resolved_version,
                created_by="frontend_view_artifact_v2259",
                metadata={
                    "viewKey": view_key,
                    "userId": user_id,
                    "scopeKey": scope_key,
                    "moduleKey": module_key,
                    "businessContentHash": document["businessContentHash"],
                },
            )
            module_ref = str(artifact["artifactId"])
            module_refs.append(module_ref)
            modules[module_key] = {
                "artifactRef": module_ref,
                "contentHash": str(artifact["contentHash"]),
                "businessContentHash": document["businessContentHash"],
            }

        manifest = {
            "schema": "frontend_view.manifest.v2259",
            "version": FRONTEND_VIEW_ARTIFACT_VERSION,
            "viewKey": view_key,
            "userId": user_id,
            "scopeKey": scope_key,
            "dataVersion": resolved_version,
            "modules": modules,
            "moduleOrder": list(modules),
            "atomicPublication": True,
            "crossDataVersionFallbackAllowed": False,
        }
        artifact = store_artifact(
            artifact_type="frontend_view.manifest.v2259",
            value=manifest,
            schema_version=FRONTEND_VIEW_ARTIFACT_VERSION,
            tenant_id=user_id,
            data_version=resolved_version,
            created_by="frontend_view_artifact_v2259",
            parent_refs=module_refs,
            metadata={
                "viewKey": view_key,
                "userId": user_id,
                "scopeKey": scope_key,
                "moduleCount": len(modules),
            },
        )
        now = _now()
        with connect() as conn:
            conn.execute(
                """
                UPDATE frontend_view_head_v2259
                SET data_version=?,pending_data_version=NULL,manifest_ref=?,manifest_hash=?,
                    status='ready',error=NULL,updated_at=?
                WHERE scope_key=? AND status='building'
                """,
                (
                    resolved_version,
                    artifact["artifactId"],
                    artifact["contentHash"],
                    now,
                    scope_key,
                ),
            )
            changed = int(conn.execute("SELECT changes() AS n").fetchone()["n"] or 0)
            conn.commit()
        if changed != 1:
            raise RuntimeError(f"frontend_view_head_atomic_publish_failed:{scope_key}")
        return {
            "version": FRONTEND_VIEW_ARTIFACT_VERSION,
            "status": "ready",
            "viewKey": view_key,
            "userId": user_id,
            "scopeKey": scope_key,
            "dataVersion": resolved_version,
            "manifestRef": artifact["artifactId"],
            "manifestHash": artifact["contentHash"],
            "modules": modules,
            "changedModules": [
                key
                for key, value in modules.items()
                if value.get("contentHash")
                != (((resolve_artifact(str(previous.get("manifest_ref"))) if str(previous.get("manifest_ref") or "").startswith("ART-") else {}).get("modules") or {}).get(key) or {}).get("contentHash")
            ],
        }
    except Exception as exc:
        with connect() as conn:
            conn.execute(
                """
                UPDATE frontend_view_head_v2259
                SET status='failed',error=?,updated_at=? WHERE scope_key=?
                """,
                (str(exc)[:1000], _now(), scope_key),
            )
            conn.commit()
        raise


def get_frontend_view_head_v2259(
    *,
    view_key: str = DEFAULT_VIEW_KEY,
    user_id: str = "competition_operator",
    data_version: str | None = None,
    materialize_if_missing: bool = True,
) -> Dict[str, Any]:
    scope_key = _scope_key(view_key, user_id)
    requested_version = data_version or _latest_data_version()
    row = _head_row(scope_key)
    if materialize_if_missing and (
        not row
        or not str(row.get("manifest_ref") or "").startswith("ART-")
        or (requested_version and row.get("data_version") != requested_version and row.get("status") != "building")
    ):
        return materialize_frontend_views_v2259(
            data_version=requested_version,
            view_key=view_key,
            user_id=user_id,
        )
    display_mode = "current"
    if row.get("status") == "building" and row.get("pending_data_version"):
        display_mode = "previous_snapshot" if row.get("manifest_ref") else "building"
    return {
        "version": FRONTEND_VIEW_ARTIFACT_VERSION,
        "viewKey": view_key,
        "userId": user_id,
        "scopeKey": scope_key,
        "dataVersion": row.get("data_version"),
        "pendingDataVersion": row.get("pending_data_version"),
        "manifestRef": row.get("manifest_ref"),
        "manifestHash": row.get("manifest_hash"),
        "status": row.get("status") or "empty",
        "displayMode": display_mode,
        "error": row.get("error"),
        "crossDataVersionFallbackAllowed": False,
        "updatedAt": row.get("updated_at"),
    }


def read_frontend_view_artifact_v2259(
    artifact_ref: str,
    *,
    view_key: str = DEFAULT_VIEW_KEY,
    user_id: str = "competition_operator",
) -> Dict[str, Any]:
    scope_key = _scope_key(view_key, user_id)
    row = _head_row(scope_key)
    manifest_ref = str(row.get("manifest_ref") or "")
    if not manifest_ref.startswith("ART-"):
        raise KeyError(f"frontend_view_manifest_missing:{scope_key}")
    manifest = resolve_artifact(manifest_ref)
    if not isinstance(manifest, dict) or manifest.get("scopeKey") != scope_key:
        raise PermissionError(f"frontend_view_manifest_scope_mismatch:{scope_key}")
    allowed = {manifest_ref}
    for item in (manifest.get("modules") or {}).values():
        if isinstance(item, dict) and str(item.get("artifactRef") or "").startswith("ART-"):
            allowed.add(str(item["artifactRef"]))
    if artifact_ref not in allowed:
        raise PermissionError(f"frontend_view_artifact_not_authorized:{artifact_ref}")
    validation = validate_artifact(artifact_ref)
    if validation.get("ok") is not True:
        raise RuntimeError(f"frontend_view_artifact_invalid:{artifact_ref}:{validation.get('status')}")
    value = resolve_artifact(artifact_ref)
    if not isinstance(value, dict) or value.get("scopeKey") != scope_key:
        raise PermissionError(f"frontend_view_artifact_scope_mismatch:{artifact_ref}")
    return {
        "version": FRONTEND_VIEW_ARTIFACT_VERSION,
        "artifactRef": artifact_ref,
        "contentHash": validation.get("contentHash"),
        "dataVersion": value.get("dataVersion"),
        "moduleKey": value.get("moduleKey"),
        "payload": value.get("payload") if value.get("schema") == "frontend_view.module.v2259" else value,
        "immutable": True,
    }


__all__ = [
    "FRONTEND_VIEW_ARTIFACT_VERSION",
    "DEFAULT_VIEW_KEY",
    "stable_view_payload",
    "ensure_frontend_view_artifact_tables",
    "materialize_frontend_views_v2259",
    "get_frontend_view_head_v2259",
    "read_frontend_view_artifact_v2259",
]
