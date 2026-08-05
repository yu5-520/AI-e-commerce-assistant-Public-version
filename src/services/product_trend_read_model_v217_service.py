"""V21.7 single-product trend read model.

The product detail page reads one product's effective observations only:
- a report that does not contain the product is skipped;
- same-business-date partial observations are merged without writing zeroes;
- the latest five effective observations are returned for direct comparison;
- older observations contribute deterministic previous/MoM/YoY/slope/volatility features;
- inventory remains a capacity fact and never becomes an operating action here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Sequence

from src.repositories.sqlite_repository import connect, loads
from src.services.v215_report_batch_evidence_service import build_time_series_features

PRODUCT_TREND_READ_MODEL_VERSION = "21.7.0"
RECENT_DIRECT_WINDOW = 5
MAX_HISTORY_OBSERVATIONS = 30
MAX_SNAPSHOT_SCAN = 120

METRIC_DEFINITIONS: List[Dict[str, str]] = [
    {"code": "paymentAmount", "label": "支付金额", "group": "经营结果", "kind": "money"},
    {"code": "roi", "label": "ROI", "group": "经营结果", "kind": "number"},
    {"code": "adSpend", "label": "广告消耗", "group": "经营结果", "kind": "money"},
    {"code": "grossMargin", "label": "毛利率", "group": "经营结果", "kind": "percent"},
    {"code": "organicVisitors", "label": "自然访客", "group": "流量与承接", "kind": "integer"},
    {"code": "paidVisitors", "label": "付费访客", "group": "流量与承接", "kind": "integer"},
    {"code": "visitorCount", "label": "总访客", "group": "流量与承接", "kind": "integer"},
    {"code": "clickRate", "label": "点击率", "group": "流量与承接", "kind": "percent"},
    {"code": "conversionRate", "label": "转化率", "group": "流量与承接", "kind": "percent"},
    {"code": "refundRate", "label": "退款率", "group": "售后与服务", "kind": "percent"},
    {"code": "afterSalesRate", "label": "售后率", "group": "售后与服务", "kind": "percent"},
    {"code": "refundAmount", "label": "退款金额", "group": "售后与服务", "kind": "money"},
    {"code": "inventory", "label": "可售库存", "group": "库存容量", "kind": "integer"},
    {"code": "availableDays", "label": "可售天数", "group": "库存容量", "kind": "number"},
]

METRIC_ALIASES: Dict[str, Sequence[str]] = {
    "paymentAmount": ("paymentAmount", "gmv"),
    "roi": ("roi", "roas"),
    "adSpend": ("adSpend",),
    "grossMargin": ("grossMargin",),
    "organicVisitors": ("organicVisitors",),
    "paidVisitors": ("paidVisitors",),
    "visitorCount": ("visitorCount", "visitors"),
    "clickRate": ("clickRate",),
    "conversionRate": ("conversionRate",),
    "refundRate": ("refundRate", "afterSalesRate"),
    "afterSalesRate": ("afterSalesRate", "refundRate"),
    "refundAmount": ("refundAmount",),
    "inventory": ("inventory",),
    "availableDays": ("availableDays", "sellableDays"),
}

_CACHE: Dict[tuple[str, str, str, int], Dict[str, Any]] = {}


def _safe_load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        result = loads(value)
    except Exception:
        result = {}
    return result if isinstance(result, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> float | None:
    if value in {None, "", "—", "UNKNOWN", "未识别"}:
        return None
    text = str(value).replace(",", "").replace("￥", "").replace("¥", "").strip()
    is_percent = text.endswith("%")
    text = text.replace("%", "")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number / 100 if is_percent and abs(number) > 1 else number


def _change(previous: float | None, current: float | None) -> float | None:
    if previous is None or current is None:
        return None
    if abs(previous) < 1e-12:
        if abs(current) < 1e-12:
            return 0.0
        return 1.0 if current > 0 else -1.0
    return (current - previous) / abs(previous)


def _metric(item: Dict[str, Any], code: str) -> float | None:
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    for key in METRIC_ALIASES.get(code, (code,)):
        value = metric.get(key)
        if value in {None, "", "—", "UNKNOWN", "未识别"}:
            value = item.get(key)
        number = _num(value)
        if number is not None:
            return number
    return None


def _identity_values(item: Dict[str, Any]) -> set[str]:
    profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
    values = {
        item.get("objectId"),
        item.get("id"),
        item.get("productId"),
        item.get("skuId"),
        profile.get("objectId"),
        profile.get("productId"),
        profile.get("skuId"),
    }
    return {_text(value) for value in values if _text(value)}


def _store_id(item: Dict[str, Any]) -> str:
    profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
    return _text(item.get("storeId") or profile.get("storeId"))


def _matches(item: Dict[str, Any], product_id: str, store_id: str | None) -> bool:
    if store_id and _store_id(item) != _text(store_id):
        return False
    return _text(product_id) in _identity_values(item)


def _first_product(snapshot: Dict[str, Any], product_id: str, store_id: str | None) -> Dict[str, Any] | None:
    for item in snapshot.get("products") or []:
        if isinstance(item, dict) and _matches(item, product_id, store_id):
            return item
    return None


def _business_date(item: Dict[str, Any], snapshot: Dict[str, Any]) -> tuple[str, str]:
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
    candidates = [
        item.get("metricDate"), item.get("reportDate"), item.get("dataDate"),
        metric.get("metricDate"), metric.get("reportDate"), metric.get("dataDate"),
        profile.get("metricDate"), profile.get("reportDate"), profile.get("dataDate"),
    ]
    for value in candidates:
        text = _text(value)
        if text and text not in {"—", "未识别"}:
            return text[:10].replace("/", "-").replace(".", "-"), "report_business_date"
    created = _text(snapshot.get("createdAt") or snapshot.get("updatedAt"))
    if created:
        return created[:10], "snapshot_created_at"
    return _text(snapshot.get("dataVersion")) or "未知日期", "data_version_fallback"


def _observation(snapshot: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any] | None:
    metrics = {
        definition["code"]: value
        for definition in METRIC_DEFINITIONS
        if (value := _metric(item, definition["code"])) is not None
    }
    if not metrics:
        return None
    business_date, date_source = _business_date(item, snapshot)
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    versions = [
        *([snapshot.get("dataVersion")] if snapshot.get("dataVersion") else []),
        *(item.get("sourceDataVersions") or []),
        *(metric.get("sourceDataVersions") or []),
    ]
    datasets = [*(item.get("sourceDatasets") or []), *(metric.get("sourceDatasets") or [])]
    return {
        "businessDate": business_date,
        "dateSource": date_source,
        "snapshotId": snapshot.get("snapshotId"),
        "dataVersion": snapshot.get("dataVersion"),
        "sourceDataVersions": list(dict.fromkeys(_text(value) for value in versions if _text(value))),
        "sourceDatasets": list(dict.fromkeys(_text(value) for value in datasets if _text(value))),
        "createdAt": snapshot.get("createdAt"),
        "updatedAt": snapshot.get("updatedAt"),
        "metrics": metrics,
        "observedMetrics": list(metrics),
        "changes": {},
        "rawItem": item,
    }


def _merge_same_business_date(current: Dict[str, Any], older: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(current)
    merged_metrics = dict(current.get("metrics") or {})
    for code, value in (older.get("metrics") or {}).items():
        merged_metrics.setdefault(code, value)
    merged["metrics"] = merged_metrics
    merged["observedMetrics"] = list(merged_metrics)
    merged["sourceDataVersions"] = list(dict.fromkeys([*(current.get("sourceDataVersions") or []), *(older.get("sourceDataVersions") or [])]))
    merged["sourceDatasets"] = list(dict.fromkeys([*(current.get("sourceDatasets") or []), *(older.get("sourceDatasets") or [])]))
    return merged


def _chronological_key(observation: Dict[str, Any]) -> tuple[str, str]:
    return (_text(observation.get("businessDate")), _text(observation.get("createdAt")))


def _synthetic_item(observation: Dict[str, Any]) -> Dict[str, Any]:
    metrics = dict(observation.get("metrics") or {})
    metrics["metricDate"] = observation.get("businessDate")
    return {
        "metricDate": observation.get("businessDate"),
        "dataDate": observation.get("businessDate"),
        "metricSnapshot": metrics,
    }


def _add_direct_changes(observations: List[Dict[str, Any]]) -> None:
    previous_by_metric: Dict[str, float] = {}
    for observation in observations:
        changes: Dict[str, float | None] = {}
        for code, value in (observation.get("metrics") or {}).items():
            changes[code] = _change(previous_by_metric.get(code), value)
            previous_by_metric[code] = value
        observation["changes"] = changes


def _trend_state(features: Dict[str, Dict[str, Any]], count: int) -> Dict[str, str]:
    if count < 2:
        return {"code": "insufficient_data", "label": "数据不足"}
    core_codes = ("paymentAmount", "roi", "clickRate", "conversionRate")
    positive = 0
    negative = 0
    volatile = 0
    for code in core_codes:
        feature = features.get(code) or {}
        slope = feature.get("slope5")
        previous = feature.get("previousDelta")
        volatility = feature.get("volatility10")
        if slope is not None and previous is not None:
            if float(slope) >= 0.03 and float(previous) >= 0.03:
                positive += 1
            elif float(slope) <= -0.03 and float(previous) <= -0.03:
                negative += 1
        if volatility is not None and abs(float(volatility)) >= 0.2:
            volatile += 1
    if negative >= 2:
        return {"code": "persistent_decline", "label": "持续下降"}
    if positive >= 2:
        return {"code": "growth_trend", "label": "增长趋势"}
    if volatile >= 2:
        return {"code": "short_term_volatility", "label": "短期波动"}
    return {"code": "stable", "label": "整体稳定"}


def build_product_trend_projection(
    snapshots: Sequence[Dict[str, Any]],
    product_id: str,
    *,
    store_id: str | None = None,
) -> Dict[str, Any]:
    grouped: Dict[str, Dict[str, Any]] = {}
    latest_item: Dict[str, Any] | None = None
    latest_data_version: str | None = None

    for snapshot in snapshots:
        item = _first_product(snapshot, product_id, store_id)
        if not item:
            continue
        latest_item = latest_item or item
        latest_data_version = latest_data_version or _text(snapshot.get("dataVersion")) or None
        observation = _observation(snapshot, item)
        if not observation:
            continue
        key = _text(observation.get("businessDate")) or _text(observation.get("dataVersion"))
        grouped[key] = _merge_same_business_date(grouped[key], observation) if key in grouped else observation

    observations = sorted(grouped.values(), key=_chronological_key)
    _add_direct_changes(observations)
    recent = observations[-RECENT_DIRECT_WINDOW:]
    history_for_features = observations[-MAX_HISTORY_OBSERVATIONS:]

    features: Dict[str, Dict[str, Any]] = {}
    if history_for_features:
        current = _synthetic_item(history_for_features[-1])
        previous = [_synthetic_item(item) for item in reversed(history_for_features[:-1])]
        features = build_time_series_features(current, previous)

    usable_definitions = [
        definition
        for definition in METRIC_DEFINITIONS
        if any(definition["code"] in (item.get("metrics") or {}) for item in observations)
    ]
    possible = max(1, len(usable_definitions) * max(1, len(recent)))
    observed_count = sum(len(item.get("observedMetrics") or []) for item in recent)
    completeness = round(min(1.0, observed_count / possible), 4)

    profile = latest_item.get("profileSnapshot") if isinstance((latest_item or {}).get("profileSnapshot"), dict) else {}
    identity = {
        "objectId": (latest_item or {}).get("objectId") or profile.get("objectId"),
        "productId": (latest_item or {}).get("productId") or profile.get("productId") or product_id,
        "skuId": (latest_item or {}).get("skuId") or profile.get("skuId"),
        "storeId": (latest_item or {}).get("storeId") or profile.get("storeId") or store_id,
        "storeName": (latest_item or {}).get("storeName") or profile.get("storeName"),
        "platform": (latest_item or {}).get("platform") or profile.get("platform"),
        "title": (latest_item or {}).get("title") or profile.get("title"),
    }

    return {
        "version": PRODUCT_TREND_READ_MODEL_VERSION,
        "ready": bool(observations),
        "product": identity,
        "latestDataVersion": latest_data_version,
        "observationSummary": {
            "validSnapshotCount": len(observations),
            "recentWindowSize": len(recent),
            "latestBusinessDate": observations[-1].get("businessDate") if observations else None,
            "oldestBusinessDate": observations[0].get("businessDate") if observations else None,
            "historyAlgorithmWindowCount": len(history_for_features),
            "dataCompleteness": completeness,
            "missingReportMeansZero": False,
        },
        "trendState": _trend_state(features, len(observations)),
        "metricDefinitions": usable_definitions,
        "recentSnapshots": [
            {key: value for key, value in item.items() if key != "rawItem"}
            for item in recent
        ],
        "metricTrends": {definition["code"]: features.get(definition["code"], {}) for definition in usable_definitions},
        "inventoryBoundary": "inventory_and_available_days_are_capacity_facts_not_operating_action_families",
        "readRule": "Only effective product observations are included; latest five compare directly; older observations provide previous/MoM/YoY/slope/volatility evidence; missing product reports are skipped, never written as zero.",
    }


def _snapshot_rows(limit: int = MAX_SNAPSHOT_SCAN) -> List[Dict[str, Any]]:
    with connect() as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_product_snapshots_v14'"
        ).fetchone()
        if not table:
            return []
        rows = conn.execute(
            "SELECT snapshot_id,data_version,payload,created_at,updated_at FROM system_product_snapshots_v14 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result: List[Dict[str, Any]] = []
    for row in rows:
        payload = _safe_load(row["payload"])
        result.append(
            {
                **payload,
                "snapshotId": row["snapshot_id"],
                "dataVersion": row["data_version"] or payload.get("dataVersion"),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
        )
    return result


def read_product_trend(product_id: str, *, store_id: str | None = None) -> Dict[str, Any]:
    snapshots = _snapshot_rows()
    latest_stamp = _text(snapshots[0].get("updatedAt")) if snapshots else "none"
    cache_key = (_text(product_id), _text(store_id), latest_stamp, len(snapshots))
    cached = _CACHE.get(cache_key)
    if cached:
        return {**cached, "cacheState": "memory_hit"}
    projection = build_product_trend_projection(snapshots, product_id, store_id=store_id)
    if len(_CACHE) >= 256:
        _CACHE.clear()
    _CACHE[cache_key] = projection
    return {**projection, "cacheState": "fresh"}
