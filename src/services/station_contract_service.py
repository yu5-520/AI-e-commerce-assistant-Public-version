"""V22 strict station contracts for the single governed runtime.

Business station contracts validate the adapter's business output. Immutable
``ART-`` transport references are created by ``station_queue_service`` only after
that business contract passes, so those post-contract transport refs must never be
required as adapter output fields.
"""
from __future__ import annotations

import importlib
from typing import Any, Dict

from src.runtime_version import VERSION
from src.services.pipeline_gate_service import record_stage_gate, stage_summary
from src.services.pipeline_item_service import build_item_envelope
from src.services.station_adapter_service import run_station_adapter
from src.services.station_registry_service import get_station, list_stations

STATION_CONTRACT_VERSION = VERSION
STATION_ADAPTER_VERSION = VERSION
STATION_REGISTRY_VERSION = VERSION
PIPELINE_ITEM_VERSION = VERSION

PIPELINE_ITEM_ENVELOPE_FIELDS = [
    "dataVersion",
    "itemId",
    "productId",
    "storeId",
    "signalId",
    "packageId",
    "actionFamily",
    "inputRef",
]

DEFAULT_INPUTS = {
    "report_receive_station": ["dataVersion"],
    "report_schema_station": ["dataVersion", "rawReportRef"],
    "report_fact_station": ["dataVersion", "reportSchemaMappingRef"],
    "product_master_station": ["dataVersion", "factRef"],
    "product_metric_snapshot_station": ["dataVersion", "productMasterRef"],
    "full_product_bundle_station": ["dataVersion", "productMetricSnapshotRef"],
    "bundle_validation_station": ["dataVersion", "fullProductBundleRef"],
    "product_signal_admission_station": ["dataVersion", "validatedBundleRef"],
    "product_judgment_agent_station": ["dataVersion", "pipelineItemEnvelope"],
    "action_parameter_enrichment_station": ["dataVersion", "pipelineItemEnvelope"],
    "action_plan_judgment_agent_station": ["dataVersion", "pipelineItemEnvelope"],
    "task_mapping_agent_station": ["dataVersion", "pipelineItemEnvelope"],
    "task_pool_admission_station": ["dataVersion", "pipelineItemEnvelope"],
    "frontend_read_model_station": ["dataVersion", "taskPoolRef"],
    "task_pool_acceptance_station": ["dataVersion", "frontendReadModelRef"],
    "task_acceptance_station": ["taskId"],
    "task_assignment_station": ["taskId", "assigneeId"],
    "task_submission_station": ["taskId", "evidence"],
    "task_review_station": ["taskId", "decision"],
    "recap_schedule_station": ["taskId"],
    "recap_complete_station": ["taskId", "afterMetrics"],
    "rag_feedback_station": ["taskId", "recapResult"],
}

