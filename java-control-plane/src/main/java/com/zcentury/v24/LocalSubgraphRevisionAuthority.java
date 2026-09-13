package com.zcentury.v24;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * V26.6 deterministic local-subgraph revision planner.
 *
 * V26.5 makes Judgement/Action/Operation nodes addressable. V26.6 consumes only
 * those already-authoritative node identities plus a V26 System Review result and
 * decides which exact business subgraph may be reopened. The model never chooses
 * node hashes and this authority never rewrites business reasoning or plan values.
 *
 * A mapped metric breach reopens the directly affected Action nodes, any downstream
 * Action nodes that depend on them, the Judgement nodes supporting those actions,
 * and the Operation nodes implementing the reopened actions (plus downstream
 * Operation dependencies). Unrelated successful nodes remain preserved.
 *
 * Legacy/non-deterministic reviews without a structured breached metric cannot be
 * localized safely and therefore fail closed to FULL_GRAPH_COMPATIBILITY.
 */
final class LocalSubgraphRevisionAuthority {
    enum ScopeMode {
        LOCAL,
        FULL_GRAPH_COMPATIBILITY
    }

    record JudgementNode(String nodeKey, String nodeHash) {}

    record ActionNode(
        String nodeKey,
        String nodeHash,
        List<String> judgementRefs,
        List<String> dependencies,
        List<String> affectedMetrics
    ) {}

    record OperationNode(
        String nodeKey,
        String nodeHash,
        List<String> actionRefs,
        List<String> dependencies
    ) {}

    record GraphSnapshot(
        String judgementGraphHash,
        String actionGraphHash,
        String operationGraphHash,
        List<JudgementNode> judgementNodes,
        List<ActionNode> actionNodes,
        List<OperationNode> operationNodes,
        Set<String> successfulNodeHashes
    ) {
        GraphSnapshot(String j, String a, String o, List<JudgementNode> js, List<ActionNode> as, List<OperationNode> os) {
            this(j, a, o, js, as, os, Set.of());
        }
        GraphSnapshot { successfulNodeHashes = Set.copyOf(successfulNodeHashes); }
    }

    record Scope(
        ScopeMode mode,
        String reason,
        String reviewHash,
        List<String> breachedMetrics,
        String parentJudgementGraphHash,
        String parentActionGraphHash,
        String parentOperationGraphHash,
        List<String> reopenJudgementNodeHashes,
        List<String> reopenActionNodeHashes,
        List<String> reopenOperationNodeHashes,
        List<String> preservedJudgementNodeHashes,
        List<String> preservedActionNodeHashes,
        List<String> preservedOperationNodeHashes,
        String revisionHash
    ) {}

    private LocalSubgraphRevisionAuthority() {}

