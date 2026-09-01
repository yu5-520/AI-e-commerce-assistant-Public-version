package com.zcentury.v24;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Executable Shadow verifier for V24.23 Unified Authority Kernel V1. */
public final class UnifiedAuthorityKernelMain {
    private UnifiedAuthorityKernelMain() {}

    public static void main(String[] args) throws Exception {
        Arguments parsed = Arguments.parse(args);
        Map<String, Object> policy = Json.object(Json.parse(
            Files.readString(parsed.policy(), StandardCharsets.UTF_8)
        ));
        UnifiedAuthorityKernel.validatePolicy(policy);
        String policyHash = UnifiedAuthorityKernel.policyHash(policy);
        Map<String, Object> adapterRegistry = AuthorityAdapterRegistry.verify(policy);

        String generationHash = Hashing.canonicalHash(Map.of(
            "schema", "v24.unified_authority.shadow_generation.v1",
            "generationSeq", 7L,
            "fencingToken", 7L
        ));
        Map<String, Object> generation = generation(7L, generationHash, 7L);

        List<Map<String, Object>> decisions = new ArrayList<>();

        Map<String, Object> allowedInvocation = request(
            "REQ-INV-ALLOW", policyHash, generation,
            "AGENT1", "INVOKE", "AGENT2", "INVOCATION",
            "BUILD_ACTION_DRAFT", "AGENT1_COMPLETED", "ACTIVE", "FACT"
        );
        decisions.add(expect(
            UnifiedAuthorityKernel.evaluate(allowedInvocation, policy, generation),
            "PASS", null
        ));

        Map<String, Object> graphExpansion = new LinkedHashMap<>(allowedInvocation);
        graphExpansion.put("requestId", "REQ-INV-BLOCK");
        graphExpansion.put("resource", "FINANCE_AGENT");
        decisions.add(expect(
            UnifiedAuthorityKernel.evaluate(graphExpansion, policy, generation),
            "BLOCK", "AUTHORITY_EDGE_NOT_GRANTED"
        ));

        Map<String, Object> factRead = request(
            "REQ-INFO-FACT", policyHash, generation,
            "AGENT1", "READ", "product.roas", "INFORMATION",
            "OPERATING_DIAGNOSIS", "AGENT1_RUNNING", "ACTIVE", "FACT"
        );
        decisions.add(expect(
            UnifiedAuthorityKernel.evaluate(factRead, policy, generation),
            "PASS", null
        ));

        Map<String, Object> inferredPromotion = request(
            "REQ-INFO-PROMOTE", policyHash, generation,
            "AGENT1", "PROMOTE_TO_FACT", "derived.roas_reason", "INFORMATION",
            "OPERATING_DIAGNOSIS", "AGENT1_RUNNING", "ACTIVE", "INFERENCE"
        );
        decisions.add(expect(
            UnifiedAuthorityKernel.evaluate(inferredPromotion, policy, generation),
            "BLOCK", "DERIVED_INFORMATION_CANNOT_CREATE_SYSTEM_FACT"
        ));

        Map<String, Object> retrospectiveRead = request(
            "REQ-TIME-READ", policyHash, generation,
            "REVIEWER", "RETROSPECTIVE_READ", "TASK-123-HISTORY", "TEMPORAL",
            "REVIEW_COMPLETED_TASK", "COMPLETED", "RETROSPECTIVE_READONLY", "HUMAN_APPROVED"
        );
        decisions.add(expect(
            UnifiedAuthorityKernel.evaluate(retrospectiveRead, policy, generation),
            "PASS", null
        ));

        Map<String, Object> reopenClosed = new LinkedHashMap<>(retrospectiveRead);
        reopenClosed.put("requestId", "REQ-TIME-REOPEN");
        reopenClosed.put("action", "REOPEN");
        decisions.add(expect(
            UnifiedAuthorityKernel.evaluate(reopenClosed, policy, generation),
            "BLOCK", "TERMINAL_REOPEN_BLOCKED"
        ));

        Map<String, Object> activeMutation = request(
            "REQ-MUTATE-ACTIVE", policyHash, generation,
            "SYSTEM_PIPELINE", "MUTATE", "TASK_PLAN", "MUTATION",
            "COMMIT_DETERMINISTIC_TASK", "SOP_READY", "ACTIVE", "FACT"
        );
        decisions.add(expect(
            UnifiedAuthorityKernel.evaluate(activeMutation, policy, generation),
            "PASS", null
        ));

        Map<String, Object> closedMutation = new LinkedHashMap<>(activeMutation);
        closedMutation.put("requestId", "REQ-MUTATE-CLOSED");
        closedMutation.put("state", "CLOSED");
        closedMutation.put("temporalMode", "RETROSPECTIVE_READONLY");
        decisions.add(expect(
            UnifiedAuthorityKernel.evaluate(closedMutation, policy, generation),
            "BLOCK", "TERMINAL_TRANSACTION_MUTATION_BLOCKED"
        ));

        Map<String, Object> staleGeneration = new LinkedHashMap<>(allowedInvocation);
        staleGeneration.put("requestId", "REQ-STALE-GENERATION");
        staleGeneration.put("fencingToken", 6L);
        decisions.add(expect(
            UnifiedAuthorityKernel.evaluate(staleGeneration, policy, generation),
            "CONFLICT", "STALE_AUTHORITY_GENERATION"
        ));

        Map<String, Object> modelGrant = new LinkedHashMap<>(allowedInvocation);
        modelGrant.put("requestId", "REQ-MODEL-GRANT");
        modelGrant.put("action", "GRANT_AUTHORITY");
        decisions.add(expect(
            UnifiedAuthorityKernel.evaluate(modelGrant, policy, generation),
            "BLOCK", "INVOCATION_ACTION_NOT_REGISTERED"
        ));

        LinkedHashMap<String, Object> report = new LinkedHashMap<>();
        report.put("schema", "v24.unified_authority_kernel.verification.v1");
        report.put("version", UnifiedAuthorityKernel.VERSION);
        report.put("verified", true);
        report.put("enforcementMode", policy.get("enforcementMode"));
        report.put("testCount", decisions.size());
        report.put("policyHash", policyHash);
        report.put("adapterRegistryHash", adapterRegistry.get("registryHash"));
        report.put("taskScopedAuthorityVerified", true);
        report.put("effectiveAuthoritySubsetInvariantVerified", true);
        report.put("informationAuthorityVerified", true);
        report.put("invocationAuthorityVerified", true);
        report.put("temporalAuthorityVerified", true);
        report.put("mutationAuthorityVerified", true);
        report.put("derivedInformationFactPromotionBlocked", true);
        report.put("unregisteredInvocationEdgeBlocked", true);
        report.put("terminalReopenBlocked", true);
        report.put("terminalMutationBlocked", true);
        report.put("staleGenerationBlocked", true);
        report.put("modelAuthorityGrantBlocked", true);
        report.put("singleAuthorityGenerationRootRequired", true);
        report.put("authorityGrantCreated", false);
        report.put("productionAuthorityOwnershipChanged", false);
        report.put("existingAuthorityAdaptersReplaced", false);
        report.put("decisions", decisions);
        report.put("verificationHash", Hashing.canonicalHash(report));

        String encoded = Json.canonical(report) + "\n";
        if (parsed.output() != null) {
            Path parent = parsed.output().toAbsolutePath().normalize().getParent();
            if (parent != null) Files.createDirectories(parent);
            Files.writeString(parsed.output(), encoded, StandardCharsets.UTF_8);
        }
        System.out.print(encoded);
    }

