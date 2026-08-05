"""V12.13 operating-unit snapshot reader.

The operating page must not re-run report parsing, product projection, traffic
projection, task generation, RAG retrieval or LLM generation. It reads a compact
snapshot produced from already-materialized operating objects and lightweight task
state.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, ensure_columns
from src.services import module_task_service
from src.services.account_service import current_user, list_stores, visible_store_ids_for_user
from src.services.pipeline_gate_service import PIPELINE_GATE_VERSION, record_stage_gate, stable_hash, stage_summary

OPERATING_UNIT_SNAPSHOT_VERSION = "12.13.0"
DONE_STATUS = {"已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘"}
HIDDEN_QUEUE_TYPES = {"backend_tag", "store_product_tag", "observe_candidate", "candidate_only", "report_seed_only", "merged_duplicate"}


def now_iso() -> str:
    return datetime.now().isoformat()


def _safe_json(value: Any, fallback: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return fallback
    return fallback


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except Exception:
        return default


def ensure_snapshot_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operating_unit_snapshots (
                snapshot_key TEXT PRIMARY KEY,
                user_id TEXT,
                role_id TEXT,
                data_version TEXT,
                input_hash TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "operating_unit_snapshots",
            {
                "role_id": "TEXT",
                "data_version": "TEXT",
                "input_hash": "TEXT",
            },
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_operating_unit_snapshots_user ON operating_unit_snapshots(user_id, data_version, updated_at)")
        conn.commit()


def _visible_store(row: Any, user_id: str | None) -> bool:
    if not user_id:
        return True
    user = current_user(user_id)
    if user.get("roleId") in {"owner", "manager", "finance"}:
        return True
    visible_user_ids = set(_safe_json(row["visible_user_ids"], []) or [])
    if user_id in visible_user_ids:
        return True
    if user_id in {row["assigned_operator_id"], row["owner_user_id"], row["imported_by_user_id"]}:
        return True
    store_id = str(row["normalized_store_id"] or row["store_id"] or "")
    return not store_id or store_id in set(visible_store_ids_for_user(user_id))


def _visible_product(row: Any, user_id: str | None) -> bool:
    if not user_id:
        return True
    user = current_user(user_id)
    if user.get("roleId") in {"owner", "manager", "finance"}:
        return True
    visible_user_ids = set(_safe_json(row["visible_user_ids"], []) or [])
    if user_id in visible_user_ids:
        return True
    if user_id in {row["assigned_operator_id"], row["owner_user_id"], row["imported_by_user_id"]}:
        return True
    store_id = str(row["normalized_store_id"] or row["store_id"] or "")
    return not store_id or store_id in set(visible_store_ids_for_user(user_id))


def _store_rows_from_master(user_id: str | None) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM operating_stores ORDER BY updated_at DESC, store_name ASC").fetchall()
    result: List[Dict[str, Any]] = []
    for row in rows:
        if not _visible_store(row, user_id):
            continue
        payload = _safe_json(row["payload"], {}) or {}
        product_count = _as_int(row["product_count"])
        store_id = row["normalized_store_id"] or row["store_id"]
        store_name = row["normalized_store_name"] or row["store_name"] or store_id or "未命名店铺"
        result.append({
            "storeId": store_id,
            "storeName": store_name,
            "displayName": store_name,
            "platform": row["platform"] or payload.get("platform") or "平台",
            "productCount": product_count,
            "trafficCount": 0,
            "latestDataVersion": row["latest_data_version"],
            "sourceDataset": row["source_dataset"],
            "productRoleTags": [f"商品 {product_count}"],
            "businessTags": ["经营对象", "快照读取"],
            "riskTags": ["事实表取数"],
            "dataTags": ["已入库"],
            "displayTags": ["经营对象", f"商品 {product_count}"],
            "storeWeightTag": "经营对象",
            "storeWeight": {"level": "snapshot", "source": "operating_unit_snapshot"},
            "activeTaskCount": 0,
            "alertCount": 0,
            "taskIntensity": "标签观察",
            "level": "watch",
            "judgment": f"{store_name} 已完成对象入库，经营页读取快照，不重复解析报表。",
            "snapshotSource": "operating_stores",
        })
    return result


def _product_counts_by_store(user_id: str | None) -> Dict[str, int]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM operating_products").fetchall()
    counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        if not _visible_product(row, user_id):
            continue
        key = str(row["normalized_store_id"] or row["store_id"] or row["normalized_store_name"] or row["store_name"] or "未归属店铺")
        counts[key] += 1
    return dict(counts)


def _latest_data_version(store_rows: List[Dict[str, Any]]) -> str | None:
    for row in store_rows:
        if row.get("latestDataVersion"):
            return str(row["latestDataVersion"])
    return None


def _source_counts() -> Dict[str, int]:
    tables = ["import_records", "report_records", "imported_report_rows", "data_snapshots", "metric_snapshots", "operating_products", "operating_stores", "alert_events"]
    result: Dict[str, int] = {}
    with connect() as conn:
        for table in tables:
            exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                result[table] = 0
                continue
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            result[table] = int(row["count"] if row else 0)
    return result


def _light_task_counts(user_id: str | None = None) -> Dict[str, Any]:
    visible = []
    for task in list(module_task_service.TASKS or []):
        if not isinstance(task, dict):
            continue
        if task.get("status") in DONE_STATUS or task.get("displayState") == "backend_only" or task.get("queueType") in HIDDEN_QUEUE_TYPES:
            continue
        visible.append(task)
    return {
        "version": OPERATING_UNIT_SNAPSHOT_VERSION,
        "visibleActive": len(visible),
        "processing": len([task for task in visible if task.get("status") == "处理中"]),
        "waitingRecap": len([task for task in visible if task.get("workflowStatus") == "等待自动复盘" or task.get("lifecycleStage") == "recap_scheduled"]),
        "reviewing": len([task for task in visible if task.get("status") == "待复核"]),
        "rule": "经营页只读取轻量任务计数，不读取RAG/LLM大任务对象。",
    }


def _task_count_by_store() -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for task in list(module_task_service.TASKS or []):
        if not isinstance(task, dict):
            continue
        if task.get("status") in DONE_STATUS or task.get("displayState") == "backend_only" or task.get("queueType") in HIDDEN_QUEUE_TYPES:
            continue
        store_ids = [str(item) for item in (task.get("storeIds") or task.get("visibleStoreIds") or []) if item]
        store_name = str(task.get("storeName") or task.get("store") or "")
        for store_id in store_ids or ([store_name] if store_name else []):
            counts[store_id] += 1
    return dict(counts)


def _fallback_static_store_rows(user_id: str | None) -> List[Dict[str, Any]]:
    rows = []
    for store in list_stores():
        rows.append({
            "storeId": store.get("id"),
            "storeName": store.get("name") or store.get("id") or "店铺",
            "displayName": store.get("name") or store.get("id") or "店铺",
            "platform": store.get("platform") or "平台",
            "productCount": 0,
            "businessTags": ["等待报表"],
            "displayTags": ["等待报表"],
            "productRoleTags": ["暂无商品"],
            "activeTaskCount": 0,
            "alertCount": 0,
            "taskIntensity": "等待数据",
            "level": "watch",
            "storeWeightTag": "等待数据",
            "judgment": "尚未生成经营对象快照。",
            "snapshotSource": "account_store_fallback",
        })
    return rows


def _snapshot_key(user_id: str | None, data_version: str | None, input_hash: str) -> str:
    return f"{user_id or 'anonymous'}|{data_version or 'latest'}|{input_hash}"


def _read_snapshot(user_id: str | None, data_version: str | None = None) -> Dict[str, Any] | None:
    ensure_snapshot_tables()
    sql = "SELECT * FROM operating_unit_snapshots WHERE user_id IS ?"
    params: List[Any] = [user_id]
    if data_version:
        sql += " AND data_version = ?"
        params.append(data_version)
    sql += " ORDER BY updated_at DESC LIMIT 1"
    with connect() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    if not row:
        return None
    payload = _safe_json(row["payload"], None)
    return payload if isinstance(payload, dict) else None


def _write_snapshot(user_id: str | None, role_id: str | None, data_version: str | None, input_hash: str, payload: Dict[str, Any]) -> None:
    ensure_snapshot_tables()
    now = now_iso()
    key = _snapshot_key(user_id, data_version, input_hash)
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO operating_unit_snapshots (
                snapshot_key, user_id, role_id, data_version, input_hash, payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM operating_unit_snapshots WHERE snapshot_key = ?), ?), ?)
            """,
            (key, user_id, role_id, data_version, input_hash, json.dumps(payload, ensure_ascii=False), key, now, now),
        )
        conn.commit()


