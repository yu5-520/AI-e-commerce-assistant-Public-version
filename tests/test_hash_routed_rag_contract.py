import pytest

from src.services.hash_routed_rag_service import (
    bind_legacy_rag_document_ids,
    build_hash_route,
    candidate_matches_route,
    compute_route_hash,
    filter_vector_candidates,
    graph_expansion_allowed,
    provider_scope_is_valid,
)


def test_route_hash_is_stable_across_case_space_and_order():
    left = compute_route_hash(["  Paid   Traffic ", "PRODUCT:Shoes"])
    right = compute_route_hash(["product:shoes", "paid traffic"])
    assert left == right


def test_different_route_tags_produce_different_hashes():
    assert compute_route_hash(["product:shoes"]) != compute_route_hash(["product:bags"])


def test_route_tags_are_required_and_fail_closed():
    with pytest.raises(ValueError, match="route_tags_required"):
        build_hash_route([])


def test_vector_filter_rejects_cross_tag_candidate():
    route = build_hash_route(["product:shoes"], query="why did ROAS fall")
    allowed = {
        "documentId": "DOC-1",
        "routeHash": route["routeHash"],
        "routeTags": route["routeTags"],
    }
    rejected = {
        "documentId": "DOC-2",
        "routeTags": ["product:bags"],
    }

    result = filter_vector_candidates([allowed, rejected], route)

    assert result["accepted"] == [allowed]
    assert result["acceptedCount"] == 1
    assert result["rejectedCount"] == 1
    assert result["rejected"][0]["reason"] in {
        "candidate_route_tags_mismatch",
        "candidate_derived_hash_mismatch",
    }
    assert result["globalFallbackAllowed"] is False


def test_candidate_without_route_proof_is_rejected():
    route = build_hash_route(["product:shoes"])
    assert candidate_matches_route({"documentId": "DOC-NO-PROOF"}, route) is False


def test_graph_expansion_is_conditional_and_after_scoped_vector_stage():
    disabled = build_hash_route(["product:shoes"], relationship_required=False)
    enabled = build_hash_route(["product:shoes"], relationship_required=True)

    assert graph_expansion_allowed(
        disabled,
        scoped_vector_stage_complete=True,
    ) is False
    assert graph_expansion_allowed(
        enabled,
        scoped_vector_stage_complete=False,
    ) is False
    assert graph_expansion_allowed(
        enabled,
        scoped_vector_stage_complete=True,
    ) is True


def test_input_hash_is_deterministic_for_same_route_and_query():
    left = build_hash_route(["product:shoes", "metric:roas"], query=" ROAS   decline ")
    right = build_hash_route(["metric:roas", "PRODUCT:SHOES"], query="ROAS decline")

    assert left["routeHash"] == right["routeHash"]
    assert left["inputHash"] == right["inputHash"]


def test_legacy_rag_document_ids_are_preserved_without_route_authority():
    route = build_hash_route(["product:shoes"])
    bound = bind_legacy_rag_document_ids(
        route,
        [" DOC-1 ", "DOC-1", "DOC-2", ""],
    )

    assert bound["ragDocumentIds"] == ["DOC-1", "DOC-2"]
    assert bound["routeHash"] == route["routeHash"]
    assert bound["legacyRagCompatibility"]["maySelectRoute"] is False
    assert bound["legacyRagCompatibility"]["mayBypassRouteProof"] is False
    assert bound["legacyRagCompatibility"]["mayRebindAcrossTags"] is False


def test_provider_may_only_echo_exact_application_route():
    route = build_hash_route(["product:shoes", "metric:roas"])
    exact = {
        "routingAuthority": False,
        "routeHash": route["routeHash"],
        "routeTags": route["routeTags"],
    }
    provider_selected = {
        **exact,
        "routingAuthority": True,
    }
    widened = {
        **exact,
        "routeTags": ["product:shoes", "metric:roas", "global"],
    }

    assert provider_scope_is_valid(exact, route) is True
    assert provider_scope_is_valid(provider_selected, route) is False
    assert provider_scope_is_valid(widened, route) is False
