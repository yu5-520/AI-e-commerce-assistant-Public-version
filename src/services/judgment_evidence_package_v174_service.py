"""V18.5 judgment evidence package service.

The product judgment Agent outputs structured operating judgments. This station
joins those judgments back to the fullProductBundle evidence and expands the
operating graph strong-linked metric route.

V18.5 adds:
- allMetricChanges: every available metric delta from the bundle;
- correlatedMetricChanges: metrics on the selected operating graph route;
- operatingGraphRoute: coverage rate, route strength and recommended task type.

The package station still does not use package-count/fullProductBundle-count as a
hard gate. The fullProductBundle count is the evidence universe, not the expected
judgment count.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import src.services.dual_agent_product_task_service as base
from src.repositories.sqlite_repository import connect, loads
from src.services.agent_budget_ledger_service import read_agent_budget_summary
from src.services.metric_trigger_expansion_v171_service import (
    delete_version_packages,
    generate_metric_trigger_scene_packages,
    is_first_report_baseline,
)
from src.services.operating_graph_route_v185_service import attach_route_to_package
from src.services.product_signal_snapshot_v164_service import get_product_signal_snapshot
from src.services.signal_pool_service import list_signals
from src.services.task_generation_run_service import record_task_generation_run

JUDGMENT_EVIDENCE_PACKAGE_VERSION = "18.5"
CORE_EVIDENCE_METRICS = [
    "paymentAmount",
    "gmv",
    "roi",
    "roas",
    "adSpend",
    "refundRate",
    "afterSalesRate",
    "refundOrderCount",
    "refundAmount",
    "inventory",
    "stock",
    "availableDays",
    "conversionRate",
    "grossMargin",
    "clickRate",
    "organicVisitors",
    "paidVisitors",
    "visitorCount",
]


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


def _load_saved_packages(data_version: str | None) -> List[Dict[str, Any]]:
    base.ensure_dual_agent_tables()
    with connect() as conn:
        if not _table(conn, "product_judgment_packages_v15"):
            return []
        if data_version:
            rows = conn.execute(
                "SELECT payload FROM product_judgment_packages_v15 WHERE data_version = ? ORDER BY task_candidate_allowed DESC, created_at ASC",
                (data_version,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT payload FROM product_judgment_packages_v15 ORDER BY created_at DESC").fetchall()
    return [_load(row["payload"]) for row in rows]


def _bundle_list(snapshot: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not snapshot:
        return []
    bundles = snapshot.get("productSignalPackages") or snapshot.get("signals") or snapshot.get("products") or []
    return [item for item in bundles if isinstance(item, dict)] if isinstance(bundles, list) else []


def _profile(bundle: Dict[str, Any]) -> Dict[str, Any]:
    value = bundle.get("profileLayer") or bundle.get("productProfileSnapshot") or {}
    return value if isinstance(value, dict) else {}


def _metric(bundle: Dict[str, Any]) -> Dict[str, Any]:
    value = bundle.get("metricLayer") or bundle.get("productMetricSnapshot") or {}
    return value if isinstance(value, dict) else {}


def _snapshot_layer(bundle: Dict[str, Any]) -> Dict[str, Any]:
    value = bundle.get("snapshotLayer") or {}
    return value if isinstance(value, dict) else {}


def _product_id(bundle: Dict[str, Any]) -> str:
    profile = _profile(bundle)
    return str(bundle.get("productId") or bundle.get("entityId") or profile.get("productId") or "")


def _store_id(bundle: Dict[str, Any]) -> str:
    profile = _profile(bundle)
    return str(bundle.get("storeId") or profile.get("storeId") or "GLOBAL")


def _index_bundles(bundles: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for bundle in bundles:
        product_id = _product_id(bundle)
        if not product_id:
            continue
        store_id = _store_id(bundle)
        index[(store_id, product_id)] = bundle
        index[("*", product_id)] = bundle
    return index


def _field_signal_map(bundle: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    snapshot = _snapshot_layer(bundle)
    values = snapshot.get("fieldSignals") or bundle.get("fieldSignals") or []
    if not isinstance(values, list):
        values = []
    result: Dict[str, Dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        metric_code = str(item.get("metricCode") or "").strip()
        if metric_code:
            result[metric_code] = item
    return result


def _metric_evidence(bundle: Dict[str, Any], trigger_metric: str | None) -> Dict[str, Any]:
    metric = _metric(bundle)
    field_map = _field_signal_map(bundle)
    ordered = []
    if trigger_metric:
        ordered.append(trigger_metric)
    ordered.extend(CORE_EVIDENCE_METRICS)
    evidence: Dict[str, Any] = {}
    for metric_code in dict.fromkeys(ordered):
        signal = field_map.get(metric_code) or {}
        current = signal.get("latest") if signal else metric.get(metric_code)
        if current is None and metric_code in metric:
            current = metric.get(metric_code)
        previous = signal.get("previous")
        delta = signal.get("changeVsPrevious") if "changeVsPrevious" in signal else signal.get("delta")
        change_rate = signal.get("changeRate") or signal.get("changeVsPreviousRate") or signal.get("deltaRate")
        if current is None and previous is None and delta is None and metric_code not in metric:
            continue
        evidence[metric_code] = {
            "current": current,
            "previous": previous,
            "changeVsPrevious": delta,
            "changeRate": change_rate,
            "signalStrength": signal.get("signalStrength"),
            "signalType": signal.get("signalType"),
            "windows": signal.get("windows"),
        }
    return evidence


def _compact_bundle_evidence(bundle: Dict[str, Any] | None, package: Dict[str, Any]) -> Dict[str, Any]:
    trigger = package.get("triggerMetric") or package.get("primaryRisk") or ((package.get("sceneJudgmentPackage") or {}).get("triggerMetric") if isinstance(package.get("sceneJudgmentPackage"), dict) else None)
    if not bundle:
        return {
            "version": JUDGMENT_EVIDENCE_PACKAGE_VERSION,
            "evidenceJoinStatus": "missing_full_product_bundle",
            "triggerMetric": trigger,
            "summary": "未找到同 dataVersion/storeId/productId 的 fullProductBundle；保留判断，但标记证据挂接缺口。",
        }
    profile = _profile(bundle)
    metric = _metric(bundle)
    snapshot = _snapshot_layer(bundle)
    cross = bundle.get("crossValidation") if isinstance(bundle.get("crossValidation"), dict) else {}
    evidence = {
        "version": JUDGMENT_EVIDENCE_PACKAGE_VERSION,
        "evidenceRole": "task_mapping_sop_context",
        "evidenceJoinStatus": "joined",
        "productId": _product_id(bundle) or package.get("productId"),
        "storeId": _store_id(bundle) or package.get("storeId"),
        "title": profile.get("title") or profile.get("shortName") or bundle.get("title"),
        "platform": profile.get("platform") or bundle.get("platform"),
        "verticalCategory": profile.get("verticalCategory") or bundle.get("verticalCategory"),
        "metricDate": metric.get("metricDate") or profile.get("metricDate") or bundle.get("metricDate"),
        "triggerMetric": trigger,
        "metricEvidence": _metric_evidence(bundle, trigger),
        "trendWindows": snapshot.get("trendWindows") or bundle.get("trendWindows"),
        "factLayerValidation": bundle.get("factLayerValidation") or metric.get("factLayerValidation"),
        "crossValidation": cross,
        "sourceDataVersions": cross.get("sourceDataVersions") or [],
        "summary": "fullProductBundle 在 V18.5 中作为 SOP 解释、强关联路线、证据支撑、复盘指标和运营理解上下文。",
    }
    return attach_route_to_package(package, evidence)


def _enrich_packages(packages: List[Dict[str, Any]], bundles: List[Dict[str, Any]], *, source_mode: str) -> List[Dict[str, Any]]:
    index = _index_bundles(bundles)
    full_bundle_count = len(bundles)
    enriched: List[Dict[str, Any]] = []
    for package in packages:
        store_id = str(package.get("storeId") or "GLOBAL")
        product_id = str(package.get("productId") or "")
        bundle = index.get((store_id, product_id)) or index.get(("*", product_id))
        evidence = _compact_bundle_evidence(bundle, package)
        item = dict(package)
        scene_package = item.get("sceneJudgmentPackage") if isinstance(item.get("sceneJudgmentPackage"), dict) else {}
        scene_package = dict(scene_package)
        scene_package["fullProductBundleEvidenceRef"] = f"full_product_bundle:{item.get('dataVersion') or 'latest'}:{store_id}:{product_id}"
        scene_package["fullProductBundleEvidenceStatus"] = evidence.get("evidenceJoinStatus")
        scene_package["operatingGraphRoute"] = evidence.get("operatingGraphRoute") or {}
        scene_package["strongLinkedMetricSupport"] = evidence.get("correlatedMetricChanges") or []
        item.update({
            "version": JUDGMENT_EVIDENCE_PACKAGE_VERSION,
            "packageMode": "judgment_plus_operating_graph_route_evidence",
            "sourceMode": source_mode,
            "expectedPackageCountSource": "agent_or_graph_candidate_count",
            "fullProductBundleCount": full_bundle_count,
            "evidenceJoinStatus": evidence.get("evidenceJoinStatus"),
            "fullProductBundleEvidence": evidence,
            "operatingGraphRoute": evidence.get("operatingGraphRoute") or {},
            "allMetricChanges": evidence.get("allMetricChanges") or [],
            "correlatedMetricChanges": evidence.get("correlatedMetricChanges") or [],
            "dynamicMetricChanges": evidence.get("dynamicMetricChanges") or [],
            "routeCoverageRate": evidence.get("routeCoverageRate"),
            "routeSignalStrength": evidence.get("routeSignalStrength"),
            "recommendedTaskType": evidence.get("recommendedTaskType"),
            "sceneJudgmentPackage": scene_package,
            "taskMappingContext": {
                "inputContract": "structured_judgment + fullProductBundleEvidence + operatingGraphRoute + RAG permission context",
                "structuredJudgmentRole": "decide SOP direction and scenario",
                "fullProductBundleEvidenceRole": "explain why the SOP is needed and provide metrics for operator-readable execution",
                "operatingGraphRouteRole": "calculate strong-linked metric route coverage and suggest action strength",
                "allowedForTaskMapping": bool(item.get("taskCandidateAllowed") or item.get("candidateSignal")),
            },
            "rule": "V18.5: package station joins structured judgment to fullProductBundle and expands operating graph route coverage; 80% available strong-linked movement is enough for strong actions.",
        })
        enriched.append(item)
    return enriched


def _candidate_count(packages: List[Dict[str, Any]]) -> int:
    return sum(1 for item in packages if item.get("taskCandidateAllowed") or item.get("candidateSignal"))


def _judgment_count(packages: List[Dict[str, Any]]) -> int:
    return sum(int(item.get("judgmentCount") or 1) for item in packages)


def _signal_total(data_version: str | None, fallback: int = 0) -> int:
    try:
        signals = list_signals(data_version=data_version, status=None, limit=1000).get("signals") or []
        return len(signals) or fallback
    except Exception:
        return fallback


def _record_run(data_version: str | None, *, full_bundle_count: int, packages: List[Dict[str, Any]], identity_gap_count: int, zero_reason: str) -> Dict[str, Any]:
    budget = read_agent_budget_summary(data_version=data_version)
    return record_task_generation_run(
        data_version=data_version,
        input_bundle_count=full_bundle_count,
        agent_judgment_count=_judgment_count(packages),
        product_judgment_package_count=len(packages),
        identity_gap_count=identity_gap_count,
        task_decision_count=0,
        by_decision={},
        streamed_task_snapshot_count=0,
        task_pool_created_count=0,
        skipped_formal_count=0,
        zero_task_reasons=[zero_reason],
        agent1_api_call_count=int((budget.get("productJudgmentProvider") or {}).get("actualCalls") or 0),
        rag_retrieval_count=0,
        api_budget_violation=bool(budget.get("budgetViolation")),
        agent_budget_summary=budget,
        total_agent_call_count=int(budget.get("totalAgentCalls") or 0),
        total_agent_budget=int(budget.get("totalAgentBudget") or 8),
        source="v18_5_judgment_evidence_package_station" if packages else "v18_5_completed_no_signal",
    )


def product_judgment_package_station_v174(data_version: str | None, **_: Any) -> Dict[str, Any]:
    base.ensure_dual_agent_tables()
    baseline = is_first_report_baseline(data_version)
    snapshot = get_product_signal_snapshot(data_version)
    bundles = _bundle_list(snapshot)
    full_bundle_count = len(bundles) or int((snapshot or {}).get("productSignalPackageCount") or (snapshot or {}).get("productSignalCount") or 0)

    if baseline.get("isFirstReportBaseline"):
        delete_version_packages(data_version)
        run = record_task_generation_run(
            data_version=data_version,
            input_bundle_count=full_bundle_count,
            agent_judgment_count=0,
            product_judgment_package_count=0,
            identity_gap_count=0,
            task_decision_count=0,
            by_decision={},
            streamed_task_snapshot_count=0,
            task_pool_created_count=0,
            skipped_formal_count=0,
            zero_task_reasons=["首份报表仅建立商品与指标基线，不生成变化任务。"],
            source="v18_5_first_report_baseline",
        )
        return {
            "version": JUDGMENT_EVIDENCE_PACKAGE_VERSION,
            "stationId": "product_judgment_package_station",
            "dataVersion": data_version,
            "baselineMode": "first_report",
            "baselineNoPrevious": True,
            "packageMode": "baseline_no_task",
            "inputBundleCount": full_bundle_count,
            "fullProductBundleCount": full_bundle_count,
            "expectedPackageCount": 0,
            "productJudgmentPackageCount": 0,
            "candidatePackageCount": 0,
            "coverageStatus": "baseline",
            "coverageRate": 1.0 if full_bundle_count else 0,
            "taskGenerationRun": run,
            "productJudgmentPackageRef": f"baseline_no_task_package:{data_version or 'latest'}",
            "outputRef": f"baseline_no_task_package:{data_version or 'latest'}",
            "rule": "V18.5 first report baseline: no previous snapshot, so judgment evidence package is skipped and the run is closed as baseline_completed.",
        }

    raw_packages, identity_gaps = base._package_product_judgments(data_version)
    source_mode = "agent_judgment_join"
    packages = raw_packages
    expansion: Dict[str, Any] = {}

    if not packages:
        expansion = generate_metric_trigger_scene_packages(data_version=data_version, replace_existing=True)
        packages = expansion.get("packages") or _load_saved_packages(data_version)
        source_mode = "graph_metric_trigger_join"

    enriched = _enrich_packages(packages, bundles, source_mode=source_mode)
    if enriched or packages:
        delete_version_packages(data_version)
        base._save_packages(enriched)

    candidate_count = _candidate_count(enriched)
    missing_count = sum(1 for item in enriched if item.get("evidenceJoinStatus") != "joined")
    expected_count = len(enriched)
    if not enriched:
        status = "completed_no_signal"
        zero_reason = f"V18.5证据合包完成：fullProductBundle={full_bundle_count}，运营图谱/判断Agent未输出有效判断包，本轮无动作任务。"
    elif missing_count:
        status = "attention_evidence_join_gap"
        zero_reason = f"V18.5证据合包完成但存在 {missing_count} 个 fullProductBundle 证据挂接缺口。"
    else:
        status = "evidence_join_completed"
        zero_reason = f"V18.5证据合包完成：fullProductBundle={full_bundle_count}，有效判断包={len(enriched)}，候选任务包={candidate_count}，已展开运营图谱强关联路线。"

    run = _record_run(
        data_version,
        full_bundle_count=full_bundle_count,
        packages=enriched,
        identity_gap_count=len(identity_gaps),
        zero_reason=zero_reason,
    )
    route_strengths: Dict[str, int] = {}
    for item in enriched:
        strength = str(item.get("routeSignalStrength") or (item.get("operatingGraphRoute") or {}).get("routeSignalStrength") or "unknown")
        route_strengths[strength] = route_strengths.get(strength, 0) + 1
    return {
        "version": JUDGMENT_EVIDENCE_PACKAGE_VERSION,
        "stationId": "product_judgment_package_station",
        "dataVersion": data_version,
        "baselineMode": "normal_delta",
        "packageMode": "judgment_plus_operating_graph_route_evidence",
        "packageCompletionStatus": status,
        "inputBundleCount": full_bundle_count,
        "fullProductBundleCount": full_bundle_count,
        "graphSignalCount": _signal_total(data_version, fallback=full_bundle_count),
        "rawAgentPackageCount": len(raw_packages),
        "metricTriggerCount": expansion.get("metricTriggerCount", 0),
        "expectedPackageCount": expected_count,
        "productJudgmentPackageCount": len(enriched),
        "candidatePackageCount": candidate_count,
        "observeOnlyPackageCount": max(0, len(enriched) - candidate_count),
        "evidenceJoinMissingCount": missing_count,
        "identityGapCount": len(identity_gaps),
        "routeStrengths": route_strengths,
        "strongRoutePackageCount": route_strengths.get("strong", 0),
        "mediumRoutePackageCount": route_strengths.get("medium", 0),
        "coverageRate": 1.0 if enriched else 0,
        "coverageStatus": "passed" if enriched else "no_signal",
        "productJudgmentPackageRef": f"judgment_evidence_package:{data_version or 'latest'}",
        "outputRef": f"judgment_evidence_package:{data_version or 'latest'}",
        "taskGenerationRun": run,
        "packages": enriched[:20],
        "rule": "V18.5: 合包站展开运营图谱强关联指标路线；80%可用强关联指标明显波动即可支撑强运营动作，缺少个别指标不再默认降级观察。",
    }