DEFAULT_OUTPUTS = {
    "report_receive_station": ["dataVersion", "rowCount", "rawReportRef", "outputRef"],
    "report_schema_station": ["headerCount", "dateFields", "reportSchemaMappingRef", "outputRef"],
    "report_fact_station": ["productFactCount", "factNamespaceStatus", "factRef", "outputRef"],
    "product_master_station": ["productMasterCount", "productMasterRef", "outputRef"],
    "product_metric_snapshot_station": ["productMetricSnapshotCount", "productMetricSnapshotRef", "outputRef"],
    # The queue writes the immutable fullProductBundleRef only after this contract
    # passes. Validate baseline/delta-neutral business fields instead.
    "full_product_bundle_station": [
        "productSignalPackageCount",
        "baselineProductBundleCount",
        "signalEligibility",
        "baselineGate",
        "contractValidation",
        "outputRef",
    ],
    # validatedBundleRef is likewise a post-contract Artifact-Hub transport ref.
    "bundle_validation_station": [
        "bundleCount",
        "baselineProductBundleCount",
        "validatedSignalCount",
        "validationStatus",
        "signalEligibility",
        "baselineGate",
        "contractValidation",
        "outputRef",
    ],
    # admissionRef is produced by the queue after admission business output passes;
    # zero Signal/Agent counts are valid for a first-report historical gate closure.
    "product_signal_admission_station": [
        "businessOutputType",
        "validatedBundleArtifactRef",
        "signalEligibility",
        "baselineGate",
        "fullSignalCount",
        "generatedSignalCount",
        "qualifiedSignalCount",
        "candidateProductCount",
        "admittedSignalCount",
        "observedSignalCount",
        "agent1PendingItemCount",
        "outputRef",
    ],
    "product_judgment_agent_station": ["agentJudgmentCount", "pendingItemCount", "outputRef"],
    "action_parameter_enrichment_station": ["claimedItemCount", "actionPackReadyItemCount", "outputRef"],
    "action_plan_judgment_agent_station": ["claimedItemCount", "actionPlanCount", "pendingItemCount", "outputRef"],
    "task_mapping_agent_station": ["claimedItemCount", "taskDecisionCount", "formalTaskDecisionCount", "outputRef"],
    "task_pool_admission_station": ["claimedItemCount", "createdTaskCount", "pendingItemCount", "outputRef"],
    "frontend_read_model_station": ["frontendReadModelStatus", "frontendReadModelRef", "outputRef"],
    "task_pool_acceptance_station": ["acceptanceStatus", "mismatchCount", "taskPoolAcceptanceRef", "outputRef"],
    "task_acceptance_station": ["taskId", "action", "outputRef"],
    "task_assignment_station": ["taskId", "action", "outputRef"],
    "task_submission_station": ["taskId", "transition", "outputRef"],
    "task_review_station": ["taskId", "decision", "outputRef"],
    "recap_schedule_station": ["taskId", "scheduledCount", "outputRef"],
    "recap_complete_station": ["taskId", "recapResult", "outputRef"],
    "rag_feedback_station": ["taskId", "candidateCount", "outputRef"],
}

ITEM_WORKER_STATIONS = {
    "product_judgment_agent_station",
    "action_parameter_enrichment_station",
    "action_plan_judgment_agent_station",
    "task_mapping_agent_station",
    "task_pool_admission_station",
}
REAL_ADAPTERS = set(DEFAULT_OUTPUTS)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _body_envelope(body: Dict[str, Any], station_id: str | None = None) -> Dict[str, Any]:
    raw = body.get("pipelineItemEnvelope") if isinstance(body.get("pipelineItemEnvelope"), dict) else {}
    return build_item_envelope(
        data_version=raw.get("dataVersion") or body.get("dataVersion") or body.get("data_version"),
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
    )


def station_contract(station_id: str) -> Dict[str, Any]:
    station = get_station(station_id)
    if not station:
        return {"version": VERSION, "ok": False, "error": "station_not_found", "stationId": station_id}
    sid = station["stationId"]
    is_item_worker = sid in ITEM_WORKER_STATIONS
    return {
        "version": VERSION,
        "contractVersion": VERSION,
        "registryVersion": VERSION,
        "adapterVersion": VERSION,
        "pipelineItemVersion": VERSION,
        "runtimeMode": "single_v22_runtime",
        "ok": True,
        "stationId": sid,
        "requestedStationId": station_id,
        "stage": station["stage"],
        "title": station["title"],
        "stationLine": station.get("stationLine"),
        "stationDomain": station.get("stationDomain"),
        "acceptance": station.get("acceptance"),
        "runtimeUnit": station.get("runtimeUnit"),
        "input": {
            "required": DEFAULT_INPUTS.get(sid, ["dataVersion"]),
            "envelope": "pipelineItemEnvelope",
            "itemWorker": is_item_worker,
            "batchCompatibilityAllowed": False,
        },
        "pipelineItemEnvelope": {
            "version": VERSION,
            "requiredForStreaming": PIPELINE_ITEM_ENVELOPE_FIELDS,
            "requiredForItemWorkers": is_item_worker,
        },
        "output": {
            "required": DEFAULT_OUTPUTS.get(sid, ["outputRef"]),
            "recommended": ["pipelineItemEnvelope", "pipelineItemState", "pipelineItemSummary"],
            "missingFieldsAutoFilled": False,
        },
        "nextStation": station.get("nextStation"),
        "replayable": bool(station.get("replayable")),
        "diagnosticSupported": bool(station.get("diagnosticSupported")),
        "backendModule": station.get("backendModule"),
        "frontendModule": station.get("frontendModule"),
        "standardInterface": {
            "contract": f"/api/stations/{sid}/contract",
            "health": f"/api/stations/{sid}/health",
            "run": f"/api/stations/{sid}/run",
            "replay": f"/api/stations/{sid}/replay",
            "gates": f"/api/stations/{sid}/gates",
        },
        "adapter": {
            "realAdapterSupported": sid in REAL_ADAPTERS,
            "diagnosticUsesSimulation": True,
        },
        "rule": (
            "V22 validates exact business outputs; post-contract Artifact-Hub "
            "transport refs are created by the station queue, never fabricated by adapters."
        ),
    }


