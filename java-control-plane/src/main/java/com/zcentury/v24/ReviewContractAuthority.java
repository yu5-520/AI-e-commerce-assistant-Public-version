package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * V26.4 immutable review-contract compiler.
 *
 * Agent2 remains the sole writer of plan.*. Java only freezes the already-authorized
 * review fields plus system-observed baselines so the success criteria cannot drift
 * retrospectively after execution starts.
 */
final class ReviewContractAuthority {
    private static final Set<String> SUPPORTED_TRENDS = Set.of(
        "NON_DECREASE", "INCREASE", "NON_INCREASE", "DECREASE", "ANY"
    );
    private static final Set<String> SUPPORTED_SAFETY_COMPARATORS = Set.of("GTE", "LTE");

    record Contract(
        String productId,
        String taskId,
        String actionGraphHash,
        long frozenAtMillis,
        long reviewDueAtMillis,
        String reviewWindow,
        Map<String, String> expectedTrend,
        Map<String, Double> lowerGuard,
        Map<String, Double> upperGuard,
        Map<String, Double> minimumEvidence,
        Map<String, Double> baselineMetrics,
        String acceptanceCriteriaHash,
        String riskBoundariesHash,
        String sourcePlanHash,
        List<String> unsupportedFields,
        boolean deterministicSpec,
        String contractHash
    ) {}

    private ReviewContractAuthority() {}

    static Contract freeze(
        String productId,
        String taskId,
        String actionGraphHash,
        Map<String, Object> planHeaders,
        Map<String, Double> baselineMetrics,
        long frozenAtMillis,
        long reviewDueAtMillis
    ) {
        String product = requireText(productId, "review_product_id_required");
        String task = requireText(taskId, "review_task_id_required");
        String graphHash = requireText(actionGraphHash, "review_action_graph_hash_required");
        if (frozenAtMillis < 0L) throw new IllegalArgumentException("review_frozen_at_invalid");
        if (reviewDueAtMillis < frozenAtMillis) {
            throw new IllegalArgumentException("review_due_before_freeze");
        }
        if (planHeaders == null) throw new IllegalArgumentException("review_plan_headers_required");

        ArrayList<String> unsupported = new ArrayList<>();
        Map<String, String> trends = trendMap(planHeaders.get("plan.expected_trend"), unsupported);
        Map<String, Double> lower = numericMap(planHeaders.get("plan.lower_guard"), "plan.lower_guard", unsupported);
        Map<String, Double> upper = numericMap(planHeaders.get("plan.upper_guard"), "plan.upper_guard", unsupported);
        Map<String, Double> minimum = numericMap(planHeaders.get("plan.minimum_evidence"), "plan.minimum_evidence", unsupported);
        Map<String, Double> baseline = copyFiniteMap(baselineMetrics, "baseline_metrics", unsupported);

        for (Map.Entry<String, String> entry : trends.entrySet()) {
            String direction = entry.getValue();
            if (!SUPPORTED_TRENDS.contains(direction)) {
                unsupported.add("plan.expected_trend." + entry.getKey() + ":" + direction);
            } else if (!"ANY".equals(direction) && !baseline.containsKey(entry.getKey())) {
                unsupported.add("baseline_missing:" + entry.getKey());
            }
        }

        for (String metric : lower.keySet()) {
            if (upper.containsKey(metric) && lower.get(metric) > upper.get(metric)) unsupported.add("contradictory_guards:" + metric);
        }
        minimum.forEach((metric, value) -> { if (value < 0) unsupported.add("negative_evidence:" + metric); });
        for (String field : List.of("plan.acceptance_criteria", "plan.risk_boundaries")) {
            Object raw = planHeaders.get(field);
            if (raw == null) continue;
            if (!(raw instanceof List<?> conditions)) { unsupported.add(field + ":conditions_required"); continue; }
            for (Object condition : conditions) {
                if (!(condition instanceof Map<?, ?> rule)
                    || !(rule.get("metric") instanceof String metric)
                    || !(rule.get("constraint") instanceof String constraint)
                    || !(switch (constraint) {
                        case "lower_guard" -> lower.containsKey(metric);
                        case "upper_guard" -> upper.containsKey(metric);
                        case "expected_trend" -> trends.containsKey(metric);
                        default -> false;
                    })) unsupported.add(field + ":uncompiled_condition");
            }
        }
        String window = text(planHeaders.get("plan.review_window"));
        try {
            if (!window.matches("[1-9][0-9]*(ms|s|h|d)")) throw new IllegalArgumentException();
            String unit = window.replaceAll("[0-9]", "");
            long count = Long.parseLong(window.replaceAll("[^0-9]", ""));
            long scale = switch(unit) { case "ms" -> 1; case "s" -> 1000; case "h" -> 3600000; default -> 86400000; };
            if (Math.multiplyExact(count, scale) != reviewDueAtMillis - frozenAtMillis)
                unsupported.add("review_window_due_mismatch");
        } catch (RuntimeException exc) { unsupported.add("review_window_unsupported"); }

        boolean hasExpectation = !trends.isEmpty() || !lower.isEmpty() || !upper.isEmpty();
        if (!hasExpectation) unsupported.add("review_expectation_missing");
        unsupported.sort(String::compareTo);

        String reviewWindow = text(planHeaders.get("plan.review_window"));
        String acceptanceHash = Hashing.canonicalHash(Map.of(
            "plan.acceptance_criteria", safeValue(planHeaders.get("plan.acceptance_criteria"))
        ));
        String riskHash = Hashing.canonicalHash(Map.of(
            "plan.risk_boundaries", safeValue(planHeaders.get("plan.risk_boundaries"))
        ));
        String sourcePlanHash = Hashing.canonicalHash(new TreeMap<>(planHeaders));

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v26.4.review_contract.v1");
        material.put("productId", product);
        material.put("taskId", task);
        material.put("actionGraphHash", graphHash);
        material.put("frozenAtMillis", frozenAtMillis);
        material.put("reviewDueAtMillis", reviewDueAtMillis);
        material.put("reviewWindow", reviewWindow);
        material.put("expectedTrend", trends);
        material.put("lowerGuard", lower);
        material.put("upperGuard", upper);
        material.put("minimumEvidence", minimum);
        material.put("baselineMetrics", baseline);
        material.put("acceptanceCriteriaHash", acceptanceHash);
        material.put("riskBoundariesHash", riskHash);
        material.put("sourcePlanHash", sourcePlanHash);
        material.put("unsupportedFields", List.copyOf(unsupported));
        material.put("deterministicSpec", unsupported.isEmpty());
        String contractHash = Hashing.canonicalHash(material);

        return new Contract(
            product,
            task,
            graphHash,
            frozenAtMillis,
            reviewDueAtMillis,
            reviewWindow,
            Map.copyOf(trends),
            Map.copyOf(lower),
            Map.copyOf(upper),
            Map.copyOf(minimum),
            Map.copyOf(baseline),
            acceptanceHash,
            riskHash,
            sourcePlanHash,
            List.copyOf(unsupported),
            unsupported.isEmpty(),
            contractHash
        );
    }

