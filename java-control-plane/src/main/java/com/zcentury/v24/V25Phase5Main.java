package com.zcentury.v24;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** V25.13-V25.15 release gate for RAG quantification, Eval and Knowledge Center. */
public final class V25Phase5Main {
    private V25Phase5Main() {}

    public static void main(String[] args) throws Exception {
        Map<String, String> options = options(args);
        Path root = Paths.get(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Map<String, Object> evidence = read(root, options.getOrDefault("evidence", "dist/v25-phase5/rag-runtime-verification.json"));
        Map<String, Object> policy = read(root, options.getOrDefault("policy", "governance/v25/phase5-rag-quant-eval-policy.json"));
        Map<String, Object> eval = read(root, options.getOrDefault("eval-contract", "governance/v25/rag-eval-contract-v25.json"));
        Path output = resolve(root, options.getOrDefault("output", "dist/v25-phase5/phase5-verification-report.json"));

        verifyEvidence(evidence);
        verifyPolicy(policy);
        verifyEval(eval);

        Map<String, Object> boundary = object(policy.get("runtimeBoundary"));
        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v25.phase5_verification.v1");
        material.put("version", "25.15.0-phase5");
        material.put("verified", true);
        material.put("enforcementMode", policy.get("enforcementMode"));
        material.put("quantificationAuthority", "RECEIPT_AND_MANIFEST_BOUND_OBSERVABILITY");
        material.put("evalAuthority", "IMMUTABLE_EVALSET_BASE_TARGET_REGRESSION");
        material.put("knowledgeCenterAuthority", "ZH_CN_GOVERNED_PROJECTION");
        material.put("groundTruthMetricsRequireEvalSet", object(policy.get("quantification")).get("groundTruthMetricsRequireEvalSet"));
        material.put("llmJudgeSoleReleaseAuthority", object(policy.get("eval")).get("llmJudgeSoleReleaseAuthority"));
        material.put("directDatabaseMutationAllowed", object(policy.get("knowledgeCenter")).get("directDatabaseMutationAllowed"));
        material.put("activeRevisionInPlaceEditAllowed", object(policy.get("knowledgeCenter")).get("activeRevisionInPlaceEditAllowed"));
        material.put("physicalRagProviderReplaced", boundary.get("physicalRagProviderReplaced"));
        material.put("vectorIndexRequired", boundary.get("vectorIndexRequired"));
        material.put("newAgentRuntimeIntroduced", boundary.get("newAgentRuntimeIntroduced"));
        material.put("phase4KnowledgeGovernanceRetained", boundary.get("phase4KnowledgeGovernanceRetained"));
        material.put("knowledgeMayCreateSystemFact", boundary.get("knowledgeMayCreateSystemFact"));
        material.put("pythonEvidenceHash", evidence.get("evidenceHash"));
        material.put("policyHash", Hashing.canonicalHash(policy));
        material.put("evalContractHash", Hashing.canonicalHash(eval));
        material.put("phaseCoverage", List.of(
            "V25.13_RAG_QUANTIFICATION_AND_RETRIEVAL_OBSERVABILITY",
            "V25.14_RAG_EVAL_AND_REGRESSION_AUTHORITY",
            "V25.15_CHINESE_RAG_KNOWLEDGE_CENTER"
        ));
        String verificationHash = Hashing.canonicalHash(material);
        material.put("verificationHash", verificationHash);
        Files.createDirectories(output.getParent());
        Files.writeString(output, Json.canonical(material) + "\n", StandardCharsets.UTF_8);
        System.out.println(Json.canonical(material));
    }

    private static void verifyEvidence(Map<String, Object> evidence) {
        String declared = text(evidence.get("evidenceHash"));
        LinkedHashMap<String, Object> material = new LinkedHashMap<>(evidence);
        material.remove("evidenceHash");
        require(declared.equals(Hashing.canonicalHash(material)), "phase5_evidence_hash_mismatch");
        require(Boolean.TRUE.equals(evidence.get("verified")), "phase5_runtime_not_verified");
        for (String key : List.of(
            "receiptBoundMetrics", "manifestBoundMetrics", "tamperedReceiptBlocked",
            "metricSnapshotImmutable", "groundTruthMetricsRequireEvalSet",
            "evalSetImmutable", "evalSetVersioned", "evalRunImmutable",
            "baseTargetEvalRequired", "regressionGateBlocksDegradation",
            "staleLeakGateBlocksDegradation", "retrievalAnswerEvalSeparated",
            "chineseKnowledgeCenterRegistered", "phase4KnowledgeGovernanceRetained"
        )) require(Boolean.TRUE.equals(evidence.get(key)), "missing_phase5_runtime_evidence:" + key);
        require(Boolean.FALSE.equals(evidence.get("llmJudgeSoleReleaseAuthority")), "llm_judge_cannot_be_sole_release_authority");
        require(Boolean.FALSE.equals(evidence.get("directDatabaseMutationAllowed")), "knowledge_center_direct_db_mutation_forbidden");
        require(Boolean.FALSE.equals(evidence.get("activeRevisionInPlaceEditAllowed")), "active_revision_in_place_edit_forbidden");
        require(Boolean.FALSE.equals(evidence.get("physicalRagProviderReplaced")), "physical_rag_replacement_forbidden");
        require(Boolean.FALSE.equals(evidence.get("vectorIndexRequired")), "vector_index_not_required");
        require(Boolean.FALSE.equals(evidence.get("newAgentRuntimeIntroduced")), "new_agent_runtime_forbidden");
        require(Boolean.FALSE.equals(evidence.get("knowledgeMayCreateSystemFact")), "knowledge_may_not_create_system_fact");
    }

    private static void verifyPolicy(Map<String, Object> policy) {
        require("v25.phase5_rag_quant_eval_policy.v1".equals(text(policy.get("schema"))), "phase5_policy_schema_mismatch");
        require("25.15.0".equals(text(policy.get("version"))), "phase5_policy_version_mismatch");
        require("VERSIONED_RAG_QUANT_EVAL_KNOWLEDGE_CENTER".equals(text(policy.get("enforcementMode"))), "phase5_enforcement_mode_mismatch");
        Map<String, Object> quant = object(policy.get("quantification"));
        Map<String, Object> eval = object(policy.get("eval"));
        Map<String, Object> center = object(policy.get("knowledgeCenter"));
        Map<String, Object> boundary = object(policy.get("runtimeBoundary"));
        require(Boolean.TRUE.equals(quant.get("receiptBound")), "receipt_bound_quantification_required");
        require(Boolean.TRUE.equals(quant.get("manifestBound")), "manifest_bound_quantification_required");
        require(Boolean.TRUE.equals(quant.get("metricSnapshotImmutable")), "immutable_metric_snapshot_required");
        require(Boolean.TRUE.equals(quant.get("groundTruthMetricsRequireEvalSet")), "evalset_required_for_ground_truth_metrics");
        require(Boolean.TRUE.equals(eval.get("evalSetImmutable")), "immutable_evalset_required");
        require(Boolean.TRUE.equals(eval.get("evalRunImmutable")), "immutable_evalrun_required");
        require(Boolean.TRUE.equals(eval.get("baseTargetRequired")), "base_target_eval_required");
        require(Boolean.FALSE.equals(eval.get("llmJudgeSoleReleaseAuthority")), "llm_judge_sole_release_forbidden");
        require(Boolean.TRUE.equals(eval.get("releaseRegressionGate")), "release_regression_gate_required");
        require("zh-CN".equals(text(center.get("language"))), "knowledge_center_language_mismatch");
        require(Boolean.FALSE.equals(center.get("directDatabaseMutationAllowed")), "direct_db_mutation_forbidden_by_policy");
        require(Boolean.TRUE.equals(center.get("allWritesUseGovernanceAuthority")), "governance_authority_required_for_writes");
        require(Boolean.FALSE.equals(center.get("activeRevisionInPlaceEditAllowed")), "active_revision_in_place_edit_forbidden_by_policy");
        require(Boolean.TRUE.equals(center.get("rollbackUsesIndexHead")), "index_head_rollback_required");
        require(Boolean.FALSE.equals(boundary.get("physicalRagProviderReplaced")), "physical_rag_cutover_forbidden");
        require(Boolean.FALSE.equals(boundary.get("vectorIndexRequired")), "vector_index_not_required_by_phase5");
        require(Boolean.FALSE.equals(boundary.get("newAgentRuntimeIntroduced")), "new_agent_runtime_forbidden_by_policy");
        require(Boolean.TRUE.equals(boundary.get("phase4KnowledgeGovernanceRetained")), "phase4_knowledge_governance_must_remain");
        require(Boolean.TRUE.equals(boundary.get("phase3UnifiedKnowledgeIngressRetained")), "phase3_ingress_must_remain");
        require(Boolean.FALSE.equals(boundary.get("knowledgeMayCreateSystemFact")), "knowledge_cannot_create_system_fact");
    }

    private static void verifyEval(Map<String, Object> eval) {
        require("rag.eval_contract.v1".equals(text(eval.get("schema"))), "eval_contract_schema_mismatch");
        require("25.14.0".equals(text(eval.get("version"))), "eval_contract_version_mismatch");
        Map<String, Object> evalSet = object(eval.get("evalSet"));
        Map<String, Object> evalRun = object(eval.get("evalRun"));
        Map<String, Object> retrieval = object(eval.get("retrievalEval"));
        Map<String, Object> answer = object(eval.get("answerEval"));
        Map<String, Object> regression = object(eval.get("regressionGate"));
        Map<String, Object> boundary = object(eval.get("runtimeBoundary"));
        require(Boolean.TRUE.equals(evalSet.get("immutable")), "evalset_immutable_contract_required");
        require(Boolean.TRUE.equals(evalSet.get("versioned")), "evalset_versioning_required");
        require(Boolean.TRUE.equals(evalRun.get("immutable")), "evalrun_immutable_contract_required");
        require(Boolean.TRUE.equals(retrieval.get("separateFromAnswerEval")), "retrieval_answer_eval_separation_required");
        require(Boolean.TRUE.equals(answer.get("separateFromRetrievalEval")), "answer_retrieval_eval_separation_required");
        require(Boolean.FALSE.equals(answer.get("llmJudgeSoleReleaseAuthority")), "llm_judge_sole_authority_forbidden_by_contract");
        require(Boolean.TRUE.equals(regression.get("baseTargetRequired")), "base_target_regression_contract_required");
        require(number(regression.get("maximumHitAt3Regression")) == 0.03d, "hit_at_3_regression_threshold_mismatch");
        require(number(regression.get("maximumZeroHitRateIncrease")) == 0.02d, "zero_hit_regression_threshold_mismatch");
        require(number(regression.get("maximumStaleLeakCount")) == 0.0d, "stale_leak_threshold_mismatch");
        require(number(regression.get("maximumSupersededLeakCount")) == 0.0d, "superseded_leak_threshold_mismatch");
        require(Boolean.TRUE.equals(regression.get("failClosed")), "eval_regression_must_fail_closed");
        require(Boolean.FALSE.equals(boundary.get("physicalRagProviderReplaced")), "eval_may_not_replace_physical_rag");
        require(Boolean.FALSE.equals(boundary.get("vectorIndexRequired")), "eval_may_not_require_vector_index");
        require(Boolean.FALSE.equals(boundary.get("newAgentRuntimeIntroduced")), "eval_may_not_introduce_agent_runtime");
    }

    private static Map<String, Object> read(Path root, String value) throws Exception {
        Path path = resolve(root, value);
        require(Files.isRegularFile(path), "required_file_missing:" + path);
        return Json.object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
    }

    private static Path resolve(Path root, String value) {
        Path path = Paths.get(value);
        return path.isAbsolute() ? path : root.resolve(path).normalize();
    }

    private static Map<String, String> options(String[] args) {
        LinkedHashMap<String, String> result = new LinkedHashMap<>();
        for (int i = 0; i < args.length; i++) {
            if (args[i].startsWith("--") && i + 1 < args.length) result.put(args[i].substring(2), args[++i]);
        }
        return result;
    }

    private static Map<String, Object> object(Object value) {
        return value instanceof Map<?, ?> ? Json.object(value) : Map.of();
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static double number(Object value) {
        if (value instanceof Number) return ((Number) value).doubleValue();
        try { return Double.parseDouble(text(value)); }
        catch (Exception ignored) { return Double.NaN; }
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
