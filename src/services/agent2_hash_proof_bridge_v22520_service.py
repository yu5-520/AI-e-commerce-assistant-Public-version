"""V22.5.20 Agent2 hash-proof bridge with fail-closed input history lookup.

The V22.5.15 bridge remains the validator for every immutable input/output pair.  This
module only expands discovery from the row's current ``agent2DraftInputRef`` to prior
Agent2 input Artifacts for the same package, store, product, data version and action
family.  It never accepts package identity without the original hash proof.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services import agent2_action_draft_core_v225_service as core
from src.services import agent2_hash_proof_bridge_v22515_service as legacy
from src.services.agent_input_contract_v225_service import AGENT2_DRAFT_INPUT_SCHEMA
from src.services.artifact_transport_service import resolve_artifact, validate_artifact

AGENT2_HASH_PROOF_BRIDGE_VERSION = "22.5.20"
AGENT2_HASH_STAGE = legacy.AGENT2_HASH_STAGE
AGENT2_HASH_OUTPUT_TYPE = legacy.AGENT2_HASH_OUTPUT_TYPE
Agent2HashProofError = legacy.Agent2HashProofError
hash_proof_provider_summary = legacy.hash_proof_provider_summary


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _payload_for_ref(input_ref: str) -> Dict[str, Any]:
    if not str(input_ref or "").startswith("ART-"):
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
    envelope = _dict(resolve_artifact(input_ref))
    payload = _dict(envelope.get("payload"))
    if not payload:
        raise Agent2HashProofError("agent2_hash_input_payload_missing")
    return payload


def _identity(input_ref: str) -> Dict[str, Any]:
    payload = _payload_for_ref(input_ref)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT artifact_id,data_version,store_id,product_id,created_at
            FROM artifact_registry
            WHERE artifact_id=? AND artifact_type=? AND status='valid'
            LIMIT 1
            """,
            (input_ref, AGENT2_DRAFT_INPUT_SCHEMA),
        ).fetchone()
    if not row:
        raise Agent2HashProofError("agent2_hash_input_registry_missing")
    record = dict(row)
    try:
        family = core.selected_family(payload)
    except Exception:
        family = _text(
            payload.get("lockedActionFamily") or payload.get("actionFamily"),
            100,
        )
    return {
        "artifactRef": input_ref,
        "packageId": _text(payload.get("packageId") or payload.get("itemId"), 220),
        "storeId": _text(payload.get("storeId") or record.get("store_id"), 160),
        "productId": _text(payload.get("productId") or record.get("product_id"), 160),
        "dataVersion": _text(payload.get("dataVersion") or record.get("data_version"), 220),
        "actionFamily": _text(family, 100),
        "createdAt": record.get("created_at"),
    }


def _historical_input_refs(identity: Dict[str, Any]) -> List[str]:
    where = ["artifact_type=?", "status='valid'"]
    params: List[Any] = [AGENT2_DRAFT_INPUT_SCHEMA]
    for column, key in (
        ("data_version", "dataVersion"),
        ("store_id", "storeId"),
        ("product_id", "productId"),
    ):
        value = _text(identity.get(key), 220)
        if value:
            where.append(f"COALESCE({column},'')=COALESCE(?,'')")
            params.append(value)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT artifact_id FROM artifact_registry WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC",
            tuple(params),
        ).fetchall()
    refs: List[str] = []
    for row in rows:
        ref = str(row["artifact_id"] or "")
        if ref.startswith("ART-") and ref not in refs:
            refs.append(ref)
    return refs


def _same_business_input(candidate_ref: str, expected: Dict[str, Any]) -> bool:
    try:
        candidate = _identity(candidate_ref)
    except Exception:
        return False
    for key in ("packageId", "storeId", "productId", "dataVersion", "actionFamily"):
        left = _text(expected.get(key), 220)
        right = _text(candidate.get(key), 220)
        if left and right and left != right:
            return False
        if left and not right:
            return False
    return True