    /** Freeze one PlanAction without collapsing other actions or their review windows. */
    static Contract freezePlanAction(String productId, String taskId, Map<String, Object> graph,
        String expectedContractHash, String actionRef, Map<String, Object> facts, long frozenAtMillis) {
        Map<String, Map<String, Object>> nodes = semanticGraph(graph, "PlanGraph", expectedContractHash);
        Map<String, Object> node = nodes.get(actionRef);
        if (node == null || !"PlanActionNode".equals(node.get("kind")))
            throw new IllegalArgumentException("review_plan_action_missing");
        Map<String, Object> baseline = Json.object(node.get("baseline"));
        Map<String, Object> outcomes = Json.object(node.get("expectedOutcome"));
        TreeMap<String, Double> bases = new TreeMap<>(), lower = new TreeMap<>(), upper = new TreeMap<>();
        ArrayList<Object> criteria = new ArrayList<>(), safetyCriteria = new ArrayList<>();
        if (outcomes.isEmpty() || !outcomes.keySet().equals(baseline.keySet()) || Json.array(node.get("acceptanceCriteria")).isEmpty())
            throw new IllegalArgumentException("review_plan_criteria_or_baseline_missing");
        for (Map.Entry<String, Object> entry : outcomes.entrySet()) {
            String metric = entry.getKey(), scoped = actionRef + ":" + metric;
            Map<String, Object> base = Json.object(baseline.get(metric));
            Map<String, Object> fact = Json.object(facts.get(text(base.get("sourceRef"))));
            Double value = finiteNumber(base.get("value"));
            if (value == null || !value.equals(finiteNumber(fact.get("value")))
                || !base.get("unit").equals(fact.get("unit")))
                throw new IllegalArgumentException("review_baseline_fact_mismatch");
            Map<String, Object> outcome = Json.object(entry.getValue());
            List<Object> bounds = Json.array(outcome.get("expectedRange"));
            Double expected = finiteNumber(outcome.get("expectedValue")), delta = finiteNumber(outcome.get("expectedDelta"));
            if (bounds.size() != 2 || expected == null || delta == null
                || finiteNumber(bounds.get(0)) == null || finiteNumber(bounds.get(1)) == null)
                throw new IllegalArgumentException("review_expected_value_invalid");
            double lo = finiteNumber(bounds.get(0)), hi = finiteNumber(bounds.get(1));
            if (lo > expected || expected > hi || Math.abs(expected - value - delta) > Math.max(1e-9, Math.abs(delta) * 1e-9))
                throw new IllegalArgumentException("review_expected_delta_or_range_mismatch");
            bases.put(scoped, value); lower.put(scoped, lo); upper.put(scoped, hi);
        }
        for (Object raw : Json.array(node.get("acceptanceCriteria"))) {
            Map<String, Object> rule = Json.object(raw);
            String scoped = actionRef + ":" + text(rule.get("metric"));
            if (rule.keySet().equals(Set.of("metric", "constraint")) && "expectedRange".equals(rule.get("constraint")) && lower.containsKey(scoped)) {
                criteria.add(Map.of("metric", scoped, "constraint", "lower_guard"));
                criteria.add(Map.of("metric", scoped, "constraint", "upper_guard"));
            } else criteria.add(Map.of("metric", scoped, "constraint", "uncompiled_plan_criterion"));
        }

        // Agent2 may supply deterministic safety rules. Java never interprets prose:
        // only {metric, comparator:GTE|LTE, value:<finite number>} is compilable.
        for (Map.Entry<String, Object> entry : new TreeMap<>(Json.object(node.get("guard"))).entrySet()) {
            if (!compileSafetyRule(actionRef, "guard:" + entry.getKey(), entry.getValue(), bases, lower, upper, safetyCriteria)) {
                safetyCriteria.add(Map.of(
                    "metric", actionRef,
                    "constraint", "uncompiled_plan_safety_rule",
                    "ruleId", "guard:" + entry.getKey()
                ));
            }
        }
        int riskIndex = 0;
        for (Object raw : Json.array(node.get("riskBoundary"))) {
            String ruleId = "risk:" + (++riskIndex);
            if (!compileSafetyRule(actionRef, ruleId, raw, bases, lower, upper, safetyCriteria)) {
                safetyCriteria.add(Map.of(
                    "metric", actionRef,
                    "constraint", "uncompiled_plan_safety_rule",
                    "ruleId", ruleId
                ));
            }
        }

        Object secondsRaw = Json.object(node.get("reviewWindow")).get("durationSeconds");
        if (!(secondsRaw instanceof Number seconds) || seconds.doubleValue() != seconds.longValue() || seconds.longValue() <= 0)
            throw new IllegalArgumentException("review_duration_invalid");
        long due = Math.addExact(frozenAtMillis, Math.multiplyExact(seconds.longValue(), 1000L));
        LinkedHashMap<String, Object> headers = new LinkedHashMap<>();
        headers.put("plan.lower_guard", lower); headers.put("plan.upper_guard", upper);
        headers.put("plan.acceptance_criteria", criteria); headers.put("plan.risk_boundaries", safetyCriteria);
        headers.put("plan.review_window", seconds.longValue() + "s");
        headers.put("plan.action_ref", actionRef); headers.put("plan.node_hash", node.get("nodeHash"));
        headers.put("plan.semantic_contract_hash", expectedContractHash);
        return freeze(productId, taskId, text(graph.get("graphHash")), headers, bases, frozenAtMillis, due);
    }

