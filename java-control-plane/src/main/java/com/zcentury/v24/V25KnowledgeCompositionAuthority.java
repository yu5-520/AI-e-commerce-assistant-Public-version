package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** V25.6 deterministic Knowledge Composition Table authority. */
public final class V25KnowledgeCompositionAuthority {
    private static final Set<String> ALLOWED_OPS = Set.of("EQ", "IN", "CONTAINS", "TRUTHY");

    private V25KnowledgeCompositionAuthority() {}

    public static Map<String, Map<String, Object>> verifyAndIndex(
        Map<String, Object> table,
        Map<String, Map<String, Object>> fieldIndex
    ) {
        require("rag.knowledge_composition_table.v1".equals(text(table.get("schema"))), "composition_table_schema_invalid");
        require("BLOCK".equals(text(table.get("defaultDecision"))), "composition_table_default_must_block");
        require(
            new java.util.HashSet<>(V25KnowledgeRegistry.strings(table.get("allowedPredicateOps"))).equals(ALLOWED_OPS),
            "composition_predicate_ops_invalid"
        );

        LinkedHashMap<String, Map<String, Object>> index = new LinkedHashMap<>();
        for (Object raw : Json.array(table.get("compositions"))) {
            Map<String, Object> composition = Json.object(raw);
            String compositionId = text(composition.get("compositionId"));
            String agent = text(composition.get("agent"));
            String stage = text(composition.get("stage"));
            require(!compositionId.isBlank(), "composition_id_missing");
            require(!agent.isBlank(), "composition_agent_missing:" + compositionId);
            require(!stage.isBlank(), "composition_stage_missing:" + compositionId);
            require(!index.containsKey(agent), "duplicate_agent_composition:" + agent);

            verifyRefs(agent, Json.array(composition.get("baseFields")), fieldIndex, compositionId + ":BASE");
            for (Object groupRaw : Json.array(composition.get("conditionalGroups"))) {
                Map<String, Object> group = Json.object(groupRaw);
                String groupId = text(group.get("groupId"));
                require(!groupId.isBlank(), "composition_group_id_missing:" + compositionId);
                Map<String, Object> predicate = object(group.get("when"));
                String op = text(predicate.get("op"));
                require(ALLOWED_OPS.contains(op), "unsupported_composition_predicate:" + op);
                require(!text(predicate.get("path")).isBlank(), "composition_predicate_path_missing:" + groupId);
                if (!"TRUTHY".equals(op)) {
                    require(predicate.containsKey("value"), "composition_predicate_value_missing:" + groupId);
                }
                verifyRefs(agent, Json.array(group.get("fields")), fieldIndex, compositionId + ":" + groupId);
            }
            index.put(agent, composition);
        }
        require(index.keySet().equals(Set.of("Agent1", "Agent2", "Agent3")), "composition_agents_must_be_exact_agent1_agent2_agent3");
        return index;
    }

    public static void verifyPolicy(Map<String, Object> policy) {
        require(
            "v25.phase3_agent_knowledge_migration_policy.v1".equals(text(policy.get("schema"))),
            "phase3_policy_schema_invalid"
        );
        require(
            "PRODUCTION_KNOWLEDGE_INGRESS".equals(text(policy.get("enforcementMode"))),
            "phase3_enforcement_mode_invalid"
        );
        Map<String, Object> principles = object(policy.get("principles"));
        require(Boolean.TRUE.equals(principles.get("compositionTableRequired")), "composition_table_required");
        require(Boolean.TRUE.equals(principles.get("registeredFieldHashRequired")), "registered_field_hash_required");
        require(Boolean.TRUE.equals(principles.get("consumerAuthorizationRequired")), "consumer_authorization_required");
        require(Boolean.TRUE.equals(principles.get("deterministicPredicateOnly")), "deterministic_predicate_required");
        require(Boolean.FALSE.equals(principles.get("legacyDirectAgentKnowledgeRead")), "legacy_direct_knowledge_read_must_be_false");
        require(Boolean.TRUE.equals(principles.get("legacyProviderBehindUnifiedAdapter")), "legacy_provider_adapter_required");
        require(Boolean.FALSE.equals(principles.get("physicalRagProviderCutover")), "physical_provider_cutover_must_be_false");
        require(Boolean.FALSE.equals(principles.get("retrievalMayCreateSystemFact")), "knowledge_may_not_create_system_fact");
        require(Boolean.TRUE.equals(principles.get("insufficientEvidenceMustRemainVisible")), "knowledge_gaps_must_remain_visible");
        require(Boolean.FALSE.equals(principles.get("agent1LegacyExperienceDirectReadAllowed")), "agent1_legacy_experience_must_be_removed");
        require(Boolean.TRUE.equals(principles.get("agent2LegacyExperienceProviderAllowedBehindAdapter")), "agent2_legacy_provider_adapter_required");
        require(Boolean.FALSE.equals(principles.get("agent3SemanticRuntimeCreated")), "new_agent3_runtime_forbidden");
        require(Boolean.TRUE.equals(principles.get("currentSopRuntimeUnchanged")), "current_sop_runtime_must_remain");
    }

