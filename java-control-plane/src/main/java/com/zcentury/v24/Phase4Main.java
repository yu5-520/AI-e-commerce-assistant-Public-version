package com.zcentury.v24;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/** V24.16-V24.17 shadow verifier: Frontend Authority + SSE. */
public final class Phase4Main {
    private static final String VERSION = "24.17.0-phase4.1";

    private Phase4Main() {}

    public static void main(String[] args) throws Exception {
        Map<String, String> options = options(args);
        Path root = Paths.get(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Path evidencePath = resolve(root, options.getOrDefault("evidence", "dist/v24-java-phase4/frontend-baseline-evidence.json"));
        Path policyPath = resolve(root, options.getOrDefault("policy", "governance/v24/phase4-frontend-authority-policy.json"));
        Path contractPath = resolve(root, options.getOrDefault("contract", "governance/v24/frontend-view-contract-v24.json"));
        Path output = resolve(root, options.getOrDefault("output", "dist/v24-java-phase4/phase4-verification-report.json"));

        Map<String, Object> evidence = readObject(evidencePath);
        Map<String, Object> policy = readObject(policyPath);
        Map<String, Object> contract = readObject(contractPath);
        verifyEvidence(evidence);
        verifyPolicy(policy, contract);

        String generation1 = Hashing.canonicalHash(Map.of("generationSeq", 1L, "release", "v24-phase4"));
        FrontendViewAuthority authority = new FrontendViewAuthority(1L, generation1);
        FrontendSseAuthority sse = new FrontendSseAuthority();

        Map<String, Object> initialModules = modules("A", "A", "A", "A", "A", "A");
        String runtime1 = Hashing.canonicalHash(Map.of("state", "runtime-1"));
        Map<String, Object> manifest1 = authority.buildManifest("operator-center", "competition_operator", runtime1, initialModules);
        Map<String, Object> publish1 = authority.publish(0L, generation1, manifest1);
        require("PUBLISHED".equals(text(publish1.get("decision"))), "initial_publish_failed:" + publish1);
        Map<String, Object> event1 = sse.acceptPublication(publish1);
        require(Boolean.TRUE.equals(event1.get("emitted")), "initial_sse_not_emitted");

        String beforeRead = Hashing.canonicalHash(authority.debugState());
        Map<String, Object> headRead1 = authority.readHead();
        Map<String, Object> headRead2 = authority.readHead();
        String afterRead = Hashing.canonicalHash(authority.debugState());
        boolean headReadPure = beforeRead.equals(afterRead) && headRead1.equals(headRead2);
        require(headReadPure, "head_read_mutated_authority");

        Map<String, Object> duplicate = authority.publish(1L, generation1, manifest1);
        require("NO_CHANGE".equals(text(duplicate.get("decision"))), "duplicate_manifest_must_no_change");
        Map<String, Object> duplicateEvent = sse.acceptPublication(duplicate);
        require(Boolean.FALSE.equals(duplicateEvent.get("emitted")), "duplicate_manifest_sse_must_suppress");
        require(sse.eventCount() == 1, "duplicate_sse_event_created");

        Map<String, Object> changedModulesPayload = modules("A", "B", "A", "A", "A", "A");
        String runtime2 = Hashing.canonicalHash(Map.of("state", "runtime-2"));
        Map<String, Object> manifest2 = authority.buildManifest("operator-center", "competition_operator", runtime2, changedModulesPayload);
        Map<String, Object> publish2 = authority.publish(1L, generation1, manifest2);
        require("PUBLISHED".equals(text(publish2.get("decision"))), "changed_publish_failed:" + publish2);
        require(List.of("products").equals(strings(publish2.get("changedModules"))), "changed_module_isolation_failed:" + publish2);
        sse.acceptPublication(publish2);

        int changedFetchCount = changedModuleFetchCount(manifest1, manifest2);
        require(changedFetchCount == 1, "browser_hash_cache_should_fetch_one_changed_module:" + changedFetchCount);

        Map<String, Object> casModules = modules("A", "B", "C", "A", "A", "A");
        String runtime3 = Hashing.canonicalHash(Map.of("state", "runtime-3"));
        Map<String, Object> manifest3 = authority.buildManifest("operator-center", "competition_operator", runtime3, casModules);
        Map<String, Object> casWinner = authority.publish(2L, generation1, manifest3);
        Map<String, Object> casLoser = authority.publish(2L, generation1, manifest3);
        require("PUBLISHED".equals(text(casWinner.get("decision"))), "cas_winner_missing");
        require("HEAD_VERSION_CONFLICT".equals(text(casLoser.get("decision"))), "cas_loser_must_conflict:" + casLoser);
        sse.acceptPublication(casWinner);
        sse.acceptPublication(casLoser);
        boolean casSingleWinner = authority.headVersion() == 3L && sse.eventCount() == 3;
        require(casSingleWinner, "cas_single_winner_failed");

        Map<String, Object> staleManifest = authority.buildManifest(
            "operator-center",
            "competition_operator",
            Hashing.canonicalHash(Map.of("state", "stale-runtime")),
            modules("D", "B", "C", "A", "A", "A")
        );
        String generation2 = Hashing.canonicalHash(Map.of("generationSeq", 2L, "release", "v24-phase4"));
        authority.rotateGeneration(2L, generation2);
        Map<String, Object> stalePublish = authority.publish(3L, generation1, staleManifest);
        boolean staleGenerationBlocked = "STALE_GENERATION".equals(text(stalePublish.get("decision"))) && authority.headVersion() == 3L;
        require(staleGenerationBlocked, "stale_generation_publish_not_blocked:" + stalePublish);
        sse.acceptPublication(stalePublish);
        require(sse.eventCount() == 3, "stale_generation_must_not_emit_sse");

        List<Map<String, Object>> events = sse.events();
        Map<String, Object> lastEvent = events.get(events.size() - 1);
        Map<String, Object> lastData = object(lastEvent.get("data"));
        Map<String, Object> currentHead = authority.readHead();
        boolean eventHeadAligned = text(lastData.get("manifestHash")).equals(text(currentHead.get("manifestHash")));
        require(eventHeadAligned, "sse_head_manifest_mismatch");
        boolean sseFrameValid = text(lastEvent.get("frame")).startsWith("event: view-head-changed\n")
            && text(lastEvent.get("frame")).contains("id: " + text(lastEvent.get("id")) + "\n")
            && text(lastEvent.get("frame")).endsWith("\n\n");
        require(sseFrameValid, "sse_frame_invalid");

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v24.phase4_verification.v1");
        material.put("version", VERSION);
        material.put("verified", true);
        material.put("enforcementMode", "SHADOW");
        material.put("frontendAuthority", "JAVA_SHADOW_VIEW_HEAD_MANIFEST_CAS");
        material.put("sseAuthority", "JAVA_SHADOW_VIEW_HEAD_CHANGED");
        material.put("headReadPure", headReadPure);
        material.put("casSingleWinner", casSingleWinner);
        material.put("staleGenerationBlocked", staleGenerationBlocked);
        material.put("duplicateSseSuppressed", sse.eventCount() == 3);
        material.put("changedModuleFetchIsolation", changedFetchCount == 1);
        material.put("changedModuleFetchCount", changedFetchCount);
        material.put("sseEventCount", sse.eventCount());
        material.put("sseEventName", FrontendSseAuthority.EVENT_NAME);
        material.put("sseFrameValid", sseFrameValid);
        material.put("eventHeadManifestAligned", eventHeadAligned);
        material.put("browserJsRuntimeUnchanged", true);
        material.put("pythonProductionViewWriteAuthorityUnchanged", true);
        material.put("javaProductionViewCutoverEnabled", false);
        material.put("networkSseCutoverEnabled", false);
        material.put("existingHeadGetMayMaterialize", evidence.get("headGetMayMaterialize"));
        material.put("existingBrowserImmutableHashCache", evidence.get("browserImmutableHashCache"));
        material.put("existingBrowserEventSourcePresent", evidence.get("browserEventSourcePresent"));
        material.put("phaseCoverage", List.of("V24.16_FRONTEND_AUTHORITY", "V24.17_SSE"));
        material.put("pythonEvidenceHash", evidence.get("evidenceHash"));
        material.put("policyHash", Hashing.canonicalHash(policy));
        material.put("contractHash", Hashing.canonicalHash(contract));
        String verificationHash = Hashing.canonicalHash(material);
        LinkedHashMap<String, Object> report = new LinkedHashMap<>(material);
        report.put("verificationHash", verificationHash);
        Files.createDirectories(output.getParent());
        Files.writeString(output, Json.canonical(report) + "\n", StandardCharsets.UTF_8);
        System.out.println(Json.canonical(report));
    }

    private static void verifyEvidence(Map<String, Object> evidence) {
        String declared = text(evidence.get("evidenceHash"));
        LinkedHashMap<String, Object> material = new LinkedHashMap<>(evidence);
        material.remove("evidenceHash");
        require(declared.equals(Hashing.canonicalHash(material)), "frontend_baseline_evidence_hash_mismatch");
        require(Boolean.TRUE.equals(evidence.get("headGetMayMaterialize")), "baseline_head_get_materialization_not_detected");
        require(Boolean.TRUE.equals(evidence.get("browserImmutableHashCache")), "baseline_browser_hash_cache_not_detected");
        require(Boolean.FALSE.equals(evidence.get("browserEventSourcePresent")), "baseline_sse_already_present_unexpectedly");
        require("PYTHON_SQLITE".equals(text(evidence.get("productionViewAuthority"))), "baseline_view_authority_mismatch");
        require("JAVASCRIPT".equals(text(evidence.get("browserRuntime"))), "baseline_browser_runtime_mismatch");
    }

    private static void verifyPolicy(Map<String, Object> policy, Map<String, Object> contract) {
        require("SHADOW".equals(text(policy.get("enforcementMode"))), "phase4_must_start_shadow");
        Map<String, Object> frontend = object(policy.get("frontendAuthority"));
        Map<String, Object> sse = object(policy.get("sseAuthority"));
        require("SHADOW".equals(text(frontend.get("mode"))), "frontend_authority_must_shadow");
        require(Boolean.TRUE.equals(frontend.get("pythonProductionViewWriteAuthorityUnchanged")), "python_view_writer_boundary_required");
        require(Boolean.FALSE.equals(frontend.get("javaProductionViewCutoverEnabled")), "java_view_cutover_must_be_disabled");
        require("PURE_READ".equals(text(frontend.get("headReadTarget"))), "head_read_target_must_be_pure");
        require("DOMAIN_EVENT_ONLY".equals(text(frontend.get("projectionTriggerTarget"))), "projection_trigger_target_invalid");
        require("CAS".equals(text(frontend.get("publishMode"))), "publish_mode_must_cas");
        require("SHADOW".equals(text(sse.get("mode"))), "sse_authority_must_shadow");
        require("view-head-changed".equals(text(sse.get("eventName"))), "sse_event_name_mismatch");
        require("SUPPRESS".equals(text(sse.get("duplicateManifestEvent"))), "duplicate_sse_must_suppress");
        require(Boolean.FALSE.equals(sse.get("networkCutoverEnabled")), "network_sse_cutover_must_be_disabled");
        Map<String, Object> contractHead = object(contract.get("head"));
        Map<String, Object> contractSse = object(contract.get("sse"));
        require(Boolean.FALSE.equals(contractHead.get("readSideEffectsAllowed")), "contract_head_read_must_be_pure");
        require("COMPARE_AND_SET".equals(text(contractHead.get("publishMode"))), "contract_publish_must_cas");
        require("view-head-changed".equals(text(contractSse.get("event"))), "contract_sse_event_mismatch");
    }

    private static Map<String, Object> modules(String dashboard, String products, String tasks, String pipeline, String dataLine, String systemStatus) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("dashboard", Map.of("revision", dashboard, "counts", List.of(1L, 2L)));
        result.put("products", Map.of("revision", products, "items", List.of("SKU-1", "SKU-2")));
        result.put("tasks", Map.of("revision", tasks, "items", List.of("TASK-1")));
        result.put("pipeline", Map.of("revision", pipeline, "stage", "agent1"));
        result.put("dataLine", Map.of("revision", dataLine, "status", "running"));
        result.put("systemStatus", Map.of("revision", systemStatus, "ready", true));
        return result;
    }

