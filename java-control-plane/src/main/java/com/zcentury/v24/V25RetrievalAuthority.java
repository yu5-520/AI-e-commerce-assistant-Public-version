package com.zcentury.v24;

import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** V25.3-V25.5 field-first retrieval authority. Providers retrieve; Java owns order, scope and admission. */
public final class V25RetrievalAuthority {
    private V25RetrievalAuthority() {}

    public static Map<String, String> verifyAliasIndex(
        Map<String, Object> aliasRegistry,
        Map<String, Map<String, Object>> fieldIndex
    ) {
        require("rag.alias_registry.v1".equals(text(aliasRegistry.get("schema"))), "alias_registry_schema_invalid");
        require("BLOCK".equals(text(aliasRegistry.get("defaultDecision"))), "alias_registry_default_must_block");
        LinkedHashMap<String, String> index = new LinkedHashMap<>();
        for (Object raw : Json.array(aliasRegistry.get("entries"))) {
            Map<String, Object> entry = Json.object(raw);
            String canonical = text(entry.get("canonicalField"));
            require(fieldIndex.containsKey(canonical), "alias_targets_unknown_field:" + canonical);
            for (String alias : V25KnowledgeRegistry.strings(entry.get("aliases"))) {
                String key = normalize(alias);
                require(!key.isBlank(), "blank_alias:" + canonical);
                String previous = index.putIfAbsent(key, canonical);
                require(previous == null || previous.equals(canonical), "ambiguous_alias:" + alias);
            }
        }
        require(!index.isEmpty(), "alias_registry_empty");
        return index;
    }

    public static Set<String> verifyStructuredKeys(Map<String, Object> contract) {
        require("rag.structured_filter_contract.v1".equals(text(contract.get("schema"))), "structured_contract_schema_invalid");
        require("BLOCK".equals(text(contract.get("defaultDecision"))), "structured_contract_default_must_block");
        HashSet<String> keys = new HashSet<>();
        for (Object raw : Json.array(contract.get("filters"))) {
            Map<String, Object> filter = Json.object(raw);
            String key = text(filter.get("key"));
            require(!key.isBlank(), "structured_filter_key_missing");
            require(keys.add(key), "duplicate_structured_filter_key:" + key);
            require(V25KnowledgeRegistry.strings(filter.get("operators")).equals(List.of("EQ")), "structured_operator_must_eq:" + key);
        }
        require(!keys.isEmpty(), "structured_filter_keys_empty");
        return keys;
    }

    public static Set<String> verifyGraphContract(Map<String, Object> contract) {
        require("rag.knowledge_graph_contract.v1".equals(text(contract.get("schema"))), "graph_contract_schema_invalid");
        require("BLOCK".equals(text(contract.get("defaultDecision"))), "graph_contract_default_must_block");
        require("SUPPLEMENT_ONLY".equals(text(contract.get("graphRole"))), "graph_role_must_supplement");
        require(Boolean.TRUE.equals(contract.get("requiresScopedVectorStage")), "graph_requires_scoped_vector_stage");
        require(Boolean.FALSE.equals(contract.get("crossDomainWideningAllowed")), "cross_domain_graph_widening_must_be_false");
        HashSet<String> edgeTypes = new HashSet<>(V25KnowledgeRegistry.strings(contract.get("allowedEdgeTypes")));
        require(!edgeTypes.isEmpty(), "graph_edge_types_empty");
        return edgeTypes;
    }

