"""V11.14 operating object backfill and runtime diagnostics.

This service fixes deployments where historical report rows or demo/API sync rows
exist, but operating_products / operating_stores were not filled by older chains.
Backfill is an explicit system operation and does not restore legacy task rules.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Tuple

from src.core.context import UserContext
from src.repositories.sqlite_repository import DB_PATH, connect
from src.services.data_import_service import DATASET_CONFIGS, read_csv
from src.services.import_row_store_service import load_import_rows
from src.services.operating_object_store_service import list_operating_products, list_operating_stores, operating_object_summary, upsert_operating_objects_from_import
from src.services.report_alert_service import now_iso

BACKFILL_VERSION = "11.14.0"
BACKFILL_DATASETS = ["products", "orders", "inventory", "refunds"]
DIAGNOSTIC_TABLES = [
    "import_records",
    "report_records",
    "imported_report_rows",
    "data_snapshots",
    "metric_snapshots",
    "business_signals_v6",
    "operating_products",
    "operating_stores",
    "task_status",
    "task_assignments",
    "task_submissions",
    "task_reviews",
    "alert_events",
]
SOURCE_TABLES = ["import_records", "report_records", "imported_report_rows", "data_snapshots", "metric_snapshots"]
DERIVED_TABLES = ["business_signals_v6", "operating_products", "operating_stores", "task_status", "task_assignments", "task_submissions", "task_reviews", "alert_events"]


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
    return bool(row)


def _table_count(conn, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    return int(row["count"] if row else 0)


def _latest_value(conn, table_name: str, column: str) -> str | None:
    if not _table_exists(conn, table_name):
        return None
    try:
        row = conn.execute(f"SELECT MAX({column}) AS latest FROM {table_name}").fetchone()
    except Exception:
        return None
    return row["latest"] if row else None


def _normalize_row(row: Dict[str, Any], *, dataset_name: str, data_version: str) -> Dict[str, Any]:
    payload = {str(key): value for key, value in row.items()}
    payload.setdefault("datasetName", dataset_name)
    payload.setdefault("dataVersion", data_version)
    return payload


def _fallback_example_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    stamp = now_iso().replace(":", "").replace(".", "")[:15]
    for dataset_name in BACKFILL_DATASETS:
        if dataset_name not in DATASET_CONFIGS:
            continue
        for row in read_csv(str(DATASET_CONFIGS[dataset_name]["filename"])):
            rows.append(_normalize_row(row, dataset_name=dataset_name, data_version=f"BACKFILL_{dataset_name}_{stamp}"))
    return rows


def _materialize_backfill_rows() -> Dict[str, Any]:
    rows = load_import_rows()
    source = "imported_report_rows"
    if not rows:
        rows = _fallback_example_rows()
        source = "examples_fallback"
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dataset_name = str(row.get("datasetName") or row.get("dataset_name") or "backfill")
        data_version = str(row.get("dataVersion") or row.get("data_version") or f"BACKFILL_{dataset_name}")
        normalized.append(_normalize_row(row, dataset_name=dataset_name, data_version=data_version))
    return {"source": source, "rows": normalized}


def _group_results(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dataset_name = str(row.get("datasetName") or "backfill")
        data_version = str(row.get("dataVersion") or f"BACKFILL_{dataset_name}")
        groups[(dataset_name, data_version)].append(row)
    return [{"datasetName": dataset_name, "dataVersion": data_version, "rowCount": len(items), "rows": items} for (dataset_name, data_version), items in groups.items()]


def runtime_diagnostics(ctx: UserContext) -> Dict[str, Any]:
    with connect() as conn:
        table_counts = {name: _table_count(conn, name) for name in DIAGNOSTIC_TABLES}
        latest = {
            "importedReportRows": _latest_value(conn, "imported_report_rows", "created_at"),
            "operatingProducts": _latest_value(conn, "operating_products", "updated_at"),
            "operatingStores": _latest_value(conn, "operating_stores", "updated_at"),
        }
    visible_products = list_operating_products(ctx.user_id)
    visible_stores = list_operating_stores(ctx.user_id)
    object_summary = operating_object_summary(ctx.user_id)
    imported_rows = table_counts.get("imported_report_rows", 0)
    total_object_rows = table_counts.get("operating_products", 0) + table_counts.get("operating_stores", 0)
    source_rows = sum(table_counts.get(name, 0) for name in SOURCE_TABLES)
    derived_rows = sum(table_counts.get(name, 0) for name in DERIVED_TABLES)
    dirty_runtime_residue = source_rows == 0 and derived_rows > 0
    object_sync_failed = imported_rows > 0 and total_object_rows == 0
    visible_object_empty = imported_rows > 0 and not visible_products and not visible_stores
    if dirty_runtime_residue:
        status = "dirty_runtime_residue"
    elif object_sync_failed:
        status = "object_sync_failed"
    elif visible_object_empty:
        status = "visible_empty"
    else:
        status = "ok"
    return {
        "version": BACKFILL_VERSION,
        "status": status,
        "database": {"type": "sqlite", "path": str(DB_PATH)},
        "currentContext": ctx.audit_meta(),
        "tableCounts": table_counts,
        "visibleCounts": {"products": len(visible_products), "stores": len(visible_stores)},
        "objectSummary": object_summary,
        "latest": latest,
        "objectSyncFailed": object_sync_failed,
        "visibleObjectEmpty": visible_object_empty,
        "dirtyRuntimeResidue": dirty_runtime_residue,
        "orphanDerivedCount": derived_rows if dirty_runtime_residue else 0,
        "orphanSignalCount": table_counts.get("business_signals_v6", 0) if dirty_runtime_residue else 0,
        "canBackfill": (not dirty_runtime_residue) and (imported_rows > 0 or total_object_rows == 0),
        "canClearRuntime": dirty_runtime_residue or derived_rows > 0 or source_rows > 0,
        "rule": "系统运行态以完整导入链路为准；清空测试数据必须同步删除导入行、快照、信号、任务、日志和经营对象主档。",
    }


def backfill_operating_objects(ctx: UserContext) -> Dict[str, Any]:
    before = runtime_diagnostics(ctx)
    if before.get("dirtyRuntimeResidue"):
        return {
            "version": BACKFILL_VERSION,
            "status": "blocked_dirty_runtime_residue",
            "source": "runtime_diagnostics",
            "rowCount": 0,
            "datasetCount": 0,
            "operatingObjectSync": {"productUpsertCount": 0, "storeUpsertCount": 0},
            "before": before,
            "after": before,
            "rule": "检测到报表源数据已清空但派生经营对象/信号仍残留。先执行清空演示环境，不允许用回填继续叠脏数据。",
        }
    materialized = _materialize_backfill_rows()
    rows: List[Dict[str, Any]] = materialized["rows"]
    result = {"version": BACKFILL_VERSION, "mode": "operating_object_backfill", "results": _group_results(rows), "rows": rows}
    sync = upsert_operating_objects_from_import(
        result,
        rows,
        source=f"v1114_backfill_{materialized['source']}",
        uploader_user_id=ctx.user_id,
        uploader_role_id=ctx.role_id,
    )
    after = runtime_diagnostics(ctx)
    return {
        "version": BACKFILL_VERSION,
        "status": "completed" if rows else "no_rows",
        "source": materialized["source"],
        "rowCount": len(rows),
        "datasetCount": len(result["results"]),
        "operatingObjectSync": sync,
        "before": before,
        "after": after,
        "rule": "回填只补经营对象主档，不恢复旧任务生成规则。",
    }
