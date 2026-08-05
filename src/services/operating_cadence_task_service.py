"""V12.5 baseline-first ROI / GMV operating cadence task service.

V12.5 fixes the V12.4.1 over-generation problem:
- the first imported report is a baseline, not a strategy-test trigger;
- ROI/GMV daily operating tasks require at least two comparable report dates;
- 3/7/14/30/90 day trend tasks require enough report cadence to support them;
- baseline imports may still create red-line tasks, data quality tasks, and report
  seeds, but they must not create high-ROI/low-GMV expansion tests.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services import module_task_service
from src.services.metric_fact_store_service import ensure_metric_fact_tables
from src.services.task_evidence_gate_service import apply_evidence_gate_to_created_task

OPERATING_CADENCE_VERSION = "12.5.0"
CADENCE_WINDOWS = [3, 7, 14, 30, 90]

TRACKED_METRICS = {
    "roi",
    "payment_amount",
    "ad_spend",
    "inventory_qty",
    "sellable_days",
    "payment_conversion_rate",
    "click_rate",
    "visitor_count",
    "page_view_count",
    "click_user_count",
    "organic_visitor_count",
    "paid_visitor_count",
    "gross_margin_rate",
    "refund_rate",
    "refund_amount",
    "payment_order_count",
    "payment_unit_count",
}

METRIC_LABELS = {
    "roi": "ROI",
    "payment_amount": "GMV/支付金额",
    "ad_spend": "广告消耗",
    "inventory_qty": "库存数量",
    "sellable_days": "可售天数",
    "payment_conversion_rate": "支付转化率",
    "click_rate": "点击率",
    "visitor_count": "访客数",
    "page_view_count": "浏览量",
    "click_user_count": "点击人数",
    "organic_visitor_count": "自然流量访客数",
    "paid_visitor_count": "付费流量访客数",
    "gross_margin_rate": "毛利率",
    "refund_rate": "退款率",
    "refund_amount": "退款金额",
    "payment_order_count": "支付订单数",
    "payment_unit_count": "支付件数",
}

ROI_GOOD = 2.0
ROI_WEAK = 1.5
GMV_MOVE = 0.12
ROI_MOVE = 0.06
REDLINE_TYPES = {"redline_inventory_zero", "redline_margin_floor", "redline_refund_floor"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}".upper()


def _num(value: Any) -> float | None:
    if value in {None, "", "未识别", "—"}:
        return None
    try:
        return float(str(value).replace("¥", "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _pct_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or abs(old) < 1e-9:
        return None
    return (new - old) / abs(old)


def _fmt_change(value: float | None) -> str:
    return "无法计算" if value is None else f"{value * 100:+.1f}%"


def _safe_date(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if text else "未知日期"


def _date_value(value: Any) -> datetime | None:
    text = _safe_date(value)
    if text == "未知日期":
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _window_bucket(day_span: int) -> Dict[str, Any]:
    if day_span <= 3:
        return {"windowDays": 3, "windowType": "short_wave", "reportTarget": "日报", "label": "3天短波动"}
    if day_span <= 7:
        return {"windowDays": 7, "windowType": "small_trend", "reportTarget": "日报/周报", "label": "7天小趋势"}
    if day_span <= 14:
        return {"windowDays": 14, "windowType": "mid_short_trend", "reportTarget": "周报", "label": "14天中短趋势"}
    if day_span <= 30:
        return {"windowDays": 30, "windowType": "mid_trend", "reportTarget": "周报/月报", "label": "30天中趋势"}
    return {"windowDays": 90, "windowType": "large_trend", "reportTarget": "月报/季报", "label": "90天大趋势"}


def ensure_operating_cadence_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS operating_cadence_signals (
                signal_id TEXT PRIMARY KEY,
                data_version TEXT,
                product_id TEXT,
                store_id TEXT,
                metric_scope TEXT,
                cadence_window TEXT,
                signal_type TEXT,
                risk_level TEXT,
                queue_type TEXT,
                status TEXT,
                task_id TEXT,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "operating_cadence_signals",
            {
                "upload_frequency_level": "TEXT",
                "report_target": "TEXT",
                "agent_judgment": "TEXT",
                "roi_gmv_quadrant": "TEXT",
                "primary_axis": "TEXT",
                "baseline_mode": "INTEGER DEFAULT 0",
            },
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_operating_cadence_signals_version ON operating_cadence_signals(data_version, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_operating_cadence_signals_product ON operating_cadence_signals(product_id, store_id, created_at)")
        conn.commit()


def _table_exists(conn: Any, table: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _load_facts(limit: int = 8000) -> List[Dict[str, Any]]:
    ensure_metric_fact_tables()
    with connect() as conn:
        if not _table_exists(conn, "product_metric_facts"):
            return []
        rows = conn.execute(
            f"""
            SELECT *
            FROM product_metric_facts
            WHERE metric_code IN ({','.join('?' for _ in TRACKED_METRICS)})
              AND (metric_scope IS NULL OR metric_scope = '' OR metric_scope = 'product')
            ORDER BY COALESCE(stat_date, updated_at) ASC, updated_at ASC
            LIMIT ?
            """,
            [*sorted(TRACKED_METRICS), limit],
        ).fetchall()
    return [dict(row) for row in rows]


def _context_for_product(product_id: str | None, store_id: str | None) -> Dict[str, Any]:
    if not product_id:
        return {}
    with connect() as conn:
        if not _table_exists(conn, "operating_products"):
            return {"productId": product_id, "storeId": store_id, "title": f"商品 {product_id}", "storeName": store_id or "导入店铺"}
        if store_id:
            row = conn.execute(
                """
                SELECT * FROM operating_products
                WHERE product_id = ? AND (normalized_store_id = ? OR store_id = ? OR store_name = ?)
                ORDER BY updated_at DESC LIMIT 1
                """,
                (product_id, store_id, store_id, store_id),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM operating_products WHERE product_id = ? ORDER BY updated_at DESC LIMIT 1", (product_id,)).fetchone()
    if not row:
        return {"productId": product_id, "storeId": store_id, "title": f"商品 {product_id}", "storeName": store_id or "导入店铺"}
    payload = loads(row["payload"])
    return {
        **payload,
        "productId": row["product_id"],
        "storeId": row["normalized_store_id"] or row["store_id"],
        "storeName": row["normalized_store_name"] or row["store_name"],
        "title": row["title"],
        "platform": row["platform"],
        "category": row["category"],
        "assignedOperatorId": row["assigned_operator_id"],
        "ownerUserId": row["owner_user_id"],
        "reviewerId": row["reviewer_id"],
        "visibleUserIds": payload.get("visibleUserIds") or [],
        "visibleRoleIds": payload.get("visibleRoleIds") or [],
        "dataScopeSource": row["data_scope_source"],
    }


def _upload_cadence(facts: List[Dict[str, Any]]) -> Dict[str, Any]:
    dates = sorted({_safe_date(row.get("stat_date") or row.get("updated_at")) for row in facts if _safe_date(row.get("stat_date") or row.get("updated_at")) != "未知日期"})
    versions = sorted({str(row.get("data_version")) for row in facts if row.get("data_version")})
    if not dates:
        return {"uploadCount": len(versions), "statDateCount": 0, "daySpan": 0, "avgIntervalDays": None, "frequencyLevel": "unknown", "sensitivityMultiplier": 1.0, "baselineMode": True, "comparisonReady": False, "trendReady": False, "primaryAxis": "ROI_GMV"}
    parsed_dates = [item for item in (_date_value(date) for date in dates) if item]
    day_span = (max(parsed_dates) - min(parsed_dates)).days if parsed_dates else 0
    avg_interval = day_span / max(len(dates) - 1, 1) if len(dates) > 1 else None
    baseline_mode = len(dates) < 2
    comparison_ready = len(dates) >= 2
    trend_ready = len(dates) >= 3 or day_span >= 7
    if baseline_mode:
        level = "baseline"
        multiplier = 1.0
    elif len(dates) >= 3 and day_span <= 7:
        level = "high"
        multiplier = 0.55
    elif avg_interval is not None and avg_interval <= 7:
        level = "medium"
        multiplier = 0.75
    else:
        level = "low"
        multiplier = 1.0
    return {
        "uploadCount": len(versions) or len(dates),
        "statDateCount": len(dates),
        "firstStatDate": dates[0],
        "latestStatDate": dates[-1],
        "daySpan": day_span,
        "avgIntervalDays": avg_interval,
        "frequencyLevel": level,
        "sensitivityMultiplier": multiplier,
        "baselineMode": baseline_mode,
        "comparisonReady": comparison_ready,
        "trendReady": trend_ready,
        "primaryAxis": "ROI_GMV",
        "rule": "V12.5：首份报表只建基线，不生成ROI/GMV经营测试任务；两份报表才允许环比任务；三份或7天窗口才允许趋势任务。",
    }


def _entity_metrics(facts: List[Dict[str, Any]]) -> Dict[Tuple[str, str | None], Dict[str, List[Dict[str, Any]]]]:
    grouped: Dict[Tuple[str, str | None], Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in facts:
        product_id = str(row.get("product_id") or row.get("sku_id") or row.get("erp_product_code") or "").strip()
        if not product_id:
            continue
        store_id = str(row.get("store_id") or row.get("store_code") or row.get("store_name") or "").strip() or None
        metric = str(row.get("metric_code") or "").strip()
        if metric in TRACKED_METRICS:
            grouped[(product_id, store_id)][metric].append(row)
    for metrics in grouped.values():
        for rows in metrics.values():
            rows.sort(key=lambda item: (_safe_date(item.get("stat_date") or item.get("updated_at")), str(item.get("updated_at") or "")))
    return grouped


def _metric_pair(metrics: Dict[str, List[Dict[str, Any]]], code: str) -> Dict[str, Any]:
    rows = metrics.get(code) or []
    if not rows:
        return {"metricCode": code, "metricName": METRIC_LABELS.get(code, code), "missing": True, "comparable": False}
    first = rows[0]
    latest = rows[-1]
    old = _num(first.get("metric_value"))
    new = _num(latest.get("metric_value"))
    first_date = _safe_date(first.get("stat_date") or first.get("updated_at"))
    latest_date = _safe_date(latest.get("stat_date") or latest.get("updated_at"))
    comparable = first_date != latest_date and first_date != "未知日期" and latest_date != "未知日期"
    return {
        "metricCode": code,
        "metricName": METRIC_LABELS.get(code, code),
        "firstValue": old,
        "latestValue": new,
        "firstDisplayValue": first.get("display_value") or first.get("raw_value"),
        "latestDisplayValue": latest.get("display_value") or latest.get("raw_value"),
        "firstDate": first_date,
        "latestDate": latest_date,
        "changeRate": _pct_change(old, new) if comparable else None,
        "comparable": comparable,
        "factId": latest.get("fact_id"),
        "sourceSheet": latest.get("source_sheet"),
        "sourceBlockId": latest.get("source_block_id"),
        "dataVersion": latest.get("data_version"),
    }


def _movement(change: float | None, threshold: float) -> str:
    if change is None:
        return "unknown"
    if change >= threshold:
        return "up"
    if change <= -threshold:
        return "down"
    return "flat"


def _roi_gmv_quadrant(roi: Dict[str, Any], gmv: Dict[str, Any], threshold: float) -> Dict[str, Any]:
    if not (roi.get("comparable") and gmv.get("comparable")):
        return {"quadrant": "baseline_only", "quadrantName": "首份基线｜不生成经营测试", "actionType": "基线建档", "queueType": "report_seed_only", "score": 40, "roiMove": "baseline", "gmvMove": "baseline", "roiLevel": "baseline"}
    roi_latest = roi.get("latestValue")
    roi_change = roi.get("changeRate")
    gmv_change = gmv.get("changeRate")
    roi_move = _movement(roi_change, ROI_MOVE * threshold)
    gmv_move = _movement(gmv_change, GMV_MOVE * threshold)
    roi_level = "unknown"
    if roi_latest is not None:
        roi_level = "good" if roi_latest >= ROI_GOOD else "weak" if roi_latest < ROI_WEAK else "normal"
    if roi_move in {"up", "flat"} and gmv_move == "up" and roi_level != "weak":
        return {"quadrant": "high_roi_high_gmv", "quadrantName": "高ROI + 高GMV｜放量承接", "actionType": "GMV放量承接", "queueType": "daily_operating_task", "score": 92, "roiMove": roi_move, "gmvMove": gmv_move, "roiLevel": roi_level}
    if roi_move in {"up", "flat"} and gmv_move in {"flat", "down"} and roi_level != "weak":
        return {"quadrant": "high_roi_low_gmv", "quadrantName": "高ROI + 低GMV｜扩流测试", "actionType": "高ROI扩流测试", "queueType": "daily_operating_task", "score": 84, "roiMove": roi_move, "gmvMove": gmv_move, "roiLevel": roi_level}
    if roi_move == "down" and gmv_move == "up":
        return {"quadrant": "low_roi_high_gmv", "quadrantName": "低ROI + 高GMV｜效率复核", "actionType": "放量效率复核", "queueType": "daily_operating_task", "score": 88, "roiMove": roi_move, "gmvMove": gmv_move, "roiLevel": roi_level}
    if roi_move == "down" and gmv_move in {"flat", "down"}:
        return {"quadrant": "low_roi_low_gmv", "quadrantName": "低ROI + 低GMV｜降投排查", "actionType": "ROI异常排查", "queueType": "daily_operating_task", "score": 90, "roiMove": roi_move, "gmvMove": gmv_move, "roiLevel": roi_level}
    return {"quadrant": "neutral_roi_gmv", "quadrantName": "ROI/GMV常规观察", "actionType": "经营观察", "queueType": "report_seed_only", "score": 55, "roiMove": roi_move, "gmvMove": gmv_move, "roiLevel": roi_level}


def _signal_base(product: Dict[str, Any], cadence: Dict[str, Any], data_version: str | None, signal_type: str, risk_level: str, queue_type: str, domain: str, reason: str, metrics: List[Dict[str, Any]], score: float, *, quadrant: Dict[str, Any] | None = None) -> Dict[str, Any]:
    window = _window_bucket(int(cadence.get("daySpan") or 0))
    return {
        "version": OPERATING_CADENCE_VERSION,
        "signalId": make_id("CADSIG"),
        "dataVersion": data_version or next((m.get("dataVersion") for m in metrics if m.get("dataVersion")), None),
        "productId": product.get("productId"),
        "storeId": product.get("storeId"),
        "storeName": product.get("storeName"),
        "productTitle": product.get("title") or product.get("productId"),
        "signalType": signal_type,
        "riskLevel": risk_level,
        "queueType": queue_type,
        "riskDomain": domain,
        "metricScope": "product",
        "primaryAxis": "ROI_GMV",
        "roiGmvQuadrant": quadrant or {},
        "reason": reason,
        "score": score,
        "metrics": metrics,
        "cadence": cadence,
        "window": window,
        "reportTarget": "基线观察" if cadence.get("baselineMode") and queue_type == "report_seed_only" else window.get("reportTarget"),
        "agentJudgment": {
            "status": "v12_5_baseline_first_roi_gmv_agent_judgment",
            "boundary": "首份报表只建基线；两份报表才做环比；三份或7天窗口才做趋势任务；红线仍强制生成任务。",
            "reason": reason,
            "roiGmvQuadrant": (quadrant or {}).get("quadrantName"),
            "cadenceSensitivity": cadence.get("frequencyLevel"),
            "baselineMode": bool(cadence.get("baselineMode")),
        },
    }


def _baseline_signal(product: Dict[str, Any], metrics: List[Dict[str, Any]], cadence: Dict[str, Any], data_version: str | None) -> Dict[str, Any]:
    product_name = product.get("title") or product.get("productId") or "经营对象"
    return _signal_base(
        product,
        cadence,
        data_version,
        "baseline_snapshot",
        "低",
        "report_seed_only",
        "基线建档",
        f"{product_name} 已完成首份报表基线建档。当前只沉淀 ROI/GMV、库存、广告、转化等基线，不生成扩流、加投、降投等经营测试任务。",
        metrics,
        35,
        quadrant={"quadrant": "baseline_only", "quadrantName": "首份基线｜不生成经营测试", "actionType": "基线建档", "queueType": "report_seed_only"},
    )


def _build_signals_for_entity(product: Dict[str, Any], metrics: Dict[str, List[Dict[str, Any]]], cadence: Dict[str, Any], data_version: str | None) -> List[Dict[str, Any]]:
    mult = float(cadence.get("sensitivityMultiplier") or 1.0)
    roi = _metric_pair(metrics, "roi")
    gmv = _metric_pair(metrics, "payment_amount")
    ad_spend = _metric_pair(metrics, "ad_spend")
    inventory = _metric_pair(metrics, "inventory_qty")
    sellable_days = _metric_pair(metrics, "sellable_days")
    conversion = _metric_pair(metrics, "payment_conversion_rate")
    click_rate = _metric_pair(metrics, "click_rate")
    visitors = _metric_pair(metrics, "visitor_count")
    refund_rate = _metric_pair(metrics, "refund_rate")
    gross_margin = _metric_pair(metrics, "gross_margin_rate")
    paid_visitors = _metric_pair(metrics, "paid_visitor_count")
    organic_visitors = _metric_pair(metrics, "organic_visitor_count")

    product_name = product.get("title") or product.get("productId")
    primary_metrics = [roi, gmv, ad_spend, inventory, sellable_days, conversion, click_rate, refund_rate, gross_margin]
    signals: List[Dict[str, Any]] = []

    inv_latest = inventory.get("latestValue")
    gmv_latest = gmv.get("latestValue")
    if inv_latest is not None and inv_latest <= 0 and (gmv_latest is None or gmv_latest >= 0):
        signals.append(_signal_base(product, cadence, data_version, "redline_inventory_zero", "高", "urgent_execution", "库存承接", f"{product_name} 库存已归零，若 GMV/支付金额仍有承接，必须立即复核断货、下架、补货或替代主推。", [roi, gmv, inventory, sellable_days, conversion], 100))

    gm_latest = gross_margin.get("latestValue")
    if gm_latest is not None and gm_latest < 0.2:
        signals.append(_signal_base(product, cadence, data_version, "redline_margin_floor", "高", "urgent_execution", "利润底线", f"{product_name} 毛利率低于 20% 红线，ROI/GMV 放大前必须复核售价、成本、优惠和活动承接。", [roi, gmv, gross_margin, ad_spend], 96))

    rr_latest = refund_rate.get("latestValue")
    rr_change = refund_rate.get("changeRate")
    if rr_latest is not None and (rr_latest >= 0.12 or (rr_change is not None and rr_change >= 0.8)):
        signals.append(_signal_base(product, cadence, data_version, "redline_refund_floor", "高", "urgent_execution", "售后反噬", f"{product_name} 退款率已进入红线区，可能反噬 ROI 与 GMV，必须复核退款理由和售后承接。", [roi, gmv, refund_rate], 94))

    if cadence.get("baselineMode"):
        signals.append(_baseline_signal(product, primary_metrics, cadence, data_version))
        return signals

    if not cadence.get("comparisonReady"):
        signals.append(_baseline_signal(product, primary_metrics, cadence, data_version))
        return signals

    threshold = mult
    quadrant = _roi_gmv_quadrant(roi, gmv, threshold)
    roi_change = roi.get("changeRate")
    gmv_change = gmv.get("changeRate")
    ad_change = ad_spend.get("changeRate")
    inv_change = inventory.get("changeRate")
    conv_change = conversion.get("changeRate")
    click_change = click_rate.get("changeRate")
    paid_change = paid_visitors.get("changeRate")
    organic_change = organic_visitors.get("changeRate")

    if quadrant["queueType"] == "daily_operating_task":
        if quadrant["quadrant"] == "high_roi_high_gmv":
            reason = f"{product_name} 进入 {quadrant['quadrantName']}：ROI变化 {_fmt_change(roi_change)}，GMV变化 {_fmt_change(gmv_change)}。优先判断加投、补货、主推位承接。"
        elif quadrant["quadrant"] == "high_roi_low_gmv":
            reason = f"{product_name} 进入 {quadrant['quadrantName']}：ROI表现可用，但GMV未放大，优先排查流量入口、人群、渠道和主推曝光。"
        elif quadrant["quadrant"] == "low_roi_high_gmv":
            reason = f"{product_name} 进入 {quadrant['quadrantName']}：GMV放大但ROI转弱，可能是放量效率下降，需要复核预算、人群、素材和转化承接。"
        else:
            reason = f"{product_name} 进入 {quadrant['quadrantName']}：ROI变化 {_fmt_change(roi_change)}，GMV变化 {_fmt_change(gmv_change)}，需要排查素材、流量、转化、价格、库存和售后。"
        signals.append(_signal_base(product, cadence, data_version, f"roi_gmv_quadrant_{quadrant['quadrant']}", "中", "daily_operating_task", "ROI/GMV", reason, [roi, gmv, ad_spend, inventory, conversion, click_rate, paid_visitors, organic_visitors], quadrant["score"], quadrant=quadrant))

    if ad_change is not None and ad_change >= (0.18 * mult) and roi_change is not None and roi_change <= -(0.05 * mult):
        signals.append(_signal_base(product, cadence, data_version, "roi_ad_spend_efficiency_review", "中", "daily_operating_task", "投产", f"{product_name} 广告消耗 {_fmt_change(ad_change)}，ROI {_fmt_change(roi_change)}。投放效率变化优先级高于普通指标波动，需要当天复核预算、关键词、人群、素材。", [roi, gmv, ad_spend, click_rate, conversion], 89, quadrant=quadrant))

    if roi.get("latestValue") is not None and roi.get("latestValue") >= ROI_GOOD and inv_latest is not None and (inv_latest <= 0 or (inv_change is not None and inv_change <= -(0.15 * mult))):
        signals.append(_signal_base(product, cadence, data_version, "high_roi_low_inventory_restock", "中", "daily_operating_task", "库存承接", f"{product_name} ROI可用但库存变化 {_fmt_change(inv_change)}，属于高投产商品承接不足，应优先判断补货、调拨或主推位替换。", [roi, gmv, inventory, sellable_days], 86, quadrant=quadrant))

    if conv_change is not None and conv_change <= -(0.10 * mult) and (roi_change is None or roi_change <= 0.02):
        signals.append(_signal_base(product, cadence, data_version, "roi_gmv_conversion_explain", "中", "daily_operating_task", "转化承接", f"{product_name} 转化率 {_fmt_change(conv_change)}，可能解释 ROI/GMV 变化，需要排查主图、详情页、价格、评价和客服承接。", [roi, gmv, conversion, click_rate, visitors], 80, quadrant=quadrant))

    if click_change is not None and click_change <= -(0.10 * mult) and (paid_change is not None or organic_change is not None):
        signals.append(_signal_base(product, cadence, data_version, "roi_gmv_click_explain", "中", "daily_operating_task", "素材点击", f"{product_name} 点击率 {_fmt_change(click_change)}，可能影响 ROI/GMV 放大，需要复核主图、标题、素材和流量人群。", [roi, gmv, click_rate, paid_visitors, organic_visitors], 76, quadrant=quadrant))

    return signals


def _actions_for_signal(signal: Dict[str, Any]) -> List[str]:
    signal_type = signal.get("signalType") or ""
    quadrant = (signal.get("roiGmvQuadrant") or {}).get("quadrant")
    if signal_type == "redline_inventory_zero":
        return ["6小时内核对真实库存、可售天数和在途库存。", "6小时内判断是否暂停投放、下架缺货链接或切换替代主推品。", "12小时内给出补货、替换主推位或客服承接话术结论。"]
    if signal_type in {"redline_margin_floor", "redline_refund_floor"}:
        return ["6小时内复核利润、退款、广告消耗和GMV承接。", "确认是否暂停加投或收紧活动优惠。", "提交红线原因、证据截图和处理结论。"]
    if quadrant == "high_roi_high_gmv":
        return ["今日内确认ROI是否稳定高于店铺投产底线。", "复核库存和可售天数是否支撑未来3-7天放量。", "提交加预算、补货、主推位承接或素材扩量建议。"]
    if quadrant == "high_roi_low_gmv":
        return ["今日内复核流量入口、曝光、人群和渠道覆盖。", "选择1-2个低风险渠道做扩流测试。", "提交扩流测试预算、周期和回看指标。"]
    if quadrant == "low_roi_high_gmv":
        return ["12小时内拆分广告计划、人群、关键词或素材消耗。", "确认GMV放大是否以ROI恶化为代价。", "提交降预算、换素材、换人群或保GMV控ROI方案。"]
    if quadrant == "low_roi_low_gmv":
        return ["今日内排查流量、点击、转化、价格、库存和竞品变化。", "判断是否暂停加投、降预算或进入素材/主图测试。", "提交一个优先处理动作和复核时间。"]
    return ["24小时内围绕ROI和GMV复核该商品变化。", "用库存、流量、点击率、转化率、退款率解释变化原因。", "提交加投、降投、补货、换素材、查转化或观察的明确结论。"]


def _task_payload(signal: Dict[str, Any]) -> Dict[str, Any]:
    product = _context_for_product(signal.get("productId"), signal.get("storeId"))
    actions = _actions_for_signal(signal)
    risk_level = signal.get("riskLevel") or "中"
    queue_type = signal.get("queueType") or "daily_operating_task"
    deadline = "6小时内" if risk_level == "高" else "今日内" if queue_type == "daily_operating_task" else "本周内"
    title = signal.get("productTitle") or product.get("title") or signal.get("productId") or "经营对象"
    quadrant_name = (signal.get("roiGmvQuadrant") or {}).get("quadrantName") or "ROI/GMV经营判断"
    subtitle = "红线强制复核" if risk_level == "高" else quadrant_name
    evidence_pack = [
        {
            "type": "roi_gmv_operating_signal",
            "title": item.get("metricName") or item.get("metricCode"),
            "metric": item.get("latestDisplayValue") or item.get("latestValue"),
            "reason": f"{item.get('metricName')} {item.get('firstDisplayValue')} → {item.get('latestDisplayValue')}，变化 {_fmt_change(item.get('changeRate'))}",
            "dataVersion": item.get("dataVersion") or signal.get("dataVersion"),
            "sourceSheet": item.get("sourceSheet"),
            "sourceBlockId": item.get("sourceBlockId"),
        }
        for item in signal.get("metrics") or []
        if not item.get("missing")
    ]
    ownership = {
        "assignedOperatorId": product.get("assignedOperatorId"),
        "ownerUserId": product.get("ownerUserId") or product.get("assignedOperatorId"),
        "reviewerId": product.get("reviewerId"),
        "visibleUserIds": list(dict.fromkeys([item for item in [product.get("assignedOperatorId"), product.get("ownerUserId"), product.get("reviewerId"), *(product.get("visibleUserIds") or [])] if item])),
        "visibleRoleIds": list(dict.fromkeys([*(product.get("visibleRoleIds") or []), "owner", "manager", "operator"])),
        "dataScopeSource": product.get("dataScopeSource") or "uploader_account",
        "rule": "V12.5 任务继承商品/店铺归属，ROI/GMV信号不反向制造权限。",
    }
    detail = {
        "version": OPERATING_CADENCE_VERSION,
        "title": f"ROI/GMV经营任务｜{title} · {subtitle}",
        "warningSummary": signal.get("reason"),
        "primaryAxis": "ROI_GMV",
        "baselineMode": bool((signal.get("cadence") or {}).get("baselineMode")),
        "roiGmvQuadrant": signal.get("roiGmvQuadrant"),
        "cadence": signal.get("cadence"),
        "window": signal.get("window"),
        "evidencePack": evidence_pack,
        "sopSteps": actions,
        "reviewMetrics": {"primaryMetrics": ["ROI", "GMV/支付金额", "广告消耗"], "explainMetrics": ["库存", "流量", "点击率", "转化率", "退款率", "毛利率"], "metricScope": "product", "requiredFactTables": ["product_metric_facts"]},
        "completionGate": ["已说明ROI/GMV变化", "已用解释指标定位原因", "已提交加投/降投/补货/测试/观察结论", "总管复核后进入日报/周报复盘"],
        "failureThreshold": {"riskLevel": risk_level, "queueType": queue_type, "rule": "红线必须处理；非红线动作必须至少有两份可比报表。"},
        "agentBoundary": "Agent 只生成经营判断和SOP，不直接改预算、库存、价格或店铺数据。",
    }
    return {
        "id": make_id("ROITASK"),
        "taskGenerationMode": "v11_8_sop_package",
        "title": title,
        "taskCard": {"title": title, "subtitle": subtitle, "deadline": deadline, "priority": risk_level, "ownerRole": "运营" if ownership.get("assignedOperatorId") else "总管"},
        "taskDetailReport": detail,
        "evidencePack": evidence_pack,
        "sopSteps": actions,
        "reviewMetrics": detail["reviewMetrics"],
        "completionGate": detail["completionGate"],
        "failureThreshold": detail["failureThreshold"],
        "task": actions[0],
        "taskType": "红线强制复核任务" if risk_level == "高" else "ROI/GMV经营调整任务",
        "priority": risk_level,
        "deadline": deadline,
        "timeBucket": deadline,
        "urgencyLevel": "urgent" if risk_level == "高" else "today",
        "queueType": queue_type,
        "displayState": "expanded",
        "source": "ROI/GMV经营节奏Agent",
        "sourceModule": "ROI/GMV经营节奏Agent",
        "sourceRoute": "business-actions",
        "productId": signal.get("productId"),
        "entityId": signal.get("productId"),
        "entityType": "商品",
        "store": product.get("storeName") or signal.get("storeName") or signal.get("storeId") or "导入店铺",
        "storeName": product.get("storeName") or signal.get("storeName"),
        "storeIds": [signal.get("storeId")] if signal.get("storeId") else [],
        "visibleStoreIds": [signal.get("storeId")] if signal.get("storeId") else [],
        "platform": product.get("platform") or "未知平台",
        "category": product.get("category") or "未分类",
        "riskDomain": signal.get("riskDomain"),
        "metricScope": "product",
        "actionType": (signal.get("roiGmvQuadrant") or {}).get("actionType") or "ROI/GMV经营调整",
        "taskLayer": "manager_dispatch" if risk_level == "高" else "operator_execution",
        "assigneeId": ownership.get("assignedOperatorId") if risk_level != "高" else None,
        "reviewerId": ownership.get("reviewerId"),
        "visibleUserIds": ownership.get("visibleUserIds"),
        "visibleRoleIds": ["owner", "manager", "finance"] if risk_level == "高" else ownership.get("visibleRoleIds"),
        "ownership": ownership,
        "sourceEvent": f"V125:{signal.get('dataVersion') or 'latest'}:{signal.get('productId')}:{signal.get('signalType')}:{(signal.get('roiGmvQuadrant') or {}).get('quadrant')}",
        "riskGrade": risk_level,
        "riskPolicy": {"riskMode": "baseline_first_roi_gmv_operating_cadence", "requiresEvidenceGate": True, "requiresApproval": risk_level == "高", "rule": "V12.5 首份报表基线建档，非红线经营任务必须有对比数据。"},
        "investmentApplicationAllowed": False,
        "executionAllowed": risk_level != "高",
        "approvalChain": ["总管复核"] if risk_level == "高" else [],
        "executionRequirements": actions,
        "judgmentTags": ["V12.5 基线优先", risk_level, signal.get("riskDomain"), queue_type, (signal.get("window") or {}).get("label"), (signal.get("roiGmvQuadrant") or {}).get("quadrantName")],
        "evidence": evidence_pack,
        "reason": signal.get("reason"),
        "agentJudgment": signal.get("agentJudgment"),
        "sourceTrail": ["报表中心", "指标事实表", "ROI/GMV经营节奏Agent", "任务证据闸门", "日报/周报候选"],
        "recapTarget": "日报" if queue_type == "daily_operating_task" or risk_level == "高" else "周报",
    }


def _save_signal(signal: Dict[str, Any], status: str, task_id: str | None = None) -> Dict[str, Any]:
    ensure_operating_cadence_tables()
    payload = {**signal, "status": status, "taskId": task_id}
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO operating_cadence_signals (
                signal_id, data_version, product_id, store_id, metric_scope, cadence_window,
                signal_type, risk_level, queue_type, status, task_id, payload, created_at,
                upload_frequency_level, report_target, agent_judgment, roi_gmv_quadrant, primary_axis, baseline_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.get("signalId"),
                signal.get("dataVersion"),
                signal.get("productId"),
                signal.get("storeId"),
                signal.get("metricScope") or "product",
                (signal.get("window") or {}).get("windowType"),
                signal.get("signalType"),
                signal.get("riskLevel"),
                signal.get("queueType"),
                status,
                task_id,
                dumps(payload),
                now_iso(),
                (signal.get("cadence") or {}).get("frequencyLevel"),
                signal.get("reportTarget"),
                dumps(signal.get("agentJudgment") or {}),
                (signal.get("roiGmvQuadrant") or {}).get("quadrant"),
                signal.get("primaryAxis") or "ROI_GMV",
                1 if (signal.get("cadence") or {}).get("baselineMode") else 0,
            ),
        )
        conn.commit()
    return payload


def _signal_can_create_task(signal: Dict[str, Any], cadence: Dict[str, Any]) -> bool:
    if signal.get("signalType") in REDLINE_TYPES:
        return True
    if cadence.get("baselineMode") or not cadence.get("comparisonReady"):
        return False
    return signal.get("queueType") in {"urgent_execution", "daily_operating_task", "weekly_review_task"}


def generate_operating_cadence_tasks(data_version: str | None = None, *, max_tasks: int = 16) -> Dict[str, Any]:
    ensure_operating_cadence_tables()
    facts = _load_facts()
    cadence = _upload_cadence(facts)
    grouped = _entity_metrics(facts)
    latest_version = data_version or next((row.get("data_version") for row in reversed(facts) if row.get("data_version")), None)
    signals: List[Dict[str, Any]] = []
    for (product_id, store_id), metrics in grouped.items():
        product = _context_for_product(product_id, store_id)
        signals.extend(_build_signals_for_entity(product, metrics, cadence, latest_version))
    signals.sort(key=lambda item: (0 if item.get("riskLevel") == "高" else 1, -float(item.get("score") or 0), item.get("productId") or ""))

    tasks: List[Dict[str, Any]] = []
    candidate_count = 0
    report_only_count = 0
    baseline_signal_count = 0
    blocked_by_baseline = 0
    for signal in signals:
        if signal.get("signalType") == "baseline_snapshot":
            baseline_signal_count += 1
        if _signal_can_create_task(signal, cadence) and (len(tasks) < max_tasks or signal.get("riskLevel") == "高"):
            payload = _task_payload(signal)
            gated = apply_evidence_gate_to_created_task(payload)
            task = module_task_service.create_task(gated)
            tasks.append(task)
            _save_signal(signal, "task_created", task.get("id"))
        elif cadence.get("baselineMode") and signal.get("signalType") not in REDLINE_TYPES:
            blocked_by_baseline += 1
            report_only_count += 1
            _save_signal(signal, "baseline_snapshot")
        elif signal.get("queueType") in {"report_seed_only", "candidate_only"}:
            candidate_count += 1 if signal.get("queueType") == "candidate_only" else 0
            report_only_count += 1 if signal.get("queueType") == "report_seed_only" else 0
            _save_signal(signal, signal.get("queueType") or "report_seed_only")
        else:
            report_only_count += 1
            _save_signal(signal, "report_seed_only")

    return {
        "version": OPERATING_CADENCE_VERSION,
        "mode": "v12_5_baseline_first_roi_gmv_operating_task_generation",
        "dataVersion": data_version,
        "cadence": cadence,
        "primaryAxis": "ROI_GMV",
        "baselineMode": bool(cadence.get("baselineMode")),
        "comparisonReady": bool(cadence.get("comparisonReady")),
        "trendReady": bool(cadence.get("trendReady")),
        "quadrantPolicy": {
            "baseline_only": "首份报表只建基线，不生成扩流/加投/降投测试",
            "high_roi_high_gmv": "放量承接",
            "high_roi_low_gmv": "扩流测试",
            "low_roi_high_gmv": "效率复核",
            "low_roi_low_gmv": "降投排查",
        },
        "signalCount": len(signals),
        "baselineSignalCount": baseline_signal_count,
        "blockedByBaselineCount": blocked_by_baseline,
        "createdTaskCount": len(tasks),
        "candidateCount": candidate_count,
        "reportSeedOnlyCount": report_only_count,
        "tasks": tasks,
        "topSignals": signals[:20],
        "rule": "V12.5：第一份报表只建基线；第二份才允许环比任务；第三份或7天窗口才允许趋势任务；红线任务仍强制进入执行队列。",
    }


def operating_cadence_summary(limit: int = 40) -> Dict[str, Any]:
    ensure_operating_cadence_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM operating_cadence_signals ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    items = [loads(row["payload"]) for row in rows]
    by_status: Dict[str, int] = defaultdict(int)
    by_window: Dict[str, int] = defaultdict(int)
    by_report: Dict[str, int] = defaultdict(int)
    by_quadrant: Dict[str, int] = defaultdict(int)
    baseline_count = 0
    for item in items:
        by_status[str(item.get("status") or "unknown")] += 1
        by_window[str((item.get("window") or {}).get("windowType") or "unknown")] += 1
        by_report[str(item.get("reportTarget") or "unknown")] += 1
        by_quadrant[str((item.get("roiGmvQuadrant") or {}).get("quadrant") or "unknown")] += 1
        if (item.get("cadence") or {}).get("baselineMode"):
            baseline_count += 1
    return {
        "version": OPERATING_CADENCE_VERSION,
        "primaryAxis": "ROI_GMV",
        "baselineSignalCount": baseline_count,
        "signalCount": len(items),
        "byStatus": dict(by_status),
        "byWindow": dict(by_window),
        "byReportTarget": dict(by_report),
        "byRoiGmvQuadrant": dict(by_quadrant),
        "dailyReportSeeds": [item for item in items if "日报" in str(item.get("reportTarget"))][:12],
        "weeklyReportSeeds": [item for item in items if "周报" in str(item.get("reportTarget"))][:12],
        "baselineSeeds": [item for item in items if (item.get("cadence") or {}).get("baselineMode")][:12],
        "items": items,
        "rule": "日报/周报优先围绕 ROI、GMV、广告消耗展示；首份报表只进入基线观察，不能生成经营测试任务。",
    }
