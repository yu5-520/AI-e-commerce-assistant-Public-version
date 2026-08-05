"""V22.5 three-Agent semantic input contracts.

Agent1 keeps the V22.3 diagnostic projection. Agent2 consumes a vertical/platform
action draft projection. Agent3 consumes the validated draft plus company operating
and SOP RAG context. No Agent may widen its scope to full upstream artifacts.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Iterable, List

from src.services import agent_input_contract_v230_service as legacy

THREE_AGENT_PIPELINE_VERSION = "22.5.0"
AGENT_INPUT_CONTRACT_VERSION = THREE_AGENT_PIPELINE_VERSION

AGENT1_INPUT_SCHEMA = legacy.AGENT1_INPUT_SCHEMA
AGENT2_DRAFT_INPUT_SCHEMA = "agent_input.agent2_draft.v1"
AGENT3_SOP_INPUT_SCHEMA = "agent_input.agent3_sop.v1"

# Compatibility alias. It now means the Agent2 draft input, not a final SOP plan.
AGENT2_INPUT_SCHEMA = AGENT2_DRAFT_INPUT_SCHEMA

AGENT1_MAX_ITEM_CHARS = legacy.AGENT1_MAX_ITEM_CHARS
AGENT1_MAX_BATCH_CHARS = legacy.AGENT1_MAX_BATCH_CHARS
AGENT2_MAX_ITEM_CHARS = 16_000
AGENT2_MAX_BATCH_CHARS = 48_000
AGENT3_MAX_ITEM_CHARS = 20_000
AGENT3_MAX_BATCH_CHARS = 40_000

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
_AGENT2_DRAFT_KEYS = {
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
    "verticalActionRag",
    "diagnosticExtensionContract",
    "diagnosticExtensions",
    "inputContract",
}
_AGENT3_SOP_KEYS = {
    "packageId",
    "itemId",
    "dataVersion",
    "productId",
    "storeId",
    "productTitle",
    "title",
    "productIdentity",
    "agent1DecisionIR",
    "agent1OperatingJudgment",
    "matrixDispatch",
    "lockedActionFamily",
    "actionParameterPack",
    "recentFiveOrLatestFacts",
    "agent2ActionDraft",
    "agent2DraftExecutionProof",
    "companyOperatingPolicySnapshot",
    "companySopRagSnapshot",
    "approvalPolicySnapshot",
    "brandStyleSnapshot",
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
    return max(1, math.ceil(len(stable_json(value)) / 2.0))


def _item_budget(schema: str) -> int:
    if schema == AGENT1_INPUT_SCHEMA:
        return AGENT1_MAX_ITEM_CHARS
    if schema == AGENT2_DRAFT_INPUT_SCHEMA:
        return AGENT2_MAX_ITEM_CHARS
    if schema == AGENT3_SOP_INPUT_SCHEMA:
        return AGENT3_MAX_ITEM_CHARS
    raise AgentInputContractError("unsupported_agent_input_schema", schema)


def batch_char_budget(schema: str) -> int:
    if schema == AGENT1_INPUT_SCHEMA:
        return AGENT1_MAX_BATCH_CHARS
    if schema == AGENT2_DRAFT_INPUT_SCHEMA:
        return AGENT2_MAX_BATCH_CHARS
    if schema == AGENT3_SOP_INPUT_SCHEMA:
        return AGENT3_MAX_BATCH_CHARS
    raise AgentInputContractError("unsupported_agent_input_schema", schema)


def _stage(schema: str) -> str:
    if schema == AGENT1_INPUT_SCHEMA:
        return "agent1"
    if schema == AGENT2_DRAFT_INPUT_SCHEMA:
        return "agent2_draft"
    if schema == AGENT3_SOP_INPUT_SCHEMA:
        return "agent3_sop"
    raise AgentInputContractError("unsupported_agent_input_schema", schema)


def build_projection_envelope(
    *,
    schema: str,
    payload: Dict[str, Any],
    source_artifact_refs: Iterable[str],
    source_content_hash: str,
) -> Dict[str, Any]:
    refs = [str(value) for value in source_artifact_refs if str(value).startswith("ART-")]
    if not refs:
        raise AgentInputContractError("source_artifact_ref_required")
    if not isinstance(payload, dict) or not payload:
        raise AgentInputContractError("projected_payload_required")
    chars = len(stable_json(payload))
    envelope = {
        "schema": schema,
        "projectionVersion": AGENT_INPUT_CONTRACT_VERSION,
        "sourceArtifactRefs": list(dict.fromkeys(refs)),
        "sourceContentHash": str(source_content_hash or ""),
        "projectedContentHash": content_hash(payload),
        "payload": payload,
        "projectionAudit": {
            "stage": _stage(schema),
            "projectedChars": chars,
            "estimatedTokens": estimated_tokens(payload),
            "itemCharBudget": _item_budget(schema),
            "budgetStatus": "passed" if chars <= _item_budget(schema) else "exceeded",
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
    if not isinstance(value, dict):
        return {"ok": False, "errors": ["envelope_not_object"]}
    schema = str(value.get("schema") or "")
    if schema == AGENT1_INPUT_SCHEMA:
        # Existing Agent1 artifacts remain valid during the V22.5 semantic split.
        return legacy.validate_agent_input_envelope(
            value,
            expected_schema=expected_schema or AGENT1_INPUT_SCHEMA,
        )

    errors: List[str] = []
    if expected_schema and schema != expected_schema:
        errors.append("schema_mismatch")
    if schema not in {AGENT2_DRAFT_INPUT_SCHEMA, AGENT3_SOP_INPUT_SCHEMA}:
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
    if not isinstance(refs, list) or not refs or any(
        not str(ref).startswith("ART-") for ref in refs
    ):
        errors.append("invalid_source_artifact_refs")
    payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
    if not payload:
        errors.append("payload_missing")
    allowed = _AGENT2_DRAFT_KEYS if schema == AGENT2_DRAFT_INPUT_SCHEMA else _AGENT3_SOP_KEYS
    payload_unknown = sorted(set(payload) - allowed) if payload else []
    if payload_unknown:
        errors.append("unknown_payload_fields:" + ",".join(payload_unknown))
    if payload and content_hash(payload) != str(value.get("projectedContentHash") or ""):
        errors.append("projected_content_hash_mismatch")
    chars = len(stable_json(payload)) if payload else 0
    if payload and chars > _item_budget(schema):
        errors.append("projection_item_budget_exceeded")

    required = (
        (
            "packageId",
            "productId",
            "storeId",
            "agent1OperatingJudgment",
            "matrixDispatch",
            "actionParameterPack",
            "inputContract",
        )
        if schema == AGENT2_DRAFT_INPUT_SCHEMA
        else (
            "packageId",
            "productId",
            "storeId",
            "lockedActionFamily",
            "agent2ActionDraft",
            "companyOperatingPolicySnapshot",
            "companySopRagSnapshot",
            "inputContract",
        )
    )
    prefix = "agent2_draft" if schema == AGENT2_DRAFT_INPUT_SCHEMA else "agent3_sop"
    for key in required:
        if payload.get(key) in (None, "", {}, []):
            errors.append(f"{prefix}_payload_missing:{key}")
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
        raise AgentInputContractError(
            "agent_input_contract_invalid",
            result.get("errors"),
        )
    return value


def split_envelopes_by_budget(
    envelopes: List[Dict[str, Any]],
    *,
    expected_schema: str,
    max_items: int,
) -> List[List[Dict[str, Any]]]:
    if expected_schema == AGENT1_INPUT_SCHEMA:
        return legacy.split_envelopes_by_budget(
            envelopes,
            expected_schema=expected_schema,
            max_items=max_items,
        )
    limit = batch_char_budget(expected_schema)
    batches: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_chars = 0
    for envelope in envelopes:
        assert_agent_input_envelope(envelope, expected_schema=expected_schema)
        chars = len(stable_json(envelope.get("payload") or {}))
        if current and (len(current) >= max(1, int(max_items)) or current_chars + chars > limit):
            batches.append(current)
            current = []
            current_chars = 0
        if chars > limit:
            raise AgentInputContractError("projection_batch_budget_exceeded", chars)
        current.append(envelope)
        current_chars += chars
    if current:
        batches.append(current)
    return batches


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT_INPUT_CONTRACT_VERSION",
    "AGENT1_INPUT_SCHEMA",
    "AGENT2_INPUT_SCHEMA",
    "AGENT2_DRAFT_INPUT_SCHEMA",
    "AGENT3_SOP_INPUT_SCHEMA",
    "AGENT1_MAX_ITEM_CHARS",
    "AGENT1_MAX_BATCH_CHARS",
    "AGENT2_MAX_ITEM_CHARS",
    "AGENT2_MAX_BATCH_CHARS",
    "AGENT3_MAX_ITEM_CHARS",
    "AGENT3_MAX_BATCH_CHARS",
    "AgentInputContractError",
    "stable_json",
    "content_hash",
    "estimated_tokens",
    "build_projection_envelope",
    "validate_agent_input_envelope",
    "assert_agent_input_envelope",
    "split_envelopes_by_budget",
]
