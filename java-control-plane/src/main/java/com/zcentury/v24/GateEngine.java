package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/** V24.7 deterministic fail-closed gate engine. */
final class GateEngine {
    private final Map<String, Map<String, Object>> definitions = new LinkedHashMap<>();
    private final String defaultDecision;

    GateEngine(Map<String, Object> root) {
        this.defaultDecision = text(root.getOrDefault("defaultDecision", "BLOCK"));
        for (Object raw : Json.array(root.get("gates"))) {
            Map<String, Object> gate = Json.object(raw);
            String id = text(gate.get("gateId"));
            if (id.isBlank()) throw new IllegalArgumentException("gate_id_missing");
            if (definitions.put(id, gate) != null) throw new IllegalArgumentException("duplicate_gate:" + id);
        }
    }

    Map<String, Object> evaluate(String gateId, Map<String, Object> input) {
        Map<String, Object> gate = definitions.get(gateId);
        if (gate == null) return decision(gateId, defaultDecision, false, List.of("UNKNOWN_GATE"), input);
        List<String> failures = new ArrayList<>();
        for (Object raw : Json.array(gate.get("predicates"))) {
            Map<String, Object> predicate = Json.object(raw);
            if (!matches(input, predicate)) {
                failures.add(text(predicate.get("path")) + ":" + text(predicate.get("op")));
            }
        }
        boolean passed = failures.isEmpty();
        String decision = text(gate.getOrDefault(passed ? "passDecision" : "failDecision", passed ? "PASS" : "BLOCK"));
        return decision(gateId, decision, passed, failures, input);
    }

    private static Map<String, Object> decision(String gateId, String decision, boolean passed, List<String> failures, Map<String, Object> input) {
        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("gateId", gateId);
        material.put("decision", decision);
        material.put("passed", passed);
        material.put("failures", failures);
        material.put("inputHash", Hashing.canonicalHash(input));
        String hash = Hashing.canonicalHash(material);
        LinkedHashMap<String, Object> result = new LinkedHashMap<>(material);
        result.put("gateDecisionHash", hash);
        return result;
    }

    private static boolean matches(Map<String, Object> input, Map<String, Object> predicate) {
        String path = text(predicate.get("path"));
        String op = text(predicate.get("op"));
        Object actual = valueAt(input, path);
        Object expected = predicate.get("value");
        return switch (op) {
            case "equals" -> equivalent(actual, expected);
            case "nonBlank" -> actual != null && !text(actual).isBlank();
            case "prefix" -> actual != null && text(actual).startsWith(text(expected));
            case "sameAs" -> equivalent(actual, valueAt(input, text(expected)));
            case "equalsPath" -> equivalent(actual, valueAt(input, text(expected)));
            default -> false;
        };
    }

    private static Object valueAt(Map<String, Object> input, String path) {
        Object current = input;
        for (String part : path.split("\\.")) {
            if (!(current instanceof Map<?, ?>)) return null;
            current = Json.object(current).get(part);
        }
        return current;
    }

    private static boolean equivalent(Object left, Object right) {
        if (Objects.equals(left, right)) return true;
        if (left == null || right == null) return false;
        if (left instanceof Number && right instanceof Number) return text(left).equals(text(right));
        return false;
    }

    private static String text(Object value) { return value == null ? "" : String.valueOf(value); }
}
