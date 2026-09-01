package com.zcentury.v24;

import java.util.LinkedHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Generation/fencing helper used by queue-domain consumers.
 *
 * Legacy shadow callers may still use the local constructor. V24.24 adds a root-bound
 * consumer mode in which current/fence/matches are sourced from UnifiedAuthorityGenerationRoot
 * and local rotate is forbidden.
 */
final class GenerationFencer {
    record Snapshot(long generationSeq, String generationHash, long fencingToken) {}
    record Fence(long generationSeq, String generationHash, long fencingToken) {}

    private final AtomicLong generationSeq;
    private final AtomicLong fencingToken;
    private final UnifiedAuthorityGenerationRoot root;
    private final String consumerDomain;
    private volatile Snapshot current;

    GenerationFencer() {
        this.generationSeq = new AtomicLong(1L);
        this.fencingToken = new AtomicLong(1L);
        this.root = null;
        this.consumerDomain = "LEGACY_SHADOW";
        this.current = snapshot(1L, 1L, "initial");
    }

    private GenerationFencer(UnifiedAuthorityGenerationRoot root, String consumerDomain) {
        if (root == null) throw new IllegalArgumentException("unified_authority_generation_root_required");
        this.generationSeq = new AtomicLong(0L);
        this.fencingToken = new AtomicLong(0L);
        this.root = root;
        this.consumerDomain = consumerDomain;
        this.current = null;
    }

    static GenerationFencer consumerOf(UnifiedAuthorityGenerationRoot root, String consumerDomain) {
        return new GenerationFencer(root, consumerDomain);
    }

    Snapshot current() {
        if (root != null) {
            UnifiedAuthorityGenerationRoot.Identity value = root.current();
            return new Snapshot(value.generationSeq(), value.generationHash(), value.fencingToken());
        }
        return current;
    }

    synchronized Snapshot rotate(String reason) {
        if (root != null) {
            throw new IllegalStateException(
                "generation_rotation_forbidden_for_root_bound_consumer:" + consumerDomain
            );
        }
        long seq = generationSeq.incrementAndGet();
        long token = fencingToken.incrementAndGet();
        current = snapshot(seq, token, reason == null ? "reset" : reason);
        return current;
    }

    Fence fence() {
        Snapshot value = current();
        return new Fence(value.generationSeq(), value.generationHash(), value.fencingToken());
    }

    boolean matches(Fence fence) {
        if (fence == null) return false;
        Snapshot value = current();
        return value.generationSeq() == fence.generationSeq()
            && value.fencingToken() == fence.fencingToken()
            && value.generationHash().equals(fence.generationHash());
    }

    boolean rootBound() {
        return root != null;
    }

    String consumerDomain() {
        return consumerDomain;
    }

    private static Snapshot snapshot(long seq, long token, String reason) {
        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v24.runtime_generation_fence.v1");
        material.put("generationSeq", seq);
        material.put("fencingToken", token);
        material.put("reason", reason);
        return new Snapshot(seq, Hashing.canonicalHash(material), token);
    }
}
