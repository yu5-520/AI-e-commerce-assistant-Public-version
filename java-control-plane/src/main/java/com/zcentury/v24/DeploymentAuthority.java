package com.zcentury.v24;

import java.util.LinkedHashMap;
import java.util.Map;

/** V24.18 deterministic deployment head with CAS and generation fencing. */
final class DeploymentAuthority {
    private long generationSeq;
    private String generationHash;
    private long deploymentVersion;
    private Map<String, Object> head = Map.of();

    DeploymentAuthority(long generationSeq, String generationHash) {
        if (generationSeq < 1L) throw new IllegalArgumentException("generation_seq_must_be_positive");
        if (generationHash == null || !generationHash.startsWith("sha256:")) {
            throw new IllegalArgumentException("generation_hash_invalid");
        }
        this.generationSeq = generationSeq;
        this.generationHash = generationHash;
    }

    synchronized Map<String, Object> publish(
        long expectedVersion,
        String callerGenerationHash,
        Map<String, Object> candidate,
        Map<String, Object> compatibility
    ) {
        if (!generationHash.equals(callerGenerationHash)) {
            return decision("STALE_GENERATION", expectedVersion, candidate, compatibility);
        }
        if (expectedVersion != deploymentVersion) {
            return decision("DEPLOYMENT_VERSION_CONFLICT", expectedVersion, candidate, compatibility);
        }
        if (!"COMPATIBLE".equals(text(compatibility.get("decision")))) {
            return decision("COMPATIBILITY_REJECTED", expectedVersion, candidate, compatibility);
        }
        String proofFailure = candidateProofFailure(candidate);
        if (!proofFailure.isBlank()) {
            return decision("CANDIDATE_PROOF_REJECTED:" + proofFailure, expectedVersion, candidate, compatibility);
        }
        String releaseHash = text(candidate.get("releaseHash"));
        if (releaseHash.equals(text(head.get("releaseHash")))) {
            return decision("NO_CHANGE", expectedVersion, candidate, compatibility);
        }

        deploymentVersion += 1L;
        LinkedHashMap<String, Object> next = new LinkedHashMap<>();
        next.put("schema", "v24.deployment_head.v1");
        next.put("authorityId", "java.deployment.v24");
        next.put("deploymentVersion", deploymentVersion);
        next.put("generationSeq", generationSeq);
        next.put("generationHash", generationHash);
        next.put("releaseHash", releaseHash);
        next.put("sourceCommit", candidate.get("sourceCommit"));
        next.put("productVersion", candidate.get("productVersion"));
        next.put("dataSchemaVersion", candidate.get("dataSchemaVersion"));
        next.put("compatibilityHash", compatibility.get("compatibilityHash"));
        next.put("migrationRequired", compatibility.get("migrationRequired"));
        next.put("migrationIds", compatibility.get("migrationIds"));
        next.put("authoritative", true);
        next.put("rollbackPrepared", candidate.get("rollbackPrepared"));
        next.put("headHash", Hashing.canonicalHash(next));
        head = Map.copyOf(next);

        LinkedHashMap<String, Object> result = new LinkedHashMap<>(decision("DEPLOYED", expectedVersion, candidate, compatibility));
        result.put("head", head);
        return result;
    }

    synchronized void rotateGeneration(long nextSeq, String nextHash) {
        if (nextSeq <= generationSeq) throw new IllegalArgumentException("generation_must_increase");
        if (nextHash == null || !nextHash.startsWith("sha256:")) throw new IllegalArgumentException("generation_hash_invalid");
        generationSeq = nextSeq;
        generationHash = nextHash;
    }

    synchronized Map<String, Object> readHead() {
        return head.isEmpty() ? Map.of() : new LinkedHashMap<>(head);
    }

    synchronized long deploymentVersion() {
        return deploymentVersion;
    }

    synchronized Map<String, Object> debugState() {
        LinkedHashMap<String, Object> state = new LinkedHashMap<>();
        state.put("generationSeq", generationSeq);
        state.put("generationHash", generationHash);
        state.put("deploymentVersion", deploymentVersion);
        state.put("head", head);
        return state;
    }

    private Map<String, Object> decision(
        String decision,
        long expectedVersion,
        Map<String, Object> candidate,
        Map<String, Object> compatibility
    ) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("schema", "v24.deployment_decision.v1");
        result.put("decision", decision);
        result.put("expectedDeploymentVersion", expectedVersion);
        result.put("currentDeploymentVersion", deploymentVersion);
        result.put("generationSeq", generationSeq);
        result.put("generationHash", generationHash);
        result.put("candidateReleaseHash", candidate.get("releaseHash"));
        result.put("compatibilityHash", compatibility.get("compatibilityHash"));
        result.put("currentHeadHash", head.get("headHash"));
        return result;
    }

    private static String candidateProofFailure(Map<String, Object> candidate) {
        String[] booleans = {
            "releaseVerified",
            "environmentVerified",
            "schemaPrepared",
            "runtimeSmokeVerified",
            "rollbackPrepared",
            "executionExclusivityVerified"
        };
        for (String field : booleans) {
            if (!Boolean.TRUE.equals(candidate.get(field))) return field;
        }
        String releaseHash = text(candidate.get("releaseHash"));
        if (!releaseHash.startsWith("sha256:") || releaseHash.length() != 71) return "releaseHash";
        if (text(candidate.get("sourceCommit")).isBlank()) return "sourceCommit";
        return "";
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }
}