def list_station_contracts() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "pipelineItemVersion": VERSION,
        "contracts": [station_contract(item["stationId"]) for item in list_stations()],
        "rule": "One public station registry maps to one current V22 adapter contract.",
    }


def validate_contract_payload(
    station_id: str,
    payload: Dict[str, Any] | None,
    *,
    direction: str = "input",
) -> Dict[str, Any]:
    payload = payload or {}
    contract = station_contract(station_id)
    required = list(((contract.get(direction) or {}).get("required") or []))
    missing = [key for key in required if key not in payload or _is_blank(payload.get(key))]
    envelope = payload.get("pipelineItemEnvelope") if isinstance(payload.get("pipelineItemEnvelope"), dict) else None
    if direction == "input" and station_id in ITEM_WORKER_STATIONS and envelope:
        envelope_missing = [
            key for key in PIPELINE_ITEM_ENVELOPE_FIELDS if _is_blank(envelope.get(key))
        ]
        missing.extend(f"pipelineItemEnvelope.{key}" for key in envelope_missing)
    missing = list(dict.fromkeys(missing))
    return {
        "version": VERSION,
        "stationId": contract.get("stationId") or station_id,
        "direction": direction,
        "status": "passed" if not missing else "failed",
        "missing": missing,
        "required": required,
        "pipelineItemEnvelope": "present" if envelope else "absent",
        "payloadKeys": sorted(payload.keys()),
    }


def station_health(station_id: str) -> Dict[str, Any]:
    station = get_station(station_id)
    if not station:
        return {"version": VERSION, "stationId": station_id, "status": "failed", "message": "station not found"}
    try:
        importlib.import_module(str(station.get("backendModule")))
        module_ok = True
        error = None
    except Exception as exc:
        module_ok = False
        error = str(exc)
    gates = stage_summary(None, limit=20)
    return {
        "version": VERSION,
        "stationId": station["stationId"],
        "stage": station["stage"],
        "title": station["title"],
        "status": "healthy" if module_ok else "degraded",
        "backendModule": station.get("backendModule"),
        "moduleImportOk": module_ok,
        "errorMessage": error,
        "gateTableOk": gates.get("gateCount") is not None,
        "contract": station_contract(station["stationId"]),
    }


def station_gates(
    station_id: str,
    data_version: str | None = None,
    limit: int = 40,
    *,
    include_diagnostic: bool = False,
) -> Dict[str, Any]:
    station = get_station(station_id)
    if not station:
        return {"version": VERSION, "stationId": station_id, "gates": [], "error": "station_not_found"}
    summary = stage_summary(data_version=data_version, limit=limit, include_diagnostic=include_diagnostic)
    gates = [gate for gate in summary.get("gates", []) if gate.get("stage") == station["stage"]]
    return {
        "version": VERSION,
        "stationId": station["stationId"],
        "stage": station["stage"],
        "includeDiagnostic": include_diagnostic,
        "gates": gates,
        "gateCount": len(gates),
    }


