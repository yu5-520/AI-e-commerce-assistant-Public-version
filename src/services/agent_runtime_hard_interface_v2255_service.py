"""V22.5.9 hash-directed facade over the sealed V22.5.5 pipeline state machine.

V22.5.11 routes startup schema preparation through the same deterministic owner used
by deployment before data-lineage sealing.
"""
from __future__ import annotations

from typing import Any, Dict

from src.services import agent_runtime_hard_interface_v2255_legacy_service as legacy

AGENT_RUNTIME_HARD_INTERFACE_VERSION = "22.5.9"
THREE_AGENT_PIPELINE_VERSION = legacy.THREE_AGENT_PIPELINE_VERSION
EXECUTION_LOCK_CONTRACT = legacy.EXECUTION_LOCK_CONTRACT


def _publish_hash_views(result: Dict[str, Any], data_version: str | None) -> None:
    if not result.get("ran") or not data_version:
        return
    try:
        from src.services.frontend_view_artifact_v2259_service import (
            materialize_frontend_views_v2259,
        )

        result["hashViewPublication"] = materialize_frontend_views_v2259(
            data_version=str(data_version),
            view_key="operator-center",
            user_id="U001",
        )
        result["hashViewPublicationStatus"] = "completed"
    except Exception as exc:
        result["hashViewPublicationStatus"] = "failed"
        result["hashViewPublicationError"] = str(exc)[:500]


def _refresh_read_models(result: Dict[str, Any], data_version: str | None) -> None:
    legacy._refresh_read_models(result, data_version)
    _publish_hash_views(result, data_version)


def run_agent1_microbatch_hard(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 8,
) -> Dict[str, Any]:
    result = legacy.run_agent1_microbatch_hard(
        data_version,
        user_id=user_id,
        batch_size=batch_size,
    )
    result["version"] = AGENT_RUNTIME_HARD_INTERFACE_VERSION
    result["hashDirectedExecution"] = True
    result["runtimeSource"] = "exact_inputArtifactHash_to_outputArtifactHash"
    _publish_hash_views(result, data_version)
    return result


def run_agent_pipeline_tick_hard(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    result = legacy.run_agent_pipeline_tick_hard(*args, **kwargs)
    data_version = result.get("dataVersion") or kwargs.get("data_version") or (args[0] if args else None)
    result["version"] = AGENT_RUNTIME_HARD_INTERFACE_VERSION
    result["hashDirectedExecution"] = True
    result["executionIndex"] = "artifact_execution_index_v2259"
    result["batchManifestContract"] = "agent_batch_manifest.v2259"
    result["cachedOutputRebindingAllowed"] = False
    result["runtimeSource"] = "exact_stage_inputArtifactHash_to_outputArtifactHash"
    _publish_hash_views(result, data_version)
    return result


def startup_agent_runtime_hard() -> Dict[str, Any]:
    from src.services.runtime_database_prepare_v22511_service import (
        prepare_runtime_database_schema,
    )

    schema_prepare = prepare_runtime_database_schema(verify_idempotent=False)
    result = legacy.startup_agent_runtime_hard()
    result.update(
        version=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        hashDirectedExecution=True,
        executionIndex="artifact_execution_index_v2259",
        batchManifestContract="agent_batch_manifest.v2259",
        frontendViewArtifactContract="frontend_view.manifest.v2259",
        runtimeDatabaseSchemaPreparation=schema_prepare,
        schemaPreparedBeforeLineageRequired=True,
        cachedOutputRebindingAllowed=False,
        fallbackAllowed=False,
    )
    return result


def agent_runtime_hard_interface_status() -> Dict[str, Any]:
    result = legacy.agent_runtime_hard_interface_status()
    result.update(
        version=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        hashDirectedExecution=True,
        agent1RuntimeSource="artifactRefs.agent1InputRef.v3+inputContentHash",
        agent2RuntimeSource="artifactRefs.agent2DraftInputRef+inputContentHash",
        agent3RuntimeSource="artifactRefs.agent3SopInputRef+inputContentHash",
        tokenRuntimeOwner="agent_token_runtime_v2259",
        transportOwner="artifact_transport+hash_directed_artifact_runtime_v2259",
        executionIndex="artifact_execution_index_v2259",
        batchManifestContract="agent_batch_manifest.v2259",
        runtimeDatabaseSchemaOwner="runtime_database_prepare_v22511_service",
        schemaPreparedBeforeLineageRequired=True,
        cachedOutputRebindingAllowed=False,
        frontendViewMode="manifestHash_plus_moduleContentHash",
        fallbackAllowed=False,
        executionMode="hash_directed_artifact_once_then_reference_only",
    )
    return result


def select_runnable_data_version_v225(preferred: str | None = None) -> str | None:
    return legacy.select_runnable_data_version_v225(preferred)


_finish_agent1 = legacy._finish_agent1
_recover_unresolved_once = legacy._recover_unresolved_once
run_agent2_draft_microbatch_hard = legacy.run_agent2_draft_microbatch_hard
run_agent2_microbatch_hard = legacy.run_agent2_microbatch_hard
migrate_legacy_agent2_outputs = legacy.migrate_legacy_agent2_outputs
migrate_misclassified_agent2_input_failures = legacy.migrate_misclassified_agent2_input_failures

__all__ = [
    "AGENT_RUNTIME_HARD_INTERFACE_VERSION",
    "THREE_AGENT_PIPELINE_VERSION",
    "EXECUTION_LOCK_CONTRACT",
    "run_agent1_microbatch_hard",
    "run_agent2_draft_microbatch_hard",
    "run_agent2_microbatch_hard",
    "run_agent_pipeline_tick_hard",
    "select_runnable_data_version_v225",
    "startup_agent_runtime_hard",
    "agent_runtime_hard_interface_status",
    "migrate_legacy_agent2_outputs",
    "migrate_misclassified_agent2_input_failures",
]