    public static void verifyPolicy(Map<String, Object> policy) {
        require("v25.phase2_retrieval_authority_policy.v1".equals(text(policy.get("schema"))), "phase2_policy_schema_invalid");
        require("SHADOW".equals(text(policy.get("enforcementMode"))), "phase2_must_start_shadow");
        Map<String, Object> p = object(policy.get("principles"));
        require(Boolean.TRUE.equals(p.get("fieldFirst")), "field_first_required");
        require(Boolean.TRUE.equals(p.get("exactFieldBeforeSemantic")), "exact_before_semantic_required");
        require(Boolean.TRUE.equals(p.get("aliasMayOnlyCanonicalizeRegisteredField")), "alias_canonical_only_required");
        require(Boolean.TRUE.equals(p.get("structuredFilterAllowlistRequired")), "structured_allowlist_required");
        require(Boolean.TRUE.equals(p.get("structuredBeforeVector")), "structured_before_vector_required");
        require(Boolean.TRUE.equals(p.get("vectorSupplementOnly")), "vector_must_be_supplement");
        require(Boolean.TRUE.equals(p.get("graphSupplementOnly")), "graph_must_be_supplement");
        require(Boolean.TRUE.equals(p.get("graphRequiresScopedVectorStage")), "graph_vector_prerequisite_required");
        require(Boolean.TRUE.equals(p.get("sourceHashRequired")), "source_hash_required");
        require(Boolean.TRUE.equals(p.get("sourceRefRequired")), "source_ref_required");
        require(Boolean.FALSE.equals(p.get("providerRoutingAuthority")), "provider_routing_authority_forbidden");
        require(Boolean.FALSE.equals(p.get("globalFallbackAllowed")), "global_fallback_forbidden");
        require(Boolean.FALSE.equals(p.get("crossDomainWideningAllowed")), "cross_domain_widening_forbidden");
        require(Boolean.TRUE.equals(p.get("insufficientEvidenceMustRemainVisible")), "insufficient_evidence_must_be_visible");
        require(Boolean.FALSE.equals(p.get("retrievalMayCreateSystemFact")), "retrieval_cannot_create_system_fact");
        require(Boolean.TRUE.equals(p.get("productionAgentInputsUnchanged")), "production_agent_boundary_required");
        require(Boolean.TRUE.equals(p.get("productionRagWriterUnchanged")), "production_writer_boundary_required");
        require(Boolean.FALSE.equals(p.get("productionRetrievalCutoverEnabled")), "production_retrieval_cutover_must_stay_off");
    }

    public static Map<String, Object> retrieve(
        Map<String, Object> request,
        Map<String, Map<String, Object>> fieldIndex,
        Map<String, String> aliasIndex,
        Set<String> structuredKeys,
        Set<String> allowedEdgeTypes,
        Map<String, Object> graphContract,
        List<Map<String, Object>> records,
        List<Map<String, Object>> vectorCandidates,
        List<Map<String, Object>> graphEdges
    ) {
        List<String> attempts = new ArrayList<>();
        String requested = text(request.get("field"));
        require(!requested.isBlank(), "retrieval_field_required");

        Map<String, Object> field = fieldIndex.get(requested);
        String resolutionMode = "DIRECT_CANONICAL_FIELD";
        if (field == null) {
            attempts.add("FIELD_DIRECT_MISS");
            String canonical = aliasIndex.get(normalize(requested));
            if (canonical == null) throw new IllegalArgumentException("unknown_rag_field_or_alias:" + requested);
            field = fieldIndex.get(canonical);
            require(field != null, "alias_resolution_target_missing:" + canonical);
            resolutionMode = "ALIAS_TO_CANONICAL_FIELD";
            attempts.add("ALIAS_CANONICALIZE");
        } else {
            attempts.add("FIELD_DIRECT_RESOLVE");
        }

        String canonicalField = text(field.get("canonicalField"));
        Map<String, Object> filters = object(request.get("filters"));
        for (String key : filters.keySet()) {
            if (!structuredKeys.contains(key)) throw new IllegalArgumentException("unknown_structured_filter:" + key);
        }

        List<Map<String, Object>> bucket = matchingRecords(records, field);
        attempts.add("EXACT_FIELD");
        if (filters.isEmpty() && !bucket.isEmpty()) {
            return result("PASS", "EXACT_FIELD", "REGISTERED_EXACT", true, false, resolutionMode, field, List.of(bucket.get(0)), attempts);
        }

        attempts.add("STRUCTURED_FILTER");
        List<Map<String, Object>> structured = structuredMatch(bucket, filters);
        if (!filters.isEmpty() && !structured.isEmpty()) {
            return result("PASS", "STRUCTURED_FILTER", "REGISTERED_STRUCTURED", true, false, resolutionMode, field, List.of(structured.get(0)), attempts);
        }

        List<String> preferred = V25KnowledgeRegistry.strings(field.get("preferredRetrieval"));
        boolean vectorEligible = preferred.contains("VECTOR");
        boolean vectorStageComplete = false;
        if (vectorEligible) {
            attempts.add("VECTOR_SUPPLEMENT");
            vectorStageComplete = true;
            List<Map<String, Object>> acceptedVector = matchingVectorCandidates(vectorCandidates, field);
            if (!acceptedVector.isEmpty()) {
                return result("PASS", "VECTOR_SUPPLEMENT", "SUPPLEMENTAL", true, true, resolutionMode, field, List.of(acceptedVector.get(0)), attempts);
            }
        }

        boolean relationshipRequired = Boolean.TRUE.equals(request.get("relationshipRequired"));
        if (relationshipRequired && preferred.contains("GRAPH")) {
            require(Boolean.TRUE.equals(graphContract.get("requiresScopedVectorStage")), "graph_vector_stage_contract_missing");
            require(vectorStageComplete, "graph_before_scoped_vector_stage_forbidden");
            attempts.add("GRAPH_SUPPLEMENT");
            List<Map<String, Object>> graphMatches = graphMatch(
                canonicalField, field, fieldIndex, allowedEdgeTypes, graphContract, graphEdges, records
            );
            if (!graphMatches.isEmpty()) {
                return result("PASS", "GRAPH_SUPPLEMENT", "SUPPLEMENTAL", true, true, resolutionMode, field, List.of(graphMatches.get(0)), attempts);
            }
        }

        return result("INSUFFICIENT", "INSUFFICIENT_EVIDENCE", "NONE", false, false, resolutionMode, field, List.of(), attempts);
    }