    static Scope plan(
        ReviewContractAuthority.Contract contract,
        SystemReviewAuthority.Result review,
        GraphSnapshot graph
    ) {
        if (contract == null) throw new IllegalArgumentException("revision_review_contract_required");
        if (review == null) throw new IllegalArgumentException("revision_review_result_required");
        if (graph == null) throw new IllegalArgumentException("revision_graph_snapshot_required");
        if (review.decision() != SystemReviewAuthority.Decision.ADJUSTMENT_REQUIRED
            || !review.invokeAgent1()) {
            throw new IllegalStateException("revision_requires_adjustment_review");
        }
        if (!contract.contractHash().equals(review.contractHash())) {
            throw new IllegalStateException("revision_review_contract_hash_mismatch");
        }

        String judgementGraphHash = requireHash(
            graph.judgementGraphHash(), "revision_judgement_graph_hash_required"
        );
        String actionGraphHash = requireHash(
            graph.actionGraphHash(), "revision_action_graph_hash_required"
        );
        String operationGraphHash = requireHash(
            graph.operationGraphHash(), "revision_operation_graph_hash_required"
        );
        if (!contract.actionGraphHash().equals(actionGraphHash)) {
            throw new IllegalStateException("revision_action_graph_contract_mismatch");
        }

        Map<String, JudgementNode> judgements = judgementMap(graph.judgementNodes());
        Map<String, ActionNode> actions = actionMap(graph.actionNodes(), judgements);
        Map<String, OperationNode> operations = operationMap(graph.operationNodes(), actions);
        if (judgements.isEmpty()) throw new IllegalArgumentException("revision_judgement_nodes_required");
        if (actions.isEmpty()) throw new IllegalArgumentException("revision_action_nodes_required");
        if (operations.isEmpty()) throw new IllegalArgumentException("revision_operation_nodes_required");

        TreeSet<String> breachedMetrics = normalizedSet(review.breachedMetrics());
        LinkedHashSet<String> reopenActionKeys = new LinkedHashSet<>();
        ScopeMode mode = ScopeMode.LOCAL;
        String reason = "MAPPED_BREACH_LOCAL_REVISION";

        if (!breachedMetrics.isEmpty()) {
            for (ActionNode action : actions.values()) {
                if (intersects(normalizedSet(action.affectedMetrics()), breachedMetrics)) {
                    reopenActionKeys.add(action.nodeKey());
                }
            }
        }

        TreeSet<String> mappedMetrics = new TreeSet<>();
        for (ActionNode action : actions.values()) mappedMetrics.addAll(action.affectedMetrics());
        if (!mappedMetrics.containsAll(breachedMetrics)) {
            reopenActionKeys.clear(); // partial metric coverage is not localizable
        }
        if (breachedMetrics.isEmpty()) {
            mode = ScopeMode.FULL_GRAPH_COMPATIBILITY;
            reason = "STRUCTURED_BREACH_METRIC_UNAVAILABLE";
            reopenActionKeys.addAll(actions.keySet());
        } else if (reopenActionKeys.isEmpty()) {
            mode = ScopeMode.FULL_GRAPH_COMPATIBILITY;
            reason = "BREACH_METRIC_UNMAPPED";
            reopenActionKeys.addAll(actions.keySet());
        } else {
            expandDownstreamActions(reopenActionKeys, actions);
        }

        LinkedHashSet<String> reopenJudgementKeys = new LinkedHashSet<>();
        for (String actionKey : reopenActionKeys) {
            reopenJudgementKeys.addAll(actions.get(actionKey).judgementRefs());
        }

        LinkedHashSet<String> reopenOperationKeys = new LinkedHashSet<>();
        if (mode == ScopeMode.FULL_GRAPH_COMPATIBILITY) {
            reopenJudgementKeys.addAll(judgements.keySet());
            reopenOperationKeys.addAll(operations.keySet());
        } else {
            for (OperationNode operation : operations.values()) {
                if (intersects(normalizedSet(operation.actionRefs()), reopenActionKeys)) {
                    reopenOperationKeys.add(operation.nodeKey());
                }
            }
            TreeSet<String> coveredActions = new TreeSet<>();
            for (String key : reopenOperationKeys) coveredActions.addAll(operations.get(key).actionRefs());
            if (!coveredActions.containsAll(reopenActionKeys)) {
                mode = ScopeMode.FULL_GRAPH_COMPATIBILITY;
                reason = "ACTION_TO_OPERATION_LINEAGE_UNMAPPED";
                reopenActionKeys.clear();
                reopenActionKeys.addAll(actions.keySet());
                reopenJudgementKeys.clear();
                reopenJudgementKeys.addAll(judgements.keySet());
                reopenOperationKeys.addAll(operations.keySet());
            } else {
                expandDownstreamOperations(reopenOperationKeys, operations);
            }
        }

        // Unknown or failed unrelated nodes cannot be labeled successful/preserved.
        Set<String> preserveCandidates = new TreeSet<>();
        preserveCandidates.addAll(preservedHashes(judgements, reopenJudgementKeys));
        preserveCandidates.addAll(preservedHashes(actions, reopenActionKeys));
        preserveCandidates.addAll(preservedHashes(operations, reopenOperationKeys));
        if (mode == ScopeMode.LOCAL && !graph.successfulNodeHashes().containsAll(preserveCandidates)) {
            mode = ScopeMode.FULL_GRAPH_COMPATIBILITY;
            reason = "PRESERVED_NODE_SUCCESS_UNPROVEN";
            reopenJudgementKeys.addAll(judgements.keySet());
            reopenActionKeys.addAll(actions.keySet());
            reopenOperationKeys.addAll(operations.keySet());
        }
        List<String> reopenJudgementHashes = hashesForKeys(reopenJudgementKeys, judgements);
        List<String> reopenActionHashes = hashesForKeys(reopenActionKeys, actions);
        List<String> reopenOperationHashes = hashesForKeys(reopenOperationKeys, operations);
        List<String> preservedJudgementHashes = preservedHashes(judgements, reopenJudgementKeys);
        List<String> preservedActionHashes = preservedHashes(actions, reopenActionKeys);
        List<String> preservedOperationHashes = preservedHashes(operations, reopenOperationKeys);

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v26.6.local_subgraph_revision.v1");
        material.put("scopeMode", mode.name());
        material.put("reason", reason);
        material.put("reviewHash", requireHash(review.reviewHash(), "revision_review_hash_required"));
        material.put("breachedMetrics", List.copyOf(breachedMetrics));
        material.put("parentJudgementGraphHash", judgementGraphHash);
        material.put("parentActionGraphHash", actionGraphHash);
        material.put("parentOperationGraphHash", operationGraphHash);
        material.put("reopenJudgementNodeHashes", reopenJudgementHashes);
        material.put("reopenActionNodeHashes", reopenActionHashes);
        material.put("reopenOperationNodeHashes", reopenOperationHashes);
        material.put("preservedJudgementNodeHashes", preservedJudgementHashes);
        material.put("preservedActionNodeHashes", preservedActionHashes);
        material.put("preservedOperationNodeHashes", preservedOperationHashes);
        String revisionHash = Hashing.canonicalHash(material);

        return new Scope(
            mode,
            reason,
            review.reviewHash(),
            List.copyOf(breachedMetrics),
            judgementGraphHash,
            actionGraphHash,
            operationGraphHash,
            reopenJudgementHashes,
            reopenActionHashes,
            reopenOperationHashes,
            preservedJudgementHashes,
            preservedActionHashes,
            preservedOperationHashes,
            revisionHash
        );
    }