    private static boolean compileSafetyRule(
        String actionRef,
        String ruleId,
        Object raw,
        Map<String, Double> baseline,
        Map<String, Double> lower,
        Map<String, Double> upper,
        List<Object> compiled
    ) {
        if (!(raw instanceof Map<?, ?> source)) return false;
        Map<String, Object> rule = stringKeyMap(source);
        if (!rule.keySet().equals(Set.of("metric", "comparator", "value"))) return false;
        String metric = text(rule.get("metric"));
        String comparator = text(rule.get("comparator")).toUpperCase();
        Double threshold = finiteNumber(rule.get("value"));
        String scoped = actionRef + ":" + metric;
        if (metric.isBlank() || threshold == null || !SUPPORTED_SAFETY_COMPARATORS.contains(comparator)
            || !baseline.containsKey(scoped)) return false;
        String constraint;
        if ("GTE".equals(comparator)) {
            lower.put(scoped, Math.max(lower.getOrDefault(scoped, threshold), threshold));
            constraint = "lower_guard";
        } else {
            upper.put(scoped, Math.min(upper.getOrDefault(scoped, threshold), threshold));
            constraint = "upper_guard";
        }
        compiled.add(Map.of(
            "metric", scoped,
            "constraint", constraint,
            "ruleId", ruleId
        ));
        return true;
    }

