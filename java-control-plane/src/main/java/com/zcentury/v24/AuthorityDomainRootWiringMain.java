package com.zcentury.v24;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Executable V24.25 verification for root-bound domain wiring and cutover preparation. */
public final class AuthorityDomainRootWiringMain {
    private static final Set<String> DOMAINS = Set.of(
        "INFORMATION", "INVOCATION", "TEMPORAL", "MUTATION"
    );

    private AuthorityDomainRootWiringMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                "usage: AuthorityDomainRootWiringMain <kernel-policy-json> <binding-policy-json>"
            );
        }

        Map<String, Object> kernelPolicy = readObject(Path.of(args[0]));
        Map<String, Object> bindingPolicy = readObject(Path.of(args[1]));
        UnifiedAuthorityKernel.validatePolicy(kernelPolicy);
        verifyBindingPolicy(bindingPolicy);

        Path temp = Files.createTempDirectory("v24-authority-domain-root-wiring-");
        AuthorityGenerationStore store = new AuthorityGenerationStore(
            temp.resolve("authority-generation.json")
        );
        UnifiedAuthorityGenerationRoot root = new UnifiedAuthorityGenerationRoot(store);
        Map<String, RootBoundAuthorityAdapter> adapters = AuthorityAdapterRegistry.bind(
            kernelPolicy,
            root
        );
        require(adapters.keySet().equals(DOMAINS), "root_bound_domain_set_mismatch:" + adapters.keySet());

        Map<String, Object> initialState = store.status();
        String initialOwnerHash = ownerHash(initialState);
        Map<String, RootBoundAuthorityAdapter.Token> initialTokens = tokens(adapters);
        Map<String, Object> initialBindingReport = AuthorityAdapterRegistry.bindingReport(
            kernelPolicy,
            root
        );

        RootBoundAuthorityAdapter information = adapters.get("INFORMATION");
        RootBoundAuthorityAdapter invocation = adapters.get("INVOCATION");
        RootBoundAuthorityAdapter temporal = adapters.get("TEMPORAL");
        RootBoundAuthorityAdapter mutation = adapters.get("MUTATION");

        Map<String, String> directInformation = informationVector();
        Map<String, String> rootBoundInformation = information.execute(
            initialTokens.get("INFORMATION"),
            "VERIFY_ALIAS_INDEX",
            AuthorityDomainRootWiringMain::informationVector
        );
        boolean informationSemanticParity = Json.canonical(directInformation)
            .equals(Json.canonical(rootBoundInformation));

        String queueContract = "v24.25-domain-root-wiring-shadow";
        QueueAuthority legacyQueue = new QueueAuthority(new GenerationFencer(), queueContract);
        QueueAuthority rootBoundQueue = invocation.queue(queueContract);
        String inputHash = Hashing.canonicalHash(Map.of("input", "domain-root-wiring"));
        legacyQueue.registerItem("ITEM-WIRING-1", "DATA-V1", 1);
        rootBoundQueue.registerItem("ITEM-WIRING-1", "DATA-V1", 1);
        QueueAuthority.EnqueueResult legacyEnqueue = legacyQueue.enqueue(
            "ITEM-WIRING-1", QueueAuthority.Stage.AGENT1, inputHash, 1
        );
        QueueAuthority.EnqueueResult rootEnqueue = rootBoundQueue.enqueue(
            "ITEM-WIRING-1", QueueAuthority.Stage.AGENT1, inputHash, 1
        );
        boolean invocationSemanticParity = legacyEnqueue.jobId().equals(rootEnqueue.jobId())
            && legacyEnqueue.idempotencyKey().equals(rootEnqueue.idempotencyKey())
            && legacyEnqueue.duplicateSuppressed() == rootEnqueue.duplicateSuppressed();
        QueueAuthority.Claim legacyClaim = legacyQueue.claim(
            QueueAuthority.Stage.AGENT1, "legacy-lane-worker", 60_000L
        );
        QueueAuthority.Claim rootClaim = rootBoundQueue.claim(
            QueueAuthority.Stage.AGENT1, "root-bound-shadow-worker", 60_000L
        );
        require(legacyClaim != null && rootClaim != null, "domain_root_wiring_claim_missing");

        Map<String, Object> directTemporal = TaskStateAuthority.decide("待接收", "处理中", 1L, 1L);
        Map<String, Object> rootBoundTemporal = temporal.execute(
            initialTokens.get("TEMPORAL"),
            "TASK_STATE_DECIDE",
            () -> TaskStateAuthority.decide("待接收", "处理中", 1L, 1L)
        );
        boolean temporalSemanticParity = Json.canonical(directTemporal)
            .equals(Json.canonical(rootBoundTemporal));

        Map<String, Object> gateDefinitions = gateDefinitions();
        GateEngine directGate = new GateEngine(gateDefinitions);
        GateEngine rootBoundGate = new GateEngine(gateDefinitions);
        Map<String, Object> gateInput = Map.of("ready", true);
        Map<String, Object> directMutation = directGate.evaluate("ROOT_WIRING_TEST", gateInput);
        Map<String, Object> rootBoundMutation = mutation.execute(
            initialTokens.get("MUTATION"),
            "GATE_EVALUATE",
            () -> rootBoundGate.evaluate("ROOT_WIRING_TEST", gateInput)
        );
        boolean mutationSemanticParity = Json.canonical(directMutation)
            .equals(Json.canonical(rootBoundMutation));

        boolean legacyDeterministicSemanticParity = informationSemanticParity
            && invocationSemanticParity
            && temporalSemanticParity
            && mutationSemanticParity;

        String sourceCommit = "0123456789abcdef0123456789abcdef01234567";
        String releaseHash = Hashing.canonicalHash(Map.of(
            "release", "v24.25-domain-root-wiring-shadow"
        ));
        Map<String, Object> prepared = store.prepare(
            text(initialState.get("stateHash")),
            sourceCommit,
            releaseHash,
            generationProof(sourceCommit, releaseHash)
        );
        String preparedOwnerHash = ownerHash(prepared);
        Map<String, RootBoundAuthorityAdapter.Token> preparedTokens = tokens(adapters);
        boolean allDomainsObservedPreparedGeneration = allCurrent(adapters, preparedTokens);

        boolean staleInformationBlocked = staleBlocked(
            information, initialTokens.get("INFORMATION"), "STALE_INFORMATION"
        );
        boolean staleTemporalBlocked = staleBlocked(
            temporal, initialTokens.get("TEMPORAL"), "STALE_TEMPORAL"
        );
        boolean staleMutationBlocked = staleBlocked(
            mutation, initialTokens.get("MUTATION"), "STALE_MUTATION"
        );
        QueueAuthority.CommitResult staleInvocationCommit = rootBoundQueue.completeAndHandoff(
            rootClaim,
            Hashing.canonicalHash(Map.of("output", "stale-root-bound-invocation")),
            QueueAuthority.Stage.AGENT2
        );
        boolean staleInvocationBlocked = !staleInvocationCommit.accepted()
            && "STALE_GENERATION".equals(staleInvocationCommit.reason());
        boolean allOldDomainTokensFailClosed = staleInformationBlocked
            && staleInvocationBlocked
            && staleTemporalBlocked
            && staleMutationBlocked;

        QueueAuthority.CommitResult legacyLaneCommitAfterRootRotation = legacyQueue.completeAndHandoff(
            legacyClaim,
            Hashing.canonicalHash(Map.of("output", "legacy-lane-unaffected")),
            null
        );
        boolean legacyLaneUnaffectedByShadowRootRotation = legacyLaneCommitAfterRootRotation.accepted();

        boolean freshInformationPass = information.execute(
            preparedTokens.get("INFORMATION"),
            "FRESH_INFORMATION",
            () -> Map.of("decision", "PASS")
        ).get("decision").equals("PASS");
        boolean freshTemporalPass = "PASS".equals(text(temporal.execute(
            preparedTokens.get("TEMPORAL"),
            "FRESH_TEMPORAL",
            () -> TaskStateAuthority.decide("待接收", "处理中", 2L, 2L)
        ).get("decision")));
        boolean freshMutationPass = "PASS".equals(text(mutation.execute(
            preparedTokens.get("MUTATION"),
            "FRESH_MUTATION",
            () -> rootBoundGate.evaluate("ROOT_WIRING_TEST", gateInput)
        ).get("decision")));

        rootBoundQueue.registerItem("ITEM-WIRING-2", "DATA-V2", 1);
        rootBoundQueue.enqueue(
            "ITEM-WIRING-2", QueueAuthority.Stage.AGENT1,
            Hashing.canonicalHash(Map.of("input", "fresh-root-generation")), 1
        );
        QueueAuthority.Claim freshInvocationClaim = rootBoundQueue.claim(
            QueueAuthority.Stage.AGENT1, "root-bound-fresh-worker", 60_000L
        );
        boolean freshInvocationPass = freshInvocationClaim != null
            && rootBoundQueue.completeAndHandoff(
                freshInvocationClaim,
                Hashing.canonicalHash(Map.of("output", "fresh-root-generation")),
                null
            ).accepted();
        boolean freshRootBoundOperationsPass = freshInformationPass
            && freshInvocationPass
            && freshTemporalPass
            && freshMutationPass;

        Map<String, Object> rolledBack = store.rollback(
            text(prepared.get("stateHash")),
            "v24_25_domain_root_wiring_rollback_proof"
        );
        String rollbackOwnerHash = ownerHash(rolledBack);
        boolean preparedGenerationInvalidAfterRollback = staleBlocked(
            temporal,
            preparedTokens.get("TEMPORAL"),
            "PREPARED_GENERATION_AFTER_ROLLBACK"
        );

        boolean ownerBoundaryStable = initialOwnerHash.equals(preparedOwnerHash)
            && initialOwnerHash.equals(rollbackOwnerHash);
        boolean productionMutationStillForbidden = !Boolean.TRUE.equals(
            rolledBack.get("productionMutationAllowed")
        );
        boolean javaProductionOwnerStillForbidden = Json.object(rolledBack.get("owners"))
            .values().stream().noneMatch(value -> "JAVA_PRODUCTION".equals(text(value)));

        boolean verified = legacyDeterministicSemanticParity
            && Boolean.TRUE.equals(initialBindingReport.get("allDomainsRootBound"))
            && allDomainsObservedPreparedGeneration
            && allOldDomainTokensFailClosed
            && legacyLaneUnaffectedByShadowRootRotation
            && freshRootBoundOperationsPass
            && preparedGenerationInvalidAfterRollback
            && ownerBoundaryStable
            && productionMutationStillForbidden
            && javaProductionOwnerStillForbidden;

        LinkedHashMap<String, Object> report = new LinkedHashMap<>();
        report.put("schema", "v24.unified_authority_domain_root_wiring.verification.v1");
        report.put("version", RootBoundAuthorityAdapter.VERSION);
        report.put("verified", verified);
        report.put("enforcementMode", "SHADOW");
        report.put("rootSource", "AuthorityGenerationStore");
        report.put("domainCount", adapters.size());
        report.put("allDomainsRootBound", initialBindingReport.get("allDomainsRootBound"));
        report.put("informationSemanticParity", informationSemanticParity);
        report.put("invocationSemanticParity", invocationSemanticParity);
        report.put("temporalSemanticParity", temporalSemanticParity);
        report.put("mutationSemanticParity", mutationSemanticParity);
        report.put("legacyDeterministicSemanticParity", legacyDeterministicSemanticParity);
        report.put("pythonJavaMirrorParityRequired", true);
        report.put("pythonJavaMirrorParityVerifiedByWorkflow", true);
        report.put("allDomainsObservedPreparedGeneration", allDomainsObservedPreparedGeneration);
        report.put("staleInformationBlocked", staleInformationBlocked);
        report.put("staleInvocationBlocked", staleInvocationBlocked);
        report.put("staleTemporalBlocked", staleTemporalBlocked);
        report.put("staleMutationBlocked", staleMutationBlocked);
        report.put("allOldDomainTokensFailClosed", allOldDomainTokensFailClosed);
        report.put("legacyLaneUnaffectedByShadowRootRotation", legacyLaneUnaffectedByShadowRootRotation);
        report.put("freshRootBoundOperationsPass", freshRootBoundOperationsPass);
        report.put("preparedGenerationInvalidAfterRollback", preparedGenerationInvalidAfterRollback);
        report.put("productionOwnerBoundaryStable", ownerBoundaryStable);
        report.put("productionMutationAllowed", !productionMutationStillForbidden);
        report.put("productionAuthorityOwnershipChanged", !javaProductionOwnerStillForbidden);
        report.put("authorityGrantCreated", false);
        report.put("externalProductionMirrorRequiredBeforeCutover", true);
        report.put("externalProductionMirrorParityProvenByThisPhase", false);
        report.put("cutoverAllowed", false);
        report.put("cutoverPrepareStatus", "ROOT_WIRING_VERIFIED_EXTERNAL_MIRROR_REQUIRED");
        report.put("nextRequiredGate", "PRODUCTION_MIRROR_PARITY_AND_ROLLBACK_WINDOW");
        report.put("initialOwnerHash", initialOwnerHash);
        report.put("preparedOwnerHash", preparedOwnerHash);
        report.put("rollbackOwnerHash", rollbackOwnerHash);
        report.put("initialBindingReport", initialBindingReport);
        report.put("bindingPolicyHash", Hashing.canonicalHash(bindingPolicy));
        report.put("kernelPolicyHash", Hashing.canonicalHash(kernelPolicy));
        report.put("verificationHash", Hashing.canonicalHash(report));

        if (!verified) {
            throw new IllegalStateException(
                "v24_25_unified_authority_domain_root_wiring_verification_failed:"
                    + Json.canonical(report)
            );
        }
        System.out.println(Json.canonical(report));
    }

    private static Map<String, String> informationVector() {
        LinkedHashMap<String, Map<String, Object>> fieldIndex = new LinkedHashMap<>();
        fieldIndex.put("shop.sales", Map.of("canonicalField", "shop.sales"));
        Map<String, Object> aliasRegistry = Map.of(
            "schema", "rag.alias_registry.v1",
            "defaultDecision", "BLOCK",
            "entries", List.of(Map.of(
                "canonicalField", "shop.sales",
                "aliases", List.of("sales", "gmv")
            ))
        );
        return V25RetrievalAuthority.verifyAliasIndex(aliasRegistry, fieldIndex);
    }

    private static Map<String, Object> gateDefinitions() {
        return Map.of(
            "defaultDecision", "BLOCK",
            "gates", List.of(Map.of(
                "gateId", "ROOT_WIRING_TEST",
                "predicates", List.of(Map.of(
                    "path", "ready",
                    "op", "equals",
                    "value", true
                )),
                "passDecision", "PASS",
                "failDecision", "BLOCK"
            ))
        );
    }

    private static Map<String, RootBoundAuthorityAdapter.Token> tokens(
        Map<String, RootBoundAuthorityAdapter> adapters
    ) {
        LinkedHashMap<String, RootBoundAuthorityAdapter.Token> out = new LinkedHashMap<>();
        for (String domain : DOMAINS.stream().sorted().toList()) {
            out.put(domain, adapters.get(domain).token());
        }
        return out;
    }

    private static boolean allCurrent(
        Map<String, RootBoundAuthorityAdapter> adapters,
        Map<String, RootBoundAuthorityAdapter.Token> tokens
    ) {
        for (String domain : DOMAINS) {
            if (!adapters.get(domain).matches(tokens.get(domain))) return false;
        }
        return true;
    }

    private static boolean staleBlocked(
        RootBoundAuthorityAdapter adapter,
        RootBoundAuthorityAdapter.Token token,
        String operation
    ) {
        try {
            adapter.execute(token, operation, () -> Map.of("unexpected", true));
            return false;
        } catch (IllegalStateException expected) {
            return expected.getMessage() != null
                && expected.getMessage().startsWith(
                    "stale_root_bound_authority_generation:" + adapter.domain() + ":"
                );
        }
    }

    private static Map<String, Object> generationProof(String sourceCommit, String releaseHash) {
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
        return proof;
    }

    private static String ownerHash(Map<String, Object> state) {
        return Hashing.canonicalHash(Json.object(state.get("owners")));
    }

    private static void verifyBindingPolicy(Map<String, Object> policy) {
        require(
            "v24.unified_authority_domain_root_binding.policy.v1".equals(text(policy.get("schema"))),
            "domain_root_binding_policy_schema_invalid"
        );
        require(
            RootBoundAuthorityAdapter.VERSION.equals(text(policy.get("version"))),
            "domain_root_binding_policy_version_invalid"
        );
        require("SHADOW".equals(text(policy.get("enforcementMode"))), "binding_mode_must_shadow");
        require("BLOCK".equals(text(policy.get("defaultDecision"))), "binding_default_must_block");
        Set<String> configuredDomains = new LinkedHashSet<>();
        for (Object item : Json.array(policy.get("domains"))) configuredDomains.add(text(item));
        require(configuredDomains.equals(DOMAINS), "binding_domain_set_mismatch:" + configuredDomains);
        Map<String, Object> principles = Json.object(policy.get("principles"));
        requireTrue(principles, "allDomainsMustBindSingleRoot");
        requireFalse(principles, "domainMayRotateGeneration");
        requireTrue(principles, "generationAdmissionBeforeDomainExecution");
        requireTrue(principles, "generationAdmissionRecheckAfterDomainExecution");
        requireTrue(principles, "staleGenerationMustFailClosed");
        requireTrue(principles, "legacyDeterministicSemanticsMustRemainIdentical");
        requireTrue(principles, "pythonJavaMirrorParityMustRemainProven");
        requireTrue(principles, "productionOwnerMustRemainUnchanged");
        requireFalse(principles, "productionMutationAllowed");
        requireFalse(principles, "authorityGrantCreatedByThisPhase");
        requireTrue(principles, "externalProductionMirrorRequiredBeforeCutover");
        requireFalse(principles, "cutoverAllowedByThisPhase");
    }

    private static Map<String, Object> readObject(Path path) throws Exception {
        require(Files.isRegularFile(path), "json_file_missing:" + path);
        return Json.object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
    }

    private static void requireTrue(Map<String, Object> values, String key) {
        require(Boolean.TRUE.equals(values.get(key)), "binding_principle_required_true:" + key);
    }

    private static void requireFalse(Map<String, Object> values, String key) {
        require(Boolean.FALSE.equals(values.get(key)), "binding_principle_required_false:" + key);
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
