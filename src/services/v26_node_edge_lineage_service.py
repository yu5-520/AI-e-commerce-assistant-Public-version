"""V26.5 addressable Node/Edge lineage for the active V26 business graphs.

V26.2 established authority-bearing graph envelopes while deliberately preserving the
proven V22/V23 execution/hash runtime. V26.5 makes the business graph itself
addressable without changing that execution authority:

- Agent1 may expose multiple Judgement nodes.
- Agent2 may expose multiple Action nodes linked to Judgement nodes.
- Agent3 Operation stages link to Action nodes.
- nodeHash / edgeHash are always system compiled; the model only emits local keys.
- historical single-primary fields remain compatibility projections.
- Agent2 transport and semantic-cache identity include the complete JudgementGraph.

The module is installed after ``install_v26_business_graph_bridge``. It wraps the V26
semantic seams only; it does not replace ExecutionHash, Artifact refs, SystemStage, or
the Java authority call graph.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping

from src.services import v26_business_graph_bridge_service as bridge

V26_NODE_EDGE_LINEAGE_VERSION = "26.5.0"
JUDGEMENT_NODE_SCHEMA = "v26.judgement_node.v1"
ACTION_NODE_SCHEMA = "v26.action_node.v1"
OPERATION_NODE_SCHEMA = "v26.operation_node.v1"
GRAPH_EDGE_SCHEMA = "v26.business_graph_edge.v1"

_V262_COMPILE_JUDGEMENT = bridge.compile_judgement_graph
_V262_COMPILE_ACTION = bridge.compile_action_graph
_V262_COMPILE_OPERATION = bridge.compile_operation_graph
_INSTALLED = False

_ALLOWED_JUDGEMENT_RELATIONS = {
    "supports",
    "causes",
    "conflicts_with",
    "constrains",
    "enables",
    "related_to",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 12000) -> str:
    return bridge._text(value, limit)


def _clean_key(value: Any, fallback: str) -> str:
    text = _text(value, 160).strip()
    if not text:
        text = fallback
    return text.replace(" ", "_")[:160]


def _float01(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return None


def _unique_texts(value: Any, *, limit: int = 128) -> List[str]:
    result: List[str] = []
    for item in _arr(value):
        text = _text(item, 240)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _source_evidence(normalized: Mapping[str, Any], raw: Mapping[str, Any]) -> List[str]:
    refs: List[str] = []
    for value in _dict(normalized.get("artifactRefs")).values():
        if isinstance(value, str) and value:
            refs.append(value)
    decision = _dict(
        normalized.get("agent1DecisionIR")
        or _dict(normalized.get("agent1OperatingJudgment")).get("agent1DecisionIR")
    )
    rag_proof = _dict(raw.get("ragProof") or decision.get("ragProof"))
    for value in rag_proof.values():
        if isinstance(value, str) and value:
            refs.append(value)
    return list(dict.fromkeys(refs))


def _node(
    *,
    schema: str,
    actor: str,
    node_key: str,
    headers: Mapping[str, Any],
    index: int,
    compatibility_projection: bool,
) -> Dict[str, Any]:
    clean = bridge._clean_headers(headers)
    bridge._authority().assert_payload_write(actor, clean)
    body = {
        "schema": schema,
        "version": V26_NODE_EDGE_LINEAGE_VERSION,
        "actor": actor,
        "nodeKey": node_key,
        "index": index,
        "authorityHeaders": clean,
        "compatibilityProjection": bool(compatibility_projection),
    }
    return {**body, "nodeHash": bridge._canonical_hash(body)}


def _edge(
    *,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    relation: str,
    cross_graph: bool = False,
) -> Dict[str, Any]:
    relation_text = _text(relation, 120)
    if not relation_text:
        raise ValueError("v26_graph_edge_relation_required")
    body = {
        "schema": GRAPH_EDGE_SCHEMA,
        "version": V26_NODE_EDGE_LINEAGE_VERSION,
        "sourceKey": source.get("nodeKey"),
        "sourceNodeHash": source.get("nodeHash"),
        "targetKey": target.get("nodeKey"),
        "targetNodeHash": target.get("nodeHash"),
        "relation": relation_text,
        "crossGraph": bool(cross_graph),
    }
    return {**body, "edgeHash": bridge._canonical_hash(body)}


def semantic_graph_identity(graph: Mapping[str, Any]) -> str:
    """Exclude only execution provenance, never business headers/evidence content."""
    material = {key: deepcopy(value) for key, value in graph.items()
                if key not in {"graphHash", "sourceExecutionIdentity"}}
    identity = _dict(graph.get("sourceExecutionIdentity"))
    material["businessScope"] = {key: identity[key] for key in ("storeId", "productId") if key in identity}
    return bridge._canonical_hash(material)


def _validate_graph_budget(nodes, edges):
    limits = bridge._authority().contract["closeoutLimits"]
    if len(nodes) > limits["maxNodesPerGraph"] or len(edges) > limits["maxEdgesPerGraph"]:
        raise ValueError("v26_graph_budget_exceeded")
    parents = {str(n["nodeKey"]): [] for n in nodes}
    for edge in edges:
        if edge.get("relation") in {"depends_on", "depends_on_stage"}:
            parents[edge["targetKey"]].append(edge["sourceKey"])
    visiting, depths = set(), {}
    def visit(key):
        if key in visiting:
            raise ValueError("v26_dependency_cycle")
        if key in depths:
            return depths[key]
        visiting.add(key)
        depth = 1 + max((visit(parent) for parent in parents[key]), default=0)
        visiting.remove(key)
        if depth > limits["maxDependencyDepth"]:
            raise ValueError("v26_dependency_depth_exceeded")
        depths[key] = depth
        return depth
    for key in parents:
        visit(key)
    if len(__import__("json").dumps([nodes, edges], ensure_ascii=False)) > limits["maxGraphChars"]:
        raise ValueError("v26_graph_context_budget_exceeded")


def _finalize_graph(
    aggregate: Mapping[str, Any],
    *,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    compatibility_node_projection: bool,
) -> Dict[str, Any]:
    _validate_graph_budget(nodes, edges)
    graph = {
        key: deepcopy(value)
        for key, value in aggregate.items()
        if key != "graphHash"
    }
    graph["version"] = V26_NODE_EDGE_LINEAGE_VERSION
    graph["nodes"] = nodes
    graph["edges"] = edges
    graph["nodeCount"] = len(nodes)
    graph["edgeCount"] = len(edges)
    graph["compatibilityNodeProjection"] = bool(compatibility_node_projection)
    graph["modelMayWriteNodeHashes"] = False
    graph["modelMayWriteEdgeHashes"] = False
    graph["graphHash"] = bridge._canonical_hash(graph)
    return graph


def compile_judgement_graph(
    normalized: Mapping[str, Any],
    raw: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    item = _dict(normalized)
    raw_item = _dict(raw)
    aggregate = _V262_COMPILE_JUDGEMENT(item, raw_item)
    aggregate_headers = _dict(aggregate.get("authorityHeaders"))
    provider_nodes = [value for value in _arr(raw_item.get("judgementNodes")) if isinstance(value, dict)]
    compatibility = not provider_nodes
    source_evidence = _source_evidence(item, {})

    nodes: List[Dict[str, Any]] = []
    raw_relations: Dict[str, List[Dict[str, Any]]] = {}
    if provider_nodes:
        for index, raw_node in enumerate(provider_nodes):
            key = _clean_key(raw_node.get("nodeKey") or raw_node.get("id"), f"J{index + 1}")
            evidence = _unique_texts(raw_node.get("evidenceRefs")) or source_evidence
            if not evidence:
                raise ValueError(f"v26_judgement_node_evidence_required:{key}")
            if not set(evidence).issubset(set(source_evidence)):
                raise ValueError(f"v26_judgement_evidence_outside_authorized_input:{key}")
            headers = {
                "judgement.node_key": key,
                "judgement.reasoning": _text(raw_node.get("reasoning") or raw_node.get("decisionSummary")),
                "judgement.primary_issue": _text(raw_node.get("primaryIssue") or raw_node.get("coreProblem")),
                "judgement.secondary_signals": raw_node.get("secondarySignals"),
                "judgement.signal_conflicts": raw_node.get("signalConflicts"),
                "judgement.possible_causes": raw_node.get("possibleCauses") or raw_node.get("causalHypotheses"),
                "judgement.rejected_hypotheses": raw_node.get("rejectedHypotheses"),
                "judgement.evidence_refs": evidence,
                "judgement.ignored_signals": raw_node.get("ignoredSignals"),
                "judgement.recommended_direction": _text(raw_node.get("recommendedDirection") or raw_node.get("actionIntent")),
                "judgement.action_family": _text(raw_node.get("actionFamilyHint") or raw_node.get("selectedActionFamilyHint"), 240),
                "judgement.priority_weight": _float01(raw_node.get("priorityWeight")),
                "judgement.evidence_confidence": _float01(raw_node.get("confidence")),
                "judgement.relations": raw_node.get("relations"),
            }
            nodes.append(
                _node(
                    schema=JUDGEMENT_NODE_SCHEMA,
                    actor="agent1",
                    node_key=key,
                    headers=headers,
                    index=index,
                    compatibility_projection=False,
                )
            )
            raw_relations[key] = [
                dict(value) for value in _arr(raw_node.get("relations")) if isinstance(value, dict)
            ]
    else:
        key = "J1"
        headers = dict(aggregate_headers)
        headers["judgement.node_key"] = key
        headers["judgement.relations"] = []
        nodes.append(
            _node(
                schema=JUDGEMENT_NODE_SCHEMA,
                actor="agent1",
                node_key=key,
                headers=headers,
                index=0,
                compatibility_projection=True,
            )
        )
        raw_relations[key] = []

    by_key = {str(node["nodeKey"]): node for node in nodes}
    if len(by_key) != len(nodes):
        raise ValueError("v26_judgement_node_key_duplicate")
    edges: List[Dict[str, Any]] = []
    for source_key, relations in raw_relations.items():
        for relation in relations:
            target_key = _clean_key(
                relation.get("targetRef") or relation.get("targetKey") or relation.get("target"),
                "",
            )
            relation_type = _text(relation.get("relation") or relation.get("type"), 120)
            if relation_type not in _ALLOWED_JUDGEMENT_RELATIONS:
                raise ValueError(f"v26_judgement_relation_invalid:{relation_type}")
            if target_key not in by_key:
                raise ValueError(f"v26_judgement_relation_orphan:{target_key}")
            if target_key == source_key:
                raise ValueError(f"v26_judgement_relation_self:{source_key}")
            edges.append(
                _edge(
                    source=by_key[source_key],
                    target=by_key[target_key],
                    relation=relation_type,
                )
            )
    return _finalize_graph(
        aggregate,
        nodes=nodes,
        edges=edges,
        compatibility_node_projection=compatibility,
    )


def _extract_judgement_graph(package: Mapping[str, Any]) -> Dict[str, Any]:
    value = _dict(package.get("v26JudgementGraph"))
    if value:
        return deepcopy(value)
    judgement = _dict(package.get("agent1OperatingJudgment"))
    value = _dict(judgement.get("v26JudgementGraph"))
    if value:
        return deepcopy(value)
    decision = _dict(package.get("agent1DecisionIR"))
    value = _dict(decision.get("v26JudgementGraph"))
    if value:
        return deepcopy(value)
    # Historical capability Artifacts may not carry the V26 graph. Project the same
    # already-authoritative Agent1 handoff into one compatibility node; no LLM call.
    return compile_judgement_graph(package, {})


def _first_value(sources: Iterable[Mapping[str, Any]], *keys: str) -> Any:
    return bridge._first_value(sources, *keys)


def _first_number(sources: Iterable[Mapping[str, Any]], *keys: str) -> float | None:
    return bridge._first_number(sources, *keys)


def compile_action_graph(
    normalized: Mapping[str, Any],
    raw: Mapping[str, Any] | None = None,
    package: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    draft = _dict(normalized)
    raw_item = _dict(raw)
    package = _dict(package)
    aggregate = _V262_COMPILE_ACTION(draft, raw_item)
    aggregate_headers = _dict(aggregate.get("authorityHeaders"))
    judgement_graph = _extract_judgement_graph(package)
    judgement_nodes = [value for value in _arr(judgement_graph.get("nodes")) if isinstance(value, dict)]
    judgement_by_key = {str(value.get("nodeKey") or ""): value for value in judgement_nodes}
    provider_nodes = [value for value in _arr(raw_item.get("actionNodes")) if isinstance(value, dict)]
    compatibility = not provider_nodes

    nodes: List[Dict[str, Any]] = []
    raw_links: Dict[str, Dict[str, List[str]]] = {}
    if provider_nodes:
        for index, raw_node in enumerate(provider_nodes):
            key = _clean_key(raw_node.get("actionKey") or raw_node.get("nodeKey") or raw_node.get("id"), f"A{index + 1}")
            refs = _unique_texts(raw_node.get("judgementRefs"))
            if not refs and len(judgement_nodes) == 1:
                refs = [str(judgement_nodes[0].get("nodeKey"))]
            if not refs:
                raise ValueError(f"v26_action_node_judgement_ref_required:{key}")
            for ref in refs:
                if ref not in judgement_by_key:
                    raise ValueError(f"v26_action_node_orphan_judgement_ref:{key}:{ref}")
            sources = [raw_node, _dict(raw_node.get("parameterPack")), _dict(raw_node.get("selectedStrategy"))]
            headers = {
                "plan.action_key": key,
                "plan.action_family": _text(raw_node.get("actionFamily") or raw_node.get("selectedActionFamily"), 240),
                "plan.judgement_refs": refs,
                "plan.strategy_summary": _text(raw_node.get("strategySummary") or raw_node.get("strategyReason")),
                "plan.candidate_strategies": raw_node.get("candidateStrategies"),
                "plan.selected_strategy": raw_node.get("selectedStrategy"),
                "plan.parameter_pack": _dict(raw_node.get("parameterPack") or raw_node.get("familyPayload")),
                "plan.daily_budget": _first_number(sources, "dailyBudget", "daily_budget", "budget"),
                "plan.target_roas": _first_number(sources, "targetRoas", "targetROAS", "target_roas"),
                "plan.review_window": _text(_first_value(sources, "reviewWindow", "review_window", "verificationPeriod"), 240),
                "plan.expected_trend": _first_value(sources, "expectedTrend", "expected_trend"),
                "plan.lower_guard": _first_value(sources, "lowerGuard", "lower_guard", "lowerBoundary"),
                "plan.upper_guard": _first_value(sources, "upperGuard", "upper_guard", "upperBoundary"),
                "plan.minimum_evidence": _first_value(sources, "minimumEvidence", "minimum_evidence", "requiredEvidence"),
                "plan.acceptance_criteria": _first_value(sources, "acceptanceCriteria", "validationMetrics"),
                "plan.risk_boundaries": _first_value(sources, "riskBoundaries", "stopConditions"),
                "plan.dependencies": _unique_texts(raw_node.get("dependencies")),
                "plan.conflicts": _unique_texts(raw_node.get("conflicts")),
                "plan.affected_fields": _unique_texts(raw_node.get("affectedFields")),
                "plan.affected_metrics": _unique_texts(raw_node.get("affectedMetrics")),
            }
            nodes.append(
                _node(
                    schema=ACTION_NODE_SCHEMA,
                    actor="agent2",
                    node_key=key,
                    headers=headers,
                    index=index,
                    compatibility_projection=False,
                )
            )
            raw_links[key] = {
                "judgementRefs": refs,
                "dependencies": _unique_texts(raw_node.get("dependencies")),
                "conflicts": _unique_texts(raw_node.get("conflicts")),
            }
    else:
        key = "A1"
        refs = [str(judgement_nodes[0].get("nodeKey"))] if judgement_nodes else []
        headers = dict(aggregate_headers)
        headers.update(
            {
                "plan.action_key": key,
                "plan.action_family": _text(
                    draft.get("actionFamily")
                    or draft.get("lockedActionFamily")
                    or draft.get("selectedActionFamily"),
                    240,
                ),
                "plan.judgement_refs": refs,
                "plan.dependencies": [],
                "plan.conflicts": [],
                "plan.affected_fields": [],
                "plan.affected_metrics": draft.get("validationMetrics") or [],
            }
        )
        nodes.append(
            _node(
                schema=ACTION_NODE_SCHEMA,
                actor="agent2",
                node_key=key,
                headers=headers,
                index=0,
                compatibility_projection=True,
            )
        )
        raw_links[key] = {"judgementRefs": refs, "dependencies": [], "conflicts": []}

    action_by_key = {str(node["nodeKey"]): node for node in nodes}
    if len(action_by_key) != len(nodes):
        raise ValueError("v26_action_node_key_duplicate")
    edges: List[Dict[str, Any]] = []
    for action_key, links in raw_links.items():
        action_node = action_by_key[action_key]
        for ref in links["judgementRefs"]:
            judgement_node = judgement_by_key.get(ref)
            if judgement_node is None:
                raise ValueError(f"v26_action_node_orphan_judgement_ref:{action_key}:{ref}")
            edges.append(
                _edge(
                    source=judgement_node,
                    target=action_node,
                    relation="supports_action",
                    cross_graph=True,
                )
            )
        for ref in links["dependencies"]:
            target = action_by_key.get(ref)
            if target is None:
                raise ValueError(f"v26_action_dependency_orphan:{action_key}:{ref}")
            if ref == action_key:
                raise ValueError(f"v26_action_dependency_self:{action_key}")
            edges.append(_edge(source=target, target=action_node, relation="depends_on"))
        for ref in links["conflicts"]:
            target = action_by_key.get(ref)
            if target is None:
                raise ValueError(f"v26_action_conflict_orphan:{action_key}:{ref}")
            if ref == action_key:
                raise ValueError(f"v26_action_conflict_self:{action_key}")
            edges.append(_edge(source=action_node, target=target, relation="conflicts_with"))

    graph = _finalize_graph(
        aggregate,
        nodes=nodes,
        edges=edges,
        compatibility_node_projection=compatibility,
    )
    graph["judgementGraphHash"] = judgement_graph.get("graphHash")
    graph["judgementNodeCount"] = len(judgement_nodes)
    graph["graphHash"] = bridge._canonical_hash({key: value for key, value in graph.items() if key != "graphHash"})
    return graph


def _extract_action_graph(package: Mapping[str, Any]) -> Dict[str, Any]:
    draft = _dict(package.get("agent2ActionDraft"))
    graph = _dict(draft.get("v26ActionGraph"))
    if graph:
        return deepcopy(graph)
    return {}


def compile_operation_graph(
    normalized: Mapping[str, Any],
    raw: Mapping[str, Any] | None = None,
    package: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    sop = _dict(normalized)
    raw_item = _dict(raw)
    package = _dict(package)
    aggregate = _V262_COMPILE_OPERATION(sop, raw_item, package)
    action_graph = _extract_action_graph(package)
    action_nodes = [value for value in _arr(action_graph.get("nodes")) if isinstance(value, dict)]
    action_by_key = {str(value.get("nodeKey") or ""): value for value in action_nodes}

    stages = [value for value in _arr(raw_item.get("operationStages")) if isinstance(value, dict)]
    compatibility_stage_projection = False
    if not stages:
        stages = bridge._compat_operation_stages(sop)
        compatibility_stage_projection = True

    nodes: List[Dict[str, Any]] = []
    raw_dependencies: Dict[str, List[str]] = {}
    compatibility_action_ref_projection = False
    action_keys = [str(value.get("nodeKey")) for value in action_nodes if value.get("nodeKey")]
    for index, stage in enumerate(stages):
        headers = bridge._operation_stage_headers(stage, index)
        stage_key = _clean_key(
            headers.get("operation_stage.stage_id") or stage.get("stageId") or stage.get("id"),
            f"S{index + 1}",
        )
        refs = _unique_texts(stage.get("actionRefs"))
        if not refs and action_keys:
            if not compatibility_stage_projection:
                raise ValueError(f"v26_new_operation_action_refs_required:{stage_key}")
            refs = list(action_keys)
            compatibility_action_ref_projection = True
        for ref in refs:
            if ref not in action_by_key:
                raise ValueError(f"v26_operation_stage_orphan_action_ref:{stage_key}:{ref}")
        headers["operation_stage.stage_id"] = stage_key
        headers["operation_stage.action_refs"] = refs
        bridge._authority().assert_stage_write("agent3", "operationStage")
        nodes.append(
            _node(
                schema=OPERATION_NODE_SCHEMA,
                actor="agent3",
                node_key=stage_key,
                headers=headers,
                index=index,
                compatibility_projection=compatibility_stage_projection,
            )
        )
        raw_dependencies[stage_key] = _unique_texts(stage.get("dependencies"))

    stage_by_key = {str(node["nodeKey"]): node for node in nodes}
    if len(stage_by_key) != len(nodes):
        raise ValueError("v26_operation_stage_key_duplicate")
    edges: List[Dict[str, Any]] = []
    for stage in nodes:
        stage_key = str(stage["nodeKey"])
        refs = _unique_texts(_dict(stage.get("authorityHeaders")).get("operation_stage.action_refs"))
        for ref in refs:
            action = action_by_key.get(ref)
            if action is None:
                raise ValueError(f"v26_operation_stage_orphan_action_ref:{stage_key}:{ref}")
            edges.append(
                _edge(
                    source=action,
                    target=stage,
                    relation="implemented_by",
                    cross_graph=True,
                )
            )
        for dependency in raw_dependencies.get(stage_key, []):
            source = stage_by_key.get(dependency)
            if source is None:
                raise ValueError(f"v26_operation_stage_dependency_orphan:{stage_key}:{dependency}")
            if dependency == stage_key:
                raise ValueError(f"v26_operation_stage_dependency_self:{stage_key}")
            edges.append(_edge(source=source, target=stage, relation="depends_on_stage"))

    _validate_graph_budget(nodes, edges)
    graph = {
        key: deepcopy(value)
        for key, value in aggregate.items()
        if key not in {"graphHash", "operationStages", "operationStageCount"}
    }
    graph["version"] = V26_NODE_EDGE_LINEAGE_VERSION
    graph["nodes"] = nodes
    graph["edges"] = edges
    graph["operationStages"] = [
        {**deepcopy(node), "stageHash": node["nodeHash"]} for node in nodes
    ]
    graph["nodeCount"] = len(nodes)
    graph["edgeCount"] = len(edges)
    graph["operationStageCount"] = len(nodes)
    graph["compatibilityStageProjection"] = compatibility_stage_projection
    graph["compatibilityActionRefProjection"] = compatibility_action_ref_projection
    graph["actionGraphHash"] = action_graph.get("graphHash")
    graph["resolvedPlanReferences"] = [
        {"actionNodeHash": node.get("nodeHash"), "actionGraphHash": action_graph.get("graphHash"),
         "field": field, "valueHash": bridge._canonical_hash(value)}
        for node in action_nodes for field, value in _dict(node.get("authorityHeaders")).items()
        if field.startswith("plan.")
    ]
    graph["actionNodeCount"] = len(action_nodes)
    graph["modelMayWriteNodeHashes"] = False
    graph["modelMayWriteEdgeHashes"] = False
    graph["systemStageMutationAllowed"] = False
    graph["graphHash"] = bridge._canonical_hash(graph)
    return graph


def _append_system_contract(messages: List[Dict[str, str]], addition: str) -> List[Dict[str, str]]:
    return bridge._append_system_contract(messages, addition)


def _raw_by_execution(payload: Mapping[str, Any], key: str) -> Dict[str, Dict[str, Any]]:
    return bridge._raw_by_execution(payload, key)


def _extract_transport_judgement_graph(source: Mapping[str, Any]) -> Dict[str, Any]:
    direct = _dict(source.get("v26JudgementGraph"))
    if direct:
        return deepcopy(direct)
    judgement = _dict(source.get("agent1OperatingJudgment"))
    nested = _dict(judgement.get("v26JudgementGraph"))
    if nested:
        return deepcopy(nested)
    decision = _dict(source.get("agent1DecisionIR"))
    nested = _dict(decision.get("v26JudgementGraph"))
    if nested:
        return deepcopy(nested)
    return compile_judgement_graph(source, {})


def install_v26_node_edge_lineage() -> Dict[str, Any]:
    """Install V26.5 on top of the already-installed V26.2 migration seams."""
    global _INSTALLED
    if _INSTALLED:
        return {
            "version": V26_NODE_EDGE_LINEAGE_VERSION,
            "installed": True,
            "idempotentReplay": True,
        }

    from src.services import agent_input_contract_v225_service as input_contract
    from src.services import agent_input_transport_v225_service as input_transport
    from src.services import agent2_action_draft_core_v225_service as agent2_core
    from src.services import agent_token_runtime_v22520_service as agent2_runtime
    from src.services import agent3_sop_core_v225_service as agent3_core
    from src.services import real_product_judgment_agent_v2259_service as agent1_core

    # ---- Agent1: preserve legacy primary projection, add provider-authored nodes. ----
    agent1_build = agent1_core._build_messages
    agent1_normalize = agent1_core._normalize_judgments

    def agent1_build_v265(*args, **kwargs):
        messages, payload = agent1_build(*args, **kwargs)
        return _append_system_contract(
            messages,
            "V26.5 Node/Edge JudgementGraph：保留原有唯一primary字段作为兼容投影，但同时可以在每个judgment中输出"
            "judgementNodes数组，数量按真实经营判断需要决定，不固定。每个节点使用nodeKey、reasoning、primaryIssue、"
            "secondarySignals、signalConflicts、possibleCauses、rejectedHypotheses、evidenceRefs、ignoredSignals、"
            "recommendedDirection、actionFamilyHint、priorityWeight、confidence、relations。relations仅写targetRef+relation，"
            "不得生成任何hash；evidenceRefs只能引用输入中已有证据，不得伪造。允许一项经营任务存在多个判断。",
        ), payload

    def agent1_normalize_v265(provider_payload, products, data_version):
        normalized, diagnostics = agent1_normalize(provider_payload, products, data_version)
        raw_map = _raw_by_execution(_dict(provider_payload), "judgments")
        for item in normalized:
            execution_id = _text(_dict(item).get("itemExecutionId"), 180)
            graph = compile_judgement_graph(item, raw_map.get(execution_id))
            item["v26JudgementGraph"] = graph
        return normalized, diagnostics

    agent1_core._build_messages = agent1_build_v265
    agent1_core._normalize_judgments = agent1_normalize_v265

    # ---- Agent2 transport: the full JudgementGraph becomes a hard projected input. ----
    input_contract._AGENT2_DRAFT_KEYS.add("v26JudgementGraph")
    compile_agent2_v225 = input_transport.compile_agent2_draft_envelope
    existing_v225 = input_transport._existing

    def compile_agent2_draft_envelope_v265(
        source,
        *,
        source_ref,
        source_content_hash,
    ):
        base = compile_agent2_v225(
            source,
            source_ref=source_ref,
            source_content_hash=source_content_hash,
        )
        payload = dict(_dict(base.get("payload")))
        payload["v26JudgementGraph"] = _extract_transport_judgement_graph(_dict(source))
        handoff_audit = _dict(_dict(base.get("projectionAudit")).get("agent1Handoff"))
        return input_transport._finalize_projection(
            schema=input_contract.AGENT2_DRAFT_INPUT_SCHEMA,
            stage="agent2_draft",
            payload=payload,
            source_ref=source_ref,
            source_content_hash=source_content_hash,
            item_budget=input_contract.AGENT2_MAX_ITEM_CHARS,
            handoff_audit=handoff_audit,
        )

    def existing_v265(row, *, ref_key, schema, source_ref, source_hash):
        existing = existing_v225(
            row,
            ref_key=ref_key,
            schema=schema,
            source_ref=source_ref,
            source_hash=source_hash,
        )
        if not existing or schema != input_contract.AGENT2_DRAFT_INPUT_SCHEMA:
            return existing
        try:
            value = input_transport.resolve_artifact(existing)
            payload = _dict(_dict(value).get("payload"))
            graph = _dict(payload.get("v26JudgementGraph"))
            if str(graph.get("version") or "") != V26_NODE_EDGE_LINEAGE_VERSION:
                return None
            if not _arr(graph.get("nodes")) or not _text(graph.get("graphHash"), 120):
                return None
        except Exception:
            return None
        return existing

    input_transport.compile_agent2_draft_envelope = compile_agent2_draft_envelope_v265
    input_transport._existing = existing_v265

    # ---- Agent2: multiple Action nodes, all linked to existing Judgement nodes. ----
    agent2_build = agent2_core._build_messages
    agent2_normalize = agent2_core._normalize_draft

    def agent2_build_v265(*args, **kwargs):
        messages, payload = agent2_build(*args, **kwargs)
        return _append_system_contract(
            messages,
            "V26.5 Node/Edge ActionGraph：原lockedActionFamily/familyPayload继续作为兼容主投影，不代表只能有一个经营动作。"
            "必须读取v26JudgementGraph，并可额外输出actionNodes数组。每个actionNode使用actionKey、actionFamily、"
            "judgementRefs、strategySummary、candidateStrategies、selectedStrategy、parameterPack、dependencies、conflicts、"
            "affectedFields、affectedMetrics以及需要的Agent2计划字段。每个动作必须至少有一个真实judgementRef；"
            "dependencies/conflicts只能引用本次actionNodes中的actionKey；不得输出nodeHash/edgeHash。",
        ), payload

    def agent2_normalize_v265(raw, package, proof=None):
        normalized = agent2_normalize(raw, package, proof)
        normalized["v26ActionGraph"] = compile_action_graph(normalized, raw, package)
        return normalized

    agent2_core._build_messages = agent2_build_v265
    agent2_core._normalize_draft = agent2_normalize_v265
    agent2_runtime._build_messages = agent2_build_v265
    agent2_runtime._normalize_draft = agent2_normalize_v265

    # Bind the complete JudgementGraph into semantic reuse identity. ExecutionHash
    # remains unchanged and authoritative; this only prevents semantic-cache aliasing.
    semantic_identity_v225 = agent2_runtime.build_agent2_semantic_identity

    def build_agent2_semantic_identity_v265(envelope, descriptor, package):
        result = dict(semantic_identity_v225(envelope, descriptor, package))
        graph = _dict(_dict(package).get("v26JudgementGraph"))
        graph_hash = _text(graph.get("graphHash"), 160)
        if not graph_hash:
            graph = _extract_transport_judgement_graph(_dict(package))
            graph_hash = _text(graph.get("graphHash"), 160)
        semantic_input_hash = agent2_runtime.hash_value(
            {
                "legacySemanticInputHash": result.get("semanticInputHash"),
                "v26JudgementSemanticHash": semantic_graph_identity(graph),
                "v26NodeEdgeLineageVersion": V26_NODE_EDGE_LINEAGE_VERSION,
            }
        )
        semantic_hash = agent2_runtime.hash_value(
            {
                "schema": result.get("schema"),
                "semanticInputHash": semantic_input_hash,
                "semanticContractHash": result.get("semanticContractHash"),
                "v26NodeEdgeLineageVersion": V26_NODE_EDGE_LINEAGE_VERSION,
            }
        )
        result.update(
            semanticInputHash=semantic_input_hash,
            semanticHash=semantic_hash,
            v26JudgementGraphHash=graph_hash,
            v26NodeEdgeLineageVersion=V26_NODE_EDGE_LINEAGE_VERSION,
        )
        return result

    agent2_runtime.build_agent2_semantic_identity = build_agent2_semantic_identity_v265

    # ---- Agent3: operation stages explicitly implement one or more Action nodes. ----
    agent3_build = agent3_core._build_messages
    agent3_normalize = agent3_core._normalize_sop

    def agent3_build_v265(*args, **kwargs):
        messages, payload = agent3_build(*args, **kwargs)
        return _append_system_contract(
            messages,
            "V26.5 Operation lineage：每个operationStages元素必须增加actionRefs，引用agent2ActionDraft.v26ActionGraph"
            "中真实存在的actionKey。一个阶段可实现多个动作，一个动作也可由多个阶段实现；dependencies仍只引用stageId。"
            "不得输出任何hash，不得创建新的SystemStage/Agent/调用边。",
        ), payload

    def agent3_normalize_v265(raw, package, proof=None):
        normalized = agent3_normalize(raw, package, proof)
        normalized["v26OperationGraph"] = compile_operation_graph(normalized, raw, package)
        if package.get("revisionScope"):
            from src.services.v26_revision_acceptance_service import verify_revision_result
            verify_revision_result(package.get("parentOperationGraph"), normalized["v26OperationGraph"],
                package["revisionScope"], graph_kind="Operation")
        from src.services.v26_sop_evidence_service import freeze_decision_evidence
        normalized["sopDecisionEvidence"] = freeze_decision_evidence(package, normalized)
        return normalized

    agent3_core._build_messages = agent3_build_v265
    agent3_core._normalize_sop = agent3_normalize_v265

    # Public compile API now resolves to the addressable V26.5 graph compilers.
    bridge.V26_BUSINESS_GRAPH_VERSION = V26_NODE_EDGE_LINEAGE_VERSION
    bridge.compile_judgement_graph = compile_judgement_graph
    bridge.compile_action_graph = compile_action_graph
    bridge.compile_operation_graph = compile_operation_graph

    _INSTALLED = True
    return {
        "version": V26_NODE_EDGE_LINEAGE_VERSION,
        "installed": True,
        "judgementNodesAddressable": True,
        "actionNodesAddressable": True,
        "operationNodesAddressable": True,
        "systemGeneratedNodeHashes": True,
        "systemGeneratedEdgeHashes": True,
        "agent2JudgementGraphTransport": True,
        "agent2SemanticIdentityIncludesJudgementGraph": True,
        "legacySinglePrimaryFieldsCompatibilityOnly": True,
        "executionHashAuthorityChanged": False,
        "systemStageMutationAllowed": False,
    }


__all__ = [
    "V26_NODE_EDGE_LINEAGE_VERSION",
    "JUDGEMENT_NODE_SCHEMA",
    "ACTION_NODE_SCHEMA",
    "OPERATION_NODE_SCHEMA",
    "GRAPH_EDGE_SCHEMA",
    "compile_judgement_graph",
    "compile_action_graph",
    "compile_operation_graph",
    "install_v26_node_edge_lineage",
]
