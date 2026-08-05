"""V22.5.15 Agent2 hash-proof bridge with V23.2.6 runtime identities.

The generic V22.5.9 Artifact runtime keeps its stable content/replay identity. Agent2
adds a separate time-bearing runtime transaction identity, validates historical and
new outputs against the current semantic contract, and never deletes immutable Raw
or Accepted Artifacts.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps
from src.services.agent2_action_draft_core_v225_service import (
    AGENT2_FAMILY_PAYLOAD_SCHEMA,
    AGENT2_GENERATION_COMPILER_VERSION,
    missing_agent2_draft_contract,
)
from src.services.agent_input_contract_v225_service import (
    AGENT2_DRAFT_INPUT_SCHEMA,
    build_projection_envelope,
)
from src.services.artifact_transport_service import (
    inspect_artifact,
    resolve_artifact,
    store_artifact,
    validate_artifact,
)
from src.services.hash_directed_artifact_runtime_v2259_service import (
    accepted_execution,
    ensure_hash_directed_runtime_tables,
)

AGENT2_HASH_PROOF_BRIDGE_VERSION = "22.5.15"
AGENT2_RUNTIME_SOLID_HASH_VERSION = "23.2.6"
AGENT2_HASH_STAGE = "action_plan_judgment_agent"
AGENT2_HASH_OUTPUT_TYPE = "agent2_model_output.v2259"


class Agent2HashProofError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(f"{self.code}:{self.detail}" if self.detail else self.code)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_VOLATILE_KEYS = {
    "createdAt",
    "updatedAt",
    "startedAt",
    "finishedAt",
    "acceptedAt",
    "executionId",
    "itemExecutionId",
    "executionHash",
    "runtimeExecutionHash",
    "replayKeyHash",
    "semanticInputHash",
    "attemptNo",
    "executionMode",
    "sourceExecutionHash",
    "inputArtifactRef",
    "inputContentHash",
    "outputArtifactRef",
    "outputContentHash",
    "rawBatchOutputRef",
    "artifactHash",
    "artifactRefs",
    "agent2DraftExecutionProof",
    "agent2DraftHashExecutionProof",
    "providerRequestId",
    "semanticCallId",
    "exactExecutionReplay",
    "hashIdentityMatched",
    "fallbackIdentityMatchingUsed",
    "cachedOutputRebound",
    "hashDirectedRuntimeVersion",
}


def _solid_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _solid_value(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_solid_value(item) for item in value]
    return value


def stable_agent2_content_hash(value: Any) -> str:
    """Return a content-cache Hash independent of runtime transaction metadata."""

    return _sha256(_solid_value(value))


def _artifact_hash(artifact_ref: str) -> str:
    metadata = inspect_artifact(artifact_ref)
    return _text(
        metadata.get("contentHash") or metadata.get("content_hash"),
        160,
    )


def _business_output(value: Any) -> Dict[str, Any]:
    envelope = _dict(value)
    output = envelope.get("output")
    return dict(output) if isinstance(output, dict) else dict(envelope)


def _table_columns(conn: Any, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def ensure_agent2_runtime_identity_tables() -> None:
    """Add Agent2-owned replay eligibility fields without replacing generic runtime."""

    ensure_hash_directed_runtime_tables()
    with connect() as conn:
        columns = _table_columns(conn, "artifact_execution_index_v2259")
        additions = {
            "reusable": "INTEGER NOT NULL DEFAULT 1",
            "replay_rejection_reason": "TEXT",
            "accepted_content_hash": "TEXT",
            "accepted_contract_version": "TEXT",
        }
        for name, ddl in additions.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE artifact_execution_index_v2259 ADD COLUMN {name} {ddl}"
                )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent2_runtime_execution_v2326 (
                runtime_execution_hash TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                replay_key_hash TEXT NOT NULL,
                semantic_input_hash TEXT NOT NULL,
                source_execution_hash TEXT,
                input_artifact_ref TEXT NOT NULL,
                output_artifact_ref TEXT,
                output_artifact_hash TEXT,
                accepted_content_hash TEXT,
                execution_mode TEXT NOT NULL,
                attempt_no INTEGER NOT NULL,
                status TEXT NOT NULL,
                contract_version TEXT,
                contract_missing_json TEXT,
                receipt_artifact_ref TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent2_runtime_replay_v2326
            ON agent2_runtime_execution_v2326(replay_key_hash,attempt_no,status)
            """
        )
        conn.commit()


