"""V17 scene-routed data judgment package service.

V17 keeps the product/data judgment Agent light:
- it judges metric change, linked-metric relation and candidate signal only;
- it does not generate actions, permissions, approval rules or SOP boundaries;
- task mapping Agent receives candidate data judgment packages and owns operation
  action generation with permission/SOP/RAG context.

The package produced here is a scene_data_judgment package, not an action task.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.agent_budget_ledger_service import get_or_create_agent_budget_ledger, read_agent_budget_summary, register_agent_event
from src.services.rag_context_station_service import build_rag_context_snapshot, latest_rag_context
from src.services.signal_pool_service import list_signals, update_signal_status
from src.services.task_generation_run_service import record_task_generation_run
from src.services.task_pool_station_service import enter_task_pool_from_snapshot
from src.services.task_snapshot_station_service import create_task_snapshot

DUAL_AGENT_PIPELINE_VERSION = "17.0"
FORMAL_DECISIONS = {"create_task_snapshot", "manager_review_required"}
SEVERITY_RANK = {"normal": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
MAX_TASKS_PER_RUN = 8
MAX_METRIC_JUDGMENTS_PER_SIGNAL = 8
RELATION_CONFIDENCE_THRESHOLD = 0.55
AGENT1_API_MODE = "scene_judgment_rag_router_data_only"
AGENT1_API_CALLS_PER_BUNDLE = 0
TASK_MAPPING_API_CALLS_PER_RUN = 0
RAG_RETRIEVAL_SCOPE = "judgment_rag_data_version_once"
BLANK_VALUES = {None, "", "—", "未识别", "UNKNOWN", "PRODUCT"}
CORE_METRICS = ["paymentAmount", "roi", "roas", "adSpend", "refundRate", "inventory", "conversionRate", "grossMargin", "clickRate"]
FIELD_SIGNAL_METRICS = [*CORE_METRICS, "organicVisitors", "paidVisitors", "visitorCount", "gmv", "afterSalesRate"]
HIGH_IMPACT_METRICS = {"roi", "roas", "refundRate", "inventory", "conversionRate", "grossMargin", "paymentAmount", "clickRate", "adSpend"}
DOWNSIDE_WHEN_UP = {"refundRate", "afterSalesRate", "adSpend"}
DOWNSIDE_WHEN_DOWN = {"paymentAmount", "gmv", "roi", "roas", "inventory", "conversionRate", "grossMargin", "clickRate", "organicVisitors", "paidVisitors", "visitorCount"}
ENGINEERING_ID_PREFIXES = ("PSIG-", "TS-", "LINK-", "SPU-", "SKU-", "STORE-", "AJ-", "PJP-", "TGD-")
PRODUCT_ID_PATTERN = re.compile(r"^(P\d+|PROD[-_A-Z0-9]+|PRODUCT[-_A-Z0-9]+)$", re.IGNORECASE)

JUDGMENT_SCENE_PROTOCOLS: Dict[str, Dict[str, Any]] = {
    "inventory_traffic_relation": {
        "triggerMetrics": ["inventory"],
        "candidateSignalType": "business_opportunity",
        "strongRelationWeights": {"organicVisitors": 0.18, "paidVisitors": 0.18, "visitorCount": 0.16, "paymentAmount": 0.18, "gmv": 0.18, "conversionRate": 0.12, "roi": 0.10, "roas": 0.10, "clickRate": 0.06},
        "weakRelationWeights": {"refundRate": 0.03, "afterSalesRate": 0.03, "grossMargin": 0.04},
        "description": "库存大幅波动时，优先判断库存与流量、成交、转化和投产的联动关系；售后仅作弱背景。",
    },
    "refund_commitment_relation": {
        "triggerMetrics": ["refundRate", "afterSalesRate"],
        "candidateSignalType": "service_risk",
        "strongRelationWeights": {"adSpend": 0.20, "paidVisitors": 0.18, "roas": 0.16, "clickRate": 0.14, "conversionRate": 0.16, "paymentAmount": 0.14, "gmv": 0.12},
        "weakRelationWeights": {"inventory": 0.04, "organicVisitors": 0.06, "grossMargin": 0.04},
        "description": "售后波动优先判断强推、人群、点击转化和承诺偏差，不把库存作为主判断依据。",
    },
    "revenue_conversion_relation": {
        "triggerMetrics": ["paymentAmount", "gmv"],
        "candidateSignalType": "revenue_change",
        "strongRelationWeights": {"organicVisitors": 0.18, "paidVisitors": 0.18, "visitorCount": 0.14, "conversionRate": 0.18, "clickRate": 0.12, "inventory": 0.12, "roi": 0.10, "roas": 0.10, "adSpend": 0.08},
        "weakRelationWeights": {"refundRate": 0.05, "afterSalesRate": 0.05},
        "description": "支付/GMV变化优先判断流量入口、转化承接、库存承接和投放效率。",
    },
    "ad_efficiency_relation": {
        "triggerMetrics": ["roi", "roas", "adSpend"],
        "candidateSignalType": "efficiency_drop",
        "strongRelationWeights": {"adSpend": 0.22, "paymentAmount": 0.18, "gmv": 0.18, "conversionRate": 0.14, "clickRate": 0.12, "paidVisitors": 0.12, "roi": 0.10, "roas": 0.10},
        "weakRelationWeights": {"inventory": 0.05, "refundRate": 0.06, "afterSalesRate": 0.04},
        "description": "投放效率场景优先判断广告消耗、成交、点击转化和付费流量联动。",
    },
    "conversion_traffic_relation": {
        "triggerMetrics": ["conversionRate", "clickRate", "organicVisitors", "paidVisitors", "visitorCount"],
        "candidateSignalType": "conversion_mismatch",
        "strongRelationWeights": {"organicVisitors": 0.18, "paidVisitors": 0.18, "visitorCount": 0.16, "clickRate": 0.16, "conversionRate": 0.18, "paymentAmount": 0.16, "roi": 0.08, "roas": 0.08},
        "weakRelationWeights": {"refundRate": 0.05, "inventory": 0.06},
        "description": "流量/转化场景优先判断流量入口和承接效率，不用所有指标平均。",
    },
    "generic_metric_relation": {
        "triggerMetrics": [],
        "candidateSignalType": "generic_data_signal",
        "strongRelationWeights": {"paymentAmount": 0.18, "conversionRate": 0.16, "roi": 0.16, "roas": 0.16, "clickRate": 0.12, "inventory": 0.10, "refundRate": 0.08},
        "weakRelationWeights": {},
        "description": "未命中明确场景时只输出数据观察，不直接动作化。",
    },
}

METRIC_TO_SCENE = {
    metric: scene
    for scene, config in JUDGMENT_SCENE_PROTOCOLS.items()
    for metric in config.get("triggerMetrics", [])
}


def now_iso() -> str:
    return datetime.now().isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone())


def _safe_load(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return loads(value)
    except Exception:
        return value


def ensure_dual_agent_tables() -> None:
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_product_judgments_v15 (
                judgment_id TEXT PRIMARY KEY,
                data_version TEXT,
                store_id TEXT,
                product_id TEXT,
                signal_id TEXT,
                metric_code TEXT,
                severity TEXT,
                decision_hint TEXT,
                confidence REAL DEFAULT 0,
                payload TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_judgment_packages_v15 (
                package_id TEXT PRIMARY KEY,
                data_version TEXT,
                store_id TEXT,
                product_id TEXT,
                judgment_count INTEGER DEFAULT 0,
                primary_risk TEXT,
                max_severity TEXT,
                overall_decision TEXT,
                task_candidate_allowed INTEGER DEFAULT 0,
                payload TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_generation_decisions_v15 (
                decision_id TEXT PRIMARY KEY,
                package_id TEXT,
                data_version TEXT,
                store_id TEXT,
                product_id TEXT,
                decision TEXT,
                task_title TEXT,
                priority TEXT,
                payload TEXT,
                created_at TEXT NOT NULL
            )
        """)
        ensure_columns(conn, "agent_product_judgments_v15", {"data_version": "TEXT", "store_id": "TEXT", "product_id": "TEXT", "signal_id": "TEXT", "metric_code": "TEXT", "severity": "TEXT", "decision_hint": "TEXT", "confidence": "REAL DEFAULT 0", "payload": "TEXT", "created_at": "TEXT"})
        ensure_columns(conn, "product_judgment_packages_v15", {"data_version": "TEXT", "store_id": "TEXT", "product_id": "TEXT", "judgment_count": "INTEGER DEFAULT 0", "primary_risk": "TEXT", "max_severity": "TEXT", "overall_decision": "TEXT", "task_candidate_allowed": "INTEGER DEFAULT 0", "payload": "TEXT", "created_at": "TEXT"})
        ensure_columns(conn, "task_generation_decisions_v15", {"package_id": "TEXT", "data_version": "TEXT", "store_id": "TEXT", "product_id": "TEXT", "decision": "TEXT", "task_title": "TEXT", "priority": "TEXT", "payload": "TEXT", "created_at": "TEXT"})
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_product_judgments_v15_product ON agent_product_judgments_v15(data_version, store_id, product_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_product_judgment_packages_v15_product ON product_judgment_packages_v15(data_version, store_id, product_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_generation_decisions_v15_package ON task_generation_decisions_v15(package_id, decision)")
        conn.commit()


def _metric_layer(bundle: Dict[str, Any]) -> Dict[str, Any]:
    value = bundle.get("metricLayer")
    return value if isinstance(value, dict) else {}


def _profile_layer(bundle: Dict[str, Any]) -> Dict[str, Any]:
    value = bundle.get("profileLayer")
    return value if isinstance(value, dict) else {}


def _candidate_text(value: Any) -> str | None:
    if value in BLANK_VALUES:
        return None
    text = str(value).strip()
    if not text or text in BLANK_VALUES:
        return None
    upper = text.upper()
    if upper.startswith(ENGINEERING_ID_PREFIXES):
        return None
    if ":" in text or "|" in text:
        return None
    return text


def _strict_product_id(bundle: Dict[str, Any]) -> str | None:
    profile = _profile_layer(bundle)
    product_obj = bundle.get("product") if isinstance(bundle.get("product"), dict) else {}
    candidates = [bundle.get("productId"), bundle.get("product_id"), bundle.get("productCode"), bundle.get("product_code"), profile.get("productId"), profile.get("product_id"), profile.get("productCode"), profile.get("product_code"), product_obj.get("productId"), product_obj.get("id")]
    for value in candidates:
        text = _candidate_text(value)
        if text and (PRODUCT_ID_PATTERN.match(text) or str(value) == str(bundle.get("productId") or profile.get("productId"))):
            return text
    return None


def _store_id(bundle: Dict[str, Any]) -> str:
    profile = _profile_layer(bundle)
    return str(bundle.get("storeId") or bundle.get("store_id") or profile.get("storeId") or profile.get("store_id") or "GLOBAL")


def _known(value: Any) -> bool:
    return value not in BLANK_VALUES


def _num(value: Any) -> float | None:
    if value in BLANK_VALUES:
        return None
    try:
        return float(str(value).replace("¥", "").replace(",", "").replace("%", "").strip())
    except Exception:
        return None


def _clamp(value: Any, low: float = 0.0, high: float = 0.98) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    return round(max(low, min(high, number)), 4)


def _signal_primary_metric(bundle: Dict[str, Any]) -> str:
    return str(bundle.get("metricCode") or bundle.get("primaryRisk") or "all_metrics")


def _extract_metric_codes(bundle: Dict[str, Any]) -> List[str]:
    metric = _metric_layer(bundle)
    primary = _signal_primary_metric(bundle)
    ordered: List[str] = []
    if primary and primary != "all_metrics":
        ordered.append(primary)
    for key in CORE_METRICS:
        if key in metric and _known(metric.get(key)):
            ordered.append(key)
    for key in CORE_METRICS:
        if key in metric and key not in ordered:
            ordered.append(key)
    if not ordered:
        ordered.append(primary or "all_metrics")
    seen: set[str] = set()
    result: List[str] = []
    for key in ordered:
        if not key or key in seen:
            continue
        seen.add(str(key))
        result.append(str(key))
        if len(result) >= MAX_METRIC_JUDGMENTS_PER_SIGNAL:
            break
    return result


def _field_signals(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = source.get("snapshotLayer") if isinstance(source.get("snapshotLayer"), dict) else {}
    values = snapshot.get("fieldSignals") or source.get("fieldSignals") or []
    return values if isinstance(values, list) else []


def _field_signal_for(source: Dict[str, Any], metric_code: str) -> Dict[str, Any]:
    signals = _field_signals(source)
    if metric_code and metric_code != "all_metrics":
        for item in signals:
            if str(item.get("metricCode")) == metric_code:
                return item
    rank = {"high": 4, "medium": 3, "low": 2, "normal": 1}
    return max(signals, key=lambda item: rank.get(str(item.get("signalStrength") or "normal"), 1), default={})


def _best_trend_record(field_signal: Dict[str, Any]) -> Dict[str, Any]:
    windows = field_signal.get("windows") if isinstance(field_signal.get("windows"), dict) else {}
    best: Dict[str, Any] = {"window": None, "count": 0, "changeVsAvg": None, "avg": None}
    for name, item in windows.items():
        if not isinstance(item, dict):
            continue
        change = item.get("changeVsAvg")
        count = int(item.get("count") or 0)
        if change is None:
            continue
        if best.get("changeVsAvg") is None or abs(float(change)) > abs(float(best.get("changeVsAvg") or 0)):
            best = {"window": name, "count": count, "changeVsAvg": float(change), "avg": item.get("avg")}
    return best


def _trend_band(field_signal: Dict[str, Any], metric_code: str) -> Dict[str, Any]:
    trend = _best_trend_record(field_signal)
    change = trend.get("changeVsAvg")
    count = int(trend.get("count") or 0)
    threshold = 0.08 if metric_code in {"roi", "roas", "conversionRate", "refundRate", "grossMargin", "clickRate"} else 0.15
    if change is None or count < 3:
        return {"insideTrendBand": None, "trendBandDeviation": 0.0, "trendStatus": "insufficient_history", "trendWindow": trend.get("window"), "trendCount": count, "trendChangeVsAvg": change, "trendBandThreshold": threshold}
    deviation = max(0.0, abs(float(change)) - threshold)
    return {"insideTrendBand": deviation <= 0, "trendBandDeviation": round(deviation, 4), "trendStatus": "available", "trendWindow": trend.get("window"), "trendCount": count, "trendChangeVsAvg": round(float(change), 4), "trendBandThreshold": threshold}


def _raw_delta(field_signal: Dict[str, Any]) -> float | None:
    delta = field_signal.get("changeVsPrevious")
    if delta is None:
        delta = field_signal.get("delta")
    try:
        return None if delta is None else float(delta)
    except Exception:
        return None


def _physical_direction(delta: float | None, *, deadband: float = 0.03) -> str:
    if delta is None or abs(delta) < deadband:
        return "stable"
    return "up" if delta > 0 else "down"


def _business_direction(metric_code: str, physical_direction: str, latest: Any = None) -> str:
    latest_num = _num(latest)
    if metric_code == "inventory" and latest_num is not None and latest_num <= 0:
        return "downside"
    if physical_direction == "stable":
        return "flat"
    if metric_code in DOWNSIDE_WHEN_UP:
        return "downside" if physical_direction == "up" else "upside"
    if metric_code in DOWNSIDE_WHEN_DOWN:
        return "downside" if physical_direction == "down" else "upside"
    return "changed"


def _magnitude(delta: float | None) -> str:
    if delta is None:
        return "unknown"
    value = abs(delta)
    if value >= 0.30:
        return "large"
    if value >= 0.12:
        return "medium"
    if value >= 0.03:
        return "small"
    return "flat"


def _metric_gate_profile(item: Dict[str, Any]) -> Dict[str, Any]:
    metric_code = str(item.get("metricCode") or "all_metrics")
    severity = str(item.get("severity") or "normal")
    source = item.get("signal") if isinstance(item.get("signal"), dict) else {}
    field_signal = _field_signal_for(source, metric_code)
    delta_num = _raw_delta(field_signal)
    physical = _physical_direction(delta_num)
    business = _business_direction(metric_code, physical, field_signal.get("latest"))
    band = _trend_band(field_signal, metric_code)
    strength = str(field_signal.get("signalStrength") or (item.get("softScore") or {}).get("strength") or severity or "normal")
    cross = source.get("crossValidation") if isinstance(source.get("crossValidation"), dict) else {}
    source_count = int(cross.get("sourceVersionCount") or cross.get("sourceDatasetCount") or 0)
    base_delta = {"critical": 0.72, "high": 0.68, "medium": 0.50, "low": 0.34, "normal": 0.18}.get(severity, 0.18)
    strength_bonus = {"high": 0.14, "medium": 0.08, "low": 0.03, "normal": 0.0}.get(strength, 0.0)
    delta_conf = base_delta + strength_bonus + (min(0.28, abs(delta_num) * 1.1) if delta_num is not None else 0) + (0.08 if source_count >= 2 and delta_num is not None else 0)
    if metric_code == "all_metrics" and severity == "normal":
        delta_conf = min(delta_conf, 0.28)
    trend_conf = 0.0
    if band.get("trendStatus") == "available":
        trend_conf = 0.38 + min(0.34, abs(float(band.get("trendChangeVsAvg") or 0)) * 1.0) + min(0.18, int(band.get("trendCount") or 0) * 0.03)
    risk_conf = max(_clamp(item.get("confidence")), _clamp(delta_conf), _clamp(trend_conf))
    return {
        "metricCode": metric_code,
        "severity": severity,
        "deltaValue": delta_num,
        "metricPhysicalDirection": physical,
        "deltaDirection": business,
        "triggerMagnitude": _magnitude(delta_num),
        "deltaConfidence": _clamp(delta_conf),
        "trendConfidence": _clamp(trend_conf),
        "riskConfidence": _clamp(risk_conf),
        "insideTrendBand": band.get("insideTrendBand"),
        "trendBandDeviation": float(band.get("trendBandDeviation") or 0),
        "trendStatus": band.get("trendStatus"),
        "trendWindow": band.get("trendWindow"),
        "trendCount": band.get("trendCount"),
        "trendChangeVsAvg": band.get("trendChangeVsAvg"),
    }


def _scene_route_for_metric(metric_code: str) -> str:
    return METRIC_TO_SCENE.get(metric_code) or "generic_metric_relation"


def _metric_relation_status(source: Dict[str, Any], metric_code: str) -> Dict[str, Any]:
    signal = _field_signal_for(source, metric_code)
    delta = _raw_delta(signal)
    physical = _physical_direction(delta)
    band = _trend_band(signal, metric_code)
    trend_change = band.get("trendChangeVsAvg")
    business = _business_direction(metric_code, physical, signal.get("latest"))
    strength = str(signal.get("signalStrength") or "normal")
    base = {"high": 0.78, "medium": 0.64, "low": 0.46, "normal": 0.24}.get(strength, 0.24)
    delta_part = min(0.32, abs(delta or 0) * 1.1) if delta is not None else 0
    trend_part = min(0.16, abs(float(trend_change or 0)) * 0.7) if band.get("trendStatus") == "available" else 0
    return {
        "metricCode": metric_code,
        "latest": signal.get("latest"),
        "previous": signal.get("previous"),
        "deltaValue": delta,
        "physicalDirection": physical,
        "businessDirection": business,
        "magnitude": _magnitude(delta),
        "signalStrength": strength,
        "supportConfidence": _clamp(base + delta_part + trend_part),
        "trendStatus": band.get("trendStatus"),
        "trendChangeVsAvg": trend_change,
    }


def _supports_scene(scene: str, metric_code: str, status: Dict[str, Any], trigger_profile: Dict[str, Any]) -> tuple[bool, bool]:
    physical = status.get("physicalDirection")
    business = status.get("businessDirection")
    trigger_physical = trigger_profile.get("metricPhysicalDirection")
    if scene == "inventory_traffic_relation":
        if trigger_physical != "down":
            return False, False
        if metric_code in {"organicVisitors", "paidVisitors", "visitorCount", "paymentAmount", "gmv", "clickRate"}:
            return physical == "up", physical == "down"
        if metric_code in {"conversionRate", "roi", "roas"}:
            return physical in {"up", "stable"}, business == "downside"
        return False, False
    if scene == "refund_commitment_relation":
        if trigger_physical != "up":
            return False, False
        if metric_code in {"adSpend", "paidVisitors", "paymentAmount", "gmv", "clickRate"}:
            return physical == "up", False
        if metric_code == "conversionRate":
            return physical in {"down", "stable"}, physical == "up"
        if metric_code == "roas":
            return physical in {"up", "stable"}, False
        return False, False
    if scene == "revenue_conversion_relation":
        if trigger_physical == "down":
            if metric_code in {"organicVisitors", "paidVisitors", "visitorCount", "conversionRate", "clickRate", "inventory", "roi", "roas"}:
                return physical == "down" or business == "downside", physical == "up" and metric_code in {"organicVisitors", "paidVisitors", "visitorCount"}
        if trigger_physical == "up":
            if metric_code in {"organicVisitors", "paidVisitors", "visitorCount", "conversionRate", "roi", "roas"}:
                return physical in {"up", "stable"}, business == "downside"
        return False, False
    if scene == "ad_efficiency_relation":
        if metric_code == "adSpend":
            return physical == "up", physical == "down"
        if metric_code in {"paymentAmount", "gmv", "conversionRate", "clickRate"}:
            return physical in {"down", "stable"}, physical == "up"
        if metric_code in {"paidVisitors"}:
            return physical == "up", False
        return False, False
    if scene == "conversion_traffic_relation":
        if metric_code in {"organicVisitors", "paidVisitors", "visitorCount", "clickRate"}:
            return physical != "stable", False
        if metric_code in {"conversionRate", "paymentAmount", "gmv", "roi", "roas"}:
            return physical != "stable" or business == "downside", False
    return status.get("magnitude") in {"medium", "large"}, False


def _scene_relation_judgment(primary_item: Dict[str, Any], trigger_profile: Dict[str, Any]) -> Dict[str, Any]:
    source = primary_item.get("signal") if isinstance(primary_item.get("signal"), dict) else {}
    trigger_metric = str(trigger_profile.get("metricCode") or primary_item.get("metricCode") or "all_metrics")
    scene = _scene_route_for_metric(trigger_metric)
    protocol = JUDGMENT_SCENE_PROTOCOLS.get(scene) or JUDGMENT_SCENE_PROTOCOLS["generic_metric_relation"]
    strong: Dict[str, Any] = {}
    weak: Dict[str, Any] = {}
    conflicts: Dict[str, Any] = {}
    strong_total = sum(float(v) for v in (protocol.get("strongRelationWeights") or {}).values()) or 1.0
    support_score = 0.0
    for metric_code, weight in (protocol.get("strongRelationWeights") or {}).items():
        status = _metric_relation_status(source, metric_code)
        supported, conflict = _supports_scene(scene, metric_code, status, trigger_profile)
        status["relationRole"] = "strong"
        status["relationWeight"] = weight
        status["supportsTrigger"] = supported
        strong[metric_code] = status
        if supported:
            support_score += float(weight) * float(status.get("supportConfidence") or 0)
        if conflict:
            conflicts[metric_code] = {**status, "conflictReason": "strong relation moves against this scene interpretation"}
    for metric_code, weight in (protocol.get("weakRelationWeights") or {}).items():
        status = _metric_relation_status(source, metric_code)
        status["relationRole"] = "weak_background"
        status["relationWeight"] = weight
        weak[metric_code] = status
    linked_conf = support_score / strong_total
    trigger_conf = max(float(trigger_profile.get("deltaConfidence") or 0), float(trigger_profile.get("trendConfidence") or 0), float(trigger_profile.get("riskConfidence") or 0))
    relation_conf = _clamp(trigger_conf * 0.45 + linked_conf * 0.55)
    trigger_physical = trigger_profile.get("metricPhysicalDirection")
    trigger_business = trigger_profile.get("deltaDirection")
    candidate = False
    reason = "未形成足够强的场景关联数据判断，只沉淀为观察。"
    signal_type = protocol.get("candidateSignalType") or "generic_data_signal"
    if scene == "inventory_traffic_relation":
        candidate = trigger_physical == "down" and relation_conf >= RELATION_CONFIDENCE_THRESHOLD and bool([v for v in strong.values() if v.get("supportsTrigger")])
        reason = "库存下降与流量/成交/转化/投产形成联动，构成可进入任务映射的数据判断包。" if candidate else "库存变化未与流量、成交或投产形成强联动，不进入运营动作任务。"
    elif scene == "refund_commitment_relation":
        candidate = trigger_physical == "up" and relation_conf >= RELATION_CONFIDENCE_THRESHOLD
        reason = "售后上升与强推、点击转化或成交联动，构成可进入任务映射的数据判断包。" if candidate else "售后变化缺少强关联指标支撑，先沉淀观察。"
    elif scene == "revenue_conversion_relation":
        candidate = trigger_physical in {"down", "up"} and relation_conf >= RELATION_CONFIDENCE_THRESHOLD and trigger_business != "upside"
        reason = "支付/GMV变化与流量、转化、库存或投放效率联动，构成可进入任务映射的数据判断包。" if candidate else "支付/GMV变化未形成明确动作入口或为正向机会，先观察。"
    elif scene == "ad_efficiency_relation":
        candidate = trigger_business == "downside" and relation_conf >= RELATION_CONFIDENCE_THRESHOLD
        reason = "投放效率指标与广告消耗、成交或转化形成低效联动，构成可进入任务映射的数据判断包。" if candidate else "投放效率变化未形成足够联动证据，先沉淀观察。"
    elif scene == "conversion_traffic_relation":
        candidate = trigger_business == "downside" and relation_conf >= RELATION_CONFIDENCE_THRESHOLD
        reason = "流量/转化承接关系出现负向联动，构成可进入任务映射的数据判断包。" if candidate else "流量/转化变化未形成明确负向承接信号，先观察。"
    return {
        "sceneRoute": scene,
        "sceneProtocolDescription": protocol.get("description"),
        "triggerMetric": trigger_metric,
        "triggerDirection": trigger_business,
        "triggerPhysicalDirection": trigger_physical,
        "triggerMagnitude": trigger_profile.get("triggerMagnitude"),
        "strongLinkedMetricSupport": strong,
        "weakLinkedMetricBackground": weak,
        "conflictMetrics": conflicts,
        "deltaConfidence": trigger_profile.get("deltaConfidence"),
        "trendConfidence": trigger_profile.get("trendConfidence"),
        "relationConfidence": relation_conf,
        "linkedMetricConfidence": _clamp(linked_conf),
        "candidateSignal": bool(candidate),
        "candidateSignalType": signal_type,
        "candidateReason": reason,
        "dataJudgmentOnly": True,
        "rule": "V17 data judgment package: scene RAG routing identifies linked metrics only; permissions/actions/SOP are task-mapping responsibilities.",
    }


def _score_signal(bundle: Dict[str, Any]) -> Dict[str, Any]:
    strength = str(bundle.get("signalStrength") or "normal")
    cross = bundle.get("crossValidation") if isinstance(bundle.get("crossValidation"), dict) else {}
    abnormal = int(cross.get("abnormalMetricCount") or 0)
    changed = int(cross.get("changedMetricCount") or 0)
    source_count = int(cross.get("sourceVersionCount") or cross.get("sourceDatasetCount") or 0)
    metric = _metric_layer(bundle)
    missing = [key for key in CORE_METRICS if key in metric and not _known(metric.get(key))]
    base = {"high": 0.76, "medium": 0.56, "low": 0.32, "normal": 0.18}.get(strength, 0.18)
    score = base + min(0.16, abnormal * 0.05) + min(0.08, changed * 0.02) + min(0.08, source_count * 0.015)
    critical_gap = any(key in missing for key in ["paymentAmount", "inventory", "refundRate"]) or {"roi", "roas"}.issubset(set(missing))
    return {"strength": strength, "score": round(max(0.35, min(0.92, score)), 4), "abnormal": abnormal, "changed": changed, "sourceCount": source_count, "missingFields": missing, "criticalGap": critical_gap}


def _score_metric(bundle: Dict[str, Any], metric_code: str, signal_score: Dict[str, Any]) -> Dict[str, Any]:
    metric = _metric_layer(bundle)
    primary = _signal_primary_metric(bundle)
    strength = str(signal_score.get("strength") or "normal")
    base_score = float(signal_score.get("score") or 0.45)
    value = metric.get(metric_code)
    is_primary = metric_code == primary or primary == "all_metrics"
    is_high_impact = metric_code in HIGH_IMPACT_METRICS
    missing = not _known(value) and metric_code in CORE_METRICS
    if missing and metric_code in {"paymentAmount", "inventory", "refundRate", "roi", "roas"}:
        severity, hint, score = "medium", "data_gap_candidate", max(base_score, 0.62)
    elif is_primary and strength == "high":
        severity, hint, score = "high", "risk_candidate", max(base_score, 0.82)
    elif is_primary and strength == "medium":
        severity, hint, score = "medium", "risk_candidate", max(base_score, 0.66)
    elif is_high_impact and strength == "high":
        severity, hint, score = "medium", "related_risk", max(base_score - 0.08, 0.68)
    elif is_high_impact and (strength == "medium" or int(signal_score.get("changed") or 0) > 0):
        severity, hint, score = "low", "related_observation", max(base_score - 0.12, 0.52)
    elif _known(value):
        severity, hint, score = ("low" if int(signal_score.get("changed") or 0) > 0 else "normal"), "metric_observation", max(base_score - 0.18, 0.42)
    else:
        severity, hint, score = "normal", "metric_observation", max(base_score - 0.2, 0.35)
    return {"metricCode": metric_code, "severity": severity, "decisionHint": hint, "confidence": round(max(0.35, min(0.92, score)), 4), "metricValue": value, "isPrimaryMetric": is_primary, "isHighImpactMetric": is_high_impact, "missing": missing, **signal_score}


def _agent1_analyze_signal(signal: Dict[str, Any], rag_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    store_id = _store_id(signal)
    product_id = _strict_product_id(signal)
    product_key = product_id or "UNRESOLVED_PRODUCT"
    signal_score = _score_signal(signal)
    if not product_id:
        scored = {**signal_score, "metricCode": _signal_primary_metric(signal), "severity": "low", "decisionHint": "identity_gap", "confidence": 0.45, "metricValue": None, "missing": True}
        return [{"version": DUAL_AGENT_PIPELINE_VERSION, "judgmentId": make_id("APJ"), "dataVersion": signal.get("dataVersion"), "storeId": store_id, "productId": product_key, "productIdentityResolved": False, "signalId": signal.get("signalId"), "bundleId": signal.get("bundleId"), "metricCode": scored["metricCode"], "severity": "low", "decisionHint": "identity_gap", "confidence": scored["confidence"], "finding": f"{product_key} 缺少真实商品ID，不能进入商品判断包整合。", "evidence": {"missingProductId": True, "sourceSignalId": signal.get("signalId"), "sourceBundleId": signal.get("bundleId")}, "signal": signal, "softScore": scored, "metricGranularity": "identity_gap", "agent1ApiCallCount": 0, "ragRetrievalScope": RAG_RETRIEVAL_SCOPE, "rule": "Agent1 identity gap stays in judgment layer and never enters task mapping."}]
    judgments: List[Dict[str, Any]] = []
    for metric_code in _extract_metric_codes(signal):
        scored = _score_metric(signal, metric_code, signal_score)
        severity = str(scored.get("severity") or "normal")
        if severity not in SEVERITY_RANK:
            severity = "normal"
        value = scored.get("metricValue")
        value_text = "未识别" if value in BLANK_VALUES else value
        finding = f"{product_key} 的 {metric_code} 指标判断为 {severity}；当前值 {value_text}。"
        if scored.get("isPrimaryMetric"):
            finding = f"{product_key} 主波动指标 {metric_code} 判断为 {severity}；当前值 {value_text}。"
        judgments.append({"version": DUAL_AGENT_PIPELINE_VERSION, "judgmentId": make_id("APJ"), "dataVersion": signal.get("dataVersion"), "storeId": store_id, "productId": product_key, "productIdentityResolved": True, "signalId": signal.get("signalId"), "bundleId": signal.get("bundleId"), "metricCode": metric_code, "severity": severity, "decisionHint": scored.get("decisionHint"), "confidence": float(scored.get("confidence") or 0), "finding": finding, "evidence": {"metricValue": value, "isPrimaryMetric": scored.get("isPrimaryMetric"), "isHighImpactMetric": scored.get("isHighImpactMetric"), "signalStrength": scored.get("strength"), "abnormal": scored.get("abnormal"), "changed": scored.get("changed"), "sourceCount": scored.get("sourceCount"), "missingFields": scored.get("missingFields")}, "signal": signal, "softScore": scored, "metricGranularity": "metric_level", "agent1ApiCallCount": 0, "ragRetrievalScope": RAG_RETRIEVAL_SCOPE, "rule": "V17 data analysis Agent outputs metric judgments only; permissions and operation actions are excluded."})
    return judgments


def _clear_version_rows(data_version: str | None) -> None:
    if not data_version:
        return
    with connect() as conn:
        for table in ["agent_product_judgments_v15", "product_judgment_packages_v15", "task_generation_decisions_v15"]:
            if _table_exists(conn, table):
                conn.execute(f"DELETE FROM {table} WHERE data_version = ?", (data_version,))
        conn.commit()


def _save_raw_judgments(judgments: List[Dict[str, Any]]) -> None:
    ensure_dual_agent_tables()
    now = now_iso()
    with connect() as conn:
        for item in judgments:
            conn.execute("""
                INSERT OR REPLACE INTO agent_product_judgments_v15 (judgment_id, data_version, store_id, product_id, signal_id, metric_code, severity, decision_hint, confidence, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item.get("judgmentId"), item.get("dataVersion"), item.get("storeId"), item.get("productId"), item.get("signalId"), item.get("metricCode"), item.get("severity"), item.get("decisionHint"), float(item.get("confidence") or 0), dumps(item), now))
        conn.commit()


def _load_raw_judgments(data_version: str | None) -> List[Dict[str, Any]]:
    ensure_dual_agent_tables()
    with connect() as conn:
        if data_version:
            rows = conn.execute("SELECT payload FROM agent_product_judgments_v15 WHERE data_version = ? ORDER BY created_at", (data_version,)).fetchall()
        else:
            rows = conn.execute("SELECT payload FROM agent_product_judgments_v15 ORDER BY created_at DESC").fetchall()
    return [_safe_load(row["payload"]) for row in rows]


def _severity_max(items: Iterable[Dict[str, Any]]) -> str:
    max_item = "normal"
    for item in items:
        sev = str(item.get("severity") or "normal")
        if SEVERITY_RANK.get(sev, 0) > SEVERITY_RANK.get(max_item, 0):
            max_item = sev
    return max_item


def _fact_envelope(items: List[Dict[str, Any]], store_id: str, product_id: str) -> Dict[str, Any]:
    source = next((item.get("signal") for item in items if isinstance(item.get("signal"), dict)), {}) or {}
    profile = _profile_layer(source)
    facts = {
        "productId": product_id,
        "storeId": store_id,
        "title": profile.get("title") or profile.get("shortName") or source.get("title"),
        "verticalCategory": source.get("verticalCategory") or profile.get("verticalCategory"),
        "platform": source.get("platform") or profile.get("platform"),
        "metricDate": (_metric_layer(source).get("metricDate") or profile.get("metricDate")),
    }
    missing = [key for key in ["productId", "storeId"] if not facts.get(key)]
    return {"factStatus": "passed" if not missing else "missing", "facts": facts, "missing": missing, "confidencePolicy": "fact fields are context and do not enter relation confidence"}


def _select_primary_pair(items: List[Dict[str, Any]]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    pairs = [(item, _metric_gate_profile(item)) for item in items]
    pairs.sort(key=lambda pair: (SEVERITY_RANK.get(str(pair[1].get("severity") or "normal"), 0), float(pair[1].get("riskConfidence") or 0), float(pair[1].get("trendBandDeviation") or 0), 0 if pair[1].get("metricCode") == "all_metrics" else 1), reverse=True)
    if not pairs:
        return {}, {"metricCode": "all_metrics", "riskConfidence": 0.0, "deltaConfidence": 0.0, "trendConfidence": 0.0}
    return pairs[0]


def _package_product_judgments(data_version: str | None) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    raw = _load_raw_judgments(data_version)
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    identity_gaps: List[Dict[str, Any]] = []
    for item in raw:
        if not item.get("productIdentityResolved") or not _candidate_text(item.get("productId")):
            identity_gaps.append(item)
            continue
        grouped[(str(item.get("storeId") or "GLOBAL"), str(item.get("productId")))].append(item)
    packages: List[Dict[str, Any]] = []
    for (store_id, product_id), items in grouped.items():
        max_severity = _severity_max(items)
        metric_profiles = [_metric_gate_profile(item) for item in items]
        primary_item, primary_profile = _select_primary_pair(items)
        relation = _scene_relation_judgment(primary_item, primary_profile)
        risk_items = [item for item in items if SEVERITY_RANK.get(str(item.get("severity") or "normal"), 0) >= 1]
        risk_counts = Counter(str(item.get("metricCode") or "all_metrics") for item in risk_items)
        primary_risk = relation.get("triggerMetric") or (risk_counts.most_common(1)[0][0] if risk_counts else "all_metrics")
        secondary = [risk for risk, _ in risk_counts.most_common(5) if risk != primary_risk]
        relation_confidence = float(relation.get("relationConfidence") or 0)
        candidate_signal = bool(relation.get("candidateSignal"))
        overall = "candidate_signal" if candidate_signal else "observe_only"
        fact = _fact_envelope(items, store_id, product_id)
        evidence_pack = [{"metricCode": item.get("metricCode"), "severity": item.get("severity"), "confidence": item.get("confidence"), "finding": item.get("finding"), "evidence": item.get("evidence"), "metricGateProfile": _metric_gate_profile(item)} for item in sorted(items, key=lambda x: SEVERITY_RANK.get(str(x.get("severity") or "normal"), 0), reverse=True)[:8]]
        scene_package = {
            "judgmentPackageType": "scene_data_judgment",
            "sceneRoute": relation.get("sceneRoute"),
            "triggerMetric": relation.get("triggerMetric"),
            "triggerDirection": relation.get("triggerDirection"),
            "triggerPhysicalDirection": relation.get("triggerPhysicalDirection"),
            "triggerMagnitude": relation.get("triggerMagnitude"),
            "strongLinkedMetricSupport": relation.get("strongLinkedMetricSupport"),
            "weakLinkedMetricBackground": relation.get("weakLinkedMetricBackground"),
            "conflictMetrics": relation.get("conflictMetrics"),
            "deltaConfidence": relation.get("deltaConfidence"),
            "trendConfidence": relation.get("trendConfidence"),
            "relationConfidence": relation_confidence,
            "linkedMetricConfidence": relation.get("linkedMetricConfidence"),
            "candidateSignal": candidate_signal,
            "candidateSignalType": relation.get("candidateSignalType"),
            "candidateReason": relation.get("candidateReason"),
            "dataJudgmentOnly": True,
        }
        packages.append({
            "version": DUAL_AGENT_PIPELINE_VERSION,
            "packageId": make_id("PJP"),
            "dataVersion": data_version or (items[0].get("dataVersion") if items else None),
            "storeId": store_id,
            "productId": product_id,
            "judgmentCount": len(items),
            "primaryRisk": primary_risk,
            "secondaryRisks": secondary,
            "maxSeverity": max_severity,
            "overallDecision": overall,
            "taskCandidateAllowed": candidate_signal,
            "confidence": relation_confidence,
            "packageConfidence": relation_confidence,
            "relationConfidenceThreshold": RELATION_CONFIDENCE_THRESHOLD,
            "factEnvelope": fact,
            "primaryGateProfile": primary_profile,
            "metricGateProfiles": metric_profiles,
            "sceneJudgmentPackage": scene_package,
            "sceneRoute": scene_package.get("sceneRoute"),
            "triggerMetric": scene_package.get("triggerMetric"),
            "candidateSignal": candidate_signal,
            "candidateSignalType": scene_package.get("candidateSignalType"),
            "candidateReason": scene_package.get("candidateReason"),
            "dataJudgmentOnly": True,
            "riskCandidateCount": sum(1 for item in items if item.get("decisionHint") in {"risk_candidate", "related_risk", "data_gap_candidate"}),
            "metricJudgmentCount": len(items),
            "summary": f"{product_id} 生成V17场景数据判断包：scene={scene_package.get('sceneRoute')}，trigger={scene_package.get('triggerMetric')}，relationConfidence={relation_confidence}，candidateSignal={candidate_signal}。",
            "evidencePack": evidence_pack,
            "rawJudgmentIds": [item.get("judgmentId") for item in items],
            "identityStatus": "resolved",
            "rule": "V17: data analysis produces scene data judgment only; permissions/actions/SOP are generated by task mapping Agent.",
        })
    _save_packages(packages)
    return packages, identity_gaps


def _save_packages(packages: List[Dict[str, Any]]) -> None:
    ensure_dual_agent_tables()
    now = now_iso()
    with connect() as conn:
        for item in packages:
            conn.execute("""
                INSERT OR REPLACE INTO product_judgment_packages_v15 (package_id, data_version, store_id, product_id, judgment_count, primary_risk, max_severity, overall_decision, task_candidate_allowed, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item.get("packageId"), item.get("dataVersion"), item.get("storeId"), item.get("productId"), int(item.get("judgmentCount") or 0), item.get("primaryRisk"), item.get("maxSeverity"), item.get("overallDecision"), 1 if item.get("taskCandidateAllowed") else 0, dumps(item), now))
        conn.commit()


def _priority(max_severity: str, relation_confidence: float) -> str:
    if max_severity in {"high", "critical"} or relation_confidence >= 0.82:
        return "高"
    return "中" if relation_confidence >= RELATION_CONFIDENCE_THRESHOLD else "低"


def _fallback_task_plan_for_scene(package: Dict[str, Any], decision: str, priority: str) -> Dict[str, Any]:
    product_id = package.get("productId") or "PRODUCT"
    scene = package.get("sceneRoute") or "generic_metric_relation"
    trigger = package.get("triggerMetric") or package.get("primaryRisk") or "经营指标"
    deadline = "6小时内" if priority == "高" else "24小时内"
    if scene == "inventory_traffic_relation":
        title = f"库存承接动作｜{product_id}｜保留流量并提高备货优先级"
        steps = [f"{deadline}把 {product_id} 标记为库存承接商品，确认当前流量入口继续保留。", "同步补货/备货优先级，避免库存不足导致流量权重流失。", "库存未恢复前，不盲目继续放大付费流量，优先保留已有效入口。"]
        metrics = ["库存", "流量", "支付金额", "转化率", "ROI/ROAS"]
    elif scene == "refund_commitment_relation":
        title = f"售后承接动作｜{product_id}｜调整承诺表达与强推流量"
        steps = [f"{deadline}处理 {product_id} 的售后上升信号，优先调整承诺表达或强推入口。", "根据点击/转化/强推联动，选择更换标题、主图或收缩低质量流量。", "保留原版本作为对照，3天后复盘退款率、转化率和ROAS。"]
        metrics = ["退款率", "点击率", "转化率", "广告消耗", "ROAS"]
    elif scene == "ad_efficiency_relation":
        title = f"投放效率动作｜{product_id}｜收缩低效强推入口"
        steps = [f"{deadline}处理 {product_id} 的投放效率下滑信号，优先收缩低效入口。", "降低未带来同步成交增长的强推消耗，保留高转化入口。", "3天后复盘广告消耗、支付金额、ROI/ROAS和转化率。"]
        metrics = ["广告消耗", "支付金额", "ROI", "ROAS", "转化率"]
    elif scene == "revenue_conversion_relation":
        title = f"成交承接动作｜{product_id}｜恢复成交入口或优化承接"
        steps = [f"{deadline}处理 {product_id} 的成交变化信号，按流量、转化、库存或投放联动确定动作入口。", "若流量下滑，恢复有效入口；若转化下滑，优先调整页面承接或投放人群。", "3天后复盘支付金额、流量、转化率和ROI/ROAS。"]
        metrics = ["支付金额", "流量", "转化率", "库存", "ROI/ROAS"]
    else:
        title = f"经营动作任务｜{product_id}｜{trigger}场景处理"
        steps = [f"{deadline}依据V17数据判断包处理 {product_id} 的 {trigger} 场景。", "只执行低成本、可逆的运营动作，并保留复盘指标。", "3天后复盘主触发指标与强关联指标。"]
        metrics = [trigger, "支付金额", "转化率", "ROI/ROAS"]
    return {
        "title": title,
        "subtitle": "V17任务映射站生成的运营动作",
        "entityType": "product",
        "entityId": product_id,
        "productId": product_id,
        "storeId": package.get("storeId"),
        "taskType": "operation_action",
        "actionType": "task_mapping_agent_operation_action",
        "priority": priority,
        "riskLevel": "high" if priority == "高" else "medium",
        "deadline": deadline,
        "riskDomain": trigger,
        "operationBudget": {"requiresApproval": decision == "manager_review_required", "operatorBudgetApplies": False, "budgetUpperBound": 0},
        "sopSteps": steps,
        "evidenceRequirements": ["V17数据判断包", "执行前后页面/投放/库存截图", "动作版本记录", "复盘指标截图"],
        "reviewMetrics": metrics,
        "needManagerReview": decision == "manager_review_required",
        "reason": package.get("candidateReason") or "V17数据判断包通过任务候选阀门，由任务映射站生成运营动作。",
    }


def _agent2_task_decision(package: Dict[str, Any], rank_index: int) -> Dict[str, Any]:
    product_id = package.get("productId") or "PRODUCT"
    trigger = package.get("triggerMetric") or package.get("primaryRisk") or "经营状态"
    max_sev = package.get("maxSeverity") or "normal"
    relation_confidence = float(package.get("relationConfidence") or package.get("packageConfidence") or package.get("confidence") or 0)
    allowed = bool(package.get("taskCandidateAllowed") or package.get("candidateSignal")) and rank_index < MAX_TASKS_PER_RUN
    if not allowed:
        decision = "no_task"
        reason = package.get("candidateReason") or ("V17数据判断包未通过任务候选阀门，沉淀为观察。" if rank_index < MAX_TASKS_PER_RUN else "本轮任务达到上限，剩余候选沉淀。")
        task_plan = {"title": f"数据观察记录｜{product_id}｜{trigger}", "taskType": "observe_only", "priority": "低", "deadline": "后台观察", "reason": reason, "sopSteps": [], "evidenceRequirements": []}
    else:
        decision = "manager_review_required" if max_sev in {"high", "critical"} else "create_task_snapshot"
        priority = _priority(max_sev, relation_confidence)
        task_plan = _fallback_task_plan_for_scene(package, decision, priority)
    return {"version": DUAL_AGENT_PIPELINE_VERSION, "decisionId": make_id("TGD"), "packageId": package.get("packageId"), "dataVersion": package.get("dataVersion"), "storeId": package.get("storeId"), "productId": product_id, "decision": decision, "taskTitle": task_plan.get("title"), "priority": task_plan.get("priority"), "reason": task_plan.get("reason"), "taskPlan": task_plan, "productJudgmentPackage": package, "rule": "V17 task mapping consumes data judgment packages and generates actions; data judgment package itself contains no permissions or SOP."}


def _save_decision(decision: Dict[str, Any]) -> None:
    ensure_dual_agent_tables()
    with connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO task_generation_decisions_v15 (decision_id, package_id, data_version, store_id, product_id, decision, task_title, priority, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (decision.get("decisionId"), decision.get("packageId"), decision.get("dataVersion"), decision.get("storeId"), decision.get("productId"), decision.get("decision"), decision.get("taskTitle"), decision.get("priority"), dumps(decision), now_iso()))
        conn.commit()


def _existing_product_pool_task(data_version: str | None, store_id: str | None, product_id: str | None) -> Dict[str, Any] | None:
    if not product_id:
        return None
    with connect() as conn:
        if not _table_exists(conn, "task_pool_entries"):
            return None
        rows = conn.execute("SELECT payload FROM task_pool_entries WHERE data_version = ? ORDER BY created_at DESC", (data_version,)).fetchall() if data_version else conn.execute("SELECT payload FROM task_pool_entries ORDER BY created_at DESC").fetchall()
    for row in rows:
        payload = _safe_load(row["payload"])
        task = payload.get("task") if isinstance(payload, dict) else {}
        snapshot = payload.get("snapshot") if isinstance(payload, dict) else {}
        plan = snapshot.get("taskPlan") if isinstance(snapshot, dict) else {}
        task_product = task.get("productId") or plan.get("productId") or snapshot.get("productId")
        task_store = (task.get("storeIds") or [None])[0] if isinstance(task.get("storeIds"), list) else plan.get("storeId") or snapshot.get("storeId")
        if str(task_product) == str(product_id) and (not store_id or not task_store or str(task_store) == str(store_id)):
            return payload
    return None


def _stream_decision_to_task_pool(decision: Dict[str, Any], created_by: str | None = None) -> Dict[str, Any]:
    if decision.get("decision") not in FORMAL_DECISIONS:
        return {"ok": False, "skipped": True, "reason": "decision_not_formal_task", "decisionId": decision.get("decisionId")}
    if _existing_product_pool_task(decision.get("dataVersion"), decision.get("storeId"), decision.get("productId")):
        return {"ok": False, "skipped": True, "reason": "same_product_task_already_in_pool", "decisionId": decision.get("decisionId"), "createdTaskCount": 0}
    plan = decision.get("taskPlan") or {}
    package = decision.get("productJudgmentPackage") or {}
    snapshot = create_task_snapshot({"dataVersion": decision.get("dataVersion"), "decision": decision.get("decision"), "confidence": package.get("relationConfidence") or package.get("packageConfidence") or package.get("confidence") or 0.72, "entityType": "product", "entityId": decision.get("productId"), "productId": decision.get("productId"), "storeId": decision.get("storeId"), "signalRef": decision.get("packageId"), "bundleRef": decision.get("packageId"), "ragContext": {"source": "scene_data_judgment_package", "version": DUAL_AGENT_PIPELINE_VERSION}, "agentJudgment": {"decision": decision.get("decision"), "confidence": package.get("relationConfidence") or package.get("packageConfidence") or package.get("confidence") or 0.72, "reason": decision.get("reason"), "status": "task_generated_from_v17_data_judgment_package"}, "taskPlan": plan, "operationBudget": plan.get("operationBudget") or {}, "evidenceRequirements": plan.get("evidenceRequirements") or [], "systemFacts": {"sceneDataJudgmentPackage": package, "taskGenerationDecision": decision}, "source": "v17_scene_judgment_to_task_mapping"}, created_by=created_by)
    pool = enter_task_pool_from_snapshot(str(snapshot.get("taskSnapshotId")), created_by=created_by, force=False)
    return {"ok": True, "snapshot": snapshot, "poolResult": pool, "createdTaskCount": int((pool or {}).get("createdTaskCount") or 0)}


def _latest_or_build_rag_context(data_version: str | None) -> tuple[Dict[str, Any], int]:
    latest = latest_rag_context(data_version)
    if latest:
        return latest, 0
    return build_rag_context_snapshot(data_version=data_version), 1


def run_dual_agent_product_task_pipeline(data_version: str | None = None, *, rag_context_ref: str | None = None, max_signals: int = 160, created_by: str | None = None) -> Dict[str, Any]:
    ensure_dual_agent_tables()
    ledger = get_or_create_agent_budget_ledger(data_version=data_version, source="v17_scene_judgment_rag_router")
    _clear_version_rows(data_version)
    rag_context, rag_retrieval_count = _latest_or_build_rag_context(data_version)
    signals = (list_signals(data_version=data_version, status="pending_rag_agent", limit=max_signals).get("signals") or [])[:max_signals]
    agent1_api_call_count = len(signals) * AGENT1_API_CALLS_PER_BUNDLE
    register_agent_event(ledger_id=ledger["ledgerId"], data_version=data_version, stage="product_judgment_agent", call_type="scene_judgment_data_only", requested_calls=1 if signals else 0, actual_calls=agent1_api_call_count, fallback_used=True, rag_retrievals=rag_retrieval_count, reason="V17数据判断Agent只生成场景数据判断包，不生成权限、动作边界或SOP。", payload={"signalCount": len(signals), "apiMode": AGENT1_API_MODE})
    raw_judgments: List[Dict[str, Any]] = []
    for signal in signals:
        raw_judgments.extend(_agent1_analyze_signal(signal, rag_context))
    _save_raw_judgments(raw_judgments)
    for signal in signals:
        update_signal_status(signal.get("signalId"), "product_analysis_judged", {"version": DUAL_AGENT_PIPELINE_VERSION, "metricJudgmentMode": "scene_data_judgment", "agent1ApiMode": AGENT1_API_MODE})
    packages, identity_gaps = _package_product_judgments(data_version)
    sorted_packages = sorted(packages, key=lambda item: (1 if item.get("taskCandidateAllowed") else 0, float(item.get("relationConfidence") or item.get("packageConfidence") or item.get("confidence") or 0), SEVERITY_RANK.get(str(item.get("maxSeverity") or "normal"), 0)), reverse=True)
    candidate_packages = [item for item in sorted_packages if item.get("taskCandidateAllowed")]
    task_mapping_calls = TASK_MAPPING_API_CALLS_PER_RUN if candidate_packages else 0
    register_agent_event(ledger_id=ledger["ledgerId"], data_version=data_version, stage="task_mapping_agent", call_type="permission_sop_action_mapping", requested_calls=1 if candidate_packages else 0, actual_calls=task_mapping_calls, fallback_used=True, reason="V17任务映射站接收数据判断包后再生成运营动作；数据判断站不承担动作职责。", payload={"candidatePackageCount": len(candidate_packages), "maxTasksPerRun": MAX_TASKS_PER_RUN})
    decisions: List[Dict[str, Any]] = []
    streamed: List[Dict[str, Any]] = []
    candidate_index = 0
    for package in sorted_packages:
        decision = _agent2_task_decision(package, candidate_index if package.get("taskCandidateAllowed") else MAX_TASKS_PER_RUN + 1)
        if package.get("taskCandidateAllowed"):
            candidate_index += 1
        _save_decision(decision)
        decisions.append(decision)
        streamed.append(_stream_decision_to_task_pool(decision, created_by=created_by))
    by_decision = Counter(str(item.get("decision")) for item in decisions)
    task_pool_created = sum(int(item.get("createdTaskCount") or 0) for item in streamed)
    formal_decision_count = int(by_decision.get("create_task_snapshot", 0) or 0) + int(by_decision.get("manager_review_required", 0) or 0)
    budget_summary = read_agent_budget_summary(ledger_id=ledger["ledgerId"])
    api_budget_violation = bool(budget_summary.get("budgetViolation"))
    generation_run = record_task_generation_run(data_version=data_version, input_bundle_count=len(signals), agent_judgment_count=len(raw_judgments), product_judgment_package_count=len(packages), identity_gap_count=len(identity_gaps), task_decision_count=len(decisions), by_decision=dict(by_decision), streamed_task_snapshot_count=sum(1 for item in streamed if item.get("ok")), task_pool_created_count=task_pool_created, skipped_formal_count=sum(1 for item in streamed if item.get("skipped")), zero_task_reasons=[item.get("reason") for item in decisions if item.get("decision") == "no_task"][:20], agent1_api_call_count=agent1_api_call_count, rag_retrieval_count=rag_retrieval_count, api_budget_violation=api_budget_violation, agent_budget_summary=budget_summary, total_agent_call_count=int(budget_summary.get("totalAgentCalls") or 0), total_agent_budget=int(budget_summary.get("totalAgentBudget") or 8), source="v17_scene_judgment_rag_router")
    try:
        from src.services.frontend_read_model_service import refresh_dashboard_view, refresh_task_views
        refresh_task_views() if task_pool_created else refresh_dashboard_view()
    except Exception:
        pass
    ref = f"dual_agent_product_task:{data_version or 'latest'}"
    return {"version": DUAL_AGENT_PIPELINE_VERSION, "mode": "v17_scene_judgment_rag_router", "dataVersion": data_version, "outputRef": ref, "agentJudgmentRef": ref, "ragContextRef": rag_context_ref or rag_context.get("ragContextRef") or rag_context.get("outputRef"), "signalCount": len(signals), "judgmentCount": len(raw_judgments), "rawJudgmentCount": len(raw_judgments), "metricJudgmentMode": "scene_data_judgment", "agent1ApiMode": AGENT1_API_MODE, "agent1ApiCallCount": agent1_api_call_count, "taskMappingApiCallCount": task_mapping_calls, "totalAgentCallCount": int(budget_summary.get("totalAgentCalls") or 0), "totalAgentBudget": int(budget_summary.get("totalAgentBudget") or 8), "apiBudgetViolation": api_budget_violation, "agentBudgetLedger": budget_summary, "ragRetrievalCount": rag_retrieval_count, "ragRetrievalScope": RAG_RETRIEVAL_SCOPE, "averageJudgmentsPerSignal": round(len(raw_judgments) / len(signals), 2) if signals else 0, "productJudgmentPackageCount": len(packages), "candidatePackageCount": len(candidate_packages), "identityGapCount": len(identity_gaps), "taskDecisionCount": len(decisions), "formalDecisionCount": formal_decision_count, "streamedTaskSnapshotCount": sum(1 for item in streamed if item.get("ok")), "streamedTaskPoolCount": task_pool_created, "byDecision": dict(by_decision), "taskGenerationRun": generation_run, "packages": packages[:50], "identityGaps": identity_gaps[:50], "decisions": decisions[:50], "streamed": streamed[:50], "rule": "V17: data judgment Agent only proves data relation; task mapping Agent owns operation action generation."}
