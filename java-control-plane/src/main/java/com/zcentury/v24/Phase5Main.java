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

/** V24.18-V24.20 shadow verifier: Deployment + Compatibility Authority + Legacy Removal. */
public final class Phase5Main {
    private static final String VERSION = "24.20.0-phase5.1";

    private Phase5Main() {}

    public static void main(String[] args) throws Exception {
        Map<String, String> options = options(args);
        Path root = Paths.get(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Path evidencePath = resolve(root, options.getOrDefault("evidence", "dist/v24-java-phase5/deployment-baseline-evidence.json"));
        Path policyPath = resolve(root, options.getOrDefault("policy", "governance/v24/phase5-deployment-authority-policy.json"));
        Path compatibilityPath = resolve(root, options.getOrDefault("compatibility", "governance/v24/deployment-compatibility-contract-v24.json"));
        Path legacyPath = resolve(root, options.getOrDefault("legacy", "governance/v24/legacy-removal-contract-v24.json"));
        Path output = resolve(root, options.getOrDefault("output", "dist/v24-java-phase5/phase5-verification-report.json"));

        Map<String, Object> evidence = readObject(evidencePath);
        Map<String, Object> policy = readObject(policyPath);
        Map<String, Object> compatibilityContract = readObject(compatibilityPath);
        Map<String, Object> legacyContract = readObject(legacyPath);
        verifyEvidence(evidence);
        verifyPolicy(policy, compatibilityContract, legacyContract);

        Map<String, Object> current = releaseIdentity("current", "db.v1", "22.5.16", capabilities("registry", "lineage", "task", "queue", "frontend"));
        Map<String, Object> candidate1 = releaseIdentity("candidate-1", "db.v2", "22.5.16", capabilities("registry", "lineage", "task", "queue", "frontend", "deployment"));
        Map<String, Object> compatibility1 = CompatibilityAuthority.assess(current, candidate1, compatibilityContract);
        require("COMPATIBLE".equals(text(compatibility1.get("decision"))), "compatible_candidate_rejected:" + compatibility1);
        require(Boolean.TRUE.equals(compatibility1.get("migrationRequired")), "explicit_schema_migration_not_detected");
        require(strings(compatibility1.get("migrationIds")).contains("db-v1-to-v2"), "migration_id_missing");

        Map<String, Object> incompatible = new LinkedHashMap<>(candidate1);
        incompatible.put("releaseHash", Hashing.canonicalHash(Map.of("release", "bad-api")));
        incompatible.put("publicApiVersion", "99.0");
        Map<String, Object> incompatibleDecision = CompatibilityAuthority.assess(current, incompatible, compatibilityContract);
        boolean incompatibleBlocked = "INCOMPATIBLE".equals(text(incompatibleDecision.get("decision")))
            && strings(incompatibleDecision.get("violations")).stream().anyMatch(v -> v.startsWith("EXACT_FIELD_CHANGED:publicApiVersion"));
        require(incompatibleBlocked, "breaking_api_change_not_blocked:" + incompatibleDecision);

        String generation1 = Hashing.canonicalHash(Map.of("generationSeq", 1L, "release", "v24-phase5"));
        DeploymentAuthority deployment = new DeploymentAuthority(1L, generation1);

        Map<String, Object> incompatiblePublish = deployment.publish(0L, generation1, incompatible, incompatibleDecision);
        require("COMPATIBILITY_REJECTED".equals(text(incompatiblePublish.get("decision"))), "deployment_must_reject_incompatible_candidate");
        require(deployment.deploymentVersion() == 0L, "incompatible_candidate_mutated_deployment_head");

        Map<String, Object> deploy1 = deployment.publish(0L, generation1, candidate1, compatibility1);
        require("DEPLOYED".equals(text(deploy1.get("decision"))), "first_deployment_failed:" + deploy1);
        require(deployment.deploymentVersion() == 1L, "deployment_version_1_missing");

        String beforeRead = Hashing.canonicalHash(deployment.debugState());
        Map<String, Object> read1 = deployment.readHead();
        Map<String, Object> read2 = deployment.readHead();
        String afterRead = Hashing.canonicalHash(deployment.debugState());
        boolean deploymentReadPure = beforeRead.equals(afterRead) && read1.equals(read2);
        require(deploymentReadPure, "deployment_head_read_mutated_state");

        Map<String, Object> duplicate = deployment.publish(1L, generation1, candidate1, compatibility1);
        require("NO_CHANGE".equals(text(duplicate.get("decision"))), "duplicate_release_must_no_change");

        Map<String, Object> candidate2 = releaseIdentity("candidate-2", "db.v2", "22.5.16", capabilities("registry", "lineage", "task", "queue", "frontend", "deployment"));
        Map<String, Object> compatibility2 = CompatibilityAuthority.assess(candidate1, candidate2, compatibilityContract);
        require("COMPATIBLE".equals(text(compatibility2.get("decision"))), "same_contract_release_should_be_compatible");
        Map<String, Object> casWinner = deployment.publish(1L, generation1, candidate2, compatibility2);
        Map<String, Object> casLoser = deployment.publish(1L, generation1, candidate2, compatibility2);
        boolean casSingleWinner = "DEPLOYED".equals(text(casWinner.get("decision")))
            && "DEPLOYMENT_VERSION_CONFLICT".equals(text(casLoser.get("decision")))
            && deployment.deploymentVersion() == 2L;
        require(casSingleWinner, "deployment_cas_single_winner_failed");

        Map<String, Object> candidate3 = releaseIdentity("candidate-3", "db.v2", "22.5.16", capabilities("registry", "lineage", "task", "queue", "frontend", "deployment"));
        Map<String, Object> compatibility3 = CompatibilityAuthority.assess(candidate2, candidate3, compatibilityContract);
        String generation2 = Hashing.canonicalHash(Map.of("generationSeq", 2L, "release", "v24-phase5"));
        deployment.rotateGeneration(2L, generation2);
        Map<String, Object> stalePublish = deployment.publish(2L, generation1, candidate3, compatibility3);
        boolean staleGenerationBlocked = "STALE_GENERATION".equals(text(stalePublish.get("decision")))
            && deployment.deploymentVersion() == 2L;
        require(staleGenerationBlocked, "stale_generation_deployment_not_blocked");

        LegacyRemovalAuthority legacy = new LegacyRemovalAuthority();
        legacy.register("python_deployment_writer", "java.deployment.v24", 2, 1, true);
        legacy.register("mutable_repository_working_tree", "sealed.release.root", 0, 0, false);

        Map<String, Object> premature = legacy.remove("python_deployment_writer", true, compatibility2, deployment.readHead());
        boolean prematureRemovalBlocked = "LEGACY_EXECUTION_RIGHTS_REMAIN".equals(text(premature.get("decision")));
        require(prematureRemovalBlocked, "legacy_removed_before_execution_revoke");
        legacy.revokeExecution("python_deployment_writer");
        Map<String, Object> stillReferenced = legacy.remove("python_deployment_writer", true, compatibility2, deployment.readHead());
        require("LEGACY_WRITERS_REMAIN".equals(text(stillReferenced.get("decision"))), "legacy_writer_dependency_not_blocked");
        legacy.detach("python_deployment_writer", 0, 0);
        Map<String, Object> removedWriter = legacy.remove("python_deployment_writer", true, compatibility2, deployment.readHead());
        require("REMOVED".equals(text(removedWriter.get("decision"))), "legacy_writer_not_removed_after_proof");

        Map<String, Object> replacementMissing = legacy.remove("mutable_repository_working_tree", false, compatibility2, deployment.readHead());
        require("REPLACEMENT_NOT_ACTIVE".equals(text(replacementMissing.get("decision"))), "legacy_removed_without_replacement_authority");
        Map<String, Object> removedTree = legacy.remove("mutable_repository_working_tree", true, compatibility2, deployment.readHead());
        require("REMOVED".equals(text(removedTree.get("decision"))), "legacy_tree_not_removed_after_replacement_proof");
        Map<String, Object> restoreAttempt = legacy.restore("python_deployment_writer");
        boolean automaticFallbackForbidden = "RESTORE_FORBIDDEN".equals(text(restoreAttempt.get("decision")));
        require(automaticFallbackForbidden, "removed_legacy_must_not_auto_restore");
        require(legacy.allRemoved(), "legacy_contract_entries_not_fully_removed_in_shadow_model");

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v24.phase5_verification.v1");
        material.put("version", VERSION);
        material.put("verified", true);
        material.put("enforcementMode", "SHADOW");
        material.put("deploymentAuthority", "JAVA_SHADOW_DEPLOYMENT_CAS_GENERATION");
        material.put("compatibilityAuthority", "JAVA_SHADOW_COMPATIBILITY_DEFAULT_DENY");
        material.put("legacyRemovalAuthority", "JAVA_SHADOW_LEGACY_RETIREMENT_NO_FALLBACK");
        material.put("deploymentReadPure", deploymentReadPure);
        material.put("deploymentCasSingleWinner", casSingleWinner);
        material.put("staleGenerationBlocked", staleGenerationBlocked);
        material.put("incompatibleCandidateBlocked", incompatibleBlocked);
        material.put("explicitSchemaMigrationRequired", Boolean.TRUE.equals(compatibility1.get("migrationRequired")));
        material.put("legacyPrematureRemovalBlocked", prematureRemovalBlocked);
        material.put("legacyRemovalAfterProof", legacy.allRemoved());
        material.put("automaticLegacyFallbackForbidden", automaticFallbackForbidden);
        material.put("productionDeploymentWriterUnchanged", true);
        material.put("javaProductionDeploymentCutoverEnabled", false);
        material.put("productionLegacyDeletionByJavaEnabled", false);
        material.put("shellLegacyRetirementStillProduction", true);
        material.put("existingProductionDeploymentAuthority", evidence.get("productionDeploymentAuthority"));
        material.put("existingCompatibilityAuthority", evidence.get("compatibilityAuthority"));
        material.put("existingLegacyRetirementAuthority", evidence.get("legacyRetirementAuthority"));
        material.put("existingCurrentSymlinkCutover", evidence.get("currentSymlinkCutover"));
        material.put("existingRollbackPresent", evidence.get("rollbackPresent"));
        material.put("existingLegacyForbiddenPathRetirement", evidence.get("legacyForbiddenPathRetirement"));
        material.put("existingLegacyWorkingTreeRetirement", evidence.get("legacyWorkingTreeRetirement"));
        material.put("phaseCoverage", List.of("V24.18_DEPLOYMENT", "V24.19_COMPATIBILITY_AUTHORITY", "V24.20_LEGACY_REMOVAL"));
        material.put("pythonEvidenceHash", evidence.get("evidenceHash"));
        material.put("policyHash", Hashing.canonicalHash(policy));
        material.put("compatibilityContractHash", Hashing.canonicalHash(compatibilityContract));
        material.put("legacyContractHash", Hashing.canonicalHash(legacyContract));
        String verificationHash = Hashing.canonicalHash(material);
        LinkedHashMap<String, Object> report = new LinkedHashMap<>(material);
        report.put("verificationHash", verificationHash);
        Files.createDirectories(output.getParent());
        Files.writeString(output, Json.canonical(report) + "\n", StandardCharsets.UTF_8);
        System.out.println(Json.canonical(report));
    }

    private static Map<String, Object> releaseIdentity(String name, String schemaVersion, String publicApiVersion, List<String> capabilities) {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("releaseHash", Hashing.canonicalHash(Map.of("release", name)));
        value.put("sourceCommit", Hashing.canonicalHash(Map.of("commit", name)).substring("sha256:".length(), "sha256:".length() + 40));
        value.put("productVersion", "24.20.0");
        value.put("publicApiVersion", publicApiVersion);
        value.put("stateMachineVersion", "22.5.15");
        value.put("frontendContractVersion", "24");
        value.put("runtimeMode", "single_release_sealed_runtime");
        value.put("pythonVersion", "3.11.9");
        value.put("dataSchemaVersion", schemaVersion);
        value.put("callableAuthorityVersion", "2026.08.10.1");
        value.put("capabilities", capabilities);
        value.put("releaseVerified", true);
        value.put("environmentVerified", true);
        value.put("schemaPrepared", true);
        value.put("runtimeSmokeVerified", true);
        value.put("rollbackPrepared", true);
        value.put("executionExclusivityVerified", true);
        return value;
    }

    private static List<String> capabilities(String... values) {
        return List.of(values);
    }

    private static void verifyEvidence(Map<String, Object> evidence) {
        String declared = text(evidence.get("evidenceHash"));
        LinkedHashMap<String, Object> material = new LinkedHashMap<>(evidence);
        material.remove("evidenceHash");
        require(declared.equals(Hashing.canonicalHash(material)), "deployment_baseline_evidence_hash_mismatch");
        require(Boolean.TRUE.equals(evidence.get("verified")), "deployment_baseline_not_verified");
        require("BASH_SYSTEMD_ROOT".equals(text(evidence.get("productionDeploymentAuthority"))), "production_deployment_authority_mismatch");
        require("BASH_PLUS_PYTHON_ASSERTIONS".equals(text(evidence.get("compatibilityAuthority"))), "compatibility_baseline_mismatch");
        require("BASH_PYTHON_RUNTIME_EXCLUSIVITY_GUARD".equals(text(evidence.get("legacyRetirementAuthority"))), "legacy_retirement_baseline_mismatch");
        require(Boolean.TRUE.equals(evidence.get("currentSymlinkCutover")), "current_symlink_cutover_not_detected");
        require(Boolean.TRUE.equals(evidence.get("rollbackPresent")), "rollback_not_detected");
        require(Boolean.TRUE.equals(evidence.get("legacyForbiddenPathRetirement")), "legacy_forbidden_path_retirement_not_detected");
        require(Boolean.TRUE.equals(evidence.get("legacyWorkingTreeRetirement")), "legacy_working_tree_retirement_not_detected");
        require(Boolean.FALSE.equals(evidence.get("javaDeploymentAuthorityInProduction")), "unexpected_existing_java_deployment_cutover");
    }

    private static void verifyPolicy(
        Map<String, Object> policy,
        Map<String, Object> compatibility,
        Map<String, Object> legacy
    ) {
        require("SHADOW".equals(text(policy.get("enforcementMode"))), "phase5_must_start_shadow");
        Map<String, Object> deploy = object(policy.get("deploymentAuthority"));
        Map<String, Object> compat = object(policy.get("compatibilityAuthority"));
        Map<String, Object> removal = object(policy.get("legacyRemovalAuthority"));
        require("COMPARE_AND_SET".equals(text(deploy.get("publishMode"))), "deployment_publish_must_cas");
        require(Boolean.TRUE.equals(deploy.get("generationFenceRequired")), "deployment_generation_fence_required");
        require(Boolean.TRUE.equals(deploy.get("productionDeploymentWriterUnchanged")), "production_deployment_writer_boundary_required");
        require(Boolean.FALSE.equals(deploy.get("javaProductionDeploymentCutoverEnabled")), "java_deployment_cutover_must_be_disabled");
        require("DENY".equals(text(compat.get("defaultDecision"))), "compatibility_must_default_deny");
        require(Boolean.FALSE.equals(compat.get("implicitFallbackAllowed")), "implicit_compatibility_fallback_forbidden");
        require(Boolean.TRUE.equals(compat.get("explicitMigrationRequired")), "schema_migration_must_be_explicit");
        require(Boolean.TRUE.equals(removal.get("replacementAuthorityRequired")), "legacy_replacement_authority_required");
        require(Boolean.TRUE.equals(removal.get("zeroReferencesRequired")), "legacy_zero_references_required");
        require(Boolean.TRUE.equals(removal.get("zeroWritersRequired")), "legacy_zero_writers_required");
        require(Boolean.TRUE.equals(removal.get("executionRightsRevokedRequired")), "legacy_execution_revoke_required");
        require(Boolean.FALSE.equals(removal.get("automaticRestoreAllowed")), "legacy_automatic_restore_forbidden");
        require(Boolean.FALSE.equals(removal.get("productionLegacyDeletionByJavaEnabled")), "java_legacy_deletion_must_be_disabled_until_cutover");
        require(Boolean.TRUE.equals(compatibility.get("capabilitySupersetRequired")), "compatibility_capability_superset_required");
        require(!Json.array(compatibility.get("schemaMigrations")).isEmpty(), "compatibility_migration_contract_missing");
        require(Json.array(legacy.get("entries")).size() >= 2, "legacy_removal_contract_entries_missing");
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
