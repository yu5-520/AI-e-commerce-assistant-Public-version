"""V26.9.A deterministic BusinessFacts projection for the existing Agent1 seam.

No model call and no RAG feedback happens here. The existing immutable signal Artifact
is converted into the canonical Agent1 input contract plus a small typed fact ledger.
Candidate execution is explicit until the registered rollout status becomes active.
Historical V22 rows are still read by their historical projection while V26.9 rows have
one graph semantic interpretation and never fall back to old primary-action fields.

A later immutable signal Artifact may also be consumed as TARGET evidence for an
already-executed V26.9 task. That review happens before current Agent1 projection and
never treats a cache replay of an older Artifact as a new observation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import os
from typing import Any, Dict

from src.services import agent_input_transport_v2258_service as legacy_facts
from src.services import v269_input_migration_service as migration
from src.services import v269_semantic_graph_service as graphs
from src.services.agent_input_contract_v2258_service import (
    AGENT1_INPUT_PROJECTION_VERSION,
    AGENT1_INPUT_SCHEMA,
    assert_agent_input_envelope,
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

VERSION = "26.9.0"
CANDIDATE_ENV = "V269_CANDIDATE_GRAPH_RUNTIME"


def candidate_runtime_enabled() -> bool:
    status = str(graphs.contract().get("rolloutStatus") or "")
    if status == "active":
        return True
    return str(os.getenv(CANDIDATE_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def metric_unit(metric: str) -> str:
    name = str(metric or "").replace("_", "").replace("-", "").casefold()
    currency = (
        "spend", "amount", "price", "cost", "revenue", "profit", "budget",
        "cpc", "cpm", "bid", "gmv", "sales", "payment", "refundamount",
    )
    if any(token in name for token in currency):
        return "CNY"
    if any(token in name for token in ("roas", "roi")):
        return "ratio"
    if any(token in name for token in ("rate", "ctr", "cvr", "ratio", "margin")):
        return "ratio"
    if any(token in name for token in ("count", "orders", "visitors", "clicks", "inventory", "quantity")):
        return "count"
    return "raw"


def _fact_ledger(metric_layer: Dict[str, Any], signals: list[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    facts: Dict[str, Dict[str, Any]] = {}
    for metric, raw in sorted(metric_layer.items()):
        value = _number(raw)
        if value is None:
            continue
        ref = f"fact:metric:{metric}"
        facts[ref] = {"value": value, "unit": metric_unit(metric)}
    for item in signals:
        metric = str(item.get("metricCode") or item.get("metricName") or "").strip()
        if not metric:
            continue
        current = _number(item.get("current", item.get("latest")))
        previous = _number(item.get("previous"))
        if current is not None:
            facts[f"fact:signal:{metric}:current"] = {
                "value": current,
                "unit": metric_unit(metric),
            }
        if previous is not None:
            facts[f"fact:signal:{metric}:previous"] = {
                "value": previous,
                "unit": metric_unit(metric),
            }
    return facts


def empty_knowledge_context() -> Dict[str, Any]:
    """Explicitly represent that V26.9.A has no activated Experience Store yet."""
    retrieval = graphs.digest(
        {
            "schema": "v269.agent1_retrieval_policy.v1",
            "mode": "exact_field_only",
            "experienceStore": "NOT_ACTIVATED",
            "dynamicRetrievalPlanning": False,
            "vectorRetrieval": False,
        }
    )
    body = {"retrievalPolicyHash": retrieval, "records": []}
    return {"headHash": graphs.digest(body), **body}


def _artifact_observed_at_millis(artifact_id: str) -> int | None:
    try:
        metadata = inspect_artifact(artifact_id)
        raw = metadata.get("created_at") or metadata.get("createdAt")
        if not raw:
            return None
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except Exception:
        return None


def compile_candidate_source(
    source: Dict[str, Any],
    *,
    source_ref: str,
    source_content_hash: str,
) -> Dict[str, Any]:
    root = legacy_facts._payload(source)
    identity = legacy_facts._identity(source)
    product_id = legacy_facts._text(identity.get("productId"), 160)
    store_id = legacy_facts._text(identity.get("storeId"), 160)
    graphs.require(bool(product_id and store_id), "agent1_fact_identity_missing")
    signal_id = legacy_facts._text(
        source.get("signalId")
        or source.get("signal_id")
        or root.get("signalId")
        or root.get("signal_id"),
        180,
    )
    correlation_id = legacy_facts._text(
        source.get("correlationId")
        or source.get("pipelineItemId")
        or source.get("itemId")
        or f"{store_id}:{product_id}:{signal_id}",
        240,
    )
    signals = legacy_facts._signals(root)
    metric_layer = legacy_facts._metric_layer(root)
    lineage = legacy_facts._source_lineage(root, source_ref, source_content_hash)
    fact_values = _fact_ledger(metric_layer, signals)
    evidence_refs = list(fact_values)
    if source_ref not in evidence_refs:
        evidence_refs.append(source_ref)
    business_facts = {
        "productIdentity": deepcopy(identity),
        "fieldSignals": deepcopy(signals),
        "metricSnapshot": deepcopy(metric_layer),
        "trendContext": legacy_facts._trend_context(root),
        "sourceLineageValidation": lineage,
        "strongRelations": legacy_facts._compact(
            root.get("strongRelations") or root.get("relationFacts"),
            max_depth=6,
            max_list=16,
            max_keys=64,
        ),
        "crossValidation": legacy_facts._metric_cross_validation(root),
        "factLayerValidation": legacy_facts._compact(
            root.get("factLayerValidation"),
            max_depth=5,
            max_list=16,
            max_keys=48,
        ),
        "dataFingerprint": root.get("dataFingerprint") or source.get("dataFingerprint"),
        "factValues": fact_values,
        "sourceArtifactRef": source_ref,
        "sourceContentHash": source_content_hash,
        "sourceObservedAtMillis": _artifact_observed_at_millis(source_ref),
    }
    business_facts = {k: v for k, v in business_facts.items() if v not in (None, "", [], {})}
    package_id = str(
        source.get("packageId")
        or source.get("itemId")
        or correlation_id
    ).strip()
    return {
        "semanticContractVersion": VERSION,
        "packageId": package_id,
        "productId": product_id,
        "storeId": store_id,
        "dataVersion": source.get("dataVersion") or root.get("dataVersion"),
        "correlationId": correlation_id,
        "signalId": signal_id or None,
        "BusinessFacts": business_facts,
        "evidenceRefs": evidence_refs,
        "knowledgeContext": empty_knowledge_context(),
    }


def _source_hash(artifact_id: str) -> str:
    metadata = inspect_artifact(artifact_id)
    return str(metadata.get("contentHash") or metadata.get("content_hash") or "")


def _existing_candidate(
    row: Dict[str, Any],
    *,
    source_ref: str,
    source_hash: str,
) -> str | None:
    current = str(artifact_refs_from_row(row).get("agent1InputRef") or "")
    if not current.startswith("ART-"):
        return None
    if validate_artifact(current, expected_type=AGENT1_INPUT_SCHEMA).get("ok") is not True:
        return None
    try:
        value = resolve_artifact(current)
        assert_agent_input_envelope(value, expected_schema=AGENT1_INPUT_SCHEMA)
        payload = value.get("payload") if isinstance(value, dict) else None
        migration.validate_payload("agent1", payload)
    except Exception:
        return None
    refs = value.get("sourceArtifactRefs") or []
    if source_ref not in refs or str(value.get("sourceContentHash") or "") != source_hash:
        return None
    return current


def ensure_candidate_agent1_input_ref(
    row: Dict[str, Any],
    *,
    policy_context: Dict[str, Any] | None = None,
) -> str:
    del policy_context
    graphs.require(candidate_runtime_enabled(), "candidate_runtime_not_enabled")
    source_ref = str(artifact_refs_from_row(row).get("signalRef") or "")
    graphs.require(source_ref.startswith("ART-"), "agent1_source_signal_ref_missing")
    source_hash = _source_hash(source_ref)
    source = resolve_artifact(source_ref)
    graphs.require(isinstance(source, dict) and bool(source), "agent1_source_signal_invalid")
    canonical = compile_candidate_source(
        source,
        source_ref=source_ref,
        source_content_hash=source_hash,
    )

    from src.services.v269_system_review_service import observe_candidate_facts
    review = observe_candidate_facts(
        canonical,
        source_content_hash=source_hash,
        observed_at_millis=(canonical.get("BusinessFacts") or {}).get("sourceObservedAtMillis"),
    )
    revision = review.get("revisionDirective") if isinstance(review, dict) else None
    if review.get("status") == "ADJUSTMENT_REQUIRED" and isinstance(revision, dict):
        parent = review.get("parentDecisionGraph")
        graphs.require(isinstance(parent, dict), "review_revision_parent_missing")
        canonical["revisionScope"] = deepcopy(revision)
        canonical["parentGraph"] = deepcopy(parent)

    # A revision-bearing input is a new semantic input even when the underlying
    # BusinessFacts Artifact was already projected before execution completed.
    existing = None if "revisionScope" in canonical else _existing_candidate(
        row, source_ref=source_ref, source_hash=source_hash
    )
    if existing:
        attach_pipeline_artifact_ref(
            str(row.get("item_id")), "agent1InputRef", existing, make_current=True
        )
        return existing

    envelope = migration.project_input(
        "agent1",
        canonical,
        source_ref=source_ref,
        source_content_hash=source_hash,
    )
    artifact = store_artifact(
        artifact_type=AGENT1_INPUT_SCHEMA,
        value=envelope,
        schema_version=AGENT1_INPUT_PROJECTION_VERSION,
        tenant_id=row.get("tenant_id"),
        store_id=row.get("store_id"),
        product_id=row.get("product_id"),
        data_version=row.get("data_version"),
        created_by="v269_fact_projection_service",
        parent_refs=[source_ref],
        metadata={
            "pipelineItemId": row.get("item_id"),
            "semanticContractVersion": VERSION,
            "sourceArtifactRef": source_ref,
            "sourceContentHash": source_hash,
            "sourceObservedAtMillis": (canonical.get("BusinessFacts") or {}).get("sourceObservedAtMillis"),
            "projectedContentHash": envelope.get("projectedContentHash"),
            "systemReviewStatus": review.get("status") if isinstance(review, dict) else None,
            "systemReviewRevisionHash": (revision or {}).get("revisionHash") if isinstance(revision, dict) else None,
            "candidateRuntime": graphs.contract().get("rolloutStatus") != "active",
            "fallbackAllowed": False,
        },
    )
    artifact_id = str(artifact["artifactId"])
    attach_pipeline_artifact_ref(
        str(row.get("item_id")), "agent1InputRef", artifact_id, make_current=True
    )
    return artifact_id


def ensure_agent1_input_ref(
    row: Dict[str, Any],
    *,
    policy_context: Dict[str, Any] | None = None,
) -> str:
    """Registered versioned seam: current contract decides the one legal projection."""
    if candidate_runtime_enabled():
        return ensure_candidate_agent1_input_ref(row, policy_context=policy_context)
    return legacy_facts.ensure_agent1_input_ref(row, policy_context=policy_context)


resolve_agent_input_ref = legacy_facts.resolve_agent_input_ref


__all__ = [
    "VERSION",
    "CANDIDATE_ENV",
    "candidate_runtime_enabled",
    "metric_unit",
    "empty_knowledge_context",
    "compile_candidate_source",
    "ensure_candidate_agent1_input_ref",
    "ensure_agent1_input_ref",
    "resolve_agent_input_ref",
]