    /** Adapt sealed graph nodes into the existing deterministic dependency planner. */
    static Map<String, Object> planSemantic(ReviewContractAuthority.Contract contract,
        SystemReviewAuthority.Result review, Map<String, Object> decision, Map<String, Object> plan,
        Map<String, Object> operation, String contractHash, Set<String> successfulHashes) {
        var decisions = ReviewContractAuthority.semanticGraph(decision, "DecisionGraph", contractHash);
        var plans = ReviewContractAuthority.semanticGraph(plan, "PlanGraph", contractHash);
        var operations = ReviewContractAuthority.semanticGraph(operation, "OperationGraph", contractHash);
        if (!decision.get("graphHash").equals(plan.get("upstreamGraphHash"))
            || !plan.get("graphHash").equals(operation.get("upstreamGraphHash")))
            throw new IllegalArgumentException("revision_semantic_lineage_mismatch");
        ArrayList<JudgementNode> decisionNodes = new ArrayList<>();
        for (var node : decisions.values()) {
            if (!Set.of("JudgementNode", "DecisionActionNode").contains(text(node.get("kind"))))
                throw new IllegalArgumentException("revision_decision_node_kind_invalid");
            decisionNodes.add(new JudgementNode(text(node.get("nodeKey")), text(node.get("nodeHash"))));
        }
        ArrayList<ActionNode> planNodes = new ArrayList<>();
        for (var node : plans.values()) {
            String key = text(node.get("nodeKey")), candidate = text(node.get("decisionActionRef"));
            if (!"PlanActionNode".equals(node.get("kind")) || !decisions.containsKey(candidate)
                || !"DecisionActionNode".equals(decisions.get(candidate).get("kind")))
                throw new IllegalArgumentException("revision_decision_action_ref_invalid");
            List<String> refs = semanticStrings(node.get("judgementRefs"));
            if (!new TreeSet<>(refs).equals(new TreeSet<>(semanticStrings(decisions.get(candidate).get("judgementRefs")))))
                throw new IllegalArgumentException("revision_judgement_reselection");
            for (String ref : refs) if (!decisions.containsKey(ref) || !"JudgementNode".equals(decisions.get(ref).get("kind")))
                throw new IllegalArgumentException("revision_judgement_ref_invalid");
            ArrayList<String> supporting = new ArrayList<>(refs); supporting.add(candidate);
            planNodes.add(new ActionNode(key, text(node.get("nodeHash")), supporting,
                semanticDependencies(plan, key), semanticStrings(node.get("affectedMetrics")).stream().map(m -> key + ":" + m).toList()));
        }
        ArrayList<OperationNode> operationNodes = new ArrayList<>();
        for (var node : operations.values()) {
            if (!"OperationStage".equals(node.get("kind"))) throw new IllegalArgumentException("revision_operation_node_kind_invalid");
            operationNodes.add(new OperationNode(text(node.get("nodeKey")), text(node.get("nodeHash")),
                semanticStrings(node.get("planActionRefs")), semanticDependencies(operation, text(node.get("nodeKey")))));
        }
        Scope scope = plan(contract, review, new GraphSnapshot(text(decision.get("graphHash")),
            text(plan.get("graphHash")), text(operation.get("graphHash")), decisionNodes, planNodes, operationNodes, successfulHashes));
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("schema", "v269.local_subgraph_revision.v1");
        result.put("scopeMode", scope.mode().name()); result.put("reason", scope.reason());
        result.put("reviewHash", scope.reviewHash()); result.put("breachedMetrics", scope.breachedMetrics());
        result.put("parentDecisionGraphHash", scope.parentJudgementGraphHash());
        result.put("parentPlanGraphHash", scope.parentActionGraphHash());
        result.put("parentOperationGraphHash", scope.parentOperationGraphHash());
        result.put("reopenDecisionNodeHashes", scope.reopenJudgementNodeHashes());
        result.put("reopenPlanNodeHashes", scope.reopenActionNodeHashes());
        result.put("reopenOperationNodeHashes", scope.reopenOperationNodeHashes());
        result.put("preservedDecisionNodeHashes", scope.preservedJudgementNodeHashes());
        result.put("preservedPlanNodeHashes", scope.preservedActionNodeHashes());
        result.put("preservedOperationNodeHashes", scope.preservedOperationNodeHashes());
        result.put("revisionHash", Hashing.canonicalHash(result));
        return Map.copyOf(result);
    }

