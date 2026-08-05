"""V22.2.5 Pipeline Live read model.

Batch truth comes from ``pipeline_jobs`` / ``station_queue``. Product truth comes
from reference-only ``pipeline_items`` columns. The read model never reads the
retired pipeline payload and never disguises a failed or replaying batch as an API
loading state.
"""
from __future__ import annotations

import copy
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Set

from src.repositories.sqlite_repository import connect, loads, run_with_retry
from src.services.artifact_transport_service import resolve_artifact

PIPELINE_LIVE_READ_MODEL_VERSION = "22.2.5"

NODE_ORDER = [
    "数据中台",
    "事实引擎",
    "信号引擎",
    "RAG 上下文",
    "Agent 研判",
    "动作矩阵",
    "Agent2 动作方案",
    "SOP 生成",
    "任务池",
    "任务闭环",
]

STAGE_NODE = {
    "batch_created": "数据中台",
    "data_received": "数据中台",
    "schema_ready": "数据中台",
    "fact_ready": "事实引擎",
    "product_master_ready": "事实引擎",
    "metric_snapshot_ready": "事实引擎",
    "context_bundle_ready": "RAG 上下文",
    "quality_gate_ready": "RAG 上下文",
    "signal_admission_completed": "信号引擎",
    "observed_soft_gate": "信号引擎",
    "signal_admitted": "信号引擎",
    "agent1_pending": "Agent 研判",
    "agent1_running": "Agent 研判",
    "agent1_failed": "Agent 研判",
    "agent1_completed": "Agent 研判",
    "agent1_output_invalid": "Agent 研判",
    "action_pack_ready": "动作矩阵",
    "action_pack_invalid": "动作矩阵",
    "agent2_running": "Agent2 动作方案",
    "agent2_failed": "Agent2 动作方案",
    "agent2_output_invalid": "Agent2 动作方案",
    "agent2_completed": "Agent2 动作方案",
    "sop_mapped": "SOP 生成",
    "task_admitted": "任务池",
    "read_model_ready": "任务闭环",
    "task_loop_ready": "任务闭环",
}

STAGE_LABELS = {
    "batch_created": "批次建立",
    "data_received": "数据接收",
    "schema_ready": "字段清洗",
    "fact_ready": "事实入库",
    "product_master_ready": "商品建档",
    "metric_snapshot_ready": "指标快照",
    "context_bundle_ready": "商品上下文",
    "quality_gate_ready": "质量校验",
    "signal_admission_completed": "信号准入汇总",
    "observed_soft_gate": "观察沉淀",
    "signal_admitted": "信号准入",
    "agent1_pending": "Agent1排队",
    "agent1_running": "Agent1运行",
    "agent1_completed": "Agent1完成",
    "agent1_output_invalid": "Agent1输出异常",
    "agent1_failed": "Agent1失败",
    "action_pack_ready": "动作矩阵补包",
    "action_pack_invalid": "动作补包异常",
    "agent2_running": "Agent2运行",
    "agent2_completed": "Agent2完成",
    "agent2_output_invalid": "Agent2输出异常",
    "agent2_failed": "Agent2失败",
    "sop_mapped": "SOP完成",
    "task_admitted": "任务入池",
    "read_model_ready": "读模型",
    "task_loop_ready": "任务闭环",
}

STATION_NODE = {
    "report_receive_station": "数据中台",
    "report_schema_station": "数据中台",
    "report_fact_station": "事实引擎",
    "product_master_station": "事实引擎",
    "product_metric_snapshot_station": "事实引擎",
    "full_product_bundle_station": "RAG 上下文",
    "bundle_validation_station": "RAG 上下文",
    "product_signal_admission_station": "信号引擎",
}

STATION_LABELS = {
    "report_receive_station": "报表接收",
    "report_schema_station": "字段映射",
    "report_fact_station": "事实生成",
    "product_master_station": "商品建档",
    "product_metric_snapshot_station": "指标快照",
    "full_product_bundle_station": "商品证据包",
    "bundle_validation_station": "质量校验",
    "product_signal_admission_station": "商品信号准入",
}

