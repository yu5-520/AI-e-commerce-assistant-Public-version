package com.zcentury.v24;

import java.util.LinkedHashMap;
import java.util.concurrent.atomic.AtomicLong;

/** Dependency-free generation/fencing authority used by the Phase3 queue core. */
final class GenerationFencer {
    record Snapshot(long generationSeq, String generationHash, long fencingToken) {}
    record Fence(long generationSeq, String generationHash, long fencingToken) {}

    private final AtomicLong generationSeq = new AtomicLong(1L);
    private final AtomicLong fencingToken = new AtomicLong(1L);
    private volatile Snapshot current = snapshot(1L, 1L, "initial");

    Snapshot current() {
        return current;
    }

    synchronized Snapshot rotate(String reason) {
        long seq = generationSeq.incrementAndGet();
        long token = fencingToken.incrementAndGet();
        current = snapshot(seq, token, reason == null ? "reset" : reason);
        return current;
    }

    Fence fence() {
        Snapshot value = current;
        return new Fence(value.generationSeq(), value.generationHash(), value.fencingToken());
    }

    boolean matches(Fence fence) {
        if (fence == null) return false;
        Snapshot value = current;
        return value.generationSeq() == fence.generationSeq()
            && value.fencingToken() == fence.fencingToken()
            && value.generationHash().equals(fence.generationHash());
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
