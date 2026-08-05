"""SQLite metadata repository for immutable operating artifacts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads

ARTIFACT_REPOSITORY_VERSION = "22.2.1"


def now_iso() -> str:
    return datetime.now().isoformat()


def ensure_artifact_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_registry (
                artifact_id TEXT PRIMARY KEY,
                artifact_type TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                storage_uri TEXT NOT NULL,
                tenant_id TEXT,
                store_id TEXT,
                product_id TEXT,
                data_version TEXT,
                created_by TEXT,
                status TEXT NOT NULL DEFAULT 'valid',
                immutable INTEGER NOT NULL DEFAULT 1,
                size_bytes INTEGER DEFAULT 0,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_edges (
                parent_artifact_id TEXT NOT NULL,
                child_artifact_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(parent_artifact_id, child_artifact_id, relation_type),
                FOREIGN KEY(parent_artifact_id) REFERENCES artifact_registry(artifact_id),
                FOREIGN KEY(child_artifact_id) REFERENCES artifact_registry(artifact_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_access_log (
                access_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                actor_id TEXT,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_gc_queue (
                artifact_id TEXT PRIMARY KEY,
                reason TEXT,
                requested_by TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                requested_at TEXT NOT NULL,
                processed_at TEXT
            )
            """
        )
        ensure_columns(
            conn,
            "artifact_registry",
            {
                "schema_version": "TEXT",
                "content_hash": "TEXT",
                "storage_uri": "TEXT",
                "tenant_id": "TEXT",
                "store_id": "TEXT",
                "product_id": "TEXT",
                "data_version": "TEXT",
                "created_by": "TEXT",
                "status": "TEXT DEFAULT 'valid'",
                "immutable": "INTEGER DEFAULT 1",
                "size_bytes": "INTEGER DEFAULT 0",
                "metadata_json": "TEXT",
                "updated_at": "TEXT",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifact_registry_hash ON artifact_registry(artifact_type, content_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifact_registry_scope ON artifact_registry(data_version, store_id, product_id, artifact_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifact_edges_child ON artifact_edges(child_artifact_id, relation_type)"
        )
        conn.commit()


def _row(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    result = dict(row)
    result["metadata"] = loads(result.pop("metadata_json", None))
    result["immutable"] = bool(result.get("immutable"))
    return result


def get_artifact(artifact_id: str) -> Dict[str, Any] | None:
    ensure_artifact_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM artifact_registry WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
    return _row(row) if row else None


def find_artifact_by_hash(
    artifact_type: str,
    content_hash: str,
    *,
    tenant_id: str | None = None,
) -> Dict[str, Any] | None:
    ensure_artifact_tables()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM artifact_registry
            WHERE artifact_type=? AND content_hash=?
              AND COALESCE(tenant_id,'')=COALESCE(?, '')
              AND status='valid'
            ORDER BY created_at DESC LIMIT 1
            """,
            (artifact_type, content_hash, tenant_id),
        ).fetchone()
    return _row(row) if row else None


def upsert_artifact(record: Dict[str, Any]) -> Dict[str, Any]:
    ensure_artifact_tables()
    now = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO artifact_registry (
                artifact_id, artifact_type, schema_version, content_hash, storage_uri,
                tenant_id, store_id, product_id, data_version, created_by, status,
                immutable, size_bytes, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                status=excluded.status,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                record["artifactId"],
                record["artifactType"],
                record["schemaVersion"],
                record["contentHash"],
                record["storageUri"],
                record.get("tenantId"),
                record.get("storeId"),
                record.get("productId"),
                record.get("dataVersion"),
                record.get("createdBy"),
                record.get("status") or "valid",
                1 if record.get("immutable", True) else 0,
                int(record.get("sizeBytes") or 0),
                dumps(record.get("metadata") or {}),
                record.get("createdAt") or now,
                now,
            ),
        )
        conn.commit()
    return get_artifact(record["artifactId"]) or {}


def link_artifacts(
    parent_artifact_id: str,
    child_artifact_id: str,
    relation_type: str = "derived_from",
) -> None:
    ensure_artifact_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO artifact_edges
            (parent_artifact_id, child_artifact_id, relation_type, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (parent_artifact_id, child_artifact_id, relation_type, now_iso()),
        )
        conn.commit()


def artifact_parents(artifact_id: str) -> List[Dict[str, Any]]:
    ensure_artifact_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT e.relation_type, a.*
            FROM artifact_edges e
            JOIN artifact_registry a ON a.artifact_id=e.parent_artifact_id
            WHERE e.child_artifact_id=?
            ORDER BY e.created_at ASC
            """,
            (artifact_id,),
        ).fetchall()
    return [{**_row(row), "relationType": row["relation_type"]} for row in rows]


def artifact_children(artifact_id: str) -> List[Dict[str, Any]]:
    ensure_artifact_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT e.relation_type, a.*
            FROM artifact_edges e
            JOIN artifact_registry a ON a.artifact_id=e.child_artifact_id
            WHERE e.parent_artifact_id=?
            ORDER BY e.created_at ASC
            """,
            (artifact_id,),
        ).fetchall()
    return [{**_row(row), "relationType": row["relation_type"]} for row in rows]


def list_artifacts(
    *,
    data_version: str | None = None,
    artifact_type: str | None = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    ensure_artifact_tables()
    where: List[str] = []
    params: List[Any] = []
    if data_version:
        where.append("data_version=?")
        params.append(data_version)
    if artifact_type:
        where.append("artifact_type=?")
        params.append(artifact_type)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM artifact_registry{clause} ORDER BY created_at DESC LIMIT ?",
            (*params, max(1, min(500, int(limit)))),
        ).fetchall()
    return [_row(row) for row in rows]


__all__ = [
    "ARTIFACT_REPOSITORY_VERSION",
    "ensure_artifact_tables",
    "get_artifact",
    "find_artifact_by_hash",
    "upsert_artifact",
    "link_artifacts",
    "artifact_parents",
    "artifact_children",
    "list_artifacts",
]
