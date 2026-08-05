"""V15.1 data-change driven product judgment wrapper.

This module patches the V15 product task pipeline so Agent1 no longer expands
every product to a fixed eight-metric judgment set. It only expands metrics that
are present in fullProductBundle.fieldSignals as changed/abnormal, plus critical
data gaps. This removes demo-like padding while preserving the existing task
package, budget ledger, and task-pool contracts.
"""

from __future__ import annotations

from typing import Any, Dict, List

import src.services.dual_agent_product_task_service as base

DUAL_AGENT_PIPELINE_VERSION = "15.1"
AGENT1_API_MODE = "local_data_change_metric_expansion_no_padding"


def _known(value: Any) -> bool:
    return value not in base.BLANK_VALUES


def _metric_layer(bundle: Dict[str, Any]) -> Dict[str, Any]:
    value = bundle.get("metricLayer")
    return value if isinstance(value, dict) else {}


def _field_signals(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = bundle.get("snapshotLayer") if isinstance(bundle.get("snapshotLayer"), dict) else {}
    signals = snapshot.get("fieldSignals") or bundle.get("fieldSignals") or []
    return signals if isinstance(signals, list) else []


def _signal_primary_metric(bundle: Dict[str, Any]) -> str:
    return str(bundle.get("metricCode") or bundle.get("primaryRisk") or "all_metrics")


def _extract_metric_codes_v151(bundle: Dict[str, Any]) -> List[str]:
    metric = _metric_layer(bundle)
    primary = _signal_primary_metric(bundle)
    ordered: List[str] = []

    for sig in _field_signals(bundle):
        code = str(sig.get("metricCode") or "")
        strength = str(sig.get("signalStrength") or "normal")
        if code and code in base.CORE_METRICS and strength in {"high", "medium", "low"}:
            ordered.append(code)

    if primary and primary in base.CORE_METRICS and primary not in ordered:
        ordered.insert(0, primary)

    for key in ["paymentAmount", "inventory", "refundRate", "roi", "roas"]:
        if key in metric and not _known(metric.get(key)):
            ordered.append(key)

    if not ordered:
        ordered.append(primary if primary and primary != "all_metrics" else "all_metrics")

    result: List[str] = []
    seen: set[str] = set()
    for key in ordered:
        if key and key not in seen:
            seen.add(key)
            result.append(key)
        if len(result) >= base.MAX_METRIC_JUDGMENTS_PER_SIGNAL:
            break
    return result


def apply_v151_patch() -> None:
    base.DUAL_AGENT_PIPELINE_VERSION = DUAL_AGENT_PIPELINE_VERSION
    base.AGENT1_API_MODE = AGENT1_API_MODE
    base._extract_metric_codes = _extract_metric_codes_v151


apply_v151_patch()

ensure_dual_agent_tables = base.ensure_dual_agent_tables
run_dual_agent_product_task_pipeline = base.run_dual_agent_product_task_pipeline
