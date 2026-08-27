package com.zcentury.v24;

import java.util.LinkedHashMap;
import java.util.Map;

/** V24.20 legacy retirement authority. Removal is default-deny and has no automatic fallback. */
final class LegacyRemovalAuthority {
    private final LinkedHashMap<String, LinkedHashMap<String, Object>> entries = new LinkedHashMap<>();

    synchronized void register(
        String legacyId,
        String replacementAuthority,
        int referenceCount,
        int writerCount,
        boolean executionRights
    ) {
        if (legacyId == null || legacyId.isBlank()) throw new IllegalArgumentException("legacy_id_required");
        if (entries.containsKey(legacyId)) throw new IllegalStateException("legacy_already_registered:" + legacyId);
        LinkedHashMap<String, Object> entry = new LinkedHashMap<>();
        entry.put("legacyId", legacyId);
        entry.put("replacementAuthority", replacementAuthority);
        entry.put("state", "REGISTERED");
        entry.put("referenceCount", Math.max(0, referenceCount));
        entry.put("writerCount", Math.max(0, writerCount));
        entry.put("executionRights", executionRights);
        entry.put("removalHash", null);
        entries.put(legacyId, entry);
    }

    synchronized Map<String, Object> revokeExecution(String legacyId) {
        LinkedHashMap<String, Object> entry = requireEntry(legacyId);
        if ("REMOVED".equals(text(entry.get("state")))) return decision("ALREADY_REMOVED", entry);
        entry.put("executionRights", false);
        entry.put("state", "QUARANTINED");
        return decision("EXECUTION_REVOKED", entry);
    }

    synchronized Map<String, Object> detach(String legacyId, int referenceCount, int writerCount) {
        LinkedHashMap<String, Object> entry = requireEntry(legacyId);
        if ("REMOVED".equals(text(entry.get("state")))) return decision("ALREADY_REMOVED", entry);
        entry.put("referenceCount", Math.max(0, referenceCount));
        entry.put("writerCount", Math.max(0, writerCount));
        if (!Boolean.TRUE.equals(entry.get("executionRights"))) entry.put("state", "QUARANTINED");
        return decision("DETACHED", entry);
    }

    synchronized Map<String, Object> remove(
        String legacyId,
        boolean replacementAuthorityActive,
        Map<String, Object> compatibility,
        Map<String, Object> deploymentHead
    ) {
        LinkedHashMap<String, Object> entry = requireEntry(legacyId);
        if ("REMOVED".equals(text(entry.get("state")))) return decision("ALREADY_REMOVED", entry);
        if (!replacementAuthorityActive) return decision("REPLACEMENT_NOT_ACTIVE", entry);
        if (!"COMPATIBLE".equals(text(compatibility.get("decision")))) {
            return decision("COMPATIBILITY_NOT_PROVEN", entry);
        }
        if (!Boolean.TRUE.equals(deploymentHead.get("authoritative"))) {
            return decision("DEPLOYMENT_NOT_AUTHORITATIVE", entry);
        }
        if (Boolean.TRUE.equals(entry.get("executionRights"))) {
            return decision("LEGACY_EXECUTION_RIGHTS_REMAIN", entry);
        }
        if (number(entry.get("writerCount")) != 0L) return decision("LEGACY_WRITERS_REMAIN", entry);
        if (number(entry.get("referenceCount")) != 0L) return decision("LEGACY_REFERENCES_REMAIN", entry);

        LinkedHashMap<String, Object> proof = new LinkedHashMap<>();
        proof.put("legacyId", legacyId);
        proof.put("replacementAuthority", entry.get("replacementAuthority"));
        proof.put("replacementReleaseHash", deploymentHead.get("releaseHash"));
        proof.put("deploymentHeadHash", deploymentHead.get("headHash"));
        proof.put("compatibilityHash", compatibility.get("compatibilityHash"));
        proof.put("referenceCount", 0L);
        proof.put("writerCount", 0L);
        proof.put("executionRights", false);
        proof.put("automaticFallbackAllowed", false);
        String removalHash = Hashing.canonicalHash(proof);
        entry.put("state", "REMOVED");
        entry.put("removalHash", removalHash);
        return decision("REMOVED", entry);
    }

    synchronized Map<String, Object> restore(String legacyId) {
        LinkedHashMap<String, Object> entry = requireEntry(legacyId);
        if ("REMOVED".equals(text(entry.get("state")))) {
            return decision("RESTORE_FORBIDDEN", entry);
        }
        return decision("NO_RESTORE_NEEDED", entry);
    }

    synchronized boolean allRemoved() {
        if (entries.isEmpty()) return false;
        for (Map<String, Object> entry : entries.values()) {
            if (!"REMOVED".equals(text(entry.get("state")))) return false;
        }
        return true;
    }

    synchronized Map<String, Object> debugState() {
        LinkedHashMap<String, Object> copy = new LinkedHashMap<>();
        for (Map.Entry<String, LinkedHashMap<String, Object>> item : entries.entrySet()) {
            copy.put(item.getKey(), new LinkedHashMap<>(item.getValue()));
        }
        return copy;
    }

    private LinkedHashMap<String, Object> requireEntry(String legacyId) {
        LinkedHashMap<String, Object> entry = entries.get(legacyId);
        if (entry == null) throw new IllegalArgumentException("legacy_not_registered:" + legacyId);
        return entry;
    }

    private static Map<String, Object> decision(String decision, Map<String, Object> entry) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("schema", "v24.legacy_removal_decision.v1");
        result.put("decision", decision);
        result.put("legacyId", entry.get("legacyId"));
        result.put("state", entry.get("state"));
        result.put("referenceCount", entry.get("referenceCount"));
        result.put("writerCount", entry.get("writerCount"));
        result.put("executionRights", entry.get("executionRights"));
        result.put("replacementAuthority", entry.get("replacementAuthority"));
        result.put("removalHash", entry.get("removalHash"));
        result.put("automaticFallbackAllowed", false);
        return result;
    }

    private static long number(Object value) {
        if (value instanceof Number number) return number.longValue();
        try {
            return Long.parseLong(text(value));
        } catch (NumberFormatException ignored) {
            return -1L;
        }
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }
}