def _input_identity(input_ref: str) -> Dict[str, Any]:
    if not input_ref.startswith("ART-"):
        raise Agent2HashProofError("agent2_hash_input_ref_missing")
    validation = validate_artifact(
        input_ref,
        expected_type=AGENT2_DRAFT_INPUT_SCHEMA,
    )
    if validation.get("ok") is not True:
        raise Agent2HashProofError(
            "agent2_hash_input_artifact_invalid",
            _text(validation.get("status")),
        )
    value = resolve_artifact(input_ref)
    envelope = _dict(value)
    payload = _dict(envelope.get("payload"))
    if not payload:
        raise Agent2HashProofError("agent2_hash_input_payload_missing")
    content_hash = _artifact_hash(input_ref)
    if not content_hash:
        raise Agent2HashProofError("agent2_hash_input_content_hash_missing")
    return {
        "artifactRef": input_ref,
        "contentHash": content_hash,
        "semanticHash": _text(envelope.get("projectedContentHash"), 160) or content_hash,
        "envelope": envelope,
        "payload": payload,
        "packageId": _text(payload.get("packageId") or payload.get("itemId"), 220),
        "storeId": _text(payload.get("storeId"), 160),
        "productId": _text(payload.get("productId"), 160),
    }




def build_agent2_generation_envelope(
    envelope: Dict[str, Any],
    *,
    canonical_input_ref: str,
) -> Dict[str, Any]:
    """Version the Agent2 generation contract without changing business facts."""

    source = json.loads(json.dumps(envelope, ensure_ascii=False, default=str))
    payload = dict(_dict(source.get("payload")))
    if not payload:
        raise Agent2HashProofError("agent2_generation_compiler_payload_missing")
    extensions = dict(_dict(payload.get("diagnosticExtensions")))
    extensions["agent2GenerationCompiler"] = {
        "version": AGENT2_GENERATION_COMPILER_VERSION,
        "schema": AGENT2_FAMILY_PAYLOAD_SCHEMA,
        "mode": "system_compiled_family_payload",
        "systemComputedDraftStatus": True,
        "modelOwnedOutput": [
            "familyPayload",
            "missingData",
            "conflictReasons",
            "rejectedReason",
        ],
    }
    payload["diagnosticExtensions"] = extensions
    compiled_envelope = build_projection_envelope(
        schema=AGENT2_DRAFT_INPUT_SCHEMA,
        payload=payload,
        source_artifact_refs=[canonical_input_ref],
        source_content_hash=str(source.get("sourceContentHash") or ""),
    )
    artifact = store_artifact(
        artifact_type=AGENT2_DRAFT_INPUT_SCHEMA,
        value=compiled_envelope,
        schema_version=_text(compiled_envelope.get("projectionVersion"), 80),
        store_id=payload.get("storeId"),
        product_id=payload.get("productId"),
        data_version=payload.get("dataVersion"),
        created_by="agent2_hash_proof_bridge_v22515",
        parent_refs=[canonical_input_ref],
        metadata={
            "generationCompilerVersion": AGENT2_GENERATION_COMPILER_VERSION,
            "familyPayloadSchema": AGENT2_FAMILY_PAYLOAD_SCHEMA,
            "canonicalInputArtifactRef": canonical_input_ref,
            "semanticInputHash": compiled_envelope.get("projectedContentHash"),
            "runtimeTimestampInjected": False,
        },
    )
    return {
        "envelope": compiled_envelope,
        "compilerInputArtifactRef": artifact["artifactId"],
        "compilerInputArtifactHash": artifact["contentHash"],
        "canonicalInputArtifactRef": canonical_input_ref,
        "semanticInputHash": str(compiled_envelope.get("projectedContentHash") or ""),
        "generationCompilerVersion": AGENT2_GENERATION_COMPILER_VERSION,
    }

