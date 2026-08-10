"""V22.3.0.2 non-Agent bridge for the hard-interface runtime.

The bridge preserves deterministic producer handoffs and layered read models without
replacing public Station callables. Signal Admission execution remains owned by
``artifact_signal_admission_v225_service.product_signal_admission_station_v225``;
this bridge may decorate its private signal-item producer hook, but it must never
swap the Station function or its contract version at import time.
"""
from __future__ import annotations

from typing import Any, Dict

HARD_INTERFACE_BRIDGE_VERSION = "22.3.0.2"
_BOUND = False


def _row_for_signal(data_version: str | None, signal_id: str) -> Dict[str, Any]:
    from src.repositories.sqlite_repository import connect

    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM pipeline_items
            WHERE COALESCE(data_version,'')=COALESCE(?,'')
              AND signal_id=?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (data_version, signal_id),
        ).fetchone()
    return dict(row) if row else {}


def bind_hard_interface_bridge_v2301() -> Dict[str, Any]:
    """Bind producer handoffs/read models without replacing governed Station owners."""
    global _BOUND
    if _BOUND:
        return hard_interface_bridge_status(idempotent=True)

    from src.services import agent_pipeline_item_worker_v2010_service as pipeline_worker
    from src.services import artifact_signal_admission_v225_service as admission
    from src.services import end_to_end_agent_flow_v226_service as flow
    from src.services import pipeline_live_read_model_v208_service as live
    from src.services import pipeline_item_service as item_service
    from src.services.agent_input_transport_v2258_service import ensure_agent1_input_ref
    from src.services.agent_input_transport_v230_service import migrate_pending_agent_inputs
    from src.services.operating_policy_context_v2028_service import build_operating_policy_context

    original_seed_signal = admission._seed_signal_item
    original_seed_action_pack = pipeline_worker.seed_action_pack_from_agent1_items
    immutable_base_reader = live.read_pipeline_live_model
    layered_reader = flow.read_pipeline_live_model_v226
    status_reader = flow.agent_pipeline_status_v226

    def seed_signal_with_input_projection(
        *,
        data_version: str | None,
        signal: Dict[str, Any],
        score: Dict[str, Any],
        source_artifact_ref: str,
        admitted: bool,
    ) -> Dict[str, Any]:
        result = original_seed_signal(
            data_version=data_version,
            signal=signal,
            score=score,
            source_artifact_ref=source_artifact_ref,
            admitted=admitted,
        )
        if not admitted:
            result.update(
                inputTransportVersion=HARD_INTERFACE_BRIDGE_VERSION,
                agent1InputCompiled=False,
                transportReady=True,
            )
            return result

        signal_id = str(signal.get("signalId") or "").strip()
        row = _row_for_signal(data_version, signal_id)
        if not row:
            raise RuntimeError(f"agent1_input_handoff_row_missing:{signal_id}")
        input_ref = ensure_agent1_input_ref(
            row,
            policy_context=build_operating_policy_context(),
        )
        result.update(
            agent1InputRef=input_ref,
            inputTransportVersion=HARD_INTERFACE_BRIDGE_VERSION,
            agent1InputProjectionVersion="22.5.8",
            agent1InputTransportOwner="src.services.agent_input_transport_v2258_service",
            agent1InputCompiled=True,
            transportReady=True,
            fullSignalReadByAgentAllowed=False,
        )
        return result

    def seed_action_pack_with_input_projection(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = original_seed_action_pack(*args, **kwargs)
        data_version = (
            result.get("dataVersion")
            or kwargs.get("data_version")
            or (args[0] if args else None)
        )
        migration = migrate_pending_agent_inputs(data_version)
        failed = int(migration.get("failed") or 0)
        result.update(
            agentInputMigration=migration,
            inputTransportVersion=HARD_INTERFACE_BRIDGE_VERSION,
            agent2InputCompiledCount=int(migration.get("agent2Compiled") or 0),
            transportReady=failed == 0,
            fullCapabilityReadByAgentAllowed=False,
        )
        return result

    def safe_layered_reader(
        data_version: str | None = None,
        *,
        limit: int = 80,
        _base_reader: Any = immutable_base_reader,
        _layered_reader: Any = layered_reader,
    ) -> Dict[str, Any]:
        current = flow._ORIGINAL_PIPELINE_LIVE_READER
        flow._ORIGINAL_PIPELINE_LIVE_READER = _base_reader
        try:
            result = _layered_reader(data_version=data_version, limit=limit)
        finally:
            flow._ORIGINAL_PIPELINE_LIVE_READER = current
        result["hardInterfaceBridgeVersion"] = HARD_INTERFACE_BRIDGE_VERSION
        result["agentExecutionOwner"] = "agent_runtime_hard_interface_v230"
        result["legacyAgentRuntimeBound"] = False
        return result

    def hard_pipeline_status(data_version: str | None = None) -> Dict[str, Any]:
        result = dict(status_reader(data_version))
        result.update(
            hardInterfaceBridgeVersion=HARD_INTERFACE_BRIDGE_VERSION,
            agent1RuntimeSource="artifactRefs.agent1InputRef",
            agent2RuntimeSource="artifactRefs.agent2InputRef",
            runtimeSource="pipeline_items.artifact_refs_json",
            executionMode="hard_interface_projection_artifact_only",
            legacyAgentRuntimeBound=False,
            alternateRuntimeAllowed=False,
            fallbackAllowed=False,
        )
        return result

    # Producer hook only. Do not replace the governed public Station callable.
    admission._seed_signal_item = seed_signal_with_input_projection
    pipeline_worker.seed_action_pack_from_agent1_items = seed_action_pack_with_input_projection
    pipeline_worker.agent_pipeline_status = hard_pipeline_status

    item_service.STAGE_ORDER["observed_soft_gate"] = 48
    flow._ORIGINAL_PIPELINE_LIVE_READER = immutable_base_reader
    live.read_pipeline_live_model = safe_layered_reader
    live.PIPELINE_LIVE_READ_MODEL_VERSION = HARD_INTERFACE_BRIDGE_VERSION

    _BOUND = True
    return hard_interface_bridge_status(idempotent=False)


def hard_interface_bridge_status(*, idempotent: bool = False) -> Dict[str, Any]:
    return {
        "version": HARD_INTERFACE_BRIDGE_VERSION,
        "bound": _BOUND,
        "idempotent": idempotent,
        "agentRuntimeReplaced": False,
        "agentExecutionOwner": "agent_runtime_hard_interface_v230",
        "signalAdmissionOwner": "artifact_signal_admission_v225_service.product_signal_admission_station_v225",
        "signalAdmissionCallableReplaced": False,
        "signalProducerHook": "_seed_signal_item_to_agent1InputRef_v2258",
        "agent1InputProducerHandoff": "signalRef_to_agent1InputRef_v2258_before_admission_returns",
        "agent1InputTransportOwner": "src.services.agent_input_transport_v2258_service",
        "agent2InputProducerHandoff": "capabilityRef_to_agent2InputRef_before_next_tick",
        "pipelineLiveLayered": True,
        "fallbackAllowed": False,
    }


__all__ = [
    "HARD_INTERFACE_BRIDGE_VERSION",
    "bind_hard_interface_bridge_v2301",
    "hard_interface_bridge_status",
]