    private static Map<String, Object> request(
        String requestId,
        String policyHash,
        Map<String, Object> generation,
        String actor,
        String action,
        String resource,
        String domain,
        String goal,
        String state,
        String temporalMode,
        String semanticType
    ) {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("schema", UnifiedAuthorityKernel.REQUEST_SCHEMA);
        value.put("requestId", requestId);
        value.put("transactionId", "TASK-123");
        value.put("executionId", "EXE-123-G7");
        value.put("generationSeq", generation.get("generationSeq"));
        value.put("generationHash", generation.get("generationHash"));
        value.put("fencingToken", generation.get("fencingToken"));
        value.put("actor", actor);
        value.put("action", action);
        value.put("resource", resource);
        value.put("domain", domain);
        value.put("goal", goal);
        value.put("state", state);
        value.put("temporalMode", temporalMode);
        value.put("semanticType", semanticType);
        value.put("evidenceRefs", List.of("ART-INPUT-001", "ART-POLICY-001"));
        value.put("requestedScope", Map.of(
            "resource", resource,
            "goal", goal
        ));
        value.put("parentAuthorityRef", "AUTH-TASK-123");
        value.put("policyHash", policyHash);
        return value;
    }

    private static Map<String, Object> generation(long seq, String hash, long token) {
        LinkedHashMap<String, Object> value = new LinkedHashMap<>();
        value.put("generationSeq", seq);
        value.put("generationHash", hash);
        value.put("fencingToken", token);
        return value;
    }

    private static Map<String, Object> expect(
        Map<String, Object> decision,
        String expectedDecision,
        String expectedReason
    ) {
        if (!expectedDecision.equals(String.valueOf(decision.get("decision")))) {
            throw new IllegalStateException("unexpected_authority_decision:" + Json.canonical(decision));
        }
        if (Boolean.TRUE.equals(decision.get("authorityGrantCreated"))) {
            throw new IllegalStateException("shadow_kernel_must_not_create_authority_grant");
        }
        if (Boolean.TRUE.equals(decision.get("productionAuthorityOwnershipChanged"))) {
            throw new IllegalStateException("shadow_kernel_must_not_change_production_owner");
        }
        if (expectedReason != null) {
            List<Object> reasons = decision.get("reasonCodes") instanceof List<?>
                ? Json.array(decision.get("reasonCodes"))
                : List.of();
            if (!reasons.contains(expectedReason)) {
                throw new IllegalStateException(
                    "expected_authority_reason_missing:" + expectedReason + ":" + Json.canonical(decision)
                );
            }
        }
        return decision;
    }

    private record Arguments(Path policy, Path output) {
        static Arguments parse(String[] args) {
            Path policy = null;
            Path output = null;
            for (int i = 0; i < args.length; i++) {
                switch (args[i]) {
                    case "--policy" -> policy = Path.of(requireValue(args, ++i, "--policy"));
                    case "--output" -> output = Path.of(requireValue(args, ++i, "--output"));
                    default -> throw new IllegalArgumentException("unknown_argument:" + args[i]);
                }
            }
            if (policy == null) throw new IllegalArgumentException("--policy is required");
            return new Arguments(policy, output);
        }

        private static String requireValue(String[] args, int index, String flag) {
            if (index >= args.length) throw new IllegalArgumentException("missing_value:" + flag);
            return args[index];
        }
    }
}
