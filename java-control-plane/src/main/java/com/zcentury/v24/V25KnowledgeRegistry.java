package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** V25.1 unified RAG field registry verifier and field-to-domain resolver. */
public final class V25KnowledgeRegistry {
    private V25KnowledgeRegistry() {}

    public static Map<String, Map<String, Object>> verifyAndIndex(
        Map<String, Object> registry,
        Map<String, Map<String, Object>> domainIndex
    ) {
        require("rag.unified_field_registry.v1".equals(text(registry.get("schema"))), "field_registry_schema_invalid");
        require("BLOCK".equals(text(registry.get("defaultDecision"))), "field_registry_default_must_block");
        require("rag.field.identity.v1".equals(text(registry.get("fieldIdentitySchema"))), "field_identity_schema_invalid");

        LinkedHashMap<String, Map<String, Object>> index = new LinkedHashMap<>();
        Set<String> ids = new HashSet<>();
        Set<String> hashes = new HashSet<>();
        List<String> exclusions = strings(registry.get("systemContractExclusions"));

        for (Object raw : Json.array(registry.get("fields"))) {
            Map<String, Object> field = Json.object(raw);
            String fieldId = text(field.get("fieldId"));
            String canonicalField = text(field.get("canonicalField"));
            String valueType = text(field.get("valueType"));
            String declaredHash = text(field.get("fieldHash"));
            List<String> domains = strings(field.get("domains"));
            require(!fieldId.isBlank(), "field_id_missing");
            require(!canonicalField.isBlank(), "canonical_field_missing:" + fieldId);
            require(!valueType.isBlank(), "field_value_type_missing:" + fieldId);
            require(!domains.isEmpty(), "field_domain_missing:" + fieldId);
            require(ids.add(fieldId), "duplicate_field_id:" + fieldId);
            require(!index.containsKey(canonicalField), "duplicate_canonical_field:" + canonicalField);
            require(hashes.add(declaredHash), "duplicate_field_hash:" + declaredHash);
            require(!matchesExcludedContract(canonicalField, exclusions), "system_contract_leaked_into_rag:" + canonicalField);

            List<String> sortedDomains = new ArrayList<>(domains);
            sortedDomains.sort(String::compareTo);
            LinkedHashMap<String, Object> material = new LinkedHashMap<>();
            material.put("schema", "rag.field.identity.v1");
            material.put("fieldId", fieldId);
            material.put("canonicalField", canonicalField);
            material.put("valueType", valueType);
            material.put("domains", sortedDomains);
            String computed = Hashing.canonicalHash(material);
            require(computed.equals(declaredHash), "field_hash_mismatch:" + canonicalField);

            for (String domainId : domains) {
                V25KnowledgeDomainAuthority.resolve(domainId, domainIndex);
            }
            index.put(canonicalField, field);
        }
        require(!index.isEmpty(), "rag_fields_empty");
        return index;
    }

    public static Map<String, Object> resolve(
        String canonicalField,
        Map<String, Map<String, Object>> fieldIndex,
        Map<String, Map<String, Object>> domainIndex
    ) {
        Map<String, Object> field = fieldIndex.get(canonicalField);
        if (field == null) throw new IllegalArgumentException("unknown_rag_field:" + canonicalField);
        List<Map<String, Object>> resolved = new ArrayList<>();
        for (String domainId : strings(field.get("domains"))) {
            Map<String, Object> domain = V25KnowledgeDomainAuthority.resolve(domainId, domainIndex);
            LinkedHashMap<String, Object> projection = new LinkedHashMap<>();
            projection.put("domainId", domain.get("domainId"));
            projection.put("domainHash", domain.get("domainHash"));
            projection.put("nameZh", domain.get("nameZh"));
            projection.put("domainType", domain.get("domainType"));
            resolved.add(projection);
        }
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("fieldId", field.get("fieldId"));
        result.put("fieldHash", field.get("fieldHash"));
        result.put("canonicalField", field.get("canonicalField"));
        result.put("nameZh", field.get("nameZh"));
        result.put("consumers", field.get("consumers"));
        result.put("preferredRetrieval", field.get("preferredRetrieval"));
        result.put("domains", resolved);
        result.put("resolutionMode", "FIELD_REGISTRY_TO_DISTRIBUTION_DOMAIN");
        return result;
    }

    private static boolean matchesExcludedContract(String canonicalField, List<String> exclusions) {
        for (String pattern : exclusions) {
            String prefix = pattern.endsWith("*") ? pattern.substring(0, pattern.length() - 1) : pattern;
            if (!prefix.isBlank() && canonicalField.startsWith(prefix)) return true;
        }
        return false;
    }

    static List<String> strings(Object value) {
        List<String> result = new ArrayList<>();
        if (value instanceof List<?> list) {
            for (Object item : list) {
                String text = text(item);
                if (!text.isBlank()) result.add(text);
            }
        } else {
            String text = text(value);
            if (!text.isBlank()) result.add(text);
        }
        return result;
    }

    static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