def _derive_known_outputs(station_id: str, output: Dict[str, Any]) -> Dict[str, Any]:
    completed = dict(output or {})
    if station_id == "action_plan_judgment_agent_station" and "actionPlanCount" not in completed:
        completed["actionPlanCount"] = completed.get("planCount")
    if station_id == "task_mapping_agent_station":
        if "formalTaskDecisionCount" not in completed:
            completed["formalTaskDecisionCount"] = completed.get("taskDecisionCount")
    return completed


def run_station_contract(
    station_id: str,
    body: Dict[str, Any] | None = None,
    *,
    diagnostic: bool = False,
) -> Dict[str, Any]:
    station = get_station(station_id)
    body = dict(body or {})
    if not station:
        return {"version": VERSION, "ok": False, "status": "failed", "error": "station_not_found", "stationId": station_id}
    if "pipelineItemEnvelope" not in body and station_id in ITEM_WORKER_STATIONS:
        # Do not silently manufacture an item envelope for business execution.
        body["pipelineItemEnvelope"] = None
    input_check = validate_contract_payload(station["stationId"], body, direction="input")
    if input_check.get("status") != "passed":
        return {
            "version": VERSION,
            "ok": False,
            "status": "contract_invalid",
            "stationId": station["stationId"],
            "stage": station["stage"],
            "inputContract": input_check,
            "outputContract": None,
            "adapterError": None,
            "rule": "V22 does not execute a station with an incomplete input contract.",
        }
    envelope = _body_envelope(body, station.get("stage") or station["stationId"])
    try:
        adapter_output = run_station_adapter(station, body, diagnostic=diagnostic)
        adapter_error = None
    except Exception as exc:
        adapter_output = {}
        adapter_error = str(exc)
    data_version = adapter_output.get("dataVersion") or body.get("dataVersion") or envelope.get("dataVersion")
    output_ref = adapter_output.get("outputRef")
    output_envelope = build_item_envelope(
        data_version=data_version,
        item_id=envelope.get("itemId"),
        product_id=envelope.get("productId"),
        store_id=envelope.get("storeId"),
        signal_id=envelope.get("signalId"),
        package_id=envelope.get("packageId"),
        decision_id=envelope.get("decisionId"),
        task_id=envelope.get("taskId"),
        action_family=envelope.get("actionFamily"),
        route=envelope.get("route"),
        input_ref=envelope.get("inputRef"),
        output_ref=output_ref,
        stage=station["stage"],
    )
    output = _derive_known_outputs(
        station["stationId"],
        {
            **adapter_output,
            "dataVersion": data_version,
            "stationId": station["stationId"],
            "isDiagnostic": diagnostic,
            "pipelineItemEnvelope": output_envelope,
        },
    )
    output_check = validate_contract_payload(station["stationId"], output, direction="output")
    status = "completed" if not adapter_error and output_check.get("status") == "passed" else "failed"
    gate = record_stage_gate(
        data_version=data_version,
        stage=station["stage"],
        status=status,
        input_payload={**body, "isDiagnostic": diagnostic, "stationId": station["stationId"]},
        output_payload=output,
        user_id=body.get("userId") or body.get("user_id") or ("OPS" if diagnostic else None),
        upstream_stage=body.get("upstreamStage"),
        output_ref=output_ref,
        error_message=adapter_error or ",".join(output_check.get("missing") or []),
        run_type="diagnostic" if diagnostic else "business",
        is_diagnostic=diagnostic,
    )
    return {
        "version": VERSION,
        "ok": status == "completed",
        "status": status,
        "stationId": station["stationId"],
        "requestedStationId": station_id,
        "stage": station["stage"],
        "pipelineItemEnvelope": output_envelope,
        "inputContract": input_check,
        "outputContract": output_check,
        "output": output,
        "gate": gate,
        "adapterVersion": VERSION,
        "adapterError": adapter_error,
        "nextStation": station.get("nextStation"),
        "rule": "V22 records completion only after the real adapter and strict business output contract both pass; transport Artifact refs are persisted afterward.",
    }


__all__ = [
    "STATION_CONTRACT_VERSION",
    "list_station_contracts",
    "station_contract",
    "station_health",
    "station_gates",
    "run_station_contract",
    "validate_contract_payload",
]
