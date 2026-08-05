"""V21.5 business report batch and operating evidence runtime.

This module installs the V21.5 contracts before API routes import service symbols:

- one uploaded file owns one business dataVersion/reportBatchId;
- routed datasets share the same version and persist without deleting sibling datasets;
- projections read one business version, never merge all raw history into current facts;
- signal evidence separates current facts, recent five reports, 10/30-report trends,
  linked metrics, conflicts, severity and confidence;
- Agent admission consumes cross-validated operating events instead of fixed metric-count
  score padding.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import mean
from typing import Any, Dict, List, Sequence
from uuid import uuid4

V215_VERSION = "21.5.0"

_BATCH_CONTEXT: ContextVar[Dict[str, str] | None] = ContextVar(
    "v215_report_batch_context", default=None
)
_PROJECTION_VERSION_CONTEXT: ContextVar[str | None] = ContextVar(
    "v215_projection_data_version", default=None
)
_INSTALLED = False


METRIC_ALIASES: Dict[str, Sequence[str]] = {
    "paymentAmount": ("paymentAmount", "gmv"),
    "gmv": ("gmv", "paymentAmount"),
    "roi": ("roi", "roas"),
    "roas": ("roas", "roi"),
    "clickRate": ("clickRate",),
    "conversionRate": ("conversionRate",),
    "refundRate": ("refundRate", "afterSalesRate"),
    "afterSalesRate": ("afterSalesRate", "refundRate"),
    "adSpend": ("adSpend",),
    "organicVisitors": ("organicVisitors",),
    "paidVisitors": ("paidVisitors",),
    "visitorCount": ("visitorCount", "organicVisitors", "paidVisitors"),
    "grossMargin": ("grossMargin",),
    "inventory": ("inventory",),
    "availableDays": ("availableDays", "sellableDays"),
}

EVIDENCE_GROUP = {
    "paymentAmount": "sales",
    "gmv": "sales",
    "roi": "efficiency",
    "roas": "efficiency",
    "clickRate": "click",
    "conversionRate": "conversion",
    "refundRate": "service",
    "afterSalesRate": "service",
    "adSpend": "spend",
    "organicVisitors": "organic_traffic",
    "paidVisitors": "paid_traffic",
    "visitorCount": "traffic",
    "grossMargin": "margin",
    "inventory": "inventory",
    "availableDays": "inventory_capacity",
}

HYPOTHESIS_SPECS: Dict[str, Dict[str, Any]] = {
    "paid_efficiency_decline": {
        "label": "投放效率恶化",
        "primary": [("roi", "down")],
        "related": [
            ("adSpend", "up"),
            ("paidVisitors", "up"),
            ("conversionRate", "down"),
            ("paymentAmount", "down"),
            ("grossMargin", "down"),
        ],
        "impact": 82,
    },
    "click_acceptance_decline": {
        "label": "标题主图承接下降",
        "primary": [("clickRate", "down")],
        "related": [
            ("organicVisitors", "down"),
            ("visitorCount", "down"),
            ("conversionRate", "down"),
            ("paymentAmount", "down"),
        ],
        "impact": 68,
    },
    "conversion_decline": {
        "label": "商品转化承接下降",
        "primary": [("conversionRate", "down")],
        "related": [
            ("clickRate", "up"),
            ("visitorCount", "up"),
            ("paymentAmount", "down"),
            ("roi", "down"),
        ],
        "impact": 76,
    },
    "service_risk": {
        "label": "售后风险上升",
        "primary": [("refundRate", "up"), ("afterSalesRate", "up")],
        "related": [
            ("conversionRate", "down"),
            ("paymentAmount", "down"),
            ("roi", "down"),
        ],
        "impact": 86,
    },
    "growth_opportunity": {
        "label": "增长机会",
        "primary": [("paymentAmount", "up"), ("gmv", "up")],
        "related": [
            ("organicVisitors", "up"),
            ("paidVisitors", "up"),
            ("clickRate", "up"),
            ("conversionRate", "up"),
            ("roi", "up"),
        ],
        "impact": 70,
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_batch() -> Dict[str, str]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = uuid4().hex[:6]
    return {
        "reportBatchId": f"RB-{stamp}-{suffix.upper()}",
        "businessDataVersion": f"DV-{stamp}-{suffix}",
    }


def _num(value: Any) -> float | None:
    if value in {None, "", "—", "UNKNOWN", "未识别"}:
        return None
    text = str(value).replace(",", "").replace("￥", "").replace("¥", "").strip()
    is_percent = text.endswith("%")
    text = text.replace("%", "")
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result / 100 if is_percent and abs(result) > 1 else result


def _change(previous: float | None, current: float | None) -> float | None:
    if previous is None or current is None:
        return None
    if abs(previous) < 1e-12:
        if abs(current) < 1e-12:
            return 0.0
        return 1.0 if current > 0 else -1.0
    return (current - previous) / abs(previous)


def _metric(item: Dict[str, Any] | None, code: str) -> float | None:
    if not item:
        return None
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    for key in METRIC_ALIASES.get(code, (code,)):
        value = metric.get(key)
        if value in {None, "", "—", "UNKNOWN", "未识别"}:
            value = item.get(key)
        number = _num(value)
        if number is not None:
            return number
    return None


def _object_key(item: Dict[str, Any]) -> str:
    return str(
        item.get("objectId")
        or f"{item.get('storeId') or 'GLOBAL'}::{item.get('productId') or item.get('id')}::{item.get('skuId') or 'NO-SKU'}"
    )


def _index_products(snapshot: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    return {
        _object_key(item): item
        for item in (snapshot or {}).get("products") or []
        if isinstance(item, dict)
    }


def _parse_date(item: Dict[str, Any] | None) -> datetime | None:
    if not item:
        return None
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
    candidates = [
        item.get("metricDate"),
        item.get("reportDate"),
        item.get("dataDate"),
        metric.get("metricDate"),
        metric.get("reportDate"),
        metric.get("dataDate"),
        profile.get("metricDate"),
        profile.get("reportDate"),
        profile.get("dataDate"),
    ]
    for value in candidates:
        if value in {None, "", "—", "未识别"}:
            continue
        text = str(value).strip().replace(".", "-").replace("/", "-")
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
            try:
                return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _linear_slope(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    n = len(values)
    x_mean = (n - 1) / 2
    y_mean = mean(values)
    denominator = sum((idx - x_mean) ** 2 for idx in range(n))
    if denominator <= 0:
        return None
    raw = sum((idx - x_mean) * (value - y_mean) for idx, value in enumerate(values)) / denominator
    scale = max(abs(y_mean), 1e-9)
    return raw / scale


def _return_volatility(values: Sequence[float]) -> float | None:
    if len(values) < 3:
        return None
    returns = [
        change
        for previous, current in zip(values, values[1:])
        if (change := _change(previous, current)) is not None
    ]
    if len(returns) < 2:
        return None
    avg = mean(returns)
    variance = sum((value - avg) ** 2 for value in returns) / len(returns)
    return sqrt(variance)


def _streak(values: Sequence[float]) -> tuple[str | None, int]:
    if len(values) < 2:
        return None, 0
    direction: str | None = None
    length = 0
    for previous, current in reversed(list(zip(values, values[1:]))):
        if current > previous:
            current_direction = "up"
        elif current < previous:
            current_direction = "down"
        else:
            break
        if direction is None:
            direction = current_direction
        if current_direction != direction:
            break
        length += 1
    return direction, length


def _nearest_period_change(
    points: Sequence[tuple[datetime | None, float]],
    days: int,
) -> float | None:
    dated = [(date, value) for date, value in points if date is not None]
    if len(dated) < 2:
        return None
    current_date, current = dated[-1]
    target = current_date - timedelta(days=days)
    previous_date, previous = min(dated[:-1], key=lambda item: abs((item[0] - target).days))
    tolerance = 12 if days <= 31 else 45
    if abs((previous_date - target).days) > tolerance:
        return None
    return _change(previous, current)


def build_time_series_features(
    current_item: Dict[str, Any],
    history_items: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Build deterministic multi-window features without rewriting current facts."""
    ordered_items = list(reversed(list(history_items[:30]))) + [current_item]
    result: Dict[str, Dict[str, Any]] = {}
    for code in METRIC_ALIASES:
        points = [
            (_parse_date(item), value)
            for item in ordered_items
            if (value := _metric(item, code)) is not None
        ]
        values = [value for _date, value in points]
        current = values[-1] if values else None
        previous = values[-2] if len(values) >= 2 else None
        direction, streak_length = _streak(values)
        recent5 = values[-5:]
        medium10 = values[-10:]
        long30 = values[-30:]
        date_quality = sum(1 for date, _value in points if date is not None) / len(points) if points else 0.0
        confidence = min(1.0, len(values) / 10) * (0.7 + 0.3 * date_quality)
        seasonal_residual = None
        if len(points) >= 12 and points[-1][0] is not None:
            month = points[-1][0].month
            seasonal = [value for date, value in points[:-1] if date is not None and date.month == month]
            if seasonal:
                seasonal_residual = _change(mean(seasonal), current)
        result[code] = {
            "metricCode": code,
            "current": current,
            "previous": previous,
            "previousDelta": _change(previous, current),
            "mom": _nearest_period_change(points, 30),
            "yoy": _nearest_period_change(points, 365),
            "slope5": _linear_slope(recent5),
            "slope10": _linear_slope(medium10),
            "slope30": _linear_slope(long30),
            "volatility10": _return_volatility(medium10),
            "streakDirection": direction,
            "streakLength": streak_length,
            "seasonalResidual": seasonal_residual,
            "sampleCount": len(values),
            "sampleConfidence": round(confidence, 4),
        }
    return result