    private static List<Map<String, Object>> matchingRecords(List<Map<String, Object>> records, Map<String, Object> field) {
        List<Map<String, Object>> result = new ArrayList<>();
        String canonical = text(field.get("canonicalField"));
        for (Map<String, Object> record : records) {
            if (!canonical.equals(text(record.get("canonicalField")))) continue;
            validateRecord(record, field);
            result.add(record);
        }
        result.sort(Comparator.comparing(item -> text(item.get("recordId"))));
        return result;
    }

    private static List<Map<String, Object>> structuredMatch(List<Map<String, Object>> records, Map<String, Object> filters) {
        if (filters.isEmpty()) return List.of();
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, Object> record : records) {
            Map<String, Object> attributes = object(record.get("attributes"));
            boolean matches = true;
            for (Map.Entry<String, Object> filter : filters.entrySet()) {
                if (!normalize(attributes.get(filter.getKey())).equals(normalize(filter.getValue()))) {
                    matches = false;
                    break;
                }
            }
            if (matches) result.add(record);
        }
        return result;
    }

    private static List<Map<String, Object>> matchingVectorCandidates(
        List<Map<String, Object>> candidates,
        Map<String, Object> field
    ) {
        List<Map<String, Object>> result = new ArrayList<>();
        String canonical = text(field.get("canonicalField"));
        for (Map<String, Object> candidate : candidates) {
            if (!canonical.equals(text(candidate.get("canonicalField")))) continue;
            validateRecord(candidate, field);
            require(Boolean.TRUE.equals(candidate.get("routeProofAccepted")), "vector_candidate_route_proof_missing:" + text(candidate.get("recordId")));
            require(!text(candidate.get("routeHash")).isBlank(), "vector_candidate_route_hash_missing:" + text(candidate.get("recordId")));
            result.add(candidate);
        }
        result.sort((left, right) -> {
            int score = Double.compare(number(right.get("score")), number(left.get("score")));
            return score != 0 ? score : text(left.get("recordId")).compareTo(text(right.get("recordId")));
        });
        return result;
    }

    private static List<Map<String, Object>> graphMatch(
        String canonicalField,
        Map<String, Object> field,
        Map<String, Map<String, Object>> fieldIndex,
        Set<String> allowedEdgeTypes,
        Map<String, Object> graphContract,
        List<Map<String, Object>> edges,
        List<Map<String, Object>> records
    ) {
        List<String> requestDomains = V25KnowledgeRegistry.strings(field.get("domains"));
        List<String> forbiddenPrefixes = V25KnowledgeRegistry.strings(graphContract.get("forbiddenTargetPrefixes"));
        for (Map<String, Object> edge : edges) {
            if (!canonicalField.equals(text(edge.get("fromCanonicalField")))) continue;
            String edgeType = text(edge.get("edgeType"));
            require(allowedEdgeTypes.contains(edgeType), "graph_edge_type_forbidden:" + edgeType);
            String target = text(edge.get("toCanonicalField"));
            for (String prefix : forbiddenPrefixes) {
                require(!target.startsWith(prefix), "graph_target_system_contract_forbidden:" + target);
            }
            Map<String, Object> targetField = fieldIndex.get(target);
            require(targetField != null, "graph_target_unknown_rag_field:" + target);
            List<String> targetDomains = V25KnowledgeRegistry.strings(targetField.get("domains"));
            require(overlap(requestDomains, targetDomains), "graph_cross_domain_widening_forbidden:" + target);
            List<Map<String, Object>> targetRecords = matchingRecords(records, targetField);
            if (!targetRecords.isEmpty()) return targetRecords;
        }
        return List.of();
    }

    private static void validateRecord(Map<String, Object> record, Map<String, Object> field) {
        String id = text(record.get("recordId"));
        require(!id.isBlank(), "knowledge_record_id_missing");
        require(text(field.get("fieldHash")).equals(text(record.get("fieldHash"))), "knowledge_record_field_hash_mismatch:" + id);
        require(overlap(V25KnowledgeRegistry.strings(field.get("domains")), V25KnowledgeRegistry.strings(record.get("domains"))), "knowledge_record_domain_mismatch:" + id);
        require(!text(record.get("sourceRef")).isBlank(), "knowledge_record_source_ref_missing:" + id);
        require(isSha256(text(record.get("sourceHash"))), "knowledge_record_source_hash_invalid:" + id);
    }

    private static Map<String, Object> result(
        String decision,
        String retrievalMode,
        String matchClass,
        boolean sufficient,
        boolean supplemental,
        String resolutionMode,
        Map<String, Object> field,
        List<Map<String, Object>> matches,
        List<String> attempts
    ) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("schema", "rag.field_first_retrieval_result.v1");
        result.put("decision", decision);
        result.put("retrievalMode", retrievalMode);
        result.put("knowledgeMatchClass", matchClass);
        result.put("retrievalSufficient", sufficient);
        result.put("supplemental", supplemental);
        result.put("mayCreateSystemFact", false);
        result.put("resolutionMode", resolutionMode);
        result.put("fieldId", field.get("fieldId"));
        result.put("fieldHash", field.get("fieldHash"));
        result.put("canonicalField", field.get("canonicalField"));
        result.put("domains", field.get("domains"));
        result.put("matches", matches);
        result.put("attemptedLayers", attempts);
        result.put("resultHash", Hashing.canonicalHash(result));
        return result;
    }

    private static boolean overlap(List<String> left, List<String> right) {
        for (String item : left) if (right.contains(item)) return true;
        return false;
    }

    private static boolean isSha256(String value) {
        return value.matches("sha256:[0-9a-fA-F]{64}");
    }

    private static double number(Object value) {
        if (value instanceof Number n) return n.doubleValue();
        try { return Double.parseDouble(text(value)); } catch (Exception ignored) { return 0.0d; }
    }

    private static String normalize(Object value) {
        return Normalizer.normalize(text(value), Normalizer.Form.NFKC)
            .toLowerCase(Locale.ROOT)
            .replaceAll("\\s+", " ")
            .trim();
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
