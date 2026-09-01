package com.zcentury.v24;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/**
 * V24.24 shadow generation root for the Unified Authority Kernel.
 *
 * AuthorityGenerationStore remains the only durable generation writer. Domain authorities
 * consume this root through root-bound GenerationFencer instances and cannot rotate the
 * authority generation themselves.
 */
final class UnifiedAuthorityGenerationRoot {
    static final String VERSION = "24.24.0";
    static final String SCHEMA = "v24.unified_authority_generation_root.v1";
    private static final Set<String> DOMAINS = Set.of(
        "INFORMATION", "INVOCATION", "TEMPORAL", "MUTATION"
    );

    record Identity(
        long generationSeq,
        String generationHash,
        long fencingToken,
        String stateHash,
        String mode
    ) {}

    private final AuthorityGenerationStore store;

    UnifiedAuthorityGenerationRoot(AuthorityGenerationStore store) {
        if (store == null) throw new IllegalArgumentException("authority_generation_store_required");
        this.store = store;
    }

    Identity current() {
        Map<String, Object> state = state();
        return new Identity(
            number(state.get("generationSeq")),
            text(state.get("generationHash")),
            number(state.get("fencingToken")),
            text(state.get("stateHash")),
            text(state.get("mode"))
        );
    }

    GenerationFencer consumerFence(String domain) {
        String normalized = normalizeDomain(domain);
        return GenerationFencer.consumerOf(this, normalized);
    }

    boolean matches(long generationSeq, String generationHash, long fencingToken) {
        Identity current = current();
        return current.generationSeq() == generationSeq
            && current.fencingToken() == fencingToken
            && current.generationHash().equals(generationHash);
    }

    Map<String, Object> kernelGeneration() {
        Identity current = current();
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("generationSeq", current.generationSeq());
        value.put("generationHash", current.generationHash());
        value.put("fencingToken", current.fencingToken());
        return value;
    }

    Map<String, Object> evaluate(Map<String, Object> request, Map<String, Object> policy) {
        return UnifiedAuthorityKernel.evaluate(request, policy, kernelGeneration());
    }

    Map<String, Object> status() {
        Identity current = current();
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("schema", SCHEMA);
        value.put("version", VERSION);
        value.put("enforcementMode", "SHADOW");
        value.put("source", "AuthorityGenerationStore");
        value.put("generationSeq", current.generationSeq());
        value.put("generationHash", current.generationHash());
        value.put("fencingToken", current.fencingToken());
        value.put("stateHash", current.stateHash());
        value.put("mode", current.mode());
        value.put("consumerDomains", DOMAINS.stream().sorted().toList());
        value.put("domainMayRotateGeneration", false);
        value.put("productionAuthorityOwnershipChanged", false);
        value.put("rootHash", Hashing.canonicalHash(value));
        return value;
    }

    private Map<String, Object> state() {
        try {
            return store.status();
        } catch (IOException exc) {
            throw new IllegalStateException("authority_generation_root_read_failed", exc);
        }
    }

    private static String normalizeDomain(String domain) {
        String normalized = text(domain).toUpperCase();
        if (!DOMAINS.contains(normalized)) {
            throw new IllegalArgumentException("unknown_authority_generation_consumer_domain:" + normalized);
        }
        return normalized;
    }

    private static long number(Object value) {
        if (value instanceof Number number) return number.longValue();
        try { return Long.parseLong(text(value)); }
        catch (Exception ignored) { return -1L; }
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }
}
