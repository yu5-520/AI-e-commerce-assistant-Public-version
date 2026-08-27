package com.zcentury.v24;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** V25.10-V25.12 release gate for versioned knowledge assets. */
public final class V25Phase4Main {
    private V25Phase4Main() {}

    public static void main(String[] args) throws Exception {
        Map<String, String> options = options(args);
        Path root = Paths.get(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Map<String, Object> evidence = read(root, options.getOrDefault("evidence", "dist/v25-phase4/knowledge-runtime-verification.json"));
        Map<String, Object> policy = read(root, options.getOrDefault("policy", "governance/v25/phase4-knowledge-asset-governance-policy.json"));
        Map<String, Object> index = read(root, options.getOrDefault("index-contract", "governance/v25/knowledge-index-contract-v25.json"));
        Path output = resolve(root, options.getOrDefault("output", "dist/v25-phase4/phase4-verification-report.json"));

        verifyEvidence(evidence);
        verifyPolicy(policy);
        verifyIndex(index);

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v25.phase4_verification.v1");
        material.put("version", "25.12.0-phase4");
        material.put("verified", true);
        material.put("enforcementMode", policy.get("enforcementMode"));
        material.put("promotionAuthority", "HUMAN_REVIEW_PLUS_IMMUTABLE_REVISION");
        material.put("lifecycleAuthority", "KNOWLEDGE_LIFECYCLE_SEPARATE_FROM_TASK");
        material.put("indexAuthority", "IMMUTABLE_MANIFEST_PLUS_ATOMIC_HEAD");
        material.put("physicalRagProviderReplaced", object(policy.get("runtimeBoundary")).get("physicalRagProviderReplaced"));
        material.put("newAgentRuntimeIntroduced", object(policy.get("runtimeBoundary")).get("newAgentRuntimeIntroduced"));
        material.put("vectorIndexRequired", index.get("vectorIndexRequired"));
        material.put("pythonEvidenceHash", evidence.get("evidenceHash"));
        material.put("policyHash", Hashing.canonicalHash(policy));
        material.put("indexContractHash", Hashing.canonicalHash(index));
        material.put("phaseCoverage", List.of(
            "V25.10_KNOWLEDGE_PROMOTION_AND_REVIEW_AUDIT",
            "V25.11_KNOWLEDGE_LIFECYCLE_AUTHORITY",
            "V25.12_KNOWLEDGE_INDEX_MANIFEST_AND_HEAD"
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
        require(declared.equals(Hashing.canonicalHash(material)), "phase4_evidence_hash_mismatch");
        require(Boolean.TRUE.equals(evidence.get("verified")), "phase4_runtime_not_verified");
        for (String key : List.of(
            "pendingReviewRetrievalBlocked", "humanApprovalPromotesImmutableRevision",
            "newRevisionSupersedesOldRevision", "oldRevisionPreserved",
            "indexManifestRotatesOnKnowledgeMutation", "retrievalReceiptBindsRevisionAndManifest",
            "headRollbackExact", "headRollbackPinnedAcrossRetrieval",
            "expiredKnowledgeBecomesStale", "staleKnowledgeRetrievalBlocked"
        )) require(Boolean.TRUE.equals(evidence.get(key)), "missing_runtime_evidence:" + key);
        require(Boolean.FALSE.equals(evidence.get("automaticApprovalAllowed")), "automatic_approval_forbidden");
        require(Boolean.FALSE.equals(evidence.get("automaticDeleteAllowed")), "automatic_delete_forbidden");
        require(Boolean.FALSE.equals(evidence.get("physicalRagProviderReplaced")), "physical_rag_replacement_forbidden");
        require(Boolean.FALSE.equals(evidence.get("newAgentRuntimeIntroduced")), "new_agent_runtime_forbidden");
    }

    private static void verifyPolicy(Map<String, Object> policy) {
        require("25.12.0".equals(text(policy.get("version"))), "phase4_policy_version_mismatch");
        Map<String, Object> promotion = object(policy.get("promotionAudit"));
        Map<String, Object> lifecycle = object(policy.get("lifecycle"));
        Map<String, Object> boundary = object(policy.get("runtimeBoundary"));
        require(Boolean.TRUE.equals(promotion.get("humanReviewRequired")), "human_review_required");
        require(Boolean.FALSE.equals(promotion.get("automaticApprovalAllowed")), "automatic_approval_forbidden_by_policy");
        require(Boolean.TRUE.equals(promotion.get("immutableRevision")), "immutable_revision_required");
        require(Boolean.FALSE.equals(lifecycle.get("automaticDeleteAllowed")), "automatic_delete_forbidden_by_policy");
        require(Boolean.FALSE.equals(boundary.get("physicalRagProviderReplaced")), "physical_rag_cutover_forbidden");
        require(Boolean.FALSE.equals(boundary.get("newAgentRuntimeIntroduced")), "new_agent_runtime_forbidden_by_policy");
        require(Boolean.TRUE.equals(boundary.get("phase3UnifiedKnowledgeIngressRetained")), "phase3_ingress_must_remain");
    }

    private static void verifyIndex(Map<String, Object> index) {
        require("25.12.0".equals(text(index.get("version"))), "index_contract_version_mismatch");
        require(Boolean.FALSE.equals(index.get("vectorIndexRequired")), "vector_index_not_required");
        require(Boolean.TRUE.equals(object(index.get("manifest")).get("immutable")), "immutable_manifest_required");
        require(Boolean.TRUE.equals(object(index.get("head")).get("atomicSwitch")), "atomic_head_required");
        require(Boolean.TRUE.equals(object(index.get("head")).get("rollbackByHeadSwap")), "head_rollback_required");
        require(Boolean.TRUE.equals(object(index.get("retrievalReceipt")).get("snapshotReuseRequiresCurrentManifest")), "snapshot_manifest_guard_required");
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

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
