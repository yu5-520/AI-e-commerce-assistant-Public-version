"""V22.3.0 hard semantic transport for Agent1 and Agent2.

Full business artifacts remain available to audit and operations. This service is
the sole producer of model-facing semantic DTOs and stores them as immutable
Artifact Hub objects referenced by ``agent1InputRef`` or ``agent2InputRef``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services.agent_input_contract_v230_service import (
    AGENT_INPUT_CONTRACT_VERSION,
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

AGENT_INPUT_TRANSPORT_VERSION = "22.3.0"
_AGENT1_STAGES = {"agent1_pending", "agent1_running"}
_AGENT2_STAGES = {"action_pack_ready", "agent2_running"}
_DROP_KEYS = {
    "raw",
    "payload",
    "events",
    "history",
    "provider",
    "providerTrace",
    "systemFacts",
    "signalEvidence",
    "metricEvidence",
    "pipelineItemEnvelope",
    "artifactRefs",
    "agent2Provider",
    "rawAgent1Judgment",
}


class AgentInputTransportError(RuntimeError):
    def __init__(self, code: str, detail: Any = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _compact(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 5,
    max_list: int = 12,
    max_keys: int = 36,
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value)
    if depth >= max_depth:
        if isinstance(value, dict):
            return {
                str(key): _compact(item, depth=depth + 1, max_depth=max_depth)
                for key, item in list(value.items())[:max_keys]
                if key not in _DROP_KEYS
                and isinstance(item, (str, int, float, bool))
            }
        if isinstance(value, list):
            return [
                _compact(item, depth=depth + 1, max_depth=max_depth)
                for item in value[:max_list]
                if isinstance(item, (str, int, float, bool))
            ]
        return None
    if isinstance(value, list):
        result: List[Any] = []
        for item in value[:max_list]:
            compact = _compact(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_list=max_list,
                max_keys=max_keys,
            )
            if compact not in (None, "", [], {}):
                result.append(compact)
        return result
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key in list(value)[:max_keys]:
            if key in _DROP_KEYS:
                continue
            compact = _compact(
                value.get(key),
                depth=depth + 1,
                max_depth=max_depth,
                max_list=max_list,
                max_keys=max_keys,
            )
            if compact not in (None, "", [], {}):
                result[str(key)] = compact
        return result
    return _text(value)


def _payload(source: Dict[str, Any]) -> Dict[str, Any]:
    nested = source.get("payload")
    return nested if isinstance(nested, dict) and nested else source


def _identity(source: Dict[str, Any]) -> Dict[str, Any]:
    root = _payload(source)
    merged = {
        **_dict(root.get("profileLayer")),
        **_dict(root.get("productIdentity")),
        **_dict(root.get("identity")),
        **root,
        **source,
    }
    result = {
        "productId": merged.get("productId")
        or merged.get("product_id")
        or merged.get("entityId"),
        "storeId": merged.get("storeId") or merged.get("store_id"),
        "productTitle": merged.get("productTitle")
        or merged.get("title")
        or merged.get("shortTitle"),
        "title": merged.get("productTitle")
        or merged.get("title")
        or merged.get("shortTitle"),
        "platform": merged.get("platform"),
        "verticalCategory": merged.get("verticalCategory"),
        "categoryId": merged.get("categoryId"),
        "productRole": merged.get("productRole"),
        "lifecycleStage": merged.get("lifecycleStage"),
        "skuId": merged.get("skuId"),
        "spuId": merged.get("spuId"),
        "erpProductCode": merged.get("erpProductCode"),
        "storeName": merged.get("storeName"),
    }
    return {
        key: _text(value, 240)
        for key, value in result.items()
        if value not in (None, "")
    }


def _field_signals(root: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = _dict(root.get("snapshotLayer"))
    candidates = snapshot.get("fieldSignals") or root.get("fieldSignals") or []
    result: List[Dict[str, Any]] = []
    for item in _arr(candidates)[:16]:
        if not isinstance(item, dict):
            continue
        clean = {
            "metricCode": item.get("metricCode")
            or item.get("code")
            or item.get("metricName"),
            "previous": item.get("previous", item.get("previousValue")),
            "current": item.get(
                "current",
                item.get("currentValue", item.get("latest")),
            ),
            "changeRatio": item.get(
                "changeRatio",
                item.get("changeRate", item.get("deltaRate")),
            ),
            "reason": _text(item.get("reason"), 180) or None,
        }
        result.append(
            {key: value for key, value in clean.items() if value not in (None, "")}
        )
    return result


def _metric_digest(root: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for layer_name in ("metricLayer", "snapshotLayer", "dynamicMetrics"):
        layer = _dict(root.get(layer_name))
        for key, value in layer.items():
            if (
                key in _DROP_KEYS
                or key == "fieldSignals"
                or value in (None, "", [], {})
            ):
                continue
            if isinstance(value, (str, int, float, bool)):
                result[str(key)] = _text(value, 240) if isinstance(value, str) else value
    for key in (
        "recentFiveTrendSummary",
        "historicalTrendSummary",
        "trendSummary",
        "recentFiveOrLatestFacts",
    ):
        value = root.get(key)
        if value not in (None, "", [], {}):
            result[key] = _compact(
                value,
                max_depth=4,
                max_list=10,
                max_keys=24,
            )
    return dict(list(result.items())[:48])


def _compact_policy(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version": value.get("version"),
        "mode": value.get("mode"),
        "principles": [
            _text(item, 260)
            for item in _arr(value.get("principles"))[:8]
            if _text(item)
        ],
        "guardrails": _compact(
            value.get("guardrails"),
            max_depth=3,
            max_list=8,
            max_keys=16,
        ),
        "approvedCaseIds": [
            str(item) for item in _arr(value.get("approvedCaseIds"))[:12]
        ],
        "queryFingerprint": value.get("queryFingerprint"),
    }


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
        raise AgentInputTransportError(
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
    compact_policy = _compact_policy(policy_context or {})
    policy_hash = content_hash(compact_policy)
    profile = {
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
    }
    payload = {
        "productId": product_id,
        "storeId": store_id,
        "signalId": signal_id or None,
        "correlationId": correlation_id,
        "dataVersion": source.get("dataVersion") or root.get("dataVersion"),
        "productIdentity": identity,
        "profileLayer": profile,
        "snapshotLayer": {"fieldSignals": _field_signals(root)},
        "metricLayer": _metric_digest(root),
        "strongRelations": _compact(
            root.get("strongRelations") or root.get("relationFacts"),
            max_depth=4,
            max_list=8,
            max_keys=20,
        ),
        "crossValidation": _compact(
            root.get("crossValidation"),
            max_depth=4,
            max_list=10,
            max_keys=24,
        ),
        "factLayerValidation": _compact(
            root.get("factLayerValidation"),
            max_depth=3,
            max_list=8,
            max_keys=16,
        ),
        "dataFingerprint": root.get("dataFingerprint")
        or source.get("dataFingerprint"),
        "diagnosticRag": compact_policy,
        "inputContract": {
            "schema": AGENT1_INPUT_SCHEMA,
            "version": AGENT_INPUT_CONTRACT_VERSION,
            "sourceRef": source_ref,
            "sourceContentHash": source_content_hash,
            "policyContextHash": policy_hash,
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


def _compact_rag_snapshot(value: Dict[str, Any]) -> Dict[str, Any]:
    cards: List[Dict[str, Any]] = []
    for item in _arr(value.get("positiveExperienceCards"))[:6]:
        if not isinstance(item, dict):
            continue
        cards.append(
            {
                "caseId": item.get("caseId") or item.get("id"),
                "experiencePrinciples": [
                    _text(entry, 240)
                    for entry in _arr(
                        item.get("experiencePrinciples") or item.get("principles")
                    )[:4]
                ],
                "applicableConditions": [
                    _text(entry, 200)
                    for entry in _arr(item.get("applicableConditions"))[:4]
                ],
            }
        )
    return {
        "version": value.get("version"),
        "status": value.get("status"),
        "queryFingerprint": value.get("queryFingerprint"),
        "matchedCount": int(value.get("matchedCount") or 0),
        "approvedCaseIds": [
            str(item) for item in _arr(value.get("approvedCaseIds"))[:12]
        ],
        "positiveExperienceCards": cards,
        "negativeCases": _compact(
            value.get("negativeCases"),
            max_depth=4,
            max_list=6,
            max_keys=12,
        ),
        "agentInstruction": _text(value.get("agentInstruction"), 600) or None,
    }


def compile_agent2_envelope(
    source: Dict[str, Any],
    *,
    source_ref: str,
    source_content_hash: str,
) -> Dict[str, Any]:
    identity = _identity(source)
    product_id = _text(source.get("productId") or identity.get("productId"), 160)
    store_id = _text(source.get("storeId") or identity.get("storeId"), 160)
    package_id = _text(source.get("packageId") or source.get("itemId"), 220)
    judgment = _dict(source.get("agent1OperatingJudgment"))
    decision_ir = _dict(source.get("agent1DecisionIR")) or _dict(
        judgment.get("agent1DecisionIR")
    )
    matrix = _dict(source.get("matrixDispatch"))
    family_lock = _dict(judgment.get("actionFamilyLock"))
    family = _text(
        source.get("lockedActionFamily")
        or source.get("actionFamily")
        or matrix.get("selectedActionFamily")
        or family_lock.get("selectedActionFamily"),
        100,
    )
    if not product_id or not store_id or not package_id or not family:
        raise AgentInputTransportError(
            "agent2_identity_or_family_missing",
            {
                "productId": product_id,
                "storeId": store_id,
                "packageId": package_id,
                "actionFamily": family,
            },
        )
    pack = _compact(
        source.get("actionParameterPack"),
        max_depth=6,
        max_list=16,
        max_keys=48,
    )
    payload = {
        "packageId": package_id,
        "itemId": source.get("itemId"),
        "dataVersion": source.get("dataVersion"),
        "productId": product_id,
        "storeId": store_id,
        "productTitle": identity.get("productTitle") or identity.get("title"),
        "title": identity.get("productTitle") or identity.get("title"),
        "productIdentity": identity,
        "decisionType": source.get("decisionType")
        or decision_ir.get("decisionType")
        or judgment.get("decisionType"),
        "agent1DecisionIR": _compact(
            decision_ir,
            max_depth=6,
            max_list=14,
            max_keys=36,
        ),
        "agent1OperatingJudgment": _compact(
            judgment,
            max_depth=6,
            max_list=14,
            max_keys=36,
        ),
        "matrixDispatch": _compact(
            matrix,
            max_depth=4,
            max_list=8,
            max_keys=20,
        ),
        "actionFamily": family,
        "selectedActionFamily": family,
        "lockedActionFamily": family,
        "actionParameterPack": pack,
        "recentFiveOrLatestFacts": _compact(
            source.get("recentFiveOrLatestFacts"),
            max_depth=4,
            max_list=12,
            max_keys=18,
        ),
        "ragContextSnapshot": _compact_rag_snapshot(
            _dict(source.get("ragContextSnapshot"))
        ),
        "diagnosticExtensionContract": _compact(
            source.get("diagnosticExtensionContract"),
            max_depth=4,
            max_list=8,
            max_keys=18,
        ),
        "diagnosticExtensions": _compact(
            source.get("diagnosticExtensions")
            or decision_ir.get("diagnosticExtensions"),
            max_depth=5,
            max_list=8,
            max_keys=18,
        ),
        "inputContract": {
            "schema": AGENT2_INPUT_SCHEMA,
            "version": AGENT_INPUT_CONTRACT_VERSION,
            "sourceRef": source_ref,
            "sourceContentHash": source_content_hash,
            "fallbackAllowed": False,
            "fullCapabilityReadAllowed": False,
        },
    }
    payload = {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {})
    }
    return build_projection_envelope(
        schema=AGENT2_INPUT_SCHEMA,
        payload=payload,
        source_artifact_refs=[source_ref],
        source_content_hash=source_content_hash,
    )


def _source_hash(artifact_id: str) -> str:
    metadata = inspect_artifact(artifact_id)
    return str(metadata.get("contentHash") or metadata.get("content_hash") or "")


def _store_input_artifact(
    envelope: Dict[str, Any],
    *,
    artifact_type: str,
    row: Dict[str, Any],
    source_ref: str,
) -> str:
    artifact = store_artifact(
        artifact_type=artifact_type,
        value=envelope,
        schema_version=AGENT_INPUT_CONTRACT_VERSION,
        tenant_id=row.get("tenant_id"),
        store_id=row.get("store_id"),
        product_id=row.get("product_id"),
        data_version=row.get("data_version"),
        created_by="agent_input_transport_v230",
        parent_refs=[source_ref],
        metadata={
            "pipelineItemId": row.get("item_id"),
            "hardInterface": True,
            "fallbackAllowed": False,
            "sourceArtifactRef": source_ref,
            "sourceContentHash": envelope.get("sourceContentHash"),
            "projectedContentHash": envelope.get("projectedContentHash"),
            "projectedChars": (envelope.get("projectionAudit") or {}).get(
                "projectedChars"
            ),
            "estimatedTokens": (envelope.get("projectionAudit") or {}).get(
                "estimatedTokens"
            ),
        },
    )
    return str(artifact["artifactId"])


def _existing_input_ref(
    row: Dict[str, Any],
    key: str,
    schema: str,
    *,
    source_ref: str,
    source_hash: str,
    policy_hash: str | None = None,
) -> str | None:
    refs = artifact_refs_from_row(row)
    artifact_id = str(refs.get(key) or "")
    validation = (
        validate_artifact(artifact_id, expected_type=schema)
        if artifact_id.startswith("ART-")
        else {"ok": False}
    )
    if validation.get("ok") is not True:
        return None
    try:
        envelope = resolve_artifact(artifact_id)
        assert_agent_input_envelope(envelope, expected_schema=schema)
    except Exception:
        return None
    source_refs = envelope.get("sourceArtifactRefs")
    payload = _dict(envelope.get("payload"))
    input_contract = _dict(payload.get("inputContract"))
    if not isinstance(source_refs, list) or source_ref not in source_refs:
        return None
    if str(envelope.get("sourceContentHash") or "") != source_hash:
        return None
    if str(input_contract.get("sourceRef") or "") != source_ref:
        return None
    if str(input_contract.get("sourceContentHash") or "") != source_hash:
        return None
    if policy_hash is not None and str(input_contract.get("policyContextHash") or "") != policy_hash:
        return None
    return artifact_id


def ensure_agent1_input_ref(
    row: Dict[str, Any],
    *,
    policy_context: Dict[str, Any] | None = None,
) -> str:
    refs = artifact_refs_from_row(row)
    source_ref = str(refs.get("signalRef") or "")
    if not source_ref.startswith("ART-"):
        raise AgentInputTransportError(
            "agent1_source_signal_ref_missing",
            row.get("item_id"),
        )
    source_hash = _source_hash(source_ref)
    compact_policy = _compact_policy(policy_context or {})
    policy_hash = content_hash(compact_policy)
    existing = _existing_input_ref(
        row,
        "agent1InputRef",
        AGENT1_INPUT_SCHEMA,
        source_ref=source_ref,
        source_hash=source_hash,
        policy_hash=policy_hash,
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
        raise AgentInputTransportError("agent1_source_signal_invalid", source_ref)
    envelope = compile_agent1_envelope(
        source,
        source_ref=source_ref,
        source_content_hash=source_hash,
        policy_context=policy_context,
    )
    artifact_id = _store_input_artifact(
        envelope,
        artifact_type=AGENT1_INPUT_SCHEMA,
        row=row,
        source_ref=source_ref,
    )
    attach_pipeline_artifact_ref(
        str(row.get("item_id")),
        "agent1InputRef",
        artifact_id,
        make_current=True,
    )
    return artifact_id


def ensure_agent2_input_ref(row: Dict[str, Any]) -> str:
    refs = artifact_refs_from_row(row)
    source_ref = str(refs.get("capabilityRef") or "")
    if not source_ref.startswith("ART-"):
        raise AgentInputTransportError(
            "agent2_source_capability_ref_missing",
            row.get("item_id"),
        )
    source_hash = _source_hash(source_ref)
    existing = _existing_input_ref(
        row,
        "agent2InputRef",
        AGENT2_INPUT_SCHEMA,
        source_ref=source_ref,
        source_hash=source_hash,
    )
    if existing:
        attach_pipeline_artifact_ref(
            str(row.get("item_id")),
            "agent2InputRef",
            existing,
            make_current=True,
        )
        return existing
    source = resolve_artifact(source_ref)
    if not isinstance(source, dict) or not source:
        raise AgentInputTransportError("agent2_source_capability_invalid", source_ref)
    envelope = compile_agent2_envelope(
        source,
        source_ref=source_ref,
        source_content_hash=source_hash,
    )
    artifact_id = _store_input_artifact(
        envelope,
        artifact_type=AGENT2_INPUT_SCHEMA,
        row=row,
        source_ref=source_ref,
    )
    attach_pipeline_artifact_ref(
        str(row.get("item_id")),
        "agent2InputRef",
        artifact_id,
        make_current=True,
    )
    return artifact_id


def resolve_agent_input_ref(
    artifact_id: str,
    *,
    expected_schema: str,
) -> Dict[str, Any]:
    if not str(artifact_id).startswith("ART-"):
        raise AgentInputTransportError("agent_input_ref_invalid", artifact_id)
    if validate_artifact(
        artifact_id,
        expected_type=expected_schema,
    ).get("ok") is not True:
        raise AgentInputTransportError("agent_input_artifact_invalid", artifact_id)
    value = resolve_artifact(artifact_id)
    assert_agent_input_envelope(value, expected_schema=expected_schema)
    return value


def migrate_pending_agent_inputs(
    data_version: str | None = None,
    *,
    limit: int = 10_000,
) -> Dict[str, Any]:
    from src.services.operating_policy_context_v2028_service import (
        build_operating_policy_context,
    )

    where = [
        "current_stage IN "
        "('agent1_pending','agent1_running','action_pack_ready','agent2_running')"
    ]
    params: List[Any] = []
    if data_version is not None:
        where.append("COALESCE(data_version,'')=COALESCE(?,'')")
        params.append(data_version)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_items WHERE {' AND '.join(where)} "
            "ORDER BY updated_at ASC LIMIT ?",
            (*params, max(1, min(100_000, int(limit)))),
        ).fetchall()
    policy = build_operating_policy_context()
    counts = {
        "agent1Compiled": 0,
        "agent2Compiled": 0,
        "failed": 0,
    }
    failures: List[Dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        try:
            if str(row.get("current_stage")) in _AGENT1_STAGES:
                ensure_agent1_input_ref(row, policy_context=policy)
                counts["agent1Compiled"] += 1
            elif str(row.get("current_stage")) in _AGENT2_STAGES:
                ensure_agent2_input_ref(row)
                counts["agent2Compiled"] += 1
        except Exception as exc:
            counts["failed"] += 1
            failures.append(
                {
                    "itemId": row.get("item_id"),
                    "stage": row.get("current_stage"),
                    "error": str(exc)[:400],
                }
            )
    return {
        "version": AGENT_INPUT_TRANSPORT_VERSION,
        "dataVersion": data_version,
        "candidateCount": len(rows),
        **counts,
        "failures": failures[:50],
        "fallbackAllowed": False,
        "completedAt": datetime.now().isoformat(),
    }


__all__ = [
    "AGENT_INPUT_TRANSPORT_VERSION",
    "AgentInputTransportError",
    "compile_agent1_envelope",
    "compile_agent2_envelope",
    "ensure_agent1_input_ref",
    "ensure_agent2_input_ref",
    "resolve_agent_input_ref",
    "migrate_pending_agent_inputs",
]
