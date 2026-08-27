package com.zcentury.v24;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** V24.17 deterministic SSE event authority for frontend Head changes. */
public final class FrontendSseAuthority {
    public static final String VERSION = "24.17.0";
    public static final String EVENT_NAME = "view-head-changed";

    private final List<Map<String, Object>> events = new ArrayList<>();
    private String lastManifestHash = "";

    public synchronized Map<String, Object> acceptPublication(Map<String, Object> publication) {
        if (!"PUBLISHED".equals(text(publication.get("decision"))) || !Boolean.TRUE.equals(publication.get("published"))) {
            return suppressed("NOT_A_CHANGED_PUBLISH", text(publication.get("manifestHash")));
        }
        String manifestHash = text(publication.get("manifestHash"));
        if (manifestHash.isBlank() || !manifestHash.startsWith("sha256:")) {
            throw new IllegalArgumentException("sse_manifest_hash_required");
        }
        if (manifestHash.equals(lastManifestHash)) {
            return suppressed("DUPLICATE_MANIFEST", manifestHash);
        }

        LinkedHashMap<String, Object> data = new LinkedHashMap<>();
        data.put("headVersion", publication.get("headVersion"));
        data.put("manifestHash", manifestHash);
        data.put("runtimeStateHash", publication.get("runtimeStateHash"));
        data.put("generationSeq", publication.get("generationSeq"));
        data.put("generationHash", publication.get("generationHash"));
        data.put("changedModules", publication.get("changedModules"));

        LinkedHashMap<String, Object> event = new LinkedHashMap<>();
        event.put("schema", "frontend_view.sse_event.v24");
        event.put("version", VERSION);
        event.put("event", EVENT_NAME);
        event.put("id", manifestHash);
        event.put("data", data);
        event.put("eventHash", Hashing.canonicalHash(event));
        event.put("frame", sseFrame(manifestHash, data));
        events.add(event);
        lastManifestHash = manifestHash;

        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("emitted", true);
        result.put("reason", "HEAD_CHANGED");
        result.put("event", event);
        result.put("eventCount", events.size());
        return result;
    }

    public synchronized List<Map<String, Object>> events() {
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, Object> event : events) result.add(new LinkedHashMap<>(event));
        return result;
    }

    public synchronized int eventCount() {
        return events.size();
    }

    private Map<String, Object> suppressed(String reason, String manifestHash) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("emitted", false);
        result.put("reason", reason);
        result.put("manifestHash", manifestHash);
        result.put("eventCount", events.size());
        return result;
    }

    private static String sseFrame(String manifestHash, Map<String, Object> data) {
        return "event: " + EVENT_NAME + "\n"
            + "id: " + manifestHash + "\n"
            + "data: " + Json.canonical(data) + "\n\n";
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }
}
