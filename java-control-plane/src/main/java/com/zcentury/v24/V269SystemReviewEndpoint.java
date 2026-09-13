package com.zcentury.v24;

import com.sun.net.httpserver.HttpExchange;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * V26.9.A read-only production Review endpoint.
 *
 * It is deliberately incapable of mutating task state or queue state. The endpoint
 * binds ReviewContract/SystemReview/LocalRevision calculations to the existing
 * Authority Generation root and returns one immutable receipt for the Python
 * production lifecycle writer to consume. Missing root state fails closed; this
 * endpoint never initializes or rotates a production Authority Generation.
 */
final class V269SystemReviewEndpoint {
    static final String PATH = "/v1/v269/system-review";
    static final String VERSION = "26.9.0";
    private static final int MAX_BODY_BYTES = 2_000_000;

    private V269SystemReviewEndpoint() {}

    static void handle(HttpExchange exchange) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            exchange.getResponseHeaders().set("Allow", "POST");
            exchange.sendResponseHeaders(405, -1);
            exchange.close();
            return;
        }
        byte[] raw = exchange.getRequestBody().readNBytes(MAX_BODY_BYTES + 1);
        if (raw.length > MAX_BODY_BYTES) {
            write(exchange, 413, error("REQUEST_TOO_LARGE", "review_request_too_large"));
            return;
        }
        try {
            Map<String, Object> request = Json.object(Json.parse(new String(raw, StandardCharsets.UTF_8)));
            write(exchange, 200, evaluate(request));
        } catch (Unavailable unavailable) {
            write(exchange, 503, error("REVIEW_UNAVAILABLE", unavailable.getMessage()));
        } catch (RuntimeException invalid) {
            write(exchange, 422, error("REVIEW_REJECTED", invalid.getMessage()));
        } catch (Exception failure) {
            write(exchange, 500, error("REVIEW_FAILED", failure.getClass().getSimpleName()));
        }
    }

    static Map<String, Object> evaluate(Map<String, Object> request) throws Exception {
        if (!"v269.system_review.request.v1".equals(text(request.get("schema")))) {
            throw new IllegalArgumentException("v269_review_request_schema_invalid");
        }
        String taskId = requiredText(request.get("taskId"), "v269_review_task_id_required");
        String productId = requiredText(request.get("productId"), "v269_review_product_id_required");
        String semanticContractHash = requiredText(
            request.get("semanticContractHash"), "v269_review_semantic_contract_hash_required"
        );
        long frozenAtMillis = wholeNumber(request.get("frozenAtMillis"), "v269_review_frozen_at_invalid");
        long observedAtMillis = wholeNumber(request.get("observedAtMillis"), "v269_review_observed_at_invalid");
        if (observedAtMillis < frozenAtMillis) throw new IllegalArgumentException("v269_review_observation_before_freeze");

        Map<String, Object> decision = Json.object(request.get("DecisionGraph"));
        Map<String, Object> plan = Json.object(request.get("PlanGraph"));
        Map<String, Object> operation = Json.object(request.get("OperationGraph"));
        Map<String, Object> baselineFacts = Json.object(request.get("baselineFacts"));
        Map<String, Object> targetFacts = Json.object(request.get("targetFacts"));
        if (baselineFacts.isEmpty()) throw new IllegalArgumentException("v269_review_baseline_facts_required");
        if (targetFacts.isEmpty()) throw new IllegalArgumentException("v269_review_target_facts_required");

        // Verify all three sealed graphs and lineage before any review calculation.
        ReviewContractAuthority.semanticGraph(decision, "DecisionGraph", semanticContractHash);
        Map<String, Map<String, Object>> planNodes = ReviewContractAuthority.semanticGraph(
            plan, "PlanGraph", semanticContractHash
        );
        ReviewContractAuthority.semanticGraph(operation, "OperationGraph", semanticContractHash);
        if (!decision.get("graphHash").equals(plan.get("upstreamGraphHash"))
            || !plan.get("graphHash").equals(operation.get("upstreamGraphHash"))) {
            throw new IllegalArgumentException("v269_review_graph_lineage_mismatch");
        }

        Path generationState = generationStatePath();
        AuthorityGenerationStore store = new AuthorityGenerationStore(generationState);
        // statePath must already exist. Calling status now only validates the existing state.
        Map<String, Object> rootState = store.status();
        UnifiedAuthorityGenerationRoot root = new UnifiedAuthorityGenerationRoot(store);
        RootBoundAuthorityAdapter reviewAdapter = new RootBoundAuthorityAdapter(
            "INFORMATION", SystemReviewAuthority.class.getName(), root
        );
        RootBoundAuthorityAdapter revisionAdapter = new RootBoundAuthorityAdapter(
            "INFORMATION", LocalSubgraphRevisionAuthority.class.getName(), root
        );
        RootBoundAuthorityAdapter.Token reviewToken = reviewAdapter.token();
        RootBoundAuthorityAdapter.Token revisionToken = revisionAdapter.token();

        Set<String> successful = stringSet(request.get("successfulNodeHashes"));
        ArrayList<Map<String, Object>> actionReviews = new ArrayList<>();
        ArrayList<Map<String, Object>> adjustmentScopes = new ArrayList<>();
        boolean waitingTime = false;
        boolean waitingEvidence = false;
        boolean adjustment = false;

        for (Map.Entry<String, Map<String, Object>> entry : planNodes.entrySet()) {
            String actionRef = entry.getKey();
            Map<String, Object> node = entry.getValue();
            if (!"PlanActionNode".equals(node.get("kind"))) {
                throw new IllegalArgumentException("v269_review_plan_node_kind_invalid");
            }
            ReviewContractAuthority.Contract contract = reviewAdapter.execute(
                reviewToken,
                "V26_9_FREEZE_PLAN_ACTION_REVIEW",
                () -> ReviewContractAuthority.freezePlanAction(
                    productId, taskId, plan, semanticContractHash, actionRef,
                    baselineFacts, frozenAtMillis
                )
            );
            Map<String, Double> scopedTargets = scopedTargetMetrics(actionRef, node, targetFacts);
            SystemReviewAuthority.Observation observation = new SystemReviewAuthority.Observation(
                observedAtMillis, scopedTargets, Map.of()
            );
            SystemReviewAuthority.Result review = reviewAdapter.execute(
                reviewToken,
                "V26_9_COMPARE_TARGET_FACTS",
                () -> SystemReviewAuthority.evaluate(contract, observation)
            );
            Map<String, Object> reviewView = reviewView(actionRef, contract, review);
            if (review.decision() == SystemReviewAuthority.Decision.ADJUSTMENT_REQUIRED) {
                adjustment = true;
                Map<String, Object> scope = revisionAdapter.execute(
                    revisionToken,
                    "V26_9_PLAN_LOCAL_REVISION",
                    () -> LocalSubgraphRevisionAuthority.planSemantic(
                        contract, review, decision, plan, operation,
                        semanticContractHash, successful
                    )
                );
                reviewView = new LinkedHashMap<>(reviewView);
                reviewView.put("revisionScope", scope);
                adjustmentScopes.add(scope);
            } else if (review.decision() == SystemReviewAuthority.Decision.WAITING_EVIDENCE) {
                waitingEvidence = true;
            } else if (review.decision() == SystemReviewAuthority.Decision.WAITING_TIME) {
                waitingTime = true;
            }
            actionReviews.add(Map.copyOf(reviewView));
        }
        if (actionReviews.isEmpty()) throw new IllegalArgumentException("v269_review_plan_actions_required");

        String aggregateDecision = adjustment ? "ADJUSTMENT_REQUIRED"
            : waitingEvidence ? "WAITING_EVIDENCE"
            : waitingTime ? "WAITING_TIME"
            : "SETTLED";
        Map<String, Object> revisionDirective = adjustment
            ? combineScopes(decision, plan, operation, adjustmentScopes, successful)
            : Map.of();

        LinkedHashMap<String, Object> receipt = new LinkedHashMap<>();
        receipt.put("schema", "v269.system_review.receipt.v1");
        receipt.put("version", VERSION);
        receipt.put("taskId", taskId);
        receipt.put("productId", productId);
        receipt.put("semanticContractHash", semanticContractHash);
        receipt.put("DecisionGraphHash", decision.get("graphHash"));
        receipt.put("PlanGraphHash", plan.get("graphHash"));
        receipt.put("OperationGraphHash", operation.get("graphHash"));
        receipt.put("frozenAtMillis", frozenAtMillis);
        receipt.put("observedAtMillis", observedAtMillis);
        receipt.put("targetFactsHash", Hashing.canonicalHash(targetFacts));
        receipt.put("aggregateDecision", aggregateDecision);
        receipt.put("actionReviews", List.copyOf(actionReviews));
        receipt.put("revisionDirective", revisionDirective);
        receipt.put("authorityGenerationHash", rootState.get("generationHash"));
        receipt.put("authorityGenerationSeq", rootState.get("generationSeq"));
        receipt.put("rootBound", reviewAdapter.matches(reviewToken) && revisionAdapter.matches(revisionToken));
        receipt.put("productionMutationPerformed", false);
        receipt.put("ragFeedbackPerformed", false);
        receipt.put("receiptHash", Hashing.canonicalHash(receipt));
        return Map.copyOf(receipt);
    }

    private static Map<String, Double> scopedTargetMetrics(
        String actionRef,
        Map<String, Object> planNode,
        Map<String, Object> targetFacts
    ) {
        Map<String, Object> baseline = Json.object(planNode.get("baseline"));
        LinkedHashMap<String, Double> metrics = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : baseline.entrySet()) {
            String metric = entry.getKey();
            Map<String, Object> base = Json.object(entry.getValue());
            Map<String, Object> target = Json.object(targetFacts.get(metric));
            if (target.isEmpty()) continue;
            if (!text(base.get("unit")).equals(text(target.get("unit")))) {
                throw new IllegalArgumentException("v269_review_target_unit_mismatch:" + metric);
            }
            Double value = finiteNumber(target.get("value"));
            if (value == null) throw new IllegalArgumentException("v269_review_target_value_invalid:" + metric);
            metrics.put(actionRef + ":" + metric, value);
        }
        return Map.copyOf(metrics);
    }

    private static Map<String, Object> reviewView(
        String actionRef,
        ReviewContractAuthority.Contract contract,
        SystemReviewAuthority.Result review
    ) {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("planActionRef", actionRef);
        value.put("contractHash", contract.contractHash());
        value.put("reviewDueAtMillis", contract.reviewDueAtMillis());
        value.put("deterministicSpec", contract.deterministicSpec());
        value.put("decision", review.decision().name());
        value.put("reason", review.reason());
        value.put("breaches", review.breaches());
        value.put("breachedMetrics", review.breachedMetrics());
        value.put("invokeAgent1", review.invokeAgent1());
        value.put("reviewHash", review.reviewHash());
        return Map.copyOf(value);
    }

    private static Map<String, Object> combineScopes(
        Map<String, Object> decision,
        Map<String, Object> plan,
        Map<String, Object> operation,
        List<Map<String, Object>> scopes,
        Set<String> successful
    ) {
        LinkedHashSet<String> reopenDecision = new LinkedHashSet<>();
        LinkedHashSet<String> reopenPlan = new LinkedHashSet<>();
        LinkedHashSet<String> reopenOperation = new LinkedHashSet<>();
        TreeSet<String> breached = new TreeSet<>();
        boolean full = false;
        for (Map<String, Object> scope : scopes) {
            full |= "FULL_GRAPH_COMPATIBILITY".equals(scope.get("scopeMode"));
            reopenDecision.addAll(stringSet(scope.get("reopenDecisionNodeHashes")));
            reopenPlan.addAll(stringSet(scope.get("reopenPlanNodeHashes")));
            reopenOperation.addAll(stringSet(scope.get("reopenOperationNodeHashes")));
            breached.addAll(stringSet(scope.get("breachedMetrics")));
        }
        Set<String> decisionHashes = graphNodeHashes(decision);
        Set<String> planHashes = graphNodeHashes(plan);
        Set<String> operationHashes = graphNodeHashes(operation);
        if (full) {
            reopenDecision.clear(); reopenDecision.addAll(decisionHashes);
            reopenPlan.clear(); reopenPlan.addAll(planHashes);
            reopenOperation.clear(); reopenOperation.addAll(operationHashes);
        }
        List<String> preservedDecision = preserved(decisionHashes, reopenDecision, successful);
        List<String> preservedPlan = preserved(planHashes, reopenPlan, successful);
        List<String> preservedOperation = preserved(operationHashes, reopenOperation, successful);
        if (!full) {
            // If an allegedly preserved node has no success proof, fail closed to full graph.
            int expectedPreserved = decisionHashes.size() + planHashes.size() + operationHashes.size()
                - reopenDecision.size() - reopenPlan.size() - reopenOperation.size();
            if (preservedDecision.size() + preservedPlan.size() + preservedOperation.size() != expectedPreserved) {
                full = true;
                reopenDecision.clear(); reopenDecision.addAll(decisionHashes);
                reopenPlan.clear(); reopenPlan.addAll(planHashes);
                reopenOperation.clear(); reopenOperation.addAll(operationHashes);
                preservedDecision = List.of(); preservedPlan = List.of(); preservedOperation = List.of();
            }
        }
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("schema", "v269.local_subgraph_revision.v1");
        result.put("scopeMode", full ? "FULL_GRAPH_COMPATIBILITY" : "LOCAL");
        result.put("reason", full ? "COMBINED_SCOPE_FAIL_CLOSED" : "COMBINED_ACTION_REVIEW_BREACH");
        result.put("breachedMetrics", List.copyOf(breached));
        result.put("parentDecisionGraphHash", decision.get("graphHash"));
        result.put("parentPlanGraphHash", plan.get("graphHash"));
        result.put("parentOperationGraphHash", operation.get("graphHash"));
        result.put("reopenDecisionNodeHashes", sorted(reopenDecision));
        result.put("reopenPlanNodeHashes", sorted(reopenPlan));
        result.put("reopenOperationNodeHashes", sorted(reopenOperation));
        result.put("preservedDecisionNodeHashes", preservedDecision);
        result.put("preservedPlanNodeHashes", preservedPlan);
        result.put("preservedOperationNodeHashes", preservedOperation);
        result.put("sourceScopeHashes", scopes.stream().map(scope -> text(scope.get("revisionHash"))).sorted().toList());
        result.put("revisionHash", Hashing.canonicalHash(result));
        return Map.copyOf(result);
    }

    private static Set<String> graphNodeHashes(Map<String, Object> graph) {
        TreeSet<String> values = new TreeSet<>();
        for (Object raw : Json.array(graph.get("nodes"))) {
            values.add(requiredText(Json.object(raw).get("nodeHash"), "v269_review_node_hash_required"));
        }
        return Set.copyOf(values);
    }

    private static List<String> preserved(Set<String> all, Set<String> reopened, Set<String> successful) {
        TreeSet<String> result = new TreeSet<>(all);
        result.removeAll(reopened);
        result.retainAll(successful);
        return List.copyOf(result);
    }

    private static List<String> sorted(Set<String> values) {
        return values.stream().sorted().toList();
    }

    private static Set<String> stringSet(Object raw) {
        TreeSet<String> values = new TreeSet<>();
        for (Object value : Json.array(raw)) {
            if (!(value instanceof String text) || text.isBlank() || !values.add(text)) {
                throw new IllegalArgumentException("v269_review_string_set_invalid");
            }
        }
        return Set.copyOf(values);
    }

    private static Path generationStatePath() {
        String raw = System.getenv().getOrDefault("V24_AUTHORITY_GENERATION_STATE", "").trim();
        if (raw.isEmpty()) throw new Unavailable("authority_generation_state_path_not_configured");
        Path path = Path.of(raw).toAbsolutePath().normalize();
        if (!Files.isRegularFile(path)) throw new Unavailable("authority_generation_state_missing");
        return path;
    }

    private static long wholeNumber(Object value, String error) {
        if (!(value instanceof Number number)) throw new IllegalArgumentException(error);
        double raw = number.doubleValue();
        long whole = number.longValue();
        if (!Double.isFinite(raw) || raw != whole || whole < 0L) throw new IllegalArgumentException(error);
        return whole;
    }

    private static Double finiteNumber(Object value) {
        if (!(value instanceof Number number)) return null;
        double raw = number.doubleValue();
        return Double.isFinite(raw) ? raw : null;
    }

    private static String requiredText(Object value, String error) {
        String text = text(value);
        if (text.isEmpty()) throw new IllegalArgumentException(error);
        return text;
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static Map<String, Object> error(String status, String reason) {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("schema", "v269.system_review.error.v1");
        value.put("version", VERSION);
        value.put("status", status);
        value.put("reason", reason == null ? "unknown" : reason);
        value.put("productionMutationPerformed", false);
        value.put("ragFeedbackPerformed", false);
        value.put("errorHash", Hashing.canonicalHash(value));
        return Map.copyOf(value);
    }

    private static void write(HttpExchange exchange, int code, Map<String, Object> payload) throws IOException {
        byte[] body = (Json.canonical(payload) + "\n").getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.sendResponseHeaders(code, body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }

    private static final class Unavailable extends RuntimeException {
        Unavailable(String message) { super(message); }
    }
}
