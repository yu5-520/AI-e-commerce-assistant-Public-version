"""V22.5.8 Agent1 evidence-continuity input contract."""
from __future__ import annotations
import hashlib, json, math
from typing import Any, Dict, Iterable, List
from src.services import agent_input_contract_v230_service as legacy

AGENT1_INPUT_PROJECTION_VERSION = "22.5.8"
AGENT1_INPUT_SCHEMA = "agent_input.agent1.v3"
AGENT2_INPUT_SCHEMA = legacy.AGENT2_INPUT_SCHEMA
AGENT1_MAX_ITEM_CHARS = 22_000
AGENT1_MAX_BATCH_CHARS = 72_000
AGENT2_MAX_ITEM_CHARS = legacy.AGENT2_MAX_ITEM_CHARS
AGENT2_MAX_BATCH_CHARS = legacy.AGENT2_MAX_BATCH_CHARS
AGENT1_PROJECTION_DEDUPE_REVISION = "2026.08.11.1"

_AGENT1_TOP_LEVEL_KEYS = {"schema","projectionVersion","sourceArtifactRefs","sourceContentHash","projectedContentHash","payload","projectionAudit","hardInterface"}
_AGENT1_PAYLOAD_KEYS = {"productId","storeId","signalId","correlationId","dataVersion","productIdentity","profileLayer","snapshotLayer","metricLayer","trendContext","sourceLineageValidation","strongRelations","crossValidation","factLayerValidation","dataFingerprint","diagnosticRag","inputContract"}

class AgentInputContractV2258Error(RuntimeError):
    def __init__(self, code: str, detail: Any = None) -> None:
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")

def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

def content_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()

def estimated_tokens(value: Any) -> int:
    return max(1, math.ceil(len(stable_json(value)) / 2.0))

def batch_char_budget(schema: str) -> int:
    if schema == AGENT1_INPUT_SCHEMA:
        return AGENT1_MAX_BATCH_CHARS
    if schema == AGENT2_INPUT_SCHEMA:
        return AGENT2_MAX_BATCH_CHARS
    raise AgentInputContractV2258Error("unsupported_agent_input_schema", schema)


def _bounded(value: Any, *, depth: int = 0, max_depth: int = 4, max_list: int = 12, max_keys: int = 32) -> Any:
    if depth >= max_depth:
        if isinstance(value, str):
            return value[:320]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return str(value)[:320]
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key in sorted(value, key=str)[:max_keys]:
            child = value[key]
            if child in (None, "", [], {}):
                continue
            result[str(key)] = _bounded(
                child,
                depth=depth + 1,
                max_depth=max_depth,
                max_list=max_list,
                max_keys=max_keys,
            )
        return result
    if isinstance(value, list):
        return [
            _bounded(
                child,
                depth=depth + 1,
                max_depth=max_depth,
                max_list=max_list,
                max_keys=max_keys,
            )
            for child in value[:max_list]
        ]
    if isinstance(value, str):
        return value[:480]
    return value