    public static Map<String, Object> compose(
        String agent,
        Map<String, Object> context,
        Map<String, Map<String, Object>> compositionIndex,
        Map<String, Map<String, Object>> fieldIndex,
        String tableVersion
    ) {
        Map<String, Object> composition = compositionIndex.get(agent);
        if (composition == null) throw new IllegalArgumentException("unknown_knowledge_composition_agent:" + agent);

        LinkedHashMap<String, Map<String, Object>> selected = new LinkedHashMap<>();
        LinkedHashMap<String, List<String>> reasons = new LinkedHashMap<>();

        for (Object raw : Json.array(composition.get("baseFields"))) {
            add(agent, Json.object(raw), "BASE", selected, reasons, fieldIndex);
        }

        List<String> matchedGroups = new ArrayList<>();
        for (Object raw : Json.array(composition.get("conditionalGroups"))) {
            Map<String, Object> group = Json.object(raw);
            if (!matches(object(group.get("when")), context)) continue;
            String groupId = text(group.get("groupId"));
            matchedGroups.add(groupId);
            for (Object ref : Json.array(group.get("fields"))) {
                add(agent, Json.object(ref), groupId, selected, reasons, fieldIndex);
            }
        }

        List<Object> fields = new ArrayList<>();
        for (Map.Entry<String, Map<String, Object>> entry : selected.entrySet()) {
            LinkedHashMap<String, Object> projection = new LinkedHashMap<>(entry.getValue());
            projection.put("selectionReasons", reasons.get(entry.getKey()));
            fields.add(projection);
        }

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "rag.knowledge_composition_plan.v1");
        material.put("version", "25.9.0");
        material.put("compositionId", composition.get("compositionId"));
        material.put("compositionVersion", tableVersion);
        material.put("agent", agent);
        material.put("stage", composition.get("stage"));
        material.put("matchedConditionalGroups", matchedGroups);
        material.put("fields", fields);
        material.put("mayCreateSystemFact", false);

        LinkedHashMap<String, Object> result = new LinkedHashMap<>(material);
        result.put("compositionHash", Hashing.canonicalHash(material));
        return result;
    }

    private static void verifyRefs(
        String agent,
        List<Object> refs,
        Map<String, Map<String, Object>> fieldIndex,
        String source
    ) {
        require(!refs.isEmpty(), "composition_field_refs_empty:" + source);
        java.util.HashSet<String> seen = new java.util.HashSet<>();
        for (Object raw : refs) {
            Map<String, Object> ref = Json.object(raw);
            String canonical = text(ref.get("canonicalField"));
            require(seen.add(canonical), "duplicate_composition_field:" + source + ":" + canonical);
            Map<String, Object> field = fieldIndex.get(canonical);
            require(field != null, "composition_unknown_field:" + canonical);
            require(
                text(field.get("fieldHash")).equals(text(ref.get("fieldHash"))),
                "composition_field_hash_mismatch:" + canonical
            );
            require(
                V25KnowledgeRegistry.strings(field.get("consumers")).contains(agent),
                "composition_consumer_forbidden:" + agent + ":" + canonical
            );
            String role = text(ref.get("role"));
            require("REQUIRED".equals(role) || "OPTIONAL".equals(role), "composition_field_role_invalid:" + canonical);
        }
    }

    private static void add(
        String agent,
        Map<String, Object> ref,
        String reason,
        LinkedHashMap<String, Map<String, Object>> selected,
        LinkedHashMap<String, List<String>> reasons,
        Map<String, Map<String, Object>> fieldIndex
    ) {
        String canonical = text(ref.get("canonicalField"));
        Map<String, Object> field = fieldIndex.get(canonical);
        require(field != null, "composition_unknown_field:" + canonical);
        require(text(field.get("fieldHash")).equals(text(ref.get("fieldHash"))), "composition_field_hash_mismatch:" + canonical);
        require(V25KnowledgeRegistry.strings(field.get("consumers")).contains(agent), "composition_consumer_forbidden:" + agent + ":" + canonical);

        if (!selected.containsKey(canonical)) {
            LinkedHashMap<String, Object> projection = new LinkedHashMap<>();
            projection.put("canonicalField", canonical);
            projection.put("fieldHash", field.get("fieldHash"));
            projection.put("domains", field.get("domains"));
            projection.put("preferredRetrieval", field.get("preferredRetrieval"));
            projection.put("role", ref.get("role"));
            selected.put(canonical, projection);
        }
        reasons.computeIfAbsent(canonical, ignored -> new ArrayList<>()).add(reason);
    }

    private static boolean matches(Map<String, Object> predicate, Map<String, Object> context) {
        String op = text(predicate.get("op"));
        Object actual = pathValue(context, text(predicate.get("path")));
        Object expected = predicate.get("value");
        return switch (op) {
            case "EQ" -> java.util.Objects.equals(actual, expected);
            case "IN" -> Json.array(expected).contains(actual);
            case "CONTAINS" -> {
                if (actual instanceof List<?>) {
                    yield ((List<?>) actual).contains(expected);
                }
                yield text(actual).toLowerCase(java.util.Locale.ROOT)
                    .contains(text(expected).toLowerCase(java.util.Locale.ROOT));
            }
            case "TRUTHY" -> truthy(actual);
            default -> throw new IllegalArgumentException("unsupported_composition_predicate:" + op);
        };
    }

    private static Object pathValue(Map<String, Object> context, String path) {
        Object current = context;
        for (String part : path.split("\\.")) {
            if (!(current instanceof Map<?, ?>)) return null;
            current = Json.object(current).get(part);
        }
        return current;
    }

    private static boolean truthy(Object value) {
        if (value == null) return false;
        if (value instanceof Boolean b) return b;
        if (value instanceof Number n) return n.doubleValue() != 0.0d;
        if (value instanceof List<?> list) return !list.isEmpty();
        if (value instanceof Map<?, ?> map) return !map.isEmpty();
        return !text(value).isBlank();
    }

    private static Map<String, Object> object(Object value) {
        return value instanceof Map<?, ?> ? Json.object(value) : Map.of();
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
