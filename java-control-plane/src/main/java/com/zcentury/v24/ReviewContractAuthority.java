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