    private static String text(Object value) {
        if (!(value instanceof String text) || text.isBlank()) throw new IllegalArgumentException("revision_semantic_text_required");
        return text;
    }

    private static List<String> semanticStrings(Object raw) {
        ArrayList<String> result = new ArrayList<>();
        for (Object value : Json.array(raw)) {
            if (!(value instanceof String text) || text.isBlank() || result.contains(text))
                throw new IllegalArgumentException("revision_semantic_refs_invalid");
            result.add(text);
        }
        return List.copyOf(result);
    }

    private static List<String> semanticDependencies(Map<String, Object> graph, String key) {
        TreeSet<String> result = new TreeSet<>();
        for (Object raw : Json.array(graph.get("edges"))) {
            Map<String, Object> edge = Json.object(raw);
            if (key.equals(edge.get("targetRef")) && Set.of("depends_on", "sequence", "enables").contains(text(edge.get("relation"))))
                result.add(text(edge.get("sourceRef")));
        }
        return List.copyOf(result);
    }

    static Map<String, Object> authorityHeaders(Scope scope) {
        if (scope == null) throw new IllegalArgumentException("revision_scope_required");
        LinkedHashMap<String, Object> headers = new LinkedHashMap<>();
        headers.put("revision.review_hash", scope.reviewHash());
        headers.put("revision.scope_mode", scope.mode().name());
        headers.put("revision.scope_reason", scope.reason());
        headers.put("revision.breached_metrics", scope.breachedMetrics());
        headers.put("revision.parent_judgement_graph_hash", scope.parentJudgementGraphHash());
        headers.put("revision.parent_action_graph_hash", scope.parentActionGraphHash());
        headers.put("revision.parent_operation_graph_hash", scope.parentOperationGraphHash());
        headers.put("revision.reopen_judgement_node_hashes", scope.reopenJudgementNodeHashes());
        headers.put("revision.reopen_action_node_hashes", scope.reopenActionNodeHashes());
        headers.put("revision.reopen_operation_node_hashes", scope.reopenOperationNodeHashes());
        headers.put("revision.preserved_judgement_node_hashes", scope.preservedJudgementNodeHashes());
        headers.put("revision.preserved_action_node_hashes", scope.preservedActionNodeHashes());
        headers.put("revision.preserved_operation_node_hashes", scope.preservedOperationNodeHashes());
        headers.put("revision.revision_hash", scope.revisionHash());
        return Map.copyOf(headers);
    }

