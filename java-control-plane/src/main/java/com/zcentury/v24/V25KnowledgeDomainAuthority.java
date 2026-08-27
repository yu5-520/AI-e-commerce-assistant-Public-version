package com.zcentury.v24;

import java.util.LinkedHashMap;
import java.util.Map;

/** V25.2 knowledge distribution-domain identity and lookup authority. */
public final class V25KnowledgeDomainAuthority {
    private V25KnowledgeDomainAuthority() {}

    public static Map<String, Map<String, Object>> verifyAndIndex(Map<String, Object> registry) {
        require("rag.knowledge_distribution_domains.v1".equals(text(registry.get("schema"))), "domain_schema_invalid");
        require("BLOCK".equals(text(registry.get("defaultDecision"))), "domain_default_must_block");
        LinkedHashMap<String, Map<String, Object>> index = new LinkedHashMap<>();
        for (Object raw : Json.array(registry.get("domains"))) {
            Map<String, Object> domain = Json.object(raw);
            String domainId = text(domain.get("domainId"));
            String declaredHash = text(domain.get("domainHash"));
            require(!domainId.isBlank(), "domain_id_missing");
            require(!index.containsKey(domainId), "duplicate_domain_id:" + domainId);
            LinkedHashMap<String, Object> material = new LinkedHashMap<>();
            material.put("schema", "rag.domain.identity.v1");
            material.put("domainId", domainId);
            material.put("domainType", domain.get("domainType"));
            material.put("scope", domain.get("scope"));
            String computed = Hashing.canonicalHash(material);
            require(computed.equals(declaredHash), "domain_hash_mismatch:" + domainId);
            index.put(domainId, domain);
        }
        require(!index.isEmpty(), "knowledge_domains_empty");
        return index;
    }

    public static Map<String, Object> resolve(
        String domainId,
        Map<String, Map<String, Object>> domainIndex
    ) {
        Map<String, Object> domain = domainIndex.get(domainId);
        if (domain == null) throw new IllegalArgumentException("unknown_knowledge_domain:" + domainId);
        return domain;
    }

    static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