def _direction_support(feature: Dict[str, Any], direction: str) -> Dict[str, Any]:
    sign = 1 if direction == "up" else -1
    checks = {
        "previous": feature.get("previousDelta"),
        "mom": feature.get("mom"),
        "yoy": feature.get("yoy"),
        "slope5": feature.get("slope5"),
        "slope10": feature.get("slope10"),
        "slope30": feature.get("slope30"),
    }
    supported = [
        name
        for name, value in checks.items()
        if value is not None and sign * float(value) >= 0.03
    ]
    opposed = [
        name
        for name, value in checks.items()
        if value is not None and sign * float(value) <= -0.03
    ]
    return {"supported": supported, "opposed": opposed}


def _metric_evidence(
    features: Dict[str, Dict[str, Any]],
    metric_code: str,
    direction: str,
) -> Dict[str, Any] | None:
    feature = features.get(metric_code) or {}
    support = _direction_support(feature, direction)
    delta = feature.get("previousDelta")
    sign = 1 if direction == "up" else -1
    primary_change = delta is not None and sign * float(delta) >= 0.03
    trend_change = bool(support["supported"])
    if not primary_change and not trend_change:
        return None
    magnitude = max(
        [sign * float(value) for value in [
            feature.get("previousDelta"),
            feature.get("mom"),
            feature.get("yoy"),
            feature.get("slope5"),
            feature.get("slope10"),
        ] if value is not None] or [0.0]
    )
    return {
        "metricCode": metric_code,
        "group": EVIDENCE_GROUP.get(metric_code, metric_code),
        "direction": direction,
        "magnitude": round(max(0.0, magnitude), 6),
        "temporalSupport": support["supported"],
        "temporalOpposition": support["opposed"],
        "sampleConfidence": feature.get("sampleConfidence") or 0.0,
        "streakDirection": feature.get("streakDirection"),
        "streakLength": feature.get("streakLength") or 0,
        "volatility10": feature.get("volatility10"),
        "seasonalResidual": feature.get("seasonalResidual"),
    }