    private static Map<String, JudgementNode> judgementMap(List<JudgementNode> raw) {
        TreeMap<String, JudgementNode> result = new TreeMap<>();
        TreeSet<String> hashes = new TreeSet<>();
        for (JudgementNode node : safe(raw)) {
            if (node == null) throw new IllegalArgumentException("revision_judgement_node_null");
            String key = requireText(node.nodeKey(), "revision_judgement_node_key_required");
            String hash = requireHash(node.nodeHash(), "revision_judgement_node_hash_required");
            if (result.putIfAbsent(key, new JudgementNode(key, hash)) != null) {
                throw new IllegalArgumentException("revision_judgement_node_key_duplicate:" + key);
            }
            if (!hashes.add(hash)) {
                throw new IllegalArgumentException("revision_judgement_node_hash_duplicate:" + hash);
            }
        }
        return Map.copyOf(result);
    }

    private static Map<String, ActionNode> actionMap(
        List<ActionNode> raw,
        Map<String, JudgementNode> judgements
    ) {
        TreeMap<String, ActionNode> result = new TreeMap<>();
        TreeSet<String> hashes = new TreeSet<>();
        for (ActionNode node : safe(raw)) {
            if (node == null) throw new IllegalArgumentException("revision_action_node_null");
            String key = requireText(node.nodeKey(), "revision_action_node_key_required");
            String hash = requireHash(node.nodeHash(), "revision_action_node_hash_required");
            List<String> judgementRefs = normalizedList(node.judgementRefs());
            if (judgementRefs.isEmpty()) {
                throw new IllegalArgumentException("revision_action_judgement_ref_required:" + key);
            }
            for (String ref : judgementRefs) {
                if (!judgements.containsKey(ref)) {
                    throw new IllegalArgumentException("revision_action_judgement_ref_orphan:" + key + ":" + ref);
                }
            }
            ActionNode normalized = new ActionNode(
                key,
                hash,
                judgementRefs,
                normalizedList(node.dependencies()),
                normalizedList(node.affectedMetrics())
            );
            if (result.putIfAbsent(key, normalized) != null) {
                throw new IllegalArgumentException("revision_action_node_key_duplicate:" + key);
            }
            if (!hashes.add(hash)) {
                throw new IllegalArgumentException("revision_action_node_hash_duplicate:" + hash);
            }
        }
        for (ActionNode node : result.values()) {
            for (String dependency : node.dependencies()) {
                if (dependency.equals(node.nodeKey())) {
                    throw new IllegalArgumentException("revision_action_dependency_self:" + node.nodeKey());
                }
                if (!result.containsKey(dependency)) {
                    throw new IllegalArgumentException(
                        "revision_action_dependency_orphan:" + node.nodeKey() + ":" + dependency
                    );
                }
            }
        }
        assertAcyclic(result.entrySet().stream().collect(java.util.stream.Collectors.toMap(Map.Entry::getKey, e -> e.getValue().dependencies())));
        return Map.copyOf(result);
    }

    private static Map<String, OperationNode> operationMap(
        List<OperationNode> raw,
        Map<String, ActionNode> actions
    ) {
        TreeMap<String, OperationNode> result = new TreeMap<>();
        TreeSet<String> hashes = new TreeSet<>();
        for (OperationNode node : safe(raw)) {
            if (node == null) throw new IllegalArgumentException("revision_operation_node_null");
            String key = requireText(node.nodeKey(), "revision_operation_node_key_required");
            String hash = requireHash(node.nodeHash(), "revision_operation_node_hash_required");
            List<String> actionRefs = normalizedList(node.actionRefs());
            if (actionRefs.isEmpty()) {
                throw new IllegalArgumentException("revision_operation_action_ref_required:" + key);
            }
            for (String ref : actionRefs) {
                if (!actions.containsKey(ref)) {
                    throw new IllegalArgumentException("revision_operation_action_ref_orphan:" + key + ":" + ref);
                }
            }
            OperationNode normalized = new OperationNode(
                key,
                hash,
                actionRefs,
                normalizedList(node.dependencies())
            );
            if (result.putIfAbsent(key, normalized) != null) {
                throw new IllegalArgumentException("revision_operation_node_key_duplicate:" + key);
            }
            if (!hashes.add(hash)) {
                throw new IllegalArgumentException("revision_operation_node_hash_duplicate:" + hash);
            }
        }
        for (OperationNode node : result.values()) {
            for (String dependency : node.dependencies()) {
                if (dependency.equals(node.nodeKey())) {
                    throw new IllegalArgumentException("revision_operation_dependency_self:" + node.nodeKey());
                }
                if (!result.containsKey(dependency)) {
                    throw new IllegalArgumentException(
                        "revision_operation_dependency_orphan:" + node.nodeKey() + ":" + dependency
                    );
                }
            }
        }
        assertAcyclic(result.entrySet().stream().collect(java.util.stream.Collectors.toMap(Map.Entry::getKey, e -> e.getValue().dependencies())));
        return Map.copyOf(result);
    }

