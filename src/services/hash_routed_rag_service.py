"""Deterministic hash-routed RAG scope contract.

The application owns route selection. Retrieval providers may search only inside the
already-selected route scope; they cannot widen, replace or infer that scope. Graph
expansion is optional and can run only after the exact-route vector stage completes.

This module performs no network or model calls. It is the deterministic routing and
proof layer that provider adapters must consume.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Sequence

HASH_ROUTED_RAG_VERSION = "23.0.0"
HASH_ROUTE_SCHEMA = "rag.hash_route.v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash_payload(value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def normalize_route_tags(tags: str | Iterable[Any] | None) -> List[str]:
    """Return a stable, deduplicated route-tag set.

    Tag identity is case-insensitive and whitespace-insensitive. A string is one tag;
    callers must pass a collection for multiple tags so commas inside business labels
    remain legal.
    """
    if tags is None:
        values: Iterable[Any] = []
    elif isinstance(tags, str):
        values = [tags]
    else:
        values = tags

    normalized = {
        _clean_text(value).casefold()
        for value in values
        if _clean_text(value)
    }
    return sorted(normalized)


def normalize_query(query: Any) -> str:
    return _clean_text(query)


def compute_route_hash(tags: str | Iterable[Any] | None) -> str:
    normalized = normalize_route_tags(tags)
    if not normalized:
        raise ValueError("route_tags_required")
    return _hash_payload(
        {
            "schema": HASH_ROUTE_SCHEMA,
            "routeTags": normalized,
        }
    )


def build_hash_route(
    tags: str | Iterable[Any] | None,
    *,
    query: Any = None,
    relationship_required: bool = False,
) -> Dict[str, Any]:
    """Build the immutable application-owned retrieval scope for one request."""
    normalized = normalize_route_tags(tags)
    if not normalized:
        raise ValueError("route_tags_required")

    route_hash = compute_route_hash(normalized)
    normalized_query = normalize_query(query)
    input_hash = _hash_payload(
        {
            "schema": HASH_ROUTE_SCHEMA,
            "routeHash": route_hash,
            "routeTags": normalized,
            "query": normalized_query,
        }
    )
    return {
        "schema": HASH_ROUTE_SCHEMA,
        "version": HASH_ROUTED_RAG_VERSION,
        "routeMode": "tag_hash_first",
        "routeAuthority": "application_hash_router",
        "routeTags": normalized,
        "routeTag": normalized[0] if len(normalized) == 1 else None,
        "routeHash": route_hash,
        "inputHash": input_hash,
        "query": normalized_query,
        "retrievalScope": {
            "mode": "exact_tag_hash_scope",
            "routeTags": normalized,
            "routeHash": route_hash,
        },
        "vectorSearch": {
            "allowed": True,
            "scope": "exact_route_only",
        },
        "graphExpansion": {
            "allowed": bool(relationship_required),
            "mode": "conditional_only",
            "requiresScopedVectorStage": True,
        },
        "providerRoutingAuthority": False,
        "crossTagVectorRetrievalAllowed": False,
        "crossTagRebindingAllowed": False,
        "globalFallbackAllowed": False,
        "failClosed": True,
    }


def _candidate_tags(candidate: Dict[str, Any]) -> List[str]:
    raw = candidate.get("routeTags")
    if raw is None and candidate.get("routeTag") is not None:
        raw = [candidate.get("routeTag")]
    return normalize_route_tags(raw)


def candidate_route_proof(
    candidate: Dict[str, Any],
    route: Dict[str, Any],
) -> Dict[str, Any]:
    """Verify that a retrieval result belongs to the exact selected route."""
    expected_hash = str(route.get("routeHash") or "")
    expected_tags = normalize_route_tags(route.get("routeTags"))
    candidate_hash = str(candidate.get("routeHash") or "")
    candidate_tags = _candidate_tags(candidate)

    if not expected_hash or not expected_tags:
        return {"accepted": False, "reason": "route_contract_incomplete"}
    if not candidate_hash and not candidate_tags:
        return {"accepted": False, "reason": "candidate_route_proof_missing"}
    if candidate_hash and candidate_hash != expected_hash:
        return {"accepted": False, "reason": "candidate_route_hash_mismatch"}
    if candidate_tags and candidate_tags != expected_tags:
        return {"accepted": False, "reason": "candidate_route_tags_mismatch"}
    if not candidate_hash:
        derived = compute_route_hash(candidate_tags)
        if derived != expected_hash:
            return {"accepted": False, "reason": "candidate_derived_hash_mismatch"}

    return {
        "accepted": True,
        "reason": "exact_route_match",
        "routeHash": expected_hash,
        "routeTags": expected_tags,
    }


def candidate_matches_route(
    candidate: Dict[str, Any],
    route: Dict[str, Any],
) -> bool:
    return candidate_route_proof(candidate, route).get("accepted") is True


def filter_vector_candidates(
    candidates: Sequence[Dict[str, Any]] | None,
    route: Dict[str, Any],
) -> Dict[str, Any]:
    """Fail closed: return only candidates carrying exact-route proof."""
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for candidate in candidates or []:
        proof = candidate_route_proof(candidate, route)
        if proof.get("accepted"):
            accepted.append(candidate)
        else:
            rejected.append(
                {
                    "candidate": candidate,
                    "reason": proof.get("reason"),
                }
            )
    return {
        "schema": "rag.hash_route.vector_filter.v1",
        "version": HASH_ROUTED_RAG_VERSION,
        "routeHash": route.get("routeHash"),
        "inputCount": len(candidates or []),
        "acceptedCount": len(accepted),
        "rejectedCount": len(rejected),
        "accepted": accepted,
        "rejected": rejected,
        "crossTagRetrievalAllowed": False,
        "globalFallbackAllowed": False,
    }


def graph_expansion_allowed(
    route: Dict[str, Any],
    *,
    scoped_vector_stage_complete: bool,
) -> bool:
    graph = route.get("graphExpansion")
    if not isinstance(graph, dict):
        return False
    if graph.get("allowed") is not True:
        return False
    if graph.get("requiresScopedVectorStage") is True and not scoped_vector_stage_complete:
        return False
    return route.get("globalFallbackAllowed") is False


def normalize_legacy_rag_document_ids(values: Any) -> List[str]:
    """Preserve real legacy refs without inventing or using them as route authority."""
    if values is None:
        return []
    if isinstance(values, str):
        source: Iterable[Any] = [values]
    elif isinstance(values, Iterable):
        source = values
    else:
        source = [values]

    result: List[str] = []
    seen = set()
    for value in source:
        document_id = _clean_text(value)
        if not document_id or document_id in seen:
            continue
        seen.add(document_id)
        result.append(document_id)
    return result


def bind_legacy_rag_document_ids(
    route: Dict[str, Any],
    rag_document_ids: Any,
) -> Dict[str, Any]:
    """Attach compatibility refs after routing; refs cannot modify the route."""
    return {
        **route,
        "ragDocumentIds": normalize_legacy_rag_document_ids(rag_document_ids),
        "legacyRagCompatibility": {
            "enabled": True,
            "maySelectRoute": False,
            "mayBypassRouteProof": False,
            "mayRebindAcrossTags": False,
        },
    }


def provider_scope_is_valid(
    provider_request: Dict[str, Any],
    route: Dict[str, Any],
) -> bool:
    """Providers may echo the exact scope, never choose or widen it."""
    if provider_request.get("routingAuthority") is True:
        return False
    requested_hash = str(provider_request.get("routeHash") or "")
    requested_tags = normalize_route_tags(provider_request.get("routeTags"))
    expected_hash = str(route.get("routeHash") or "")
    expected_tags = normalize_route_tags(route.get("routeTags"))
    if requested_hash != expected_hash:
        return False
    if requested_tags != expected_tags:
        return False
    return bool(expected_hash and expected_tags)


__all__ = [
    "HASH_ROUTED_RAG_VERSION",
    "HASH_ROUTE_SCHEMA",
    "normalize_route_tags",
    "normalize_query",
    "compute_route_hash",
    "build_hash_route",
    "candidate_route_proof",
    "candidate_matches_route",
    "filter_vector_candidates",
    "graph_expansion_allowed",
    "normalize_legacy_rag_document_ids",
    "bind_legacy_rag_document_ids",
    "provider_scope_is_valid",
]