def evaluate_hypotheses(
    features: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    evaluations: List[Dict[str, Any]] = []
    for code, spec in HYPOTHESIS_SPECS.items():
        primary_candidates = [
            evidence
            for metric_code, direction in spec["primary"]
            if (evidence := _metric_evidence(features, metric_code, direction))
        ]
        if not primary_candidates:
            continue
        primary = max(primary_candidates, key=lambda item: item["magnitude"])
        related_raw = [
            evidence
            for metric_code, direction in spec["related"]
            if (evidence := _metric_evidence(features, metric_code, direction))
        ]
        related_by_group: Dict[str, Dict[str, Any]] = {}
        for evidence in related_raw:
            current = related_by_group.get(evidence["group"])
            if not current or evidence["magnitude"] > current["magnitude"]:
                related_by_group[evidence["group"]] = evidence
        related = list(related_by_group.values())

        conflict_groups: List[str] = []
        for metric_code, direction in spec["related"]:
            feature = features.get(metric_code) or {}
            support = _direction_support(feature, direction)
            if support["opposed"]:
                conflict_groups.append(EVIDENCE_GROUP.get(metric_code, metric_code))
        conflict_groups = list(dict.fromkeys(conflict_groups))

        temporal_support = len(primary["temporalSupport"])
        volatility = primary.get("volatility10")
        noisy = bool(
            volatility is not None
            and primary["magnitude"] < max(0.08, float(volatility) * 1.5)
            and temporal_support <= 1
        )
        seasonal = primary.get("seasonalResidual")
        seasonally_explained = bool(
            seasonal is not None
            and abs(float(seasonal)) < 0.05
            and temporal_support <= 1
        )

        if seasonally_explained:
            status = "seasonal_normal"
        elif noisy:
            status = "buffered"
        elif len(conflict_groups) >= 2 and not related:
            status = "conflict"
        elif related and temporal_support >= 1:
            status = "confirmed"
        else:
            status = "insufficient_evidence"

        severity = min(
            100,
            round(
                primary["magnitude"] * 260
                + min(primary.get("streakLength") or 0, 5) * 5
                + min(temporal_support, 4) * 4
            ),
        )
        confidence = round(
            max(
                0,
                min(
                    100,
                    25
                    + float(primary.get("sampleConfidence") or 0) * 30
                    + min(len(related), 3) * 15
                    + min(temporal_support, 3) * 8
                    - min(len(conflict_groups), 3) * 15
                    - (18 if noisy else 0)
                    - (20 if seasonally_explained else 0),
                ),
            )
        )
        impact = int(spec["impact"])
        urgency = min(100, round(severity * 0.65 + impact * 0.35))
        if status != "confirmed":
            intensity = "L1"
        elif severity >= 80 and confidence >= 75:
            intensity = "L4"
        elif severity >= 65 and confidence >= 65:
            intensity = "L3"
        else:
            intensity = "L2"
        evaluations.append(
            {
                "hypothesisCode": code,
                "hypothesisLabel": spec["label"],
                "status": status,
                "severity": severity,
                "confidence": confidence,
                "businessImpact": impact,
                "urgency": urgency,
                "actionIntensity": intensity,
                "primaryEvidence": primary,
                "relatedEvidence": related,
                "independentEvidenceGroups": [
                    primary["group"],
                    *[item["group"] for item in related],
                ],
                "conflictEvidenceGroups": conflict_groups,
                "temporalConfirmationCount": temporal_support,
                "rule": "primary metric + independent linked metrics + multi-window time evidence; conflicts reduce confidence.",
            }
        )
    return evaluations


def build_cross_validation(
    current_item: Dict[str, Any],
    history_items: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    features = build_time_series_features(current_item, history_items)
    hypotheses = evaluate_hypotheses(features)
    priority = {
        "confirmed": 5,
        "conflict": 4,
        "buffered": 3,
        "seasonal_normal": 2,
        "insufficient_evidence": 1,
    }
    selected = max(
        hypotheses,
        key=lambda item: (
            priority.get(item["status"], 0),
            item["confidence"],
            item["severity"],
        ),
        default={
            "hypothesisCode": "no_operating_event",
            "hypothesisLabel": "没有形成经营事件",
            "status": "insufficient_evidence",
            "severity": 0,
            "confidence": 0,
            "businessImpact": 0,
            "urgency": 0,
            "actionIntensity": "L1",
            "independentEvidenceGroups": [],
            "conflictEvidenceGroups": [],
        },
    )
    changed = [
        code
        for code, feature in features.items()
        if feature.get("previousDelta") is not None
        and abs(float(feature["previousDelta"])) >= 0.03
    ]
    abnormal = [
        code
        for code, feature in features.items()
        if any(
            value is not None and abs(float(value)) >= 0.08
            for value in (
                feature.get("previousDelta"),
                feature.get("mom"),
                feature.get("yoy"),
                feature.get("slope5"),
            )
        )
    ]
    return {
        "version": V215_VERSION,
        "contract": "operatingEvidenceGraph.v1",
        "decision": selected,
        "hypotheses": hypotheses,
        "timeSeriesFeatures": features,
        "changedMetricCount": len(changed),
        "abnormalMetricCount": len(abnormal),
        "changedMetrics": changed,
        "abnormalMetrics": abnormal,
        "sourceVersionCount": len(history_items) + 1,
        "recentDirectComparisonWindow": min(5, len(history_items) + 1),
        "trendOverlayWindows": {"medium": 10, "long": 30},
        "sourceVersionScoreContribution": 0,
        "rule": "Current facts are immutable; recent five reports compare directly; 10/30-report history only contributes derived trend evidence.",
    }


def score_cross_validated_signal(signal: Dict[str, Any], fallback: Any) -> Dict[str, Any]:
    payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else signal
    if not isinstance(payload, dict):
        return fallback(signal)
    cross = payload.get("crossValidation") if isinstance(payload.get("crossValidation"), dict) else {}
    if cross.get("version") != V215_VERSION:
        return fallback(signal)
    decision = cross.get("decision") if isinstance(cross.get("decision"), dict) else {}
    status = str(decision.get("status") or "insufficient_evidence")
    severity = int(decision.get("severity") or 0)
    confidence = int(decision.get("confidence") or 0)
    impact = int(decision.get("businessImpact") or 0)
    urgency = int(decision.get("urgency") or 0)
    composite = round(severity * 0.45 + confidence * 0.35 + impact * 0.12 + urgency * 0.08)
    if status == "confirmed" and confidence >= 55:
        if composite >= 70 and confidence >= 70:
            level = "strong_candidate"
            score = max(70, composite)
        else:
            level = "medium_candidate"
            score = max(45, composite)
    elif status in {"conflict", "buffered", "seasonal_normal"}:
        level = "weak_observation"
        score = min(44, max(25, composite))
    else:
        level = "noise_or_baseline" if composite < 25 else "weak_observation"
        score = min(44, composite)
    return {
        "score": int(max(0, min(100, score))),
        "level": level,
        "reasons": [
            f"operating_event={decision.get('hypothesisCode') or 'none'}",
            f"validation_status={status}",
            f"severity={severity}",
            f"confidence={confidence}",
            f"independent_evidence_groups={len(decision.get('independentEvidenceGroups') or [])}",
            f"conflict_groups={len(decision.get('conflictEvidenceGroups') or [])}",
        ],
        "changedMetricCount": int(cross.get("changedMetricCount") or 0),
        "abnormalMetricCount": int(cross.get("abnormalMetricCount") or 0),
        "sourceVersionCount": int(cross.get("sourceVersionCount") or 0),
        "severity": severity,
        "confidence": confidence,
        "businessImpact": impact,
        "urgency": urgency,
        "validationStatus": status,
        "actionIntensity": decision.get("actionIntensity") or "L1",
        "softGateRule": "v21_5_cross_validated_operating_event_without_metric_count_padding",
    }


def _batch_for_data_version(connect: Any, loads: Any, data_version: str | None) -> str | None:
    if not data_version:
        return None
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT import_batch_id,payload FROM imported_report_rows WHERE data_version=? ORDER BY created_at DESC LIMIT 1",
                (data_version,),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    payload = loads(row["payload"]) if row["payload"] else {}
    return (
        (payload or {}).get("reportBatchId")
        or (payload or {}).get("importBatchId")
        or row["import_batch_id"]
        or data_version
    )


def install_v215_runtime() -> None:
    """Install V21.5 as the single active data/evidence contract."""
    global _INSTALLED
    if _INSTALLED:
        return

    from src.repositories.sqlite_repository import connect, dumps, loads
    from src.services import import_row_store_service as import_rows
    from src.services import module_projection_service as projection
    from src.services import pipeline_live_read_model_v208_service as live_model
    from src.services import product_signal_admission_v197_service as admission
    from src.services import product_signal_snapshot_service as signal_snapshot
    from src.services import report_alert_service as report_alert
    from src.services import report_schema_service as report_schema
    from src.services import system_product_snapshot_service as product_snapshot

    original_confirm_import = report_schema.confirm_report_import
    original_dataset_rows = projection.dataset_rows
    original_materialize_product = product_snapshot.materialize_system_product_snapshot
    original_materialize_signal = signal_snapshot.materialize_product_signal_snapshot
    original_score = admission.score_signal
    original_live_reader = live_model._read_pipeline_live_model

    def latest_business_data_version() -> str | None:
        try:
            import_rows.ensure_import_row_table()
            with connect() as conn:
                row = conn.execute(
                    """
                    SELECT data_version, MAX(created_at) AS max_created
                    FROM imported_report_rows
                    WHERE data_version IS NOT NULL AND data_version != ''
                    GROUP BY data_version
                    ORDER BY max_created DESC, data_version DESC
                    LIMIT 1
                    """
                ).fetchone()
            if row:
                return row["data_version"]
        except Exception:
            pass
        try:
            with connect() as conn:
                row = conn.execute(
                    """
                    SELECT data_version, MAX(created_at) AS max_created
                    FROM data_snapshots
                    WHERE data_version IS NOT NULL AND data_version != ''
                    GROUP BY data_version
                    ORDER BY max_created DESC, data_version DESC
                    LIMIT 1
                    """
                ).fetchone()
            return row["data_version"] if row else None
        except Exception:
            return None

    def save_import_rows_v215(
        data_version: str,
        dataset_name: str,
        rows: List[Dict[str, Any]],
    ) -> None:
        import_rows.ensure_import_row_table()
        created_at = report_alert.now_iso()
        batch = _BATCH_CONTEXT.get() or {}
        report_batch_id = (
            batch.get("reportBatchId")
            or _batch_for_data_version(connect, loads, data_version)
            or data_version
        )
        with connect() as conn:
            conn.execute(
                "DELETE FROM imported_report_rows WHERE data_version=? AND dataset_name=?",
                (data_version, dataset_name),
            )
            for index, row in enumerate(rows):
                payload = {str(key): value for key, value in row.items()}
                payload["dataVersion"] = data_version
                payload["businessDataVersion"] = data_version
                payload["datasetName"] = dataset_name
                payload["reportBatchId"] = report_batch_id
                payload.setdefault("importBatchId", report_batch_id)
                stamp = payload.get("permissionStamp") if isinstance(payload.get("permissionStamp"), dict) else {}
                visible = payload.get("visibleUserIds") or stamp.get("visibleUserIds")
                visible_text = ",".join(str(value) for value in visible) if isinstance(visible, list) else visible
                store_id = next(
                    (
                        str(payload[key])
                        for key in ("store_id", "storeId", "店铺ID", "店铺编号")
                        if payload.get(key) not in {None, ""}
                    ),
                    None,
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO imported_report_rows (
                        row_id,data_version,dataset_name,row_index,store_id,
                        permission_stamp_id,uploaded_by_user_id,owner_user_id,
                        assigned_operator_id,visible_user_ids,permission_source,
                        import_batch_id,payload,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"{data_version}:{dataset_name}:{index}",
                        data_version,
                        dataset_name,
                        index,
                        store_id,
                        payload.get("permissionStampId") or stamp.get("permissionStampId"),
                        payload.get("uploadedByUserId") or stamp.get("uploadedByUserId"),
                        payload.get("ownerUserId") or stamp.get("ownerUserId"),
                        payload.get("assignedOperatorId") or stamp.get("assignedOperatorId"),
                        visible_text,
                        payload.get("permissionSource") or stamp.get("permissionSource"),
                        report_batch_id,
                        dumps(payload),
                        created_at,
                    ),
                )
            conn.commit()

    def import_report_dataset_v215(
        dataset_name: str,
        rows: Any = None,
        auto_create_tasks: bool = False,
        *,
        data_version: str | None = None,
        report_batch_id: str | None = None,
        import_id: str | None = None,
    ) -> Dict[str, Any]:
        batch = _BATCH_CONTEXT.get() or {}
        dataset = report_alert._normalize_dataset_name(dataset_name)
        normalized_rows = report_alert._rows_from_payload(rows)
        created_at = report_alert.now_iso()
        resolved_data_version = (
            data_version
            or batch.get("businessDataVersion")
            or f"DV-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}"
        )
        resolved_batch = (
            report_batch_id
            or batch.get("reportBatchId")
            or f"RB-{resolved_data_version.removeprefix('DV-').upper()}"
        )
        snapshot = {
            "snapshotId": report_alert.make_id("SNAPSHOT"),
            "importId": import_id or resolved_batch,
            "reportBatchId": resolved_batch,
            "businessDataVersion": resolved_data_version,
            "datasetName": dataset,
            "dataVersion": resolved_data_version,
            "rowCount": len(normalized_rows),
            "createdAt": created_at,
            "version": V215_VERSION,
            "taskCreation": "disabled_in_report_alert_layer",
            "routingRole": "fact_namespace_inside_one_business_report",
        }
        report_alert._save_snapshot(snapshot)
        alerts = report_alert._detect_observation_alerts(normalized_rows, snapshot)
        for alert in alerts:
            alert["reportBatchId"] = resolved_batch
            alert["businessDataVersion"] = resolved_data_version
            report_alert._save_alert_event(alert)
        return {
            "version": V215_VERSION,
            "datasetName": dataset,
            "dataVersion": resolved_data_version,
            "businessDataVersion": resolved_data_version,
            "reportBatchId": resolved_batch,
            "rowCount": len(normalized_rows),
            "alertCount": len(alerts),
            "taggedAlertCount": len(alerts),
            "createdTaskCount": 0,
            "autoCreateTasksRequested": bool(auto_create_tasks),
            "legacyGovernanceDependencyRemoved": True,
            "taskCreation": "disabled_in_report_alert_layer",
            "alerts": alerts,
            "summary": report_alert.get_v3_dashboard_summary(),
            "rule": "V21.5 one upload owns one business dataVersion; datasets are fact namespaces and do not create independent pipeline versions.",
        }

    def confirm_report_import_v215(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        batch = _new_batch()
        token = _BATCH_CONTEXT.set(batch)
        try:
            result = original_confirm_import(*args, **kwargs)
        finally:
            _BATCH_CONTEXT.reset(token)
        result["version"] = V215_VERSION
        result["reportBatchId"] = batch["reportBatchId"]
        result["businessDataVersion"] = batch["businessDataVersion"]
        result["dataVersion"] = batch["businessDataVersion"]
        result["batchBoundary"] = "one_uploaded_file"
        for item in result.get("results") or []:
            if isinstance(item, dict):
                item["reportBatchId"] = batch["reportBatchId"]
                item["businessDataVersion"] = batch["businessDataVersion"]
                item["dataVersion"] = batch["businessDataVersion"]
        result["message"] = "导入完成：一个完整报表批次已按内部事实命名空间写入，并只启动一个业务版本流水线。"
        return result

    def dataset_rows_v215(
        dataset_name: str | None = None,
        user_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        rows = original_dataset_rows(dataset_name, user_id)
        selected = _PROJECTION_VERSION_CONTEXT.get() or latest_business_data_version()
        if not selected:
            return rows
        return [
            row
            for row in rows
            if str(row.get("dataVersion") or "") == str(selected)
        ]

    def materialize_product_snapshot_v215(
        data_version: str | None = None,
        *,
        user_id: str | None = None,
        force: bool = True,
    ) -> Dict[str, Any]:
        resolved = data_version or latest_business_data_version()
        token = _PROJECTION_VERSION_CONTEXT.set(resolved)
        try:
            result = original_materialize_product(
                data_version=resolved,
                user_id=user_id,
                force=force,
            )
        finally:
            _PROJECTION_VERSION_CONTEXT.reset(token)
        batch_id = _batch_for_data_version(connect, loads, resolved)
        snapshot = product_snapshot.get_product_snapshot(resolved)
        if snapshot:
            snapshot["version"] = V215_VERSION
            snapshot["reportBatchId"] = batch_id
            snapshot["businessDataVersion"] = resolved
            snapshot["source"] = "v21_5_business_version_scoped_projection"
            snapshot["rule"] = "Current product facts read one business dataVersion only; history never overwrites current metrics."
            with connect() as conn:
                conn.execute(
                    "UPDATE system_product_snapshots_v14 SET payload=?,updated_at=? WHERE snapshot_id=?",
                    (dumps(snapshot), _now_iso(), snapshot["snapshotId"]),
                )
                conn.commit()
        return {
            **result,
            "version": V215_VERSION,
            "dataVersion": resolved,
            "businessDataVersion": resolved,
            "reportBatchId": batch_id,
        }

    def materialize_signal_snapshot_v215(
        data_version: str | None = None,
        *,
        user_id: str | None = None,
        force: bool = True,
    ) -> Dict[str, Any]:
        resolved = data_version or latest_business_data_version()
        result = original_materialize_signal(
            data_version=resolved,
            user_id=user_id,
            force=force,
        )
        current = product_snapshot.get_product_snapshot(resolved) or {}
        raw_history = product_snapshot.product_snapshot_history(resolved, limit=90)
        comparable, baseline = signal_snapshot._comparable_history(current, raw_history)
        history = comparable[:30]
        current_index = _index_products(current)
        history_indexes = [_index_products(snapshot) for snapshot in history]
        packages = result.get("productSignalPackages") or result.get("signals") or []
        for package in packages:
            if not isinstance(package, dict):
                continue
            key = str(package.get("entityId") or "")
            current_item = current_index.get(key)
            if current_item is None:
                product_id = str(package.get("productId") or "")
                current_item = next(
                    (
                        item
                        for item in current_index.values()
                        if str(item.get("productId") or "") == product_id
                        and str(item.get("storeId") or "") == str(package.get("storeId") or "")
                    ),
                    None,
                )
            if not current_item:
                continue
            history_items = [
                item
                for index in history_indexes
                if (item := index.get(_object_key(current_item))) is not None
            ]
            cross = build_cross_validation(current_item, history_items)
            package["crossValidation"] = cross
            package["timeSeriesFeatures"] = cross["timeSeriesFeatures"]
            package["operatingHypotheses"] = cross["hypotheses"]
            package["operatingDecision"] = cross["decision"]
            package["signalStrength"] = (
                "high"
                if cross["decision"].get("status") == "confirmed"
                and int(cross["decision"].get("confidence") or 0) >= 75
                else "medium"
                if cross["decision"].get("status") == "confirmed"
                else "low"
                if cross["decision"].get("status") in {"conflict", "buffered"}
                else "normal"
            )
            agent_package = package.get("agentProductSnapshotPackage")
            if isinstance(agent_package, dict):
                agent_package["crossValidation"] = cross
                agent_package["timeSeriesFeatures"] = cross["timeSeriesFeatures"]
                agent_package["operatingDecision"] = cross["decision"]

        result["version"] = V215_VERSION
        result["dataVersion"] = resolved
        result["businessDataVersion"] = resolved
        result["reportBatchId"] = _batch_for_data_version(connect, loads, resolved)
        result["baseline"] = baseline
        result["baselineNoPrevious"] = bool(baseline.get("baselineNoPrevious"))
        result["windowPolicy"] = {
            "recentDirectReports": 5,
            "mediumTrendReports": 10,
            "longTrendReports": 30,
            "historyCandidateLimit": 90,
            "timeBasis": "report_business_date_when_available",
        }
        result["signals"] = packages
        result["productSignalPackages"] = packages
        result["rule"] = "V21.5 current facts + recent5 direct comparison + 10/30 derived trend overlays + linked-metric conflict validation."
        snapshot_id = result.get("signalSnapshotId") or signal_snapshot.signal_snapshot_id_for(resolved)
        with connect() as conn:
            conn.execute(
                """
                UPDATE product_signal_snapshots_v14
                SET payload=?,signal_count=?,updated_at=?
                WHERE signal_snapshot_id=?
                """,
                (dumps(result), len(packages), _now_iso(), snapshot_id),
            )
            conn.commit()
        return result

    def read_pipeline_live_v215(
        data_version: str | None = None,
        *,
        limit: int = 80,
    ) -> Dict[str, Any]:
        result = original_live_reader(data_version=data_version, limit=limit)
        resolved = result.get("dataVersion") or data_version
        stage_counts = result.get("stageCounts") or {}
        observed = sum(
            int(value or 0)
            for key, value in stage_counts.items()
            if str(key).startswith("observed_soft_gate:")
        )
        if result.get("baselineOnly") and observed:
            result["baselineOnly"] = False
            result["observationOnly"] = True
            result["flowStatus"] = "completed"
            result["headline"] = f"{int(result.get('summary', {}).get('productCount') or observed)}个商品已完成对比，本轮均进入观察沉淀"
            summary = result.setdefault("summary", {})
            summary["baselineEstablished"] = 0
            summary["observedDeposited"] = observed
            summary["signalAdmitted"] = 0
            for stage in result.get("stages") or []:
                if stage.get("node") == "信号引擎":
                    stage.update(
                        {
                            "total": observed,
                            "completed": observed,
                            "observed": observed,
                            "currentCount": observed,
                            "status": "completed",
                        }
                    )
        else:
            result["observationOnly"] = False
        result["version"] = V215_VERSION
        result["businessDataVersion"] = resolved
        result["reportBatchId"] = _batch_for_data_version(connect, loads, resolved)
        result["selectionRule"] = "latest imported business dataVersion, never latest pipeline item update time"
        return result

    report_alert.import_report_dataset = import_report_dataset_v215
    report_schema.import_report_dataset = import_report_dataset_v215
    import_rows.save_import_rows = save_import_rows_v215
    report_schema.save_import_rows = save_import_rows_v215
    report_schema.confirm_report_import = confirm_report_import_v215

    projection.dataset_rows = dataset_rows_v215
    product_snapshot.materialize_system_product_snapshot = materialize_product_snapshot_v215
    signal_snapshot.materialize_system_product_snapshot = materialize_product_snapshot_v215
    signal_snapshot.materialize_product_signal_snapshot = materialize_signal_snapshot_v215

    admission.materialize_product_signal_snapshot = materialize_signal_snapshot_v215
    admission.score_signal = lambda signal: score_cross_validated_signal(
        {**signal, "payload": admission._payload(signal)}, original_score
    )

    live_model._latest_data_version = latest_business_data_version
    live_model._read_pipeline_live_model = read_pipeline_live_v215

    report_alert.REPORT_ALERT_SERVICE_VERSION = V215_VERSION
    report_schema.SCHEMA_VERSION = V215_VERSION
    import_rows.IMPORT_ROW_STORE_VERSION = V215_VERSION
    projection.PROJECTION_VERSION = V215_VERSION
    product_snapshot.SYSTEM_PRODUCT_SNAPSHOT_VERSION = V215_VERSION
    signal_snapshot.PRODUCT_SIGNAL_SNAPSHOT_VERSION = V215_VERSION
    admission.PRODUCT_SIGNAL_ADMISSION_VERSION = V215_VERSION
    live_model.PIPELINE_LIVE_READ_MODEL_VERSION = V215_VERSION

    _INSTALLED = True


__all__ = [
    "V215_VERSION",
    "build_time_series_features",
    "evaluate_hypotheses",
    "build_cross_validation",
    "score_cross_validated_signal",
    "install_v215_runtime",
]
