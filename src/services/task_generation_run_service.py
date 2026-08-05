from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads

TASK_GENERATION_RUN_VERSION = "17.7"
COVERAGE_THRESHOLD = 0.9


def now_iso() -> str:
    return datetime.now().isoformat()


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _present(value: Any) -> bool:
    return value not in {None, "", "null", "None", "UNKNOWN", "—"}


def _table(conn: Any, name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _has_col(conn: Any, table: str, col: str) -> bool:
    if not _table(conn, table):
        return False
    try:
        return col in [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return False


def _count(conn: Any, table: str, data_version: str | None = None) -> int:
    if not _table(conn, table):
        return 0
    try:
        if data_version and _has_col(conn, table, "data_version"):
            return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE data_version=?", (data_version,)).fetchone()["count"] or 0)
        return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"] or 0)
    except Exception:
        return 0


def ensure_task_generation_run_tables() -> None:
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_generation_runs_v14 (
                run_id TEXT PRIMARY KEY,
                data_version TEXT,
                status TEXT NOT NULL,
                input_bundle_count INTEGER DEFAULT 0,
                agent_judgment_count INTEGER DEFAULT 0,
                product_judgment_package_count INTEGER DEFAULT 0,
                task_decision_count INTEGER DEFAULT 0,
                formal_task_count INTEGER DEFAULT 0,
                observe_only_count INTEGER DEFAULT 0,
                data_gap_task_count INTEGER DEFAULT 0,
                manager_review_count INTEGER DEFAULT 0,
                task_pool_created_count INTEGER DEFAULT 0,
                frontend_task_view_count INTEGER DEFAULT 0,
                reason TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        ensure_columns(conn, "task_generation_runs_v14", {"data_version": "TEXT", "status": "TEXT", "payload": "TEXT", "reason": "TEXT", "created_at": "TEXT", "updated_at": "TEXT"})
        conn.commit()


def _latest_run(conn: Any, data_version: str | None = None) -> Dict[str, Any] | None:
    ensure_task_generation_run_tables()
    if data_version:
        row = conn.execute("SELECT payload FROM task_generation_runs_v14 WHERE data_version=? ORDER BY created_at DESC LIMIT 1", (data_version,)).fetchone()
    else:
        row = conn.execute("SELECT payload FROM task_generation_runs_v14 ORDER BY created_at DESC LIMIT 1").fetchone()
    payload = _load(row["payload"]) if row else None
    if payload:
        payload["version"] = payload.get("version") or TASK_GENERATION_RUN_VERSION
    return payload


def _latest_bundle(conn: Any) -> Dict[str, Any]:
    if not _table(conn, "product_signal_snapshots_v14"):
        return {"count": 0, "dataVersion": None, "baselineNoPrevious": False}
    try:
        row = conn.execute("SELECT data_version, payload FROM product_signal_snapshots_v14 ORDER BY updated_at DESC, created_at DESC LIMIT 1").fetchone()
    except Exception:
        row = conn.execute("SELECT data_version, payload FROM product_signal_snapshots_v14 ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        return {"count": 0, "dataVersion": None, "baselineNoPrevious": False}
    payload = _load(row["payload"])
    bundles = payload.get("productSignalPackages") or payload.get("signals") or payload.get("products") or []
    count = len(bundles) if isinstance(bundles, list) else int(payload.get("productSignalPackageCount") or payload.get("productSignalCount") or 0)
    prev_snapshot = payload.get("previousSnapshotId")
    prev_version = payload.get("previousDataVersion")
    return {"count": int(count or 0), "dataVersion": row["data_version"], "previousSnapshotId": prev_snapshot, "previousDataVersion": prev_version, "baselineNoPrevious": bool(count and not _present(prev_snapshot) and not _present(prev_version))}


def _latest_rag(conn: Any, data_version: str | None = None) -> Dict[str, Any]:
    if not _table(conn, "rag_context_snapshots_v14"):
        return {"version": None, "ragPackageContextCount": 0, "expectedRagPackageContextCount": 0, "contextCoverageStatus": "missing", "itemsLen": 0}
    try:
        if data_version:
            row = conn.execute("SELECT payload, created_at FROM rag_context_snapshots_v14 WHERE data_version=? ORDER BY created_at DESC LIMIT 1", (data_version,)).fetchone()
        else:
            row = conn.execute("SELECT payload, created_at FROM rag_context_snapshots_v14 ORDER BY created_at DESC LIMIT 1").fetchone()
    except Exception:
        row = None
    if not row:
        return {"version": None, "ragPackageContextCount": 0, "expectedRagPackageContextCount": 0, "contextCoverageStatus": "missing", "itemsLen": 0}
    payload = _load(row["payload"])
    items = payload.get("items") or []
    coverage_status = payload.get("contextCoverageStatus")
    if not coverage_status:
        coverage_status = "legacy" if payload.get("version") != "17.6" else "unknown"
    return {
        "version": payload.get("version"),
        "ragPackageContextCount": int(payload.get("ragPackageContextCount") or 0),
        "expectedRagPackageContextCount": int(payload.get("expectedRagPackageContextCount") or 0),
        "contextCoverageStatus": coverage_status,
        "itemsLen": len(items) if isinstance(items, list) else 0,
        "createdAt": row["created_at"],
    }


def record_task_generation_run(*, data_version: str | None, input_bundle_count: int = 0, agent_judgment_count: int = 0, product_judgment_package_count: int = 0, identity_gap_count: int = 0, task_decision_count: int = 0, by_decision: Dict[str, int] | None = None, streamed_task_snapshot_count: int = 0, task_pool_created_count: int = 0, skipped_formal_count: int = 0, zero_task_reasons: List[str] | None = None, agent1_api_call_count: int = 0, rag_retrieval_count: int = 0, api_budget_violation: bool = False, agent_budget_summary: Dict[str, Any] | None = None, total_agent_call_count: int = 0, total_agent_budget: int = 8, source: str = "station_alignment_v177") -> Dict[str, Any]:
    ensure_task_generation_run_tables()
    by_decision = by_decision or {}
    zero_task_reasons = zero_task_reasons or []
    is_baseline = "first_report_baseline" in str(source)
    is_no_signal = "completed_no_signal" in str(source)
    formal = int(by_decision.get("create_task_snapshot", 0) or 0) + int(by_decision.get("manager_review_required", 0) or 0)
    if is_baseline:
        status = "baseline_completed"
        reason = "首份报表已建立商品与指标基线，等待下一份报表形成变化判断。"
    elif task_pool_created_count:
        status = "completed_with_product_tasks"
        reason = f"V17.7站点链路完成，生成 {task_pool_created_count} 个任务。"
    elif is_no_signal:
        status = "completed_no_signal"
        reason = zero_task_reasons[0] if zero_task_reasons else "本轮运营思维图谱/判断Agent未输出有效经营信号，链路已关闭。"
    elif product_judgment_package_count:
        status = "evidence_package_completed"
        reason = zero_task_reasons[0] if zero_task_reasons else f"V17.7证据合包完成，{product_judgment_package_count} 个判断包已挂回全量包证据。"
    else:
        status = "completed_no_formal_task"
        reason = zero_task_reasons[0] if zero_task_reasons else "V17.7 station run completed."
    now = now_iso()
    run_id = f"TGR-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"
    package_completion_rate = 1.0 if product_judgment_package_count else 0
    legacy_coverage_rate = round(product_judgment_package_count / input_bundle_count, 4) if input_bundle_count else 0
    payload = {
        "version": TASK_GENERATION_RUN_VERSION,
        "runId": run_id,
        "dataVersion": data_version,
        "status": status,
        "source": source,
        "baselineMode": "first_report" if is_baseline else "normal_delta",
        "baselineNoPrevious": bool(is_baseline),
        "inputBundleCount": int(input_bundle_count or 0),
        "fullProductBundleCount": int(input_bundle_count or 0),
        "agentJudgmentCount": int(agent_judgment_count or 0),
        "productJudgmentPackageCount": int(product_judgment_package_count or 0),
        "expectedPackageCount": int(product_judgment_package_count or 0),
        "packageCompletionRate": package_completion_rate,
        "legacyBundleCoverageRateForDebugOnly": legacy_coverage_rate,
        "productJudgmentCoverageStatus": "baseline" if is_baseline else "evidence_join" if product_judgment_package_count else "no_signal" if is_no_signal else "empty",
        "taskDecisionCount": int(task_decision_count or 0),
        "formalTaskCount": formal,
        "taskPoolCreatedCount": int(task_pool_created_count or 0),
        "reason": reason,
        "createdAt": now,
        "updatedAt": now,
        "rule": "V17.7: data-line reads evidence package completion and current V17.6 RAG coverage separately.",
    }
    with connect() as conn:
        frontend_count = _count(conn, "frontend_task_view", data_version)
        payload["frontendTaskViewCount"] = frontend_count
        conn.execute("INSERT INTO task_generation_runs_v14 (run_id, data_version, status, input_bundle_count, agent_judgment_count, product_judgment_package_count, task_decision_count, formal_task_count, observe_only_count, data_gap_task_count, manager_review_count, task_pool_created_count, frontend_task_view_count, reason, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (run_id, data_version, status, int(input_bundle_count or 0), int(agent_judgment_count or 0), int(product_judgment_package_count or 0), int(task_decision_count or 0), formal, 0, 0, 0, int(task_pool_created_count or 0), frontend_count, reason, dumps(payload), now, now))
        conn.commit()
    return payload


def _station(id_: str, label: str, status: str, note: str = "") -> Dict[str, str]:
    return {"id": id_, "label": label, "status": status, "note": note}


def read_data_line_status() -> Dict[str, Any]:
    ensure_task_generation_run_tables()
    with connect() as conn:
        bundle = _latest_bundle(conn)
        data_version = bundle.get("dataVersion")
        latest_any_run = _latest_run(conn)
        latest_run = _latest_run(conn, data_version) if data_version else latest_any_run
        product_master_count = _count(conn, "system_product_snapshots_v14", data_version)
        raw_count = _count(conn, "agent_product_judgments_v15", data_version)
        package_count = _count(conn, "product_judgment_packages_v15", data_version)
        decision_count = _count(conn, "task_generation_decisions_v15", data_version)
        pool_count = _count(conn, "task_pool_entries", data_version)
        frontend_task_count = _count(conn, "frontend_task_view", data_version)
        frontend_product_count = _count(conn, "frontend_product_view", data_version)
        rag = _latest_rag(conn, data_version)
    bundle_count = int(bundle.get("count") or 0)
    baseline = bool(bundle.get("baselineNoPrevious"))
    latest_status = str((latest_run or {}).get("status") or "")
    no_signal = latest_status == "completed_no_signal"
    rag_count = int(rag.get("ragPackageContextCount") or 0)
    expected_rag = int(rag.get("expectedRagPackageContextCount") or 0)
    rag_version = str(rag.get("version") or "")
    rag_is_current = rag_version == "17.6" and (rag_count >= package_count if package_count else True)
    if baseline:
        raw_count = package_count = decision_count = pool_count = frontend_task_count = rag_count = expected_rag = 0
    if baseline:
        line_status = "baseline_completed"
    elif pool_count:
        line_status = "completed"
    elif decision_count:
        line_status = "mapping_completed"
    elif rag_is_current and package_count:
        line_status = "rag_completed"
    elif package_count:
        line_status = "evidence_package_completed"
    elif no_signal:
        line_status = "completed_no_signal"
    else:
        line_status = "processing" if bundle_count else "waiting"
    if baseline:
        headline = "首份报表已建立基线，商品 %d，正式任务 0，等待下一份报表形成变化判断" % bundle_count
    elif pool_count:
        headline = "%d 条结构化判断已完成证据合包，RAG上下文 %d/%d，正式任务 %d" % (raw_count, rag_count, max(expected_rag, package_count), pool_count)
    elif decision_count:
        headline = "%d 条结构化判断已完成证据合包，RAG上下文 %d/%d，任务映射决策 %d" % (raw_count, rag_count, max(expected_rag, package_count), decision_count)
    elif package_count and rag_is_current:
        headline = "%d 条结构化判断已完成证据合包，RAG上下文 %d/%d，等待任务映射" % (raw_count, rag_count, max(expected_rag, package_count))
    elif package_count:
        headline = "%d 条结构化判断已完成证据合包，等待 V17.6 RAG 上下文刷新" % raw_count
    elif no_signal:
        headline = "本轮商品 %d 已完成图谱判断，无有效经营信号，正式任务 0" % bundle_count
    elif bundle_count:
        headline = "新报表已接入，等待本轮商品判断 Agent"
    else:
        headline = "等待数据接入"
    package_note = "首份基线" if baseline else "证据合包 %d/%d" % (package_count, package_count) if package_count else "无有效判断" if no_signal else "等待"
    rag_note = "首份跳过" if baseline else "无任务" if no_signal else "上下文 %d/%d" % (rag_count, max(expected_rag, package_count)) if rag_is_current and package_count else "旧RAG/待刷新" if package_count else "等待"
    stations = [
        _station("report_receive_station", "接入", "passed" if bundle_count else "waiting", "数据入库"),
        _station("report_schema_station", "结构", "passed" if bundle_count else "waiting", "表头/日期"),
        _station("report_fact_station", "事实", "passed" if bundle_count else "waiting", "商品/店铺/流量"),
        _station("product_master_station", "主档", "passed" if product_master_count or bundle_count else "waiting", "商品 %d" % (product_master_count or bundle_count)),
        _station("product_metric_snapshot_station", "指标", "passed" if bundle_count else "waiting", "基线" if baseline else "ROI/日期"),
        _station("full_product_bundle_station", "全量包", "passed" if bundle_count else "waiting", "%d 个包" % bundle_count),
        _station("bundle_validation_station", "验收", "passed" if bundle_count else "waiting", "基线" if baseline else "事实层"),
        _station("product_judgment_agent_station", "判断", "skipped" if baseline else "passed" if raw_count else "empty" if no_signal else "current", "首份跳过" if baseline else "%d 条" % raw_count),
        _station("product_judgment_package_station", "合包", "skipped" if baseline else "passed" if package_count else "empty" if no_signal else "waiting", package_note),
        _station("rag_permission_context_station", "RAG", "skipped" if baseline or no_signal else "passed" if rag_is_current and package_count else "waiting" if package_count else "waiting", rag_note),
        _station("task_mapping_agent_station", "映射", "skipped" if baseline or no_signal else "passed" if decision_count else "waiting", "首份跳过" if baseline else "无任务" if no_signal else "决策 %d" % decision_count),
        _station("task_pool_admission_station", "入池", "empty" if baseline or no_signal else "passed" if pool_count else "waiting", "首份无任务" if baseline else "无任务" if no_signal else "任务 %d" % pool_count),
        _station("frontend_read_model_station", "读模", "passed" if baseline or no_signal or frontend_task_count or frontend_product_count else "waiting", "基线完成" if baseline else "无任务" if no_signal else "任务 %d" % frontend_task_count),
        _station("task_pool_acceptance_station", "验收", "passed" if baseline or no_signal else "waiting", "基线完成" if baseline else "无任务" if no_signal else "轻量首屏"),
    ]
    return {
        "version": TASK_GENERATION_RUN_VERSION,
        "runtimeVersion": TASK_GENERATION_RUN_VERSION,
        "ready": bool(bundle_count or latest_run),
        "lineStatus": line_status,
        "headline": headline,
        "baselineMode": "first_report" if baseline else "normal_delta",
        "baselineNoPrevious": bool(baseline),
        "bundlePreviousSnapshotId": bundle.get("previousSnapshotId"),
        "bundlePreviousDataVersion": bundle.get("previousDataVersion"),
        "dataVersion": data_version,
        "currentDataVersion": data_version,
        "latestBundleDataVersion": data_version,
        "latestRunDataVersion": latest_any_run.get("dataVersion") if latest_any_run else None,
        "newReportPendingJudgment": False if baseline else bool(data_version and latest_any_run and latest_any_run.get("dataVersion") != data_version),
        "formalTaskCount": int(pool_count or 0),
        "taskPoolTotalCount": int(pool_count or 0),
        "agentJudgmentCount": int(raw_count or 0),
        "rawJudgmentCount": int(raw_count or 0),
        "productJudgmentPackageCount": int(package_count or 0),
        "expectedPackageCount": int(package_count or 0),
        "fullProductBundleCount": int(bundle_count or 0),
        "ragVersion": rag_version or None,
        "ragPackageContextCount": int(rag_count or 0),
        "expectedRagPackageContextCount": int(expected_rag or 0),
        "ragContextCoverageStatus": rag.get("contextCoverageStatus"),
        "ragContextItemsLen": int(rag.get("itemsLen") or 0),
        "taskDecisionCount": int(decision_count or 0),
        "inputBundleCount": int(bundle_count or 0),
        "productMasterCount": int(product_master_count or 0),
        "frontendTaskViewCount": int(frontend_task_count or 0),
        "frontendProductViewCount": int(frontend_product_count or 0),
        "productJudgmentCoverageStatus": "baseline" if baseline else "evidence_join" if package_count else "no_signal" if no_signal else "waiting",
        "productJudgmentCoverageRate": 1.0 if (baseline and bundle_count) or package_count else 0,
        "runtimeResidual": False,
        "staleRunIgnored": False,
        "stations": stations,
        "stationAlignment": "v17_7_evidence_package_rag_data_line",
        "latestRun": latest_run,
        "previousRun": None,
        "decisionCounts": {},
        "updatedAt": now_iso(),
        "rule": "V17.7 data-line separates evidence package completion from V17.6 RAG coverage and task mapping decisions.",
    }