    private static void assertAcyclic(Map<String, List<String>> dependencies) {
        Set<String> done = new LinkedHashSet<>();
        while (done.size() < dependencies.size()) {
            int before = done.size();
            dependencies.forEach((key, parents) -> { if (done.containsAll(parents)) done.add(key); });
            if (before == done.size()) throw new IllegalArgumentException("revision_dependency_cycle");
        }
    }

    private static void expandDownstreamActions(
        LinkedHashSet<String> selected,
        Map<String, ActionNode> actions
    ) {
        boolean changed;
        do {
            changed = false;
            for (ActionNode node : actions.values()) {
                if (selected.contains(node.nodeKey())) continue;
                for (String dependency : node.dependencies()) {
                    if (selected.contains(dependency)) {
                        selected.add(node.nodeKey());
                        changed = true;
                        break;
                    }
                }
            }
        } while (changed);
    }

    private static void expandDownstreamOperations(
        LinkedHashSet<String> selected,
        Map<String, OperationNode> operations
    ) {
        boolean changed;
        do {
            changed = false;
            for (OperationNode node : operations.values()) {
                if (selected.contains(node.nodeKey())) continue;
                for (String dependency : node.dependencies()) {
                    if (selected.contains(dependency)) {
                        selected.add(node.nodeKey());
                        changed = true;
                        break;
                    }
                }
            }
        } while (changed);
    }

    private static boolean intersects(Set<String> left, Collection<String> right) {
        for (String value : right) {
            if (left.contains(value)) return true;
        }
        return false;
    }

    private static <T> List<String> hashesForKeys(
        Collection<String> keys,
        Map<String, T> nodes
    ) {
        TreeSet<String> hashes = new TreeSet<>();
        for (String key : keys) {
            T node = nodes.get(key);
            if (node instanceof JudgementNode judgement) hashes.add(judgement.nodeHash());
            else if (node instanceof ActionNode action) hashes.add(action.nodeHash());
            else if (node instanceof OperationNode operation) hashes.add(operation.nodeHash());
            else throw new IllegalStateException("revision_node_type_unknown:" + key);
        }
        return List.copyOf(hashes);
    }

    private static <T> List<String> preservedHashes(
        Map<String, T> nodes,
        Collection<String> reopenedKeys
    ) {
        TreeSet<String> preserved = new TreeSet<>();
        for (Map.Entry<String, T> entry : nodes.entrySet()) {
            if (reopenedKeys.contains(entry.getKey())) continue;
            T node = entry.getValue();
            if (node instanceof JudgementNode judgement) preserved.add(judgement.nodeHash());
            else if (node instanceof ActionNode action) preserved.add(action.nodeHash());
            else if (node instanceof OperationNode operation) preserved.add(operation.nodeHash());
        }
        return List.copyOf(preserved);
    }

    private static TreeSet<String> normalizedSet(Collection<String> values) {
        return new TreeSet<>(normalizedList(values));
    }

    private static List<String> normalizedList(Collection<String> values) {
        TreeSet<String> result = new TreeSet<>();
        if (values == null) return List.of();
        for (String value : values) {
            if (value != null && !value.isBlank()) result.add(value.trim());
        }
        return List.copyOf(result);
    }

    private static <T> List<T> safe(List<T> value) {
        return value == null ? List.of() : value;
    }

    private static String requireHash(String value, String error) {
        String text = requireText(value, error);
        if (!text.matches("sha256:[0-9a-f]{64}")) {
            throw new IllegalArgumentException(error);
        }
        return text;
    }

    private static String requireText(String value, String error) {
        if (value == null || value.isBlank()) throw new IllegalArgumentException(error);
        return value.trim();
    }
}
