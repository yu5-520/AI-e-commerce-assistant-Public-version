"""V20.8.5 Legacy Task Chain Cleanup.

This cleanup removes old/illegal task-chain runtime products while preserving the
fact layer. It is intentionally scoped to task output tables and task-admitted
pipeline items, so product master, report facts and metric snapshots remain.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, loads

LEGACY_TASK_CHAIN_CLEANUP_VERSION = "20.8.5"
REQUIRED_CHAIN_STAGES = ["agent1_completed", "action_pack_ready", "agent2_completed", "sop_mapped"]
TASK_OUTPUT_STAGES = ["task_admitted", "task_loop_ready", "read_model_ready"]
TASK_CACHE_TABLES = [
    "task_pool_entries",
    "frontend_task_view",
    "frontend_task_detail_view",
    "task_lifecycle_events",
    "task_lifecycle_snapshots",
    "task_acceptance_events",
    "task_submission_events",
    "task_review_events",
    "task_review_records",
    "task_snapshots",
    "task_pool_snapshots",
    "task_pool_acceptance_records",
    "task_pool_acceptance_snapshots",
]


def now_iso() -> str:
    return datetime.now().isoformat()


def _table_exists(conn: Any, table: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _columns(conn: Any, table: str) -> List[str]:
    if not _table_exists(conn, table):
        return []
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _safe_load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def latest_data_version() -> str | None:
    with connect() as conn:
        if not _table_exists(conn, "pipeline_items"):
            return None
        row = conn.execute(
            """
            SELECT data_version
            FROM pipeline_items
            WHERE data_version IS NOT NULL AND data_version != ''
            GROUP BY data_version
            ORDER BY MAX(updated_at) DESC
            LIMIT 1
            """
        ).fetchone()
        return str(row["data_version"]) if row and row["data_version"] else None


def _event_stages_for_item(conn: Any, item_id: str) -> List[str]:
    if not item_id or not _table_exists(conn, "pipeline_item_events"):
        return []
    cols = _columns(conn, "pipeline_item_events")
    stage_cols = [c for c in ["to_stage", "current_stage", "stage", "event_stage"] if c in cols]
    payload_col = "payload" if "payload" in cols else None
    rows = conn.execute("SELECT * FROM pipeline_item_events WHERE item_id = ? ORDER BY created_at ASC LIMIT 500", (item_id,)).fetchall()
    seen: List[str] = []
    for row in rows:
        for col in stage_cols:
            value = row[col]
            if value and str(value) not in seen:
                seen.append(str(value))
        if payload_col:
            payload = _safe_load(row[payload_col])
            for key in ["stage", "toStage", "to_stage", "currentStage", "current_stage"]:
                value = payload.get(key)
                if value and str(value) not in seen:
                    seen.append(str(value))
    return seen


def chain_integrity_for_item(conn: Any, item_id: str) -> Dict[str, Any]:
    seen = _event_stages_for_item(conn, item_id)
    missing = [stage for stage in REQUIRED_CHAIN_STAGES if stage not in seen]
    return {
        "version": LEGACY_TASK_CHAIN_CLEANUP_VERSION,
        "passed": not missing,
        "missing": missing,
        "seen": seen,
        "rule": "task_admitted is legal only when the same pipeline item has agent1_completed, action_pack_ready, agent2_completed and sop_mapped in its event lineage.",
    }


def _where_for_data_version(conn: Any, table: str, data_version: str | None) -> tuple[str, List[Any]]:
    cols = _columns(conn, table)
    if data_version and "data_version" in cols:
        return "WHERE data_version = ?", [data_version]
    if data_version and "dataVersion" in cols:
        return "WHERE dataVersion = ?", [data_version]
    return "", []


def _count_table(conn: Any, table: str, data_version: str | None = None) -> int:
    if not _table_exists(conn, table):
        return 0
    where, params = _where_for_data_version(conn, table, data_version)
    row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table} {where}", params).fetchone()
    return int(row["cnt"] or 0)


def legacy_task_chain_status(data_version: str | None = None) -> Dict[str, Any]:
    resolved = data_version or latest_data_version()
    with connect() as conn:
        stage_counts: Dict[str, int] = {}
        illegal_items: List[Dict[str, Any]] = []
        task_cache_counts: Dict[str, int] = {}
        if _table_exists(conn, "pipeline_items"):
            if resolved:
                rows = conn.execute(
                    """
                    SELECT current_stage, status, COUNT(*) AS cnt
                    FROM pipeline_items
                    WHERE data_version = ?
                    GROUP BY current_stage, status
                    ORDER BY current_stage, status
                    """,
                    (resolved,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT current_stage, status, COUNT(*) AS cnt
                    FROM pipeline_items
                    GROUP BY current_stage, status
                    ORDER BY current_stage, status
                    """
                ).fetchall()
            for row in rows:
                stage_counts[f"{row['current_stage']}:{row['status']}"] = int(row["cnt"] or 0)

            params: List[Any] = []
            where = "WHERE (current_stage IN ('task_admitted','task_loop_ready','read_model_ready') OR task_id IS NOT NULL)"
            if resolved:
                where += " AND data_version = ?"
                params.append(resolved)
            task_rows = conn.execute(f"SELECT * FROM pipeline_items {where} ORDER BY updated_at DESC LIMIT 500", params).fetchall()
            for row in task_rows:
                integrity = chain_integrity_for_item(conn, row["item_id"])
                if not integrity["passed"]:
                    illegal_items.append({
                        "itemId": row["item_id"],
                        "taskId": row["task_id"] if "task_id" in row.keys() else None,
                        "productId": row["product_id"] if "product_id" in row.keys() else None,
                        "currentStage": row["current_stage"],
                        "status": row["status"],
                        "missing": integrity["missing"],
                        "seen": integrity["seen"],
                    })
        for table in TASK_CACHE_TABLES:
            task_cache_counts[table] = _count_table(conn, table, resolved)
    return {
        "version": LEGACY_TASK_CHAIN_CLEANUP_VERSION,
        "dataVersion": resolved,
        "stageCounts": stage_counts,
        "illegalTaskItemCount": len(illegal_items),
        "illegalTaskItems": illegal_items[:80],
        "taskCacheCounts": task_cache_counts,
        "rule": "Only task-chain runtime products are inspected; fact/product/report tables are not touched.",
    }


