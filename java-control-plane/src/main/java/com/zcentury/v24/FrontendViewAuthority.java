package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/**
 * V24.16 deterministic frontend publication authority.
 *
 * This class is intentionally transport-free in Phase4 SHADOW mode. It proves the
 * Head/Manifest/Module identity, compare-and-set publication and generation fencing
 * rules before any production HTTP ownership moves out of Python.
 */
public final class FrontendViewAuthority {
    public static final String VERSION = "24.16.0";

    private long generationSeq;
    private String generationHash;
    private long headVersion;
    private Map<String, Object> head;
    private Map<String, Object> currentManifest;
    private final Map<String, Object> immutableArtifacts = new LinkedHashMap<>();

    public FrontendViewAuthority(long generationSeq, String generationHash) {
        this.generationSeq = generationSeq;
        this.generationHash = requiredHash(generationHash, "generation_hash_required");
        this.headVersion = 0L;
        this.head = emptyHead();
        this.currentManifest = Map.of();
    }

    public synchronized Map<String, Object> buildManifest(
        String viewKey,
        String userId,
        String runtimeStateHash,
        Map<String, Object> modulePayloads
    ) {
        requiredHash(runtimeStateHash, "runtime_state_hash_required");
        if (modulePayloads == null || modulePayloads.isEmpty()) {
            throw new IllegalArgumentException("frontend_modules_required");
        }
        LinkedHashMap<String, Object> modules = new LinkedHashMap<>();
        List<String> order = new ArrayList<>();
        for (String moduleKey : new TreeSet<>(modulePayloads.keySet())) {
            Object payload = modulePayloads.get(moduleKey);
            String contentHash = Hashing.canonicalHash(payload);
            String artifactRef = "V24-MOD-" + contentHash.substring("sha256:".length(), "sha256:".length() + 24);
            LinkedHashMap<String, Object> artifact = new LinkedHashMap<>();
            artifact.put("schema", "frontend_view.module.v24");
            artifact.put("version", VERSION);
            artifact.put("moduleKey", moduleKey);
            artifact.put("contentHash", contentHash);
            artifact.put("payload", payload);
            immutableArtifacts.putIfAbsent(contentHash, artifact);

            LinkedHashMap<String, Object> record = new LinkedHashMap<>();
            record.put("artifactRef", artifactRef);
            record.put("contentHash", contentHash);
            modules.put(moduleKey, record);
            order.add(moduleKey);
        }

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "frontend_view.manifest.v24");
        material.put("version", VERSION);
        material.put("viewKey", nonBlank(viewKey, "view_key_required"));
        material.put("userId", nonBlank(userId, "user_id_required"));
        material.put("scopeKey", viewKey + "::" + userId);
        material.put("runtimeStateHash", runtimeStateHash);
        material.put("generationSeq", generationSeq);
        material.put("generationHash", generationHash);
        material.put("modules", modules);
        material.put("moduleOrder", order);
        material.put("atomicPublication", true);
        material.put("crossGenerationFallbackAllowed", false);
        String manifestHash = Hashing.canonicalHash(material);
        LinkedHashMap<String, Object> manifest = new LinkedHashMap<>(material);
        manifest.put("manifestHash", manifestHash);
        manifest.put("manifestRef", "V24-MAN-" + manifestHash.substring("sha256:".length(), "sha256:".length() + 24));
        immutableArtifacts.putIfAbsent(manifestHash, manifest);
        return copy(manifest);
    }

    public synchronized Map<String, Object> publish(
        long expectedHeadVersion,
        String expectedGenerationHash,
        Map<String, Object> manifest
    ) {
        String expectedGeneration = requiredHash(expectedGenerationHash, "expected_generation_hash_required");
        String manifestGeneration = requiredHash(text(manifest.get("generationHash")), "manifest_generation_hash_required");
        String manifestHash = requiredHash(text(manifest.get("manifestHash")), "manifest_hash_required");

        if (!generationHash.equals(expectedGeneration) || !generationHash.equals(manifestGeneration)) {
            return decision("STALE_GENERATION", manifestHash, List.of(), expectedHeadVersion);
        }
        if (expectedHeadVersion != headVersion) {
            return decision("HEAD_VERSION_CONFLICT", manifestHash, List.of(), expectedHeadVersion);
        }
        if (manifestHash.equals(text(head.get("manifestHash")))) {
            return decision("NO_CHANGE", manifestHash, List.of(), expectedHeadVersion);
        }

        List<String> changedModules = changedModules(currentManifest, manifest);
        headVersion += 1L;
        currentManifest = copy(manifest);
        LinkedHashMap<String, Object> next = new LinkedHashMap<>();
        next.put("schema", "frontend_view.head.v24");
        next.put("version", VERSION);
        next.put("viewKey", manifest.get("viewKey"));
        next.put("userId", manifest.get("userId"));
        next.put("scopeKey", manifest.get("scopeKey"));
        next.put("headVersion", headVersion);
        next.put("manifestRef", manifest.get("manifestRef"));
        next.put("manifestHash", manifestHash);
        next.put("runtimeStateHash", manifest.get("runtimeStateHash"));
        next.put("generationSeq", generationSeq);
        next.put("generationHash", generationHash);
        next.put("status", "ready");
        head = next;

        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("decision", "PUBLISHED");
        result.put("published", true);
        result.put("headVersion", headVersion);
        result.put("manifestHash", manifestHash);
        result.put("runtimeStateHash", manifest.get("runtimeStateHash"));
        result.put("generationSeq", generationSeq);
        result.put("generationHash", generationHash);
        result.put("changedModules", changedModules);
        result.put("publishHash", Hashing.canonicalHash(result));
        return result;
    }

    /** Pure read: no materialization, cache refresh, version bump or state mutation. */
    public synchronized Map<String, Object> readHead() {
        return copy(head);
    }

    public synchronized Map<String, Object> readManifest(String manifestHash) {
        Object value = immutableArtifacts.get(requiredHash(manifestHash, "manifest_hash_required"));
        if (!(value instanceof Map<?, ?> map)) throw new IllegalArgumentException("manifest_not_found:" + manifestHash);
        @SuppressWarnings("unchecked")
        Map<String, Object> typed = (Map<String, Object>) map;
        if (!"frontend_view.manifest.v24".equals(text(typed.get("schema")))) {
            throw new IllegalArgumentException("artifact_is_not_manifest:" + manifestHash);
        }
        return copy(typed);
    }

    public synchronized Map<String, Object> readModule(String contentHash) {
        Object value = immutableArtifacts.get(requiredHash(contentHash, "module_hash_required"));
        if (!(value instanceof Map<?, ?> map)) throw new IllegalArgumentException("module_not_found:" + contentHash);
        @SuppressWarnings("unchecked")
        Map<String, Object> typed = (Map<String, Object>) map;
        if (!"frontend_view.module.v24".equals(text(typed.get("schema")))) {
            throw new IllegalArgumentException("artifact_is_not_module:" + contentHash);
        }
        return copy(typed);
    }

    public synchronized void rotateGeneration(long nextSeq, String nextHash) {
        if (nextSeq <= generationSeq) throw new IllegalArgumentException("generation_seq_must_increase");
        generationSeq = nextSeq;
        generationHash = requiredHash(nextHash, "generation_hash_required");
    }

    public synchronized long headVersion() {
        return headVersion;
    }

    public synchronized String generationHash() {
        return generationHash;
    }

    public synchronized Map<String, Object> debugState() {
        LinkedHashMap<String, Object> state = new LinkedHashMap<>();
        state.put("generationSeq", generationSeq);
        state.put("generationHash", generationHash);
        state.put("headVersion", headVersion);
        state.put("head", head);
        state.put("currentManifestHash", currentManifest.get("manifestHash"));
        state.put("artifactHashes", new ArrayList<>(immutableArtifacts.keySet()));
        return copy(state);
    }

    private Map<String, Object> decision(String code, String manifestHash, List<String> changed, long expected) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("decision", code);
        result.put("published", false);
        result.put("expectedHeadVersion", expected);
        result.put("currentHeadVersion", headVersion);
        result.put("manifestHash", manifestHash);
        result.put("generationSeq", generationSeq);
        result.put("generationHash", generationHash);
        result.put("changedModules", changed);
        result.put("decisionHash", Hashing.canonicalHash(result));
        return result;
    }

    private static List<String> changedModules(Map<String, Object> before, Map<String, Object> after) {
        Map<String, Object> beforeModules = object(before.get("modules"));
        Map<String, Object> afterModules = object(after.get("modules"));
        TreeSet<String> keys = new TreeSet<>();
        keys.addAll(beforeModules.keySet());
        keys.addAll(afterModules.keySet());
        List<String> changed = new ArrayList<>();
        for (String key : keys) {
            String oldHash = text(object(beforeModules.get(key)).get("contentHash"));
            String newHash = text(object(afterModules.get(key)).get("contentHash"));
            if (!oldHash.equals(newHash)) changed.add(key);
        }
        return changed;
    }

    private Map<String, Object> emptyHead() {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("schema", "frontend_view.head.v24");
        value.put("version", VERSION);
        value.put("headVersion", 0L);
        value.put("manifestHash", "");
        value.put("generationSeq", generationSeq);
        value.put("generationHash", generationHash);
        value.put("status", "empty");
        return value;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value) {
        return value instanceof Map<?, ?> ? (Map<String, Object>) value : Map.of();
    }

    private static LinkedHashMap<String, Object> copy(Map<String, Object> value) {
        return new LinkedHashMap<>(value);
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private static String nonBlank(String value, String error) {
        if (value == null || value.isBlank()) throw new IllegalArgumentException(error);
        return value;
    }

    private static String requiredHash(String value, String error) {
        if (value == null || !value.startsWith("sha256:") || value.length() <= "sha256:".length()) {
            throw new IllegalArgumentException(error);
        }
        return value;
    }
}
