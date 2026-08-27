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

/** V24.6-V24.8 shadow verifier: Mapping + Unified Gate + Task/State. */
public final class Phase2Main {
    private static final String VERSION = "24.8.0-phase2.1";

    private Phase2Main() {}

    public static void main(String[] args) throws Exception {
        Map<String, String> options = options(args);
        Path root = Paths.get(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Path evidencePath = resolve(root, options.getOrDefault("evidence", "dist/v24-java-phase2/python-shadow-evidence.json"));
        Path policyPath = resolve(root, options.getOrDefault("policy", "governance/v24/phase2-authority-policy.json"));
        Path gatesPath = resolve(root, options.getOrDefault("gates", "governance/v24/unified-gate-definitions.json"));
        Path output = resolve(root, options.getOrDefault("output", "dist/v24-java-phase2/phase2-verification-report.json"));

        Map<String, Object> evidence = readObject(evidencePath);
        Map<String, Object> policy = readObject(policyPath);
        Map<String, Object> gateDefinitions = readObject(gatesPath);
        verifyEvidenceHash(evidence);
        verifyPolicy(policy);

        int mappingCount = verifyMappingVectors(evidence);
        GateEngine gateEngine = new GateEngine(gateDefinitions);
        int gateCount = verifyGateVectors(evidence, gateEngine);
        int taskCount = verifyTaskVectors(evidence, gateEngine);

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v24.phase2_verification.v1");
        material.put("version", VERSION);
        material.put("verified", true);
        material.put("enforcementMode", "SHADOW");
        material.put("mappingVectorCount", mappingCount);
        material.put("gateVectorCount", gateCount);
        material.put("taskStateVectorCount", taskCount);
        material.put("mappingAuthority", "JAVA_SHADOW_REPRODUCED");
        material.put("gateAuthority", "JAVA_SHADOW_DECISION");
        material.put("taskStateAuthority", "JAVA_SHADOW_DECISION_WITH_VERSION_CAS");
        material.put("unknownGate", "BLOCK");
        material.put("unknownTaskState", "BLOCK");
        material.put("terminalReopen", "BLOCK");
        material.put("canonicalHashAlgorithm", "SHA-256");
        material.put("legacyPipelineGateHash", "SHA-1_READ_ONLY_COMPATIBILITY");
        material.put("pythonProductionWriteAuthorityUnchanged", true);
        material.put("postgreSqlSourceOfTruthEnabled", false);
        material.put("phaseCoverage", List.of("V24.6_MAPPING", "V24.7_UNIFIED_GATE", "V24.8_TASK_STATE"));
        material.put("pythonEvidenceHash", evidence.get("evidenceHash"));
        material.put("gateDefinitionHash", Hashing.canonicalHash(gateDefinitions));
        material.put("policyHash", Hashing.canonicalHash(policy));
        String verificationHash = Hashing.canonicalHash(material);
        LinkedHashMap<String, Object> report = new LinkedHashMap<>(material);
        report.put("verificationHash", verificationHash);
        Files.createDirectories(output.getParent());
        Files.writeString(output, Json.canonical(report) + "\n", StandardCharsets.UTF_8);
        System.out.println(Json.canonical(report));
    }

    private static int verifyMappingVectors(Map<String, Object> evidence) {
        int count = 0;
        for (Object raw : Json.array(evidence.get("mappingVectors"))) {
            Map<String, Object> vector = Json.object(raw);
            Map<String, Object> input = Json.object(vector.get("input"));
            Map<String, Object> expected = Json.object(vector.get("expected"));
            String dataVersion = text(vector.get("dataVersion"));
            Map<String, Object> actual = CanonicalProductMapper.build(input, dataVersion.isBlank() ? null : dataVersion);
            String actualCanonical = Json.canonical(actual);
            String expectedCanonical = Json.canonical(expected);
            require(actualCanonical.equals(expectedCanonical), "mapping_vector_mismatch:" + text(vector.get("name")) + "\nexpected=" + expectedCanonical + "\nactual=" + actualCanonical);
            require(text(expected.get("productSnapshotHash")).equals(text(actual.get("productSnapshotHash"))), "mapping_snapshot_hash_mismatch:" + text(vector.get("name")));
            require(text(vector.get("expectedCanonicalHash")).equals(Hashing.canonicalHash(actual)), "mapping_canonical_hash_mismatch:" + text(vector.get("name")));
            count++;
        }
        require(count > 0, "mapping_vectors_required");
        return count;
    }

    private static int verifyGateVectors(Map<String, Object> evidence, GateEngine engine) {
        int count = 0;
        for (Object raw : Json.array(evidence.get("gateVectors"))) {
            Map<String, Object> vector = Json.object(raw);
            String gateId = text(vector.get("gateId"));
            Map<String, Object> input = Json.object(vector.get("input"));
            Map<String, Object> result = engine.evaluate(gateId, input);
            require(text(vector.get("expectedDecision")).equals(text(result.get("decision"))), "gate_vector_mismatch:" + text(vector.get("name")) + ":" + result);
            require(text(result.get("gateDecisionHash")).startsWith("sha256:"), "gate_decision_hash_required:" + gateId);
            count++;
        }
        Map<String, Object> unknown = engine.evaluate("UNREGISTERED_GATE", Map.of("x", 1L));
        require("BLOCK".equals(text(unknown.get("decision"))), "unknown_gate_must_block");
        require(count > 0, "gate_vectors_required");
        return count;
    }

    private static int verifyTaskVectors(Map<String, Object> evidence, GateEngine gateEngine) {
        Map<String, Object> taskState = Json.object(evidence.get("taskState"));
        TaskStateAuthority.assertPythonMatrix(taskState);
        int count = 0;
        for (Object raw : Json.array(taskState.get("vectors"))) {
            Map<String, Object> vector = Json.object(raw);
            long currentVersion = number(vector.get("currentVersion"));
            long expectedVersion = number(vector.get("expectedVersion"));
            Map<String, Object> result = TaskStateAuthority.decide(
                text(vector.get("fromStatus")), text(vector.get("toStatus")), currentVersion, expectedVersion
            );
            require(text(vector.get("expectedDecision")).equals(text(result.get("decision"))), "task_vector_mismatch:" + text(vector.get("name")) + ":" + result);
            if (!"CONFLICT".equals(text(result.get("decision")))) {
                LinkedHashMap<String, Object> gateInput = new LinkedHashMap<>();
                gateInput.put("transitionAllowed", result.get("transitionAllowed"));
                gateInput.put("versionMatch", result.get("versionMatch"));
                Map<String, Object> gate = gateEngine.evaluate("TASK_TRANSITION_ADMISSION", gateInput);
                String expectedGate = "PASS".equals(text(result.get("decision"))) ? "PASS" : "BLOCK";
                require(expectedGate.equals(text(gate.get("decision"))), "task_gate_mismatch:" + text(vector.get("name")));
            }
            count++;
        }
        Map<String, Object> unknown = TaskStateAuthority.decide("UNKNOWN", "处理中", 1L, 1L);
        require("BLOCK".equals(text(unknown.get("decision"))), "unknown_task_state_must_block");
        require(count > 0, "task_vectors_required");
        return count;
    }

    private static void verifyEvidenceHash(Map<String, Object> evidence) {
        String declared = text(evidence.get("evidenceHash"));
        LinkedHashMap<String, Object> material = new LinkedHashMap<>(evidence);
        material.remove("evidenceHash");
        require(declared.equals(Hashing.canonicalHash(material)), "python_evidence_hash_mismatch");
        require("PYTHON_UNCHANGED".equals(text(evidence.get("productionWriteAuthority"))), "python_write_authority_boundary_changed");
    }

    private static void verifyPolicy(Map<String, Object> policy) {
        require("SHADOW".equals(text(policy.get("enforcementMode"))), "phase2_must_start_shadow");
        Map<String, Object> mapping = Json.object(policy.get("mappingAuthority"));
        Map<String, Object> gate = Json.object(policy.get("gateAuthority"));
        Map<String, Object> task = Json.object(policy.get("taskStateAuthority"));
        require("SHADOW".equals(text(mapping.get("mode"))), "mapping_mode_must_shadow");
        require("SHADOW".equals(text(gate.get("mode"))), "gate_mode_must_shadow");
        require("SHADOW".equals(text(task.get("mode"))), "task_mode_must_shadow");
        require(Boolean.TRUE.equals(mapping.get("pythonProductionWriterUnchanged")), "mapping_python_writer_boundary_required");
        require(Boolean.TRUE.equals(task.get("pythonTaskWriteAuthorityUnchanged")), "task_python_writer_boundary_required");
        require(Boolean.FALSE.equals(task.get("postgreSqlSourceOfTruthEnabled")), "postgres_not_enabled_in_phase2_shadow");
        require("BLOCK".equals(text(gate.get("unknownGate"))), "unknown_gate_policy_must_block");
        require("BLOCK".equals(text(task.get("unknownState"))), "unknown_state_policy_must_block");
    }

    private static Map<String, Object> readObject(Path path) throws IOException {
        require(Files.isRegularFile(path), "json_file_missing:" + path);
        return Json.object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
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

    private static long number(Object value) {
        if (!(value instanceof Number number)) throw new IllegalArgumentException("number_required:" + value);
        return number.longValue();
    }

    private static String text(Object value) { return value == null ? "" : String.valueOf(value); }
    private static void require(boolean condition, String message) { if (!condition) throw new IllegalStateException(message); }
}
