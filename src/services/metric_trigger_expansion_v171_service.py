"""V18.6 metric trigger expansion service.

First-report baseline and zero-change metrics are not business task signals.
The fullProductBundle is a data/evidence contract; metric triggers are created
only after a comparable previous report exists and a metric has real movement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple
from uuid import uuid4

import src.services.dual_agent_product_task_service as base
from src.repositories.sqlite_repository import connect, loads
from src.services.signal_pool_service import list_signals

METRIC_TRIGGER_EXPANSION_VERSION = "18.6"
MAX_TRIGGERS_PER_PRODUCT = 3
RELATION_CONFIDENCE_THRESHOLD = 0.50
TRIGGER_MAGNITUDE_THRESHOLD = 0.12
ZERO_CHANGE_EPSILON = 1e-9
TRIGGER_RANK = {"high": 4, "medium": 3, "low": 2, "normal": 1, None: 0}

SCENE_PROTOCOLS: Dict[str, Dict[str, Any]] = {
    "inventory_traffic_relation": {
        "triggerMetrics": ["inventory"],
        "candidateSignalType": "business_opportunity",
        "strong": ["organicVisitors", "paidVisitors", "paymentAmount", "conversionRate", "roi", "roas", "clickRate"],
        "weak": ["refundRate", "afterSalesRate", "grossMargin"],
        "description": "库存大幅波动时，优先判断库存与流量、成交、转化和投产的联动；全量包只作为证据上下文。",
    },
    "refund_commitment_relation": {
        "triggerMetrics": ["refundRate", "afterSalesRate"],
        "candidateSignalType": "service_risk",
        "strong": ["adSpend", "paidVisitors", "roas", "clickRate", "conversionRate", "paymentAmount"],
        "weak": ["inventory", "organicVisitors", "grossMargin"],
        "description": "售后波动优先判断强推、人群、点击转化和承诺偏差；不把库存作为主判断依据。",
    },
    "revenue_conversion_relation": {
        "triggerMetrics": ["paymentAmount", "gmv"],
        "candidateSignalType": "revenue_change",
        "strong": ["organicVisitors", "paidVisitors", "conversionRate", "clickRate", "inventory", "roi", "roas", "adSpend"],
        "weak": ["refundRate", "afterSalesRate"],
        "description": "支付/GMV变化优先判断流量入口、转化承接、库存承接和投放效率。",
    },
    "ad_efficiency_relation": {
        "triggerMetrics": ["roi", "roas", "adSpend"],
        "candidateSignalType": "efficiency_drop",
        "strong": ["adSpend", "paymentAmount", "conversionRate", "clickRate", "paidVisitors", "roi", "roas"],
        "weak": ["inventory", "refundRate", "afterSalesRate"],
        "description": "投放效率场景优先判断广告消耗、成交、点击转化和付费流量联动。",
    },
    "conversion_traffic_relation": {
        "triggerMetrics": ["conversionRate", "clickRate", "organicVisitors", "paidVisitors"],
        "candidateSignalType": "conversion_mismatch",
        "strong": ["organicVisitors", "paidVisitors", "clickRate", "conversionRate", "paymentAmount", "roi", "roas"],
        "weak": ["refundRate", "inventory"],
        "description": "流量/转化场景优先判断流量入口和承接效率，不用所有指标平均。",
    },
}

METRIC_TO_SCENE = {metric: scene for scene, protocol in SCENE_PROTOCOLS.items() for metric in protocol.get("triggerMetrics", [])}
DOWNSIDE_WHEN_UP = {"refundRate", "afterSalesRate", "adSpend"}
DOWNSIDE_WHEN_DOWN = {"paymentAmount", "gmv", "roi", "roas", "inventory", "conversionRate", "grossMargin", "clickRate", "organicVisitors", "paidVisitors"}


def now_iso() -> str:
    return datetime.now().isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6].upper()}"


def _safe_load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone())


def _latest_product_signal_snapshot(data_version: str | None) -> Dict[str, Any]:
    if not data_version:
        return {}
    with connect() as conn:
        if not _table_exists(conn, "product_signal_snapshots_v14"):
            return {}
        row = conn.execute("SELECT payload FROM product_signal_snapshots_v14 WHERE data_version=? ORDER BY created_at DESC LIMIT 1", (data_version,)).fetchone()
    return _safe_load(row["payload"]) if row else {}


def is_first_report_baseline(data_version: str | None) -> Dict[str, Any]:
    """Return baseline state for the current fullProductBundle dataVersion."""
    payload = _latest_product_signal_snapshot(data_version)
    if not payload:
        return {"isFirstReportBaseline": False, "baselineNoPrevious": False, "reason": "no_product_signal_snapshot"}
    previous_snapshot_id = payload.get("previousSnapshotId") or payload.get("previousProductSnapshotId") or payload.get("previousSignalSnapshotId")
    previous_data_version = payload.get("previousDataVersion") or payload.get("previous_data_version")
    packages = payload.get("productSignalPackages") or payload.get("signals") or []
    package_count = len(packages) if isinstance(packages, list) else int(payload.get("productSignalPackageCount") or payload.get("productSignalCount") or 0)
    baseline_meta = payload.get("baseline") if isinstance(payload.get("baseline"), dict) else {}
    no_previous = not previous_snapshot_id and not previous_data_version
    explicit_baseline = bool(baseline_meta.get("baselineNoPrevious") or payload.get("baselineNoPrevious"))
    is_baseline = bool(package_count and (no_previous or explicit_baseline))
    return {
        "isFirstReportBaseline": is_baseline,
        "baselineNoPrevious": is_baseline,
        "previousSnapshotId": previous_snapshot_id,
        "previousDataVersion": previous_data_version,
        "productSignalPackageCount": package_count,
        "baseline": baseline_meta,
        "reason": baseline_meta.get("reason") or ("首份报表没有上一期可比业务快照，仅建立商品和指标基线。" if is_baseline else "has_comparable_previous_snapshot_or_no_bundle"),
    }


def _num(value: Any) -> float | None:
    if value in {None, "", "—", "未识别"}:
        return None
    try:
        return float(str(value).replace("¥", "").replace(",", "").replace("%", "").strip())
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


def _profile(signal: Dict[str, Any]) -> Dict[str, Any]:
    value = signal.get("profileLayer")
    if isinstance(value, dict):
        return value
    value = signal.get("productProfileSnapshot")
    return value if isinstance(value, dict) else {}


def _metric(signal: Dict[str, Any]) -> Dict[str, Any]:
    value = signal.get("metricLayer")
    if isinstance(value, dict):
        return value
    value = signal.get("productMetricSnapshot")
    return value if isinstance(value, dict) else {}


def _field_signals(signal: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = signal.get("snapshotLayer") if isinstance(signal.get("snapshotLayer"), dict) else {}
    values = snapshot.get("fieldSignals") or signal.get("fieldSignals")
    if isinstance(values, list):
        return [item for item in values if isinstance(item, dict)]
    agent_pkg = signal.get("agentProductSnapshotPackage") if isinstance(signal.get("agentProductSnapshotPackage"), dict) else {}
    agent_snapshot = agent_pkg.get("snapshotLayer") if isinstance(agent_pkg.get("snapshotLayer"), dict) else {}
    values = agent_snapshot.get("fieldSignals")
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _field_map(signal: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in _field_signals(signal):
        metric = str(item.get("metricCode") or "").strip()
        if metric:
            result[metric] = item
    return result


def _delta(field_signal: Dict[str, Any]) -> float | None:
    value = field_signal.get("changeVsPrevious")
    if value is None:
        value = field_signal.get("delta")
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def _metric_status(metric_code: str, field_signal: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(field_signal, dict):
        return {"metricCode": metric_code, "available": False, "supportConfidence": 0.0, "physicalDirection": "missing", "businessDirection": "missing", "magnitude": "missing"}
    delta = _delta(field_signal)
    physical = _physical_direction(delta)
    strength = str(field_signal.get("signalStrength") or "normal")
    base = {"high": 0.78, "medium": 0.64, "low": 0.44, "normal": 0.24}.get(strength, 0.24)
    support_confidence = min(0.95, base + min(0.28, abs(delta or 0) * 1.0))
    return {"metricCode": metric_code, "available": True, "latest": field_signal.get("latest"), "previous": field_signal.get("previous"), "deltaValue": delta, "physicalDirection": physical, "businessDirection": _business_direction(metric_code, physical, field_signal.get("latest")), "magnitude": _magnitude(delta), "signalStrength": strength, "supportConfidence": round(support_confidence, 4)}


def _is_trigger(metric_code: str, status: Dict[str, Any]) -> bool:
    if metric_code not in METRIC_TO_SCENE:
        return False
    delta = status.get("deltaValue")
    if delta is None or abs(float(delta)) < ZERO_CHANGE_EPSILON:
        return False
    if status.get("physicalDirection") == "stable" or status.get("businessDirection") == "flat":
        return False
    if status.get("signalStrength") in {"high", "medium"}:
        return True
    return abs(float(delta)) >= TRIGGER_MAGNITUDE_THRESHOLD


def _rank_trigger(metric_code: str, status: Dict[str, Any]) -> Tuple[int, float]:
    strength_rank = TRIGGER_RANK.get(status.get("signalStrength"), 0)
    delta = abs(float(status.get("deltaValue") or 0))
    scene_bonus = 1 if metric_code in {"inventory", "refundRate", "paymentAmount", "roi", "roas"} else 0
    return (strength_rank + scene_bonus, delta)


def _supports_scene(scene: str, metric_code: str, status: Dict[str, Any], trigger_status: Dict[str, Any]) -> Tuple[bool, bool]:
    physical = status.get("physicalDirection")
    business = status.get("businessDirection")
    trigger_physical = trigger_status.get("physicalDirection")
    if not status.get("available"):
        return False, False
    if scene == "inventory_traffic_relation":
        if trigger_physical != "down":
            return False, False
        if metric_code in {"organicVisitors", "paidVisitors", "paymentAmount", "clickRate"}:
            return physical == "up", physical == "down"
        if metric_code in {"conversionRate", "roi", "roas"}:
            return physical in {"up", "stable"}, business == "downside"
        return False, False
    if scene == "refund_commitment_relation":
        if trigger_physical != "up":
            return False, False
        if metric_code in {"adSpend", "paidVisitors", "paymentAmount", "clickRate"}:
            return physical == "up", False
        if metric_code == "conversionRate":
            return physical in {"down", "stable"}, physical == "up"
        if metric_code == "roas":
            return physical in {"up", "stable"}, False
        return False, False
    if scene == "revenue_conversion_relation":
        if trigger_physical == "down":
            if metric_code in {"organicVisitors", "paidVisitors", "conversionRate", "clickRate", "inventory", "roi", "roas"}:
                return physical == "down" or business == "downside", physical == "up" and metric_code in {"organicVisitors", "paidVisitors"}
        if trigger_physical == "up":
            if metric_code in {"organicVisitors", "paidVisitors", "conversionRate", "roi", "roas"}:
                return physical in {"up", "stable"}, business == "downside"
        return False, False
    if scene == "ad_efficiency_relation":
        if metric_code == "adSpend":
            return physical == "up", physical == "down"
        if metric_code in {"paymentAmount", "conversionRate", "clickRate"}:
            return physical in {"down", "stable"}, physical == "up"
        if metric_code == "paidVisitors":
            return physical == "up", False
        if metric_code in {"roi", "roas"}:
            return business == "downside", physical == "up"
        return False, False
    if scene == "conversion_traffic_relation":
        if metric_code in {"organicVisitors", "paidVisitors", "clickRate", "conversionRate", "paymentAmount", "roi", "roas"}:
            return physical != "stable" or business == "downside", False
    return False, False


def _relation_for_trigger(signal: Dict[str, Any], trigger_metric: str, trigger_status: Dict[str, Any], fmap: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    scene = METRIC_TO_SCENE.get(trigger_metric) or "generic_metric_relation"
    protocol = SCENE_PROTOCOLS.get(scene, {})
    strong: Dict[str, Any] = {}
    weak: Dict[str, Any] = {}
    conflicts: Dict[str, Any] = {}
    support_sum = 0.0
    available_weight = 0.0
    strong_metrics = protocol.get("strong") or []
    weight = 1.0 / max(1, len(strong_metrics))
    for metric_code in strong_metrics:
        status = _metric_status(metric_code, fmap.get(metric_code))
        supported, conflict = _supports_scene(scene, metric_code, status, trigger_status)
        status["relationRole"] = "strong"
        status["supportsTrigger"] = supported
        status["relationWeight"] = round(weight, 4)
        strong[metric_code] = status
        if status.get("available"):
            available_weight += weight
        if supported:
            support_sum += weight * float(status.get("supportConfidence") or 0)
        if conflict:
            conflicts[metric_code] = {**status, "conflictReason": "strong linked metric moves against scene interpretation"}
    weak_metrics = protocol.get("weak") or []
    weak_weight = 1.0 / max(1, len(weak_metrics))
    for metric_code in weak_metrics:
        status = _metric_status(metric_code, fmap.get(metric_code))
        status["relationRole"] = "weak_background"
        status["relationWeight"] = round(weak_weight, 4)
        weak[metric_code] = status
    trigger_conf = float(trigger_status.get("supportConfidence") or 0)
    linked_conf = support_sum / max(0.01, available_weight) if available_weight else 0.0
    relation_conf = max(0.0, min(0.98, trigger_conf * 0.42 + linked_conf * 0.58))
    if scene == "inventory_traffic_relation":
        candidate = trigger_status.get("physicalDirection") == "down" and relation_conf >= RELATION_CONFIDENCE_THRESHOLD and any(item.get("supportsTrigger") for item in strong.values())
    elif scene == "refund_commitment_relation":
        candidate = trigger_status.get("physicalDirection") == "up" and relation_conf >= RELATION_CONFIDENCE_THRESHOLD and any(item.get("supportsTrigger") for item in strong.values())
    elif scene in {"revenue_conversion_relation", "ad_efficiency_relation", "conversion_traffic_relation"}:
        candidate = trigger_status.get("businessDirection") == "downside" and relation_conf >= RELATION_CONFIDENCE_THRESHOLD
    else:
        candidate = False
    reason = "主波动指标已展开，但未形成足够强的场景关联数据判断，只沉淀为观察。"
    if candidate:
        reason = f"{trigger_metric} 主波动与 {scene} 的强关联指标形成联动，构成可进入任务映射的数据判断包。"
    return {"sceneRoute": scene, "sceneProtocolDescription": protocol.get("description"), "triggerMetric": trigger_metric, "triggerDirection": trigger_status.get("businessDirection"), "triggerPhysicalDirection": trigger_status.get("physicalDirection"), "triggerMagnitude": trigger_status.get("magnitude"), "strongLinkedMetricSupport": strong, "weakLinkedMetricBackground": weak, "conflictMetrics": conflicts, "deltaConfidence": trigger_status.get("supportConfidence"), "trendConfidence": 0.0, "relationConfidence": round(relation_conf, 4), "linkedMetricConfidence": round(linked_conf, 4), "candidateSignal": bool(candidate), "candidateSignalType": protocol.get("candidateSignalType") or "generic_data_signal", "candidateReason": reason, "dataJudgmentOnly": True, "metricTriggerExpansion": True, "rule": "V18.6 metric trigger expansion: first-report baseline and zero-change metrics are skipped before task mapping."}


def _product_id(signal: Dict[str, Any]) -> str:
    return str(signal.get("productId") or signal.get("entityId") or "PRODUCT")


def _store_id(signal: Dict[str, Any]) -> str:
    return str(signal.get("storeId") or "GLOBAL")


def _full_bundle_evidence(signal: Dict[str, Any], trigger_metric: str, relation: Dict[str, Any]) -> Dict[str, Any]:
    profile = _profile(signal)
    metric = _metric(signal)
    return {"evidenceRole": "task_mapping_sop_context", "productId": _product_id(signal), "storeId": _store_id(signal), "title": profile.get("title") or profile.get("shortName") or signal.get("title"), "platform": profile.get("platform") or signal.get("platform"), "verticalCategory": profile.get("verticalCategory") or signal.get("verticalCategory"), "metricDate": metric.get("metricDate") or profile.get("metricDate"), "triggerMetric": trigger_metric, "triggerStatus": relation.get("triggerDirection"), "triggerMagnitude": relation.get("triggerMagnitude"), "strongLinkedMetricSupport": relation.get("strongLinkedMetricSupport"), "weakLinkedMetricBackground": relation.get("weakLinkedMetricBackground"), "summary": "全量包在V18.6中作为SOP解释、证据支撑、复盘指标和用户理解上下文。"}


def _package_for_trigger(signal: Dict[str, Any], trigger_metric: str, trigger_status: Dict[str, Any], relation: Dict[str, Any], data_version: str | None) -> Dict[str, Any]:
    product_id = _product_id(signal)
    store_id = _store_id(signal)
    severity = "high" if trigger_status.get("signalStrength") == "high" else "medium" if trigger_status.get("signalStrength") == "medium" else "low"
    evidence = _full_bundle_evidence(signal, trigger_metric, relation)
    candidate = bool(relation.get("candidateSignal"))
    scene_package = {"judgmentPackageType": "scene_data_judgment", **relation, "fullProductBundleEvidenceRef": f"full_product_bundle:{data_version or 'latest'}:{store_id}:{product_id}"}
    return {"version": METRIC_TRIGGER_EXPANSION_VERSION, "packageId": make_id("PJP"), "dataVersion": data_version or signal.get("dataVersion"), "storeId": store_id, "productId": product_id, "judgmentCount": 1, "primaryRisk": trigger_metric, "secondaryRisks": [], "maxSeverity": severity, "overallDecision": "candidate_signal" if candidate else "observe_only", "taskCandidateAllowed": candidate, "confidence": relation.get("relationConfidence") or 0, "packageConfidence": relation.get("relationConfidence") or 0, "relationConfidence": relation.get("relationConfidence") or 0, "relationConfidenceThreshold": RELATION_CONFIDENCE_THRESHOLD, "factEnvelope": {"factStatus": "passed", "facts": {"productId": product_id, "storeId": store_id, "title": evidence.get("title"), "platform": evidence.get("platform"), "verticalCategory": evidence.get("verticalCategory")}, "confidencePolicy": "facts are evidence context and are not averaged into relation confidence"}, "sceneJudgmentPackage": scene_package, "sceneRoute": relation.get("sceneRoute"), "triggerMetric": trigger_metric, "candidateSignal": candidate, "candidateSignalType": relation.get("candidateSignalType"), "candidateReason": relation.get("candidateReason"), "dataJudgmentOnly": True, "fullProductBundleEvidence": evidence, "riskCandidateCount": 1 if candidate else 0, "metricJudgmentCount": 1, "summary": f"{product_id} 由fullProductBundle展开指标触发器：trigger={trigger_metric}，scene={relation.get('sceneRoute')}，relationConfidence={relation.get('relationConfidence')}，candidateSignal={candidate}。", "evidencePack": [{"metricCode": trigger_metric, "metricTrigger": True, "metricStatus": trigger_status, "fullProductBundleEvidence": evidence}], "rawJudgmentIds": [], "identityStatus": "resolved", "rule": "V18.6: first report baseline and zero-change metrics are skipped; later reports expand real deltas into metric triggers."}


def delete_version_packages(data_version: str | None) -> None:
    if not data_version:
        return
    base.ensure_dual_agent_tables()
    with connect() as conn:
        conn.execute("DELETE FROM product_judgment_packages_v15 WHERE data_version = ?", (data_version,))
        conn.execute("DELETE FROM task_generation_decisions_v15 WHERE data_version = ?", (data_version,))
        conn.commit()


def generate_metric_trigger_scene_packages(data_version: str | None, *, limit: int = 500, replace_existing: bool = True) -> Dict[str, Any]:
    baseline = is_first_report_baseline(data_version)
    if baseline.get("isFirstReportBaseline"):
        return {"version": METRIC_TRIGGER_EXPANSION_VERSION, "mode": "first_report_baseline", "dataVersion": data_version, "baselineMode": "first_report", "baselineNoPrevious": True, "metricTriggerSkipped": True, "taskMappingSkipped": True, "signalCount": int(baseline.get("productSignalPackageCount") or 0), "metricTriggerCount": 0, "productJudgmentPackageCount": 0, "candidatePackageCount": 0, "observeOnlyPackageCount": 0, "packages": [], "baseline": baseline, "reason": "首份报表仅建立商品与指标基线，等待下一份报表形成变化判断。", "rule": "V18.6 first report baseline mode skips metricTrigger and task mapping."}
    signals = list_signals(data_version=data_version, status=None, limit=limit).get("signals") or []
    packages: List[Dict[str, Any]] = []
    observe_count = 0
    trigger_count = 0
    for signal in signals:
        fmap = _field_map(signal)
        candidates: List[Tuple[str, Dict[str, Any]]] = []
        for metric_code, field_signal in fmap.items():
            status = _metric_status(metric_code, field_signal)
            if _is_trigger(metric_code, status):
                candidates.append((metric_code, status))
        candidates.sort(key=lambda pair: _rank_trigger(pair[0], pair[1]), reverse=True)
        for metric_code, trigger_status in candidates[:MAX_TRIGGERS_PER_PRODUCT]:
            relation = _relation_for_trigger(signal, metric_code, trigger_status, fmap)
            package = _package_for_trigger(signal, metric_code, trigger_status, relation, data_version)
            packages.append(package)
            trigger_count += 1
            if not package.get("taskCandidateAllowed"):
                observe_count += 1
    if packages and replace_existing:
        delete_version_packages(data_version)
    if packages:
        base._save_packages(packages)
    candidate_count = sum(1 for item in packages if item.get("taskCandidateAllowed") or item.get("candidateSignal"))
    return {"version": METRIC_TRIGGER_EXPANSION_VERSION, "mode": "full_product_bundle_to_metric_trigger_scene_packages", "dataVersion": data_version, "baselineMode": "normal_delta", "baselineNoPrevious": False, "signalCount": len(signals), "metricTriggerCount": trigger_count, "productJudgmentPackageCount": len(packages), "candidatePackageCount": candidate_count, "observeOnlyPackageCount": observe_count, "packages": packages[:50], "rule": "V18.6 fullProductBundle expands into metric triggers only when the metric has real current-vs-previous movement."}