    /** Content verification; the caller still supplies an authority-bound Artifact and contract hash. */
    static Map<String, Map<String, Object>> semanticGraph(Map<String, Object> graph, String kind, String contractHash) {
        if (!"26.9.0".equals(graph.get("contractVersion")) || !kind.equals(graph.get("kind"))
            || !requireText(contractHash, "semantic_contract_required").equals(graph.get("contractHash")))
            throw new IllegalArgumentException("semantic_graph_contract_mismatch");
        TreeMap<String, Object> body = new TreeMap<>(graph); Object declared = body.remove("graphHash");
        if (!Hashing.canonicalHash(body).equals(declared)) throw new IllegalArgumentException("semantic_graph_hash_mismatch");
        TreeMap<String, Map<String, Object>> nodes = new TreeMap<>();
        List<Object> rawNodes = Json.array(graph.get("nodes"));
        if (rawNodes.isEmpty() || rawNodes.size() > 64 || Json.array(graph.get("edges")).size() > 256)
            throw new IllegalArgumentException("semantic_graph_budget");
        for (Object raw : rawNodes) {
            Map<String, Object> node = Json.object(raw);
            TreeMap<String, Object> material = new TreeMap<>(node); Object hash = material.remove("nodeHash");
            String key = requireText(text(node.get("nodeKey")), "semantic_node_key_required");
            if (!Hashing.canonicalHash(material).equals(hash) || nodes.putIfAbsent(key, node) != null)
                throw new IllegalArgumentException("semantic_node_hash_or_identity_invalid");
        }
        for (Object raw : Json.array(graph.get("edges"))) {
            Map<String, Object> edge = Json.object(raw);
            if (!nodes.containsKey(text(edge.get("sourceRef"))) || !nodes.containsKey(text(edge.get("targetRef"))))
                throw new IllegalArgumentException("semantic_edge_ref_invalid");
        }
        return nodes;
    }

    private static Map<String, String> trendMap(Object raw, List<String> unsupported) {
        LinkedHashMap<String, String> result = new LinkedHashMap<>();
        if (raw == null) return result;
        if (!(raw instanceof Map<?, ?> map)) {
            unsupported.add("plan.expected_trend:structured_map_required");
            return result;
        }
        for (Map.Entry<?, ?> entry : new TreeMap<>(stringKeyMap(map)).entrySet()) {
            String metric = text(entry.getKey());
            String direction = text(entry.getValue()).toUpperCase();
            if (metric.isEmpty() || direction.isEmpty()) {
                unsupported.add("plan.expected_trend:metric_and_direction_required");
                continue;
            }
            result.put(metric, direction);
        }
        return result;
    }

    private static Map<String, Double> numericMap(Object raw, String field, List<String> unsupported) {
        LinkedHashMap<String, Double> result = new LinkedHashMap<>();
        if (raw == null) return result;
        if (!(raw instanceof Map<?, ?> map)) {
            unsupported.add(field + ":structured_numeric_map_required");
            return result;
        }
        for (Map.Entry<String, Object> entry : new TreeMap<>(stringKeyMap(map)).entrySet()) {
            Double value = finiteNumber(entry.getValue());
            if (entry.getKey().isBlank() || value == null) {
                unsupported.add(field + ":finite_numeric_value_required:" + entry.getKey());
                continue;
            }
            result.put(entry.getKey(), value);
        }
        return result;
    }

    private static Map<String, Double> copyFiniteMap(
        Map<String, Double> source,
        String field,
        List<String> unsupported
    ) {
        LinkedHashMap<String, Double> result = new LinkedHashMap<>();
        if (source == null) return result;
        for (Map.Entry<String, Double> entry : new TreeMap<>(source).entrySet()) {
            Double value = finiteNumber(entry.getValue());
            if (entry.getKey() == null || entry.getKey().isBlank() || value == null) {
                unsupported.add(field + ":finite_numeric_value_required");
                continue;
            }
            result.put(entry.getKey(), value);
        }
        return result;
    }

    private static Map<String, Object> stringKeyMap(Map<?, ?> source) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : source.entrySet()) {
            result.put(String.valueOf(entry.getKey()), entry.getValue());
        }
        return result;
    }

    private static Double finiteNumber(Object raw) {
        if (!(raw instanceof Number number)) return null;
        double value = number.doubleValue();
        return Double.isFinite(value) ? value : null;
    }

    private static Object safeValue(Object value) {
        return value == null ? List.of() : value;
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static String requireText(String value, String error) {
        if (value == null || value.isBlank()) throw new IllegalArgumentException(error);
        return value.trim();
    }
}