def build_agent2_regeneration_envelope(
    envelope: Dict[str, Any],
    *,
    canonical_input_ref: str,
    source_execution_hash: str,
) -> Dict[str, Any]:
    """Materialize a unique run-input Artifact without changing Agent2 semantics."""

    source = json.loads(json.dumps(envelope, ensure_ascii=False, default=str))
    payload = _dict(source.get("payload"))
    if not payload:
        raise Agent2HashProofError("agent2_regeneration_payload_missing")
    created_at = _utc_now()
    attempt_id = "A2REGEN-" + uuid.uuid4().hex.upper()
    audit = dict(_dict(source.get("projectionAudit")))
    audit["runtimeExecution"] = {
        "version": AGENT2_RUNTIME_SOLID_HASH_VERSION,
        "createdAt": created_at,
        "runtimeAttemptId": attempt_id,
        "executionMode": "provider_regeneration_after_invalid_replay",
        "canonicalInputArtifactRef": canonical_input_ref,
        "sourceExecutionHash": source_execution_hash,
        "semanticInputHash": _text(source.get("projectedContentHash"), 160),
    }
    source["projectionAudit"] = audit
    artifact = store_artifact(
        artifact_type=AGENT2_DRAFT_INPUT_SCHEMA,
        value=source,
        schema_version=_text(source.get("projectionVersion"), 80),
        store_id=payload.get("storeId"),
        product_id=payload.get("productId"),
        data_version=payload.get("dataVersion"),
        created_by="agent2_hash_proof_bridge_v22515",
        parent_refs=[canonical_input_ref],
        metadata={
            "runtimeAttemptId": attempt_id,
            "executionMode": "provider_regeneration_after_invalid_replay",
            "canonicalInputArtifactRef": canonical_input_ref,
            "sourceExecutionHash": source_execution_hash,
            "semanticInputHash": source.get("projectedContentHash"),
        },
    )
    return {
        "envelope": source,
        "runtimeInputArtifactRef": artifact["artifactId"],
        "runtimeInputArtifactHash": artifact["contentHash"],
        "canonicalInputArtifactRef": canonical_input_ref,
        "semanticInputHash": _text(source.get("projectedContentHash"), 160),
        "runtimeAttemptId": attempt_id,
        "createdAt": created_at,
    }


