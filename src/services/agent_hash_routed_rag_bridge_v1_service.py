"""Hash-routed RAG bridge for the active Agent2/Agent3 provider boundary.

The legacy pipeline already transports compact RAG snapshots into Agent2 and Agent3.
This bridge does not replace those upstream producers.  It makes the application-owned
hash route explicit at the last deterministic boundary before provider messages are
compiled, preserves legacy document ids only as compatibility refs, and refuses any
provider-owned route widening.

No network or model call is performed here.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.services.hash_routed_rag_service import (
    bind_legacy_rag_document_ids,
    build_hash_route,
    filter_vector_candidates,
    graph_expansion_allowed,
    normalize_legacy_rag_document_ids,
    provider_scope_is_valid,
)

AGENT_HASH_ROUTED_RAG_BRIDGE_VERSION = "23.0.1"
AGENT2_RAG_STAGE = "agent2_action_draft"
AGENT3_RAG_STAGE = "agent3_sop"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split())[:limit]


def _family(package: Dict[str, Any]) -> str:
    package = _dict(package)
    draft = _dict(package.get("agent2ActionDraft"))
    matrix = _dict(package.get("matrixDispatch"))
    judgment = _dict(package.get("agent1OperatingJudgment"))
    lock = _dict(judgment.get("executionLock"))
    return _text(
        package.get("lockedActionFamily")
        or package.get("actionFamily")
        or draft.get("actionFamily")
        or matrix.get("selectedActionFamily")
        or lock.get("selectedActionFamily"),
        120,
    ).casefold()


def _route_tags(package: Dict[str, Any], *, stage: str) -> List[str]:
    family = _family(package)
    if not family:
        raise ValueError("agent_rag_route_action_family_missing")
    domain = "vertical_action" if stage == AGENT2_RAG_STAGE else "company_sop"
    return [
        f"stage:{stage}",
        f"rag_domain:{domain}",
        f"action_family:{family}",
    ]


def _legacy_ids(snapshot: Dict[str, Any]) -> List[str]:
    snapshot = _dict(snapshot)
    values: List[Any] = []
    for key in (
        "ragDocumentIds",
        "approvedCaseIds",
        "usedCaseIds",
        "ragUsedCaseIds",
    ):
        raw = snapshot.get(key)
        if isinstance(raw, list):
            values.extend(raw)
        elif raw not in (None, ""):
            values.append(raw)
    return normalize_legacy_rag_document_ids(values)


def _query(snapshot: Dict[str, Any], package: Dict[str, Any]) -> str:
    snapshot = _dict(snapshot)
    return _text(
        snapshot.get("query")
        or snapshot.get("agentInstruction")
        or snapshot.get("queryFingerprint")
        or _family(package),
        1800,
    )


def _relationship_required(snapshot: Dict[str, Any]) -> bool:
    snapshot = _dict(snapshot)
    return bool(
        snapshot.get("relationshipRequired") is True
        or snapshot.get("graphExpansionRequired") is True
    )


def build_agent_rag_route(
    package: Dict[str, Any],
    *,
    stage: str,
    snapshot: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if stage not in {AGENT2_RAG_STAGE, AGENT3_RAG_STAGE}:
        raise ValueError("agent_rag_route_stage_unsupported")
    snapshot = _dict(snapshot)
    route = build_hash_route(
        _route_tags(package, stage=stage),
        query=_query(snapshot, package),
        relationship_required=_relationship_required(snapshot),
    )
    route = bind_legacy_rag_document_ids(route, _legacy_ids(snapshot))
    provider_scope = {
        "routeHash": route.get("routeHash"),
        "routeTags": route.get("routeTags"),
        "routingAuthority": False,
    }
    if not provider_scope_is_valid(provider_scope, route):
        raise ValueError("agent_rag_provider_scope_invalid")
    return {
        **route,
        "bridgeVersion": AGENT_HASH_ROUTED_RAG_BRIDGE_VERSION,
        "stage": stage,
        "providerScope": provider_scope,
        "legacyIdsAreRouteAuthority": False,
        "providerMayWidenRoute": False,
    }


def routed_rag_context(
    package: Dict[str, Any],
    *,
    stage: str,
    snapshot: Dict[str, Any] | None,
) -> Dict[str, Any]:
    snapshot = _dict(snapshot)
    route = build_agent_rag_route(package, stage=stage, snapshot=snapshot)
    candidates = [item for item in _arr(snapshot.get("vectorCandidates")) if isinstance(item, dict)]
    filtered = filter_vector_candidates(candidates, route) if candidates else None
    context: Dict[str, Any] = {
        "bridgeVersion": AGENT_HASH_ROUTED_RAG_BRIDGE_VERSION,
        "routeMode": route.get("routeMode"),
        "routeAuthority": route.get("routeAuthority"),
        "routeHash": route.get("routeHash"),
        "routeTags": route.get("routeTags"),
        "inputHash": route.get("inputHash"),
        "providerScope": route.get("providerScope"),
        "ragDocumentIds": route.get("ragDocumentIds") or [],
        "legacyRagCompatibility": route.get("legacyRagCompatibility"),
        "vectorSearch": route.get("vectorSearch"),
        "graphExpansion": route.get("graphExpansion"),
        "crossTagVectorRetrievalAllowed": False,
        "crossTagRebindingAllowed": False,
        "globalFallbackAllowed": False,
        "failClosed": True,
    }
    if filtered is not None:
        context["vectorCandidateFilter"] = {
            "inputCount": filtered.get("inputCount"),
            "acceptedCount": filtered.get("acceptedCount"),
            "rejectedCount": filtered.get("rejectedCount"),
            "accepted": filtered.get("accepted"),
            "rejected": filtered.get("rejected"),
        }
        context["graphExpansionAllowedNow"] = graph_expansion_allowed(
            route,
            scoped_vector_stage_complete=True,
        )
    else:
        context["graphExpansionAllowedNow"] = False
    return context


def agent2_provider_rag_context(package: Dict[str, Any]) -> Dict[str, Any]:
    return routed_rag_context(
        package,
        stage=AGENT2_RAG_STAGE,
        snapshot=_dict(package).get("verticalActionRag"),
    )


def agent3_provider_rag_context(package: Dict[str, Any]) -> Dict[str, Any]:
    return routed_rag_context(
        package,
        stage=AGENT3_RAG_STAGE,
        snapshot=_dict(package).get("companySopRagSnapshot"),
    )


def install_agent_hash_routed_rag_bridge() -> Dict[str, Any]:
    """Install deterministic provider-boundary hooks once per process."""
    from src.services import agent2_action_draft_core_v225_service as agent2_core
    from src.services import agent3_system_constraint_base_v23214_service as agent3_base

    agent2_patched = bool(getattr(agent2_core, "_hash_routed_rag_bridge_installed", False))
    if not agent2_patched:
        original_compact = agent2_core._compact_package

        def _agent2_compact_with_hash_route(package: Dict[str, Any]) -> Dict[str, Any]:
            result = original_compact(package)
            action_context = dict(_dict(result.get("actionContext")))
            legacy_snapshot = _dict(action_context.get("verticalActionRag")) or _dict(
                _dict(package).get("verticalActionRag")
            )
            action_context["verticalActionRag"] = {
                **legacy_snapshot,
                "hashRoute": agent2_provider_rag_context(package),
            }
            result["actionContext"] = action_context
            return result

        agent2_core._compact_package = _agent2_compact_with_hash_route
        agent2_core._hash_routed_rag_bridge_installed = True
        agent2_core._hash_routed_rag_bridge_version = AGENT_HASH_ROUTED_RAG_BRIDGE_VERSION
        agent2_patched = True

    agent3_patched = bool(getattr(agent3_base, "_hash_routed_rag_bridge_installed", False))
    if not agent3_patched:
        original_company_context = agent3_base._safe_company_context

        def _agent3_company_context_with_hash_route(
            package: Dict[str, Any],
            policy: Dict[str, Any],
        ) -> Dict[str, Any]:
            result = original_company_context(package, policy)
            result["hashRoute"] = agent3_provider_rag_context(package)
            return result

        agent3_base._safe_company_context = _agent3_company_context_with_hash_route
        agent3_base._hash_routed_rag_bridge_installed = True
        agent3_base._hash_routed_rag_bridge_version = AGENT_HASH_ROUTED_RAG_BRIDGE_VERSION
        agent3_patched = True

    return {
        "version": AGENT_HASH_ROUTED_RAG_BRIDGE_VERSION,
        "agent2Installed": agent2_patched,
        "agent3Installed": agent3_patched,
        "providerCallsExecuted": 0,
        "globalFallbackAllowed": False,
        "crossTagRebindingAllowed": False,
    }


__all__ = [
    "AGENT_HASH_ROUTED_RAG_BRIDGE_VERSION",
    "AGENT2_RAG_STAGE",
    "AGENT3_RAG_STAGE",
    "build_agent_rag_route",
    "routed_rag_context",
    "agent2_provider_rag_context",
    "agent3_provider_rag_context",
    "install_agent_hash_routed_rag_bridge",
]
