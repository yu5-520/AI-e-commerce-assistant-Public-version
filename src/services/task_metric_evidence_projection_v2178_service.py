"""V21.7.8 frozen task metric-evidence projection.

Product detail answers "what changed for this product". Task detail must instead
answer "which frozen observations caused this task". This module reuses the
product trend observation model, freezes it at the task data version/creation
boundary, and keeps only metric codes referenced by the task's judgment,
metric digest, action parameters, Plan IR or review metrics.

The read model never treats an empty ``dynamicMetricChanges`` list as a baseline.
A formal task without a recoverable frozen evidence window is explicitly marked
``evidence_missing`` and must not be presented as executable.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

from src.services.product_trend_read_model_v217_service import (
    METRIC_DEFINITIONS,
    _snapshot_rows,
    build_product_trend_projection,
)

TASK_METRIC_EVIDENCE_PROJECTION_VERSION = "21.7.8"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}, "UNKNOWN", "未识别", "—", "未提供"):
            return value
    return None


def _normalize(value: Any) -> str:
    return "".join(
        char
        for char in _text(value).lower()
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


_CODE_ALIASES: Dict[str, set[str]] = {
    "paymentAmount": {
        "paymentamount", "gmv", "salesamount", "revenue", "支付金额", "销售额",
        "成交金额", "销售增长", "支付增长", "成交增长",
    },
    "roi": {
        "roi", "roas", "currentroi", "targetroas", "safetyroi", "投产", "投产比",
        "投放效率", "广告效率", "目标roas", "安全roi", "最低roi",
    },
    "adSpend": {
        "adspend", "spend", "cost", "广告消耗", "投放消耗", "付费消耗", "消耗增长",
    },
    "grossMargin": {"grossmargin", "margin", "毛利率", "利润率", "毛利"},
    "organicVisitors": {
        "organicvisitors", "naturaltraffic", "organictraffic", "自然访客", "自然流量",
    },
    "paidVisitors": {"paidvisitors", "付费访客", "广告访客"},
    "visitorCount": {"visitorcount", "visitors", "uv", "总访客", "访客数", "访客"},
    "clickRate": {"clickrate", "clickthroughrate", "ctr", "点击率"},
    "conversionRate": {"conversionrate", "cvr", "转化率", "支付转化率"},
    "refundRate": {"refundrate", "退款率"},
    "afterSalesRate": {"aftersalesrate", "aftersalerate", "售后率"},
    "refundAmount": {"refundamount", "退款金额"},
    "inventory": {"inventory", "stock", "可售库存", "库存数量", "库存"},
    "availableDays": {
        "availabledays", "inventorydays", "saleabledays", "sellabledays", "可售天数",
        "库存天数", "库存承接", "断货风险",
    },
}

_ALIAS_TO_CODE: Dict[str, str] = {
    _normalize(alias): code
    for code, aliases in _CODE_ALIASES.items()
    for alias in aliases | {code}
    if _normalize(alias)
}

_FAMILY_ORDER: Dict[str, List[str]] = {
    "roas_scale": ["paymentAmount", "roi", "adSpend", "availableDays", "grossMargin"],
    "roas_guard": ["roi", "adSpend", "paymentAmount", "availableDays", "grossMargin"],
    "title_image_test": ["clickRate", "visitorCount", "conversionRate", "paymentAmount"],
    "platform_activity": ["organicVisitors", "paymentAmount", "grossMargin", "availableDays"],
    "activity_apply": ["organicVisitors", "paymentAmount", "grossMargin", "availableDays"],
    "conversion_repair": ["conversionRate", "clickRate", "paymentAmount", "refundRate"],
    "service_repair": ["refundRate", "afterSalesRate", "refundAmount", "conversionRate"],
    "similar_product_test": ["conversionRate", "clickRate", "paymentAmount"],
}

_ROLE_USAGE: Dict[str, Dict[str, tuple[str, str]]] = {
    "roas_scale": {
        "paymentAmount": ("primary_signal", "验证销售增长是否具备放量基础"),
        "roi": ("risk_boundary", "判断投放效率和最低安全线"),
        "adSpend": ("supporting_signal", "核对成本增速与支付增长是否匹配"),
        "availableDays": ("capacity_boundary", "确认库存能够承接测试周期"),
        "grossMargin": ("permission_boundary", "校验放量后的利润空间"),
    },
    "roas_guard": {
        "roi": ("primary_signal", "确认低效投放是否触发收缩"),
        "adSpend": ("cost_signal", "判断消耗是否继续扩大损失"),
        "paymentAmount": ("result_signal", "核对收缩前后的支付结果"),
        "availableDays": ("capacity_context", "记录库存容量但不单独触发断流"),
        "grossMargin": ("permission_boundary", "校验保本边界"),
    },
    "title_image_test": {
        "clickRate": ("primary_signal", "确认标题主图是否需要提高点击承接"),
        "visitorCount": ("traffic_context", "核对测试入口的流量规模"),
        "conversionRate": ("risk_boundary", "防止只提高点击而损伤转化"),
        "paymentAmount": ("result_signal", "验证创意测试是否带来经营结果"),
    },
    "platform_activity": {
        "organicVisitors": ("primary_signal", "判断自然流量是否值得活动承接"),
        "paymentAmount": ("result_signal", "验证活动对支付增长的贡献"),
        "grossMargin": ("permission_boundary", "校验优惠后的利润空间"),
        "availableDays": ("capacity_boundary", "确认库存能够覆盖活动周期"),
    },
    "activity_apply": {
        "organicVisitors": ("primary_signal", "判断自然流量是否值得活动承接"),
        "paymentAmount": ("result_signal", "验证活动对支付增长的贡献"),
        "grossMargin": ("permission_boundary", "校验优惠后的利润空间"),
        "availableDays": ("capacity_boundary", "确认库存能够覆盖活动周期"),
    },
    "conversion_repair": {
        "conversionRate": ("primary_signal", "定位支付转化下降"),
        "clickRate": ("upstream_signal", "区分点击不足与承接损失"),
        "paymentAmount": ("result_signal", "验证修复后的经营结果"),
        "refundRate": ("risk_boundary", "排除售后因素对转化的干扰"),
    },
    "service_repair": {
        "refundRate": ("primary_signal", "定位退款风险"),
        "afterSalesRate": ("primary_signal", "定位售后问题规模"),
        "refundAmount": ("cost_signal", "量化售后损失"),
        "conversionRate": ("impact_signal", "评估售后问题对转化的影响"),
    },
    "similar_product_test": {
        "conversionRate": ("primary_signal", "比较同类商品转化差异"),
        "clickRate": ("supporting_signal", "比较流量承接差异"),
        "paymentAmount": ("result_signal", "比较最终支付结果"),
    },
}


_HISTORICAL_TERMS = re.compile(r"环比|同比|斜率|波动率|连续\d*期|历史趋势|趋势窗口", re.I)


def _walk(value: Any, *, depth: int = 0) -> Iterable[tuple[str, Any]]:
    if depth > 9:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value[:60]:
            yield from _walk(child, depth=depth + 1)


def _code_from_token(value: Any) -> str | None:
    normalized = _normalize(value)
    if not normalized:
        return None
    direct = _ALIAS_TO_CODE.get(normalized)
    if direct:
        return direct
    for alias, code in _ALIAS_TO_CODE.items():
        if len(alias) >= 2 and alias in normalized:
            return code
    return None


def _referenced_codes(source: Dict[str, Any]) -> List[str]:
    found: List[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        code = _code_from_token(value)
        if code and code not in seen:
            seen.add(code)
            found.append(code)

    for key, value in _walk(source):
        add(key)
        if key in {
            "metric", "metricCode", "metricName", "code", "name", "label",
            "reviewMetric", "primaryBusinessSignal", "primaryOperatingGap",
            "summary", "fact", "reason", "finding", "text", "value",
        } or isinstance(value, str):
            add(value)
    return found


def _action_family(task: Dict[str, Any]) -> str:
    report = _dict(task.get("taskDetailReport"))
    plan = _dict(_first(task.get("taskPlan"), report.get("taskPlan"), task.get("taskCard"), {}))
    agent2 = _dict(_first(task.get("agent2ActionPlan"), report.get("agent2ActionPlan"), plan.get("agent2ActionPlan"), {}))
    contract = _dict(_first(task.get("activeActionContract"), report.get("activeActionContract"), plan.get("activeActionContract"), {}))
    return _text(
        _first(
            contract.get("activeActionFamily"),
            agent2.get("actionFamily"),
            task.get("actionFamily"),
            task.get("selectedActionFamily"),
            plan.get("selectedActionFamily"),
            plan.get("actionFamily"),
            "",
        )
    )


def _product_identity(task: Dict[str, Any]) -> Dict[str, Any]:
    report = _dict(task.get("taskDetailReport"))
    plan = _dict(_first(task.get("taskPlan"), report.get("taskPlan"), task.get("taskCard"), {}))
    cards = _list(task.get("productActionCards")) or _list(report.get("productActionCards"))
    return _dict(
        _first(
            task.get("productIdentity"),
            report.get("productIdentity"),
            plan.get("productIdentity"),
            cards[0] if cards else {},
            {},
        )
    )


def _metric_digest(task: Dict[str, Any]) -> Dict[str, Any]:
    report = _dict(task.get("taskDetailReport"))
    plan = _dict(_first(task.get("taskPlan"), report.get("taskPlan"), task.get("taskCard"), {}))
    package = _dict(task.get("productJudgmentPackage"))
    return _dict(
        _first(
            task.get("metricDigest"),
            report.get("metricDigest"),
            plan.get("metricDigest"),
            package.get("metricDigest"),
            {},
        )
    )


def _evidence_source(task: Dict[str, Any]) -> Dict[str, Any]:
    report = _dict(task.get("taskDetailReport"))
    plan = _dict(_first(task.get("taskPlan"), report.get("taskPlan"), task.get("taskCard"), {}))
    agent2 = _dict(_first(task.get("agent2ActionPlan"), report.get("agent2ActionPlan"), plan.get("agent2ActionPlan"), {}))
    return {
        "metricDigest": _metric_digest(task),
        "dynamicMetricChanges": _first(
            task.get("dynamicMetricChanges"),
            report.get("dynamicMetricChanges"),
            _dict(task.get("systemChangePack")).get("dynamicMetricChanges"),
            [],
        ),
        "operatorJudgmentView": _first(
            task.get("operatorJudgmentView"), report.get("operatorJudgmentView"), plan.get("operatorJudgmentView"), {}
        ),
        "agentOperatingJudgment": _first(
            task.get("agentOperatingJudgment"), report.get("agentOperatingJudgment"), {}
        ),
        "agentJudgment": _first(task.get("agentJudgment"), report.get("agentJudgment"), {}),
        "actionParameterPack": _first(task.get("actionParameterPack"), plan.get("actionParameterPack"), {}),
        "operationPlan": _first(task.get("operationPlan"), report.get("operationPlan"), plan.get("operationPlan"), agent2.get("operationPlan"), {}),
        "reviewMetrics": _first(task.get("reviewMetrics"), plan.get("reviewMetrics"), agent2.get("reviewMetrics"), []),
        "decisionBranches": agent2.get("decisionBranches") or [],
    }


def _parse_time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _task_boundary(task: Dict[str, Any]) -> tuple[str, datetime | None, str]:
    report = _dict(task.get("taskDetailReport"))
    data_version = _text(
        _first(
            task.get("dataVersion"),
            task.get("workflowRunId"),
            task.get("workflow_run_id"),
            report.get("dataVersion"),
            "",
        )
    )
    raw_time = _first(
        task.get("createdAt"),
        task.get("created_at"),
        task.get("taskCreatedAt"),
        task.get("generatedAt"),
        report.get("createdAt"),
    )
    return data_version, _parse_time(raw_time), _text(raw_time)


def _snapshots_as_of_task(task: Dict[str, Any], snapshots: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [dict(item) for item in snapshots if isinstance(item, dict)]
    data_version, cutoff, _ = _task_boundary(task)

    if data_version:
        indexes = [index for index, item in enumerate(rows) if _text(item.get("dataVersion")) == data_version]
        if indexes:
            rows = rows[min(indexes):]

    if cutoff:
        filtered: List[Dict[str, Any]] = []
        for item in rows:
            stamp = _parse_time(item.get("createdAt") or item.get("updatedAt"))
            if stamp is None or stamp <= cutoff:
                filtered.append(item)
        rows = filtered
    return rows


def _metric_definition(code: str, family: str) -> Dict[str, Any] | None:
    base = next((item for item in METRIC_DEFINITIONS if item.get("code") == code), None)
    if not base:
        return None
    role, usage = _ROLE_USAGE.get(family, {}).get(
        code,
        ("supporting_signal", "作为本任务经营判断的参考指标"),
    )
    return {**base, "evidenceRole": role, "taskUsage": usage}


def _ordered_codes(codes: Sequence[str], family: str) -> List[str]:
    seen = set(codes)
    ordered = [code for code in _FAMILY_ORDER.get(family, []) if code in seen]
    ordered.extend(code for code in codes if code not in ordered)
    return ordered


def _project_for_product(
    snapshots: Sequence[Dict[str, Any]],
    identity: Dict[str, Any],
) -> Dict[str, Any]:
    candidates = [
        identity.get("productObjectId"),
        identity.get("objectId"),
        identity.get("productId"),
        identity.get("platformItemId"),
        identity.get("skuId"),
        identity.get("systemProductCode"),
    ]
    store_id = _text(identity.get("storeId")) or None
    best: Dict[str, Any] = {}
    for candidate in candidates:
        product_id = _text(candidate)
        if not product_id:
            continue
        projection = build_product_trend_projection(snapshots, product_id, store_id=store_id)
        if projection.get("ready") and len(projection.get("recentSnapshots") or []) > len(best.get("recentSnapshots") or []):
            best = projection
    return best


def _existing_projection(task: Dict[str, Any]) -> Dict[str, Any]:
    report = _dict(task.get("taskDetailReport"))
    plan = _dict(_first(task.get("taskPlan"), report.get("taskPlan"), task.get("taskCard"), {}))
    existing = _dict(
        _first(
            task.get("taskMetricEvidenceProjection"),
            report.get("taskMetricEvidenceProjection"),
            plan.get("taskMetricEvidenceProjection"),
            {},
        )
    )
    if existing.get("version") == TASK_METRIC_EVIDENCE_PROJECTION_VERSION:
        return copy.deepcopy(existing)
    return {}


def build_task_metric_evidence_projection(
    task: Dict[str, Any],
    *,
    snapshots: Sequence[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    task = dict(_dict(task))
    existing = _existing_projection(task)
    if existing:
        return existing

    family = _action_family(task)
    identity = _product_identity(task)
    source = _evidence_source(task)
    referenced = _ordered_codes(_referenced_codes(source), family)
    data_version, _, raw_boundary_time = _task_boundary(task)
    frozen_at = raw_boundary_time or _text(task.get("updatedAt") or task.get("updated_at"))

    base = {
        "version": TASK_METRIC_EVIDENCE_PROJECTION_VERSION,
        "actionFamily": family,
        "frozenAtTaskCreation": bool(data_version or raw_boundary_time),
        "frozenAt": frozen_at or None,
        "sourceDataVersion": data_version or None,
        "productId": _first(
            identity.get("productId"), identity.get("productObjectId"), identity.get("objectId"), identity.get("skuId")
        ),
        "storeId": identity.get("storeId"),
        "referencedMetricCodes": referenced,
        "metricDefinitions": [],
        "recentSnapshots": [],
        "metricTrends": {},
        "historicalEvidenceReferenced": bool(_HISTORICAL_TERMS.search(str(source))),
        "referenceWindow": {
            "snapshotCount": 0,
            "startBusinessDate": None,
            "endBusinessDate": None,
            "dataCompleteness": 0.0,
        },
        "sourceObservationIds": [],
        "source": "task_creation_frozen_product_observations",
        "readRule": "Only metrics actually referenced by this task are shown; values are frozen at the task data-version/creation boundary and never replaced by later product observations.",
    }

    if not referenced:
        return {
            **base,
            "ready": False,
            "evidenceStatus": "evidence_missing",
            "taskExecutableFromEvidence": False,
            "reason": "task_referenced_metric_codes_missing",
        }

    rows = _snapshots_as_of_task(task, snapshots if snapshots is not None else _snapshot_rows())
    trend = _project_for_product(rows, identity)
    if not trend.get("ready"):
        return {
            **base,
            "ready": False,
            "evidenceStatus": "evidence_missing",
            "taskExecutableFromEvidence": False,
            "reason": "task_product_observation_window_missing",
        }

    available_codes = {
        definition.get("code")
        for definition in trend.get("metricDefinitions") or []
        if definition.get("code")
    }
    selected_codes = [code for code in referenced if code in available_codes]
    definitions = [
        definition
        for code in selected_codes
        if (definition := _metric_definition(code, family)) is not None
    ]
    snapshots_out: List[Dict[str, Any]] = []
    for observation in trend.get("recentSnapshots") or []:
        metrics = {
            code: (observation.get("metrics") or {}).get(code)
            for code in selected_codes
            if code in (observation.get("metrics") or {})
        }
        changes = {
            code: (observation.get("changes") or {}).get(code)
            for code in selected_codes
            if code in (observation.get("changes") or {})
        }
        if not metrics:
            continue
        snapshots_out.append(
            {
                "businessDate": observation.get("businessDate"),
                "dataVersion": observation.get("dataVersion"),
                "snapshotId": observation.get("snapshotId"),
                "sourceDataVersions": observation.get("sourceDataVersions") or [],
                "metrics": metrics,
                "changes": changes,
            }
        )

    possible = max(1, len(definitions) * max(1, len(snapshots_out)))
    observed = sum(len(item.get("metrics") or {}) for item in snapshots_out)
    completeness = round(min(1.0, observed / possible), 4)
    count = len(snapshots_out)
    evidence_status = "ready" if count >= 2 and definitions else "baseline_only" if count == 1 else "evidence_missing"
    task_executable = evidence_status == "ready"
    historical = {
        code: (trend.get("metricTrends") or {}).get(code, {})
        for code in selected_codes
        if (trend.get("metricTrends") or {}).get(code)
    } if base["historicalEvidenceReferenced"] else {}

    return {
        **base,
        "ready": task_executable,
        "evidenceStatus": evidence_status,
        "taskExecutableFromEvidence": task_executable,
        "reason": None if task_executable else "formal_task_requires_at_least_two_frozen_observations",
        "referencedMetricCodes": selected_codes,
        "metricDefinitions": definitions,
        "recentSnapshots": snapshots_out,
        "metricTrends": historical,
        "referenceWindow": {
            "snapshotCount": count,
            "startBusinessDate": snapshots_out[0].get("businessDate") if snapshots_out else None,
            "endBusinessDate": snapshots_out[-1].get("businessDate") if snapshots_out else None,
            "dataCompleteness": completeness,
        },
        "sourceObservationIds": [
            item.get("snapshotId") for item in snapshots_out if item.get("snapshotId")
        ],
        "sourceMetricDigestVersion": _metric_digest(task).get("version"),
    }
