package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/**
 * V26.4 deterministic review evaluator.
 *
 * It never invents business thresholds. It compares observed facts only against the
 * immutable ReviewContract frozen from Agent2 plan authority. Ambiguous or unsupported
 * expectations fail closed into ADJUSTMENT_REQUIRED so Agent1 may interpret meaning.
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
                Decision.WAITING_TIME, "REVIEW_WINDOW_NOT_DUE", List.of());
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
                Decision.WAITING_EVIDENCE, "MINIMUM_EVIDENCE_NOT_MET", missingEvidence);
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
                Decision.WAITING_EVIDENCE, "REVIEW_METRIC_MISSING", missingMetrics);
        }

        if (!contract.deterministicSpec()) {
            return result(contract, observation, metrics, evidence,
                Decision.ADJUSTMENT_REQUIRED,
                "NON_DETERMINISTIC_REVIEW_CONTRACT",
                contract.unsupportedFields());
        }

        ArrayList<String> breaches = new ArrayList<>();
        for (String metric : requiredMetrics) {
            double actual = metrics.get(metric);
            Double lower = contract.lowerGuard().get(metric);
            if (lower != null && actual < lower) {
                breaches.add(metric + ":LOWER_GUARD:" + actual + "<" + lower);
            }
            Double upper = contract.upperGuard().get(metric);
            if (upper != null && actual > upper) {
                breaches.add(metric + ":UPPER_GUARD:" + actual + ">" + upper);
            }
            String trend = contract.expectedTrend().get(metric);
            if (trend != null && !trendSatisfied(trend, contract.baselineMetrics().get(metric), actual)) {
                breaches.add(metric + ":EXPECTED_TREND:" + trend);
            }
        }

        if (!breaches.isEmpty()) {
            return result(contract, observation, metrics, evidence,
                Decision.ADJUSTMENT_REQUIRED, "EXPECTED_OUTCOME_NOT_MET", breaches);
        }
        return result(contract, observation, metrics, evidence,
            Decision.SETTLED, "EXPECTED_OUTCOME_MET", List.of());
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
        List<String> breaches
    ) {
        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v26.4.system_review_result.v1");
        material.put("contractHash", contract.contractHash());
        material.put("productId", contract.productId());
        material.put("taskId", contract.taskId());
        material.put("observedAtMillis", observation.observedAtMillis());
        material.put("metrics", metrics);
        material.put("evidence", evidence);
        material.put("decision", decision.name());
        material.put("reason", reason);
        material.put("breaches", List.copyOf(breaches));
        material.put("invokeAgent1", decision == Decision.ADJUSTMENT_REQUIRED);
        String reviewHash = Hashing.canonicalHash(material);
        return new Result(
            decision,
            reason,
            List.copyOf(breaches),
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