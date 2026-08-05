"""V22.5.8 Agent1 source-lineage and trend-semantic transport."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from src.services import agent_input_transport_v230_service as legacy
from src.services.agent_input_contract_v2258_service import (
    AGENT1_INPUT_PROJECTION_VERSION,
    AGENT1_INPUT_SCHEMA,
    AGENT2_INPUT_SCHEMA,
    assert_agent_input_envelope,
    build_projection_envelope,
    content_hash,
)
from src.services.artifact_transport_service import (
    inspect_artifact,
    resolve_artifact,
    store_artifact,
    validate_artifact,
)
from src.services.pipeline_artifact_contract_service import (
    artifact_refs_from_row,
    attach_pipeline_artifact_ref,
)

AGENT_INPUT_TRANSPORT_VERSION = "22.5.8"
MAX_AGENT1_FIELD_SIGNALS = 32


class AgentInputTransportV2258Error(RuntimeError):
    def __init__(self, code: str, detail: Any = None) -> None:
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _payload(value: Dict[str, Any]) -> Dict[str, Any]:
    return legacy._payload(value)


def _identity(value: Dict[str, Any]) -> Dict[str, Any]:
    return legacy._identity(value)


def _compact(value: Any, **kwargs: Any) -> Any:
    return legacy._compact(value, **kwargs)


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _positive_max(root: Dict[str, Any], names: set[str]) -> int:
    result = 0
    for key, value in _walk(root):
        if key not in names:
            continue
        try:
            number = max(0, int(float(value or 0)))
        except Exception:
            continue
        result = max(result, number)
    return result


def _unique_values(root: Dict[str, Any], names: set[str]) -> List[str]:
    result: List[str] = []
    for key, value in _walk(root):
        if key not in names:
            continue
        for candidate in value if isinstance(value, list) else [value]:
            text = _text(candidate, 240)
            if text and text not in result:
                result.append(text)
    return result


def _source_lineage(root: Dict[str, Any], source_ref: str, source_hash: str) -> Dict[str, Any]:
    versions = _unique_values(
        root,
        {
            "dataVersion",
            "sourceDataVersion",
            "snapshotDataVersion",
            "sourceVersions",
            "dataVersions",
            "historyDataVersions",
            "snapshotVersions",
        },
    )
    dates = [
        value
        for value in _unique_values(
            root,
            {
                "businessDate",
                "metricDate",
                "reportDate",
                "dataDate",
                "snapshotDate",
                "statDate",
                "bizDate",
            },
        )
        if re.search(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", value)
    ]
    version_count = max(
        _positive_max(root, {"sourceVersionCount"}),
        len(versions),
        1 if root.get("dataVersion") else 0,
    )
    dataset_count = max(
        _positive_max(root, {"sourceDatasetCount", "datasetCount"}),
        version_count,
    )
    date_count = max(
        _positive_max(root, {"businessDateCount", "reportDateCount"}),
        len(dates),
        version_count,
    )
    record_count = max(
        _positive_max(root, {"sourceRecordCount", "recordCount"}),
        version_count,
    )
    artifact_count = 1 if source_ref.startswith("ART-") else 0
    hash_verified = bool(source_hash)
    complete = bool(
        version_count > 0
        and dataset_count > 0
        and date_count > 0
        and artifact_count > 0
        and hash_verified
    )
    blockers = []
    if version_count <= 0:
        blockers.append("source_version_count_missing")
    if dataset_count <= 0:
        blockers.append("source_dataset_count_missing")
    if date_count <= 0:
        blockers.append("business_date_count_missing")
    if artifact_count <= 0:
        blockers.append("source_artifact_ref_missing")
    if not hash_verified:
        blockers.append("source_content_hash_missing")
    return {
        "version": AGENT1_INPUT_PROJECTION_VERSION,
        "status": "complete" if complete else "incomplete",
        "sourceVersionCount": version_count,
        "sourceDatasetCount": dataset_count,
        "businessDateCount": date_count,
        "sourceRecordCount": record_count,
        "sourceArtifactCount": artifact_count,
        "contentHashVerified": hash_verified,
        "sourceIdentityComplete": complete,
        "dataVersions": versions[:12],
        "businessDates": dates[:12],
        "sourceArtifactRefs": [source_ref] if artifact_count else [],
        "sourceContentHash": source_hash,
        "blockingFactors": blockers,
        "derivedFromImmutableLineage": True,
    }


_LINEAGE_KEYS = {
    "sourceVersionCount",
    "sourceDatasetCount",
    "sourceRecordCount",
    "businessDateCount",
    "sourceArtifactCount",
    "sourceIdentityComplete",
    "sourceIdentityStatus",
    "sourceLineageStatus",
    "blockingFactors",
}


def _source_reason(value: Any) -> bool:
    text = _text(value, 800).lower()
    return any(
        marker in text
        for marker in (
            "source identity",
            "source_identity",
            "source dataset",
            "source version",
            "来源身份",
            "来源血缘",
            "数据来源",
        )
    )


def _metric_cross_validation(root: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in _dict(root.get("crossValidation")).items():
        if key in _LINEAGE_KEYS:
            continue
        if key == "reason" and _source_reason(value):
            continue
        if key == "blockingFactors":
            kept = [item for item in _arr(value) if not _source_reason(item)]
            if kept:
                result["metricBlockingFactors"] = _compact(
                    kept,
                    max_depth=4,
                    max_list=12,
                    max_keys=24,
                )
            continue
        if value not in (None, "", [], {}):
            result[key] = _compact(
                value,
                max_depth=5,
                max_list=16,
                max_keys=48,
            )
    result.update(
        lineageOwner="sourceLineageValidation",
        lineageFieldsRemoved=True,
    )
    return result


def _signals(root: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = _dict(root.get("snapshotLayer"))
    result = []
    for item in _arr(snapshot.get("fieldSignals") or root.get("fieldSignals"))[:32]:
        if not isinstance(item, dict):
            continue
        clean = {
            "metricCode": item.get("metricCode") or item.get("code") or item.get("metricName"),
            "metricName": item.get("metricName") or item.get("label"),
            "previous": item.get("previous", item.get("previousValue")),
            "current": item.get("current", item.get("currentValue", item.get("latest"))),
            "latest": item.get("latest", item.get("current", item.get("currentValue"))),
            "changeRatio": item.get("changeRatio", item.get("changeRate", item.get("deltaRate"))),
            "changeRate": item.get("changeRate", item.get("changeRatio", item.get("deltaRate"))),
            "changeVsPrevious": item.get("changeVsPrevious", item.get("delta", item.get("changeValue"))),
            "meaningfulChange": item.get("meaningfulChange"),
            "signalStrength": item.get("signalStrength") or item.get("strength"),
            "signalType": item.get("signalType") or item.get("type"),
            "direction": item.get("direction") or item.get("trendDirection"),
            "sampleCount": item.get("sampleCount") or item.get("periodCount"),
            "windows": _compact(
                item.get("windows") or item.get("windowSummary") or item.get("trendWindows"),
                max_depth=5,
                max_list=16,
                max_keys=48,
            ),
            "reason": _text(item.get("reason") or item.get("summary"), 320) or None,
        }
        clean = {
            key: value
            for key, value in clean.items()
            if value not in (None, "", [], {})
        }
        if clean.get("metricCode"):
            result.append(clean)
    return result


def _metric_layer(root: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for layer_name in ("metricLayer", "snapshotLayer", "dynamicMetrics"):
        for key, value in _dict(root.get(layer_name)).items():
            if key in legacy._DROP_KEYS or key == "fieldSignals" or value in (None, "", [], {}):
                continue
            if isinstance(value, (str, int, float, bool)):
                result[str(key)] = _text(value, 240) if isinstance(value, str) else value
    return dict(list(result.items())[:64])


def _semantic_feature(value: Any) -> Any:
    if not isinstance(value, dict):
        return _compact(value, max_depth=5, max_list=20, max_keys=64)
    return {
        str(key): (
            child
            if isinstance(child, (str, int, float, bool))
            else _compact(child, max_depth=5, max_list=20, max_keys=64)
        )
        for key, child in value.items()
        if child not in (None, "", [], {})
    }


def _trend_context(root: Dict[str, Any]) -> Dict[str, Any]:
    package = _dict(root.get("agentProductSnapshotPackage")) or _dict(
        root.get("productSnapshotPackage")
    )
    operating = _dict(package.get("operatingDecision"))
    features = _dict(package.get("timeSeriesFeatures")) or _dict(root.get("timeSeriesFeatures"))
    result: Dict[str, Any] = {
        "timeSeriesFeatures": {
            str(metric): _semantic_feature(feature)
            for metric, feature in features.items()
            if feature not in (None, "", [], {})
        },
        "primaryEvidence": _compact(
            operating.get("primaryEvidence"),
            max_depth=6,
            max_list=16,
            max_keys=64,
        ),
        "relatedEvidence": _compact(
            operating.get("relatedEvidence"),
            max_depth=6,
            max_list=16,
            max_keys=64,
        ),
        "operatingDecisionSummary": _compact(
            {
                key: value
                for key, value in operating.items()
                if key
                in {
                    "summary",
                    "direction",
                    "trendState",
                    "trendWindowCount",
                    "historyWindowCount",
                    "sampleCount",
                    "confidence",
                    "volatility",
                    "streakDirection",
                    "streakLength",
                }
            },
            max_depth=5,
            max_list=16,
            max_keys=48,
        ),
        "sourcePackageVersion": package.get("version") or package.get("schemaVersion"),
        "trendSemanticVersion": AGENT1_INPUT_PROJECTION_VERSION,
    }
    for key in (
        "recentFiveTrendSummary",
        "historicalTrendSummary",
        "trendSummary",
        "recentFiveOrLatestFacts",
    ):
        value = root.get(key) or package.get(key)
        if value not in (None, "", [], {}):
            result[key] = _compact(
                value,
                max_depth=6,
                max_list=20,
                max_keys=64,
            )
    return {
        key: value
        for key, value in result.items()
        if value not in (None, "", [], {})
    }


def _policy(value: Dict[str, Any]) -> Dict[str, Any]:
    result = legacy._compact_policy(value)
    result.update(
        agent1InputProjectionVersion=AGENT1_INPUT_PROJECTION_VERSION,
        sourceLineageVersion=AGENT1_INPUT_PROJECTION_VERSION,
        trendSemanticVersion=AGENT1_INPUT_PROJECTION_VERSION,
    )
    return result


def compile_agent1_envelope(
    source: Dict[str, Any],
    *,
    source_ref: str,
    source_content_hash: str,
    policy_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    root = _payload(source)
    identity = _identity(source)
    product_id = _text(identity.get("productId"), 160)
    store_id = _text(identity.get("storeId"), 160)
    if not product_id or not store_id:
        raise AgentInputTransportV2258Error(
            "agent1_identity_missing",
            {"productId": product_id, "storeId": store_id},
        )
    signal_id = _text(
        source.get("signalId")
        or source.get("signal_id")
        or root.get("signalId")
        or root.get("signal_id"),
        180,
    )
    correlation_id = _text(
        source.get("correlationId")
        or source.get("pipelineItemId")
        or source.get("itemId")
        or f"{store_id}:{product_id}:{signal_id}",
        240,
    )
    policy = _policy(policy_context or {})
    lineage = _source_lineage(root, source_ref, source_content_hash)
    signals = _signals(root)
    payload = {
        "productId": product_id,
        "storeId": store_id,
        "signalId": signal_id or None,
        "correlationId": correlation_id,
        "dataVersion": source.get("dataVersion") or root.get("dataVersion"),
        "productIdentity": identity,
        "profileLayer": {
            key: value
            for key, value in identity.items()
            if key
            in {
                "productId",
                "storeId",
                "productTitle",
                "title",
                "platform",
                "verticalCategory",
                "productRole",
                "lifecycleStage",
            }
        },
        "snapshotLayer": {
            "fieldSignals": signals,
            "fieldSignalCount": len(signals),
            "semanticContinuity": True,
        },
        "metricLayer": _metric_layer(root),
        "trendContext": _trend_context(root),
        "sourceLineageValidation": lineage,
        "strongRelations": _compact(
            root.get("strongRelations") or root.get("relationFacts"),
            max_depth=6,
            max_list=16,
            max_keys=64,
        ),
        "crossValidation": _metric_cross_validation(root),
        "factLayerValidation": _compact(
            root.get("factLayerValidation"),
            max_depth=5,
            max_list=16,
            max_keys=48,
        ),
        "dataFingerprint": root.get("dataFingerprint") or source.get("dataFingerprint"),
        "diagnosticRag": policy,
        "inputContract": {
            "schema": AGENT1_INPUT_SCHEMA,
            "version": AGENT1_INPUT_PROJECTION_VERSION,
            "projectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
            "sourceRef": source_ref,
            "sourceContentHash": source_content_hash,
            "sourceLineageHash": content_hash(lineage),
            "trendSemanticVersion": AGENT1_INPUT_PROJECTION_VERSION,
            "policyContextHash": content_hash(policy),
            "semanticContinuity": True,
            "completeFieldSignalTransport": True,
            "trendContextTransport": True,
            "sourceLineageTransport": True,
            "fallbackAllowed": False,
            "fullSignalReadAllowed": False,
        },
    }
    payload = {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {})
    }
    return build_projection_envelope(
        schema=AGENT1_INPUT_SCHEMA,
        payload=payload,
        source_artifact_refs=[source_ref],
        source_content_hash=source_content_hash,
    )



def inspect_agent1_input_ref(
    row: Dict[str, Any],
    *,
    policy_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Plan REUSE or REBUILD without mutating pipeline state or calling a Provider."""
    refs = artifact_refs_from_row(row)
    source_ref = str(refs.get("signalRef") or "")
    current_ref = str(refs.get("agent1InputRef") or "")
    result: Dict[str, Any] = {
        "version": "23.1.3",
        "pipelineItemId": row.get("item_id"),
        "productId": row.get("product_id"),
        "storeId": row.get("store_id"),
        "signalRef": source_ref or None,
        "currentAgent1InputRef": current_ref or None,
        "expectedSchema": AGENT1_INPUT_SCHEMA,
        "expectedProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
        "providerCallsExecuted": 0,
        "databaseMutated": False,
    }
    if not source_ref.startswith("ART-"):
        result.update(
            decision="BLOCKED",
            reusable=False,
            validation={"ok": False, "errors": ["agent1_source_signal_ref_missing"]},
        )
        return result

    source_hash = _source_hash(source_ref)
    policy_hash = content_hash(_policy(policy_context or {}))
    errors: List[str] = []
    artifact_validation: Dict[str, Any] = {}
    contract_validation: Dict[str, Any] = {}

    if not current_ref.startswith("ART-"):
        errors.append("agent1_input_ref_missing")
    else:
        artifact_validation = validate_artifact(
            current_ref,
            expected_type=AGENT1_INPUT_SCHEMA,
        )
        if artifact_validation.get("ok") is not True:
            errors.append(
                str(artifact_validation.get("status") or "agent1_input_artifact_invalid")
            )
        else:
            try:
                envelope = resolve_artifact(current_ref)
                contract_validation = assert_agent_input_envelope(
                    envelope,
                    expected_schema=AGENT1_INPUT_SCHEMA,
                )
            except Exception as exc:
                errors.append(f"agent1_input_contract_invalid:{str(exc)[:500]}")

    reusable_ref = _existing_input(
        row,
        source_ref,
        source_hash,
        policy_hash,
    )
    if current_ref.startswith("ART-") and reusable_ref != current_ref and not errors:
        errors.append("agent1_input_source_policy_or_lineage_mismatch")
    reusable = bool(current_ref.startswith("ART-") and reusable_ref == current_ref)
    result.update(
        decision="REUSE" if reusable else "REBUILD",
        reusable=reusable,
        sourceContentHash=source_hash,
        policyContextHash=policy_hash,
        validation={
            "ok": reusable,
            "errors": errors,
            "artifact": artifact_validation,
            "contract": contract_validation,
        },
    )
    return result


