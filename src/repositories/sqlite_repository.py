"""SQLite repository for product MVP persistence.

V20.20 keeps SQLite for the MVP but fixes the resource leak exposed by ECS logs:
`with sqlite3.connect(...) as conn` commits/rolls back but does not close the file
handle.  The repository now uses a ClosingConnection factory so existing
`with connect() as conn:` call sites automatically close descriptors.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

ROOT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT_DIR / "logs"
DB_PATH = LOG_DIR / "product_workbench.sqlite3"
SQLITE_TIMEOUT_SECONDS = 12.0
SQLITE_BUSY_TIMEOUT_MS = 12000
SQLITE_RETRY_VERSION = "20.20"

_WAL_LOCK = threading.Lock()
_WAL_INITIALIZED = False
T = TypeVar("T")


class ClosingConnection(sqlite3.Connection):
    """sqlite3 context manager that closes the descriptor on __exit__.

    Python's built-in sqlite3.Connection context manager does not close the
    connection. This app has many `with connect() as conn:` call sites, so the
    factory is the safest MVP fix for `[Errno 24] Too many open files`.
    """

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        suppress = super().__exit__(exc_type, exc, tb)
        self.close()
        return bool(suppress)


def _is_locked_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def run_with_retry(fn: Callable[[], T], *, attempts: int = 4, delay: float = 0.18) -> T:
    """Run a SQLite operation with a short retry on database locks."""
    last: BaseException | None = None
    for index in range(max(1, attempts)):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            last = exc
            if not _is_locked_error(exc) or index >= attempts - 1:
                raise
            time.sleep(delay * (index + 1))
    raise last  # type: ignore[misc]


def connect() -> sqlite3.Connection:
    global _WAL_INITIALIZED
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT_SECONDS, check_same_thread=False, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        if not _WAL_INITIALIZED:
            with _WAL_LOCK:
                if not _WAL_INITIALIZED:
                    try:
                        conn.execute("PRAGMA journal_mode=WAL")
                        _WAL_INITIALIZED = True
                    except sqlite3.OperationalError as exc:
                        if not _is_locked_error(exc):
                            raise
    except Exception:
        pass
    return conn


def dumps(payload: Optional[Dict[str, Any]]) -> str:
    return json.dumps(payload or {}, ensure_ascii=False)


def loads(value: str | None) -> Dict[str, Any]:
    if not value:
        return {}
    return json.loads(value)


def ensure_columns(conn: sqlite3.Connection, table_name: str, columns: Dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")


def init_db() -> None:
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workflow_runs (
                workflow_run_id TEXT PRIMARY KEY,
                workflow_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_snapshot TEXT,
                output_snapshot TEXT,
                started_at TEXT,
                finished_at TEXT,
                error_message TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_logs (
                log_id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                node_name TEXT NOT NULL,
                status TEXT NOT NULL,
                input_snapshot TEXT,
                output_snapshot TEXT,
                error_message TEXT,
                created_at TEXT,
                FOREIGN KEY(workflow_run_id) REFERENCES workflow_runs(workflow_run_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS import_records (
                import_id TEXT PRIMARY KEY,
                workflow_run_id TEXT,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                dataset_count INTEGER,
                total_rows INTEGER,
                validation TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_records (
                approval_id TEXT PRIMARY KEY,
                workflow_run_id TEXT,
                task_id TEXT NOT NULL,
                approval_status TEXT NOT NULL,
                operator TEXT,
                risk_level TEXT,
                task_type TEXT,
                payload TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_status (
                task_id TEXT PRIMARY KEY,
                workflow_run_id TEXT,
                task_type TEXT,
                risk_level TEXT,
                approval_status TEXT,
                status TEXT,
                workflow_status TEXT,
                assignee_id TEXT,
                reviewer_id TEXT,
                auto_execution_allowed INTEGER,
                payload TEXT,
                updated_at TEXT
            )
        """)
        ensure_columns(conn, "task_status", {"workflow_status": "TEXT", "assignee_id": "TEXT", "reviewer_id": "TEXT"})
        conn.execute("""
            CREATE TABLE IF NOT EXISTS report_records (
                report_id TEXT PRIMARY KEY,
                workflow_run_id TEXT,
                report_type TEXT NOT NULL,
                path TEXT,
                format TEXT,
                payload TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                user_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                role_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                role_id TEXT PRIMARY KEY,
                role_name TEXT NOT NULL,
                role_level INTEGER,
                scope TEXT,
                payload TEXT,
                updated_at TEXT
            )
        """)
        ensure_columns(conn, "roles", {"role_level": "INTEGER", "scope": "TEXT", "payload": "TEXT", "updated_at": "TEXT"})
        conn.execute("""
            CREATE TABLE IF NOT EXISTS role_permissions (
                role_id TEXT NOT NULL,
                permission_id TEXT NOT NULL,
                PRIMARY KEY(role_id, permission_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS store_groups (
                store_group_id TEXT PRIMARY KEY,
                store_group_name TEXT NOT NULL,
                owner_id TEXT,
                manager_id TEXT,
                payload TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stores (
                store_id TEXT PRIMARY KEY,
                store_name TEXT NOT NULL,
                platform TEXT,
                store_group_id TEXT,
                payload TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_memberships (
                user_id TEXT NOT NULL,
                store_group_id TEXT NOT NULL,
                store_id TEXT,
                PRIMARY KEY(user_id, store_group_id, store_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS store_permissions (
                permission_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                store_id TEXT NOT NULL,
                permission_level TEXT NOT NULL,
                payload TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_permissions (
                permission_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                store_id TEXT,
                permission_level TEXT NOT NULL,
                payload TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_assignments (
                assignment_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                assignee_id TEXT NOT NULL,
                reviewer_id TEXT,
                assigned_by_id TEXT,
                note TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_submissions (
                submission_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                submitter_id TEXT NOT NULL,
                note TEXT,
                payload TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_reviews (
                review_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                note TEXT,
                payload TEXT,
                created_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_runs_time ON workflow_runs(finished_at, started_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_logs_run ON execution_logs(workflow_run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_import_records_time ON import_records(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_records_task ON approval_records(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_status_status ON task_status(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_status_assignee ON task_status(assignee_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_report_records_time ON report_records(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_role ON accounts(role_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stores_group ON stores(store_group_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_assignments_task ON task_assignments(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_submissions_task ON task_submissions(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_reviews_task ON task_reviews(task_id)")
        conn.commit()


def upsert_workflow_run(run: Dict[str, Any]) -> None:
    init_db()
    with connect() as conn:
        conn.execute("""
            INSERT INTO workflow_runs (
                workflow_run_id, workflow_type, status, input_snapshot,
                output_snapshot, started_at, finished_at, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workflow_run_id) DO UPDATE SET
                workflow_type=excluded.workflow_type,
                status=excluded.status,
                input_snapshot=COALESCE(excluded.input_snapshot, workflow_runs.input_snapshot),
                output_snapshot=COALESCE(excluded.output_snapshot, workflow_runs.output_snapshot),
                started_at=COALESCE(workflow_runs.started_at, excluded.started_at),
                finished_at=COALESCE(excluded.finished_at, workflow_runs.finished_at),
                error_message=COALESCE(excluded.error_message, workflow_runs.error_message)
        """, (run.get("workflow_run_id"), run.get("workflow_type"), run.get("status"), dumps(run.get("input_snapshot")) if "input_snapshot" in run else None, dumps(run.get("output_snapshot")) if "output_snapshot" in run else None, run.get("started_at"), run.get("finished_at"), run.get("error_message")))
        conn.commit()


def insert_execution_log(log: Dict[str, Any]) -> None:
    init_db()
    with connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO execution_logs (
                log_id, workflow_run_id, node_name, status, input_snapshot,
                output_snapshot, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (log.get("log_id"), log.get("workflow_run_id"), log.get("node_name"), log.get("status"), dumps(log.get("input_snapshot")), dumps(log.get("output_snapshot")), log.get("error_message"), log.get("created_at")))
        conn.commit()


def insert_import_record(record: Dict[str, Any]) -> None:
    init_db()
    with connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO import_records (
                import_id, workflow_run_id, mode, status, dataset_count,
                total_rows, validation, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (record.get("import_id"), record.get("workflow_run_id"), record.get("mode"), record.get("status"), record.get("dataset_count"), record.get("total_rows"), dumps(record.get("validation")), record.get("created_at")))
        conn.commit()


def insert_approval_record(record: Dict[str, Any]) -> None:
    init_db()
    with connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO approval_records (
                approval_id, workflow_run_id, task_id, approval_status,
                operator, risk_level, task_type, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (record.get("approval_id"), record.get("workflow_run_id"), record.get("task_id"), record.get("approval_status"), record.get("operator"), record.get("risk_level"), record.get("task_type"), dumps(record), record.get("updated_at") or record.get("created_at")))
        conn.commit()


def upsert_task_status(task: Dict[str, Any]) -> None:
    init_db()
    task_id = task.get("task_id") or task.get("id")
    with connect() as conn:
        conn.execute("""
            INSERT INTO task_status (
                task_id, workflow_run_id, task_type, risk_level, approval_status,
                status, workflow_status, assignee_id, reviewer_id,
                auto_execution_allowed, payload, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                workflow_run_id=excluded.workflow_run_id,
                task_type=excluded.task_type,
                risk_level=excluded.risk_level,
                approval_status=excluded.approval_status,
                status=excluded.status,
                workflow_status=excluded.workflow_status,
                assignee_id=excluded.assignee_id,
                reviewer_id=excluded.reviewer_id,
                auto_execution_allowed=excluded.auto_execution_allowed,
                payload=excluded.payload,
                updated_at=excluded.updated_at
        """, (task_id, task.get("workflow_run_id"), task.get("task_type") or task.get("taskType"), task.get("risk_level") or task.get("priority"), task.get("approval_status"), task.get("status"), task.get("workflow_status") or task.get("workflowStatus"), task.get("assignee_id") or task.get("assigneeId"), task.get("reviewer_id") or task.get("reviewerId"), 1 if task.get("auto_execution_allowed") is True else 0, dumps(task), task.get("updated_at") or task.get("updatedAt")))
        conn.commit()


def insert_report_record(record: Dict[str, Any]) -> None:
    init_db()
    with connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO report_records (
                report_id, workflow_run_id, report_type, path, format, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (record.get("report_id"), record.get("workflow_run_id"), record.get("report_type"), record.get("path"), record.get("format"), dumps(record), record.get("created_at")))
        conn.commit()


def row_to_workflow_run(row: sqlite3.Row) -> Dict[str, Any]:
    return {"workflow_run_id": row["workflow_run_id"], "workflow_type": row["workflow_type"], "status": row["status"], "input_snapshot": loads(row["input_snapshot"]), "output_snapshot": loads(row["output_snapshot"]), "started_at": row["started_at"], "finished_at": row["finished_at"], "error_message": row["error_message"]}


def row_to_execution_log(row: sqlite3.Row) -> Dict[str, Any]:
    return {"log_id": row["log_id"], "workflow_run_id": row["workflow_run_id"], "node_name": row["node_name"], "status": row["status"], "input_snapshot": loads(row["input_snapshot"]), "output_snapshot": loads(row["output_snapshot"]), "error_message": row["error_message"], "created_at": row["created_at"]}


def list_workflow_runs(limit: int = 50) -> List[Dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM workflow_runs ORDER BY COALESCE(finished_at, started_at) DESC LIMIT ?", (limit,)).fetchall()
    return [row_to_workflow_run(row) for row in rows]


def list_execution_logs(limit: int = 100) -> List[Dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM execution_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [row_to_execution_log(row) for row in rows]


def list_execution_logs_by_run(workflow_run_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM execution_logs WHERE workflow_run_id = ? ORDER BY created_at DESC LIMIT ?", (workflow_run_id, limit)).fetchall()
    return [row_to_execution_log(row) for row in rows]


def list_import_records(limit: int = 50) -> List[Dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM import_records ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [{"import_id": row["import_id"], "workflow_run_id": row["workflow_run_id"], "mode": row["mode"], "status": row["status"], "dataset_count": row["dataset_count"], "total_rows": row["total_rows"], "validation": loads(row["validation"]), "created_at": row["created_at"]} for row in rows]


def get_task_status_map() -> Dict[str, Dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM task_status").fetchall()
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        payload = loads(row["payload"])
        payload.setdefault("task_id", row["task_id"])
        payload.setdefault("status", row["status"])
        payload.setdefault("workflowStatus", row["workflow_status"])
        payload.setdefault("assigneeId", row["assignee_id"])
        payload.setdefault("reviewerId", row["reviewer_id"])
        result[row["task_id"]] = payload
    return result


def list_approval_records(limit: int = 50) -> List[Dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM approval_records ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    records: List[Dict[str, Any]] = []
    for row in rows:
        payload = loads(row["payload"])
        payload.setdefault("approval_id", row["approval_id"])
        payload.setdefault("workflow_run_id", row["workflow_run_id"])
        payload.setdefault("task_id", row["task_id"])
        payload.setdefault("approval_status", row["approval_status"])
        payload.setdefault("operator", row["operator"])
        payload.setdefault("risk_level", row["risk_level"])
        payload.setdefault("task_type", row["task_type"])
        payload.setdefault("created_at", row["created_at"])
        records.append(payload)
    return records
