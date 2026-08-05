"""V21.3 product signal admission station.

The first comparable report remains a baseline. Production admission has no
artificial minimum and no eight-product business cap: every strong or medium
signal may enter the pipeline and downstream microbatches own throughput.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads
from src.services.agent_pipeline_governance_v213_service import (
    AGENT_PIPELINE_GOVERNANCE_VERSION,
    normalize_admission_limits,
)
from src.services.pipeline_agent1_microbatch_v20101_service import (
    seed_agent1_pipeline_items_from_admission,
)
from src.services.pipeline_item_service import (
    build_item_envelope,
    record_pipeline_item_event,
    upsert_pipeline_item,
)
from src.services.product_signal_snapshot_service import (
    materialize_product_signal_snapshot,
)
from src.services.signal_pool_service import (
    ensure_signal_pool_tables,
    generate_signal_pool,
    list_signals,
    update_signal_status,
)

PRODUCT_SIGNAL_ADMISSION_VERSION = "21.3"
ADMITTED_STATUS = "admitted_for_judgment"
OBSERVED_STATUS = "observed_soft_gate"
BASELINE_STAGE = "metric_snapshot_ready"
MIN_ADMITTED = 0
MAX_ADMITTED = 0  # zero means use max_signals; it is not a business truncation.
STRONG_SCORE = 70
MEDIUM_SCORE = 45
WEAK_SCORE = 25

SALES_METRICS = {"paymentAmount", "gmv"}
TRAFFIC_METRICS = {"organicVisitors", "paidVisitors", "visitorCount"}
CLICK_METRICS = {"clickRate"}
CONVERSION_METRICS = {"conversionRate"}
EFFICIENCY_METRICS = {"roi", "roas"}
AD_METRICS = {"adSpend"}
INVENTORY_METRICS = {"inventory", "stock", "availableDays"}
SERVICE_METRICS = {"refundRate", "afterSalesRate"}


def _num(value: Any) -> float | None:
    if value in {None, "", "—", "UNKNOWN"}:
        return None
    try:
        return float(
            str(value)
            .replace("%", "")
            .replace(",", "")
            .replace("￥", "")
            .replace("¥", "")
            .strip()
        )
    except Exception:
        return None


def _payload(signal: Dict[str, Any]) -> Dict[str, Any]:
    value = signal.get("payload")
    if isinstance(value, dict):
        return value
    try:
        data = loads(value)
        return data if isinstance(data, dict) else signal
    except Exception:
        return signal


def _field_signals(signal: Dict[str, Any]) -> List[Dict[str, Any]]:
    src = _payload(signal)
    snapshot = (
        src.get("snapshotLayer")
        if isinstance(src.get("snapshotLayer"), dict)
        else {}
    )
    values = snapshot.get("fieldSignals") or src.get("fieldSignals") or []
    return (
        [item for item in values if isinstance(item, dict)]
        if isinstance(values, list)
        else []
    )


def _metric_code(item: Dict[str, Any]) -> str:
    return str(
        item.get("metricCode")
        or item.get("code")
        or item.get("metricName")
        or ""
    ).strip()


def _ratio(item: Dict[str, Any]) -> float | None:
    for key in (
        "changeRatio",
        "changeRate",
        "deltaRate",
        "changeVsPrevious",
        "delta",
    ):
        if key in item:
            value = _num(item.get(key))
            if value is not None:
                return value / 100 if abs(value) > 2 else value
    previous = _num(
        item.get("previous")
        if "previous" in item
        else item.get("previousValue")
    )
    current = _num(
        item.get("current")
        if "current" in item
        else item.get("currentValue")
        if "currentValue" in item
        else item.get("latest")
    )
    if previous not in {None, 0} and current is not None:
        return (current - previous) / previous
    return None


def _changed(item: Dict[str, Any]) -> bool:
    ratio = _ratio(item)
    strength = str(item.get("signalStrength") or "").lower()
    return bool(
        strength in {"high", "medium"}
        or (ratio is not None and abs(ratio) >= 0.03)
    )


def _has_metric(
    signals: List[Dict[str, Any]],
    metrics: set[str],
    direction: str | None = None,
    threshold: float = 0.03,
) -> bool:
    for item in signals:
        if _metric_code(item) not in metrics:
            continue
        ratio = _ratio(item)
        if ratio is None:
            if _changed(item) and direction is None:
                return True
            continue
        if direction == "up" and ratio >= threshold:
            return True
        if direction == "down" and ratio <= -threshold:
            return True
        if direction is None and abs(ratio) >= threshold:
            return True
    return False


def _is_test_or_new_link(signal: Dict[str, Any]) -> bool:
    src = _payload(signal)
    profile = (
        src.get("profileLayer")
        if isinstance(src.get("profileLayer"), dict)
        else {}
    )
    text = json.dumps(
        {
            "title": profile.get("title") or src.get("title"),
            "role": profile.get("productRole"),
            "stage": profile.get("lifecycleStage"),
            "tags": profile.get("tags") or src.get("tags"),
        },
        ensure_ascii=False,
    ).lower()
    return any(word in text for word in ("test", "new", "测试", "新品", "次链接"))


def score_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    src = _payload(signal)
    signals = _field_signals(signal)
    cross = (
        src.get("crossValidation")
        if isinstance(src.get("crossValidation"), dict)
        else {}
    )
    changed_count = int(
        cross.get("changedMetricCount")
        or sum(1 for item in signals if _changed(item))
        or 0
    )
    abnormal_count = int(cross.get("abnormalMetricCount") or 0)
    source_count = int(
        cross.get("sourceVersionCount")
        or cross.get("sourceDatasetCount")
        or 0
    )

    score = 0
    reasons: List[str] = []

    def add(value: int, reason: str) -> None:
        nonlocal score
        score += value
        reasons.append(reason)

    if changed_count >= 1:
        add(18, f"changed_metric_count={changed_count}")
    if changed_count >= 3:
        add(12, "multi_metric_linked_change")
    if abnormal_count >= 1:
        add(12, f"abnormal_metric_count={abnormal_count}")
    if source_count >= 2:
        add(8, "has_previous_snapshot")

    sales_up = _has_metric(signals, SALES_METRICS, "up")
    sales_down = _has_metric(signals, SALES_METRICS, "down")
    traffic_change = _has_metric(signals, TRAFFIC_METRICS)
    click_down = _has_metric(signals, CLICK_METRICS, "down")
    conversion_down = _has_metric(signals, CONVERSION_METRICS, "down")
    efficiency_down = _has_metric(signals, EFFICIENCY_METRICS, "down")
    ad_up = _has_metric(signals, AD_METRICS, "up")
    inventory_down = _has_metric(
        signals,
        INVENTORY_METRICS,
        "down",
        threshold=0.08,
    )
    service_up = _has_metric(signals, SERVICE_METRICS, "up")

    if sales_up:
        add(20, "growth_window_candidate")
    if sales_down:
        add(18, "sales_risk_candidate")
    if traffic_change:
        add(12, "traffic_change")
    if click_down:
        add(18, "click_efficiency_drop")
    if conversion_down:
        add(18, "conversion_drop")
    if efficiency_down:
        add(20, "roi_roas_decline")
    if ad_up:
        add(10, "ad_spend_change")
    if ad_up and not sales_up:
        add(10, "ad_spend_without_sales_growth")
    if inventory_down and sales_up:
        add(10, "inventory_capacity_with_growth")
    elif inventory_down:
        add(-12, "inventory_only_downgrade")
    if service_up:
        add(18, "service_risk")
    if _is_test_or_new_link(signal) and (sales_up or traffic_change):
        add(22, "new_or_test_link_breakout")
    if changed_count == 0:
        add(-20, "no_clear_delta")

    score = max(0, min(100, int(round(score))))
    if score >= STRONG_SCORE:
        level = "strong_candidate"
    elif score >= MEDIUM_SCORE:
        level = "medium_candidate"
    elif score >= WEAK_SCORE:
        level = "weak_observation"
    else:
        level = "noise_or_baseline"
    return {
        "score": score,
        "level": level,
        "reasons": reasons[:12],
        "changedMetricCount": changed_count,
        "abnormalMetricCount": abnormal_count,
        "sourceVersionCount": source_count,
        "softGateRule": "quality_threshold_without_artificial_floor",
    }


def _reset_current_version_admissions(data_version: str | None) -> None:
    if not data_version:
        return
    ensure_signal_pool_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT signal_id,payload FROM signal_pool_v14 WHERE data_version=?",
            (data_version,),
        ).fetchall()
    for row in rows:
        payload = loads(row["payload"])
        if isinstance(payload, dict) and payload.get("admissionVersion") in {
            PRODUCT_SIGNAL_ADMISSION_VERSION,
            "20.9.3",
        }:
            update_signal_status(
                row["signal_id"],
                "pending_rag_agent",
                {"admissionResetBy": PRODUCT_SIGNAL_ADMISSION_VERSION},
            )


def _baseline_only(snapshot: Dict[str, Any]) -> bool:
    baseline = (
        snapshot.get("baseline")
        if isinstance(snapshot.get("baseline"), dict)
        else {}
    )
    return bool(
        snapshot.get("baselineNoPrevious")
        or baseline.get("baselineNoPrevious")
    )


def _baseline_reason(snapshot: Dict[str, Any]) -> str:
    baseline = (
        snapshot.get("baseline")
        if isinstance(snapshot.get("baseline"), dict)
        else {}
    )
    return str(
        baseline.get("reason")
        or "首份报表或没有上一份可比业务报表，只建立商品与指标基线。"
    )


def _seed_baseline_items(
    data_version: str | None,
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    packages = snapshot.get("productSignalPackages") or snapshot.get("signals") or []
    seeded = 0
    for package in packages:
        if not isinstance(package, dict):
            continue
        product_id = package.get("productId") or package.get("entityId")
        store_id = package.get("storeId")
        if not product_id:
            continue
        safe_product = str(product_id).replace(":", "-")
        safe_store = str(store_id or "GLOBAL").replace(":", "-")
        item_id = f"PI-BASE-{data_version or 'latest'}-{safe_product}-{safe_store}"
        source_ref = (
            snapshot.get("productSignalSnapshotRef")
            or snapshot.get("outputRef")
        )
        envelope = build_item_envelope(
            data_version=data_version,
            item_id=item_id,
            product_id=product_id,
            store_id=store_id,
            input_ref=source_ref,
            output_ref=f"baseline:{data_version or 'latest'}:{product_id}",
            stage=BASELINE_STAGE,
        )
        profile = (
            package.get("profileLayer")
            if isinstance(package.get("profileLayer"), dict)
            else {}
        )
        payload = {
            "version": PRODUCT_SIGNAL_ADMISSION_VERSION,
            "source": "baseline_gate",
            "baselineOnly": True,
            "baselineReason": _baseline_reason(snapshot),
            "productId": product_id,
            "storeId": store_id,
            "productIdentity": {
                "productId": product_id,
                "storeId": store_id,
                "productTitle": profile.get("title"),
            },
            "metricLayer": package.get("metricLayer"),
            "rule": "First comparable report creates baseline items only.",
        }
        envelope = upsert_pipeline_item(
            envelope,
            stage=BASELINE_STAGE,
            status="completed",
            priority=90,
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        record_pipeline_item_event(
            envelope,
            station_id="product_signal_admission_station",
            stage=BASELINE_STAGE,
            status="completed",
            input_ref=source_ref,
            output_ref=envelope.get("outputRef"),
            payload=payload,
        )
        seeded += 1
    return {
        "version": PRODUCT_SIGNAL_ADMISSION_VERSION,
        "seededBaselineItemCount": seeded,
        "stage": BASELINE_STAGE,
    }


def product_signal_admission_station_v197(
    data_version: str | None,
    *,
    user_id: str | None = None,
    max_signals: int = 160,
    min_admitted: int = MIN_ADMITTED,
    max_admitted: int | None = None,
    force: bool = False,
    **_: Any,
) -> Dict[str, Any]:
    del force
    limits = normalize_admission_limits(
        max_signals=max_signals,
        min_admitted=min_admitted,
        max_admitted=max_admitted,
    )
    signal_snapshot = materialize_product_signal_snapshot(
        data_version=data_version,
        user_id=user_id,
        force=True,
    )
    if _baseline_only(signal_snapshot):
        baseline_seed = _seed_baseline_items(data_version, signal_snapshot)
        return {
            "version": PRODUCT_SIGNAL_ADMISSION_VERSION,
            "governanceVersion": AGENT_PIPELINE_GOVERNANCE_VERSION,
            "stationId": "product_signal_admission_station",
            "dataVersion": data_version,
            "baselineOnly": True,
            "baselineGate": "closed_before_signal_engine",
            "baselineReason": _baseline_reason(signal_snapshot),
            "productSnapshotCount": signal_snapshot.get("productSnapshotCount", 0),
            "fullSignalCount": 0,
            "generatedSignalCount": 0,
            "candidateProductCount": 0,
            "admittedSignalCount": 0,
            "observedSignalCount": 0,
            "agent1PendingItemCount": 0,
            "observedItemCount": 0,
            "baselineItemSeed": baseline_seed,
            "admissionLimits": limits,
            "outputRef": f"baseline_only_product_signal_admission:{data_version or 'latest'}",
            "rule": "Baseline only: no signal_pool, Agent1 or task generation.",
        }

    _reset_current_version_admissions(data_version)
    generated = generate_signal_pool(
        data_version=data_version,
        max_signals=limits["maxSignals"],
        user_id=user_id,
    )
    signals = (
        list_signals(
            data_version=data_version,
            limit=limits["maxSignals"],
        ).get("signals")
        or []
    )
    scored = [
        {"signal": signal, "score": score_signal(signal)}
        for signal in signals
    ]
    scored.sort(
        key=lambda item: (
            int(item["score"].get("score") or 0),
            str(item["signal"].get("entityId") or ""),
        ),
        reverse=True,
    )

    qualified = [
        item
        for item in scored
        if item["score"].get("level")
        in {"strong_candidate", "medium_candidate"}
    ]
    selected = qualified[: limits["maxAdmitted"]]
    selected_ids = {
        item["signal"].get("signalId")
        for item in selected
    }

    admitted: List[Dict[str, Any]] = []
    observed: List[Dict[str, Any]] = []
    for item in scored:
        signal = item["signal"]
        patch = {
            "admissionVersion": PRODUCT_SIGNAL_ADMISSION_VERSION,
            "governanceVersion": AGENT_PIPELINE_GOVERNANCE_VERSION,
            "admissionScore": item["score"],
            "previousStatusBeforeAdmission": signal.get("status"),
            "softGateOutputRef": f"product_signal_admission:{data_version or 'latest'}",
        }
        summary = {
            "signalId": signal.get("signalId"),
            "productId": signal.get("entityId") or signal.get("productId"),
            "storeId": signal.get("storeId"),
            **item["score"],
        }
        if signal.get("signalId") in selected_ids:
            update_signal_status(signal.get("signalId"), ADMITTED_STATUS, patch)
            admitted.append(summary)
        else:
            update_signal_status(signal.get("signalId"), OBSERVED_STATUS, patch)
            observed.append(summary)

    by_level: Dict[str, int] = {}
    for item in scored:
        level = str(item["score"].get("level"))
        by_level[level] = by_level.get(level, 0) + 1

    item_seed = seed_agent1_pipeline_items_from_admission(
        data_version,
        admitted=admitted,
        observed=observed,
        source="product_signal_admission_v21_3",
    )
    return {
        "version": PRODUCT_SIGNAL_ADMISSION_VERSION,
        "governanceVersion": AGENT_PIPELINE_GOVERNANCE_VERSION,
        "stationId": "product_signal_admission_station",
        "dataVersion": data_version,
        "baselineOnly": False,
        "baselineGate": "open_has_comparable_history",
        "baselineReason": _baseline_reason(signal_snapshot),
        "fullSignalCount": len(signals),
        "generatedSignalCount": generated.get("signalCount"),
        "qualifiedSignalCount": len(qualified),
        "candidateProductCount": len(admitted),
        "admittedSignalCount": len(admitted),
        "observedSignalCount": len(observed),
        "pipelineItemSeed": item_seed,
        "agent1PendingItemCount": item_seed.get("seededAgent1PendingCount"),
        "observedItemCount": item_seed.get("observedItemCount"),
        "byAdmissionLevel": by_level,
        "admissionLimits": limits,
        "artificialMinimumApplied": False,
        "eightItemBusinessCapApplied": False,
        "admitted": admitted[: limits["maxAdmitted"]],
        "observedTop": observed[:12],
        "admissionRef": f"product_signal_admission:{data_version or 'latest'}",
        "outputRef": f"product_signal_admission:{data_version or 'latest'}",
        "rule": (
            "Only strong/medium signals enter Agent1; no weak-signal padding and "
            "the default maximum equals max_signals so microbatches own throughput."
        ),
    }
