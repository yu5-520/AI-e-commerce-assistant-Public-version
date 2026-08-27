package com.zcentury.v24;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** V25.0-V25.2 verifier: knowledge baseline + unified RAG field registry + distribution domains. */
public final class V25Phase1Main {
    private static final String VERSION = "25.2.0-phase1";

    private V25Phase1Main() {}

    public static void main(String[] args) throws Exception {
        Map<String, String> options = options(args);
        Path root = Paths.get(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Path evidencePath = resolve(root, options.getOrDefault("evidence", "dist/v25-phase1/knowledge-baseline-evidence.json"));
        Path policyPath = resolve(root, options.getOrDefault("policy", "governance/v25/phase1-knowledge-authority-policy.json"));
        Path baselinePath = resolve(root, options.getOrDefault("baseline", "governance/v25/knowledge-baseline-v25.json"));
        Path fieldsPath = resolve(root, options.getOrDefault("fields", "governance/v25/rag-field-registry-v25.json"));
        Path domainsPath = resolve(root, options.getOrDefault("domains", "governance/v25/knowledge-distribution-domains-v25.json"));
        Path output = resolve(root, options.getOrDefault("output", "dist/v25-phase1/phase1-verification-report.json"));

        Map<String, Object> evidence = readObject(evidencePath);
        Map<String, Object> policy = readObject(policyPath);
        Map<String, Object> baseline = readObject(baselinePath);
        Map<String, Object> fieldRegistry = readObject(fieldsPath);
        Map<String, Object> domainRegistry = readObject(domainsPath);

        verifyEvidence(evidence);
        verifyPolicy(policy);
        verifyBaseline(baseline, domainRegistry);

        Map<String, Map<String, Object>> domainIndex = V25KnowledgeDomainAuthority.verifyAndIndex(domainRegistry);
        Map<String, Map<String, Object>> fieldIndex = V25KnowledgeRegistry.verifyAndIndex(fieldRegistry, domainIndex);

        Map<String, Object> agent1 = V25KnowledgeRegistry.resolve(
            "metric.ctr.interpretation", fieldIndex, domainIndex
        );
        Map<String, Object> crossMetric = V25KnowledgeRegistry.resolve(
            "diagnosis.cross_metric.patterns", fieldIndex, domainIndex
        );
        Map<String, Object> agent2 = V25KnowledgeRegistry.resolve(
            "action.roas.scale.strategy", fieldIndex, domainIndex
        );
        Map<String, Object> agent3 = V25KnowledgeRegistry.resolve(
            "company.sop.execution_principles", fieldIndex, domainIndex
        );

        require(domainIds(agent1).equals(List.of("rag-domain-operating-diagnosis")), "agent1_domain_resolution_failed");
        require(domainIds(crossMetric).containsAll(List.of("rag-domain-operating-diagnosis", "rag-domain-metric-relation")), "cross_domain_resolution_failed");
        require(domainIds(agent2).equals(List.of("rag-domain-paid-operation")), "agent2_domain_resolution_failed");
        require(domainIds(agent3).equals(List.of("rag-domain-company-sop")), "agent3_domain_resolution_failed");

        boolean unknownFieldBlocked = false;
        try {
            V25KnowledgeRegistry.resolve("permission.operator.execute", fieldIndex, domainIndex);
        } catch (IllegalArgumentException expected) {
            unknownFieldBlocked = expected.getMessage().startsWith("unknown_rag_field:");
        }
        require(unknownFieldBlocked, "unknown_or_system_field_must_block");

        int migrateSources = 0;
        int mixedSources = 0;
        int systemSources = 0;
        for (Object raw : Json.array(baseline.get("inventorySources"))) {
            String classification = text(Json.object(raw).get("classification"));
            if ("MIGRATE_TO_RAG".equals(classification)) migrateSources++;
            else if ("MIXED_SPLIT_REQUIRED".equals(classification)) mixedSources++;
            else if ("SYSTEM_CONTRACT".equals(classification)) systemSources++;
        }

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v25.phase1_verification.v1");
        material.put("version", VERSION);
        material.put("verified", true);
        material.put("enforcementMode", "SHADOW");
        material.put("knowledgeBaselineAuthority", "JAVA_SHADOW_KNOWLEDGE_INVENTORY");
        material.put("ragFieldRegistryAuthority", "JAVA_SHADOW_UNIFIED_RAG_FIELD_REGISTRY");
        material.put("knowledgeDomainAuthority", "JAVA_SHADOW_DISTRIBUTION_DOMAIN_RESOLUTION");
        material.put("inventorySourceCount", Json.array(baseline.get("inventorySources")).size());
        material.put("migrateToRagSourceCount", migrateSources);
        material.put("mixedSplitSourceCount", mixedSources);
        material.put("systemContractSourceCount", systemSources);
        material.put("registeredKnowledgeFieldCount", fieldIndex.size());
        material.put("distributionDomainCount", domainIndex.size());
        material.put("fieldToDomainResolutionVerified", true);
        material.put("crossDomainFieldResolutionVerified", true);
        material.put("unknownKnowledgeFieldBlocked", unknownFieldBlocked);
        material.put("systemContractLeakBlocked", true);
        material.put("onePhysicalKnowledgeStore", object(policy.get("principles")).get("onePhysicalKnowledgeStore"));
        material.put("fieldFirst", object(policy.get("principles")).get("fieldFirst"));
        material.put("distributionDomainBeforeVector", object(policy.get("principles")).get("distributionDomainBeforeVector"));
        material.put("productionAgentInputsUnchanged", true);
        material.put("productionRagWriterUnchanged", true);
        material.put("vectorRetrievalCutoverEnabled", false);
        material.put("phaseCoverage", List.of(
            "V25.0_KNOWLEDGE_BASELINE",
            "V25.1_UNIFIED_RAG_FIELD_REGISTRY",
            "V25.2_KNOWLEDGE_DISTRIBUTION_DOMAIN"
        ));
        material.put("pythonEvidenceHash", evidence.get("evidenceHash"));
        material.put("policyHash", Hashing.canonicalHash(policy));
        material.put("knowledgeBaselineHash", Hashing.canonicalHash(baseline));
        material.put("fieldRegistryHash", Hashing.canonicalHash(fieldRegistry));
        material.put("domainRegistryHash", Hashing.canonicalHash(domainRegistry));
        material.put("sampleAgent1Resolution", agent1);
        material.put("sampleAgent2Resolution", agent2);
        material.put("sampleAgent3Resolution", agent3);

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
        require(declared.equals(Hashing.canonicalHash(material)), "knowledge_baseline_evidence_hash_mismatch");
        require(Boolean.TRUE.equals(evidence.get("verified")), "knowledge_baseline_evidence_not_verified");
        require(Boolean.TRUE.equals(evidence.get("agent1StaticKnowledgeInjectionDetected")), "agent1_static_knowledge_baseline_missing");
        require(Boolean.TRUE.equals(evidence.get("dynamicExperienceCardsDetected")), "dynamic_experience_cards_baseline_missing");
        require(Boolean.TRUE.equals(evidence.get("hashRouteContractDetected")), "hash_route_contract_baseline_missing");
        require(Boolean.TRUE.equals(evidence.get("companyContextStaticDefaultsDetected")), "company_context_baseline_missing");
    }

    private static void verifyPolicy(Map<String, Object> policy) {
        require("SHADOW".equals(text(policy.get("enforcementMode"))), "v25_phase1_must_start_shadow");
        Map<String, Object> principles = object(policy.get("principles"));
        require(Boolean.TRUE.equals(principles.get("onePhysicalKnowledgeStore")), "one_unified_knowledge_store_required");
        require(Boolean.FALSE.equals(principles.get("agentOwnsKnowledgeStore")), "agent_must_not_own_rag_store");
        require(Boolean.TRUE.equals(principles.get("fieldFirst")), "field_first_required");
        require(Boolean.TRUE.equals(principles.get("distributionDomainBeforeVector")), "distribution_domain_before_vector_required");
        require(Boolean.FALSE.equals(principles.get("systemContractMayEnterRag")), "system_contract_must_not_enter_rag");
        require("BLOCK".equals(text(principles.get("unknownFieldDecision"))), "unknown_field_must_block");
        require("BLOCK".equals(text(principles.get("unknownDomainDecision"))), "unknown_domain_must_block");
        require(Boolean.FALSE.equals(principles.get("vectorRetrievalCutoverEnabled")), "vector_cutover_must_stay_off");
        require(Boolean.TRUE.equals(principles.get("productionAgentInputsUnchanged")), "production_agent_input_boundary_required");
        require(Boolean.TRUE.equals(principles.get("productionRagWriterUnchanged")), "production_rag_writer_boundary_required");
    }

    private static void verifyBaseline(Map<String, Object> baseline, Map<String, Object> domainRegistry) {
        require("v25.knowledge_baseline.v1".equals(text(baseline.get("schema"))), "knowledge_baseline_schema_invalid");
        require(Json.array(baseline.get("inventorySources")).size() >= 7, "knowledge_inventory_too_small");
        Map<String, Map<String, Object>> domainIndex = V25KnowledgeDomainAuthority.verifyAndIndex(domainRegistry);
        for (Object raw : Json.array(baseline.get("inventorySources"))) {
            Map<String, Object> source = Json.object(raw);
            String classification = text(source.get("classification"));
            require(
                classification.equals("MIGRATE_TO_RAG")
                    || classification.equals("MIXED_SPLIT_REQUIRED")
                    || classification.equals("SYSTEM_CONTRACT"),
                "knowledge_inventory_classification_invalid"
            );
            for (String domainId : V25KnowledgeRegistry.strings(source.get("targetDomains"))) {
                V25KnowledgeDomainAuthority.resolve(domainId, domainIndex);
            }
            if ("SYSTEM_CONTRACT".equals(classification)) {
                require(Json.array(source.get("knowledgeSections")).isEmpty(), "system_contract_cannot_be_knowledge_source");
            }
        }
    }

    private static List<String> domainIds(Map<String, Object> resolution) {
        List<String> result = new ArrayList<>();
        for (Object raw : Json.array(resolution.get("domains"))) {
            result.add(text(Json.object(raw).get("domainId")));
        }
        return result;
    }

    private static Map<String, String> options(String[] args) {
        LinkedHashMap<String, String> result = new LinkedHashMap<>();
        for (int i = 0; i < args.length; i++) {
            String key = args[i];
            if (!key.startsWith("--")) continue;
            String value = i + 1 < args.length ? args[++i] : "true";
            result.put(key.substring(2), value);
        }
        return result;
    }

    private static Path resolve(Path root, String value) {
        Path path = Paths.get(value);
        return path.isAbsolute() ? path : root.resolve(path).normalize();
    }

    private static Map<String, Object> readObject(Path path) throws Exception {
        return Json.object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
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