def _dedupe_field_signal(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    current = item.get("current", item.get("latest", item.get("currentValue")))
    ratio = item.get("changeRatio", item.get("changeRate", item.get("deltaRate")))
    result = {
        "metricCode": item.get("metricCode") or item.get("code") or item.get("metricName"),
        "metricName": item.get("metricName") or item.get("label"),
        "previous": item.get("previous", item.get("previousValue")),
        "current": current,
        "changeRatio": ratio,
        "meaningfulChange": item.get("meaningfulChange"),
        "signalStrength": item.get("signalStrength") or item.get("strength"),
        "signalType": item.get("signalType") or item.get("type"),
        "direction": item.get("direction") or item.get("trendDirection"),
        "sampleCount": item.get("sampleCount") or item.get("periodCount"),
        "reason": str(item.get("reason") or item.get("summary") or "")[:180] or None,
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _compact_cross_validation(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    decision = value.get("decision") if isinstance(value.get("decision"), dict) else {}
    decision_keys = (
        "hypothesisCode",
        "hypothesisLabel",
        "status",
        "severity",
        "confidence",
        "businessImpact",
        "urgency",
        "actionIntensity",
        "primaryEvidence",
        "relatedEvidence",
        "independentEvidenceGroups",
        "conflictEvidenceGroups",
        "temporalConfirmationCount",
        "rule",
    )
    compact_decision = {
        key: _bounded(decision.get(key), max_depth=4, max_list=8, max_keys=24)
        for key in decision_keys
        if decision.get(key) not in (None, "", [], {})
    }
    keep = (
        "version",
        "contract",
        "changedMetricCount",
        "abnormalMetricCount",
        "changedMetrics",
        "abnormalMetrics",
        "recentDirectComparisonWindow",
        "trendOverlayWindows",
        "sourceVersionScoreContribution",
        "metricBlockingFactors",
        "lineageOwner",
        "lineageFieldsRemoved",
        "rule",
    )
    result = {
        key: _bounded(value.get(key), max_depth=4, max_list=16, max_keys=32)
        for key in keep
        if value.get(key) not in (None, "", [], {})
    }
    if compact_decision:
        result["decision"] = compact_decision
    result["projectionDedupe"] = {
        "revision": AGENT1_PROJECTION_DEDUPE_REVISION,
        "removedDuplicateHypothesisList": "hypotheses" in value,
        "removedDuplicateTimeSeriesFeatures": "timeSeriesFeatures" in value,
    }
    return result


def _compact_trend_context(value: Any, metric_codes: List[str], *, hard: bool = False) -> Any:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    features = value.get("timeSeriesFeatures") if isinstance(value.get("timeSeriesFeatures"), dict) else {}
    preferred = [code for code in metric_codes if code in features]
    for code in sorted(features):
        if code not in preferred:
            preferred.append(code)
    limit = 8 if hard else 14
    feature_keys = (
        "metricCode",
        "current",
        "previous",
        "previousDelta",
        "mom",
        "yoy",
        "slope5",
        "slope10",
        "slope30",
        "volatility10",
        "streakDirection",
        "streakLength",
        "seasonalResidual",
        "sampleCount",
        "sampleConfidence",
    )
    result["timeSeriesFeatures"] = {
        str(code): {
            key: feature.get(key)
            for key in feature_keys
            if isinstance((feature := features.get(code)), dict)
            and feature.get(key) not in (None, "", [], {})
        }
        for code in preferred[:limit]
        if isinstance(features.get(code), dict)
    }
    for key in (
        "primaryEvidence",
        "relatedEvidence",
        "operatingDecisionSummary",
    ):
        if key in result:
            result[key] = _bounded(result[key], max_depth=4, max_list=8, max_keys=24)
    for key in (
        "recentFiveTrendSummary",
        "historicalTrendSummary",
        "trendSummary",
        "recentFiveOrLatestFacts",
    ):
        if hard:
            result.pop(key, None)
        elif key in result:
            result[key] = _bounded(result[key], max_depth=3, max_list=8, max_keys=24)
    result["projectionDedupeRevision"] = AGENT1_PROJECTION_DEDUPE_REVISION
    return {key: child for key, child in result.items() if child not in (None, "", [], {})}


def _compact_agent1_payload(payload: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    result = dict(payload)
    original_chars = len(stable_json(payload))
    if original_chars <= AGENT1_MAX_ITEM_CHARS:
        return result, {
            "applied": False,
            "revision": AGENT1_PROJECTION_DEDUPE_REVISION,
            "originalProjectedChars": original_chars,
        }

    snapshot = dict(result.get("snapshotLayer")) if isinstance(result.get("snapshotLayer"), dict) else {}
    signals = snapshot.get("fieldSignals") if isinstance(snapshot.get("fieldSignals"), list) else []
    deduped_signals = [_dedupe_field_signal(item) for item in signals]
    snapshot["fieldSignals"] = deduped_signals
    snapshot["fieldSignalCount"] = len(deduped_signals)
    snapshot["semanticContinuity"] = True
    snapshot["projectionDedupeRevision"] = AGENT1_PROJECTION_DEDUPE_REVISION
    result["snapshotLayer"] = snapshot

    metric_codes = [
        str(item.get("metricCode"))
        for item in deduped_signals
        if isinstance(item, dict) and item.get("metricCode")
    ]
    result["crossValidation"] = _compact_cross_validation(result.get("crossValidation"))
    result["trendContext"] = _compact_trend_context(result.get("trendContext"), metric_codes)
    if "strongRelations" in result:
        result["strongRelations"] = _bounded(result["strongRelations"], max_depth=4, max_list=8, max_keys=24)
    if "factLayerValidation" in result:
        result["factLayerValidation"] = _bounded(result["factLayerValidation"], max_depth=3, max_list=8, max_keys=24)
    if "diagnosticRag" in result:
        result["diagnosticRag"] = _bounded(result["diagnosticRag"], max_depth=4, max_list=10, max_keys=32)

    chars = len(stable_json(result))
    hard_compaction = False
    if chars > AGENT1_MAX_ITEM_CHARS:
        hard_compaction = True
        result["trendContext"] = _compact_trend_context(result.get("trendContext"), metric_codes, hard=True)
        if isinstance(result.get("metricLayer"), dict):
            result["metricLayer"] = {
                key: result["metricLayer"][key]
                for key in sorted(result["metricLayer"], key=str)[:32]
            }
        if "strongRelations" in result:
            result["strongRelations"] = _bounded(result["strongRelations"], max_depth=3, max_list=4, max_keys=16)
        if "diagnosticRag" in result:
            result["diagnosticRag"] = _bounded(result["diagnosticRag"], max_depth=3, max_list=6, max_keys=20)
        chars = len(stable_json(result))

    return result, {
        "applied": True,
        "revision": AGENT1_PROJECTION_DEDUPE_REVISION,
        "originalProjectedChars": original_chars,
        "projectedCharsAfterDedupe": chars,
        "hardCompactionApplied": hard_compaction,
        "fieldSignalCountBefore": len(signals),
        "fieldSignalCountAfter": len(deduped_signals),
        "fieldSignalSemanticCountPreserved": len(signals) == len(deduped_signals),
        "itemCharBudgetChanged": False,
    }


def build_projection_envelope(*, schema: str, payload: Dict[str, Any], source_artifact_refs: Iterable[str], source_content_hash: str) -> Dict[str, Any]:
    if schema != AGENT1_INPUT_SCHEMA:
        return legacy.build_projection_envelope(schema=schema, payload=payload, source_artifact_refs=source_artifact_refs, source_content_hash=source_content_hash)
    refs = [str(value) for value in source_artifact_refs if str(value).startswith("ART-")]
    if not refs:
        raise AgentInputContractV2258Error("source_artifact_ref_required")
    if not isinstance(payload, dict) or not payload:
        raise AgentInputContractV2258Error("projected_payload_required")
    payload, dedupe_audit = _compact_agent1_payload(payload)
    chars = len(stable_json(payload))
    envelope = {
        "schema": schema,
        "projectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
        "sourceArtifactRefs": list(dict.fromkeys(refs)),
        "sourceContentHash": str(source_content_hash or ""),
        "projectedContentHash": content_hash(payload),
        "payload": payload,
        "projectionAudit": {
            "stage": "agent1",
            "projectedChars": chars,
            "estimatedTokens": estimated_tokens(payload),
            "itemCharBudget": AGENT1_MAX_ITEM_CHARS,
            "budgetStatus": "passed" if chars <= AGENT1_MAX_ITEM_CHARS else "exceeded",
            "semanticContinuity": True,
            "sourceLineageRequired": True,
            "sourceLineageOwner": "sourceLineageValidation",
            "crossValidationMayOwnLineage": False,
            "fieldSignalLimit": 32,
            "trendSemanticVersion": AGENT1_INPUT_PROJECTION_VERSION,
            "duplicateSignalRepresentationForbidden": True,
            "deterministicProjectionDedupe": dedupe_audit,
        },
        "hardInterface": {"enabled": True, "fallbackAllowed": False, "fullArtifactReadByAgentAllowed": False, "gatewayBusinessCompactionAllowed": False},
    }
    assert_agent_input_envelope(envelope, expected_schema=schema)
    return envelope

def _as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0

def validate_agent_input_envelope(value: Any, *, expected_schema: str | None = None) -> Dict[str, Any]:
    if expected_schema == AGENT2_INPUT_SCHEMA or (isinstance(value, dict) and value.get("schema") == AGENT2_INPUT_SCHEMA):
        return legacy.validate_agent_input_envelope(value, expected_schema=expected_schema)
    errors: List[str] = []
    if not isinstance(value, dict):
        return {"ok": False, "errors": ["envelope_not_object"]}
    schema = str(value.get("schema") or "")
    if expected_schema and schema != expected_schema:
        errors.append("schema_mismatch")
    if schema != AGENT1_INPUT_SCHEMA:
        errors.append("unsupported_schema")
    unknown = sorted(set(value) - _AGENT1_TOP_LEVEL_KEYS)
    if unknown:
        errors.append("unknown_top_level_fields:" + ",".join(unknown))
    if value.get("projectionVersion") != AGENT1_INPUT_PROJECTION_VERSION:
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
    payload_unknown = sorted(set(payload) - _AGENT1_PAYLOAD_KEYS) if payload else []
    if payload_unknown:
        errors.append("unknown_payload_fields:" + ",".join(payload_unknown))
    if payload and content_hash(payload) != str(value.get("projectedContentHash") or ""):
        errors.append("projected_content_hash_mismatch")
    chars = len(stable_json(payload)) if payload else 0
    if chars > AGENT1_MAX_ITEM_CHARS:
        errors.append("projection_item_budget_exceeded")
    for key in ("productId","storeId","productIdentity","snapshotLayer","sourceLineageValidation","inputContract"):
        if payload.get(key) in (None, "", {}, []):
            errors.append(f"agent1_payload_missing:{key}")
    input_contract = payload.get("inputContract") if isinstance(payload.get("inputContract"), dict) else {}
    if input_contract.get("schema") != AGENT1_INPUT_SCHEMA:
        errors.append("agent1_input_contract_schema_mismatch")
    if input_contract.get("projectionVersion") != AGENT1_INPUT_PROJECTION_VERSION:
        errors.append("agent1_input_contract_projection_mismatch")
    signals = ((payload.get("snapshotLayer") or {}).get("fieldSignals")) if isinstance(payload.get("snapshotLayer"), dict) else None
    if not isinstance(signals, list) or not signals:
        errors.append("agent1_field_signals_missing")
    lineage = payload.get("sourceLineageValidation") if isinstance(payload.get("sourceLineageValidation"), dict) else {}
    source_version_count = _as_int(lineage.get("sourceVersionCount"))
    source_dataset_count = _as_int(lineage.get("sourceDatasetCount"))
    source_artifact_count = _as_int(lineage.get("sourceArtifactCount"))
    if source_version_count <= 0: errors.append("source_lineage_version_count_missing")
    if source_dataset_count <= 0: errors.append("source_lineage_dataset_count_missing")
    if source_artifact_count <= 0: errors.append("source_lineage_artifact_count_missing")
    if lineage.get("contentHashVerified") is not True: errors.append("source_lineage_content_hash_unverified")
    if lineage.get("sourceIdentityComplete") is not True: errors.append("source_lineage_identity_incomplete")
    if lineage.get("blockingFactors") not in (None, []): errors.append("source_lineage_blocked")
    cross = payload.get("crossValidation") if isinstance(payload.get("crossValidation"), dict) else {}
    forbidden_cross_keys = {"sourceVersionCount","sourceDatasetCount","sourceRecordCount","businessDateCount","sourceIdentityComplete","sourceIdentityStatus","sourceLineageStatus","blockingFactors"}
    cross_lineage_keys = sorted(forbidden_cross_keys.intersection(cross))
    if cross_lineage_keys:
        errors.append("cross_validation_owns_lineage:" + ",".join(cross_lineage_keys))
    lineage_hash = content_hash(lineage) if lineage else ""
    if str(input_contract.get("sourceLineageHash") or "") != lineage_hash:
        errors.append("source_lineage_hash_mismatch")
    if input_contract.get("trendSemanticVersion") != AGENT1_INPUT_PROJECTION_VERSION:
        errors.append("trend_semantic_version_mismatch")
    return {"ok": not errors, "version": AGENT1_INPUT_PROJECTION_VERSION, "schema": schema, "errors": errors, "projectedChars": chars, "estimatedTokens": estimated_tokens(payload) if payload else 0, "sourceVersionCount": source_version_count, "sourceDatasetCount": source_dataset_count}

def assert_agent_input_envelope(value: Any, *, expected_schema: str | None = None) -> Dict[str, Any]:
    result = validate_agent_input_envelope(value, expected_schema=expected_schema)
    if result.get("ok") is not True:
        raise AgentInputContractV2258Error("agent_input_contract_invalid", result)
    return result

def split_envelopes_by_budget(values: List[Dict[str, Any]], *, expected_schema: str, max_items: int) -> List[List[Dict[str, Any]]]:
    if expected_schema == AGENT2_INPUT_SCHEMA:
        return legacy.split_envelopes_by_budget(values, expected_schema=expected_schema, max_items=max_items)
    limit = batch_char_budget(expected_schema)
    batches, current, current_chars = [], [], 0
    for value in values:
        assert_agent_input_envelope(value, expected_schema=expected_schema)
        chars = int((value.get("projectionAudit") or {}).get("projectedChars") or 0)
        if current and (len(current) >= max(1, max_items) or current_chars + chars > limit):
            batches.append(current)
            current, current_chars = [], 0
        current.append(value)
        current_chars += chars
    if current:
        batches.append(current)
    return batches

__all__ = ["AGENT1_INPUT_PROJECTION_VERSION","AGENT1_INPUT_SCHEMA","AGENT2_INPUT_SCHEMA","AGENT1_MAX_ITEM_CHARS","AGENT1_MAX_BATCH_CHARS","AGENT1_PROJECTION_DEDUPE_REVISION","AgentInputContractV2258Error","stable_json","content_hash","estimated_tokens","batch_char_budget","build_projection_envelope","validate_agent_input_envelope","assert_agent_input_envelope","split_envelopes_by_budget"]
