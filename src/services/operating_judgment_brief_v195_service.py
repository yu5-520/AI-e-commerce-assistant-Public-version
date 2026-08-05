"""V19.5 operating judgment brief service.

Move route/action-family boundary back to the judgment layer. This service does
not write SOP. It enriches product judgment packages with operatingJudgmentBrief:
main growth signal, operating gap, capacity constraints, allowed action families
and forbidden action families.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

import src.services.dual_agent_product_task_service as base
from src.repositories.sqlite_repository import connect, loads

OPERATING_JUDGMENT_BRIEF_VERSION = "19.5"
TITLE_IMAGE_TEST = "title_image_test"
PLATFORM_ACTIVITY = "platform_activity"
ROAS_SCALE = "roas_scale"
ROAS_GUARD = "roas_guard"
CONVERSION_REPAIR = "conversion_repair"
SIMILAR_PRODUCT_TEST = "similar_product_test"
INVENTORY_METRICS = {"inventory", "stock", "availableDays"}
SALES_METRICS = {"paymentAmount", "gmv"}
CLICK_METRICS = {"clickRate"}
CONVERSION_METRICS = {"conversionRate"}
TRAFFIC_METRICS = {"organicVisitors", "paidVisitors", "visitorCount"}
EFFICIENCY_METRICS = {"roi", "roas"}
AD_METRICS = {"adSpend"}


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _table(conn: Any, name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _num(value: Any) -> float | None:
    if value in {None, "", "—", "未识别", "UNKNOWN"}:
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except Exception:
        return None


def _metric_changes(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence = package.get("fullProductBundleEvidence") if isinstance(package.get("fullProductBundleEvidence"), dict) else {}
    route = package.get("operatingGraphRoute") if isinstance(package.get("operatingGraphRoute"), dict) else {}
    values = package.get("dynamicMetricChanges") or package.get("allMetricChanges") or evidence.get("dynamicMetricChanges") or evidence.get("allMetricChanges") or route.get("allMetricChanges") or []
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _metric_code(item: Dict[str, Any]) -> str:
    return str(item.get("metricCode") or item.get("code") or "").strip()


def _current(item: Dict[str, Any]) -> Any:
    return item.get("currentValue") if "currentValue" in item else item.get("current") if "current" in item else item.get("latest")


def _previous(item: Dict[str, Any]) -> Any:
    return item.get("previousValue") if "previousValue" in item else item.get("previous")


def _ratio(item: Dict[str, Any]) -> float | None:
    for key in ["changeRatio", "changeRate", "changeVsPrevious", "deltaRate", "changeVsAvg"]:
        if key in item:
            value = _num(item.get(key))
            if value is not None:
                return value / 100 if abs(value) > 2 else value
    prev = _num(_previous(item))
    cur = _num(_current(item))
    if prev not in {None, 0} and cur is not None:
        return (cur - prev) / prev
    return None


def _find(changes: Iterable[Dict[str, Any]], codes: set[str]) -> Dict[str, Any] | None:
    for item in changes:
        if _metric_code(item) in codes:
            return item
    return None


def _positive(changes: Iterable[Dict[str, Any]], codes: set[str], threshold: float = 0.03) -> bool:
    return any((_ratio(item) or 0) > threshold for item in changes if _metric_code(item) in codes)


def _negative(changes: Iterable[Dict[str, Any]], codes: set[str], threshold: float = 0.03) -> bool:
    return any((_ratio(item) or 0) < -threshold for item in changes if _metric_code(item) in codes)


def _fact_line(item: Dict[str, Any]) -> str:
    code = _metric_code(item)
    name = item.get("metricName") or item.get("label") or code
    ratio = _ratio(item)
    if ratio is None:
        return f"{name}: {_previous(item)} -> {_current(item)}"
    return f"{name}: {_previous(item)} -> {_current(item)}, change {ratio * 100:+.1f}%"


def _product_title(package: Dict[str, Any]) -> str:
    evidence = package.get("fullProductBundleEvidence") if isinstance(package.get("fullProductBundleEvidence"), dict) else {}
    product = package.get("productIdentity") if isinstance(package.get("productIdentity"), dict) else {}
    return str(product.get("shortTitle") or product.get("productTitle") or evidence.get("title") or package.get("productTitle") or package.get("productId") or "product")


def build_operating_judgment_brief(package: Dict[str, Any]) -> Dict[str, Any]:
    changes = _metric_changes(package)
    sales_up = _positive(changes, SALES_METRICS)
    sales_down = _negative(changes, SALES_METRICS)
    click_down = _negative(changes, CLICK_METRICS)
    conversion_down = _negative(changes, CONVERSION_METRICS)
    traffic_down = _negative(changes, TRAFFIC_METRICS)
    organic_up = _positive(changes, {"organicVisitors", "visitorCount"})
    ad_up = _positive(changes, AD_METRICS)
    roas_down = _negative(changes, EFFICIENCY_METRICS)
    inventory_down = _negative(changes, INVENTORY_METRICS, threshold=0.08)

    constraints: List[Dict[str, Any]] = []
    if inventory_down:
        inv_items = [item for item in changes if _metric_code(item) in INVENTORY_METRICS]
        constraints.append({
            "type": "inventory_capacity",
            "operatorActionAllowed": False,
            "companyHook": ["supply_chain_inventory", "manager_coordination"],
            "evidence": [_fact_line(item) for item in inv_items[:3]],
            "rule": "inventory is a company capacity constraint, not an operator completion item",
        })

    if sales_up:
        signal = "growth_window"
    elif sales_down:
        signal = "revenue_risk"
    elif click_down or traffic_down:
        signal = "traffic_gap"
    elif conversion_down:
        signal = "conversion_gap"
    elif roas_down or (ad_up and not sales_up):
        signal = "paid_efficiency_gap"
    else:
        signal = "metric_observation"

    if click_down:
        gap = "click_efficiency_gap"
        route = {"routeId": "click_efficiency_route", "routeName": "click/material traffic gap", "confidence": 0.82}
        primary_metric = "clickRate"
        traffic_gap_type = "title_or_main_image_click_gap"
    elif conversion_down:
        gap = "conversion_repair_gap"
        route = {"routeId": "conversion_repair_route", "routeName": "conversion repair route", "confidence": 0.8}
        primary_metric = "conversionRate"
        traffic_gap_type = "conversion_page_or_price_gap"
    elif roas_down or (ad_up and not sales_up):
        gap = "paid_efficiency_gap"
        route = {"routeId": "paid_efficiency_route", "routeName": "paid traffic efficiency route", "confidence": 0.78}
        primary_metric = "roas" if _find(changes, {"roas"}) else "roi" if _find(changes, {"roi"}) else "adSpend"
        traffic_gap_type = "paid_traffic_efficiency_gap"
    elif sales_up or organic_up:
        gap = "growth_window_traffic_gap_validation"
        route = {"routeId": "growth_window", "routeName": "growth window traffic validation", "confidence": 0.76}
        primary_metric = "paymentAmount" if _find(changes, {"paymentAmount"}) else "gmv"
        traffic_gap_type = "growth_window_validation"
    else:
        gap = "data_observation"
        route = {"routeId": "metric_observation", "routeName": "metric observation", "confidence": 0.55}
        primary_metric = str(package.get("triggerMetric") or package.get("primaryRisk") or "all_metrics")
        traffic_gap_type = "insufficient_growth_signal"

    allowed: List[str] = []
    if click_down:
        allowed.extend([TITLE_IMAGE_TEST, SIMILAR_PRODUCT_TEST])
    if conversion_down:
        allowed.append(CONVERSION_REPAIR)
    if sales_up or organic_up:
        allowed.append(PLATFORM_ACTIVITY)
    if roas_down or (ad_up and not sales_up):
        allowed.append(ROAS_GUARD)
    if ad_up and sales_up and not roas_down:
        allowed.append(ROAS_SCALE)
    if not allowed:
        allowed.append(SIMILAR_PRODUCT_TEST)
    allowed = list(dict.fromkeys(allowed))

    forbidden = ["inventory_task", "inventory_as_operator_action"]
    if ROAS_GUARD not in allowed:
        forbidden.append("roas_guard_without_roi_or_roas_decline")
    if ROAS_SCALE not in allowed:
        forbidden.append("roas_scale_without_growth_and_efficiency_evidence")

    title = _product_title(package)
    evidence_facts = [_fact_line(item) for item in changes[:10]]
    metric_read = []
    if sales_up:
        metric_read.append("sales/gmv growth window exists")
    if click_down:
        metric_read.append("click efficiency is dropping; validate title/image/material traffic gap")
    if conversion_down:
        metric_read.append("conversion is dropping; validate detail page, price, offer and review trust")
    if ad_up:
        metric_read.append("ad spend increased; distinguish valid scale from inefficient spend")
    if roas_down:
        metric_read.append("roi/roas dropped; roas_guard is allowed")
    if inventory_down:
        metric_read.append("inventory/available days dropped; use only as capacity constraint")

    return {
        "version": OPERATING_JUDGMENT_BRIEF_VERSION,
        "platformRead": "operator growth view: traffic gap, click efficiency, conversion and campaign rhythm; inventory is not an operator completion item",
        "categoryRead": "category reading must use title, main image, scenario words, price/offer and platform activity window",
        "metricRead": "; ".join(metric_read) or "no strong operator action signal; keep as observation",
        "primaryBusinessSignal": signal,
        "primaryOperatingGap": gap,
        "primaryMetric": primary_metric,
        "trafficGapType": traffic_gap_type,
        "selectedRoute": route,
        "capacityConstraints": constraints,
        "companyHooks": sorted({hook for item in constraints for hook in item.get("companyHook", [])}),
        "allowedActionFamilies": allowed,
        "forbiddenActionFamilies": forbidden,
        "creativeHypothesis": f"{title} should validate growth/traffic gap within allowed action families; capacity constraints only limit scale rhythm.",
        "evidenceFacts": evidence_facts,
        "operatorBoundary": "judgment agent owns route/action-family boundary; mapping agent only writes execution SOP within allowedActionFamilies",
        "rule": "V19.5 operatingJudgmentBrief prevents risk-summary packages from forcing task mapping to invent direction",
    }


def apply_operating_judgment_brief(package: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(package or {})
    original_primary = item.get("primaryRisk")
    brief = item.get("operatingJudgmentBrief") if isinstance(item.get("operatingJudgmentBrief"), dict) else build_operating_judgment_brief(item)
    item["operatingJudgmentBrief"] = brief
    item["primaryRiskOriginal"] = original_primary
    item["primaryBusinessSignal"] = brief.get("primaryBusinessSignal")
    item["primaryOperatingGap"] = brief.get("primaryOperatingGap")
    item["trafficGapType"] = brief.get("trafficGapType")
    item["capacityConstraints"] = brief.get("capacityConstraints") or []
    item["companyHooks"] = brief.get("companyHooks") or []
    item["allowedActionFamilies"] = brief.get("allowedActionFamilies") or []
    item["forbiddenActionFamilies"] = brief.get("forbiddenActionFamilies") or []
    item["creativeHypothesis"] = brief.get("creativeHypothesis")
    item["evidenceFacts"] = brief.get("evidenceFacts") or []
    primary_metric = brief.get("primaryMetric")
    if primary_metric and primary_metric not in INVENTORY_METRICS:
        item["primaryRisk"] = primary_metric
        item["triggerMetric"] = primary_metric
    item["operatorJudgmentPackageMode"] = "v19_5_operating_judgment_brief"
    item["rule"] = "V19.5: operatingJudgmentBrief moves route/action-family boundary back to judgment layer."
    return item


def _load_packages(data_version: str | None) -> List[Dict[str, Any]]:
    if not data_version:
        return []
    base.ensure_dual_agent_tables()
    with connect() as conn:
        if not _table(conn, "product_judgment_packages_v15"):
            return []
        rows = conn.execute("SELECT payload FROM product_judgment_packages_v15 WHERE data_version = ? ORDER BY task_candidate_allowed DESC, created_at ASC", (data_version,)).fetchall()
    return [_load(row["payload"]) for row in rows]


def enrich_and_save_operating_judgment_briefs(data_version: str | None) -> Dict[str, Any]:
    packages = _load_packages(data_version)
    enriched = [apply_operating_judgment_brief(item) for item in packages]
    if enriched:
        base._save_packages(enriched)
    return {
        "version": OPERATING_JUDGMENT_BRIEF_VERSION,
        "dataVersion": data_version,
        "packageCount": len(enriched),
        "briefCount": sum(1 for item in enriched if item.get("operatingJudgmentBrief")),
        "capacityConstraintCount": sum(1 for item in enriched if item.get("capacityConstraints")),
        "allowedActionFamilyCoverage": sum(1 for item in enriched if item.get("allowedActionFamilies")),
        "packages": enriched[:20],
        "rule": "V19.5 enriches judgment packages before task mapping.",
    }
