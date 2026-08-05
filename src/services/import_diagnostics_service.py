"""V12.2.6 import layout diagnostics and acceptance report.

The diagnostics endpoint is for demo trust: after uploading a report, the user can
see how the system moved data through:

    Sheet -> Block -> Fact -> Gap -> Staging

It must make layout recognition visible.  It must also make failure honest: an
unrouted / low-confidence block is a staging issue, not a successful product fact.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from src.repositories.sqlite_repository import connect
from src.services.data_gap_event_service import data_gap_summary, ensure_data_gap_tables
from src.services.metric_fact_store_service import FACT_TABLES, ensure_metric_fact_tables, metric_fact_summary

IMPORT_DIAGNOSTICS_VERSION = "12.2.6"


def _table_exists(conn: Any, table: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _table_columns(conn: Any, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _latest_data_version(conn: Any) -> str | None:
    candidates: List[str] = []
    for table in ["product_metric_facts", "store_metric_facts", "traffic_source_facts", "data_gap_events"]:
        if not _table_exists(conn, table):
            continue
        row = conn.execute(f"SELECT data_version FROM {table} WHERE data_version IS NOT NULL AND data_version != '' ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row and row["data_version"]:
            candidates.append(row["data_version"])
    return candidates[0] if candidates else None


def _fact_rows(conn: Any, data_version: str | None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for table in FACT_TABLES:
        if not _table_exists(conn, table):
            continue
        columns = _table_columns(conn, table)
        source_block = "source_block_id" if "source_block_id" in columns else "NULL"
        source_block_type = "source_block_type" if "source_block_type" in columns else "NULL"
        metric_scope = "metric_scope" if "metric_scope" in columns else "NULL"
        source_row = "source_row_index" if "source_row_index" in columns else "NULL"
        source_col = "source_column_index" if "source_column_index" in columns else "NULL"
        if data_version:
            query = f"""
                SELECT source_sheet,
                       {source_block} AS source_block_id,
                       {source_block_type} AS source_block_type,
                       {metric_scope} AS metric_scope,
                       MIN({source_row}) AS row_start,
                       MAX({source_row}) AS row_end,
                       MIN({source_col}) AS column_start,
                       MAX({source_col}) AS column_end,
                       ? AS target_table,
                       metric_code,
                       raw_field_name,
                       COUNT(*) AS count
                FROM {table}
                WHERE data_version = ?
                GROUP BY source_sheet, source_block_id, source_block_type, metric_scope, metric_code, raw_field_name
            """
            params = (table, data_version)
        else:
            query = f"""
                SELECT source_sheet,
                       {source_block} AS source_block_id,
                       {source_block_type} AS source_block_type,
                       {metric_scope} AS metric_scope,
                       MIN({source_row}) AS row_start,
                       MAX({source_row}) AS row_end,
                       MIN({source_col}) AS column_start,
                       MAX({source_col}) AS column_end,
                       ? AS target_table,
                       metric_code,
                       raw_field_name,
                       COUNT(*) AS count
                FROM {table}
                GROUP BY source_sheet, source_block_id, source_block_type, metric_scope, metric_code, raw_field_name
            """
            params = (table,)
        rows.extend(dict(row) for row in conn.execute(query, params).fetchall())
    return rows


def _gap_rows(conn: Any, data_version: str | None) -> List[Dict[str, Any]]:
    ensure_data_gap_tables()
    columns = _table_columns(conn, "data_gap_events")
    block_expr = "source_block_id" if "source_block_id" in columns else "NULL"
    scope_expr = "metric_scope" if "metric_scope" in columns else "NULL"
    if data_version:
        rows = conn.execute(
            f"""
            SELECT source_sheet,
                   {block_expr} AS source_block_id,
                   {scope_expr} AS metric_scope,
                   target_table,
                   gap_type,
                   metric_code,
                   identity_field,
                   severity,
                   is_decision_blocking,
                   COUNT(*) AS count
            FROM data_gap_events
            WHERE data_version = ?
            GROUP BY source_sheet, source_block_id, metric_scope, target_table, gap_type, metric_code, identity_field, severity, is_decision_blocking
            ORDER BY count DESC
            """,
            (data_version,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT source_sheet,
                   {block_expr} AS source_block_id,
                   {scope_expr} AS metric_scope,
                   target_table,
                   gap_type,
                   metric_code,
                   identity_field,
                   severity,
                   is_decision_blocking,
                   COUNT(*) AS count
            FROM data_gap_events
            GROUP BY source_sheet, source_block_id, metric_scope, target_table, gap_type, metric_code, identity_field, severity, is_decision_blocking
            ORDER BY count DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _profile_sheets(report_profile: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(report_profile, dict):
        return []
    return [sheet for sheet in (report_profile.get("sheetProfiles") or []) if isinstance(sheet, dict)]


def _profile_blocks(report_profile: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for sheet in _profile_sheets(report_profile):
        for block in sheet.get("blocks") or []:
            if isinstance(block, dict):
                item = dict(block)
                item.setdefault("sheetName", sheet.get("sheetName"))
                blocks.append(item)
    return blocks


def _block_key(sheet: str | None, block_id: str | None, target_table: str | None, metric_scope: str | None) -> Tuple[str, str, str, str]:
    sheet_name = str(sheet or "未知Sheet")
    target = str(target_table or "unknown")
    scope = str(metric_scope or "unknown")
    block = str(block_id or f"{sheet_name}::{target}::{scope}::inferred")
    return sheet_name, block, target, scope


def _default_block(sheet: str, block_id: str, target_table: str, metric_scope: str) -> Dict[str, Any]:
    return {
        "sheetName": sheet,
        "blockId": block_id,
        "blockType": "inferred_from_facts" if target_table != "staging_rows" else "staging_unknown",
        "targetTable": target_table,
        "metricScope": metric_scope,
        "range": "inferred",
        "rowStart": None,
        "rowEnd": None,
        "columnStart": None,
        "columnEnd": None,
        "factCount": 0,
        "gapCount": 0,
        "blockingGapCount": 0,
        "staging": target_table not in FACT_TABLES,
        "recognizedMetrics": {},
        "rawFields": {},
        "issues": [],
    }


def _merge_profile_blocks(block_map: Dict[Tuple[str, str, str, str], Dict[str, Any]], blocks: Iterable[Dict[str, Any]]) -> None:
    for block in blocks:
        key = _block_key(block.get("sheetName"), block.get("blockId"), block.get("targetTable"), block.get("metricScope"))
        item = block_map.setdefault(key, _default_block(*key))
        item.update({
            "sheetName": key[0],
            "blockId": key[1],
            "blockType": block.get("blockType") or item.get("blockType"),
            "blockKind": block.get("blockKind"),
            "targetTable": key[2],
            "metricScope": key[3],
            "range": block.get("range") or item.get("range"),
            "rowStart": block.get("rowStart") or item.get("rowStart"),
            "rowEnd": block.get("rowEnd") or item.get("rowEnd"),
            "confidence": block.get("confidence"),
            "profileIssueCount": len(block.get("issues") or []),
            "staging": key[2] not in FACT_TABLES,
        })
        if block.get("issues"):
            item["issues"].extend(block.get("issues") or [])


def _build_layout(report_profile: Dict[str, Any] | None, facts: List[Dict[str, Any]], gaps: List[Dict[str, Any]]) -> Dict[str, Any]:
    block_map: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    _merge_profile_blocks(block_map, _profile_blocks(report_profile))

    for fact in facts:
        key = _block_key(fact.get("source_sheet"), fact.get("source_block_id"), fact.get("target_table"), fact.get("metric_scope"))
        item = block_map.setdefault(key, _default_block(*key))
        count = int(fact.get("count") or 0)
        item["factCount"] += count
        metric = str(fact.get("metric_code") or "unknown")
        item["recognizedMetrics"][metric] = item["recognizedMetrics"].get(metric, 0) + count
        raw_field = str(fact.get("raw_field_name") or metric)
        item["rawFields"][raw_field] = item["rawFields"].get(raw_field, 0) + count
        if fact.get("row_start") is not None:
            row_start = int(fact.get("row_start") or 0)
            row_end = int(fact.get("row_end") or row_start)
            item["rowStart"] = row_start if item.get("rowStart") in {None, 0} else min(int(item["rowStart"]), row_start)
            item["rowEnd"] = max(int(item.get("rowEnd") or 0), row_end)
            item["range"] = f"R{item['rowStart']}:R{item['rowEnd']}"
        if fact.get("column_start") is not None:
            col_start = int(fact.get("column_start") or 0)
            col_end = int(fact.get("column_end") or col_start)
            item["columnStart"] = col_start if item.get("columnStart") in {None, 0} else min(int(item["columnStart"]), col_start)
            item["columnEnd"] = max(int(item.get("columnEnd") or 0), col_end)

    for gap in gaps:
        target = str(gap.get("target_table") or "staging_rows")
        scope = str(gap.get("metric_scope") or "unknown")
        key = _block_key(gap.get("source_sheet"), gap.get("source_block_id"), target, scope)
        item = block_map.setdefault(key, _default_block(*key))
        count = int(gap.get("count") or 0)
        item["gapCount"] += count
        if int(gap.get("is_decision_blocking") or 0):
            item["blockingGapCount"] += count
        issue = {
            "gapType": gap.get("gap_type"),
            "metricCode": gap.get("metric_code"),
            "identityField": gap.get("identity_field"),
            "severity": gap.get("severity"),
            "decisionBlocking": bool(gap.get("is_decision_blocking")),
            "count": count,
        }
        item["issues"].append(issue)
        if target not in FACT_TABLES or gap.get("gap_type") in {"unrouted_sheet", "unrouted_block", "profile_issue"}:
            item["staging"] = True

    sheets: Dict[str, Dict[str, Any]] = {}
    for item in block_map.values():
        item["recognizedMetrics"] = [{"metricCode": key, "factCount": value} for key, value in sorted(item["recognizedMetrics"].items())]
        item["rawFields"] = [{"rawFieldName": key, "factCount": value} for key, value in sorted(item["rawFields"].items())]
        item["acceptanceStatus"] = "blocked" if item["blockingGapCount"] else "staging" if item["staging"] else "passed_with_logged_gaps" if item["gapCount"] else "passed" if item["factCount"] else "empty"
        sheet = sheets.setdefault(item["sheetName"], {"sheetName": item["sheetName"], "blockCount": 0, "factCount": 0, "gapCount": 0, "blockingGapCount": 0, "stagingBlockCount": 0, "targetTables": {}, "blocks": []})
        sheet["blockCount"] += 1
        sheet["factCount"] += int(item["factCount"])
        sheet["gapCount"] += int(item["gapCount"])
        sheet["blockingGapCount"] += int(item["blockingGapCount"])
        if item["staging"]:
            sheet["stagingBlockCount"] += 1
        sheet["targetTables"][item["targetTable"]] = sheet["targetTables"].get(item["targetTable"], 0) + int(item["factCount"])
        sheet["blocks"].append(item)

    sheet_list: List[Dict[str, Any]] = []
    for sheet in sheets.values():
        sheet["targetTables"] = [{"targetTable": key, "factCount": value} for key, value in sorted(sheet["targetTables"].items())]
        sheet["acceptanceStatus"] = "blocked" if sheet["blockingGapCount"] else "staging" if sheet["stagingBlockCount"] else "passed_with_logged_gaps" if sheet["gapCount"] else "passed" if sheet["factCount"] else "empty"
        sheet["blocks"].sort(key=lambda item: (item.get("rowStart") or 999999, item.get("targetTable") or ""))
        sheet_list.append(sheet)
    sheet_list.sort(key=lambda item: (item["acceptanceStatus"], item["sheetName"]))

    return {
        "sheetCount": len(sheet_list),
        "blockCount": sum(item["blockCount"] for item in sheet_list),
        "stagingBlockCount": sum(item["stagingBlockCount"] for item in sheet_list),
        "factCount": sum(item["factCount"] for item in sheet_list),
        "gapCount": sum(item["gapCount"] for item in sheet_list),
        "blockingGapCount": sum(item["blockingGapCount"] for item in sheet_list),
        "sheets": sheet_list,
    }


def _sync_stage(metric_fact_sync: Dict[str, Any] | None, data_gap_sync: Dict[str, Any] | None, risk_task_sync: Dict[str, Any] | None, layout: Dict[str, Any]) -> List[Dict[str, Any]]:
    metric_fact_sync = metric_fact_sync or {}
    data_gap_sync = data_gap_sync or {}
    risk_task_sync = risk_task_sync or {}
    return [
        {"stage": "Sheet", "status": "ready", "count": layout.get("sheetCount", 0), "rule": "报表画像必须可见到 Sheet。"},
        {"stage": "Block", "status": "ready" if layout.get("blockCount", 0) else "empty", "count": layout.get("blockCount", 0), "stagingCount": layout.get("stagingBlockCount", 0), "rule": "一个 Sheet 可以拆成多个经营口径 Block。"},
        {"stage": "Fact", "status": "written" if layout.get("factCount", 0) else "empty", "count": layout.get("factCount", 0) or metric_fact_sync.get("factCount", 0), "mode": metric_fact_sync.get("mode"), "rule": "正式经营指标必须进入事实表。"},
        {"stage": "Gap", "status": "logged" if layout.get("gapCount", 0) else "none", "count": layout.get("gapCount", 0) or data_gap_sync.get("gapCount", 0), "rule": "普通缺口只留痕，不直接生成任务。"},
        {"stage": "Staging", "status": "needs_attention" if layout.get("stagingBlockCount", 0) else "clear", "count": layout.get("stagingBlockCount", 0), "rule": "低置信或无法路由区块进入暂存，不伪装成成功。"},
        {"stage": "EvidenceGate", "status": "scoped", "count": risk_task_sync.get("evidenceBlockedTaskCount", 0), "rule": "任务证据按 metric_scope 取证，禁止跨口径 ROI。"},
    ]


def import_diagnostics(data_version: str | None = None, *, report_profile: Dict[str, Any] | None = None, metric_fact_sync: Dict[str, Any] | None = None, data_gap_sync: Dict[str, Any] | None = None, risk_task_sync: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ensure_metric_fact_tables()
    ensure_data_gap_tables()
    with connect() as conn:
        version = data_version or _latest_data_version(conn)
        facts = _fact_rows(conn, version)
        gaps = _gap_rows(conn, version)

    layout = _build_layout(report_profile, facts, gaps)
    fact_summary = metric_fact_summary()
    gap_summary = data_gap_summary()
    has_blocking = layout.get("blockingGapCount", 0) > 0 or gap_summary.get("decisionBlockingGapCount", 0) > 0
    has_staging = layout.get("stagingBlockCount", 0) > 0
    status = "blocked" if has_blocking else "needs_layout_review" if has_staging else "passed_with_logged_gaps" if layout.get("gapCount", 0) else "passed" if layout.get("factCount", 0) else "empty"
    return {
        "version": IMPORT_DIAGNOSTICS_VERSION,
        "layoutMode": "sheet_block_fact_gap_staging",
        "dataVersion": version,
        "sheetCount": layout.get("sheetCount", 0),
        "blockCount": layout.get("blockCount", 0),
        "stagingBlockCount": layout.get("stagingBlockCount", 0),
        "factSummary": fact_summary,
        "gapSummary": gap_summary,
        "stageTrace": _sync_stage(metric_fact_sync, data_gap_sync, risk_task_sync, layout),
        "sheets": layout.get("sheets", []),
        "acceptance": {
            "factWritten": layout.get("factCount", 0) > 0 or fact_summary.get("factCount", 0) > 0,
            "hasDecisionBlockingGap": has_blocking,
            "hasStagingBlocks": has_staging,
            "status": status,
            "rule": "V12.2.6：导入验收看 Sheet→Block→Fact→Gap→Staging；普通缺口不代表任务失败，暂存区块不能伪装成功。",
        },
        "rule": "V12.2.6：返回布局诊断，展示每个 Sheet 下的 Block、事实写入、缺口和暂存状态。",
    }
