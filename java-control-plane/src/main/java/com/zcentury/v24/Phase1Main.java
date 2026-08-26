package com.zcentury.v24;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Stream;

/**
 * V24.0-V24.5 shadow authority.
 *
 * <p>This program does not replace the Python runtime. It proves the existing V23
 * registry/hash-lineage evidence, emits one default-deny ActiveRuntimeGraph and build
 * manifest, and can admit an exact materialized runtime candidate. Runtime write
 * authority remains with the sealed Python release until a later V24 phase.</p>
 */
public final class Phase1Main {
    private static final String VERSION = "24.0.0-phase1.1";

    private Phase1Main() {}

    public static void main(String[] args) throws Exception {
        if (args.length == 0) fail("command_required: shadow-compile | admit | self-test");
        String command = args[0];
        Map<String, String> options = options(args);
        switch (command) {
            case "shadow-compile" -> shadowCompile(options);
            case "admit" -> admit(options);
            case "self-test" -> selfTest();
            default -> fail("unknown_command:" + command);
        }
    }

    private static void shadowCompile(Map<String, String> options) throws Exception {
        Path root = path(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Path lineageDir = resolve(root, options.getOrDefault("lineage-dir", "dist/competition-lineage"));
        Path outputDir = resolve(root, options.getOrDefault("output-dir", "dist/v24-java-phase1"));
        Path policyPath = resolve(root, options.getOrDefault("policy", "governance/v24/runtime-exclusivity-policy.json"));

        Map<String, Object> source = readObject(root.resolve("config/competition_source_identity.json"));
        Map<String, Object> registry = readObject(root.resolve("config/v23_registry_runtime.json"));
        Map<String, Object> scope = readObject(root.resolve("config/competition_runtime_scope.json"));
        Map<String, Object> lineage = readObject(lineageDir.resolve("lineage-graph.json"));
        Map<String, Object> verification = readObject(lineageDir.resolve("verification-report.json"));
        Map<String, Object> policy = readObject(policyPath);

        require(Boolean.TRUE.equals(verification.get("verified")), "python_lineage_not_verified");
        require("SHADOW".equals(text(policy.get("enforcementMode"))), "phase1_policy_must_be_shadow");
        require("DENY".equals(text(policy.get("defaultRuntimeEligibility"))), "default_runtime_eligibility_must_deny");
        require("DENY".equals(text(policy.get("compatibilityDefault"))), "compatibility_default_must_deny");
        require("BLOCK".equals(text(policy.get("unknownRuntimeNode"))), "unknown_runtime_node_must_block");

        String sourceRegistryRoot = text(source.get("registryRootHash"));
        String registryRoot = text(registry.get("registryRootHash"));
        String lineageRegistryRoot = text(lineage.get("registryRootHash"));
        require(!sourceRegistryRoot.isBlank(), "source_registry_root_missing");
        require(sourceRegistryRoot.equals(registryRoot), "source_registry_root_mismatch");
        require(registryRoot.equals(lineageRegistryRoot), "lineage_registry_root_mismatch");

        List<Object> entrypoints = Json.array(scope.get("productionEntrypoints"));
        require(entrypoints.size() == 1, "exactly_one_production_entrypoint_required");
        String productionEntrypoint = text(entrypoints.get(0));

        List<Object> nodes = Json.array(lineage.get("nodes"));
        List<Object> edges = Json.array(lineage.get("edges"));
        LinkedHashMap<String, Object> graphMaterial = new LinkedHashMap<>();
        graphMaterial.put("nodes", nodes);
        graphMaterial.put("edges", edges);
        String computedGraphHash = Hashing.canonicalHash(graphMaterial);
        String declaredGraphHash = text(lineage.get("graphHash"));
        require(computedGraphHash.equals(declaredGraphHash), "lineage_graph_hash_mismatch:" + computedGraphHash + ":" + declaredGraphHash);

        List<Map<String, Object>> activeFileNodes = new ArrayList<>();
        Set<String> activeNodeIds = new LinkedHashSet<>();
        for (Object raw : nodes) {
            Map<String, Object> node = Json.object(raw);
            String id = text(node.get("id"));
            require(!id.isBlank(), "lineage_node_id_missing");
            activeNodeIds.add(id);
            if (!"file".equals(text(node.get("type")))) continue;
            String relative = safeRelative(text(node.get("path")));
            Path file = root.resolve(relative).normalize();
            require(file.startsWith(root), "runtime_path_escapes_root:" + relative);
            require(Files.isRegularFile(file), "active_runtime_file_missing:" + relative);
            String expectedHash = text(node.get("sha256"));
            String actualHash = Hashing.fileHash(file);
            require(expectedHash.equals(actualHash), "active_runtime_file_hash_mismatch:" + relative);
            LinkedHashMap<String, Object> record = new LinkedHashMap<>();
            record.put("id", id);
            record.put("path", relative);
            record.put("sha256", expectedHash);
            record.put("status", "ACTIVE");
            record.put("runtimeEligible", true);
            record.put("roles", node.getOrDefault("roles", List.of()));
            record.put("registryModules", node.getOrDefault("registryModules", List.of()));
            activeFileNodes.add(record);
        }
        activeFileNodes.sort(Comparator.comparing(item -> text(item.get("path"))));

        List<Object> activeGraphNodes = new ArrayList<>();
        for (Object raw : nodes) {
            Map<String, Object> original = Json.object(raw);
            LinkedHashMap<String, Object> node = new LinkedHashMap<>(original);
            node.put("runtimeStatus", "ACTIVE");
            node.put("runtimeEligible", true);
            activeGraphNodes.add(node);
        }
        List<Object> activeGraphEdges = new ArrayList<>();
        for (Object raw : edges) {
            Map<String, Object> original = Json.object(raw);
            String from = text(original.get("from"));
            String to = text(original.get("to"));
            require(activeNodeIds.contains(from) || from.startsWith("interface:"), "edge_source_unknown:" + from);
            require(activeNodeIds.contains(to) || to.startsWith("interface:"), "edge_target_unknown:" + to);
            LinkedHashMap<String, Object> edge = new LinkedHashMap<>(original);
            edge.put("runtimeTraversable", true);
            activeGraphEdges.add(edge);
        }

        String policyHash = Hashing.canonicalHash(policy);
        LinkedHashMap<String, Object> activeGraphMaterial = new LinkedHashMap<>();
        activeGraphMaterial.put("schema", "v24.active_runtime_graph.v1");
        activeGraphMaterial.put("version", VERSION);
        activeGraphMaterial.put("sourceCommit", lineage.get("sourceCommit"));
        activeGraphMaterial.put("registryRootHash", registryRoot);
        activeGraphMaterial.put("sourceLineageGraphHash", declaredGraphHash);
        activeGraphMaterial.put("productionEntrypoint", productionEntrypoint);
        activeGraphMaterial.put("defaultRuntimeEligibility", "DENY");
        activeGraphMaterial.put("compatibilityDefault", "DENY");
        activeGraphMaterial.put("policyHash", policyHash);
        activeGraphMaterial.put("nodes", activeGraphNodes);
        activeGraphMaterial.put("edges", activeGraphEdges);
        String activeGraphHash = Hashing.canonicalHash(activeGraphMaterial);
        LinkedHashMap<String, Object> activeGraph = new LinkedHashMap<>(activeGraphMaterial);
        activeGraph.put("activeRuntimeGraphHash", activeGraphHash);

        LinkedHashMap<String, Object> manifestMaterial = new LinkedHashMap<>();
        manifestMaterial.put("schema", "v24.active_runtime_manifest.v1");
        manifestMaterial.put("version", VERSION);
        manifestMaterial.put("sourceCommit", lineage.get("sourceCommit"));
        manifestMaterial.put("registryRootHash", registryRoot);
        manifestMaterial.put("sourceLineageGraphHash", declaredGraphHash);
        manifestMaterial.put("activeRuntimeGraphHash", activeGraphHash);
        manifestMaterial.put("productionEntrypoint", productionEntrypoint);
        manifestMaterial.put("defaultRuntimeEligibility", "DENY");
        manifestMaterial.put("unknownRuntimeNode", "BLOCK");
        manifestMaterial.put("compatibilityDefault", "DENY");
        manifestMaterial.put("files", new ArrayList<>(activeFileNodes));
        String manifestHash = Hashing.canonicalHash(manifestMaterial);
        LinkedHashMap<String, Object> manifest = new LinkedHashMap<>(manifestMaterial);
        manifest.put("manifestHash", manifestHash);

        LinkedHashMap<String, Object> reportMaterial = new LinkedHashMap<>();
        reportMaterial.put("schema", "v24.phase1_verification.v1");
        reportMaterial.put("version", VERSION);
        reportMaterial.put("verified", true);
        reportMaterial.put("enforcementMode", "SHADOW");
        reportMaterial.put("registryRootHash", registryRoot);
        reportMaterial.put("lineageGraphHash", declaredGraphHash);
        reportMaterial.put("activeRuntimeGraphHash", activeGraphHash);
        reportMaterial.put("activeRuntimeManifestHash", manifestHash);
        reportMaterial.put("activeRuntimeFileCount", activeFileNodes.size());
        reportMaterial.put("productionEntrypoint", productionEntrypoint);
        reportMaterial.put("defaultRuntimeEligibility", "DENY");
        reportMaterial.put("legacyOutsideActiveGraphRuntimeEligible", false);
        reportMaterial.put("pythonRuntimeReplaced", false);
        reportMaterial.put("javaAuthority", "shadow_identity_and_admission_only");
        reportMaterial.put("phaseCoverage", List.of("V24.0_BASELINE", "V24.1_REGISTRY", "V24.2_HASH_LINEAGE", "V24.3_RUNTIME_EXCLUSIVITY", "V24.4_BUILD_LOCK", "V24.5_RUNTIME_ADMISSION"));
        String reportHash = Hashing.canonicalHash(reportMaterial);
        LinkedHashMap<String, Object> report = new LinkedHashMap<>(reportMaterial);
        report.put("verificationHash", reportHash);

        Files.createDirectories(outputDir);
        writeJson(outputDir.resolve("active-runtime-graph.json"), activeGraph);
        writeJson(outputDir.resolve("active-runtime-manifest.json"), manifest);
        writeJson(outputDir.resolve("phase1-verification-report.json"), report);
        System.out.println(Json.canonical(report));
    }

    private static void admit(Map<String, String> options) throws Exception {
        Path root = path(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Path candidate = resolve(root, required(options, "candidate")).toAbsolutePath().normalize();
        Path manifestPath = resolve(root, options.getOrDefault("manifest", "dist/v24-java-phase1/active-runtime-manifest.json"));
        Path output = resolve(root, options.getOrDefault("output", "dist/v24-java-phase1/runtime-admission-report.json"));
        Map<String, Object> manifest = readObject(manifestPath);
        String expectedManifestHash = text(manifest.get("manifestHash"));
        LinkedHashMap<String, Object> material = new LinkedHashMap<>(manifest);
        material.remove("manifestHash");
        require(expectedManifestHash.equals(Hashing.canonicalHash(material)), "active_runtime_manifest_hash_mismatch");

        Map<String, String> expected = new LinkedHashMap<>();
        for (Object raw : Json.array(manifest.get("files"))) {
            Map<String, Object> file = Json.object(raw);
            String relative = safeRelative(text(file.get("path")));
            expected.put(relative, text(file.get("sha256")));
        }

        Map<String, String> actual = new LinkedHashMap<>();
        require(Files.isDirectory(candidate), "runtime_candidate_missing:" + candidate);
        try (Stream<Path> stream = Files.walk(candidate)) {
            for (Path file : stream.filter(Files::isRegularFile).sorted().toList()) {
                String relative = candidate.relativize(file).toString().replace('\\', '/');
                actual.put(relative, Hashing.fileHash(file));
            }
        }
        require(expected.keySet().equals(actual.keySet()), "runtime_candidate_file_set_mismatch:missing=" + difference(expected.keySet(), actual.keySet()) + ":extra=" + difference(actual.keySet(), expected.keySet()));
        for (Map.Entry<String, String> entry : expected.entrySet()) {
            require(entry.getValue().equals(actual.get(entry.getKey())), "runtime_candidate_hash_mismatch:" + entry.getKey());
        }

        LinkedHashMap<String, Object> reportMaterial = new LinkedHashMap<>();
        reportMaterial.put("schema", "v24.runtime_admission_report.v1");
        reportMaterial.put("version", VERSION);
        reportMaterial.put("verified", true);
        reportMaterial.put("manifestHash", expectedManifestHash);
        reportMaterial.put("candidateFileCount", actual.size());
        reportMaterial.put("exactFileSetRequired", true);
        reportMaterial.put("unknownRuntimeNode", "BLOCK");
        reportMaterial.put("retiredOrUnregisteredRuntimeAllowed", false);
        String admissionHash = Hashing.canonicalHash(reportMaterial);
        LinkedHashMap<String, Object> report = new LinkedHashMap<>(reportMaterial);
        report.put("admissionHash", admissionHash);
        Files.createDirectories(output.getParent());
        writeJson(output, report);
        System.out.println(Json.canonical(report));
    }

    private static void selfTest() {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("z", 1L);
        LinkedHashMap<String, Object> nested = new LinkedHashMap<>();
        nested.put("中", "文");
        nested.put("a", true);
        value.put("a", nested);
        String actual = Json.canonical(value);
        String expected = "{\"a\":{\"a\":true,\"中\":\"文\"},\"z\":1}";
        require(expected.equals(actual), "canonical_json_self_test_failed:" + actual);
        require(Hashing.canonicalHash(Json.parse(actual)).equals(Hashing.canonicalHash(value)), "canonical_hash_roundtrip_failed");
        System.out.println("{\"verified\":true,\"version\":\"" + VERSION + "\"}");
    }

    private static Map<String, String> options(String[] args) {
        LinkedHashMap<String, String> result = new LinkedHashMap<>();
        for (int i = 1; i < args.length; i++) {
            String key = args[i];
            if (!key.startsWith("--") || i + 1 >= args.length) fail("invalid_option:" + key);
            result.put(key.substring(2), args[++i]);
        }
        return result;
    }

    private static String required(Map<String, String> options, String name) {
        String value = options.get(name);
        if (value == null || value.isBlank()) fail("required_option_missing:--" + name);
        return value;
    }

    private static Path path(String raw) { return Paths.get(raw); }

    private static Path resolve(Path root, String raw) {
        Path value = Paths.get(raw);
        return value.isAbsolute() ? value.normalize() : root.resolve(value).normalize();
    }

    private static Map<String, Object> readObject(Path path) throws IOException {
        require(Files.isRegularFile(path), "json_file_missing:" + path);
        return Json.object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
    }

    private static void writeJson(Path path, Object value) throws IOException {
        Files.createDirectories(path.getParent());
        Files.writeString(path, Json.canonical(value) + "\n", StandardCharsets.UTF_8);
    }

    private static String safeRelative(String raw) {
        if (raw == null || raw.isBlank()) fail("runtime_path_blank");
        Path path = Paths.get(raw).normalize();
        if (path.isAbsolute() || path.startsWith("..")) fail("unsafe_runtime_path:" + raw);
        return path.toString().replace('\\', '/');
    }

    private static String text(Object value) { return value == null ? "" : String.valueOf(value); }

    private static Set<String> difference(Set<String> left, Set<String> right) {
        LinkedHashSet<String> result = new LinkedHashSet<>(left);
        result.removeAll(right);
        return result;
    }

    private static void require(boolean condition, String message) {
        if (!condition) fail(message);
    }

    private static void fail(String message) {
        throw new IllegalStateException(message);
    }
}
