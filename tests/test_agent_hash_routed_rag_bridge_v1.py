from src.services.agent_hash_routed_rag_bridge_v1_service import (
    AGENT2_RAG_STAGE,
    AGENT3_RAG_STAGE,
    agent2_provider_rag_context,
    agent3_provider_rag_context,
    build_agent_rag_route,
    install_agent_hash_routed_rag_bridge,
    routed_rag_context,
)


def _agent2_package():
    return {
        "packageId": "PKG-1",
        "lockedActionFamily": "title_image_test",
        "verticalActionRag": {
            "status": "ready",
            "queryFingerprint": "creative-title-image",
            "approvedCaseIds": ["CASE-1", "CASE-2", "CASE-1"],
            "agentInstruction": "use approved title and image test cases",
        },
    }


def _agent3_package():
    return {
        "packageId": "PKG-1",
        "lockedActionFamily": "title_image_test",
        "companySopRagSnapshot": {
            "approvedCaseIds": ["SOP-1", "SOP-2"],
            "companyExecutionPrinciples": ["single-variable test"],
        },
    }


def test_agent2_route_is_application_owned_and_legacy_ids_do_not_select_route():
    context = agent2_provider_rag_context(_agent2_package())
    assert context["routeMode"] == "tag_hash_first"
    assert context["routeAuthority"] == "application_hash_router"
    assert context["ragDocumentIds"] == ["CASE-1", "CASE-2"]
    assert context["legacyRagCompatibility"]["maySelectRoute"] is False
    assert context["legacyRagCompatibility"]["mayBypassRouteProof"] is False
    assert context["providerScope"]["routingAuthority"] is False
    assert context["globalFallbackAllowed"] is False
    assert context["crossTagRebindingAllowed"] is False


def test_agent2_and_agent3_use_distinct_route_domains():
    agent2 = agent2_provider_rag_context(_agent2_package())
    agent3 = agent3_provider_rag_context(_agent3_package())
    assert "rag_domain:vertical_action" in agent2["routeTags"]
    assert "rag_domain:company_sop" in agent3["routeTags"]
    assert agent2["routeHash"] != agent3["routeHash"]


def test_vector_candidates_are_filtered_by_exact_route_before_graph_expansion():
    package = _agent2_package()
    snapshot = dict(package["verticalActionRag"])
    route = build_agent_rag_route(package, stage=AGENT2_RAG_STAGE, snapshot=snapshot)
    snapshot["relationshipRequired"] = True
    # Relationship mode changes only graph permission, not route tags/hash.
    exact = {
        "documentId": "CASE-ROUTED",
        "routeHash": route["routeHash"],
        "routeTags": route["routeTags"],
    }
    cross_tag = {
        "documentId": "CASE-WRONG",
        "routeHash": "sha256:not-the-selected-route",
        "routeTags": ["action_family:roas_scale"],
    }
    snapshot["vectorCandidates"] = [exact, cross_tag]
    context = routed_rag_context(
        package,
        stage=AGENT2_RAG_STAGE,
        snapshot=snapshot,
    )
    filtered = context["vectorCandidateFilter"]
    assert filtered["inputCount"] == 2
    assert filtered["acceptedCount"] == 1
    assert filtered["rejectedCount"] == 1
    assert filtered["accepted"][0]["documentId"] == "CASE-ROUTED"
    assert filtered["rejected"][0]["candidate"]["documentId"] == "CASE-WRONG"
    assert context["graphExpansionAllowedNow"] is True


def test_graph_expansion_is_not_opened_without_scoped_vector_stage():
    package = _agent3_package()
    package["companySopRagSnapshot"]["relationshipRequired"] = True
    context = agent3_provider_rag_context(package)
    assert context["graphExpansion"]["allowed"] is True
    assert context["graphExpansionAllowedNow"] is False


def test_bridge_installation_is_idempotent_and_executes_no_provider_calls():
    first = install_agent_hash_routed_rag_bridge()
    second = install_agent_hash_routed_rag_bridge()
    assert first["agent2Installed"] is True
    assert first["agent3Installed"] is True
    assert second["agent2Installed"] is True
    assert second["agent3Installed"] is True
    assert first["providerCallsExecuted"] == 0
    assert second["providerCallsExecuted"] == 0


def test_active_runtime_installs_hash_routed_rag_bridge():
    from src.services import agent_token_runtime_v225_service as active_runtime

    status = active_runtime.AGENT_HASH_ROUTED_RAG_BRIDGE
    assert status["agent2Installed"] is True
    assert status["agent3Installed"] is True
    assert status["globalFallbackAllowed"] is False
