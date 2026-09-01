package com.zcentury.v24;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Executable V24.24 shadow verification for the single Authority Generation Root. */
public final class UnifiedAuthorityGenerationRootMain {
    private UnifiedAuthorityGenerationRootMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: UnifiedAuthorityGenerationRootMain <policy-json>");
        }

        Map<String, Object> policy = Json.object(Json.parse(
            Files.readString(Path.of(args[0]), StandardCharsets.UTF_8)
        ));
        UnifiedAuthorityKernel.validatePolicy(policy);

        Path temp = Files.createTempDirectory("v24-authority-generation-root-");
        AuthorityGenerationStore store = new AuthorityGenerationStore(temp.resolve("authority-generation.json"));
        UnifiedAuthorityGenerationRoot root = new UnifiedAuthorityGenerationRoot(store);
        UnifiedAuthorityGenerationRoot.Identity initial = root.current();

        GenerationFencer information = root.consumerFence("INFORMATION");
        GenerationFencer invocation = root.consumerFence("INVOCATION");
        GenerationFencer temporal = root.consumerFence("TEMPORAL");
        GenerationFencer mutation = root.consumerFence("MUTATION");
        List<GenerationFencer> consumers = List.of(information, invocation, temporal, mutation);

        boolean allRootBound = consumers.stream().allMatch(GenerationFencer::rootBound);
        boolean allSameInitialGeneration = consumers.stream().allMatch(fencer -> same(fencer.current(), initial));

        boolean rootBoundRotationBlocked = false;
        try {
            invocation.rotate("model_requested_extra_generation");
        } catch (IllegalStateException expected) {
            rootBoundRotationBlocked = expected.getMessage().startsWith(
                "generation_rotation_forbidden_for_root_bound_consumer:INVOCATION"
            );
        }

        QueueAuthority queue = new QueueAuthority(invocation, "v24.24-root-bound-shadow");
        queue.registerItem("ITEM-ROOT-1", "DATA-V1", 1);
        queue.enqueue("ITEM-ROOT-1", QueueAuthority.Stage.AGENT1, Hashing.canonicalHash(Map.of("input", "v1")), 1);
        QueueAuthority.Claim oldClaim = queue.claim(QueueAuthority.Stage.AGENT1, "worker-root-shadow", 60_000L);
        if (oldClaim == null) throw new IllegalStateException("root_bound_queue_claim_missing");

        Map<String, Object> oldRequest = invocationRequest(policy, initial, "REQ-OLD");
        Map<String, Object> decisionBeforeRotation = root.evaluate(oldRequest, policy);

        Map<String, Object> beforePrepare = store.status();
        String sourceCommit = "0123456789abcdef0123456789abcdef01234567";
        String releaseHash = Hashing.canonicalHash(Map.of("release", "v24.24-generation-root-shadow"));
        LinkedHashMap<String, Object> gates = new LinkedHashMap<>();
        gates.put("SEALED_JAVA_RUNTIME_VERIFIED", true);
        gates.put("JAVA_SERVICE_READY_NO_AUTHORITY", true);
        gates.put("PYTHON_JAVA_MIRROR_PARITY_PROVEN", true);
        gates.put("DURABLE_STATE_ADAPTER_VERIFIED", true);
        gates.put("SINGLE_WRITER_GENERATION_ROTATION_PREPARED", true);
        gates.put("FULL_ROLLBACK_PROVEN", true);
        LinkedHashMap<String, Object> proof = new LinkedHashMap<>();
        proof.put("schema", "v24.authority-generation.proof.v1");
        proof.put("verified", true);
        proof.put("sourceCommit", sourceCommit);
        proof.put("releaseHash", releaseHash);
        proof.put("gates", gates);

        Map<String, Object> prepared = store.prepare(
            text(beforePrepare.get("stateHash")), sourceCommit, releaseHash, proof
        );
        UnifiedAuthorityGenerationRoot.Identity preparedIdentity = root.current();
        boolean consumersObservedPreparedGeneration = consumers.stream().allMatch(
            fencer -> same(fencer.current(), preparedIdentity)
        );

        QueueAuthority.CommitResult staleQueueCommit = queue.completeAndHandoff(
            oldClaim,
            Hashing.canonicalHash(Map.of("output", "stale-after-root-rotation")),
            QueueAuthority.Stage.AGENT2
        );
        Map<String, Object> staleKernelDecision = root.evaluate(oldRequest, policy);

        Map<String, Object> freshRequest = invocationRequest(policy, preparedIdentity, "REQ-FRESH");
        Map<String, Object> freshKernelDecision = root.evaluate(freshRequest, policy);

        Map<String, Object> rolledBack = store.rollback(
            text(prepared.get("stateHash")), "v24_24_shadow_root_rollback_proof"
        );
        UnifiedAuthorityGenerationRoot.Identity rollbackIdentity = root.current();
        boolean consumersObservedRollbackGeneration = consumers.stream().allMatch(
            fencer -> same(fencer.current(), rollbackIdentity)
        );
        Map<String, Object> postRollbackStaleDecision = root.evaluate(freshRequest, policy);

        GenerationFencer legacyShadow = new GenerationFencer();
        UnifiedAuthorityGenerationRoot.Identity beforeLegacyRotate = root.current();
        legacyShadow.rotate("legacy_shadow_local_rotation");
        UnifiedAuthorityGenerationRoot.Identity afterLegacyRotate = root.current();
        boolean legacyLocalRotationCannotChangeAuthorityRoot = same(beforeLegacyRotate, afterLegacyRotate);

        boolean durableRootRotated = initial.generationSeq() != preparedIdentity.generationSeq()
            && initial.fencingToken() != preparedIdentity.fencingToken()
            && !initial.generationHash().equals(preparedIdentity.generationHash());
        boolean rollbackRotatedAgain = rollbackIdentity.generationSeq() != preparedIdentity.generationSeq()
            && rollbackIdentity.fencingToken() != preparedIdentity.fencingToken()
            && !rollbackIdentity.generationHash().equals(preparedIdentity.generationHash());
        boolean queueRejectedOldGeneration = !staleQueueCommit.accepted()
            && "STALE_GENERATION".equals(staleQueueCommit.reason());
        boolean kernelRejectedOldGeneration = "CONFLICT".equals(text(staleKernelDecision.get("decision")))
            && reasons(staleKernelDecision).contains("STALE_AUTHORITY_GENERATION");
        boolean freshKernelPass = "PASS".equals(text(freshKernelDecision.get("decision")));
        boolean rollbackInvalidatedFreshRequest = "CONFLICT".equals(text(postRollbackStaleDecision.get("decision")))
            && reasons(postRollbackStaleDecision).contains("STALE_AUTHORITY_GENERATION");
        boolean decisionBeforeRotationPass = "PASS".equals(text(decisionBeforeRotation.get("decision")));
        boolean productionMutationStillForbidden = !Boolean.TRUE.equals(rolledBack.get("productionMutationAllowed"));
        boolean javaProductionOwnerStillForbidden = Json.object(rolledBack.get("owners"))
            .values().stream().noneMatch(value -> "JAVA_PRODUCTION".equals(text(value)));

        boolean verified = allRootBound
            && allSameInitialGeneration
            && rootBoundRotationBlocked
            && durableRootRotated
            && consumersObservedPreparedGeneration
            && queueRejectedOldGeneration
            && decisionBeforeRotationPass
            && kernelRejectedOldGeneration
            && freshKernelPass
            && rollbackRotatedAgain
            && consumersObservedRollbackGeneration
            && rollbackInvalidatedFreshRequest
            && legacyLocalRotationCannotChangeAuthorityRoot
            && productionMutationStillForbidden
            && javaProductionOwnerStillForbidden;

        LinkedHashMap<String, Object> report = new LinkedHashMap<>();
        report.put("schema", "v24.unified_authority_generation_root.verification.v1");
        report.put("version", UnifiedAuthorityGenerationRoot.VERSION);
        report.put("verified", verified);
        report.put("enforcementMode", "SHADOW");
        report.put("rootSource", "AuthorityGenerationStore");
        report.put("rootBoundConsumerCount", consumers.size());
        report.put("allRootBound", allRootBound);
        report.put("allSameInitialGeneration", allSameInitialGeneration);
        report.put("rootBoundConsumerRotationForbidden", rootBoundRotationBlocked);
        report.put("durableRootRotated", durableRootRotated);
        report.put("consumersObservedPreparedGeneration", consumersObservedPreparedGeneration);
        report.put("queueRejectedOldGeneration", queueRejectedOldGeneration);
        report.put("kernelRejectedOldGeneration", kernelRejectedOldGeneration);
        report.put("freshKernelPass", freshKernelPass);
        report.put("rollbackRotatedAgain", rollbackRotatedAgain);
        report.put("consumersObservedRollbackGeneration", consumersObservedRollbackGeneration);
        report.put("rollbackInvalidatedFreshRequest", rollbackInvalidatedFreshRequest);
        report.put("legacyLocalRotationCannotChangeAuthorityRoot", legacyLocalRotationCannotChangeAuthorityRoot);
        report.put("productionMutationAllowed", !productionMutationStillForbidden);
        report.put("productionAuthorityOwnershipChanged", !javaProductionOwnerStillForbidden);
        report.put("authorityGrantCreated", false);
        report.put("existingAuthorityAdaptersReplaced", false);
        report.put("initialGeneration", identity(initial));
        report.put("preparedGeneration", identity(preparedIdentity));
        report.put("rollbackGeneration", identity(rollbackIdentity));
        report.put("rootStatus", root.status());
        report.put("verificationHash", Hashing.canonicalHash(report));

        if (!verified) {
            throw new IllegalStateException("v24_24_unified_authority_generation_root_verification_failed:" + Json.canonical(report));
        }
        System.out.println(Json.canonical(report));
    }

    private static Map<String, Object> invocationRequest(
        Map<String, Object> policy,
        UnifiedAuthorityGenerationRoot.Identity generation,
        String requestId
    ) {
        LinkedHashMap<String, Object> request = new LinkedHashMap<>();
        request.put("schema", UnifiedAuthorityKernel.REQUEST_SCHEMA);
        request.put("requestId", requestId);
        request.put("transactionId", "TX-ROOT-1");
        request.put("executionId", "EX-ROOT-1");
        request.put("actor", "AGENT1");
        request.put("domain", "INVOCATION");
        request.put("action", "INVOKE");
        request.put("resource", "AGENT2");
        request.put("goal", "BUILD_ACTION_DRAFT");
        request.put("state", "AGENT1_COMPLETED");
        request.put("temporalMode", "ACTIVE");
        request.put("evidenceRefs", List.of("ART-ROOT-EVIDENCE-1"));
        request.put("requestedScope", Map.of("purpose", "root-generation-verification"));
        request.put("policyHash", UnifiedAuthorityKernel.policyHash(policy));
        request.put("generationSeq", generation.generationSeq());
        request.put("generationHash", generation.generationHash());
        request.put("fencingToken", generation.fencingToken());
        return request;
    }

    private static Map<String, Object> identity(UnifiedAuthorityGenerationRoot.Identity value) {
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        out.put("generationSeq", value.generationSeq());
        out.put("generationHash", value.generationHash());
        out.put("fencingToken", value.fencingToken());
        out.put("stateHash", value.stateHash());
        out.put("mode", value.mode());
        return out;
    }

    private static boolean same(GenerationFencer.Snapshot snapshot, UnifiedAuthorityGenerationRoot.Identity identity) {
        return snapshot.generationSeq() == identity.generationSeq()
            && snapshot.fencingToken() == identity.fencingToken()
            && snapshot.generationHash().equals(identity.generationHash());
    }

    private static boolean same(
        UnifiedAuthorityGenerationRoot.Identity left,
        UnifiedAuthorityGenerationRoot.Identity right
    ) {
        return left.generationSeq() == right.generationSeq()
            && left.fencingToken() == right.fencingToken()
            && left.generationHash().equals(right.generationHash())
            && left.stateHash().equals(right.stateHash())
            && left.mode().equals(right.mode());
    }

    private static List<String> reasons(Map<String, Object> decision) {
        return Json.array(decision.get("reasonCodes")).stream().map(String::valueOf).toList();
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }
}
