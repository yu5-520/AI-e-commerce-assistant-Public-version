package com.zcentury.v24;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** V25.3-V25.5 verifier: exact field -> alias/structured -> vector/graph supplement. */
public final class V25Phase2Main {
    private static final String VERSION = "25.5.0-phase2";
    private static final String SOURCE_A = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private static final String SOURCE_B = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    private static final String SOURCE_C = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";

    private V25Phase2Main() {}

    public static void main(String[] args) throws Exception {
        Map<String, String> options = options(args);
        Path root = Paths.get(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Path evidencePath = resolve(root, options.getOrDefault("evidence", "dist/v25-phase2/retrieval-baseline-evidence.json"));
        Path policyPath = resolve(root, options.getOrDefault("policy", "governance/v25/phase2-retrieval-authority-policy.json"));
        Path fieldsPath = resolve(root, options.getOrDefault("fields", "governance/v25/rag-field-registry-v25.json"));
        Path domainsPath = resolve(root, options.getOrDefault("domains", "governance/v25/knowledge-distribution-domains-v25.json"));
        Path aliasesPath = resolve(root, options.getOrDefault("aliases", "governance/v25/rag-alias-registry-v25.json"));
        Path structuredPath = resolve(root, options.getOrDefault("structured", "governance/v25/rag-structured-filter-contract-v25.json"));
        Path graphPath = resolve(root, options.getOrDefault("graph", "governance/v25/knowledge-graph-contract-v25.json"));
        Path output = resolve(root, options.getOrDefault("output", "dist/v25-phase2/phase2-verification-report.json"));

        Map<String, Object> evidence = readObject(evidencePath);
        Map<String, Object> policy = readObject(policyPath);
        Map<String, Object> fields = readObject(fieldsPath);
        Map<String, Object> domains = readObject(domainsPath);
        Map<String, Object> aliases = readObject(aliasesPath);
        Map<String, Object> structuredContract = readObject(structuredPath);
        Map<String, Object> graphContract = readObject(graphPath);

        verifyEvidence(evidence);
        V25RetrievalAuthority.verifyPolicy(policy);
        Map<String, Map<String, Object>> domainIndex = V25KnowledgeDomainAuthority.verifyAndIndex(domains);
        Map<String, Map<String, Object>> fieldIndex = V25KnowledgeRegistry.verifyAndIndex(fields, domainIndex);
        Map<String, String> aliasIndex = V25RetrievalAuthority.verifyAliasIndex(aliases, fieldIndex);
        Set<String> structuredKeys = V25RetrievalAuthority.verifyStructuredKeys(structuredContract);
        Set<String> edgeTypes = V25RetrievalAuthority.verifyGraphContract(graphContract);

        List<Map<String, Object>> records = new ArrayList<>();
        records.add(record(fieldIndex, "metric.ctr.interpretation", "REC_CTR_001", "src://ops/ctr-guide", SOURCE_A,
            Map.of("platform", "天猫", "category", "服饰"), "CTR需与曝光结构、点击成本及转化联动判断。"));
        records.add(record(fieldIndex, "action.roas.scale.strategy", "REC_ROAS_TMALL", "src://ops/roas-scale/tmall", SOURCE_B,
            Map.of("platform", "天猫", "actionFamily", "paid_scale"), "满足利润与转化边界后分阶段放量。"));
        records.add(record(fieldIndex, "action.roas.scale.strategy", "REC_ROAS_JD", "src://ops/roas-scale/jd", SOURCE_C,
            Map.of("platform", "京东", "actionFamily", "paid_scale"), "京东场景按成交与毛利联合验证后扩量。"));
        records.add(record(fieldIndex, "diagnosis.cross_metric.patterns", "REC_CROSS_001", "src://ops/cross-metric", SOURCE_C,
            Map.of("category", "default"), "点击率下降需要结合自然流量、转化率及商品生命周期判断。"));

        Map<String, Object> direct = V25RetrievalAuthority.retrieve(
            Map.of("field", "metric.ctr.interpretation"),
            fieldIndex, aliasIndex, structuredKeys, edgeTypes, graphContract,
            records, List.of(), List.of()
        );
        require("EXACT_FIELD".equals(text(direct.get("retrievalMode"))), "exact_field_retrieval_failed");
        require(!containsAttempt(direct, "VECTOR_SUPPLEMENT"), "vector_must_not_run_after_exact_hit");

        Map<String, Object> alias = V25RetrievalAuthority.retrieve(
            Map.of("field", "点击率解释"),
            fieldIndex, aliasIndex, structuredKeys, edgeTypes, graphContract,
            records, List.of(), List.of()
        );
        require("ALIAS_TO_CANONICAL_FIELD".equals(text(alias.get("resolutionMode"))), "alias_canonicalization_failed");
        require("EXACT_FIELD".equals(text(alias.get("retrievalMode"))), "alias_then_exact_failed");

        Map<String, Object> structured = V25RetrievalAuthority.retrieve(
            Map.of("field", "action.roas.scale.strategy", "filters", Map.of("platform", "天猫")),
            fieldIndex, aliasIndex, structuredKeys, edgeTypes, graphContract,
            records, List.of(), List.of()
        );
        require("STRUCTURED_FILTER".equals(text(structured.get("retrievalMode"))), "structured_filter_retrieval_failed");
        require("REC_ROAS_TMALL".equals(firstRecordId(structured)), "structured_filter_wrong_record");
        require(!containsAttempt(structured, "VECTOR_SUPPLEMENT"), "vector_must_not_run_after_structured_hit");

        List<Map<String, Object>> vectorCandidates = List.of(
            vectorCandidate(fieldIndex, "experience.negative.risk", "VEC_NEG_001", "src://experience/negative/001", SOURCE_A, 0.91)
        );
        Map<String, Object> vector = V25RetrievalAuthority.retrieve(
            Map.of("field", "experience.negative.risk"),
            fieldIndex, aliasIndex, structuredKeys, edgeTypes, graphContract,
            records, vectorCandidates, List.of()
        );
        require("VECTOR_SUPPLEMENT".equals(text(vector.get("retrievalMode"))), "vector_supplement_failed");
        require(Boolean.TRUE.equals(vector.get("supplemental")), "vector_result_must_be_supplemental");
        require(containsAttempt(vector, "EXACT_FIELD") && containsAttempt(vector, "STRUCTURED_FILTER"), "vector_ran_before_deterministic_layers");

        List<Map<String, Object>> graphEdges = List.of(
            Map.of(
                "edgeType", "FIELD_RELATED_TO_FIELD",
                "fromCanonicalField", "metric.ctr.related_metrics",
                "toCanonicalField", "diagnosis.cross_metric.patterns"
            )
        );
        Map<String, Object> graph = V25RetrievalAuthority.retrieve(
            Map.of("field", "metric.ctr.related_metrics", "relationshipRequired", true),
            fieldIndex, aliasIndex, structuredKeys, edgeTypes, graphContract,
            records, List.of(), graphEdges
        );
        require("GRAPH_SUPPLEMENT".equals(text(graph.get("retrievalMode"))), "graph_supplement_failed");
        require(Boolean.TRUE.equals(graph.get("supplemental")), "graph_result_must_be_supplemental");
        require(containsAttempt(graph, "VECTOR_SUPPLEMENT"), "graph_must_wait_for_scoped_vector_stage");

        Map<String, Object> insufficient = V25RetrievalAuthority.retrieve(
            Map.of("field", "company.sop.historical_cases"),
            fieldIndex, aliasIndex, structuredKeys, edgeTypes, graphContract,
            records, List.of(), List.of()
        );
        require("INSUFFICIENT_EVIDENCE".equals(text(insufficient.get("retrievalMode"))), "insufficient_evidence_not_exposed");
        require("INSUFFICIENT".equals(text(insufficient.get("decision"))), "insufficient_decision_invalid");

        boolean unknownAliasBlocked = false;
        try {
            V25RetrievalAuthority.retrieve(
                Map.of("field", "permission.operator.execute"),
                fieldIndex, aliasIndex, structuredKeys, edgeTypes, graphContract,
                records, List.of(), List.of()
            );
        } catch (IllegalArgumentException expected) {
            unknownAliasBlocked = expected.getMessage().startsWith("unknown_rag_field_or_alias:");
        }
        require(unknownAliasBlocked, "system_contract_alias_must_block");

        boolean unknownFilterBlocked = false;
        try {
            V25RetrievalAuthority.retrieve(
                Map.of("field", "action.roas.scale.strategy", "filters", Map.of("unregisteredGuess", "x")),
                fieldIndex, aliasIndex, structuredKeys, edgeTypes, graphContract,
                records, List.of(), List.of()
            );
        } catch (IllegalArgumentException expected) {
            unknownFilterBlocked = expected.getMessage().startsWith("unknown_structured_filter:");
        }
        require(unknownFilterBlocked, "unknown_structured_filter_must_block");

        boolean vectorProofBlocked = false;
        try {
            Map<String, Object> invalid = vectorCandidate(
                fieldIndex, "experience.negative.risk", "VEC_BAD_001", "src://experience/negative/bad", SOURCE_A, 0.99
            );
            invalid.put("routeProofAccepted", false);
            V25RetrievalAuthority.retrieve(
                Map.of("field", "experience.negative.risk"),
                fieldIndex, aliasIndex, structuredKeys, edgeTypes, graphContract,
                records, List.of(invalid), List.of()
            );
        } catch (IllegalStateException expected) {
            vectorProofBlocked = expected.getMessage().startsWith("vector_candidate_route_proof_missing:");
        }
        require(vectorProofBlocked, "vector_without_route_proof_must_block");

        boolean graphSystemTargetBlocked = false;
        try {
            List<Map<String, Object>> invalidEdges = List.of(Map.of(
                "edgeType", "FIELD_RELATED_TO_FIELD",
                "fromCanonicalField", "metric.ctr.related_metrics",
                "toCanonicalField", "permission.operator.execute"
            ));
            V25RetrievalAuthority.retrieve(
                Map.of("field", "metric.ctr.related_metrics", "relationshipRequired", true),
                fieldIndex, aliasIndex, structuredKeys, edgeTypes, graphContract,
                records, List.of(), invalidEdges
            );
        } catch (IllegalStateException expected) {
            graphSystemTargetBlocked = expected.getMessage().startsWith("graph_target_system_contract_forbidden:");
        }
        require(graphSystemTargetBlocked, "graph_system_contract_target_must_block");

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v25.phase2_verification.v1");
        material.put("version", VERSION);
        material.put("verified", true);
        material.put("enforcementMode", "SHADOW");
        material.put("retrievalAuthority", "JAVA_SHADOW_FIELD_FIRST_RETRIEVAL");
        material.put("fieldDirectAuthority", "JAVA_SHADOW_EXACT_FIELD");
        material.put("aliasStructuredAuthority", "JAVA_SHADOW_ALIAS_STRUCTURED");
        material.put("vectorGraphAuthority", "JAVA_SHADOW_SUPPLEMENT_ADMISSION");
        material.put("registeredAliasCount", aliasIndex.size());
        material.put("structuredFilterKeyCount", structuredKeys.size());
        material.put("graphEdgeTypeCount", edgeTypes.size());
        material.put("exactStopsSemanticSearch", !containsAttempt(direct, "VECTOR_SUPPLEMENT"));
        material.put("structuredStopsSemanticSearch", !containsAttempt(structured, "VECTOR_SUPPLEMENT"));
        material.put("aliasCanonicalizationVerified", true);
        material.put("vectorRunsOnlyAfterDeterministicLayers", true);
        material.put("vectorRouteProofRequired", vectorProofBlocked);
        material.put("graphRequiresVectorStage", true);
        material.put("graphSystemTargetBlocked", graphSystemTargetBlocked);
        material.put("unknownAliasBlocked", unknownAliasBlocked);
        material.put("unknownStructuredFilterBlocked", unknownFilterBlocked);
        material.put("insufficientEvidenceExposed", true);
        material.put("retrievalMayCreateSystemFact", false);
        material.put("productionAgentInputsUnchanged", true);
        material.put("productionRagWriterUnchanged", true);
        material.put("productionRetrievalCutoverEnabled", false);
        material.put("phaseCoverage", List.of(
            "V25.3_EXACT_FIELD_RETRIEVAL",
            "V25.4_ALIAS_AND_STRUCTURED_RETRIEVAL",
            "V25.5_VECTOR_AND_GRAPH_SUPPLEMENT"
        ));
        material.put("pythonEvidenceHash", evidence.get("evidenceHash"));
        material.put("policyHash", Hashing.canonicalHash(policy));
        material.put("fieldRegistryHash", Hashing.canonicalHash(fields));
        material.put("domainRegistryHash", Hashing.canonicalHash(domains));
        material.put("aliasRegistryHash", Hashing.canonicalHash(aliases));
        material.put("structuredContractHash", Hashing.canonicalHash(structuredContract));
        material.put("graphContractHash", Hashing.canonicalHash(graphContract));
        material.put("sampleExactResult", direct);
        material.put("sampleStructuredResult", structured);
        material.put("sampleVectorResult", vector);
        material.put("sampleGraphResult", graph);
        material.put("sampleInsufficientResult", insufficient);

        String verificationHash = Hashing.canonicalHash(material);
        LinkedHashMap<String, Object> report = new LinkedHashMap<>(material);
        report.put("verificationHash", verificationHash);
        Files.createDirectories(output.getParent());
        Files.writeString(output, Json.canonical(report) + "\n", StandardCharsets.UTF_8);
        System.out.println(Json.canonical(report));
    }

    private static Map<String, Object> record(
        Map<String, Map<String, Object>> fieldIndex,
        String canonical,
        String recordId,
        String sourceRef,
        String sourceHash,
        Map<String, Object> attributes,
        String content
    ) {
        Map<String, Object> field = fieldIndex.get(canonical);
        require(field != null, "fixture_field_missing:" + canonical);
        LinkedHashMap<String, Object> record = new LinkedHashMap<>();
        record.put("recordId", recordId);
        record.put("canonicalField", canonical);
        record.put("fieldHash", field.get("fieldHash"));
        record.put("domains", field.get("domains"));
        record.put("sourceRef", sourceRef);
        record.put("sourceHash", sourceHash);
        record.put("attributes", attributes);
        record.put("content", content);
        return record;
    }

    private static Map<String, Object> vectorCandidate(
        Map<String, Map<String, Object>> fieldIndex,
        String canonical,
        String recordId,
        String sourceRef,
        String sourceHash,
        double score
    ) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>(record(
            fieldIndex, canonical, recordId, sourceRef, sourceHash, Map.of(), "semantic supplement"
        ));
        result.put("score", score);
        result.put("routeProofAccepted", true);
        result.put("routeHash", "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd");
        return result;
    }

