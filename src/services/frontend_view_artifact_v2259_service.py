"""V22.5.12 content-addressed frontend view Artifacts with canonical product hash lineage.

DataVersion identifies the business dataset. It does not identify mutable execution state
inside that dataset. This service therefore derives a deterministic runtimeStateHash from
the active pipeline projection plus the current canonical product-set hash and uses that
identity, together with dataVersion, to invalidate frontend manifests.

Immutable module Artifacts remain cacheable by their own business content. The products
module is built only from the canonical product snapshot facade; Signal/Agent admission is
never a product-inventory authority. The manifest carries runtimeStateHash, so a mutable
execution transition or a canonical product-set hash change republishes the Head without
forcing unrelated module content hashes to rotate.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Dict

from src.repositories.sqlite_repository import connect
from src.services.artifact_transport_service import resolve_artifact, store_artifact, validate_artifact

FRONTEND_VIEW_ARTIFACT_VERSION = "22.5.12"
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


def _sha256(value: Any) -> str:
    return "sha256:" + _hash(value)


def _now() -> str:
    return datetime.now().isoformat()


def _scope_key(view_key: str, user_id: str) -> str:
    return f"{view_key.strip() or DEFAULT_VIEW_KEY}::{user_id.strip() or 'competition_operator'}"


def stable_view_payload(value: Any) -> Any:
    """Remove transport timestamps only; retain business and execution identity."""
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
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(frontend_view_head_v2259)").fetchall()
        }
        additions = {
            "runtime_state_hash": "TEXT",
            "pending_runtime_state_hash": "TEXT",
            "previous_runtime_state_hash": "TEXT",
        }
        for column, column_type in additions.items():
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE frontend_view_head_v2259 ADD COLUMN {column} {column_type}"
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_frontend_view_head_scope_v2259 "
            "ON frontend_view_head_v2259(user_id,view_key,data_version,status)"
        )
        conn.commit()


def _latest_data_version() -> str | None:
    """Historical helper only; current operator view authority comes from active runtime."""
    with connect() as conn:
        candidates = []
        for table in ("pipeline_items", "pipeline_jobs", "frontend_product_view"):
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                continue
            row = conn.execute(
                f"""
                SELECT data_version,MAX(updated_at) AS latest_at
                FROM {table}
                WHERE data_version IS NOT NULL AND TRIM(data_version)!=''
                GROUP BY data_version
                ORDER BY latest_at DESC,data_version DESC LIMIT 1
                """
            ).fetchone()
            if row and row["data_version"]:
                candidates.append((str(row["latest_at"] or ""), str(row["data_version"])))
    return max(candidates)[1] if candidates else None


def _canonical_product_runtime_identity(data_version: str | None) -> Dict[str, Any]:
    """Read canonical product-set identity without deserializing product payloads.

    A missing active dataVersion must never fall back to archived canonical rows. This
    metadata-only identity is intentionally small so Head checks remain cheap while still
    invalidating an old/empty products Artifact as soon as the canonical set is committed.
    """
    if not data_version:
        return {
            "ready": False,
            "dataVersion": None,
            "snapshotId": None,
            "setSnapshotHash": None,
            "productCount": 0,
            "authority": "canonical_product_snapshot_sets_v1",
        }
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_product_snapshot_sets_v1' LIMIT 1"
        ).fetchone()
        if not exists:
            row = None
        else:
            row = conn.execute(
                """
                SELECT snapshot_id,data_version,set_snapshot_hash,product_count
                FROM canonical_product_snapshot_sets_v1
                WHERE data_version=?
                ORDER BY julianday(created_at) DESC,rowid DESC
                LIMIT 1
                """,
                (data_version,),
            ).fetchone()
    if not row:
        return {
            "ready": False,
            "dataVersion": data_version,
            "snapshotId": None,
            "setSnapshotHash": None,
            "productCount": 0,
            "authority": "canonical_product_snapshot_sets_v1",
        }
    return {
        "ready": True,
        "dataVersion": str(row["data_version"] or data_version),
        "snapshotId": str(row["snapshot_id"] or "") or None,
        "setSnapshotHash": str(row["set_snapshot_hash"] or "") or None,
        "productCount": int(row["product_count"] or 0),
        "authority": "canonical_product_snapshot_sets_v1",
    }


def _runtime_state(data_version: str | None = None) -> Dict[str, Any]:
    """Return current execution + canonical-product identity from runtime authorities.

    The active pipeline facade deliberately ignores stale caller/history versions and binds
    to imported_report_rows. After Reset it returns dataVersion=None, which invalidates any
    older frontend Head. For an active dataVersion, the canonical product set hash is also
    part of Runtime Truth, so product snapshot materialization cannot leave an empty/stale
    products Artifact behind under an otherwise unchanged pipeline state.
    """
    from src.services.pipeline_live_read_model_v225_service import read_pipeline_live_model

    pipeline = stable_view_payload(
        read_pipeline_live_model(data_version=data_version, limit=100)
    )
    authoritative_version = (
        pipeline.get("activeDataVersion")
        or pipeline.get("dataVersion")
        or None
    )
    canonical_product = _canonical_product_runtime_identity(authoritative_version)
    identity = {
        "dataVersion": authoritative_version,
        "activeDataVersion": pipeline.get("activeDataVersion"),
        "activeDataVersionGate": pipeline.get("activeDataVersionGate"),
        "canonicalProductSnapshot": canonical_product,
        "batchState": pipeline.get("batchState") or {},
        "summary": pipeline.get("summary") or {},
        "stages": pipeline.get("stages") or [],
        "items": pipeline.get("items") or [],
    }
    return {
        "dataVersion": authoritative_version,
        "runtimeStateHash": _sha256(identity),
        "canonicalProductSetSnapshotHash": canonical_product.get("setSnapshotHash"),
        "identity": identity,
        "pipeline": pipeline,
    }


def _empty_current_products() -> Dict[str, Any]:
    return {
        "ready": False,
        "dataVersion": None,
        "items": [],
        "count": 0,
        "productCount": 0,
        "setSnapshotHash": None,
        "rule": "No active imported dataVersion; historical product snapshots are not current view state.",
    }


def _empty_current_tasks() -> Dict[str, Any]:
    return {
        "ready": False,
        "dataVersion": None,
        "items": [],
        "tasks": [],
        "count": 0,
        "rule": "No active imported dataVersion; historical tasks are not current view state.",
    }


def _empty_current_data_line() -> Dict[str, Any]:
    return {
        "ready": False,
        "dataVersion": None,
        "currentDataVersion": None,
        "headline": "等待数据接入",
        "lineStatus": "waiting",
        "stations": [],
        "rule": "No active imported dataVersion; current data line is empty.",
    }


def _module_builders(
    data_version: str | None,
    *,
    pipeline_payload: Dict[str, Any] | None = None,
) -> Dict[str, Callable[[], Dict[str, Any]]]:
    from src.services.frontend_read_model_service import (
        read_dashboard_view,
        read_system_status_view,
    )
    from src.services.pipeline_live_read_model_v225_service import read_pipeline_live_model
    from src.services.public_task_dto_service import project_task_list_response
    from src.services.system_product_snapshot_service import read_canonical_product_views
    from src.services.task_fast_read_model_v2021_service import read_task_fast_views_v2021
    from src.services.task_generation_run_service import read_data_line_status

    active = bool(data_version)
    pipeline = copy.deepcopy(pipeline_payload) if isinstance(pipeline_payload, dict) else None
    return {
        "dashboard": lambda: read_dashboard_view(),
        "products": (
            (lambda: read_canonical_product_views(data_version=data_version, limit=300))
            if active
            else _empty_current_products
        ),
        "tasks": (
            (lambda: project_task_list_response(
                read_task_fast_views_v2021(data_version=data_version, limit=200)
            ))
            if active
            else _empty_current_tasks
        ),
        "pipeline": (
            (lambda: copy.deepcopy(pipeline))
            if pipeline is not None
            else (lambda: read_pipeline_live_model(data_version=data_version, limit=100))
        ),
        "dataLine": (lambda: read_data_line_status()) if active else _empty_current_data_line,
        "systemStatus": lambda: read_system_status_view(),
    }


def _module_source_identity(module_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if module_key != "products":
        return {}
    return {
        "authority": "canonical_product_snapshot_service",
        "dataVersion": payload.get("currentDataVersion") or payload.get("dataVersion"),
        "setSnapshotHash": payload.get("setSnapshotHash"),
        "productCount": int(payload.get("count") or payload.get("productCount") or 0),
        "signalAdmissionIndependent": True,
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
    runtime_state_hash: str,
    previous: Dict[str, Any],
) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO frontend_view_head_v2259 (
                scope_key,view_key,user_id,data_version,pending_data_version,
                manifest_ref,manifest_hash,status,previous_manifest_ref,
                previous_manifest_hash,error,created_at,updated_at,
                runtime_state_hash,pending_runtime_state_hash,previous_runtime_state_hash
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(scope_key) DO UPDATE SET
                pending_data_version=excluded.pending_data_version,
                pending_runtime_state_hash=excluded.pending_runtime_state_hash,
                status='building',
                previous_manifest_ref=frontend_view_head_v2259.manifest_ref,
                previous_manifest_hash=frontend_view_head_v2259.manifest_hash,
                previous_runtime_state_hash=frontend_view_head_v2259.runtime_state_hash,
                error=NULL,
                updated_at=excluded.updated_at
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
                previous.get("runtime_state_hash"),
                runtime_state_hash,
                previous.get("runtime_state_hash"),
            ),
        )
        conn.commit()


def _previous_manifest_modules(previous: Dict[str, Any]) -> Dict[str, Any]:
    ref = str(previous.get("manifest_ref") or "")
    if not ref.startswith("ART-"):
        return {}
    try:
        value = resolve_artifact(ref)
    except Exception:
        return {}
    if not isinstance(value, dict):
        return {}
    modules = value.get("modules")
    return modules if isinstance(modules, dict) else {}


def materialize_frontend_views_v2259(
    *,
    data_version: str | None = None,
    view_key: str = DEFAULT_VIEW_KEY,
    user_id: str = "competition_operator",
    runtime_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    ensure_frontend_view_artifact_tables()
    runtime = runtime_state if isinstance(runtime_state, dict) else _runtime_state(data_version)
    resolved_version = runtime.get("dataVersion")
    runtime_state_hash = str(runtime.get("runtimeStateHash") or "")
    canonical_set_hash = str(runtime.get("canonicalProductSetSnapshotHash") or "") or None
    if not runtime_state_hash.startswith("sha256:"):
        raise RuntimeError("frontend_runtime_state_hash_missing")

    scope_key = _scope_key(view_key, user_id)
    previous = _head_row(scope_key)
    previous_modules = _previous_manifest_modules(previous)
    _write_building_head(
        scope_key=scope_key,
        view_key=view_key,
        user_id=user_id,
        data_version=resolved_version,
        runtime_state_hash=runtime_state_hash,
        previous=previous,
    )
    try:
        modules: Dict[str, Dict[str, Any]] = {}
        module_refs = []
        for module_key, builder in _module_builders(
            resolved_version,
            pipeline_payload=runtime.get("pipeline") if isinstance(runtime.get("pipeline"), dict) else None,
        ).items():
            payload = stable_view_payload(builder())
            source_identity = _module_source_identity(module_key, payload)
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
            if source_identity:
                document["sourceIdentity"] = source_identity
            metadata = {
                "viewKey": view_key,
                "userId": user_id,
                "scopeKey": scope_key,
                "moduleKey": module_key,
                "businessContentHash": document["businessContentHash"],
            }
            if source_identity:
                metadata["sourceSnapshotHash"] = source_identity.get("setSnapshotHash")
                metadata["sourceAuthority"] = source_identity.get("authority")
            artifact = store_artifact(
                artifact_type="frontend_view.module.v2259",
                value=document,
                schema_version=FRONTEND_VIEW_ARTIFACT_VERSION,
                tenant_id=user_id,
                data_version=resolved_version,
                created_by="frontend_view_artifact_v2259",
                metadata=metadata,
            )
            module_ref = str(artifact["artifactId"])
            module_refs.append(module_ref)
            module_record = {
                "artifactRef": module_ref,
                "contentHash": str(artifact["contentHash"]),
                "businessContentHash": document["businessContentHash"],
            }
            if source_identity:
                module_record["sourceSnapshotHash"] = source_identity.get("setSnapshotHash")
                module_record["sourceAuthority"] = source_identity.get("authority")
            modules[module_key] = module_record

        manifest = {
            "schema": "frontend_view.manifest.v2259",
            "version": FRONTEND_VIEW_ARTIFACT_VERSION,
            "viewKey": view_key,
            "userId": user_id,
            "scopeKey": scope_key,
            "dataVersion": resolved_version,
            "runtimeStateHash": runtime_state_hash,
            "canonicalProductSetSnapshotHash": canonical_set_hash,
            "modules": modules,
            "moduleOrder": list(modules),
            "atomicPublication": True,
            "crossDataVersionFallbackAllowed": False,
            "identityRule": "dataVersion identifies data; runtimeStateHash includes execution state plus canonical product-set identity.",
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
                "runtimeStateHash": runtime_state_hash,
                "canonicalProductSetSnapshotHash": canonical_set_hash,
                "moduleCount": len(modules),
            },
        )
        now = _now()
        with connect() as conn:
            conn.execute(
                """
                UPDATE frontend_view_head_v2259
                SET data_version=?,pending_data_version=NULL,
                    runtime_state_hash=?,pending_runtime_state_hash=NULL,
                    manifest_ref=?,manifest_hash=?,status='ready',error=NULL,updated_at=?
                WHERE scope_key=? AND status='building'
                  AND pending_runtime_state_hash=?
                """,
                (
                    resolved_version,
                    runtime_state_hash,
                    artifact["artifactId"],
                    artifact["contentHash"],
                    now,
                    scope_key,
                    runtime_state_hash,
                ),
            )
            changed = int(conn.execute("SELECT changes() AS n").fetchone()["n"] or 0)
            conn.commit()
        if changed != 1:
            raise RuntimeError(f"frontend_view_head_atomic_publish_failed:{scope_key}")

        changed_modules = [
            key
            for key, value in modules.items()
            if value.get("contentHash")
            != (previous_modules.get(key) or {}).get("contentHash")
        ]
        return {
            "version": FRONTEND_VIEW_ARTIFACT_VERSION,
            "status": "ready",
            "viewKey": view_key,
            "userId": user_id,
            "scopeKey": scope_key,
            "dataVersion": resolved_version,
            "runtimeStateHash": runtime_state_hash,
            "canonicalProductSetSnapshotHash": canonical_set_hash,
            "manifestRef": artifact["artifactId"],
            "manifestHash": artifact["contentHash"],
            "modules": modules,
            "changedModules": changed_modules,
            "identityRule": "manifest invalidates on dataVersion, runtime state, or canonical product-set hash change.",
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
    runtime = _runtime_state(data_version)
    requested_version = runtime.get("dataVersion")
    observed_runtime_hash = str(runtime.get("runtimeStateHash") or "")
    observed_product_set_hash = runtime.get("canonicalProductSetSnapshotHash")
    row = _head_row(scope_key)

    needs_materialization = (
        not row
        or not str(row.get("manifest_ref") or "").startswith("ART-")
        or row.get("status") == "failed"
        or (
            row.get("status") != "building"
            and (
                row.get("data_version") != requested_version
                or row.get("runtime_state_hash") != observed_runtime_hash
            )
        )
    )
    if materialize_if_missing and needs_materialization:
        return materialize_frontend_views_v2259(
            data_version=requested_version,
            view_key=view_key,
            user_id=user_id,
            runtime_state=runtime,
        )

    display_mode = "current"
    if row.get("status") == "building":
        display_mode = "previous_snapshot" if row.get("manifest_ref") else "building"
    return {
        "version": FRONTEND_VIEW_ARTIFACT_VERSION,
        "viewKey": view_key,
        "userId": user_id,
        "scopeKey": scope_key,
        "dataVersion": row.get("data_version"),
        "pendingDataVersion": row.get("pending_data_version"),
        "runtimeStateHash": row.get("runtime_state_hash"),
        "pendingRuntimeStateHash": row.get("pending_runtime_state_hash"),
        "observedRuntimeStateHash": observed_runtime_hash,
        "observedCanonicalProductSetSnapshotHash": observed_product_set_hash,
        "manifestRef": row.get("manifest_ref"),
        "manifestHash": row.get("manifest_hash"),
        "status": row.get("status") or "empty",
        "displayMode": display_mode,
        "error": row.get("error"),
        "crossDataVersionFallbackAllowed": False,
        "updatedAt": row.get("updated_at"),
        "identityRule": "Head is current only when dataVersion and the runtime hash (including canonical product-set identity) match Runtime Truth.",
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
        "runtimeStateHash": value.get("runtimeStateHash"),
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
