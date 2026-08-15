"""System status and generation-safe demo reset service.

V23.3 upgrades the product-facing Reset from a best-effort table cleanup to a
registry-driven Runtime Generation Barrier. The mutable runtime tables are still
physically cleared, but deletion happens only while the same exclusive barrier used
by the single Worker is held. Canonical history, immutable Artifacts, execution audit
and semantic caches remain outside the current-runtime reset policy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from src.repositories.sqlite_repository import DB_PATH, connect, init_db
from src.services.module_task_service import reset_tasks
from src.services.runtime_generation_barrier_v1_service import (
    RUNTIME_GENERATION_VERSION,
    current_runtime_generation,
    registered_reset_table_names,
    reset_policy_summary,
    runtime_reset_barrier,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT_DIR / "logs"
JSONL_LOG_FILES = [
    LOG_DIR / "workflow_runs.jsonl",
    LOG_DIR / "execution_logs.jsonl",
    LOG_DIR / "data_import_records.jsonl",
    LOG_DIR / "approval_records.jsonl",
]

V5_RUNTIME_RESET_MARKER = "v5_0_3_runtime_empty_state_applied"
V1114_RUNTIME_RESET_MARKER = "v11_14_runtime_full_demo_reset_applied"
V121_RUNTIME_RESET_MARKER = "v12_1_metric_fact_runtime_reset_applied"
V1213_RUNTIME_RESET_MARKER = "v12_1_3_data_gap_runtime_reset_applied"
V124_RUNTIME_RESET_MARKER = "v12_4_operating_cadence_runtime_reset_applied"
V12131_RUNTIME_RESET_MARKER = "v12_1_3_pipeline_snapshot_runtime_reset_applied"
V12140_RUNTIME_RESET_MARKER = "v12_14_station_ops_runtime_reset_applied"
V131_RUNTIME_RESET_MARKER = "v13_1_snapshot_task_handoff_runtime_reset_applied"
V133_RUNTIME_RESET_MARKER = "v13_3_task_snapshot_runtime_reset_applied"
V134_RUNTIME_RESET_MARKER = "v13_4_task_pool_runtime_reset_applied"
V1442_RUNTIME_RESET_MARKER = "v14_4_2_full_runtime_reset_applied"
V146_RUNTIME_RESET_MARKER = "v14_6_station_queue_runtime_reset_applied"
V148_RUNTIME_RESET_MARKER = "v14_8_frontend_read_model_runtime_reset_applied"
V1491_RUNTIME_RESET_MARKER = "v14_9_1_dual_agent_runtime_reset_applied"
V15_RUNTIME_RESET_MARKER = "v15_agent_budget_ledger_runtime_reset_applied"
V2092_DEMO_RESET_MARKER = "v20_9_2_demo_environment_full_reset_applied"
V233_RUNTIME_GENERATION_RESET_MARKER = "v23_3_runtime_generation_barrier_applied"
SYSTEM_SERVICE_VERSION = "23.3.0"

TABLES = [
    {"table_name": "workflow_runs", "time_expression": "COALESCE(MAX(finished_at), MAX(started_at))"},
    {"table_name": "execution_logs", "time_expression": "MAX(created_at)"},
    {"table_name": "import_records", "time_expression": "MAX(created_at)"},
    {"table_name": "approval_records", "time_expression": "MAX(created_at)"},
    {"table_name": "task_status", "time_expression": "MAX(updated_at)"},
    {"table_name": "task_assignments", "time_expression": "MAX(created_at)"},
    {"table_name": "task_submissions", "time_expression": "MAX(created_at)"},
    {"table_name": "task_reviews", "time_expression": "MAX(created_at)"},
    {"table_name": "report_records", "time_expression": "MAX(created_at)"},
    {"table_name": "data_snapshots", "time_expression": "MAX(created_at)"},
    {"table_name": "metric_snapshots", "time_expression": "MAX(created_at)"},
    {"table_name": "business_signals_v6", "time_expression": "MAX(created_at)"},
    {"table_name": "alert_events", "time_expression": "MAX(updated_at)"},
    {"table_name": "imported_report_rows", "time_expression": "MAX(created_at)"},
    {"table_name": "operating_products", "time_expression": "MAX(updated_at)"},
    {"table_name": "operating_stores", "time_expression": "MAX(updated_at)"},
    {"table_name": "product_metric_facts", "time_expression": "MAX(updated_at)"},
    {"table_name": "store_metric_facts", "time_expression": "MAX(updated_at)"},
    {"table_name": "traffic_source_facts", "time_expression": "MAX(updated_at)"},
    {"table_name": "data_gap_events", "time_expression": "MAX(updated_at)"},
    {"table_name": "operating_cadence_signals", "time_expression": "MAX(created_at)"},
    {"table_name": "operating_unit_snapshots", "time_expression": "MAX(updated_at)"},
    {"table_name": "system_product_snapshots_v14", "time_expression": "MAX(updated_at)"},
    {"table_name": "product_signal_snapshots_v14", "time_expression": "MAX(updated_at)"},
    {"table_name": "signal_pool_v14", "time_expression": "MAX(updated_at)"},
    {"table_name": "agent_judgments_v14", "time_expression": "MAX(updated_at)"},
    {"table_name": "operation_budget_ledger_v14", "time_expression": "MAX(updated_at)"},
    {"table_name": "pipeline_jobs", "time_expression": "MAX(updated_at)"},
    {"table_name": "station_queue", "time_expression": "MAX(updated_at)"},
    {"table_name": "pipeline_items", "time_expression": "MAX(updated_at)"},
    {"table_name": "pipeline_item_events", "time_expression": "MAX(created_at)"},
    {"table_name": "pipeline_stage_gates", "time_expression": "MAX(updated_at)"},
    {"table_name": "station_handoffs", "time_expression": "MAX(updated_at)"},
    {"table_name": "task_snapshots", "time_expression": "MAX(updated_at)"},
    {"table_name": "task_pool_entries", "time_expression": "MAX(updated_at)"},
    {"table_name": "task_generation_runs_v14", "time_expression": "MAX(updated_at)"},
    {"table_name": "agent_product_judgments_v15", "time_expression": "MAX(created_at)"},
    {"table_name": "product_judgment_packages_v15", "time_expression": "MAX(created_at)"},
    {"table_name": "task_generation_decisions_v15", "time_expression": "MAX(created_at)"},
    {"table_name": "agent_budget_ledgers_v15", "time_expression": "MAX(updated_at)"},
    {"table_name": "agent_call_events_v15", "time_expression": "MAX(created_at)"},
    {"table_name": "report_schema_mapping_cache_v15", "time_expression": "MAX(updated_at)"},
    {"table_name": "frontend_dashboard_view", "time_expression": "MAX(updated_at)"},
    {"table_name": "frontend_product_view", "time_expression": "MAX(updated_at)"},
    {"table_name": "frontend_task_view", "time_expression": "MAX(updated_at)"},
    {"table_name": "frontend_task_detail_view", "time_expression": "MAX(updated_at)"},
    {"table_name": "frontend_store_view", "time_expression": "MAX(updated_at)"},
    {"table_name": "frontend_system_status_view", "time_expression": "MAX(updated_at)"},
    {"table_name": "ops_diagnostic_runs", "time_expression": "MAX(finished_at)"},
    {"table_name": "ops_station_checks", "time_expression": "MAX(created_at)"},
]

# Compatibility export. The authoritative delete scope is the generation sub-registry.
RUNTIME_TABLES = [
    "task_reviews", "task_submissions", "task_assignments", "approval_records", "task_status",
    "alert_events", "business_signals_v6", "operating_cadence_signals", "metric_snapshots",
    "product_metric_facts", "store_metric_facts", "traffic_source_facts", "data_gap_events",
    "data_snapshots", "imported_report_rows", "report_records", "import_records", "execution_logs",
    "workflow_runs", "operating_products", "operating_stores", "operating_unit_snapshots",
    "system_product_snapshots_v14", "product_signal_snapshots_v14", "signal_pool_v14",
    "agent_judgments_v14", "operation_budget_ledger_v14", "pipeline_jobs", "station_queue",
    "pipeline_items", "pipeline_item_events", "pipeline_stage_gates", "station_handoffs",
    "task_snapshots", "task_pool_entries", "task_generation_runs_v14",
    "agent_product_judgments_v15", "product_judgment_packages_v15",
    "task_generation_decisions_v15", "agent_budget_ledgers_v15", "agent_call_events_v15",
    "report_schema_mapping_cache_v15", "frontend_dashboard_view", "frontend_product_view",
    "frontend_task_view", "frontend_task_detail_view", "frontend_store_view",
    "frontend_system_status_view", "ops_station_checks", "ops_diagnostic_runs",
]

V15_RUNTIME_TABLES = [
    "task_generation_runs_v14", "agent_product_judgments_v15",
    "product_judgment_packages_v15", "task_generation_decisions_v15",
    "agent_budget_ledgers_v15", "agent_call_events_v15",
    "report_schema_mapping_cache_v15", "frontend_dashboard_view",
    "frontend_product_view", "frontend_task_view", "frontend_task_detail_view",
    "frontend_system_status_view",
]
V20_PIPELINE_RUNTIME_TABLES = [
    "pipeline_items", "pipeline_item_events", "pipeline_jobs",
    "station_queue", "pipeline_stage_gates", "station_handoffs",
]
FACT_SOURCE_TABLES = [
    "imported_report_rows", "operating_products", "operating_stores",
    "product_metric_facts", "store_metric_facts", "signal_pool_v14",
    "pipeline_items", "pipeline_item_events",
]
RUNTIME_BOUNDARY_NOTE = (
    "清空演示环境由 Runtime Generation Barrier 独占执行；当前报表/经营事实/信号/"
    "Agent流水线/任务池/前端读模型回到空态。canonical archive、immutable Artifact、"
    "execution audit 与 semantic cache 保留但失去 current runtime 写入/回退权限。"
)
RESET_MARKERS = [
    V5_RUNTIME_RESET_MARKER, V1114_RUNTIME_RESET_MARKER, V121_RUNTIME_RESET_MARKER,
    V1213_RUNTIME_RESET_MARKER, V124_RUNTIME_RESET_MARKER, V12131_RUNTIME_RESET_MARKER,
    V12140_RUNTIME_RESET_MARKER, V131_RUNTIME_RESET_MARKER, V133_RUNTIME_RESET_MARKER,
    V134_RUNTIME_RESET_MARKER, V1442_RUNTIME_RESET_MARKER, V146_RUNTIME_RESET_MARKER,
    V148_RUNTIME_RESET_MARKER, V1491_RUNTIME_RESET_MARKER, V15_RUNTIME_RESET_MARKER,
    V2092_DEMO_RESET_MARKER, V233_RUNTIME_GENERATION_RESET_MARKER,
]


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    )


def _ensure_runtime_meta(conn: Any) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS runtime_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def _meta_get(conn: Any, key: str) -> str | None:
    _ensure_runtime_meta(conn)
    row = conn.execute("SELECT value FROM runtime_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(conn: Any, key: str, value: str) -> None:
    _ensure_runtime_meta(conn)
    conn.execute(
        "INSERT OR REPLACE INTO runtime_meta(key,value,updated_at) VALUES (?,?,CURRENT_TIMESTAMP)",
        (key, value),
    )


def _table_count(conn: Any, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    return int(row["count"] if row else 0)


def _delete_runtime_tables(conn: Any) -> Dict[str, int]:
    """Delete only registry-owned current-runtime tables; fail closed on drift."""
    registered = registered_reset_table_names()
    if set(registered) != set(RUNTIME_TABLES):
        missing = sorted(set(RUNTIME_TABLES).difference(registered))
        extra = sorted(set(registered).difference(RUNTIME_TABLES))
        raise RuntimeError(
            "runtime_reset_registry_drift:"
            f"missing={','.join(missing)};extra={','.join(extra)}"
        )
    deleted: Dict[str, int] = {}
    for table_name in registered:
        if not _table_exists(conn, table_name):
            deleted[table_name] = 0
            continue
        count = _table_count(conn, table_name)
        conn.execute(f"DELETE FROM {table_name}")
        deleted[table_name] = count
    return deleted


def _ensure_runtime_tables() -> None:
    for loader in [
        "src.services.frontend_read_model_service.ensure_frontend_read_model_tables",
        "src.services.task_generation_run_service.ensure_task_generation_run_tables",
        "src.services.dual_agent_product_task_service.ensure_dual_agent_tables",
        "src.services.agent_budget_ledger_service.ensure_agent_budget_tables",
        "src.services.pipeline_item_service.ensure_pipeline_item_tables",
        "src.services.station_queue_service.ensure_queue_tables",
        "src.services.signal_pool_service.ensure_signal_pool_tables",
    ]:
        try:
            module_name, func_name = loader.rsplit(".", 1)
            module = __import__(module_name, fromlist=[func_name])
            getattr(module, func_name)()
        except Exception:
            pass


def get_table_status(table_name: str, time_expression: str) -> Dict[str, Any]:
    with connect() as conn:
        if not _table_exists(conn, table_name):
            return {
                "table_name": table_name,
                "record_count": 0,
                "latest_at": None,
                "exists": False,
            }
        count_row = conn.execute(
            f"SELECT COUNT(*) AS count FROM {table_name}"
        ).fetchone()
        try:
            latest_row = conn.execute(
                f"SELECT {time_expression} AS latest_at FROM {table_name}"
            ).fetchone()
        except Exception:
            latest_row = None
    return {
        "table_name": table_name,
        "record_count": int(count_row["count"] if count_row else 0),
        "latest_at": latest_row["latest_at"] if latest_row else None,
        "exists": True,
    }


def _runtime_dirty_diagnostics(
    table_status: List[Dict[str, Any]],
) -> Dict[str, Any]:
    counts = {
        item["table_name"]: int(item.get("record_count") or 0)
        for item in table_status
    }
    fact_count = sum(counts.get(name, 0) for name in FACT_SOURCE_TABLES)
    residual_count = sum(counts.get(name, 0) for name in V15_RUNTIME_TABLES)
    pipeline_residual_count = sum(
        counts.get(name, 0) for name in V20_PIPELINE_RUNTIME_TABLES
    )
    dirty = fact_count == 0 and (
        residual_count > 0 or pipeline_residual_count > 0
    )
    return {
        "version": SYSTEM_SERVICE_VERSION,
        "status": "failed" if dirty else "passed",
        "runtimeDirty": dirty,
        "factSourceCount": fact_count,
        "v15ResidualCount": residual_count,
        "v20PipelineResidualCount": pipeline_residual_count,
        "v15ResidualTables": {
            name: counts.get(name, 0) for name in V15_RUNTIME_TABLES
        },
        "v20PipelineResidualTables": {
            name: counts.get(name, 0) for name in V20_PIPELINE_RUNTIME_TABLES
        },
        "rule": (
            "If current-generation fact sources are empty, V15 outputs and V20 "
            "pipeline runtime tables must also be empty."
        ),
    }


def get_db_status() -> Dict[str, Any]:
    init_db()
    _ensure_runtime_tables()
    db_path = Path(DB_PATH)
    table_status = [
        get_table_status(item["table_name"], item["time_expression"])
        for item in TABLES
    ]
    total_records = sum(item["record_count"] for item in table_status)
    latest_times = [
        item["latest_at"] for item in table_status if item.get("latest_at")
    ]
    dirty = _runtime_dirty_diagnostics(table_status)
    generation = current_runtime_generation()
    generation_failed = generation.get("state") == "failed"
    with connect() as conn:
        marker_states = {
            marker: _meta_get(conn, marker) == "done" for marker in RESET_MARKERS
        }
    return {
        "ok": not dirty.get("runtimeDirty") and not generation_failed,
        "version": SYSTEM_SERVICE_VERSION,
        "database": {
            "type": "sqlite",
            "path": str(db_path),
            "exists": db_path.exists(),
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "walEnabled": True,
            "busyTimeoutMs": 3000,
        },
        "tables": table_status,
        "summary": {
            "table_count": len(table_status),
            "total_records": total_records,
            "latest_at": max(latest_times) if latest_times else None,
        },
        "runtimeDirtyDiagnostics": dirty,
        "runtimeGeneration": generation,
        "runtimeGenerationBarrierVersion": RUNTIME_GENERATION_VERSION,
        "runtimeResetPolicy": reset_policy_summary(),
        "runtime_boundary": {
            "real_erp_connected": False,
            "real_crm_connected": False,
            "real_shop_backend_connected": False,
            "auto_high_risk_execution": False,
            "frontend_read_model_isolated": True,
            "note": RUNTIME_BOUNDARY_NOTE,
        },
        "resetMarkers": marker_states,
    }


def clear_runtime_data(
    include_audit_logs: bool = True,
    *,
    reason: str = "manual_reset",
    scope: str = "demo",
) -> Dict[str, Any]:
    """Reset one current generation under the Worker/Reset exclusive barrier."""
    init_db()
    _ensure_runtime_tables()
    selected_scope = str(scope or "demo").strip().lower()
    if selected_scope not in {"demo", "all", "runtime"}:
        selected_scope = "demo"
    removed_files: List[str] = []

    with runtime_reset_barrier(
        reason=reason,
        scope=selected_scope,
    ) as generation_reset:
        with connect() as conn:
            deleted_tables = _delete_runtime_tables(conn)
            for marker in RESET_MARKERS:
                _meta_set(conn, marker, "done")
                _meta_set(conn, f"{marker}_reason", reason)
            # Existing History Epoch logic already treats this marker as the
            # canonical demo reset boundary.
            _meta_set(conn, "latest_demo_reset_scope", selected_scope)
            conn.commit()

        reset_tasks()

        if include_audit_logs:
            for path in JSONL_LOG_FILES:
                if path.exists():
                    path.unlink()
                    removed_files.append(str(path))

    status = get_db_status()
    current_generation = current_runtime_generation()
    return {
        "ok": bool(status.get("ok")),
        "version": SYSTEM_SERVICE_VERSION,
        "scope": selected_scope,
        "message": (
            "演示环境已切换到新的 Runtime Generation 并完成空态：当前报表、经营事实、"
            "Signal/Agent流水线、任务池和前端读模型已清空；历史 Artifact/Cache/Archive "
            "保留但不能写回当前 Generation。"
        ),
        "reason": reason,
        "runtimeGeneration": current_generation,
        "generationTransition": generation_reset,
        "deletedTables": deleted_tables,
        "v20PipelineDeletedTables": {
            name: deleted_tables.get(name, 0)
            for name in V20_PIPELINE_RUNTIME_TABLES
        },
        "removedFiles": removed_files,
        "includeAuditLogs": include_audit_logs,
        "boundary": RUNTIME_BOUNDARY_NOTE,
        "db_status": status,
        "rule": (
            "Runtime Generation Barrier is the only product-facing reset authority; "
            "Worker iterations and Reset cannot overlap, and old-generation state "
            "cannot repopulate the new empty generation."
        ),
    }


def clear_demo_data(include_audit_logs: bool = True) -> Dict[str, Any]:
    return clear_runtime_data(
        include_audit_logs=include_audit_logs,
        reason="clear_demo_data_alias",
        scope="demo",
    )


def reset_legacy_runtime_once() -> Dict[str, Any]:
    init_db()
    _ensure_runtime_tables()
    with connect() as conn:
        if any(_meta_get(conn, marker) == "done" for marker in RESET_MARKERS):
            return {
                "ok": True,
                "skipped": True,
                "marker": V233_RUNTIME_GENERATION_RESET_MARKER,
                "message": "Runtime cleanup already applied.",
                "runtimeGeneration": current_runtime_generation(),
            }
        stale_count = sum(
            _table_count(conn, table_name) for table_name in registered_reset_table_names()
        )
    if stale_count <= 0:
        with connect() as conn:
            for marker in RESET_MARKERS:
                _meta_set(conn, marker, "done")
                _meta_set(conn, f"{marker}_reason", "empty_runtime_noop")
            conn.commit()
        reset_tasks()
        return {
            "ok": True,
            "skipped": True,
            "marker": V233_RUNTIME_GENERATION_RESET_MARKER,
            "staleRecordCount": 0,
            "message": "Runtime already empty; cleanup marker recorded.",
            "runtimeGeneration": current_runtime_generation(),
        }
    result = clear_runtime_data(
        include_audit_logs=False,
        reason="v23_3_startup_generation_runtime_cleanup",
        scope="demo",
    )
    result["staleRecordCount"] = stale_count
    result["marker"] = V233_RUNTIME_GENERATION_RESET_MARKER
    return result
