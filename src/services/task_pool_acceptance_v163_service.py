"""V16.27 current-run task pool acceptance service.

This service is the MVP-real task-pool验收闸门. It does not generate tasks and
does not call any Agent. It only proves whether the latest real run has a clean,
current-run task projection:

- latestRun.taskPoolCreatedCount
- task_pool_entries WHERE data_version = latestRun.dataVersion
- frontend_task_view WHERE data_version = latestRun.dataVersion
- frontend_task_detail_view WHERE data_version = latestRun.dataVersion

All four must align before the run is considered accepted.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads

TASK_POOL_ACCEPTANCE_VERSION = "16.27"
DONE_STATUS = {"已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘"}
FORMAL_DECISIONS = {"create_task_snapshot", "manager_review_required"}


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone())


def _safe_load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _count(conn: Any, table_name: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(conn, table_name):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name} {where}", params).fetchone()
    return int(row["count"] or 0) if row else 0


def _latest_run(conn: Any) -> Dict[str, Any] | None:
    if not _table_exists(conn, "task_generation_runs_v14"):
        return None
    row = conn.execute("SELECT payload FROM task_generation_runs_v14 ORDER BY created_at DESC LIMIT 1").fetchone()
    return _safe_load(row["payload"]) if row else None


def _decision_counts(conn: Any, data_version: str | None) -> Dict[str, int]:
    if not data_version or not _table_exists(conn, "task_generation_decisions_v15"):
        return {}
    rows = conn.execute("SELECT decision, COUNT(*) AS count FROM task_generation_decisions_v15 WHERE data_version = ? GROUP BY decision", (data_version,)).fetchall()
    return {str(row["decision"]): int(row["count"] or 0) for row in rows}


def _current_task_pool_ids(conn: Any, data_version: str | None) -> List[str]:
    if not data_version or not _table_exists(conn, "task_pool_entries"):
        return []
    rows = conn.execute("SELECT task_id FROM task_pool_entries WHERE data_version = ? ORDER BY updated_at DESC", (data_version,)).fetchall()
    return [str(row["task_id"]) for row in rows if row["task_id"]]


def _frontend_task_ids(conn: Any, data_version: str | None) -> List[str]:
    if not data_version or not _table_exists(conn, "frontend_task_view"):
        return []
    rows = conn.execute("SELECT task_id FROM frontend_task_view WHERE data_version = ? ORDER BY updated_at DESC", (data_version,)).fetchall()
    return [str(row["task_id"]) for row in rows if row["task_id"]]


def _frontend_detail_ids(conn: Any, data_version: str | None) -> List[str]:
    if not data_version or not _table_exists(conn, "frontend_task_detail_view"):
        return []
    rows = conn.execute("SELECT task_id FROM frontend_task_detail_view WHERE data_version = ? ORDER BY updated_at DESC", (data_version,)).fetchall()
    return [str(row["task_id"]) for row in rows if row["task_id"]]


def _foreign_frontend_rows(conn: Any, data_version: str | None) -> int:
    if not data_version or not _table_exists(conn, "frontend_task_view"):
        return 0
    return _count(conn, "frontend_task_view", "WHERE data_version IS NOT NULL AND data_version != ?", (data_version,))


def _current_pool_payload_errors(conn: Any, data_version: str | None) -> List[Dict[str, Any]]:
    if not data_version or not _table_exists(conn, "task_pool_entries"):
        return []
    rows = conn.execute("SELECT task_id, task_snapshot_id, decision, payload FROM task_pool_entries WHERE data_version = ? ORDER BY updated_at DESC LIMIT 1000", (data_version,)).fetchall()
    errors: List[Dict[str, Any]] = []
    for row in rows:
        payload = _safe_load(row["payload"])
        task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        plan = snapshot.get("taskPlan") if isinstance(snapshot.get("taskPlan"), dict) else {}
        task_id = str(row["task_id"] or "")
        if not task_id:
            errors.append({"taskSnapshotId": row["task_snapshot_id"], "error": "missing_task_id"})
        if row["decision"] not in FORMAL_DECISIONS:
            errors.append({"taskId": task_id, "error": "non_formal_decision_in_pool", "decision": row["decision"]})
        if task.get("status") in DONE_STATUS or task.get("workflowStatus") in DONE_STATUS:
            errors.append({"taskId": task_id, "error": "done_task_in_current_execution_pool", "status": task.get("status") or task.get("workflowStatus")})
        if not (task.get("title") or (task.get("taskCard") or {}).get("title") or plan.get("title")):
            errors.append({"taskId": task_id, "error": "missing_task_title"})
    return errors


def read_task_pool_acceptance(data_version: str | None = None) -> Dict[str, Any]:
    """Return current-run task-pool acceptance diagnostics."""

    with connect() as conn:
        latest_run = _latest_run(conn)
        current_data_version = data_version or ((latest_run or {}).get("dataVersion"))
        expected_task_count = int((latest_run or {}).get("taskPoolCreatedCount") or 0)
        run_formal_task_count = int((latest_run or {}).get("formalTaskCount") or 0)
        decision_counts = _decision_counts(conn, current_data_version)
        formal_decision_count = int(decision_counts.get("create_task_snapshot", 0) or 0) + int(decision_counts.get("manager_review_required", 0) or 0)
        pool_current_ids = _current_task_pool_ids(conn, current_data_version)
        frontend_ids = _frontend_task_ids(conn, current_data_version)
        detail_ids = _frontend_detail_ids(conn, current_data_version)
        task_pool_current_count = len(pool_current_ids)
        frontend_task_view_count = len(frontend_ids)
        frontend_detail_count = len(detail_ids)
        task_pool_total_count = _count(conn, "task_pool_entries")
        frontend_total_count = _count(conn, "frontend_task_view")
        foreign_frontend_count = _foreign_frontend_rows(conn, current_data_version)
        pool_errors = _current_pool_payload_errors(conn, current_data_version)

    mismatches: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    if not latest_run:
        mismatches.append({"field": "latestRun", "expected": "exists", "actual": "missing", "reason": "没有任务生成运行快照，无法验收本轮任务池。"})
    if not current_data_version and latest_run:
        mismatches.append({"field": "dataVersion", "expected": "latestRun.dataVersion", "actual": None, "reason": "没有本轮dataVersion，任务池不能进入验收。"})
    if expected_task_count != task_pool_current_count:
        mismatches.append({"field": "taskPoolCreatedCount_vs_task_pool_entries", "expected": expected_task_count, "actual": task_pool_current_count, "reason": "数据页本轮任务数必须等于当前dataVersion任务池数量。"})
    if task_pool_current_count != frontend_task_view_count:
        mismatches.append({"field": "task_pool_entries_vs_frontend_task_view", "expected": task_pool_current_count, "actual": frontend_task_view_count, "reason": "任务池当前任务数必须等于任务页可见任务数。"})
    if frontend_task_view_count != frontend_detail_count:
        mismatches.append({"field": "frontend_task_view_vs_detail_view", "expected": frontend_task_view_count, "actual": frontend_detail_count, "reason": "每个任务卡片都必须有详情页投影。"})
    missing_in_frontend = sorted(set(pool_current_ids) - set(frontend_ids))
    extra_frontend = sorted(set(frontend_ids) - set(pool_current_ids))
    missing_detail = sorted(set(frontend_ids) - set(detail_ids))
    if missing_in_frontend:
        mismatches.append({"field": "missing_frontend_task_ids", "expected": "all current task_pool taskIds", "actual": missing_in_frontend[:20], "reason": "任务池存在但任务页不可见。"})
    if extra_frontend:
        mismatches.append({"field": "extra_frontend_task_ids", "expected": "only current task_pool taskIds", "actual": extra_frontend[:20], "reason": "任务页出现不属于当前任务池的任务。"})
    if missing_detail:
        mismatches.append({"field": "missing_detail_task_ids", "expected": "all frontend taskIds have details", "actual": missing_detail[:20], "reason": "任务详情页投影缺失。"})
    if pool_errors:
        mismatches.append({"field": "task_pool_payload_contract", "expected": "valid current formal task payloads", "actual": pool_errors[:20], "reason": "当前任务池存在不可执行或非正式任务载荷。"})
    if run_formal_task_count and run_formal_task_count != formal_decision_count:
        warnings.append({"field": "formal_decision_count", "expected": run_formal_task_count, "actual": formal_decision_count, "reason": "运行快照与决策表正式决策数不一致，可能有重复/跳过/旧决策。"})
    if task_pool_total_count > task_pool_current_count:
        warnings.append({"field": "old_task_pool_entries", "expected": "old entries not visible", "actual": task_pool_total_count - task_pool_current_count, "reason": "历史任务可留存，但不得进入当前任务页。"})
    if foreign_frontend_count > 0:
        mismatches.append({"field": "foreign_frontend_task_rows", "expected": 0, "actual": foreign_frontend_count, "reason": "frontend_task_view 不允许保留非当前dataVersion任务。"})

    status = "passed" if not mismatches else "failed"
    if status == "passed" and warnings:
        status = "passed_with_warnings"
    return {
        "version": TASK_POOL_ACCEPTANCE_VERSION,
        "status": status,
        "ok": status in {"passed", "passed_with_warnings"},
        "ready": bool(latest_run and current_data_version),
        "dataVersion": current_data_version,
        "latestRunId": (latest_run or {}).get("runId"),
        "counts": {
            "expectedTaskCountFromLatestRun": expected_task_count,
            "runFormalTaskCount": run_formal_task_count,
            "formalDecisionCount": formal_decision_count,
            "taskPoolCurrentCount": task_pool_current_count,
            "taskPoolTotalCount": task_pool_total_count,
            "frontendTaskViewCurrentCount": frontend_task_view_count,
            "frontendTaskViewTotalCount": frontend_total_count,
            "frontendTaskDetailCurrentCount": frontend_detail_count,
        },
        "mismatches": mismatches,
        "warnings": warnings,
        "sampleTaskIds": {"taskPool": pool_current_ids[:20], "frontend": frontend_ids[:20], "details": detail_ids[:20]},
        "latestRun": latest_run,
        "rule": "V16.27 acceptance: data-line formalTaskCount, task_pool current dataVersion count, frontend task count and detail count must align before MVP-real task pool is accepted.",
    }
