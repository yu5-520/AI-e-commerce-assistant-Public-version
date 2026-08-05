"""V22.3.0 reference-only pipeline and hard Agent input contract.

Every worker resolves the immutable artifact referenced by its current stage.
Agent1 and Agent2 stages are stricter: they may resolve only agent1InputRef or
agent2InputRef. Missing projections fail closed and never fall back to full signal,
capability, current-stage or legacy payload objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.artifact_transport_service import (
    merge_artifact_refs,
    resolve_artifact,
    validate_artifact,
)

PIPELINE_ARTIFACT_CONTRACT_VERSION = "22.3.0"

_STAGE_REF_KEYS = {
    "data_received": "reportRef",
    "schema_ready": "schemaRef",
    "fact_ready": "factRef",
    "product_master_ready": "productMasterRef",
    "metric_snapshot_ready": "productSnapshotRef",
    "context_bundle_ready": "signalSnapshotRef",
    "quality_gate_ready": "validatedSignalSnapshotRef",
    "signal_admitted": "signalRef",
    "agent1_input_ready": "agent1InputRef",
    "agent1_pending": "agent1InputRef",
    "agent1_running": "agent1InputRef",
    "observed_soft_gate": "observationRef",
    "agent1_completed": "agent1Ref",
    "agent1_output_invalid": "agent1InvalidRef",
    "agent1_failed": "agent1FailureRef",
    "action_pack_ready": "agent2InputRef",
    "agent2_input_ready": "agent2InputRef",
    "agent2_running": "agent2InputRef",
    "action_pack_invalid": "capabilityFailureRef",
    "agent2_completed": "agent2Ref",
    "agent2_output_invalid": "agent2FailureRef",
    "agent2_failed": "agent2FailureRef",
    "agent2_dead_letter": "agent2FailureRef",
    "sop_mapped": "sopRef",
    "task_admitted": "taskRef",
    "read_model_ready": "readModelRef",
    "task_loop_ready": "acceptanceRef",
}
_HARD_AGENT_STAGES = {
    "agent1_input_ready",
    "agent1_pending",
    "agent1_running",
    "action_pack_ready",
    "agent2_input_ready",
    "agent2_running",
}


@dataclass(frozen=True)
class PipelineArtifactInput:
    payload: Dict[str, Any]
    artifact_id: str
    artifact_refs: Dict[str, Any]
    source: str
    expected_stage: str | None


class PipelineArtifactContractError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        item_id: str | None = None,
        stage: str | None = None,
        artifact_id: str | None = None,
        detail: Any = None,
    ) -> None:
        self.code = code
        self.item_id = item_id
        self.stage = stage
        self.artifact_id = artifact_id
        self.detail = detail
        super().__init__(
            ":".join(
                str(value)
                for value in (code, item_id, stage, artifact_id)
                if value not in (None, "")
            )
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "version": PIPELINE_ARTIFACT_CONTRACT_VERSION,
            "code": self.code,
            "pipelineItemId": self.item_id,
            "stage": self.stage,
            "artifactId": self.artifact_id,
            "detail": self.detail,
            "failureOwner": "artifact_transport",
            "fallbackAllowed": False,
        }


def _row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        return row.get(key) if isinstance(row, dict) else None


def _load_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        parsed = loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def artifact_refs_from_row(row: Any) -> Dict[str, Any]:
    """Read references only from the dedicated reference column."""
    return merge_artifact_refs(_load_mapping(_row_value(row, "artifact_refs_json")))


def stage_ref_key(stage: str | None) -> str | None:
    return _STAGE_REF_KEYS.get(str(stage or "").strip())


def input_artifact_id(row: Any, expected_stage: str | None = None) -> str | None:
    refs = artifact_refs_from_row(row)
    stage = str(expected_stage or _row_value(row, "current_stage") or "").strip()
    ref_key = stage_ref_key(stage)
    direct = str(refs.get(ref_key or "") or "").strip()
    if direct.startswith("ART-"):
        return direct
    if stage in _HARD_AGENT_STAGES:
        return None
    candidates: Iterable[Any] = (
        _row_value(row, "payload_artifact_ref"),
        refs.get("currentStageRef"),
    )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value.startswith("ART-"):
            return value
    return None


def pipeline_input_ref(row: Any, expected_stage: str | None = None) -> str:
    artifact_id = input_artifact_id(row, expected_stage)
    stage = str(expected_stage or _row_value(row, "current_stage") or "") or None
    if not artifact_id:
        code = "hard_agent_input_ref_missing" if stage in _HARD_AGENT_STAGES else "required_artifact_ref_missing"
        raise PipelineArtifactContractError(
            code,
            item_id=str(_row_value(row, "item_id") or "") or None,
            stage=stage,
            detail={
                "expectedRefKey": stage_ref_key(stage),
                "hardAgentStage": stage in _HARD_AGENT_STAGES,
                "fallbackAllowed": False,
            },
        )
    return artifact_id


def resolve_pipeline_row(
    row: Any,
    *,
    expected_stage: str | None = None,
    allow_legacy_payload: bool = False,
) -> PipelineArtifactInput:
    item_id = str(_row_value(row, "item_id") or "") or None
    actual_stage = str(_row_value(row, "current_stage") or "") or None
    if expected_stage and actual_stage and actual_stage != expected_stage:
        raise PipelineArtifactContractError(
            "pipeline_stage_mismatch",
            item_id=item_id,
            stage=actual_stage,
            detail={"expectedStage": expected_stage},
        )
    artifact_id = input_artifact_id(row, expected_stage)
    stage = actual_stage or expected_stage
    if not artifact_id:
        code = (
            "hard_agent_input_ref_missing"
            if str(stage or "") in _HARD_AGENT_STAGES
            else "legacy_payload_runtime_retired"
            if allow_legacy_payload
            else "required_artifact_ref_missing"
        )
        raise PipelineArtifactContractError(
            code,
            item_id=item_id,
            stage=stage,
            detail={
                "expectedRefKey": stage_ref_key(stage),
                "migrationRequired": True,
                "legacyPayloadRuntimeFallbackAllowed": False,
                "fullUpstreamArtifactFallbackAllowed": False,
            },
        )
    validation = validate_artifact(artifact_id)
    if validation.get("ok") is not True:
        raise PipelineArtifactContractError(
            str(validation.get("status") or "artifact_input_invalid"),
            item_id=item_id,
            stage=stage,
            artifact_id=artifact_id,
            detail=validation,
        )
    try:
        payload = resolve_artifact(artifact_id)
    except Exception as exc:
        raise PipelineArtifactContractError(
            "artifact_resolve_failed",
            item_id=item_id,
            stage=stage,
            artifact_id=artifact_id,
            detail=str(exc),
        ) from exc
    if not isinstance(payload, dict) or not payload:
        raise PipelineArtifactContractError(
            "artifact_payload_empty_or_not_object",
            item_id=item_id,
            stage=stage,
            artifact_id=artifact_id,
        )
    return PipelineArtifactInput(
        payload=dict(payload),
        artifact_id=artifact_id,
        artifact_refs=artifact_refs_from_row(row),
        source=(
            "hard_agent_input_ref_only"
            if str(stage or "") in _HARD_AGENT_STAGES
            else "artifact_ref_only"
        ),
        expected_stage=expected_stage,
    )


def resolve_pipeline_row_payload(
    row: Any,
    *,
    expected_stage: str | None = None,
    allow_legacy_payload: bool = False,
) -> Dict[str, Any]:
    return resolve_pipeline_row(
        row,
        expected_stage=expected_stage,
        allow_legacy_payload=allow_legacy_payload,
    ).payload


def attach_pipeline_artifact_ref(
    item_id: str,
    ref_key: str,
    artifact_id: str,
    *,
    make_current: bool = False,
) -> Dict[str, Any]:
    if not item_id or not ref_key or not str(artifact_id).startswith("ART-"):
        raise ValueError("invalid_pipeline_artifact_ref_attachment")
    with connect() as conn:
        row = conn.execute(
            "SELECT artifact_refs_json FROM pipeline_items WHERE item_id=?",
            (item_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"pipeline_item_not_found:{item_id}")
        refs = _load_mapping(row["artifact_refs_json"])
        refs[ref_key] = artifact_id
        if make_current:
            refs["currentStageRef"] = artifact_id
        conn.execute(
            """
            UPDATE pipeline_items
            SET artifact_refs_json=?,
                payload_artifact_ref=COALESCE(?, payload_artifact_ref),
                payload=NULL
            WHERE item_id=?
            """,
            (dumps(refs), artifact_id if make_current else None, item_id),
        )
        conn.commit()
    return refs


__all__ = [
    "PIPELINE_ARTIFACT_CONTRACT_VERSION",
    "PipelineArtifactInput",
    "PipelineArtifactContractError",
    "artifact_refs_from_row",
    "stage_ref_key",
    "input_artifact_id",
    "pipeline_input_ref",
    "resolve_pipeline_row",
    "resolve_pipeline_row_payload",
    "attach_pipeline_artifact_ref",
]
