"""Persist full imported report rows for module projection.

V14.5: imported rows keep the permission stamp. Uploading a report grants the
uploader default operating ownership unless ERP/CRM explicitly marks another
owner.
"""

from __future__ import annotations

from sqlite3 import OperationalError
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.report_alert_service import now_iso

IMPORT_ROW_STORE_VERSION = "14.5.0"


def ensure_import_row_table() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imported_report_rows (
                row_id TEXT PRIMARY KEY,
                data_version TEXT NOT NULL,
                dataset_name TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                store_id TEXT,
                permission_stamp_id TEXT,
                uploaded_by_user_id TEXT,
                owner_user_id TEXT,
                assigned_operator_id TEXT,
                visible_user_ids TEXT,
                permission_source TEXT,
                import_batch_id TEXT,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(conn, "imported_report_rows", {"permission_stamp_id": "TEXT", "uploaded_by_user_id": "TEXT", "owner_user_id": "TEXT", "assigned_operator_id": "TEXT", "visible_user_ids": "TEXT", "permission_source": "TEXT", "import_batch_id": "TEXT"})
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imported_rows_version ON imported_report_rows(data_version, row_index)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imported_rows_dataset ON imported_report_rows(dataset_name, data_version)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imported_rows_store ON imported_report_rows(store_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_imported_rows_owner ON imported_report_rows(owner_user_id, uploaded_by_user_id)")
        conn.commit()


def _store_id(row: Dict[str, Any]) -> str | None:
    for key in ["store_id", "storeId", "店铺ID", "店铺id", "店铺编号", "店铺编码"]:
        value = row.get(key)
        if value not in {None, ""}:
            return str(value)
    return None


def _visible_user_ids(row: Dict[str, Any]) -> str | None:
    value = row.get("visibleUserIds")
    if isinstance(value, list):
        return ",".join(str(item) for item in value if item not in {None, ""})
    return str(value) if value not in {None, ""} else None


def save_import_rows(data_version: str, dataset_name: str, rows: List[Dict[str, Any]]) -> None:
    ensure_import_row_table()
    created_at = now_iso()
    with connect() as conn:
        conn.execute("DELETE FROM imported_report_rows WHERE data_version = ?", (data_version,))
        for index, row in enumerate(rows):
            payload = {str(key): value for key, value in row.items()}
            payload.setdefault("dataVersion", data_version)
            payload.setdefault("datasetName", dataset_name)
            stamp = payload.get("permissionStamp") if isinstance(payload.get("permissionStamp"), dict) else {}
            conn.execute(
                """
                INSERT OR REPLACE INTO imported_report_rows (
                    row_id, data_version, dataset_name, row_index, store_id, permission_stamp_id, uploaded_by_user_id, owner_user_id, assigned_operator_id, visible_user_ids, permission_source, import_batch_id, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"{data_version}:{index}", data_version, dataset_name, index, _store_id(payload), payload.get("permissionStampId") or stamp.get("permissionStampId"), payload.get("uploadedByUserId") or stamp.get("uploadedByUserId"), payload.get("ownerUserId") or stamp.get("ownerUserId"), payload.get("assignedOperatorId") or stamp.get("assignedOperatorId"), _visible_user_ids(payload) or _visible_user_ids(stamp), payload.get("permissionSource") or stamp.get("permissionSource"), payload.get("importBatchId") or stamp.get("importBatchId") or data_version, dumps(payload), created_at),
            )
        conn.commit()


def load_import_rows(dataset_name: str | None = None) -> List[Dict[str, Any]]:
    try:
        ensure_import_row_table()
        query = "SELECT * FROM imported_report_rows"
        params: List[Any] = []
        if dataset_name:
            query += " WHERE dataset_name = ?"
            params.append(dataset_name)
        query += " ORDER BY data_version ASC, row_index ASC"
        with connect() as conn:
            rows = conn.execute(query, params).fetchall()
    except OperationalError:
        return []
    result: List[Dict[str, Any]] = []
    for row in rows:
        payload = loads(row["payload"])
        if payload:
            payload.setdefault("dataVersion", row["data_version"])
            payload.setdefault("datasetName", row["dataset_name"])
            if row["store_id"] and not payload.get("storeId"):
                payload["storeId"] = row["store_id"]
            payload.setdefault("permissionStampId", row["permission_stamp_id"])
            payload.setdefault("uploadedByUserId", row["uploaded_by_user_id"])
            payload.setdefault("ownerUserId", row["owner_user_id"])
            payload.setdefault("assignedOperatorId", row["assigned_operator_id"])
            if row["visible_user_ids"] and not payload.get("visibleUserIds"):
                payload["visibleUserIds"] = [item for item in str(row["visible_user_ids"]).split(",") if item]
            payload.setdefault("permissionSource", row["permission_source"])
            payload.setdefault("importBatchId", row["import_batch_id"])
            result.append(payload)
    return result
