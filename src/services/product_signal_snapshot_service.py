"""Competition full-product evidence snapshot service.

V18.7 keeps the V18.6 fullProductBundle business contract but replaces the legacy
runtime history rebuild with a hash-directed competition cache:

- canonical product snapshots remain the sole fact authority;
- an existing current canonical snapshot is never force-rebuilt by Evidence;
- history is scoped to the current competition epoch and strictly earlier imported
  reports;
- at most two comparable prior observations are hydrated, one canonical row at a
  time, and each is reduced to a compact immutable observation cache;
- ``evidenceInputHash`` binds the current canonical set hash, current compact
  observation hash, previous comparable observation hashes, history epoch and the
  immutable operating-evidence contract version;
- identical immutable inputs reuse the persisted Evidence snapshot even when an old
  caller passes ``force=True``.

The 7/30/90 window names remain compatibility labels. In the competition build they
operate over the bounded comparable observations actually provided by the evaluator,
not over an unbounded database scan.
"""
from __future__ import annotations

import gc
import hashlib
import json
from datetime import datetime
from statistics import mean
from typing import Any, Dict, List, Set

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.canonical_product_trend_v2_service import current_competition_history_epoch
from src.services.system_product_snapshot_service import get_product_snapshot, materialize_system_product_snapshot

PRODUCT_SIGNAL_SNAPSHOT_VERSION = "18.7-competition-hash-cache"
EVIDENCE_INPUT_CONTRACT = "competition.evidenceInput.v1"
EVIDENCE_CONTRACT = "operatingEvidenceGraph.v1"
EVIDENCE_CONTRACT_VERSION = "21.5.0"
EVIDENCE_CACHE_MODE = "competition_hash_precache"
OBSERVATION_TABLE = "competition_evidence_observation_v1"
MAX_COMPETITION_HISTORY_CANDIDATES = 8
MAX_COMPETITION_COMPARABLE_HISTORY = 2
COMPARE_FIELDS = {
    "inventory": "product_inventory_changed",
    "paymentAmount": "product_payment_changed",
    "grossMargin": "product_margin_changed",
    "roas": "product_roas_changed",
    "roi": "product_roi_changed",
    "clickRate": "product_click_changed",
    "conversionRate": "product_conversion_changed",
    "refundRate": "product_refund_changed",
    "adSpend": "product_ad_spend_changed",
    "organicVisitors": "product_organic_changed",
    "paidVisitors": "product_paid_changed",
    "afterSalesRate": "product_after_sales_changed",
    "refundOrderCount": "product_refund_order_changed",
    "refundAmount": "product_refund_amount_changed",
    "availableDays": "product_available_days_changed",
    "visitorCount": "product_visitor_changed",
    "gmv": "product_gmv_changed",
}
METRIC_ALIASES = {
    "availableDays": ["availableDays", "sellableDays", "可售天数"],
    "visitorCount": ["visitorCount", "visitors", "访客数"],
    "afterSalesRate": ["afterSalesRate", "afterSales", "售后率"],
    "refundOrderCount": ["refundOrderCount", "refundOrders", "退款订单数"],
    "refundAmount": ["refundAmount", "退款金额"],
    "gmv": ["gmv", "GMV", "paymentAmount"],
}
WINDOWS = {"previous": 1, "7d": 7, "30d": 30, "90d": 90}
ZERO_CHANGE_EPSILON = 1e-9


def now_iso() -> str:
    return datetime.now().isoformat()


