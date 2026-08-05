"""V12.1.3 data gap event store.

This service records data gaps without creating tasks.  The point is to keep the
system honest while avoiding task explosion: a missing ROI column in one report is
only a logged gap until a later evidence gate proves it blocks a real operating
judgment.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Tuple

from src.repositories.sqlite_repository import connect, dumps
from src.services.metric_catalog_service import METRIC_ALIASES, canonical_field, pick, product_identity, system_codes
from src.services.report_alert_service import now_iso

DATA_GAP_EVENT_VERSION = "12.1.3"

FACT_TARGETS = {"product_metric_facts", "store_metric_facts", "traffic_source_facts"}

# These are not required for every report.  They are the metrics that often become
# evidence in later operating judgments.  V12.1.3 records their absence as ordinary
# gaps only; V12.1.4 evidence gate decides whether a gap is decision-blocking.
TARGET_METRIC_WATCHLIST = {
    "product_metric_facts": [
        "inventory_qty",
        "avg_order_value",
        "payment_amount",
        "product_cost_amount",
        "gross_profit_amount",
        "gross_margin_rate",
        "roi",
        "click_rate",
        "payment_conversion_rate",
        "refund_rate",
        "ad_spend",
    ],
    "store_metric_facts": ["payment_amount", "gross_profit_amount", "gross_margin_rate", "roi", "refund_rate", "ad_spend"],
    "traffic_source_facts": ["visitor_count", "click_rate", "payment_conversion_rate", "roi", "ad_spend", "organic_visitor_count", "paid_visitor_count"],
}

IDENTITY_WATCHLIST = {
    "product_metric_facts": ["store_id", "store_name", "product_id", "sku_id", "erp_product_code", "product_link", "stat_date"],
    "store_metric_facts": ["store_id", "store_name", "stat_date"],
    "traffic_source_facts": ["store_id", "store_name", "product_id", "sku_id", "erp_product_code", "product_link", "traffic_source", "stat_date"],
}


def ensure_data_gap_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS data_gap_events (
                gap_id TEXT PRIMARY KEY,
                tenant_id TEXT,
                org_id TEXT,
                data_version TEXT,
                dataset_name TEXT,
                source_system TEXT,
                source_report_id TEXT,
                source_sheet TEXT,
                target_table TEXT,
                entity_level TEXT,
                store_code TEXT,
                spu_code TEXT,
                link_code TEXT,
                sku_code TEXT,
                metric_code TEXT,
                identity_field TEXT,
                gap_type TEXT NOT NULL,
                gap_scope TEXT NOT NULL,
                affected_row_count INTEGER DEFAULT 0,
                sample_count INTEGER DEFAULT 0,
                missing_count INTEGER DEFAULT 0,
                present_count INTEGER DEFAULT 0,
                is_decision_blocking INTEGER DEFAULT 0,
                related_signal_id TEXT,
                related_task_id TEXT,
                status TEXT NOT NULL DEFAULT 'logged',
                severity TEXT NOT NULL DEFAULT 'info',
                reason TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data_gap_events_version ON data_gap_events(data_version, dataset_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data_gap_events_target ON data_gap_events(target_table, source_sheet)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data_gap_events_decision ON data_gap_events(is_decision_blocking, status)")
        conn.commit()


def _first_import_item(result: Dict[str, Any]) -> Dict[str, Any]:
    items = result.get("results")
    if isinstance(items, list) and items:
        first = next((item for item in items if isinstance(item, dict)), None)
        if first:
            return first
    return result


def _fallback_dataset_version(result: Dict[str, Any]) -> Tuple[str | None, str | None]:
    item = _first_import_item(result)
    dataset = item.get("datasetName") or result.get("datasetName")
    version = item.get("dataVersion") or result.get("dataVersion")
    return (str(dataset) if dataset else None, str(version) if version else None)


def _profile_sheets(report_profile: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(report_profile, dict):
        return []
    return [sheet for sheet in (report_profile.get("sheetProfiles") or []) if isinstance(sheet, dict)]


def _recognized_fields(sheet_profile: Dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for item in sheet_profile.get("recognizedFields") or []:
        if isinstance(item, dict) and item.get("canonicalField"):
            fields.add(str(item["canonicalField"]))
    for item in sheet_profile.get("metricFields") or []:
        if item:
            fields.add(str(item))
    for item in sheet_profile.get("identityFields") or []:
        if item:
            fields.add(str(item))
    return fields


def _canonical_headers(rows: Iterable[Dict[str, Any]]) -> set[str]:
    headers: set[str] = set()
    for row in list(rows)[:20]:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            canonical = canonical_field(key)
            if canonical:
                headers.add(canonical)
    return headers


def _value_present(row: Dict[str, Any], canonical: str) -> bool:
    return pick(row, canonical) not in {None, ""}


def _row_entity(row: Dict[str, Any], target_table: str) -> Dict[str, Any]:
    ident = product_identity(row)
    codes = system_codes(row)
    if target_table == "store_metric_facts":
        entity_level = "store"
    elif target_table == "traffic_source_facts":
        entity_level = "traffic_source"
    elif ident.get("skuId"):
        entity_level = "sku"
    elif ident.get("productId") or ident.get("productLink"):
        entity_level = "link"
    else:
        entity_level = "spu"
    return {
        "entityLevel": entity_level,
        "storeCode": codes.get("systemStoreCode"),
        "spuCode": None if target_table == "store_metric_facts" else codes.get("systemSpuCode"),
        "linkCode": None if target_table == "store_metric_facts" else codes.get("systemLinkCode"),
        "skuCode": None if target_table == "store_metric_facts" else codes.get("systemSkuCode"),
        "identity": ident,
        "systemCodes": codes,
    }


def _gap_id(*parts: Any) -> str:
    source = "::".join(str(part or "") for part in parts)
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


def _insert_gap(conn: Any, *, result: Dict[str, Any], source_system: str | None, source_report_id: str | None, source_sheet: str, target_table: str, gap_type: str, gap_scope: str, metric_code: str | None = None, identity_field: str | None = None, affected_row_count: int = 0, sample_count: int = 0, missing_count: int = 0, present_count: int = 0, severity: str = "info", reason: str = "", entity: Dict[str, Any] | None = None, payload: Dict[str, Any] | None = None) -> None:
    dataset_name, data_version = _fallback_dataset_version(result)
    entity = entity or {}
    now = now_iso()
    gap_id = _gap_id(data_version, dataset_name, source_sheet, target_table, gap_type, gap_scope, metric_code, identity_field, entity.get("storeCode"), entity.get("spuCode"), entity.get("linkCode"), entity.get("skuCode"))
    payload = payload or {}
    payload.update({
        "dataGapEventVersion": DATA_GAP_EVENT_VERSION,
        "decisionGateStatus": "not_evaluated",
        "taskCreation": "disabled_in_data_gap_layer",
        "rule": "普通缺口只留痕；是否阻塞经营判断由后续证据闸门决定。",
    })
    conn.execute(
        """
        INSERT OR REPLACE INTO data_gap_events (
            gap_id, tenant_id, org_id, data_version, dataset_name, source_system, source_report_id, source_sheet,
            target_table, entity_level, store_code, spu_code, link_code, sku_code, metric_code, identity_field,
            gap_type, gap_scope, affected_row_count, sample_count, missing_count, present_count,
            is_decision_blocking, related_signal_id, related_task_id, status, severity, reason, payload, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, 'logged', ?, ?, ?, COALESCE((SELECT created_at FROM data_gap_events WHERE gap_id = ?), ?), ?)
        """,
        (
            gap_id,
            "default-tenant",
            "default-org",
            data_version,
            dataset_name,
            source_system,
            source_report_id,
            source_sheet,
            target_table,
            entity.get("entityLevel"),
            entity.get("storeCode"),
            entity.get("spuCode"),
            entity.get("linkCode"),
            entity.get("skuCode"),
            metric_code,
            identity_field,
            gap_type,
            gap_scope,
            affected_row_count,
            sample_count,
            missing_count,
            present_count,
            severity,
            reason,
            dumps(payload),
            gap_id,
            now,
            now,
        ),
    )


def _sheet_rows(parsed: Dict[str, Any], sheet_name: str) -> List[Dict[str, Any]]:
    sheet_rows = parsed.get("sheetRows") if isinstance(parsed, dict) else None
    if isinstance(sheet_rows, dict) and isinstance(sheet_rows.get(sheet_name), list):
        return [row for row in sheet_rows[sheet_name] if isinstance(row, dict)]
    rows = parsed.get("rows") if isinstance(parsed, dict) else []
    return [row for row in rows if isinstance(row, dict) and row.get("__source_sheet") == sheet_name]


def _record_sheet_level_gaps(conn: Any, *, result: Dict[str, Any], parsed: Dict[str, Any], sheet_profile: Dict[str, Any], source_system: str | None, source_report_id: str | None) -> Dict[str, Any]:
    sheet_name = str(sheet_profile.get("sheetName") or "Sheet")
    target_table = str(sheet_profile.get("targetTable") or "staging_rows")
    rows = _sheet_rows(parsed, sheet_name)
    row_count = len(rows)
    if target_table not in FACT_TARGETS:
        _insert_gap(
            conn,
            result=result,
            source_system=source_system,
            source_report_id=source_report_id,
            source_sheet=sheet_name,
            target_table=target_table,
            gap_type="unrouted_sheet",
            gap_scope="sheet",
            affected_row_count=row_count,
            sample_count=min(row_count, 20),
            severity="warning",
            reason="Sheet 未进入正式事实表，仅进入暂存/待确认。",
            payload={"sheetProfile": sheet_profile},
        )
        return {"sheetName": sheet_name, "targetTable": target_table, "gapCount": 1, "ordinaryGapCount": 1, "decisionBlockingGapCount": 0, "rowCount": row_count}

    recognized = _recognized_fields(sheet_profile) | _canonical_headers(rows)
    watch_metrics = TARGET_METRIC_WATCHLIST.get(target_table, [])
    watch_identity = IDENTITY_WATCHLIST.get(target_table, [])
    gap_count = 0

    for metric_code in watch_metrics:
        if metric_code not in recognized:
            _insert_gap(
                conn,
                result=result,
                source_system=source_system,
                source_report_id=source_report_id,
                source_sheet=sheet_name,
                target_table=target_table,
                gap_type="metric_not_in_sheet",
                gap_scope="sheet",
                metric_code=metric_code,
                affected_row_count=row_count,
                sample_count=min(row_count, 20),
                severity="info",
                reason=f"{metric_code} 未在该 Sheet 中识别；当前只留痕，不生成补数任务。",
                payload={"sheetKind": sheet_profile.get("sheetKind"), "confidence": sheet_profile.get("confidence")},
            )
            gap_count += 1
            continue
        if row_count:
            present_count = sum(1 for row in rows if _value_present(row, metric_code))
            missing_count = row_count - present_count
            if missing_count > 0:
                _insert_gap(
                    conn,
                    result=result,
                    source_system=source_system,
                    source_report_id=source_report_id,
                    source_sheet=sheet_name,
                    target_table=target_table,
                    gap_type="metric_sparse_values",
                    gap_scope="sheet_metric_aggregate",
                    metric_code=metric_code,
                    affected_row_count=row_count,
                    sample_count=min(row_count, 20),
                    missing_count=missing_count,
                    present_count=present_count,
                    severity="info" if present_count else "warning",
                    reason=f"{metric_code} 存在空值，按 Sheet 聚合留痕，不按商品逐条生成任务。",
                    payload={"sheetKind": sheet_profile.get("sheetKind"), "confidence": sheet_profile.get("confidence")},
                )
                gap_count += 1

    for identity_field in watch_identity:
        if identity_field not in recognized:
            _insert_gap(
                conn,
                result=result,
                source_system=source_system,
                source_report_id=source_report_id,
                source_sheet=sheet_name,
                target_table=target_table,
                gap_type="identity_not_in_sheet",
                gap_scope="sheet",
                identity_field=identity_field,
                affected_row_count=row_count,
                sample_count=min(row_count, 20),
                severity="warning" if identity_field in {"store_id", "store_name", "product_id", "product_link"} else "info",
                reason=f"{identity_field} 未在该 Sheet 中识别；影响定位置信度，但不直接生成任务。",
                payload={"sheetKind": sheet_profile.get("sheetKind"), "confidence": sheet_profile.get("confidence")},
            )
            gap_count += 1

    for issue in sheet_profile.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        severity = "warning" if issue.get("severity") == "blocked" else "info"
        _insert_gap(
            conn,
            result=result,
            source_system=source_system,
            source_report_id=source_report_id,
            source_sheet=sheet_name,
            target_table=target_table,
            gap_type="profile_issue",
            gap_scope="sheet",
            affected_row_count=row_count,
            sample_count=min(row_count, 20),
            severity=severity,
            reason=str(issue.get("message") or "报表画像发现待确认问题。"),
            payload={"issue": issue, "sheetKind": sheet_profile.get("sheetKind"), "confidence": sheet_profile.get("confidence")},
        )
        gap_count += 1

    return {"sheetName": sheet_name, "targetTable": target_table, "gapCount": gap_count, "ordinaryGapCount": gap_count, "decisionBlockingGapCount": 0, "rowCount": row_count}


def ingest_data_gaps_from_import(
    result: Dict[str, Any],
    parsed: Dict[str, Any] | None = None,
    *,
    report_profile: Dict[str, Any] | None = None,
    source_system: str | None = None,
    source_report_id: str | None = None,
) -> Dict[str, Any]:
    """Record report/data gaps as aggregated events.

    This function never creates tasks. It intentionally aggregates missing fields
    by sheet and metric to prevent product-level gap spam.
    """
    ensure_data_gap_tables()
    profile = report_profile if isinstance(report_profile, dict) else {}
    parsed = parsed if isinstance(parsed, dict) else {"rows": []}
    sheets = _profile_sheets(profile)
    if not sheets:
        return {"version": DATA_GAP_EVENT_VERSION, "skipped": True, "reason": "reportProfile is missing", "rule": "没有报表画像时不猜缺口。"}

    sheet_summaries: List[Dict[str, Any]] = []
    with connect() as conn:
        for sheet in sheets:
            sheet_summaries.append(_record_sheet_level_gaps(conn, result=result, parsed=parsed, sheet_profile=sheet, source_system=source_system, source_report_id=source_report_id))
        conn.commit()

    gap_count = sum(item.get("gapCount", 0) for item in sheet_summaries)
    return {
        "version": DATA_GAP_EVENT_VERSION,
        "mode": "aggregated_data_gap_events_no_task_creation",
        "gapCount": gap_count,
        "ordinaryGapCount": gap_count,
        "decisionBlockingGapCount": 0,
        "sheetSummaries": sheet_summaries,
        "rule": "V12.1.3：缺口按 Sheet/指标聚合留痕；缺字段不直接生成任务。",
    }


def data_gap_summary() -> Dict[str, Any]:
    ensure_data_gap_tables()
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM data_gap_events").fetchone()["count"]
        ordinary = conn.execute("SELECT COUNT(*) AS count FROM data_gap_events WHERE is_decision_blocking = 0").fetchone()["count"]
        blocking = conn.execute("SELECT COUNT(*) AS count FROM data_gap_events WHERE is_decision_blocking = 1").fetchone()["count"]
        open_logged = conn.execute("SELECT COUNT(*) AS count FROM data_gap_events WHERE status = 'logged'").fetchone()["count"]
        by_type = [dict(row) for row in conn.execute("SELECT gap_type AS gapType, COUNT(*) AS count FROM data_gap_events GROUP BY gap_type ORDER BY count DESC").fetchall()]
        by_sheet = [dict(row) for row in conn.execute("SELECT source_sheet AS sheetName, target_table AS targetTable, COUNT(*) AS count FROM data_gap_events GROUP BY source_sheet, target_table ORDER BY count DESC LIMIT 20").fetchall()]
    return {
        "version": DATA_GAP_EVENT_VERSION,
        "gapCount": total,
        "ordinaryGapCount": ordinary,
        "decisionBlockingGapCount": blocking,
        "openLoggedGapCount": open_logged,
        "byType": by_type,
        "bySheet": by_sheet,
        "rule": "普通缺口只留痕；决策缺口由后续任务证据闸门写入。",
    }