def ensure_agent1_input_ref_with_receipt(
    row: Dict[str, Any],
    *,
    policy_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Apply one inspected input decision and return an auditable receipt."""
    plan = inspect_agent1_input_ref(row, policy_context=policy_context)
    if plan.get("decision") == "BLOCKED":
        raise AgentInputTransportV2258Error(
            "agent1_input_recovery_blocked",
            plan.get("validation"),
        )
    active_ref = ensure_agent1_input_ref(
        row,
        policy_context=policy_context,
    )
    return {
        **plan,
        "inputAction": plan.get("decision"),
        "activeAgent1InputRef": active_ref,
        "inputRefChanged": active_ref != plan.get("currentAgent1InputRef"),
        "databaseMutated": True,
        "providerCallsExecuted": 0,
    }


def _source_hash(artifact_id: str) -> str:
    metadata = inspect_artifact(artifact_id)
    return str(metadata.get("contentHash") or metadata.get("content_hash") or "")


def _existing_input(
    row: Dict[str, Any],
    source_ref: str,
    source_hash: str,
    policy_hash: str,
) -> str | None:
    artifact_id = str(artifact_refs_from_row(row).get("agent1InputRef") or "")
    if not artifact_id.startswith("ART-"):
        return None
    if validate_artifact(artifact_id, expected_type=AGENT1_INPUT_SCHEMA).get("ok") is not True:
        return None
    try:
        envelope = resolve_artifact(artifact_id)
        assert_agent_input_envelope(envelope, expected_schema=AGENT1_INPUT_SCHEMA)
    except Exception:
        return None
    payload = _dict(envelope.get("payload"))
    contract = _dict(payload.get("inputContract"))
    refs = envelope.get("sourceArtifactRefs")
    checks = (
        envelope.get("projectionVersion") == AGENT1_INPUT_PROJECTION_VERSION,
        isinstance(refs, list) and source_ref in refs,
        str(envelope.get("sourceContentHash") or "") == source_hash,
        contract.get("projectionVersion") == AGENT1_INPUT_PROJECTION_VERSION,
        str(contract.get("sourceRef") or "") == source_ref,
        str(contract.get("sourceContentHash") or "") == source_hash,
        str(contract.get("policyContextHash") or "") == policy_hash,
        str(contract.get("sourceLineageHash") or "")
        == content_hash(_dict(payload.get("sourceLineageValidation"))),
        contract.get("trendSemanticVersion") == AGENT1_INPUT_PROJECTION_VERSION,
    )
    return artifact_id if all(checks) else None


def ensure_agent1_input_ref(
    row: Dict[str, Any],
    *,
    policy_context: Dict[str, Any] | None = None,
) -> str:
    source_ref = str(artifact_refs_from_row(row).get("signalRef") or "")
    if not source_ref.startswith("ART-"):
        raise AgentInputTransportV2258Error(
            "agent1_source_signal_ref_missing",
            row.get("item_id"),
        )
    source_hash = _source_hash(source_ref)
    policy = _policy(policy_context or {})
    existing = _existing_input(
        row,
        source_ref,
        source_hash,
        content_hash(policy),
    )
    if existing:
        attach_pipeline_artifact_ref(
            str(row.get("item_id")),
            "agent1InputRef",
            existing,
            make_current=True,
        )
        return existing
    source = resolve_artifact(source_ref)
    if not isinstance(source, dict) or not source:
        raise AgentInputTransportV2258Error(
            "agent1_source_signal_invalid",
            source_ref,
        )
    envelope = compile_agent1_envelope(
        source,
        source_ref=source_ref,
        source_content_hash=source_hash,
        policy_context=policy_context,
    )
    artifact = store_artifact(
        artifact_type=AGENT1_INPUT_SCHEMA,
        value=envelope,
        schema_version=AGENT1_INPUT_PROJECTION_VERSION,
        tenant_id=row.get("tenant_id"),
        store_id=row.get("store_id"),
        product_id=row.get("product_id"),
        data_version=row.get("data_version"),
        created_by="agent_input_transport_v2258",
        parent_refs=[source_ref],
        metadata={
            "pipelineItemId": row.get("item_id"),
            "hardInterface": True,
            "semanticContinuity": True,
            "fallbackAllowed": False,
            "sourceArtifactRef": source_ref,
            "sourceContentHash": source_hash,
            "projectedContentHash": envelope.get("projectedContentHash"),
            "projectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
        },
    )
    artifact_id = str(artifact["artifactId"])
    attach_pipeline_artifact_ref(
        str(row.get("item_id")),
        "agent1InputRef",
        artifact_id,
        make_current=True,
    )
    return artifact_id


def resolve_agent_input_ref(
    artifact_id: str,
    *,
    expected_schema: str,
) -> Dict[str, Any]:
    if expected_schema == AGENT2_INPUT_SCHEMA:
        return legacy.resolve_agent_input_ref(
            artifact_id,
            expected_schema=expected_schema,
        )
    if validate_artifact(
        artifact_id,
        expected_type=AGENT1_INPUT_SCHEMA,
    ).get("ok") is not True:
        raise AgentInputTransportV2258Error(
            "agent_input_artifact_invalid",
            artifact_id,
        )
    value = resolve_artifact(artifact_id)
    assert_agent_input_envelope(
        value,
        expected_schema=AGENT1_INPUT_SCHEMA,
    )
    return value


ensure_agent2_input_ref = legacy.ensure_agent2_input_ref

__all__ = [
    "AGENT_INPUT_TRANSPORT_VERSION",
    "MAX_AGENT1_FIELD_SIGNALS",
    "AgentInputTransportV2258Error",
    "compile_agent1_envelope",
    "inspect_agent1_input_ref",
    "ensure_agent1_input_ref_with_receipt",
    "ensure_agent1_input_ref",
    "ensure_agent2_input_ref",
    "resolve_agent_input_ref",
]