def materialize_operating_unit_snapshot(user_id: str | None = None, data_version: str | None = None, *, force: bool = False) -> Dict[str, Any]:
    """Build a lightweight operating-unit snapshot from materialized objects only."""
    ensure_snapshot_tables()
    user = current_user(user_id) if user_id else {}
    stores = _store_rows_from_master(user_id)
    if not stores:
        stores = _fallback_static_store_rows(user_id)
    counts = _product_counts_by_store(user_id)
    task_by_store = _task_count_by_store()
    for row in stores:
        sid = str(row.get("storeId") or row.get("storeName") or "")
        count = counts.get(sid) or counts.get(str(row.get("storeName") or "")) or _as_int(row.get("productCount"))
        row["productCount"] = count
        task_count = task_by_store.get(sid) or task_by_store.get(str(row.get("storeName") or "")) or 0
        row["activeTaskCount"] = task_count
        row["alertCount"] = task_count
        if task_count:
            row["businessTags"] = list(dict.fromkeys([*(row.get("businessTags") or []), "执行任务"]))
            row["displayTags"] = list(dict.fromkeys([*(row.get("displayTags") or []), "执行任务"]))
            row["taskIntensity"] = "有执行任务"
    latest = data_version or _latest_data_version(stores)
    task_counts = _light_task_counts(user_id)
    tagged = len([row for row in stores if row.get("businessTags") and row.get("businessTags") != ["常规观察"]])
    source_counts = _source_counts()
    input_hash = stable_hash({"userId": user_id, "dataVersion": latest, "stores": [(row.get("storeId"), row.get("productCount"), row.get("activeTaskCount")) for row in stores], "sourceCounts": source_counts})
    cached = None if force else _read_snapshot(user_id, latest)
    if cached and cached.get("inputHash") == input_hash:
        record_stage_gate(data_version=latest, stage="operating_unit_snapshot_ready", status="cached", input_payload={"inputHash": input_hash}, output_payload={"snapshotKey": cached.get("snapshotKey")}, user_id=user_id, upstream_stage="operating_objects_ready", output_ref=cached.get("snapshotKey"))
        cached["syncState"] = {**dict(cached.get("syncState") or {}), "label": "快照已复用", "status": "snapshot_cached"}
        cached["pipelineGate"] = stage_summary(latest, user_id=user_id, limit=20)
        return cached
    has_data = bool(source_counts.get("operating_products") or source_counts.get("operating_stores") or source_counts.get("imported_report_rows") or source_counts.get("data_snapshots"))
    snapshot_key = _snapshot_key(user_id, latest, input_hash)
    payload = {
        "version": OPERATING_UNIT_SNAPSHOT_VERSION,
        "hasData": has_data,
        "unitName": "经营单元",
        "syncState": {"label": "快照已生成", "status": "snapshot_ready", "latestDataVersion": latest},
        "latestSnapshotAt": now_iso(),
        "viewer": {"id": user.get("id"), "roleId": user.get("roleId"), "roleName": user.get("roleName")},
        "metrics": [
            {"label": "店铺", "value": len(stores) if has_data else 0},
            {"label": "商品", "value": sum(_as_int(row.get("productCount")) for row in stores)},
            {"label": "标签店铺", "value": tagged if has_data else 0},
            {"label": "执行任务", "value": task_counts.get("visibleActive", 0)},
        ],
        "storeRows": stores if has_data else [],
        "operatingJudgment": {
            "title": "经营判断",
            "summary": "经营页读取 operating_unit_snapshot，不再重复执行报表解析、商品投影、流量投影、RAG或LLM任务生成。" if has_data else "暂无经营对象快照，请先导入报表或执行经营对象生成站。",
            "priority": "前端只读快照；上游加工由pipeline站点完成。",
            "mainRisk": "接口重复拉取已关闭" if has_data else "等待数据",
            "taggedStoreCount": tagged if has_data else 0,
            "activeTaskCount": task_counts.get("visibleActive", 0),
        },
        "tasks": task_counts,
        "objectStore": {"productCount": source_counts.get("operating_products", 0), "storeCount": source_counts.get("operating_stores", 0), "latestDataVersion": latest, "source": "snapshot_counts"},
        "pipeline": {"version": PIPELINE_GATE_VERSION, "currentStage": "operating_unit_snapshot_ready", "rule": "主流程分站接力；经营页只读快照。"},
        "snapshotKey": snapshot_key,
        "inputHash": input_hash,
        "sourceCounts": source_counts,
        "diagnostics": {"errors": []},
        "rule": "V12.13：经营页只读快照；报表上传、对象映射、任务生成、Agent增强均由独立pipeline站点完成。",
    }
    _write_snapshot(user_id, user.get("roleId"), latest, input_hash, payload)
    gate = record_stage_gate(data_version=latest, stage="operating_unit_snapshot_ready", status="completed", input_payload={"sourceCounts": source_counts, "storeCount": len(stores)}, output_payload={"snapshotKey": snapshot_key, "storeRows": len(stores)}, user_id=user_id, upstream_stage="operating_objects_ready", output_ref=snapshot_key)
    payload["pipelineGate"] = stage_summary(latest, user_id=user_id, limit=20)
    payload["latestGate"] = gate
    return payload