def build_agent2_contract_repair_envelope(
    envelope: Dict[str, Any],
    *,
    canonical_input_ref: str,
    source_execution_hash: str,
    previous_output: Dict[str, Any],
    missing: List[str],
) -> Dict[str, Any]:
    """Create one isolated Agent2 contract-repair input with exact missing fields."""

    source = json.loads(json.dumps(envelope, ensure_ascii=False, default=str))
    payload = dict(_dict(source.get("payload")))
    if not payload:
        raise Agent2HashProofError("agent2_contract_repair_payload_missing")
    extensions = dict(_dict(payload.get("diagnosticExtensions")))
    previous_family = (
        _dict(previous_output.get("familyPayload"))
        or _dict(previous_output.get("creativeDraft"))
        or _dict(previous_output.get("operationPlan"))
        or _dict(previous_output.get("activityDraft"))
        or _dict(previous_output.get("repairDraft"))
        or _dict(previous_output.get("experimentDraft"))
    )
    extensions["agent2ContractRepair"] = {
        "version": "23.2.8",
        "attemptNo": 1,
        "missing": [str(value) for value in missing[:16]],
        "previousNormalizedOutput": {
            "modelDeclaredDraftStatus": previous_output.get("modelDeclaredDraftStatus"),
            "familyPayload": previous_family,
            "outcomeChannel": previous_output.get("outcomeChannel"),
        },
        "sourceExecutionHash": source_execution_hash,
        "repairScope": "family_payload_only",
        "agent1Rerun": False,
        "actionPackRerun": False,
    }
    payload["diagnosticExtensions"] = extensions
    repair_envelope = build_projection_envelope(
        schema=AGENT2_DRAFT_INPUT_SCHEMA,
        payload=payload,
        source_artifact_refs=[canonical_input_ref],
        source_content_hash=str(source.get("sourceContentHash") or ""),
    )
    created_at = _utc_now()
    attempt_id = "A2REPAIR-" + uuid.uuid4().hex.upper()
    audit = dict(_dict(repair_envelope.get("projectionAudit")))
    audit["runtimeExecution"] = {
        "version": "23.2.8",
        "createdAt": created_at,
        "runtimeAttemptId": attempt_id,
        "executionMode": "agent2_contract_repair",
        "canonicalInputArtifactRef": canonical_input_ref,
        "sourceExecutionHash": source_execution_hash,
        "semanticInputHash": repair_envelope.get("projectedContentHash"),
        "repairAttemptNo": 1,
    }
    repair_envelope["projectionAudit"] = audit
    artifact = store_artifact(
        artifact_type=AGENT2_DRAFT_INPUT_SCHEMA,
        value=repair_envelope,
        schema_version=_text(repair_envelope.get("projectionVersion"), 80),
        store_id=payload.get("storeId"),
        product_id=payload.get("productId"),
        data_version=payload.get("dataVersion"),
        created_by="agent2_hash_proof_bridge_v22515",
        parent_refs=[canonical_input_ref],
        metadata={
            "runtimeAttemptId": attempt_id,
            "executionMode": "agent2_contract_repair",
            "canonicalInputArtifactRef": canonical_input_ref,
            "sourceExecutionHash": source_execution_hash,
            "repairAttemptNo": 1,
            "contractMissing": [str(value) for value in missing[:16]],
        },
    )
    return {
        "envelope": repair_envelope,
        "runtimeInputArtifactRef": artifact["artifactId"],
        "runtimeInputArtifactHash": artifact["contentHash"],
        "canonicalInputArtifactRef": canonical_input_ref,
        "semanticInputHash": str(repair_envelope.get("projectedContentHash") or ""),
        "runtimeAttemptId": attempt_id,
        "createdAt": created_at,
        "repairAttemptNo": 1,
    }

