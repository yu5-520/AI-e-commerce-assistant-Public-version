package com.zcentury.v24;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * V24.23 adapter registry for the Unified Authority Kernel.
 *
 * The registry only binds protocol domains to existing deterministic authority owners.
 * It does not transfer production write authority or replace domain implementations.
 */
public final class AuthorityAdapterRegistry {
    private static final Map<String, String> EXPECTED = expected();

    private AuthorityAdapterRegistry() {}

    public static Map<String, Object> verify(Map<String, Object> policy) {
        UnifiedAuthorityKernel.validatePolicy(policy);
        Map<String, Object> configured = objectOrEmpty(policy.get("adapters"));
        LinkedHashMap<String, Object> resolved = new LinkedHashMap<>();
        for (Map.Entry<String, String> entry : EXPECTED.entrySet()) {
            String actual = text(configured.get(entry.getKey()));
            if (!entry.getValue().equals(actual)) {
                throw new IllegalArgumentException(
                    "authority_adapter_mismatch:" + entry.getKey() + ":" + actual + ":" + entry.getValue()
                );
            }
            try {
                Class.forName(actual);
            } catch (ClassNotFoundException exc) {
                throw new IllegalStateException("authority_adapter_class_missing:" + actual, exc);
            }
            resolved.put(entry.getKey(), actual);
        }
        if (configured.size() != EXPECTED.size()) {
            throw new IllegalArgumentException("authority_adapter_registry_size_mismatch");
        }
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        out.put("schema", "v24.unified_authority.adapter_registry.v1");
        out.put("version", UnifiedAuthorityKernel.VERSION);
        out.put("adapters", resolved);
        out.put("productionAuthorityOwnershipChanged", false);
        out.put("registryHash", Hashing.canonicalHash(out));
        return out;
    }

    private static Map<String, String> expected() {
        LinkedHashMap<String, String> value = new LinkedHashMap<>();
        value.put("INFORMATION", "com.zcentury.v24.V25RetrievalAuthority");
        value.put("INVOCATION", "com.zcentury.v24.QueueAuthority");
        value.put("TEMPORAL", "com.zcentury.v24.TaskStateAuthority");
        value.put("MUTATION", "com.zcentury.v24.GateEngine");
        return Map.copyOf(value);
    }

    private static Map<String, Object> objectOrEmpty(Object value) {
        return value instanceof Map<?, ?> ? Json.object(value) : Map.of();
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }
}