def get_operating_unit_snapshot(user_id: str | None = None, data_version: str | None = None, *, allow_build: bool = True) -> Dict[str, Any]:
    cached = _read_snapshot(user_id, data_version)
    if cached:
        cached["pipelineGate"] = stage_summary(cached.get("syncState", {}).get("latestDataVersion") or data_version, user_id=user_id, limit=20)
        return cached
    if allow_build:
        return materialize_operating_unit_snapshot(user_id, data_version, force=False)
    user = current_user(user_id) if user_id else {}
    return {
        "version": OPERATING_UNIT_SNAPSHOT_VERSION,
        "hasData": False,
        "unitName": "经营单元",
        "syncState": {"label": "快照未生成", "status": "snapshot_missing", "latestDataVersion": data_version},
        "viewer": {"id": user.get("id"), "roleId": user.get("roleId"), "roleName": user.get("roleName")},
        "metrics": [],
        "storeRows": [],
        "operatingJudgment": {"title": "经营判断", "summary": "经营页未找到快照，且当前接口不允许同步构建。", "priority": "执行pipeline快照生成站。", "mainRisk": "快照缺失"},
        "tasks": {},
        "objectStore": {},
        "pipelineGate": stage_summary(data_version, user_id=user_id, limit=20),
        "rule": "V12.13：页面读取不触发上游重计算。",
    }