def resolve_agent2_hash_execution_for_input(input_ref: str) -> Dict[str, Any]:
    """Resolve current input first, then one unique strictly validated historical pair."""
    try:
        resolved = legacy.resolve_agent2_hash_execution_for_input(input_ref)
        return {
            **resolved,
            "version": AGENT2_HASH_PROOF_BRIDGE_VERSION,
            "recoveryMode": "current_exact_input",
            "currentInputArtifactRef": input_ref,
            "historicalInputArtifactRef": None,
        }
    except Agent2HashProofError as exc:
        if exc.code != "agent2_hash_accepted_execution_missing":
            raise

    expected = _identity(input_ref)
    valid: List[Dict[str, Any]] = []
    errors: List[str] = []
    for candidate_ref in _historical_input_refs(expected):
        if candidate_ref == input_ref:
            continue
        if not _same_business_input(candidate_ref, expected):
            continue
        try:
            resolved = legacy.resolve_agent2_hash_execution_for_input(candidate_ref)
            output_ref = _text(resolved.get("outputArtifactRef"), 220)
            output_hash = _text(resolved.get("outputContentHash"), 160)
            valid.append(
                {
                    **resolved,
                    "candidateInputArtifactRef": candidate_ref,
                    "candidateOutputIdentity": f"{output_ref}:{output_hash}",
                }
            )
        except Exception as exc:
            errors.append(f"{candidate_ref}:{_text(exc, 320)}")

    unique: Dict[str, Dict[str, Any]] = {}
    for resolved in valid:
        identity_key = str(resolved.get("candidateOutputIdentity") or "")
        if identity_key:
            unique.setdefault(identity_key, resolved)

    if not unique:
        raise Agent2HashProofError(
            "agent2_hash_historical_execution_missing",
            " | ".join(errors[:8]),
        )
    if len(unique) != 1:
        raise Agent2HashProofError(
            "agent2_hash_historical_execution_ambiguous",
            ",".join(sorted(unique)[:8]),
        )

    resolved = next(iter(unique.values()))
    candidate_ref = str(resolved.pop("candidateInputArtifactRef", "") or "")
    resolved.pop("candidateOutputIdentity", None)
    proof = dict(_dict(resolved.get("proof")))
    proof.update(
        version=AGENT2_HASH_PROOF_BRIDGE_VERSION,
        recoveryMode="historical_exact_input",
        currentInputArtifactRef=input_ref,
        historicalInputArtifactRef=candidate_ref,
        historicalHashDiscoveryOnly=True,
        hashValidationRelaxed=False,
    )
    resolved["proof"] = proof
    resolved.update(
        version=AGENT2_HASH_PROOF_BRIDGE_VERSION,
        recoveryMode="historical_exact_input",
        currentInputArtifactRef=input_ref,
        historicalInputArtifactRef=candidate_ref,
    )
    return resolved


def bridge_agent2_hash_proof(
    *,
    input_ref: str,
    package_id: str | None = None,
    runtime_draft: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    resolved = resolve_agent2_hash_execution_for_input(input_ref)
    current_identity = _identity(input_ref)
    expected_package = _text(package_id, 220)
    if expected_package and current_identity.get("packageId") != expected_package:
        raise Agent2HashProofError("agent2_hash_requested_package_id_mismatch")
    proof = _dict(resolved.get("proof"))
    if runtime_draft:
        runtime_package = _text(runtime_draft.get("packageId"), 220)
        if runtime_package and runtime_package != current_identity.get("packageId"):
            raise Agent2HashProofError("agent2_hash_runtime_draft_package_mismatch")
        runtime_execution = _text(runtime_draft.get("executionHash"), 160)
        accepted_execution_hash = _text(proof.get("executionHash"), 160)
        if runtime_execution and runtime_execution != accepted_execution_hash:
            raise Agent2HashProofError("agent2_hash_runtime_execution_mismatch")
    return resolved


__all__ = [
    "AGENT2_HASH_PROOF_BRIDGE_VERSION",
    "AGENT2_HASH_STAGE",
    "AGENT2_HASH_OUTPUT_TYPE",
    "Agent2HashProofError",
    "resolve_agent2_hash_execution_for_input",
    "bridge_agent2_hash_proof",
    "hash_proof_provider_summary",
]