COMPLETED_STAGES = {
    "schema_ready",
    "fact_ready",
    "product_master_ready",
    "metric_snapshot_ready",
    "context_bundle_ready",
    "quality_gate_ready",
    "signal_admission_completed",
    "signal_admitted",
    "observed_soft_gate",
    "agent1_completed",
    "action_pack_ready",
    "agent2_completed",
    "sop_mapped",
    "task_admitted",
    "read_model_ready",
    "task_loop_ready",
}
RUNNING_STAGES = {"agent1_running", "agent2_running"}
FAILED_STAGES = {
    "agent1_failed",
    "agent1_output_invalid",
    "action_pack_invalid",
    "agent2_failed",
    "agent2_output_invalid",
}
QUEUED_STATUSES = {"queued", "ready", "retry", "pending"}
ACTIVE_PRODUCT_STAGES = {
    "agent1_pending",
    "agent1_running",
    "action_pack_ready",
    "agent2_running",
    "sop_mapped",
}

_LAST_GOOD_SNAPSHOT: Dict[str, Dict[str, Any]] = {}


def now_iso() -> str:
    return datetime.now().isoformat()


def _is_locked_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def _table_exists(conn: Any, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _load_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        parsed = loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _latest_data_version() -> str | None:
    def op() -> str | None:
        candidates: List[tuple[str, str]] = []
        with connect() as conn:
            for table in ("pipeline_items", "pipeline_jobs"):
                if not _table_exists(conn, table):
                    continue
                row = conn.execute(
                    f"""
                    SELECT data_version, MAX(updated_at) AS max_updated_at
                    FROM {table}
                    WHERE data_version IS NOT NULL AND data_version != ''
                    GROUP BY data_version
                    ORDER BY max_updated_at DESC, data_version DESC LIMIT 1
                    """
                ).fetchone()
                if row and row["data_version"]:
                    candidates.append((str(row["max_updated_at"] or ""), str(row["data_version"])))
        return max(candidates)[1] if candidates else None

    return run_with_retry(op, attempts=4, delay=0.2)


def _load_product_rows(data_version: str | None, *, limit: int = 1000) -> List[Dict[str, Any]]:
    def op() -> List[Dict[str, Any]]:
        with connect() as conn:
            if not _table_exists(conn, "pipeline_items"):
                return []
            params: List[Any] = []
            where = ""
            if data_version:
                where = "WHERE data_version=?"
                params.append(data_version)
            params.append(int(limit))
            rows = conn.execute(
                f"SELECT * FROM pipeline_items {where} ORDER BY updated_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            item_id = str(row.get("item_id") or "")
            if item_id.startswith("PI-BATCH"):
                continue
            if str(row.get("current_stage") or "") == "signal_admission_completed":
                continue
            if any(
                row.get(key)
                for key in (
                    "product_id",
                    "signal_id",
                    "package_id",
                    "decision_id",
                    "task_id",
                )
            ):
                result.append(row)
        return result

    return run_with_retry(op, attempts=4, delay=0.2)


def _load_batch_state(data_version: str | None) -> Dict[str, Any]:
    def op() -> Dict[str, Any]:
        with connect() as conn:
            if not _table_exists(conn, "pipeline_jobs"):
                return {}
            job = conn.execute(
                """
                SELECT * FROM pipeline_jobs
                WHERE system_type='task_generation'
                  AND COALESCE(data_version,'')=COALESCE(?,'')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (data_version,),
            ).fetchone()
            if not job:
                return {}
            stations = []
            if _table_exists(conn, "station_queue"):
                stations = conn.execute(
                    """
                    SELECT station_id,stage,status,input_ref,output_ref,error_message,
                           attempt_count,max_attempts,updated_at,payload
                    FROM station_queue
                    WHERE parent_job_id=?
                    ORDER BY created_at ASC, updated_at ASC
                    """,
                    (job["job_id"],),
                ).fetchall()
        job_dict = dict(job)
        station_items = [dict(item) for item in stations]
        active = [
            item
            for item in station_items
            if item.get("status") in {"running", "retry", "queued", "failed"}
        ]
        latest = active[-1] if active else (station_items[-1] if station_items else {})
        status = str(job_dict.get("status") or latest.get("status") or "waiting")
        if any(item.get("status") == "failed" for item in station_items):
            status = "failed"
        elif any(item.get("status") == "retry" for item in station_items):
            status = "retry"
        elif any(item.get("status") == "running" for item in station_items):
            status = "running"
        elif any(item.get("status") == "queued" for item in station_items):
            status = "queued"
        current_station = str(job_dict.get("current_station") or latest.get("station_id") or "")
        output_ref = str(job_dict.get("output_ref") or latest.get("output_ref") or "")
        artifact_summary: Dict[str, Any] = {}
        if output_ref.startswith("ART-"):
            try:
                value = resolve_artifact(output_ref)
                if isinstance(value, dict):
                    artifact_summary = {
                        key: value.get(key)
                        for key in (
                            "businessOutputType",
                            "baselineOnly",
                            "baselineNoPrevious",
                            "fullSignalCount",
                            "qualifiedSignalCount",
                            "admittedSignalCount",
                            "observedSignalCount",
                            "agent1PendingItemCount",
                        )
                        if value.get(key) is not None
                    }
            except Exception as exc:
                artifact_summary = {"artifactReadError": str(exc)[:240]}
        error = str(job_dict.get("error_message") or latest.get("error_message") or "").strip()
        return {
            "jobId": job_dict.get("job_id"),
            "status": status,
            "currentStation": current_station,
            "stationLabel": STATION_LABELS.get(current_station, current_station or "等待数据"),
            "errorMessage": error,
            "outputRef": output_ref,
            "updatedAt": job_dict.get("updated_at") or latest.get("updated_at"),
            "stationJobs": [
                {
                    "stationId": item.get("station_id"),
                    "label": STATION_LABELS.get(str(item.get("station_id") or ""), item.get("station_id")),
                    "stage": item.get("stage"),
                    "status": item.get("status"),
                    "attemptCount": int(item.get("attempt_count") or 0),
                    "maxAttempts": int(item.get("max_attempts") or 0),
                    "errorMessage": item.get("error_message"),
                    "outputRef": item.get("output_ref"),
                    "updatedAt": item.get("updated_at"),
                }
                for item in station_items
            ],
            "artifactSummary": artifact_summary,
            "realFailureCanBecomeCompleted": False,
        }

    return run_with_retry(op, attempts=4, delay=0.2)


def _load_event_counts(data_version: str | None) -> Counter[str]:
    def op() -> Counter[str]:
        with connect() as conn:
            if not _table_exists(conn, "pipeline_item_events"):
                return Counter()
            rows = conn.execute(
                """
                SELECT stage,status,COUNT(*) AS n FROM pipeline_item_events
                WHERE COALESCE(data_version,'')=COALESCE(?,'')
                GROUP BY stage,status
                """,
                (data_version,),
            ).fetchall()
        counts: Counter[str] = Counter()
        for row in rows:
            counts[f"{row['stage']}:{row['status']}"] += int(row["n"] or 0)
        return counts

    return run_with_retry(op, attempts=4, delay=0.2)


def _bucket(stage: str, status: Any) -> str:
    raw = str(status or "").lower()
    if raw in {"failed", "error"} or stage in FAILED_STAGES:
        return "failed"
    if raw in {"running", "processing"} or stage in RUNNING_STAGES:
        return "running"
    if raw in QUEUED_STATUSES and stage not in COMPLETED_STAGES:
        return "queued"
    if stage in COMPLETED_STAGES or raw in {"completed", "done", "passed", "observed"}:
        return "completed"
    return "queued"


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in {"", "—", "UNKNOWN", "未识别", "None", "null"} else text


def _identity(row: Dict[str, Any]) -> str:
    store = _clean(row.get("store_id"))
    product = _clean(row.get("product_id"))
    if product:
        return f"{store}::{product}" if store else product
    for key in ("package_id", "signal_id", "decision_id", "task_id", "item_id"):
        value = _clean(row.get(key))
        if value:
            return f"{store}::{value}" if store else value
    return "unknown"


def _empty_stages() -> List[Dict[str, Any]]:
    return [
        {
            "node": node,
            "label": node,
            "total": 0,
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "observed": 0,
            "admitted": 0,
            "currentCount": 0,
            "historyCompleted": 0,
            "countBasis": "batch_station_plus_product_identity",
            "status": "waiting",
        }
        for node in NODE_ORDER
    ]


def _status_from_counts(stage: Dict[str, Any]) -> str:
    if stage.get("failed"):
        return "attention"
    if stage.get("running"):
        return "running"
    if stage.get("queued"):
        return "queued"
    if stage.get("completed") or stage.get("observed") or stage.get("admitted"):
        return "completed"
    return "waiting"


def _snapshot_id(data_version: str | None, rows: List[Dict[str, Any]], batch: Dict[str, Any]) -> str:
    markers = [str(row.get("updated_at") or "") for row in rows]
    markers.append(str(batch.get("updatedAt") or ""))
    return f"{data_version or 'latest'}:{len(rows)}:{max(markers) if markers else ''}"


def _locked_payload(data_version: str | None, exc: BaseException) -> Dict[str, Any]:
    stale = _LAST_GOOD_SNAPSHOT.get(str(data_version or "__latest__")) or _LAST_GOOD_SNAPSHOT.get("__latest__")
    if stale:
        payload = copy.deepcopy(stale)
        payload.update(
            {
                "version": PIPELINE_LIVE_READ_MODEL_VERSION,
                "generatedAt": now_iso(),
                "stale": True,
                "snapshotStatus": "locked_retrying",
                "lockedRetrying": True,
                "lockError": str(exc)[:240],
                "headline": payload.get("headline") or "数据库写入中，保留上一份稳定快照",
            }
        )
        return payload
    return {
        "version": PIPELINE_LIVE_READ_MODEL_VERSION,
        "ready": False,
        "dataVersion": data_version,
        "displaySnapshotId": f"locked:{data_version or 'latest'}",
        "generatedAt": now_iso(),
        "headline": "数据库写入中，稍后自动刷新",
        "flowStatus": "writing",
        "snapshotStatus": "locked_retrying",
        "lockedRetrying": True,
        "stale": False,
        "baselineOnly": False,
        "batchState": {},
        "summary": {"totalItems": 0, "batchCount": 0, "running": 0, "queued": 0, "failed": 0},
        "stages": _empty_stages(),
        "stageCounts": {},
        "eventStageCounts": {},
        "items": [],
        "lightweight": True,
        "lockError": str(exc)[:240],
    }


def _read_pipeline_live_model(data_version: str | None = None, *, limit: int = 80) -> Dict[str, Any]:
    resolved = data_version or _latest_data_version()
    rows = _load_product_rows(resolved, limit=1000)
    batch = _load_batch_state(resolved)
    events = _load_event_counts(resolved)

    node_sets: Dict[str, Dict[str, Set[str]]] = {
        node: defaultdict(set) for node in NODE_ORDER
    }
    stage_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    product_keys: Set[str] = set()
    active_items: List[Dict[str, Any]] = []
    observed = signal_admitted = task_admitted = task_admission_failed = 0

    for row in rows:
        stage = str(row.get("current_stage") or "batch_created")
        bucket = _bucket(stage, row.get("status"))
        node = STAGE_NODE.get(stage, "数据中台")
        identity = _identity(row)
        product_keys.add(identity)
        node_sets[node][bucket].add(identity)
        node_sets[node]["total"].add(identity)
        stage_counts[f"{stage}:{bucket}"] += 1
        if stage == "observed_soft_gate":
            observed += 1
            node_sets[node]["observed"].add(identity)
        if stage in {"signal_admitted", "agent1_pending"}:
            signal_admitted += 1
            node_sets["信号引擎"]["admitted"].add(identity)
        if stage == "task_admitted":
            if row.get("task_id") and bucket == "completed":
                task_admitted += 1
                node_sets[node]["admitted"].add(identity)
            elif bucket == "failed":
                task_admission_failed += 1
        family = _clean(row.get("action_family"))
        if family:
            family_counts[family] += 1
        if bucket in {"running", "queued", "failed"} and stage != "observed_soft_gate":
            active_items.append(
                {
                    "itemId": row.get("item_id"),
                    "productId": row.get("product_id"),
                    "storeId": row.get("store_id"),
                    "packageId": row.get("package_id"),
                    "decisionId": row.get("decision_id"),
                    "taskId": row.get("task_id"),
                    "identityKey": identity,
                    "title": row.get("product_id") or row.get("signal_id") or "商品包",
                    "kind": "任务" if row.get("task_id") else "商品包",
                    "node": node,
                    "currentStage": stage,
                    "stageLabel": STAGE_LABELS.get(stage, stage),
                    "status": row.get("status"),
                    "bucket": bucket,
                    "actionFamily": family or "未锁定",
                    "updatedAt": row.get("updated_at"),
                }
            )

    batch_token = f"batch::{batch.get('jobId')}" if batch.get("jobId") else ""
    for station in batch.get("stationJobs") or []:
        if station.get("status") == "disabled":
            continue
        node = STATION_NODE.get(str(station.get("stationId") or ""), "数据中台")
        bucket = _bucket(str(station.get("stage") or ""), station.get("status"))
        token = batch_token or f"batch::{resolved or 'latest'}"
        node_sets[node][bucket].add(token)
        node_sets[node]["total"].add(token)
        stage_counts[f"station:{station.get('stationId')}:{bucket}"] += 1

    stages: List[Dict[str, Any]] = []
    for node in NODE_ORDER:
        sets = node_sets[node]
        card = {
            "node": node,
            "label": node,
            "total": len(sets.get("total", set())),
            "queued": len(sets.get("queued", set())),
            "running": len(sets.get("running", set())),
            "completed": len(sets.get("completed", set())),
            "failed": len(sets.get("failed", set())),
            "observed": len(sets.get("observed", set())),
            "admitted": len(sets.get("admitted", set())),
            "historyCompleted": 0,
            "countBasis": "batch_station_plus_product_identity",
        }
        card["currentCount"] = int(
            card["running"]
            or card["queued"]
            or card["failed"]
            or card["completed"]
            or card["observed"]
            or card["admitted"]
            or 0
        )
        card["status"] = _status_from_counts(card)
        stages.append(card)

    event_node_history: Dict[str, Counter[str]] = {node: Counter() for node in NODE_ORDER}
    for key, count in events.items():
        stage_name, event_status = key.split(":", 1)
        node = STAGE_NODE.get(stage_name)
        if node:
            event_node_history[node][_bucket(stage_name, event_status)] += int(count)
    for card in stages:
        card["historyCompleted"] = int(event_node_history[card["node"]].get("completed", 0))

    product_running = sum(card["running"] for card in stages)
    product_queued = sum(card["queued"] for card in stages)
    product_failed = sum(card["failed"] for card in stages)
    batch_status = str(batch.get("status") or "")
    baseline_only = bool(
        (batch.get("artifactSummary") or {}).get("baselineOnly")
        or (batch.get("artifactSummary") or {}).get("baselineNoPrevious")
    )

    if batch_status == "failed":
        headline = f"最新批次在{batch.get('stationLabel') or '当前站点'}失败"
        if batch.get("errorMessage"):
            headline += f"：{str(batch.get('errorMessage'))[:120]}"
        flow_status = "attention"
        snapshot_status = "blocked"
    elif batch_status == "retry":
        headline = f"最新批次正从{batch.get('stationLabel') or '真实断点'}重试"
        flow_status = "running"
        snapshot_status = "replaying"
    elif batch_status in {"running", "queued"} and not rows:
        headline = f"最新批次正在{batch.get('stationLabel') or '进入流水线'}"
        flow_status = "running"
        snapshot_status = "running"
    elif task_admitted:
        headline = f"{task_admitted}个任务已入池，观察沉淀{observed}"
        flow_status = "completed" if not product_running and not product_queued else "running"
        snapshot_status = "ready"
    elif product_running or product_queued:
        headline = f"{len(product_keys)}个商品包正在流水线中"
        flow_status = "running"
        snapshot_status = "running"
    elif product_failed:
        headline = f"{product_failed}个商品处理失败，已定位到具体阶段"
        flow_status = "attention"
        snapshot_status = "blocked"
    elif baseline_only:
        headline = "首份可比基线已建立，等待下一份报表形成经营变化"
        flow_status = "baseline"
        snapshot_status = "baseline"
    elif rows:
        headline = f"本轮处理完成 · 观察沉淀{observed}"
        flow_status = "completed"
        snapshot_status = "ready"
    elif batch:
        headline = f"最新批次已完成{batch.get('stationLabel') or '当前阶段'}"
        flow_status = "completed" if batch_status == "completed" else "waiting"
        snapshot_status = "ready"
    else:
        headline = "等待数据接入"
        flow_status = "waiting"
        snapshot_status = "empty"

    active_items.sort(
        key=lambda item: (
            0 if item["bucket"] == "running" else 1 if item["bucket"] == "queued" else 2,
            str(item.get("updatedAt") or ""),
        )
    )
    ready = bool(rows or batch)
    result = {
        "version": PIPELINE_LIVE_READ_MODEL_VERSION,
        "ready": ready,
        "interfaceStatus": "ok",
        "dataVersion": resolved,
        "displaySnapshotId": _snapshot_id(resolved, rows, batch),
        "generatedAt": now_iso(),
        "headline": headline,
        "flowStatus": flow_status,
        "snapshotStatus": snapshot_status,
        "lockedRetrying": False,
        "stale": False,
        "baselineOnly": baseline_only,
        "batchState": batch,
        "summary": {
            "totalItems": len(product_keys),
            "productCount": len(product_keys),
            "batchCount": 1 if batch else 0,
            "running": product_running,
            "queued": product_queued,
            "failed": product_failed + (1 if batch_status == "failed" else 0),
            "agent2Completed": next((card["completed"] for card in stages if card["node"] == "Agent2 动作方案"), 0),
            "sopMapped": next((card["completed"] for card in stages if card["node"] == "SOP 生成"), 0),
            "taskAdmitted": task_admitted,
            "taskAdmissionFailed": task_admission_failed,
            "ragCompleted": next((card["completed"] for card in stages if card["node"] == "RAG 上下文"), 0),
            "baselineEstablished": len(product_keys) if baseline_only else 0,
            "observedDeposited": observed,
            "signalAdmitted": signal_admitted,
        },
        "stages": stages,
        "stageCounts": dict(stage_counts),
        "eventStageCounts": dict(events),
        "eventHistoryOnly": True,
        "byActionFamily": dict(family_counts),
        "items": active_items[: int(limit)],
        "lightweight": True,
        "payloadRead": False,
        "batchTruthSource": "pipeline_jobs+station_queue",
        "productTruthSource": "pipeline_items columns+artifactRefs",
        "countBasis": "batch station state plus current product identity",
        "rule": "V22.2.5 shows real batch failure/replay/baseline states and never maps them to interface loading.",
    }
    return result


def read_pipeline_live_model(data_version: str | None = None, *, limit: int = 80) -> Dict[str, Any]:
    try:
        result = run_with_retry(
            lambda: _read_pipeline_live_model(data_version=data_version, limit=limit),
            attempts=3,
            delay=0.25,
        )
        if result.get("ready"):
            _LAST_GOOD_SNAPSHOT[str(result.get("dataVersion") or data_version or "__latest__")] = copy.deepcopy(result)
            _LAST_GOOD_SNAPSHOT["__latest__"] = copy.deepcopy(result)
        return result
    except sqlite3.OperationalError as exc:
        if _is_locked_error(exc):
            return _locked_payload(data_version, exc)
        raise


__all__ = ["PIPELINE_LIVE_READ_MODEL_VERSION", "read_pipeline_live_model"]