    private static boolean containsAttempt(Map<String, Object> result, String value) {
        return V25KnowledgeRegistry.strings(result.get("attemptedLayers")).contains(value);
    }

    private static String firstRecordId(Map<String, Object> result) {
        List<Object> matches = Json.array(result.get("matches"));
        return matches.isEmpty() ? "" : text(Json.object(matches.get(0)).get("recordId"));
    }

    private static void verifyEvidence(Map<String, Object> evidence) {
        String declared = text(evidence.get("evidenceHash"));
        LinkedHashMap<String, Object> material = new LinkedHashMap<>(evidence);
        material.remove("evidenceHash");
        require(declared.equals(Hashing.canonicalHash(material)), "retrieval_evidence_hash_mismatch");
        require(Boolean.TRUE.equals(evidence.get("verified")), "retrieval_evidence_not_verified");
        require(Boolean.TRUE.equals(evidence.get("v23ApplicationOwnsRoute")), "v23_route_authority_baseline_missing");
        require(Boolean.TRUE.equals(evidence.get("v23VectorExactRouteOnly")), "v23_vector_scope_baseline_missing");
        require(Boolean.TRUE.equals(evidence.get("v23GraphRequiresScopedVector")), "v23_graph_order_baseline_missing");
        require(Boolean.TRUE.equals(evidence.get("legacyStructuredFilteringDetected")), "structured_filter_baseline_missing");
        require(Boolean.TRUE.equals(evidence.get("phase1FieldFirstOrderDetected")), "phase1_field_order_baseline_missing");
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

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
