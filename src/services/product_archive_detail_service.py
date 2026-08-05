"""V12.2.4 product archive detail projection.

商品模块只负责商品资产、定位、指标事实和任务历史摘要。经营指标必须来自
独立事实表；事实表没有就是未识别，不能用商品对象 payload 缓存托底。

V12.2.5 ROI scope rule:
    product ROI comes only from product_metric_facts;
    traffic ROI comes only from traffic_source_facts;
    store ROI comes only from store_metric_facts.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads
from src.services.metric_fact_store_service import ensure_metric_fact_tables

PRODUCT_ARCHIVE_DETAIL_VERSION = "12.2.5"
UNKNOWN_VALUE = "未识别"

METRIC_LABELS = {
    "inventory_qty": "库存数量",
    "sellable_days": "可售天数",
    "avg_order_value": "客单价",
    "sale_price": "售价",
    "payment_amount": "支付金额",
    "product_cost_amount": "商品成本金额",
    "gross_profit_amount": "毛利金额",
    "gross_margin_rate": "毛利率",
    "visitor_count": "访客数",
    "page_view_count": "浏览量",
    "click_user_count": "点击人数",
    "click_count": "点击数",
    "click_rate": "点击率",
    "payment_buyer_count": "支付买家数",
    "payment_order_count": "支付订单数",
    "payment_unit_count": "支付件数",
    "payment_conversion_rate": "支付转化率",
    "refund_order_count": "退款订单数",
    "refund_amount": "退款金额",
    "refund_rate": "退款率",
    "ad_spend": "广告消耗",
    "ad_click_count": "广告点击数",
    "ad_order_count": "广告成交数",
    "roi": "ROI",
    "organic_visitor_count": "自然流量访客数",
    "paid_visitor_count": "付费流量访客数",
}

SECTION_RULES = [
    ("transaction", "成交与投产", ["payment_amount", "avg_order_value", "payment_order_count", "payment_unit_count", "payment_conversion_rate", "roi"]),
    ("profit", "成本与利润", ["product_cost_amount", "gross_profit_amount", "gross_margin_rate"]),
    ("traffic", "流量与广告", ["visitor_count", "page_view_count", "click_user_count", "click_rate", "ad_spend", "ad_click_count", "ad_order_count", "organic_visitor_count", "paid_visitor_count"]),
    ("stock_after_sales", "库存与售后", ["inventory_qty", "sellable_days", "refund_order_count", "refund_amount", "refund_rate"]),
]

TABLES = ("product_metric_facts", "traffic_source_facts", "store_metric_facts")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _item_value(item: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(item.get(key))
        if value and value != "—":
            return value
    return ""


def _where_for_table(table: str, item: Dict[str, Any]) -> tuple[str, List[Any]]:
    store_values = [
        _item_value(item, "systemStoreCode"),
        _item_value(item, "storeId", "normalizedStoreId"),
        _item_value(item, "storeName", "store", "normalizedStoreName"),
    ]
    product_values = [
        _item_value(item, "systemSkuCode"),
        _item_value(item, "systemLinkCode"),
        _item_value(item, "systemSpuCode"),
        _item_value(item, "productId", "rawProductId", "id"),
        _item_value(item, "skuId"),
        _item_value(item, "erpProductCode"),
        _item_value(item, "productLink", "link"),
    ]
    store_clauses: List[str] = []
    store_params: List[Any] = []
    for column, value in [("store_code", store_values[0]), ("store_id", store_values[1]), ("store_name", store_values[2])]:
        if value:
            store_clauses.append(f"{column} = ?")
            store_params.append(value)
    if table == "store_metric_facts":
        if not store_clauses:
            return "1 = 0", []
        return " OR ".join(store_clauses), store_params

    product_clauses: List[str] = []
    product_params: List[Any] = []
    for column, value in [
        ("sku_code", product_values[0]),
        ("link_code", product_values[1]),
        ("spu_code", product_values[2]),
        ("product_id", product_values[3]),
        ("sku_id", product_values[4]),
        ("erp_product_code", product_values[5]),
        ("product_link", product_values[6]),
    ]:
        if value:
            product_clauses.append(f"{column} = ?")
            product_params.append(value)
    if not product_clauses:
        return "1 = 0", []
    if store_clauses:
        return f"({' OR '.join(product_clauses)}) AND ({' OR '.join(store_clauses)})", [*product_params, *store_params]
    return " OR ".join(product_clauses), product_params


def _safe_row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        return None


def _fetch_facts(item: Dict[str, Any], limit: int = 800) -> List[Dict[str, Any]]:
    ensure_metric_fact_tables()
    facts: List[Dict[str, Any]] = []
    with connect() as conn:
        for table in TABLES:
            where, params = _where_for_table(table, item)
            rows = conn.execute(
                f"""
                SELECT *, ? AS fact_table
                FROM {table}
                WHERE {where}
                ORDER BY COALESCE(stat_date, updated_at) DESC, updated_at DESC
                LIMIT ?
                """,
                [table, *params, limit],
            ).fetchall()
            for row in rows:
                payload = loads(row["payload"])
                facts.append({
                    "factId": row["fact_id"],
                    "factTable": row["fact_table"],
                    "entityLevel": row["entity_level"],
                    "metricCode": row["metric_code"],
                    "metricName": METRIC_LABELS.get(row["metric_code"], row["metric_code"]),
                    "metricValue": row["metric_value"],
                    "displayValue": row["display_value"] or str(row["metric_value"] if row["metric_value"] is not None else UNKNOWN_VALUE),
                    "rawFieldName": row["raw_field_name"],
                    "rawValue": row["raw_value"],
                    "sourceSheet": row["source_sheet"],
                    "sourceBlockId": _safe_row_value(row, "source_block_id"),
                    "sourceRowIndex": _safe_row_value(row, "source_row_index"),
                    "metricScope": _safe_row_value(row, "metric_scope"),
                    "sourceSystem": row["source_system"],
                    "sourceReportId": row["source_report_id"],
                    "dataVersion": row["data_version"],
                    "datasetName": row["dataset_name"],
                    "statDate": row["stat_date"],
                    "trafficSource": row["traffic_source"],
                    "updatedAt": row["updated_at"],
                    "payload": payload,
                })
    return facts


def _latest_by_metric(facts: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for fact in facts:
        code = fact.get("metricCode")
        if not code:
            continue
        if code not in latest:
            latest[code] = fact
    return latest


def _build_position(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "systemStoreCode": item.get("systemStoreCode"),
        "systemSpuCode": item.get("systemSpuCode"),
        "systemLinkCode": item.get("systemLinkCode"),
        "systemSkuCode": item.get("systemSkuCode"),
        "platform": item.get("platform"),
        "storeId": item.get("storeId") or item.get("normalizedStoreId"),
        "storeName": item.get("storeName") or item.get("store") or item.get("normalizedStoreName"),
        "productId": item.get("productId") or item.get("rawProductId"),
        "skuId": item.get("skuId"),
        "erpProductCode": item.get("erpProductCode"),
        "productLink": item.get("productLink") or item.get("link"),
        "archiveId": item.get("archiveId") or item.get("objectId") or item.get("id"),
        "sourceDataVersions": item.get("sourceDataVersions") or [],
        "sourceDatasets": item.get("sourceDatasets") or [],
    }


def _product_facts(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [fact for fact in facts if fact.get("factTable") == "product_metric_facts" and (fact.get("metricScope") in {None, "", "product"})]


def _traffic_facts(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [fact for fact in facts if fact.get("factTable") == "traffic_source_facts" and (fact.get("metricScope") in {None, "", "traffic_source"})]


def _store_facts(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [fact for fact in facts if fact.get("factTable") == "store_metric_facts" and (fact.get("metricScope") in {None, "", "store"})]


def _build_sections(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest = _latest_by_metric(_product_facts(facts))
    sections: List[Dict[str, Any]] = []
    for section_key, title, metrics in SECTION_RULES:
        entries: List[Dict[str, Any]] = []
        for code in metrics:
            fact = latest.get(code)
            if not fact:
                entries.append({
                    "metricCode": code,
                    "metricName": METRIC_LABELS.get(code, code),
                    "displayValue": UNKNOWN_VALUE,
                    "sourceSheet": "事实表未命中",
                    "dataVersion": None,
                    "statDate": None,
                    "factId": None,
                    "missing": True,
                })
                continue
            entries.append({
                "metricCode": code,
                "metricName": METRIC_LABELS.get(code, code),
                "displayValue": fact.get("displayValue") or UNKNOWN_VALUE,
                "sourceSheet": fact.get("sourceSheet"),
                "sourceBlockId": fact.get("sourceBlockId"),
                "sourceRowIndex": fact.get("sourceRowIndex"),
                "metricScope": fact.get("metricScope"),
                "dataVersion": fact.get("dataVersion"),
                "statDate": fact.get("statDate"),
                "factId": fact.get("factId"),
            })
        sections.append({"key": section_key, "title": title, "items": entries})
    return sections


def _traffic_sources(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for fact in _traffic_facts(facts):
        source = fact.get("trafficSource")
        if not source:
            continue
        grouped.setdefault(str(source), []).append(fact)
    result: List[Dict[str, Any]] = []
    for source, source_facts in grouped.items():
        latest = _latest_by_metric(source_facts)
        result.append({
            "trafficSource": source,
            "visitorCount": (latest.get("visitor_count") or {}).get("displayValue") or UNKNOWN_VALUE,
            "clickRate": (latest.get("click_rate") or {}).get("displayValue") or UNKNOWN_VALUE,
            "conversionRate": (latest.get("payment_conversion_rate") or {}).get("displayValue") or UNKNOWN_VALUE,
            "roi": (latest.get("roi") or {}).get("displayValue") or UNKNOWN_VALUE,
            "adSpend": (latest.get("ad_spend") or {}).get("displayValue") or UNKNOWN_VALUE,
            "factCount": len(source_facts),
        })
    return sorted(result, key=lambda item: item.get("trafficSource") or "")


def _task_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    has_active = bool(item.get("hasActiveTask") or item.get("activeTaskId"))
    has_completed = bool(item.get("completedTaskId"))
    return {
        "activeTaskId": item.get("activeTaskId"),
        "activeTaskStatus": item.get("activeTaskStatus"),
        "activeWorkflowStatus": item.get("activeWorkflowStatus"),
        "completedTaskId": item.get("completedTaskId"),
        "completedTaskStatus": item.get("completedTaskStatus"),
        "hasActiveTask": has_active,
        "taskCount": int(has_active) + int(has_completed),
        "summary": "当前有未完成任务" if has_active else "暂无未完成任务",
        "rule": "商品页只显示任务历史摘要，完整SOP在任务详情页查看。",
    }


def _metric_value(facts: List[Dict[str, Any]], code: str) -> str:
    fact = _latest_by_metric(_product_facts(facts)).get(code)
    return str(fact.get("displayValue")) if fact and fact.get("displayValue") not in {None, ""} else UNKNOWN_VALUE


def enrich_product_archive_detail(item: Dict[str, Any]) -> Dict[str, Any]:
    facts = _fetch_facts(item)
    product_facts = _product_facts(facts)
    traffic_facts = _traffic_facts(facts)
    store_facts = _store_facts(facts)
    enriched = dict(item)
    enriched["productArchiveDetailVersion"] = PRODUCT_ARCHIVE_DETAIL_VERSION
    enriched["productPosition"] = _build_position(enriched)
    enriched["metricSections"] = _build_sections(facts)
    enriched["trafficSourceFacts"] = _traffic_sources(facts)
    enriched["taskHistorySummary"] = _task_summary(enriched)
    enriched["inventory"] = _metric_value(facts, "inventory_qty")
    enriched["avgOrderValue"] = _metric_value(facts, "avg_order_value")
    enriched["price"] = enriched["avgOrderValue"]
    enriched["paymentAmount"] = _metric_value(facts, "payment_amount")
    enriched["cost"] = _metric_value(facts, "product_cost_amount")
    enriched["grossProfitAmount"] = _metric_value(facts, "gross_profit_amount")
    enriched["grossMargin"] = _metric_value(facts, "gross_margin_rate")
    enriched["roi"] = _metric_value(facts, "roi")
    enriched["clickRate"] = _metric_value(facts, "click_rate")
    enriched["conversionRate"] = _metric_value(facts, "payment_conversion_rate")
    enriched["refundRate"] = _metric_value(facts, "refund_rate")
    enriched["adSpend"] = _metric_value(facts, "ad_spend")
    enriched["inventoryStatus"] = "已入库" if enriched["inventory"] != UNKNOWN_VALUE else "未识别"
    enriched["inventoryLevel"] = "good" if enriched["inventory"] != UNKNOWN_VALUE else "watch"
    enriched["afterSales"] = "退款率已识别" if enriched["refundRate"] != UNKNOWN_VALUE else "售后未识别"
    enriched["afterSalesLevel"] = "good" if enriched["refundRate"] != UNKNOWN_VALUE else "watch"
    enriched["metricFactSummary"] = {
        "factCount": len(facts),
        "productFactCount": len(product_facts),
        "storeFactCount": len(store_facts),
        "trafficFactCount": len(traffic_facts),
        "failClosed": True,
        "roiScopeIsolation": True,
        "rule": "V12.2.5：商品指标只读 product_metric_facts；流量来源指标只读 traffic_source_facts；店铺指标只读 store_metric_facts；未命中显示未识别，不读对象缓存。",
    }
    return enriched
