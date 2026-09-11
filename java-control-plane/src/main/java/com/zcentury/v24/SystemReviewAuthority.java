package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/**
 * Deterministic post-observation review evaluator.
 *
 * V26.4 established deterministic review against the immutable ReviewContract.
 * V26.6 additionally exposes structured breachedMetrics so the control plane can
 * locate the exact V26.5 Action/Operation subgraph without parsing human-readable
 * breach strings. It still never invents business thresholds or plan values.
 */
final class SystemReviewAuthority {
    enum Decision {
        WAITING_TIME,
        WAITING_EVIDENCE,
        SETTLED,
        ADJUSTMENT_REQUIRED
    }

    record Observation(
        long observedAtMillis,
        Map<String, Double> metrics,
        Map<String, Double> evidence
    ) {}

    record Result(
        Decision decision,
        String reason,
        List<String> breaches,
        List<String> breachedMetrics,
        boolean invokeAgent1,
        String contractHash,
        String reviewHash
    ) {}

    private SystemReviewAuthority() {}

    static Result evaluate(
        ReviewContractAuthority.Contract contract,
        Observation observation
    ) {
        if (contract == null) throw new IllegalArgumentException("review_contract_required");
        if (observation == null) throw new IllegalArgumentException("review_observation_required");
        if (observation.observedAtMillis() < 0L) {
            throw new IllegalArgumentException("review_observed_at_invalid");
        }
        Map<String, Double> metrics = finiteCopy(observation.metrics(), "review_metrics_invalid");
        Map<String, Double> evidence = finiteCopy(observation.evidence(), "review_evidence_invalid");

        if (observation.observedAtMillis() < contract.reviewDueAtMillis()) {
            return result(contract, observation, metrics, evidence,
                Decision.WAITING_TIME, "REVIEW_WINDOW_NOT_DUE", List.of(), List.of());
        }

        ArrayList<String> missingEvidence = new ArrayList<>();
        for (Map.Entry<String, Double> requirement : contract.minimumEvidence().entrySet()) {
            Double actual = evidence.get(requirement.getKey());
            if (actual == null || actual < requirement.getValue()) {
                missingEvidence.add(
                    requirement.getKey() + ":required=" + requirement.getValue()
                        + ":actual=" + (actual == null ? "missing" : actual)
                );
            }
        }
        if (!missingEvidence.isEmpty()) {
            return result(contract, observation, metrics, evidence,
                Decision.WAITING_EVIDENCE, "MINIMUM_EVIDENCE_NOT_MET", missingEvidence, List.of());
        }

        TreeSet<String> requiredMetrics = new TreeSet<>();
        requiredMetrics.addAll(contract.expectedTrend().keySet());
        requiredMetrics.addAll(contract.lowerGuard().keySet());
        requiredMetrics.addAll(contract.upperGuard().keySet());
        ArrayList<String> missingMetrics = new ArrayList<>();
        for (String metric : requiredMetrics) {
            if (!metrics.containsKey(metric)) missingMetrics.add(metric);
        }
        if (!missingMetrics.isEmpty()) {
            return result(contract, observation, metrics, evidence,
                Decision.WAITING_EVIDENCE, "REVIEW_METRIC_MISSING", missingMetrics, List.of());
        }

        if (!contract.deterministicSpec()) {
            return result(contract, observation, metrics, evidence,
                Decision.ADJUSTMENT_REQUIRED,
                "NON_DETERMINISTIC_REVIEW_CONTRACT",
                contract.unsupportedFields(),
                List.of());
        }

        ArrayList<String> breaches = new ArrayList<>();
        TreeSet<String> breachedMetrics = new TreeSet<>();
        for (String metric : requiredMetrics) {
            double actual = metrics.get(metric);
            Double lower = contract.lowerGuard().get(metric);
            if (lower != null && actual < lower) {
                breaches.add(metric + ":LOWER_GUARD:" + actual + "<" + lower);
                breachedMetrics.add(metric);
            }
            Double upper = contract.upperGuard().get(metric);
            if (upper != null && actual > upper) {
                breaches.add(metric + ":UPPER_GUARD:" + actual + ">" + upper);
                breachedMetrics.add(metric);
            }
            String trend = contract.expectedTrend().get(metric);
            if (trend != null && !trendSatisfied(trend, contract.baselineMetrics().get(metric), actual)) {
                breaches.add(metric + ":EXPECTED_TREND:" + trend);
                breachedMetrics.add(metric);
            }
        }

        if (!breaches.isEmpty()) {
            return result(contract, observation, metrics, evidence,
                Decision.ADJUSTMENT_REQUIRED,
                "EXPECTED_OUTCOME_NOT_MET",
                breaches,
                List.copyOf(breachedMetrics));
        }
        return result(contract, observation, metrics, evidence,
            Decision.SETTLED, "EXPECTED_OUTCOME_MET", List.of(), List.of());
    }

    private static boolean trendSatisfied(String trend, Double baseline, double actual) {
        return switch (trend) {
            case "ANY" -> true;
            case "NON_DECREASE" -> baseline != null && actual >= baseline;
            case "INCREASE" -> baseline != null && actual > baseline;
            case "NON_INCREASE" -> baseline != null && actual <= baseline;
            case "DECREASE" -> baseline != null && actual < baseline;
            default -> false;
        };
    }

    private static Result result(
        ReviewContractAuthority.Contract contract,
        Observation observation,
        Map<String, Double> metrics,
        Map<String, Double> evidence,
        Decision decision,
        String reason,
        List<String> breaches,
        List<String> breachedMetrics
    ) {
        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v26.6.system_review_result.v1");
        material.put("contractHash", contract.contractHash());
        material.put("productId", contract.productId());
        material.put("taskId", contract.taskId());
        material.put("observedAtMillis", observation.observedAtMillis());
        material.put("metrics", metrics);
        material.put("evidence", evidence);
        material.put("decision", decision.name());
        material.put("reason", reason);
        material.put("breaches", List.copyOf(breaches));
        material.put("breachedMetrics", List.copyOf(breachedMetrics));
        material.put("invokeAgent1", decision == Decision.ADJUSTMENT_REQUIRED);
        String reviewHash = Hashing.canonicalHash(material);
        return new Result(
            decision,
            reason,
            List.copyOf(breaches),
            List.copyOf(breachedMetrics),
            decision == Decision.ADJUSTMENT_REQUIRED,
            contract.contractHash(),
            reviewHash
        );
    }

    private static Map<String, Double> finiteCopy(Map<String, Double> source, String error) {
        LinkedHashMap<String, Double> result = new LinkedHashMap<>();
        if (source == null) return result;
        for (Map.Entry<String, Double> entry : source.entrySet()) {
            if (entry.getKey() == null || entry.getKey().isBlank()
                || entry.getValue() == null || !Double.isFinite(entry.getValue())) {
                throw new IllegalArgumentException(error);
            }
            result.put(entry.getKey(), entry.getValue());
        }
        return Map.copyOf(result);
    }
}
