"""Task Snapshot 商品血缘写入前屏障。

Task 的 created_at 是冻结证据的时间边界。创建 TaskSnapshot 前必须先确保当前
active dataVersion 已经固化 canonical 商品快照，然后再执行严格哈希绑定。

这个屏障只修复写入顺序，不放宽证据规则：
- 非 active dataVersion 不允许被重新物化或从陈旧 canonical 行恢复；
- 已绑定但不存在的 productSnapshotHash 仍然保持 lineage_broken；
- 真实历史不足两次时，Evidence Gate 仍然保持不可执行。
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from src.repositories.sqlite_repository import connect
from src.services.system_product_snapshot_service import (
    bind_task_product_lineage,
    materialize_system_product_snapshot,
)

TASK_SNAPSHOT_LINEAGE_GUARD_VERSION = "1.0"


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _active_data_version_exists(data_version: str) -> bool:
    """Only the active imported-report ledger is allowed to feed a new Task."""
    with connect() as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='imported_report_rows' LIMIT 1"
        ).fetchone()
        if not table_exists:
            return False
        row = conn.execute(
            """
            SELECT 1
            FROM imported_report_rows
            WHERE data_version = ?
            LIMIT 1
            """,
            (str(data_version),),
        ).fetchone()
    return bool(row)


def prepare_task_product_lineage(
    task: Mapping[str, Any] | None,
    *,
    user_id: str | None = None,
) -> Dict[str, Any]:
    """Materialize current canonical facts before the Task timestamp is created."""
    prepared: Dict[str, Any] = dict(task or {})
    data_version = _first_non_empty(
        prepared.get("dataVersion"),
        prepared.get("data_version"),
        prepared.get("workflowRunId"),
        prepared.get("workflow_run_id"),
    )

    if not data_version:
        return bind_task_product_lineage(prepared)

    if not _active_data_version_exists(data_version):
        # Never let a stale canonical row resurrect an inactive report version.
        # Keep any explicitly bound hash for diagnostics, but remove fact payloads
        # that could otherwise be mistaken for executable evidence downstream.
        result = dict(prepared)
        result["productSnapshot"] = {}
        result["productSnapshotStatus"] = "lineage_broken"
        result["productSnapshotLineage"] = {
            "version": TASK_SNAPSHOT_LINEAGE_GUARD_VERSION,
            "ready": False,
            "status": "lineage_broken",
            "reason": "task_data_version_not_active",
            "dataVersion": data_version,
            "strictHash": bool(str(result.get("productSnapshotHash") or "").strip()),
            "writeBarrier": "active_import_required_before_task_timestamp",
        }
        return result

    # Pre-persistence barrier: materialization commits before TaskSnapshot creates
    # its created_at timestamp. force=False keeps this idempotent for the same
    # dataVersion and never manufactures an extra historical observation.
    materialize_system_product_snapshot(
        data_version,
        user_id=str(user_id or "task_snapshot_station"),
        force=False,
    )
    result = bind_task_product_lineage(prepared)
    lineage = dict(result.get("productSnapshotLineage") or {})
    lineage.setdefault("writeBarrierVersion", TASK_SNAPSHOT_LINEAGE_GUARD_VERSION)
    lineage.setdefault("writeBarrier", "canonical_snapshot_before_task_timestamp")
    result["productSnapshotLineage"] = lineage
    return result
