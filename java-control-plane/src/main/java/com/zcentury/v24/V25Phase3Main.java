package com.zcentury.v24;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** V25.6-V25.9 verifier: composition table plus Agent knowledge ingress migration. */
public final class V25Phase3Main {
    private static final String VERSION = "25.9.0-phase3";

    private V25Phase3Main() {}

    public static void main(String[] args) throws Exception {
        Map<String, String> options = options(args);
        Path root = Paths.get(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Path evidencePath = resolve(root, options.getOrDefault("evidence", "dist/v25-phase3/agent-knowledge-migration-evidence.json"));
        Path policyPath = resolve(root, options.getOrDefault("policy", "governance/v25/phase3-agent-knowledge-migration-policy.json"));
        Path fieldsPath = resolve(root, options.getOrDefault("fields", "governance/v25/rag-field-registry-v25.json"));
        Path domainsPath = resolve(root, options.getOrDefault("domains", "governance/v25/knowledge-distribution-domains-v25.json"));
        Path compositionPath = resolve(root, options.getOrDefault("composition", "governance/v25/knowledge-composition-table-v25.json"));
        Path output = resolve(root, options.getOrDefault("output", "dist/v25-phase3/phase3-verification-report.json"));

        Map<String, Object> evidence = readObject(evidencePath);
        Map<String, Object> policy = readObject(policyPath);
        Map<String, Object> fields = readObject(fieldsPath);
        Map<String, Object> domains = readObject(domainsPath);
        Map<String, Object> table = readObject(compositionPath);

        verifyEvidence(evidence);
        V25KnowledgeCompositionAuthority.verifyPolicy(policy);
        Map<String, Map<String, Object>> domainIndex = V25KnowledgeDomainAuthority.verifyAndIndex(domains);
        Map<String, Map<String, Object>> fieldIndex = V25KnowledgeRegistry.verifyAndIndex(fields, domainIndex);
        Map<String, Map<String, Object>> compositionIndex =
            V25KnowledgeCompositionAuthority.verifyAndIndex(table, fieldIndex);

        Map<String, Object> agent1 = V25KnowledgeCompositionAuthority.compose(
            "Agent1",
            Map.of(
                "metricCodes", List.of("ctr", "conversionRate"),
                "signalFlags", List.of("organic_traffic_decline"),
                "crossMetricRequired", true
            ),
            compositionIndex,
            fieldIndex,
            text(table.get("version"))
        );
        require(hasField(agent1, "metric.ctr.interpretation"), "agent1_ctr_field_missing");
        require(hasField(agent1, "traffic.organic.decline_causes"), "agent1_organic_field_missing");
        require(hasField(agent1, "diagnosis.cross_metric.patterns"), "agent1_cross_metric_field_missing");
        require(!hasField(agent1, "experience.positive.applicability"), "agent1_experience_consumer_leak");

        Map<String, Object> agent2 = V25KnowledgeCompositionAuthority.compose(
            "Agent2",
            Map.of("actionFamily", "roas_scale", "experienceRequired", true),
            compositionIndex,
            fieldIndex,
            text(table.get("version"))
        );
        require(hasField(agent2, "action.roas.scale.strategy"), "agent2_roas_strategy_missing");
        require(hasField(agent2, "experience.positive.applicability"), "agent2_positive_experience_missing");
        require(hasField(agent2, "experience.negative.risk"), "agent2_negative_experience_missing");
        require(!hasField(agent2, "company.sop.execution_principles"), "agent2_sop_consumer_leak");

        Map<String, Object> agent2Activity = V25KnowledgeCompositionAuthority.compose(
            "Agent2",
            Map.of("actionFamily", "activity_apply", "experienceRequired", false),
            compositionIndex,
            fieldIndex,
            text(table.get("version"))
        );
        require(hasField(agent2Activity, "action.platform_activity.strategy"), "agent2_activity_alias_family_mapping_missing");

        Map<String, Object> agent3 = V25KnowledgeCompositionAuthority.compose(
            "Agent3",
            Map.of(
                "customerFacing", true,
                "historicalCaseRequired", true,
                "experienceRequired", true
            ),
            compositionIndex,
            fieldIndex,
            text(table.get("version"))
        );
        require(hasField(agent3, "company.sop.execution_principles"), "agent3_sop_principles_missing");
        require(hasField(agent3, "company.sop.task_timing"), "agent3_task_timing_missing");
        require(hasField(agent3, "brand.expression.style"), "agent3_brand_field_missing");
        require(hasField(agent3, "company.sop.historical_cases"), "agent3_historical_cases_missing");
        require(hasField(agent3, "experience.negative.risk"), "agent3_negative_experience_missing");

        boolean unknownAgentBlocked = false;
        try {
            V25KnowledgeCompositionAuthority.compose(
                "Agent4",
                Map.of(),
                compositionIndex,
                fieldIndex,
                text(table.get("version"))
            );
        } catch (IllegalArgumentException expected) {
            unknownAgentBlocked = expected.getMessage().startsWith("unknown_knowledge_composition_agent:");
        }
        require(unknownAgentBlocked, "unknown_agent_composition_must_block");

        boolean unsupportedPredicateBlocked = false;
        try {
            LinkedHashMap<String, Object> badTable = deepCopy(table);
            List<Object> comps = Json.array(badTable.get("compositions"));
            Map<String, Object> first = Json.object(comps.get(0));
            List<Object> groups = Json.array(first.get("conditionalGroups"));
            Json.object(groups.get(0)).put("when", Map.of("path", "metricCodes", "op", "SEMANTIC_GUESS", "value", "ctr"));
            V25KnowledgeCompositionAuthority.verifyAndIndex(badTable, fieldIndex);
        } catch (IllegalStateException expected) {
            unsupportedPredicateBlocked = expected.getMessage().startsWith("unsupported_composition_predicate:");
        }
        require(unsupportedPredicateBlocked, "semantic_predicate_must_block");

        boolean consumerLeakBlocked = false;
        try {
            LinkedHashMap<String, Object> badTable = deepCopy(table);
            List<Object> comps = Json.array(badTable.get("compositions"));
            Map<String, Object> first = Json.object(comps.get(0));
            List<Object> base = Json.array(first.get("baseFields"));
            base.add(Map.of(
                "canonicalField", "experience.positive.applicability",
                "fieldHash", fieldIndex.get("experience.positive.applicability").get("fieldHash"),
                "role", "OPTIONAL"
            ));
            V25KnowledgeCompositionAuthority.verifyAndIndex(badTable, fieldIndex);
        } catch (IllegalStateException expected) {
            consumerLeakBlocked = expected.getMessage().startsWith("composition_consumer_forbidden:");
        }
        require(consumerLeakBlocked, "cross_agent_consumer_leak_must_block");

        Map<String, Object> principles = object(policy.get("principles"));
        Map<String, Object> runtimeBoundary = object(policy.get("runtimeBoundary"));

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v25.phase3_verification.v1");
        material.put("version", VERSION);
        material.put("verified", true);
        material.put("enforcementMode", policy.get("enforcementMode"));
        material.put("compositionAuthority", "JAVA_RELEASE_GATE");
        material.put("productionKnowledgeIngress", "PYTHON_V25_UNIFIED");
        material.put("registeredCompositionCount", compositionIndex.size());
        material.put("agent1KnowledgeMigrated", evidence.get("agent1KnowledgeMigrated"));
        material.put("agent2KnowledgeMigrated", evidence.get("agent2KnowledgeMigrated"));
        material.put("agent3KnowledgeMigrated", evidence.get("agent3KnowledgeMigrated"));
        material.put("agent1LegacyExperienceDirectReadAllowed", principles.get("agent1LegacyExperienceDirectReadAllowed"));
        material.put("legacyDirectAgentKnowledgeRead", principles.get("legacyDirectAgentKnowledgeRead"));
        material.put("legacyProviderBehindUnifiedAdapter", principles.get("legacyProviderBehindUnifiedAdapter"));
        material.put("physicalRagProviderCutover", principles.get("physicalRagProviderCutover"));
        material.put("newAgent3RuntimeIntroduced", runtimeBoundary.get("newAgent3RuntimeIntroduced"));
        material.put("runtimeEntrypointsUnchanged", Boolean.TRUE.equals(runtimeBoundary.get("onlyKnowledgeProviderReferencesPatched")));
        material.put("unknownAgentBlocked", unknownAgentBlocked);
        material.put("unsupportedPredicateBlocked", unsupportedPredicateBlocked);
        material.put("consumerLeakBlocked", consumerLeakBlocked);
        material.put("retrievalMayCreateSystemFact", principles.get("retrievalMayCreateSystemFact"));
        material.put("insufficientEvidenceMustRemainVisible", principles.get("insufficientEvidenceMustRemainVisible"));
        material.put("phaseCoverage", List.of(
            "V25.6_KNOWLEDGE_COMPOSITION_TABLE",
            "V25.7_AGENT1_KNOWLEDGE_MIGRATION",
            "V25.8_AGENT2_KNOWLEDGE_MIGRATION",
            "V25.9_AGENT3_SOP_KNOWLEDGE_MIGRATION"
        ));
        material.put("pythonEvidenceHash", evidence.get("evidenceHash"));
        material.put("policyHash", Hashing.canonicalHash(policy));
        material.put("compositionTableHash", Hashing.canonicalHash(table));
        material.put("fieldRegistryHash", Hashing.canonicalHash(fields));
        material.put("domainRegistryHash", Hashing.canonicalHash(domains));
        material.put("sampleAgent1Plan", agent1);
        material.put("sampleAgent2Plan", agent2);
        material.put("sampleAgent3Plan", agent3);

        String verificationHash = Hashing.canonicalHash(material);
        LinkedHashMap<String, Object> report = new LinkedHashMap<>(material);
        report.put("verificationHash", verificationHash);
        Files.createDirectories(output.getParent());
        Files.writeString(output, Json.canonical(report) + "\n", StandardCharsets.UTF_8);
        System.out.println(Json.canonical(report));
    }

    private static boolean hasField(Map<String, Object> plan, String canonical) {
        for (Object raw : Json.array(plan.get("fields"))) {
            if (canonical.equals(text(Json.object(raw).get("canonicalField")))) return true;
        }
        return false;
    }

    private static void verifyEvidence(Map<String, Object> evidence) {
        String declared = text(evidence.get("evidenceHash"));
        LinkedHashMap<String, Object> material = new LinkedHashMap<>(evidence);
        material.remove("evidenceHash");
        require(declared.equals(Hashing.canonicalHash(material)), "phase3_evidence_hash_mismatch");
        require(Boolean.TRUE.equals(evidence.get("verified")), "phase3_python_evidence_not_verified");
        require(Boolean.TRUE.equals(evidence.get("agent1KnowledgeMigrated")), "agent1_migration_evidence_missing");
        require(Boolean.TRUE.equals(evidence.get("agent2KnowledgeMigrated")), "agent2_migration_evidence_missing");
        require(Boolean.TRUE.equals(evidence.get("agent3KnowledgeMigrated")), "agent3_migration_evidence_missing");
        require(Boolean.TRUE.equals(evidence.get("bootstrapInstallsV25AfterV22")), "v25_bootstrap_order_evidence_missing");
        require(Boolean.TRUE.equals(evidence.get("agent2DirectRagContextRemoved")), "agent2_direct_rag_context_removal_evidence_missing");
        require(Boolean.FALSE.equals(evidence.get("newAgent3RuntimeIntroduced")), "new_agent3_runtime_evidence_forbidden");
    }

    private static LinkedHashMap<String, Object> deepCopy(Map<String, Object> source) {
        return new LinkedHashMap<>(Json.object(Json.parse(Json.canonical(source))));
    }

    private static Map<String, Object> readObject(Path path) throws Exception {
        require(Files.isRegularFile(path), "required_file_missing:" + path);
        return Json.object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
    }

    private static Path resolve(Path root, String value) {
        Path path = Paths.get(value);
        return path.isAbsolute() ? path : root.resolve(path).normalize();
    }

    private static Map<String, String> options(String[] args) {
        LinkedHashMap<String, String> result = new LinkedHashMap<>();
        for (int index = 0; index < args.length; index++) {
            String key = args[index];
            if (!key.startsWith("--") || index + 1 >= args.length) continue;
            result.put(key.substring(2), args[++index]);
        }
        return result;
    }

    private static Map<String, Object> object(Object value) {
        return value instanceof Map<?, ?> ? Json.object(value) : Map.of();
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
