#!/usr/bin/env python3
"""Compatibility launcher for the fixed three-report E2E.

Three compatibility boundaries are applied without changing product runtime code:

1. launch the V22.5.9 exact-hash fixture provider entry;
2. collect SQLite evidence by introspecting the current ``pipeline_items`` schema;
3. disable the asynchronous station thread so the test's official manual Tick API
   deterministically drains all three imported data versions before assertions.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_competition_three_report_e2e as base  # noqa: E402


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]


def _safe_select_columns(
    connection: sqlite3.Connection,
    table: str,
    preferred: Sequence[str],
) -> list[str]:
    available = set(_table_columns(connection, table))
    return [column for column in preferred if column in available]


def query_runtime_database(db_path: Path, latest_version: str) -> dict[str, Any]:
    if not db_path.is_file():
        raise base.ThreeReportE2EError(f"RUNTIME_DATABASE_MISSING:{db_path}")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        stages: list[dict[str, Any]] = []
        family_counts: list[dict[str, Any]] = []
        pipeline_rows: list[dict[str, Any]] = []
        pipeline_columns: list[str] = []
        if "pipeline_items" in tables:
            pipeline_columns = _table_columns(connection, "pipeline_items")
            available = set(pipeline_columns)
            version_column = "data_version" if "data_version" in available else None
            where = " WHERE data_version=?" if version_column else ""
            params: tuple[Any, ...] = (latest_version,) if version_column else ()
            stage_column = (
                "current_stage"
                if "current_stage" in available
                else "stage"
                if "stage" in available
                else None
            )
            status_column = "status" if "status" in available else None
            if stage_column and status_column:
                stages = [
                    dict(row)
                    for row in connection.execute(
                        f"""
                        SELECT {stage_column} AS current_stage,
                               {status_column} AS status,
                               COUNT(*) AS count
                        FROM pipeline_items
                        {where}
                        GROUP BY {stage_column},{status_column}
                        ORDER BY {stage_column},{status_column}
                        """,
                        params,
                    ).fetchall()
                ]
            family_column = (
                "action_family"
                if "action_family" in available
                else "locked_action_family"
                if "locked_action_family" in available
                else None
            )
            if family_column:
                family_counts = [
                    dict(row)
                    for row in connection.execute(
                        f"""
                        SELECT COALESCE({family_column},'') AS actionFamily,
                               COUNT(*) AS count
                        FROM pipeline_items
                        {where}
                        GROUP BY COALESCE({family_column},'')
                        ORDER BY actionFamily
                        """,
                        params,
                    ).fetchall()
                ]
            selected = _safe_select_columns(
                connection,
                "pipeline_items",
                [
                    "item_id",
                    "pipeline_item_id",
                    "product_id",
                    "store_id",
                    "data_version",
                    "current_stage",
                    "stage",
                    "status",
                    "action_family",
                    "locked_action_family",
                    "input_ref",
                    "output_ref",
                    "input_artifact_ref",
                    "output_artifact_ref",
                    "payload_artifact_ref",
                    "artifact_refs_json",
                    "last_error_code",
                    "last_error_message",
                    "error_reason",
                    "failure_code",
                    "failure_class",
                    "retry_count",
                ],
            )
            if selected:
                order_columns = [
                    column
                    for column in ("product_id", "item_id", "pipeline_item_id")
                    if column in available
                ]
                order = " ORDER BY " + ",".join(order_columns) if order_columns else ""
                pipeline_rows = [
                    dict(row)
                    for row in connection.execute(
                        f'SELECT {",".join(selected)} FROM pipeline_items{where}{order}',
                        params,
                    ).fetchall()
                ]

        llm_audit: list[dict[str, Any]] = []
        if "llm_inference_audit_v211" in tables:
            audit_columns = set(_table_columns(connection, "llm_inference_audit_v211"))
            required = {"stage", "provider", "model", "status"}
            if required.issubset(audit_columns):
                provider_call_expression = (
                    "SUM(provider_call_executed)"
                    if "provider_call_executed" in audit_columns
                    else "0"
                )
                replay_expression = (
                    "SUM(local_replay)" if "local_replay" in audit_columns else "0"
                )
                llm_audit = [
                    dict(row)
                    for row in connection.execute(
                        f"""
                        SELECT stage,provider,model,status,
                               {provider_call_expression} AS providerCalls,
                               {replay_expression} AS localReplays,
                               COUNT(*) AS auditRows
                        FROM llm_inference_audit_v211
                        GROUP BY stage,provider,model,status
                        ORDER BY stage,status
                        """
                    ).fetchall()
                ]

        task_tables = sorted(
            table
            for table in tables
            if "task" in table.lower() and not table.startswith("sqlite_")
        )
        task_table_counts: dict[str, int] = {}
        for table in task_tables:
            try:
                row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
                task_table_counts[table] = int(row[0] or 0) if row else 0
            except sqlite3.DatabaseError:
                continue
        return {
            "databasePath": str(db_path),
            "tables": sorted(tables),
            "pipelineItemColumns": pipeline_columns,
            "pipelineStages": stages,
            "pipelineActionFamilies": family_counts,
            "pipelineItems": pipeline_rows,
            "llmAudit": llm_audit,
            "taskTableCounts": task_table_counts,
        }
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    # The production runtime still owns exactly one worker implementation. The E2E
    # disables only its asynchronous thread so every transition is driven by the
    # public manual Tick endpoint and the test cannot stop while another thread is
    # holding an older data version.
    os.environ["STATION_QUEUE_WORKER_ENABLED"] = "false"
    os.environ["AGENT_PIPELINE_ITEM_WORKER_ENABLED"] = "true"
    os.environ["STATION_QUEUE_WORKER_MAX_JOBS_PER_TICK"] = "12"
    base.SCRIPT_DIR = SCRIPTS_DIR / "competition_e2e_compat_runtime"
    base.query_runtime_database = query_runtime_database
    return base.main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition three-report E2E compatibility launch failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
