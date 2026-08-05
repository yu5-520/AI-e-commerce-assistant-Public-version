"""V22.2.5 public station adapter.

Every business station has one real implementation. Unknown stations fail closed.
Diagnostic simulation is isolated and never recorded as business output. Bundle
and signal-admission stations exchange immutable Artifact references rather than
reloading a legacy payload or Signal Pool.
"""
from __future__ import annotations

from typing import Any, Dict

from src.runtime_version import VERSION
from src.services.agent_pipeline_governance_v213_service import normalize_admission_limits
from src.services.pipeline_item_service import build_item_envelope

STATION_ADAPTER_VERSION = VERSION
AGENT_PIPELINE_GOVERNANCE_VERSION = VERSION
PIPELINE_ITEM_VERSION = VERSION


def _count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in (
            "completedItemCount",
            "createdItemCount",
            "actionPlanCount",
            "taskDecisionCount",
            "createdTaskCount",
            "judgmentCount",
            "admittedSignalCount",
            "observedSignalCount",
            "count",
            "rowCount",
        ):
            raw = value.get(key)
            if isinstance(raw, int):
                return raw
            if isinstance(raw, list):
                return len(raw)
    return 0


def _envelope(
    body: Dict[str, Any],
    *,
    data_version: str | None = None,
    station_id: str | None = None,
) -> Dict[str, Any]:
    raw = body.get("pipelineItemEnvelope") if isinstance(body.get("pipelineItemEnvelope"), dict) else {}
    return build_item_envelope(
        data_version=raw.get("dataVersion") or data_version or body.get("dataVersion") or body.get("data_version"),
        item_id=raw.get("itemId") or body.get("itemId"),
        product_id=raw.get("productId") or body.get("productId"),
        store_id=raw.get("storeId") or body.get("storeId"),
        signal_id=raw.get("signalId") or body.get("signalId"),
        package_id=raw.get("packageId") or body.get("packageId"),
        decision_id=raw.get("decisionId") or body.get("decisionId"),
        task_id=raw.get("taskId") or body.get("taskId"),
        action_family=raw.get("actionFamily") or body.get("actionFamily"),
        route=raw.get("route") or body.get("route"),
        input_ref=raw.get("inputRef") or body.get("inputRef"),
        output_ref=raw.get("outputRef") or body.get("outputRef"),
        stage=raw.get("stage") or station_id,
        artifact_refs=raw.get("artifactRefs") if isinstance(raw.get("artifactRefs"), dict) else {},
    )