def _accepted_rows_for_input(input_ref: str, input_hash: str) -> List[Dict[str, Any]]:
    ensure_agent2_runtime_identity_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM artifact_execution_index_v2259
            WHERE stage=?
              AND input_artifact_ref=?
              AND input_content_hash=?
              AND status='accepted'
              AND accepted_output_ref IS NOT NULL
              AND COALESCE(reusable,1)=1
            ORDER BY updated_at DESC
            """,
            (AGENT2_HASH_STAGE, input_ref, input_hash),
        ).fetchall()
    return [dict(row) for row in rows]



def revoked_agent2_execution_for_input(input_ref: str) -> Dict[str, Any] | None:
    """Return the immutable source execution that has lost replay eligibility."""

    identity = _input_identity(input_ref)
    ensure_agent2_runtime_identity_tables()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT execution_hash,accepted_output_ref,accepted_output_hash,
                   raw_batch_output_ref,replay_rejection_reason,updated_at
            FROM artifact_execution_index_v2259
            WHERE stage=?
              AND input_artifact_ref=?
              AND input_content_hash=?
              AND COALESCE(reusable,1)=0
              AND accepted_output_ref IS NOT NULL
              AND COALESCE(replay_rejection_reason,'') LIKE 'accepted_output_current_contract_invalid:%'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (AGENT2_HASH_STAGE, input_ref, identity["contentHash"]),
        ).fetchone()
    return dict(row) if row else None

def revoke_agent2_execution(
    execution_hash: str,
    missing: List[str],
) -> Dict[str, Any]:
    """Revoke replay eligibility while preserving historical Artifact references."""

    ensure_agent2_runtime_identity_tables()
    reason = "accepted_output_current_contract_invalid:" + ",".join(missing[:16])
    with connect() as conn:
        row = conn.execute(
            """
            SELECT accepted_output_ref,accepted_output_hash,raw_batch_output_ref,status
            FROM artifact_execution_index_v2259
            WHERE execution_hash=?
            LIMIT 1
            """,
            (execution_hash,),
        ).fetchone()
        if not row:
            raise Agent2HashProofError("agent2_hash_execution_missing", execution_hash)
        preserved = dict(row)
        conn.execute(
            """
            UPDATE artifact_execution_index_v2259
            SET status='failed',reusable=0,replay_rejection_reason=?,last_error=?,
                lease_expires_at=NULL,updated_at=?
            WHERE execution_hash=?
            """,
            (reason, reason, _utc_now(), execution_hash),
        )
        conn.commit()
    return {
        "executionHash": execution_hash,
        "reason": reason,
        "preservedAcceptedOutputRef": preserved.get("accepted_output_ref"),
        "preservedAcceptedOutputHash": preserved.get("accepted_output_hash"),
        "preservedRawBatchOutputRef": preserved.get("raw_batch_output_ref"),
        "historicalArtifactDeleted": False,
    }


def finalize_agent2_execution_acceptance(
    draft: Dict[str, Any],
    *,
    contract_version: str,
) -> Dict[str, Any]:
    """Validate the current output and persist replay/solid-content eligibility."""

    ensure_agent2_runtime_identity_tables()
    execution_hash = _text(draft.get("executionHash"), 160)
    if not execution_hash:
        raise Agent2HashProofError("agent2_runtime_execution_hash_missing")
    missing = list(missing_agent2_draft_contract(draft))
    if missing:
        revoked = revoke_agent2_execution(execution_hash, missing)
        return {
            "accepted": False,
            "missing": missing,
            "replayRevoked": revoked,
        }
    accepted_content_hash = stable_agent2_content_hash(draft)
    with connect() as conn:
        conn.execute(
            """
            UPDATE artifact_execution_index_v2259
            SET reusable=1,replay_rejection_reason=NULL,accepted_content_hash=?,
                accepted_contract_version=?,last_error=NULL,updated_at=?
            WHERE execution_hash=? AND status='accepted'
            """,
            (
                accepted_content_hash,
                contract_version,
                _utc_now(),
                execution_hash,
            ),
        )
        conn.commit()
    return {
        "accepted": True,
        "missing": [],
        "acceptedContentHash": accepted_content_hash,
        "acceptedContractVersion": contract_version,
    }


def record_agent2_runtime_outcome(
    *,
    input_ref: str,
    draft: Dict[str, Any],
    execution_mode: str,
    status: str,
    contract_version: str,
    contract_missing: List[str] | None = None,
    source_execution_hash: str | None = None,
) -> Dict[str, Any]:
    """Create one unique time-bearing Agent2 runtime transaction and receipt."""

    ensure_agent2_runtime_identity_tables()
    identity = _input_identity(input_ref)
    replay_key_hash = _text(draft.get("executionHash"), 160) or _sha256(
        {
            "stage": AGENT2_HASH_STAGE,
            "inputArtifactRef": input_ref,
            "inputContentHash": identity["semanticHash"],
        }
    )
    with connect() as conn:
        row = conn.execute(
            """
            SELECT MAX(attempt_no) AS n
            FROM agent2_runtime_execution_v2326
            WHERE replay_key_hash=?
            """,
            (replay_key_hash,),
        ).fetchone()
    attempt_no = int((row["n"] if row else 0) or 0) + 1
    started_at = _utc_now()
    execution_id = "EXE-" + uuid.uuid4().hex.upper()
    runtime_execution_hash = _sha256(
        {
            "identityVersion": AGENT2_RUNTIME_SOLID_HASH_VERSION,
            "executionId": execution_id,
            "startedAt": started_at,
            "attemptNo": attempt_no,
            "executionMode": execution_mode,
            "replayKeyHash": replay_key_hash,
            "semanticInputHash": identity["semanticHash"],
        }
    )
    output_ref = _text(draft.get("outputArtifactRef"), 220)
    output_hash = _text(draft.get("outputContentHash"), 160)
    accepted_content_hash = stable_agent2_content_hash(draft)
    finished_at = _utc_now()
    receipt_value = {
        "schema": "agent2.runtime_execution_receipt.v2326",
        "version": AGENT2_RUNTIME_SOLID_HASH_VERSION,
        "createdAt": finished_at,
        "executionId": execution_id,
        "runtimeExecutionHash": runtime_execution_hash,
        "replayKeyHash": replay_key_hash,
        "semanticInputHash": identity["semanticHash"],
        "attemptNo": attempt_no,
        "executionMode": execution_mode,
        "sourceExecutionHash": source_execution_hash,
        "inputArtifactRef": input_ref,
        "outputArtifactRef": output_ref or None,
        "outputArtifactHash": output_hash or None,
        "acceptedContentHash": accepted_content_hash,
        "contractVersion": contract_version,
        "contractMissing": list(contract_missing or []),
        "status": status,
    }
    parents = [input_ref]
    if output_ref.startswith("ART-"):
        parents.append(output_ref)
    receipt = store_artifact(
        artifact_type="agent2_runtime_execution_receipt.v2326",
        value=receipt_value,
        schema_version=AGENT2_RUNTIME_SOLID_HASH_VERSION,
        store_id=identity.get("storeId"),
        product_id=identity.get("productId"),
        data_version=_dict(identity.get("payload")).get("dataVersion"),
        created_by="agent2_hash_proof_bridge_v22515",
        parent_refs=parents,
        metadata={
            "runtimeExecutionHash": runtime_execution_hash,
            "replayKeyHash": replay_key_hash,
            "attemptNo": attempt_no,
            "executionMode": execution_mode,
            "sourceExecutionHash": source_execution_hash,
            "status": status,
        },
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agent2_runtime_execution_v2326 (
                runtime_execution_hash,execution_id,replay_key_hash,semantic_input_hash,
                source_execution_hash,input_artifact_ref,output_artifact_ref,
                output_artifact_hash,accepted_content_hash,execution_mode,attempt_no,
                status,contract_version,contract_missing_json,receipt_artifact_ref,
                started_at,finished_at,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                runtime_execution_hash,
                execution_id,
                replay_key_hash,
                identity["semanticHash"],
                source_execution_hash,
                input_ref,
                output_ref or None,
                output_hash or None,
                accepted_content_hash,
                execution_mode,
                attempt_no,
                status,
                contract_version,
                dumps(list(contract_missing or [])),
                receipt["artifactId"],
                started_at,
                finished_at,
                dumps(receipt_value),
            ),
        )
        conn.commit()
    return {
        "executionId": execution_id,
        "runtimeExecutionHash": runtime_execution_hash,
        "replayKeyHash": replay_key_hash,
        "semanticInputHash": identity["semanticHash"],
        "attemptNo": attempt_no,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "executionMode": execution_mode,
        "sourceExecutionHash": source_execution_hash,
        "acceptedContentHash": accepted_content_hash,
        "runtimeExecutionReceiptRef": receipt["artifactId"],
    }


def _resolve_record(
    record: Dict[str, Any],
    identity: Dict[str, Any],
    *,
    runtime_draft: Dict[str, Any] | None,
) -> Dict[str, Any]:
    execution_hash = _text(record.get("execution_hash"), 160)
    if not execution_hash:
        raise Agent2HashProofError("agent2_hash_execution_hash_missing")
    replay = accepted_execution(execution_hash)
    if not replay:
        raise Agent2HashProofError(
            "agent2_hash_accepted_execution_unresolvable",
            execution_hash,
        )
    execution = _dict(replay.get("execution"))
    input_ref = _text(identity.get("artifactRef"), 220)
    input_hash = _text(identity.get("contentHash"), 160)
    semantic_hash = _text(identity.get("semanticHash"), 160) or input_hash
    if _text(execution.get("stage")) != AGENT2_HASH_STAGE:
        raise Agent2HashProofError("agent2_hash_stage_mismatch")
    if _text(execution.get("input_artifact_ref"), 220) != input_ref:
        raise Agent2HashProofError("agent2_hash_input_ref_mismatch")
    if _text(execution.get("input_content_hash"), 160) != input_hash:
        raise Agent2HashProofError("agent2_hash_input_content_hash_mismatch")

    output_ref = _text(replay.get("outputArtifactRef"), 220)
    output_hash = _text(replay.get("outputContentHash"), 160)
    if not output_ref.startswith("ART-"):
        raise Agent2HashProofError("agent2_hash_output_ref_missing")
    output_validation = validate_artifact(
        output_ref,
        expected_type=AGENT2_HASH_OUTPUT_TYPE,
    )
    if output_validation.get("ok") is not True:
        raise Agent2HashProofError(
            "agent2_hash_output_artifact_invalid",
            _text(output_validation.get("status")),
        )
    actual_output_hash = _artifact_hash(output_ref)
    if not output_hash or actual_output_hash != output_hash:
        raise Agent2HashProofError("agent2_hash_output_content_hash_mismatch")
    draft = _business_output(replay.get("output"))
    if not draft:
        raise Agent2HashProofError("agent2_hash_business_output_missing")
    package_id = _text(draft.get("packageId") or identity.get("packageId"), 220)
    if identity.get("packageId") and package_id != identity.get("packageId"):
        raise Agent2HashProofError("agent2_hash_package_id_mismatch")

    replayed = bool(_dict(runtime_draft).get("exactExecutionReplay")) if runtime_draft else True
    requested_mode = _text(_dict(runtime_draft).get("agent2RuntimeExecutionMode"), 120)
    mode = requested_mode or ("exact_replay" if replayed else "provider_call")
    source_execution_hash = _text(
        _dict(runtime_draft).get("sourceExecutionHash"),
        160,
    ) or (execution_hash if replayed else "")
    runtime_identity = record_agent2_runtime_outcome(
        input_ref=input_ref,
        draft={
            **draft,
            "executionHash": execution_hash,
            "outputArtifactRef": output_ref,
            "outputContentHash": output_hash,
        },
        execution_mode=mode,
        status="accepted",
        contract_version=AGENT2_RUNTIME_SOLID_HASH_VERSION,
        source_execution_hash=source_execution_hash or None,
    )
    proof = {
        "version": AGENT2_HASH_PROOF_BRIDGE_VERSION,
        "runtimeSolidHashVersion": AGENT2_RUNTIME_SOLID_HASH_VERSION,
        "proofMode": "accepted_hash_execution_artifact",
        "stage": AGENT2_HASH_STAGE,
        "packageId": package_id,
        "itemCorrelationId": package_id,
        "semanticCallId": "A2HASH-" + runtime_identity["runtimeExecutionHash"][-20:].upper(),
        "provider": execution.get("provider"),
        "model": execution.get("model"),
        "providerRequestId": None,
        "providerCallExecuted": not replayed,
        "exactReplayValidated": replayed,
        "replayFingerprint": input_hash,
        "resultMatched": True,
        "resultOrigin": "accepted_hash_execution_artifact",
        "fallbackUsed": False,
        "passed": True,
        "hashIdentityMatched": True,
        "executionHash": execution_hash,
        "runtimeExecutionHash": runtime_identity["runtimeExecutionHash"],
        "replayKeyHash": execution_hash,
        "semanticInputHash": semantic_hash,
        "executionMode": runtime_identity["executionMode"],
        "attemptNo": runtime_identity["attemptNo"],
        "sourceExecutionHash": runtime_identity["sourceExecutionHash"],
        "acceptedContentHash": runtime_identity["acceptedContentHash"],
        "acceptedContractVersion": AGENT2_RUNTIME_SOLID_HASH_VERSION,
        "runtimeExecutionReceiptRef": runtime_identity["runtimeExecutionReceiptRef"],
        "itemExecutionId": execution.get("item_execution_id"),
        "inputArtifactRef": input_ref,
        "inputContentHash": input_hash,
        "outputArtifactRef": output_ref,
        "outputContentHash": output_hash,
        "acceptedExecutionStatus": execution.get("status"),
        "attemptCount": int(execution.get("attempt_count") or 0),
        "hashDirectedRuntimeVersion": "22.5.9",
        "providerOriginNotReconstructed": True,
        "batchCountersAcceptedAsItemProof": False,
    }
    return {
        "version": AGENT2_HASH_PROOF_BRIDGE_VERSION,
        "identity": identity,
        "execution": execution,
        "draft": draft,
        "proof": proof,
        "outputArtifactRef": output_ref,
        "outputContentHash": output_hash,
    }


def resolve_agent2_hash_execution_for_input(
    input_ref: str,
    *,
    runtime_draft: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    identity = _input_identity(input_ref)
    rows = _accepted_rows_for_input(input_ref, str(identity["contentHash"]))
    if not rows:
        raise Agent2HashProofError("agent2_hash_accepted_execution_missing")
    errors: List[str] = []
    for row in rows:
        try:
            return _resolve_record(row, identity, runtime_draft=runtime_draft)
        except Exception as exc:
            errors.append(_text(exc, 500))
    raise Agent2HashProofError(
        "agent2_hash_accepted_execution_invalid",
        " | ".join(errors[:5]),
    )


def bridge_agent2_hash_proof(
    *,
    input_ref: str,
    package_id: str | None = None,
    runtime_draft: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    resolved = resolve_agent2_hash_execution_for_input(
        input_ref,
        runtime_draft=runtime_draft,
    )
    identity = _dict(resolved.get("identity"))
    expected_package = _text(package_id, 220)
    if expected_package and identity.get("packageId") != expected_package:
        raise Agent2HashProofError("agent2_hash_requested_package_id_mismatch")
    if runtime_draft:
        runtime_package = _text(runtime_draft.get("packageId"), 220)
        if runtime_package and runtime_package != identity.get("packageId"):
            raise Agent2HashProofError("agent2_hash_runtime_draft_package_mismatch")
        runtime_execution = _text(runtime_draft.get("executionHash"), 160)
        accepted_execution_hash = _text(
            _dict(resolved.get("proof")).get("executionHash"),
            160,
        )
        if runtime_execution and runtime_execution != accepted_execution_hash:
            raise Agent2HashProofError("agent2_hash_runtime_execution_mismatch")
    return resolved


def hash_proof_provider_summary(
    proof: Dict[str, Any],
    *,
    base: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    package_id = _text(proof.get("packageId"), 220)
    result = dict(_dict(base))
    proofs = dict(_dict(result.get("itemProvenance")))
    proofs[package_id] = dict(proof)
    result.update(
        version=AGENT2_HASH_PROOF_BRIDGE_VERSION,
        providerStatus="accepted_hash_execution",
        itemProvenance=proofs,
        itemProofs=proofs,
        passedItemCount=len(proofs),
        failedItemCount=0,
        hashProofBridgeVersion=AGENT2_HASH_PROOF_BRIDGE_VERSION,
        runtimeSolidHashVersion=AGENT2_RUNTIME_SOLID_HASH_VERSION,
        hashDirectedExecution=True,
        batchCountersAcceptedAsItemProof=False,
        fallbackUsed=False,
        fallbackAllowed=False,
    )
    return result


__all__ = [
    "AGENT2_HASH_PROOF_BRIDGE_VERSION",
    "AGENT2_RUNTIME_SOLID_HASH_VERSION",
    "AGENT2_HASH_STAGE",
    "Agent2HashProofError",
    "build_agent2_regeneration_envelope",
    "build_agent2_contract_repair_envelope",
    "build_agent2_generation_envelope",
    "bridge_agent2_hash_proof",
    "ensure_agent2_runtime_identity_tables",
    "finalize_agent2_execution_acceptance",
    "hash_proof_provider_summary",
    "record_agent2_runtime_outcome",
    "revoked_agent2_execution_for_input",
    "resolve_agent2_hash_execution_for_input",
    "revoke_agent2_execution",
    "stable_agent2_content_hash",
]