def _stable_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_product_signal_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_signal_snapshots_v14 (
                signal_snapshot_id TEXT PRIMARY KEY,
                data_version TEXT,
                product_snapshot_id TEXT,
                previous_snapshot_id TEXT,
                signal_count INTEGER DEFAULT 0,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "product_signal_snapshots_v14",
            {
                "data_version": "TEXT",
                "product_snapshot_id": "TEXT",
                "previous_snapshot_id": "TEXT",
                "signal_count": "INTEGER DEFAULT 0",
                "updated_at": "TEXT",
                "evidence_input_hash": "TEXT",
                "history_epoch_id": "TEXT",
                "current_snapshot_hash": "TEXT",
                "previous_snapshot_hashes": "TEXT",
                "cache_mode": "TEXT",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_signal_snapshot_v14_version "
            "ON product_signal_snapshots_v14(data_version, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_signal_snapshot_v14_evidence_hash "
            "ON product_signal_snapshots_v14(evidence_input_hash, data_version)"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {OBSERVATION_TABLE} (
                snapshot_id TEXT PRIMARY KEY,
                data_version TEXT,
                set_snapshot_hash TEXT,
                observation_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_competition_evidence_observation_version "
            f"ON {OBSERVATION_TABLE}(data_version, updated_at)"
        )
        conn.commit()


def signal_snapshot_id_for(data_version: str | None) -> str:
    return f"PRODUCT-SIGNAL-SNAPSHOT-{data_version or 'latest'}"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> float | None:
    if value in {None, "", "—", "未识别"}:
        return None
    try:
        return float(str(value).replace("¥", "").replace(",", "").replace("%", "").strip())
    except Exception:
        return None


def _change(old: Any, new: Any) -> float | None:
    old_num = _num(old)
    new_num = _num(new)
    if old_num is None or new_num is None or abs(old_num) < ZERO_CHANGE_EPSILON:
        if old_num == 0 and new_num not in {None, 0}:
            return 1.0 if float(new_num) > 0 else -1.0
        return None
    return (new_num - old_num) / abs(old_num)


def _hash(seed: str, size: int = 14) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:size].upper()


def _signal_id(seed: str) -> str:
    return "PSIG-" + _hash(seed)


def _bundle_id(seed: str) -> str:
    return "FPB-" + _hash(seed, 16)


def _index_products(snapshot: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    products = (snapshot or {}).get("products") or []
    return {
        str(item.get("objectId") or f"{item.get('storeId') or 'GLOBAL'}::{item.get('productId')}:{item.get('skuId') or 'NO-SKU'}"): item
        for item in products
        if isinstance(item, dict) and (item.get("productId") or item.get("objectId"))
    }


def _metric(item: Dict[str, Any] | None, field: str) -> Any:
    if not item:
        return None
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    keys = METRIC_ALIASES.get(field, [field])
    if field == "roas" and metric.get("roas") in {None, "", "—", "未识别"}:
        return metric.get("roi") or item.get("roi")
    for key in keys:
        if key in metric and metric.get(key) not in {None, "", "—", "未识别"}:
            return metric.get(key)
        if key in item and item.get(key) not in {None, "", "—", "未识别"}:
            return item.get(key)
    return None


def _product_dates(snapshot: Dict[str, Any] | None) -> Set[str]:
    dates: Set[str] = set()
    for item in (snapshot or {}).get("products") or []:
        metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
        profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
        for value in [
            item.get("metricDate"), item.get("reportDate"), item.get("dataDate"),
            metric.get("metricDate"), metric.get("reportDate"), metric.get("dataDate"),
            profile.get("metricDate"), profile.get("reportDate"), profile.get("dataDate"),
        ]:
            if value not in {None, "", "—", "未识别"}:
                dates.add(str(value))
    return dates


def _changed_metric_count(current: Dict[str, Any], candidate: Dict[str, Any]) -> int:
    current_products = _index_products(current)
    old_products = _index_products(candidate)
    count = 0
    for key, item in current_products.items():
        old = old_products.get(key)
        if not old:
            continue
        for field in COMPARE_FIELDS:
            latest = _num(_metric(item, field))
            previous = _num(_metric(old, field))
            if latest is not None and previous is not None and abs(latest - previous) > ZERO_CHANGE_EPSILON:
                count += 1
    return count


def _is_comparable_previous(current: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    current_id = current.get("snapshotId")
    candidate_id = candidate.get("snapshotId")
    if not candidate or candidate_id == current_id or candidate.get("dataVersion") == current.get("dataVersion"):
        return {"comparable": False, "reason": "same_snapshot_or_data_version"}
    current_products = _index_products(current)
    candidate_products = _index_products(candidate)
    shared = set(current_products).intersection(candidate_products)
    if not shared:
        return {"comparable": False, "reason": "no_shared_products"}
    current_dates = _product_dates(current)
    candidate_dates = _product_dates(candidate)
    changed_count = _changed_metric_count(current, candidate)
    different_business_date = bool(current_dates and candidate_dates and current_dates != candidate_dates)
    comparable = bool(different_business_date or changed_count > 0)
    return {
        "comparable": comparable,
        "reason": "different_business_report" if comparable else "same_business_report_or_no_metric_delta",
        "sharedProductCount": len(shared),
        "changedMetricCount": changed_count,
        "currentDates": sorted(current_dates),
        "candidateDates": sorted(candidate_dates),
        "candidateSnapshotId": candidate_id,
        "candidateDataVersion": candidate.get("dataVersion"),
    }


def _history_values(history: List[Dict[str, Any]], key: str, field: str, limit: int) -> List[float]:
    values: List[float] = []
    for snapshot in history[:limit]:
        item = _index_products(snapshot).get(key)
        value = _num(_metric(item, field))
        if value is not None:
            values.append(value)
    return values


def _trend_windows(key: str, item: Dict[str, Any], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    trend: Dict[str, Any] = {"historyWindowCount": len(history), "windows": {}}
    for field in COMPARE_FIELDS:
        latest = _num(_metric(item, field))
        metric_windows: Dict[str, Any] = {"latest": latest}
        for name, size in WINDOWS.items():
            values = _history_values(history, key, field, size)
            avg_value = mean(values) if values else None
            metric_windows[name] = {
                "avg": avg_value,
                "count": len(values),
                "changeVsAvg": _change(avg_value, latest) if avg_value is not None else None,
            }
        trend["windows"][field] = metric_windows
    return trend


def _strength(field: str, latest: Any, previous: Any, trend_windows: Dict[str, Any]) -> str:
    latest_num = _num(latest)
    previous_num = _num(previous)
    if previous_num is None:
        return "normal"
    previous_change = _change(previous, latest)
    if previous_change is None or abs(previous_change) < ZERO_CHANGE_EPSILON:
        return "normal"
    if field == "inventory" and latest_num is not None and latest_num <= 0:
        return "high"
    if field == "refundRate" and latest_num is not None and latest_num >= 12 and previous_change > 0:
        return "high"
    windows = ((trend_windows.get("windows") or {}).get(field) or {})
    changes = [previous_change]
    for value in windows.values():
        if isinstance(value, dict):
            changes.append(value.get("changeVsAvg"))
    if any(value is not None and abs(float(value)) >= 0.25 for value in changes):
        return "medium"
    if any(value is not None and abs(float(value)) >= 0.08 for value in changes):
        return "low"
    return "normal"


def _field_signal(field: str, item: Dict[str, Any], old: Dict[str, Any] | None, trend_windows: Dict[str, Any]) -> Dict[str, Any]:
    latest = _metric(item, field)
    previous = _metric(old, field) if old else None
    change = _change(previous, latest)
    strength = _strength(field, latest, previous, trend_windows)
    windows = ((trend_windows.get("windows") or {}).get(field) or {})
    return {
        "metricCode": field,
        "signalType": COMPARE_FIELDS[field],
        "signalStrength": strength,
        "latest": _num(latest),
        "previous": _num(previous),
        "changeVsPrevious": change,
        "changeRate": change,
        "windows": windows,
        "meaningfulChange": bool(change is not None and abs(float(change)) > ZERO_CHANGE_EPSILON),
    }


def _source_versions(item: Dict[str, Any], history_items: List[Dict[str, Any]]) -> List[str]:
    versions: List[str] = []
    for candidate in [item, *history_items]:
        metric = candidate.get("metricSnapshot") if isinstance(candidate.get("metricSnapshot"), dict) else {}
        for value in [*(candidate.get("sourceDataVersions") or []), *(metric.get("sourceDataVersions") or [])]:
            if value:
                versions.append(str(value))
    return list(dict.fromkeys(versions))


def _cross_validation(item: Dict[str, Any], history_items: List[Dict[str, Any]], field_signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    source_versions = _source_versions(item, history_items)
    source_datasets = list(dict.fromkeys(str(value) for value in [*(item.get("sourceDatasets") or []), *(metric.get("sourceDatasets") or [])] if value))
    abnormal = [sig for sig in field_signals if sig.get("signalStrength") in {"high", "medium"} and sig.get("meaningfulChange")]
    changed = [sig for sig in field_signals if sig.get("meaningfulChange")]
    return {
        "sourceDataVersions": source_versions,
        "sourceDatasets": source_datasets,
        "sourceVersionCount": len(source_versions),
        "sourceDatasetCount": len(source_datasets),
        "changedMetricCount": len(changed),
        "abnormalMetricCount": len(abnormal),
        "topAbnormalMetrics": abnormal[:5],
        "rule": "V18.7 competition evidence uses bounded immutable observations; zero-change metrics are not task triggers.",
    }


def _primary_signal(field_signals: List[Dict[str, Any]], old: Dict[str, Any] | None) -> tuple[str, str, str | None]:
    if not old:
        return "product_baseline", "normal", None
    best = ("normal_state", "normal", None)
    rank = {"high": 3, "medium": 2, "low": 1, "normal": 0}
    for signal in field_signals:
        if not signal.get("meaningfulChange"):
            continue
        strength = str(signal.get("signalStrength") or "normal")
        if rank.get(strength, 0) > rank.get(best[1], 0):
            best = (str(signal.get("signalType")), strength, str(signal.get("metricCode")))
    return best


def _build_full_product_bundle(data_version: str | None, key: str, item: Dict[str, Any], old: Dict[str, Any] | None, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    history_items = [candidate for snapshot in history for k, candidate in _index_products(snapshot).items() if k == key]
    trend = _trend_windows(key, item, history)
    field_signals = [_field_signal(field, item, old, trend) for field in COMPARE_FIELDS]
    signal_type, strength, metric_code = _primary_signal(field_signals, old)
    profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    cross_validation = _cross_validation(item, history_items, field_signals)
    source_versions = cross_validation.get("sourceDataVersions") or []
    digest_seed = dumps({"key": key, "metric": metric, "sources": source_versions, "fieldSignals": [sig for sig in field_signals if sig.get("meaningfulChange")][:5]})
    bundle_seed = f"full_product_bundle|{key}|{_hash(digest_seed, 16)}"
    bundle_id = _bundle_id(bundle_seed)
    signal_id = _signal_id(bundle_seed)
    evidence_refs = [
        {"type": "metric_signal", "metricCode": sig.get("metricCode"), "signalStrength": sig.get("signalStrength"), "signalType": sig.get("signalType")}
        for sig in field_signals if sig.get("meaningfulChange")
    ]
    return {
        "signalId": signal_id,
        "packageId": bundle_id.replace("FPB-", "PKG-"),
        "bundleId": bundle_id,
        "version": PRODUCT_SIGNAL_SNAPSHOT_VERSION,
        "dataVersion": data_version,
        "entityType": "product",
        "entityId": item.get("objectId") or key,
        "productId": item.get("productId"),
        "storeId": item.get("storeId"),
        "platform": profile.get("platform") or item.get("platform"),
        "verticalCategory": profile.get("verticalCategory") or item.get("verticalCategory") or "未归类",
        "signalType": "full_product_bundle",
        "primarySignalType": signal_type,
        "signalStrength": strength,
        "metricCode": metric_code or "all_metrics",
        "profileLayer": profile,
        "metricLayer": metric,
        "snapshotLayer": {"trendWindows": trend, "fieldSignals": field_signals, "previousProductMetricSnapshot": old.get("metricSnapshot") if isinstance(old, dict) else None},
        "crossValidation": cross_validation,
        "evidenceRefs": evidence_refs,
        "productProfileSnapshot": profile,
        "productMetricSnapshot": metric,
        "trendWindows": trend,
        "previousProductMetricSnapshot": old.get("metricSnapshot") if isinstance(old, dict) else None,
        "agentProductSnapshotPackage": {
            "contract": "fullProductBundle",
            "bundleId": bundle_id,
            "profileLayer": profile,
            "metricLayer": metric,
            "snapshotLayer": {"trendWindows": trend, "fieldSignals": field_signals},
            "crossValidation": cross_validation,
            "signalSummary": {"signalType": "full_product_bundle", "primarySignalType": signal_type, "signalStrength": strength, "metricCode": metric_code or "all_metrics"},
            "ragRequest": {"verticalCategory": profile.get("verticalCategory") or "未归类", "platform": profile.get("platform"), "taskValueLayer": "baseline_safe_delta_routing"},
        },
        "status": "pending_agent_judgment",
        "rule": "V18.7 fullProductBundle is derived only from immutable canonical current/history observations.",
    }


def _build_signal_packages(current: Dict[str, Any], history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    data_version = current.get("dataVersion")
    current_products = _index_products(current)
    previous_products = _index_products(history[0] if history else None)
    packages: List[Dict[str, Any]] = []
    for key, item in current_products.items():
        packages.append(_build_full_product_bundle(data_version, key, item, previous_products.get(key), history))
    for key, old in previous_products.items():
        if key in current_products:
            continue
        profile = old.get("profileSnapshot") if isinstance(old.get("profileSnapshot"), dict) else {}
        bundle_seed = f"full_product_bundle|missing|{key}|{data_version or 'latest'}"
        signal_id = _signal_id(bundle_seed)
        bundle_id = _bundle_id(bundle_seed)
        packages.append({
            "signalId": signal_id, "packageId": bundle_id.replace("FPB-", "PKG-"), "bundleId": bundle_id,
            "version": PRODUCT_SIGNAL_SNAPSHOT_VERSION, "dataVersion": data_version, "entityType": "product",
            "entityId": old.get("objectId") or key, "productId": old.get("productId"), "storeId": old.get("storeId"),
            "platform": profile.get("platform"), "verticalCategory": profile.get("verticalCategory") or "未归类",
            "signalType": "full_product_bundle", "primarySignalType": "product_missing_from_latest", "signalStrength": "medium",
            "metricCode": "product_presence", "profileLayer": profile, "metricLayer": {},
            "snapshotLayer": {"trendWindows": {"historyWindowCount": len(history), "windows": {}}},
            "crossValidation": {"sourceDataVersions": [], "sourceDatasetCount": 0, "changedMetricCount": 1},
            "evidenceRefs": [{"type": "product_presence", "signalType": "product_missing_from_latest"}],
            "productProfileSnapshot": profile, "productMetricSnapshot": None,
            "previousProductMetricSnapshot": old.get("metricSnapshot"), "trendWindows": {"historyWindowCount": len(history), "windows": {}},
            "agentProductSnapshotPackage": {"contract": "fullProductBundle", "bundleId": bundle_id, "profileLayer": profile, "metricLayer": {}, "snapshotLayer": {}, "crossValidation": {}, "signalSummary": {"signalType": "full_product_bundle", "primarySignalType": "product_missing_from_latest", "signalStrength": "medium"}},
            "status": "pending_agent_judgment",
            "rule": "V18.7 missing product is one bundle-level evidence item, not a metric task trigger.",
        })
    return packages


def _compact_product(item: Dict[str, Any]) -> Dict[str, Any]:
    profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    return {
        "objectId": item.get("objectId") or profile.get("objectId"),
        "productId": item.get("productId") or profile.get("productId"),
        "storeId": item.get("storeId") or profile.get("storeId"),
        "skuId": item.get("skuId") or profile.get("skuId"),
        "productSnapshotHash": item.get("productSnapshotHash") or item.get("snapshotHash"),
        "profileSnapshot": {
            key: profile.get(key)
            for key in ("objectId", "productId", "storeId", "storeName", "skuId", "title", "platform", "verticalCategory", "productRole", "lifecycleStage", "metricDate", "reportDate", "dataDate")
            if key in profile
        },
        "metricSnapshot": {
            key: metric.get(key)
            for key in set(COMPARE_FIELDS).union({"metricDate", "reportDate", "dataDate", "sourceDataVersions", "sourceDatasets"})
            if key in metric
        },
        "sourceDataVersions": list(item.get("sourceDataVersions") or metric.get("sourceDataVersions") or []),
        "sourceDatasets": list(item.get("sourceDatasets") or metric.get("sourceDatasets") or []),
    }


def _compact_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    products = [_compact_product(item) for item in snapshot.get("products") or [] if isinstance(item, dict)]
    products.sort(key=lambda item: (_text(item.get("storeId")), _text(item.get("productId")), _text(item.get("objectId"))))
    return {
        "snapshotId": snapshot.get("snapshotId"),
        "dataVersion": snapshot.get("dataVersion"),
        "setSnapshotHash": snapshot.get("setSnapshotHash") or snapshot.get("snapshotHash"),
        "productCount": len(products),
        "products": products,
    }


def _store_observation(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    compact = _compact_snapshot(snapshot)
    observation_hash = _stable_sha256({
        "contract": "competition.evidenceObservation.v1",
        "snapshotId": compact.get("snapshotId"),
        "dataVersion": compact.get("dataVersion"),
        "setSnapshotHash": compact.get("setSnapshotHash"),
        "products": compact.get("products"),
    })
    compact["observationHash"] = observation_hash
    now = now_iso()
    with connect() as conn:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {OBSERVATION_TABLE}
            (snapshot_id,data_version,set_snapshot_hash,observation_hash,payload,created_at,updated_at)
            VALUES (?,?,?,?,?,COALESCE((SELECT created_at FROM {OBSERVATION_TABLE} WHERE snapshot_id=?),?),?)
            """,
            (compact.get("snapshotId"), compact.get("dataVersion"), compact.get("setSnapshotHash"), observation_hash, dumps(compact), compact.get("snapshotId"), now, now),
        )
        conn.commit()
    return compact


def _cached_observation(snapshot_id: str | None, set_snapshot_hash: str | None) -> Dict[str, Any] | None:
    if not snapshot_id:
        return None
    with connect() as conn:
        row = conn.execute(f"SELECT payload,set_snapshot_hash FROM {OBSERVATION_TABLE} WHERE snapshot_id=? LIMIT 1", (snapshot_id,)).fetchone()
    if not row or (set_snapshot_hash and str(row["set_snapshot_hash"] or "") != str(set_snapshot_hash)):
        return None
    value = loads(row["payload"])
    return value if isinstance(value, dict) else None


def _load_compact_observation(snapshot_id: str, set_snapshot_hash: str | None) -> Dict[str, Any] | None:
    cached = _cached_observation(snapshot_id, set_snapshot_hash)
    if cached:
        return cached
    with connect() as conn:
        row = conn.execute("SELECT payload FROM canonical_product_snapshot_sets_v1 WHERE snapshot_id=? LIMIT 1", (snapshot_id,)).fetchone()
    if not row:
        return None
    payload = loads(row["payload"])
    if not isinstance(payload, dict):
        return None
    compact = _store_observation(payload)
    del payload
    gc.collect()
    return compact


def _active_import_order() -> Dict[str, int]:
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='imported_report_rows' LIMIT 1").fetchone()
        if not exists:
            return {}
        rows = conn.execute(
            """
            SELECT data_version,MIN(rowid) AS first_rowid
            FROM imported_report_rows
            WHERE data_version IS NOT NULL AND TRIM(data_version)!=''
            GROUP BY data_version
            ORDER BY first_rowid ASC
            """
        ).fetchall()
    return {str(row["data_version"]): int(row["first_rowid"]) for row in rows if row["data_version"] and row["first_rowid"] is not None}


def _history_for_evidence(current: Dict[str, Any], data_version: str | None, epoch: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]]:
    order = _active_import_order()
    current_order = order.get(str(data_version or ""))
    if current_order is None:
        return [], {"baselineNoPrevious": True, "previousComparableCount": 0, "historyCandidateCount": 0, "diagnostics": [], "reason": "当前 dataVersion 不在活动导入序列，fail-closed 为基线。"}, []
    earlier_versions = [version for version, first_rowid in sorted(order.items(), key=lambda item: item[1], reverse=True) if first_rowid < current_order][:MAX_COMPETITION_HISTORY_CANDIDATES]
    epoch_started_at = str(epoch.get("startedAt") or "")
    comparable: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    identities: List[Dict[str, Any]] = []
    for version in earlier_versions:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT snapshot_id,data_version,set_snapshot_hash,created_at,updated_at
                FROM canonical_product_snapshot_sets_v1
                WHERE data_version=? AND (?='' OR julianday(created_at)>=julianday(?))
                ORDER BY julianday(created_at) DESC,rowid DESC LIMIT 1
                """,
                (version, epoch_started_at, epoch_started_at),
            ).fetchone()
        if not row:
            diagnostics.append({"candidateDataVersion": version, "comparable": False, "reason": "canonical_snapshot_outside_current_epoch_or_missing"})
            continue
        candidate = _load_compact_observation(str(row["snapshot_id"]), str(row["set_snapshot_hash"] or "") or None)
        if not candidate:
            diagnostics.append({"candidateDataVersion": version, "comparable": False, "reason": "compact_observation_missing"})
            continue
        check = _is_comparable_previous(current, candidate)
        diagnostics.append(check)
        if check.get("comparable"):
            comparable.append(candidate)
            identities.append({"snapshotId": candidate.get("snapshotId"), "dataVersion": candidate.get("dataVersion"), "setSnapshotHash": candidate.get("setSnapshotHash"), "observationHash": candidate.get("observationHash")})
            if len(comparable) >= MAX_COMPETITION_COMPARABLE_HISTORY:
                break
    baseline = {
        "baselineNoPrevious": not comparable,
        "previousComparableCount": len(comparable),
        "historyCandidateCount": len(earlier_versions),
        "diagnostics": diagnostics[:10],
        "reason": "首份报表或当前评测轮次没有上一份可比业务报表，只建立商品与指标基线。" if not comparable else "当前评测轮次存在上一份可比业务报表，可以计算动态变化。",
    }
    return comparable, baseline, identities


def _evidence_identity(current_observation: Dict[str, Any], previous: List[Dict[str, Any]], epoch: Dict[str, Any]) -> Dict[str, Any]:
    seed = {
        "contract": EVIDENCE_INPUT_CONTRACT,
        "evidenceContract": EVIDENCE_CONTRACT,
        "evidenceContractVersion": EVIDENCE_CONTRACT_VERSION,
        "snapshotBuilderVersion": PRODUCT_SIGNAL_SNAPSHOT_VERSION,
        "historyEpochId": epoch.get("epochId"),
        "current": {"snapshotId": current_observation.get("snapshotId"), "dataVersion": current_observation.get("dataVersion"), "setSnapshotHash": current_observation.get("setSnapshotHash"), "observationHash": current_observation.get("observationHash")},
        "previous": previous,
        "maxComparableHistory": MAX_COMPETITION_COMPARABLE_HISTORY,
    }
    return {
        "evidenceInputHash": _stable_sha256(seed),
        "historyEpochId": epoch.get("epochId"),
        "historyEpochStartedAt": epoch.get("startedAt"),
        "currentProductSetHash": current_observation.get("setSnapshotHash"),
        "currentObservationHash": current_observation.get("observationHash"),
        "previousProductSetHashes": [item.get("setSnapshotHash") for item in previous if item.get("setSnapshotHash")],
        "previousObservationHashes": [item.get("observationHash") for item in previous if item.get("observationHash")],
        "evidenceContract": EVIDENCE_CONTRACT,
        "evidenceVersion": EVIDENCE_CONTRACT_VERSION,
        "evidenceCacheMode": EVIDENCE_CACHE_MODE,
        "historyScanMode": "epoch_active_import_metadata_then_compact_observation_cache",
        "wholeSnapshotRetention": False,
        "maxComparableHistory": MAX_COMPETITION_COMPARABLE_HISTORY,
    }


def row_to_signal_snapshot(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    return {**payload, "signalSnapshotId": row["signal_snapshot_id"], "dataVersion": row["data_version"], "productSnapshotId": row["product_snapshot_id"], "previousSnapshotId": row["previous_snapshot_id"], "signalCount": int(row["signal_count"] or 0), "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def get_product_signal_snapshot(data_version: str | None = None) -> Dict[str, Any] | None:
    ensure_product_signal_tables()
    with connect() as conn:
        if data_version:
            row = conn.execute("SELECT * FROM product_signal_snapshots_v14 WHERE data_version=? ORDER BY created_at DESC LIMIT 1", (data_version,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM product_signal_snapshots_v14 ORDER BY created_at DESC LIMIT 1").fetchone()
    return row_to_signal_snapshot(row) if row else None


def materialize_product_signal_snapshot(data_version: str | None = None, *, user_id: str | None = None, force: bool = True) -> Dict[str, Any]:
    del force
    ensure_product_signal_tables()
    current = get_product_snapshot(data_version)
    if not current:
        materialize_system_product_snapshot(data_version=data_version, user_id=user_id, force=False)
        current = get_product_snapshot(data_version) or {}
    if not current or not current.get("snapshotId"):
        raise RuntimeError(f"canonical_product_snapshot_missing_before_evidence:{data_version or 'latest'}")

    epoch = current_competition_history_epoch()
    current_observation = _store_observation(current)
    history, baseline, previous_identities = _history_for_evidence(current, data_version, epoch)
    evidence_identity = _evidence_identity(current_observation, previous_identities, epoch)
    existing = get_product_signal_snapshot(data_version)
    if existing and existing.get("evidenceInputHash") == evidence_identity["evidenceInputHash"]:
        existing_id = existing.get("signalSnapshotId") or signal_snapshot_id_for(data_version)
        return {**existing, "idempotentHit": True, "outputRef": f"product_signal_snapshot:{existing_id}", "productSignalSnapshotRef": f"product_signal_snapshot:{existing_id}"}

    previous = history[0] if history else None
    packages = _build_signal_packages(current, history)
    for package in packages:
        package.update(evidence_identity)
        package["evidenceInputContract"] = EVIDENCE_INPUT_CONTRACT
        agent_package = package.get("agentProductSnapshotPackage") if isinstance(package.get("agentProductSnapshotPackage"), dict) else {}
        agent_package.update({"evidenceInputHash": evidence_identity["evidenceInputHash"], "historyEpochId": evidence_identity["historyEpochId"], "evidenceContract": EVIDENCE_CONTRACT, "evidenceVersion": EVIDENCE_CONTRACT_VERSION})
        package["agentProductSnapshotPackage"] = agent_package

    snapshot_id = signal_snapshot_id_for(data_version)
    payload = {
        "version": PRODUCT_SIGNAL_SNAPSHOT_VERSION,
        "signalSnapshotId": snapshot_id,
        "dataVersion": data_version,
        "stationId": "product_signal_snapshot_station",
        "contract": "fullProductBundle",
        "evidenceInputContract": EVIDENCE_INPUT_CONTRACT,
        **evidence_identity,
        "productSnapshotId": current.get("snapshotId"),
        "previousSnapshotId": previous.get("snapshotId") if previous else None,
        "previousDataVersion": previous.get("dataVersion") if previous else None,
        "baselineNoPrevious": bool(baseline.get("baselineNoPrevious")),
        "baseline": baseline,
        "productSnapshotCount": current.get("productCount") or len(current.get("products") or []),
        "productSignalPackageCount": len(packages),
        "productSignalCount": len(packages),
        "signals": packages,
        "productSignalPackages": packages,
        "windowPolicy": {"historyLimit": MAX_COMPETITION_COMPARABLE_HISTORY, "legacyWindowLabels": WINDOWS, "historyCandidateLimit": MAX_COMPETITION_HISTORY_CANDIDATES, "rule": "Competition runtime never scans 90 complete canonical snapshots; window labels operate on bounded comparable observations."},
        "rule": "V18.7 evidence is hash-precomputed from current-epoch canonical observations and reused by exact evidenceInputHash.",
    }
    now = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO product_signal_snapshots_v14
            (signal_snapshot_id,data_version,product_snapshot_id,previous_snapshot_id,signal_count,payload,created_at,updated_at,evidence_input_hash,history_epoch_id,current_snapshot_hash,previous_snapshot_hashes,cache_mode)
            VALUES (?,?,?,?,?,?,COALESCE((SELECT created_at FROM product_signal_snapshots_v14 WHERE signal_snapshot_id=?),?),?,?,?,?,?,?)
            """,
            (snapshot_id, data_version, payload["productSnapshotId"], payload["previousSnapshotId"], len(packages), dumps(payload), snapshot_id, now, now, evidence_identity["evidenceInputHash"], evidence_identity["historyEpochId"], evidence_identity["currentProductSetHash"], dumps(evidence_identity["previousProductSetHashes"]), EVIDENCE_CACHE_MODE),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM product_signal_snapshots_v14 WHERE signal_snapshot_id=?", (snapshot_id,)).fetchone()
    return {**row_to_signal_snapshot(row), "idempotentHit": False, "outputRef": f"product_signal_snapshot:{snapshot_id}", "productSignalSnapshotRef": f"product_signal_snapshot:{snapshot_id}"}


def product_signal_snapshot_summary(limit: int = 20) -> Dict[str, Any]:
    ensure_product_signal_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM product_signal_snapshots_v14 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    items = [row_to_signal_snapshot(row) for row in rows]
    return {"version": PRODUCT_SIGNAL_SNAPSHOT_VERSION, "snapshotCount": len(items), "latest": items[0] if items else None, "items": items}


__all__ = [
    "PRODUCT_SIGNAL_SNAPSHOT_VERSION",
    "EVIDENCE_INPUT_CONTRACT",
    "EVIDENCE_CONTRACT",
    "EVIDENCE_CONTRACT_VERSION",
    "EVIDENCE_CACHE_MODE",
    "MAX_COMPETITION_COMPARABLE_HISTORY",
    "signal_snapshot_id_for",
    "get_product_signal_snapshot",
    "materialize_product_signal_snapshot",
    "product_signal_snapshot_summary",
]