def _kwargs(body: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(body or {})
    for key in (
        "dataVersion",
        "data_version",
        "userId",
        "user_id",
        "pipelineItemEnvelope",
    ):
        out.pop(key, None)
    if "maxSignals" in out and "max_signals" not in out:
        out["max_signals"] = out.pop("maxSignals")
    if "minAdmitted" in out and "min_admitted" not in out:
        out["min_admitted"] = out.pop("minAdmitted")
    if "maxAdmitted" in out and "max_admitted" not in out:
        out["max_admitted"] = out.pop("maxAdmitted")
    if "fullProductBundleRef" in out and "full_product_bundle_ref" not in out:
        out["full_product_bundle_ref"] = out.pop("fullProductBundleRef")
    if "validatedBundleRef" in out and "validated_bundle_ref" not in out:
        out["validated_bundle_ref"] = out.pop("validatedBundleRef")
    return out


def _alignment(
    station_id: str,
    *,
    data_version: str | None,
    user_id: str | None,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    if station_id in {"full_product_bundle_station", "bundle_validation_station"}:
        import src.services.station_alignment_v225_service as alignment
    else:
        import src.services.station_alignment_v165_service as alignment

    return getattr(alignment, station_id)(
        data_version=data_version,
        user_id=user_id,
        **_kwargs(body),
    )


def _refresh(
    station_id: str,
    data_version: str | None,
    output: Dict[str, Any],
) -> Dict[str, Any] | None:
    refreshable = {
        "full_product_bundle_station",
        "product_signal_admission_station",
        "product_judgment_agent_station",
        "task_mapping_agent_station",
        "task_pool_admission_station",
    }
    if station_id not in refreshable:
        return None
    try:
        from src.services.frontend_read_model_service import (
            refresh_after_station,
            refresh_task_views,
        )

        result = refresh_after_station(
            station_id=station_id,
            data_version=data_version,
            output=output,
        )
        if station_id == "task_pool_admission_station":
            result.setdefault("updates", []).append(
                refresh_task_views(data_version=data_version)
            )
        return result
    except Exception as exc:
        return {"status": "read_model_refresh_failed", "error": str(exc)}


def simulated_station_output(
    station: Dict[str, Any],
    body: Dict[str, Any] | None = None,
    *,
    diagnostic: bool = False,
) -> Dict[str, Any]:
    body = body or {}
    data_version = body.get("dataVersion") or body.get("data_version") or ("DIAG-V22" if diagnostic else None)
    envelope = _envelope(body, data_version=data_version, station_id=station.get("stationId"))
    output_ref = f"{station.get('outputRefPrefix')}:{data_version or 'latest'}"
    return {
        "version": VERSION,
        "governanceVersion": VERSION,
        "adapterMode": "diagnostic_simulated" if diagnostic else "forbidden_contract_only",
        "stationId": station.get("stationId"),
        "stage": station.get("stage"),
        "dataVersion": data_version,
        "outputRef": output_ref,
        "pipelineItemEnvelope": {**envelope, "outputRef": output_ref},
        "isDiagnostic": diagnostic,
        "count": 1,
        "rule": "V22 simulation is diagnostic-only and cannot become business completion.",
    }


ALIGNMENT_FUNCTIONS = {
    "report_receive_station",
    "report_schema_station",
    "report_fact_station",
    "product_master_station",
    "product_metric_snapshot_station",
    "full_product_bundle_station",
    "bundle_validation_station",
    "frontend_read_model_station",
    "task_pool_acceptance_station",
}


def run_station_adapter(
    station: Dict[str, Any],
    body: Dict[str, Any] | None = None,
    *,
    diagnostic: bool = False,
) -> Dict[str, Any]:
    body = body or {}
    station_id = str(station.get("stationId") or "")
    if diagnostic:
        return simulated_station_output(station, body, diagnostic=True)

    data_version = body.get("dataVersion") or body.get("data_version")
    user_id = body.get("userId") or body.get("user_id")
    envelope = _envelope(body, data_version=data_version, station_id=station_id)
    kwargs = _kwargs(body)

    if station_id == "product_signal_admission_station":
        from src.services.artifact_signal_admission_v225_service import (
            product_signal_admission_station_v225,
        )

        limits = normalize_admission_limits(
            max_signals=int(kwargs.get("max_signals") or 160),
            min_admitted=kwargs.get("min_admitted"),
            max_admitted=kwargs.get("max_admitted"),
        )
        result = product_signal_admission_station_v225(
            data_version=data_version,
            validated_bundle_ref=kwargs.get("validated_bundle_ref")
            or body.get("validatedBundleRef")
            or envelope.get("inputRef"),
            max_signals=limits["maxSignals"],
            min_admitted=limits["minAdmitted"],
            max_admitted=limits["maxAdmitted"],
        )
        result["adapterMode"] = "v22_2_5_artifact_signal_itemization"
    elif station_id == "product_judgment_agent_station":
        from src.services.pipeline_agent1_microbatch_v20101_service import (
            run_agent1_microbatch_loop_v20101,
        )

        result = run_agent1_microbatch_loop_v20101(
            data_version=data_version,
            user_id=user_id,
            batch_size=int(body.get("agentBatchSize") or body.get("micro_batch_size") or 8),
            max_batches=1 if body.get("pipeline_stream_mode") else int(body.get("maxAgent1MicroBatches") or 20),
        )
        result["adapterMode"] = "v22_agent1_item_worker"
    elif station_id == "action_parameter_enrichment_station":
        from src.services.agent_pipeline_item_worker_v2010_service import (
            seed_action_pack_from_agent1_items,
        )

        result = seed_action_pack_from_agent1_items(
            data_version=data_version,
            batch_size=int(body.get("actionPackBatchSize") or body.get("micro_batch_size") or 8),
            source="v22_station_adapter",
        )
        result["adapterMode"] = "v22_capability_item_worker"
    elif station_id == "action_plan_judgment_agent_station":
        from src.services.pipeline_action_microbatch_v205_service import (
            run_agent2_microbatch_loop_v205,
        )

        result = run_agent2_microbatch_loop_v205(
            data_version=data_version,
            user_id=user_id,
            batch_size=int(body.get("agent2MicroBatchSize") or body.get("micro_batch_size") or 5),
            max_batches=1 if body.get("pipeline_stream_mode") else int(body.get("maxAgent2MicroBatches") or 20),
        )
        result["adapterMode"] = "v22_agent2_item_worker"
    elif station_id == "task_mapping_agent_station":
        from src.services.pipeline_sop_task_pool_v2010_service import (
            task_mapping_agent_station_v206,
        )

        result = task_mapping_agent_station_v206(
            data_version=data_version,
            userId=user_id,
            **kwargs,
        )
        result["adapterMode"] = "v22_sop_pipeline_item"
    elif station_id == "task_pool_admission_station":
        from src.services.pipeline_sop_task_pool_v2010_service import (
            task_pool_admission_station_v207,
        )

        result = task_pool_admission_station_v207(
            data_version=data_version,
            user_id=user_id,
            **kwargs,
        )
        result["adapterMode"] = "v22_task_pool_pipeline_item"
    elif station_id in ALIGNMENT_FUNCTIONS:
        result = _alignment(
            station_id,
            data_version=data_version,
            user_id=user_id,
            body=body,
        )
        result["adapterMode"] = f"v22_2_5_{station_id}"
    else:
        raise RuntimeError(f"no_real_station_adapter:{station_id}")

    result["version"] = VERSION
    result["governanceVersion"] = VERSION
    result.setdefault("stationId", station_id)
    result.setdefault("stage", station.get("stage"))
    result.setdefault("dataVersion", data_version)
    result.setdefault("outputRef", f"{station.get('outputRefPrefix')}:{data_version or 'latest'}")
    result["pipelineItemEnvelope"] = {
        **envelope,
        "outputRef": result.get("outputRef"),
        "stage": station.get("stage") or station_id,
        "version": VERSION,
    }
    result["pipelineInterfaceMode"] = "single_v22_runtime"
    refresh = _refresh(station_id, data_version, result)
    if refresh is not None:
        result["readModelRefresh"] = refresh
    result.setdefault("count", _count(result))
    return result


__all__ = [
    "STATION_ADAPTER_VERSION",
    "run_station_adapter",
    "simulated_station_output",
]