def _delete_by_item_ids(conn: Any, table: str, item_ids: Iterable[str]) -> int:
    ids = [item for item in item_ids if item]
    if not ids or not _table_exists(conn, table) or "item_id" not in _columns(conn, table):
        return 0
    deleted = 0
    for index in range(0, len(ids), 100):
        chunk = ids[index : index + 100]
        placeholders = ",".join(["?"] * len(chunk))
        deleted += int(conn.execute(f"DELETE FROM {table} WHERE item_id IN ({placeholders})", chunk).rowcount or 0)
    return deleted


def clear_legacy_task_chain(data_version: str | None = None, *, confirm: bool = False, clear_all_task_outputs: bool = True) -> Dict[str, Any]:
    if not confirm:
        raise ValueError("Set confirm=true to clear legacy task-chain runtime products.")
    resolved = data_version or latest_data_version()
    before = legacy_task_chain_status(resolved)
    deleted: Dict[str, int] = {}
    with connect() as conn:
        illegal_ids = [item["itemId"] for item in before.get("illegalTaskItems") or [] if item.get("itemId")]
        if _table_exists(conn, "pipeline_items"):
            params: List[Any] = []
            if clear_all_task_outputs:
                where = "WHERE (current_stage IN ('task_admitted','task_loop_ready','read_model_ready') OR task_id IS NOT NULL)"
            else:
                where = "WHERE item_id IN ({})".format(",".join(["?"] * len(illegal_ids))) if illegal_ids else "WHERE 1=0"
                params.extend(illegal_ids)
            if resolved:
                where += " AND data_version = ?"
                params.append(resolved)
            rows = conn.execute(f"SELECT item_id FROM pipeline_items {where}", params).fetchall()
            output_ids = [row["item_id"] for row in rows]
            deleted["pipeline_item_events"] = _delete_by_item_ids(conn, "pipeline_item_events", output_ids)
            deleted["pipeline_items"] = int(conn.execute(f"DELETE FROM pipeline_items {where}", params).rowcount or 0)
        else:
            output_ids = []

        for table in TASK_CACHE_TABLES:
            if not _table_exists(conn, table):
                deleted[table] = 0
                continue
            where, params = _where_for_data_version(conn, table, resolved)
            deleted[table] = int(conn.execute(f"DELETE FROM {table} {where}", params).rowcount or 0)
        conn.commit()
    after = legacy_task_chain_status(resolved)
    return {
        "version": LEGACY_TASK_CHAIN_CLEANUP_VERSION,
        "status": "cleared",
        "dataVersion": resolved,
        "clearedAt": now_iso(),
        "clearAllTaskOutputs": clear_all_task_outputs,
        "deleted": deleted,
        "before": before,
        "after": after,
        "preserved": ["report facts", "product master", "store master", "metric snapshots", "agent1_completed pipeline items"],
        "next": "rerun V20 Agent chain from agent1_completed/signal_admitted into action_pack_ready -> agent2_completed -> sop_mapped -> task_admitted.",
        "rule": "V20.8.5 clears old task-chain outputs and cache tables, not fact-layer data.",
    }
