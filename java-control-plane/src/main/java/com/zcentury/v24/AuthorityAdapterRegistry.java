package com.zcentury.v24;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * V24.23-V24.25 adapter registry for the Unified Authority Kernel.
 *
 * V24.23 verifies protocol-domain -> deterministic implementation registration.
 * V24.25 adds a shadow runtime binding layer: every registered implementation must be
 * consumed through one RootBoundAuthorityAdapter backed by UnifiedAuthorityGenerationRoot.
 * This still does not transfer production write authority or replace domain implementations.
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

    static Map<String, RootBoundAuthorityAdapter> bind(
        Map<String, Object> policy,
        UnifiedAuthorityGenerationRoot root
    ) {
        verify(policy);
        if (root == null) throw new IllegalArgumentException("unified_authority_generation_root_required");
        LinkedHashMap<String, RootBoundAuthorityAdapter> bound = new LinkedHashMap<>();
        for (Map.Entry<String, String> entry : EXPECTED.entrySet()) {
            bound.put(
                entry.getKey(),
                new RootBoundAuthorityAdapter(entry.getKey(), entry.getValue(), root)
            );
        }
        return bound;
    }

    static Map<String, Object> bindingReport(
        Map<String, Object> policy,
        UnifiedAuthorityGenerationRoot root
    ) {
        Map<String, RootBoundAuthorityAdapter> bound = bind(policy, root);
        LinkedHashMap<String, Object> receipts = new LinkedHashMap<>();
        for (Map.Entry<String, RootBoundAuthorityAdapter> entry : bound.entrySet()) {
            receipts.put(entry.getKey(), entry.getValue().bindingReceipt());
        }
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        out.put("schema", "v24.unified_authority.root_bound_adapter_registry.v1");
        out.put("version", RootBoundAuthorityAdapter.VERSION);
        out.put("enforcementMode", "SHADOW");
        out.put("rootSource", "AuthorityGenerationStore");
        out.put("rootStatus", root.status());
        out.put("bindings", receipts);
        out.put("bindingCount", receipts.size());
        out.put("allDomainsRootBound", receipts.size() == EXPECTED.size());
        out.put("domainMayRotateGeneration", false);
        out.put("productionAuthorityOwnershipChanged", false);
        out.put("authorityGrantCreated", false);
        out.put("bindingRegistryHash", Hashing.canonicalHash(out));
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