    private static int changedModuleFetchCount(Map<String, Object> before, Map<String, Object> after) {
        Map<String, Object> a = object(before.get("modules"));
        Map<String, Object> b = object(after.get("modules"));
        TreeSet<String> keys = new TreeSet<>();
        keys.addAll(a.keySet());
        keys.addAll(b.keySet());
        int changed = 0;
        for (String key : keys) {
            String oldHash = text(object(a.get(key)).get("contentHash"));
            String newHash = text(object(b.get(key)).get("contentHash"));
            if (!oldHash.equals(newHash)) changed++;
        }
        return changed;
    }

    private static List<String> strings(Object value) {
        List<String> result = new ArrayList<>();
        for (Object item : Json.array(value)) result.add(text(item));
        return result;
    }

    private static Map<String, Object> readObject(Path path) throws IOException {
        require(Files.isRegularFile(path), "json_file_missing:" + path);
        return object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
    }

    private static Map<String, String> options(String[] args) {
        LinkedHashMap<String, String> result = new LinkedHashMap<>();
        for (int i = 0; i < args.length; i++) {
            String key = args[i];
            if (!key.startsWith("--") || i + 1 >= args.length) throw new IllegalArgumentException("invalid_option:" + key);
            result.put(key.substring(2), args[++i]);
        }
        return result;
    }

    private static Path resolve(Path root, String raw) {
        Path value = Paths.get(raw);
        return value.isAbsolute() ? value.normalize() : root.resolve(value).normalize();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value) {
        return value instanceof Map<?, ?> ? (Map<String, Object>) value : Map.of();
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
