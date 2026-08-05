"""V22.3.0 hard Agent input contracts.

The transport layer is the only producer of model-facing semantic input. Agent
workers and the token runtime may consume only validated projection artifacts;
they cannot recover full upstream business artifacts or silently widen scope.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Iterable, List

AGENT_INPUT_CONTRACT_VERSION = "22.3.0"
AGENT1_INPUT_SCHEMA = "agent_input.agent1.v1"
AGENT2_INPUT_SCHEMA = "agent_input.agent2.v1"

AGENT1_MAX_ITEM_CHARS = 14_000
AGENT1_MAX_BATCH_CHARS = 56_000
AGENT2_MAX_ITEM_CHARS = 16_000
AGENT2_MAX_BATCH_CHARS = 48_000

_TOP_LEVEL_KEYS = {
    "schema",
    "projectionVersion",
    "sourceArtifactRefs",
    "sourceContentHash",
    "projectedContentHash",
    "payload",
    "projectionAudit",
    "hardInterface",
}
_AGENT1_PAYLOAD_KEYS = {
    "productId",
    "storeId",
    "signalId",
    "correlationId",
    "dataVersion",
    "productIdentity",
    "profileLayer",
    "snapshotLayer",
    "metricLayer",
    "strongRelations",
    "crossValidation",
    "factLayerValidation",
    "dataFingerprint",
    "diagnosticRag",
    "inputContract",
}
_AGENT2_PAYLOAD_KEYS = {
    "packageId",
    "itemId",
    "dataVersion",
    "productId",
    "storeId",
    "productTitle",
    "title",
    "productIdentity",
    "decisionType",
    "agent1DecisionIR",
    "agent1OperatingJudgment",
    "matrixDispatch",
    "actionFamily",
    "selectedActionFamily",
    "lockedActionFamily",
    "actionParameterPack",
    "recentFiveOrLatestFacts",
    "ragContextSnapshot",
    "diagnosticExtensionContract",
    "diagnosticExtensions",
    "inputContract",
}


class AgentInputContractError(RuntimeError):
    def __init__(self, code: str, detail: Any = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def estimated_tokens(value: Any) -> int:
    # Conservative language-agnostic estimate used only for deterministic admission.
    return max(1, math.ceil(len(stable_json(value)) / 2.0))


def _schema_stage(schema: str) -> str:
    if schema == AGENT1_INPUT_SCHEMA:
        return "agent1"
    if schema == AGENT2_INPUT_SCHEMA:
        return "agent2"
    raise AgentInputContractError("unsupported_agent_input_schema", schema)


def _item_char_budget(schema: str) -> int:
    return AGENT1_MAX_ITEM_CHARS if schema == AGENT1_INPUT_SCHEMA else AGENT2_MAX_ITEM_CHARS


def batch_char_budget(schema: str) -> int:
    return AGENT1_MAX_BATCH_CHARS if schema == AGENT1_INPUT_SCHEMA else AGENT2_MAX_BATCH_CHARS


def build_projection_envelope(
    *,
    schema: str,
    payload: Dict[str, Any],
    source_artifact_refs: Iterable[str],
    source_content_hash: str,
) -> Dict[str, Any]:
    stage = _schema_stage(schema)
    refs = [str(value) for value in source_artifact_refs if str(value).startswith("ART-")]
    if not refs:
        raise AgentInputContractError("source_artifact_ref_required")
    if not isinstance(payload, dict) or not payload:
        raise AgentInputContractError("projected_payload_required")
    projected_hash = content_hash(payload)
    chars = len(stable_json(payload))
    limit = _item_char_budget(schema)
    envelope = {
        "schema": schema,
        "projectionVersion": AGENT_INPUT_CONTRACT_VERSION,
        "sourceArtifactRefs": list(dict.fromkeys(refs)),
        "sourceContentHash": str(source_content_hash or ""),
        "projectedContentHash": projected_hash,
        "payload": payload,
        "projectionAudit": {
            "stage": stage,
            "projectedChars": chars,
            "estimatedTokens": estimated_tokens(payload),
            "itemCharBudget": limit,
            "budgetStatus": "passed" if chars <= limit else "exceeded",
        },
        "hardInterface": {
            "enabled": True,
            "fallbackAllowed": False,
            "fullArtifactReadByAgentAllowed": False,
            "gatewayBusinessCompactionAllowed": False,
        },
    }
    assert_agent_input_envelope(envelope, expected_schema=schema)
    return envelope


def validate_agent_input_envelope(
    value: Any,
    *,
    expected_schema: str | None = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    if not isinstance(value, dict):
        return {"ok": False, "errors": ["envelope_not_object"]}
    schema = str(value.get("schema") or "")
    if expected_schema and schema != expected_schema:
        errors.append("schema_mismatch")
    try:
        _schema_stage(schema)
    except AgentInputContractError:
        errors.append("unsupported_schema")
    unknown = sorted(set(value) - _TOP_LEVEL_KEYS)
    if unknown:
        errors.append("unknown_top_level_fields:" + ",".join(unknown))
    if value.get("projectionVersion") != AGENT_INPUT_CONTRACT_VERSION:
        errors.append("projection_version_mismatch")
    hard = value.get("hardInterface") if isinstance(value.get("hardInterface"), dict) else {}
    if hard.get("enabled") is not True or hard.get("fallbackAllowed") is not False:
        errors.append("hard_interface_not_sealed")
    refs = value.get("sourceArtifactRefs")
    if not isinstance(refs, list) or not refs or any(not str(ref).startswith("ART-") for ref in refs):
        errors.append("invalid_source_artifact_refs")
    payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
    if not payload:
        errors.append("payload_missing")
    allowed = _AGENT1_PAYLOAD_KEYS if schema == AGENT1_INPUT_SCHEMA else _AGENT2_PAYLOAD_KEYS
    payload_unknown = sorted(set(payload) - allowed) if payload else []
    if payload_unknown:
        errors.append("unknown_payload_fields:" + ",".join(payload_unknown))
    if payload and content_hash(payload) != str(value.get("projectedContentHash") or ""):
        errors.append("projected_content_hash_mismatch")
    chars = len(stable_json(payload)) if payload else 0
    if schema in {AGENT1_INPUT_SCHEMA, AGENT2_INPUT_SCHEMA} and chars > _item_char_budget(schema):
        errors.append("projection_item_budget_exceeded")
    if schema == AGENT1_INPUT_SCHEMA:
        for key in ("productId", "storeId", "productIdentity", "inputContract"):
            if payload.get(key) in (None, "", {}, []):
                errors.append(f"agent1_payload_missing:{key}")
    if schema == AGENT2_INPUT_SCHEMA:
        for key in (
            "packageId",
            "productId",
            "storeId",
            "agent1OperatingJudgment",
            "matrixDispatch",
            "actionParameterPack",
            "inputContract",
        ):
            if payload.get(key) in (None, "", {}, []):
                errors.append(f"agent2_payload_missing:{key}")
    return {
        "ok": not errors,
        "version": AGENT_INPUT_CONTRACT_VERSION,
        "schema": schema,
        "errors": errors,
        "projectedChars": chars,
        "estimatedTokens": estimated_tokens(payload) if payload else 0,
    }


def assert_agent_input_envelope(
    value: Any,
    *,
    expected_schema: str | None = None,
) -> Dict[str, Any]:
    result = validate_agent_input_envelope(value, expected_schema=expected_schema)
    if result.get("ok") is not True:
        raise AgentInputContractError("agent_input_contract_invalid", result)
    return result


def split_envelopes_by_budget(
    values: List[Dict[str, Any]],
    *,
    expected_schema: str,
    max_items: int,
) -> List[List[Dict[str, Any]]]:
    limit = batch_char_budget(expected_schema)
    batches: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_chars = 0
    for value in values:
        assert_agent_input_envelope(value, expected_schema=expected_schema)
        chars = int((value.get("projectionAudit") or {}).get("projectedChars") or 0)
        if current and (len(current) >= max(1, max_items) or current_chars + chars > limit):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(value)
        current_chars += chars
    if current:
        batches.append(current)
    return batches


__all__ = [
    "AGENT_INPUT_CONTRACT_VERSION",
    "AGENT1_INPUT_SCHEMA",
    "AGENT2_INPUT_SCHEMA",
    "AGENT1_MAX_ITEM_CHARS",
    "AGENT1_MAX_BATCH_CHARS",
    "AGENT2_MAX_ITEM_CHARS",
    "AGENT2_MAX_BATCH_CHARS",
    "AgentInputContractError",
    "stable_json",
    "content_hash",
    "estimated_tokens",
    "batch_char_budget",
    "build_projection_envelope",
    "validate_agent_input_envelope",
    "assert_agent_input_envelope",
    "split_envelopes_by_budget",
]
