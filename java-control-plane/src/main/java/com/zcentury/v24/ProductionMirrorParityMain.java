package com.zcentury.v24;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Executable V24.26 proof for mirror mechanics, drain fencing and rollback-window safety. */
public final class ProductionMirrorParityMain {
    private ProductionMirrorParityMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length < 2 || args.length > 3) {
            throw new IllegalArgumentException(
                "usage: ProductionMirrorParityMain <kernel-policy-json> <mirror-policy-json> [external-evidence-json]"
            );
        }

        Map<String, Object> kernelPolicy = readObject(Path.of(args[0]));
        Map<String, Object> mirrorPolicy = readObject(Path.of(args[1]));
        UnifiedAuthorityKernel.validatePolicy(kernelPolicy);
        ProductionMirrorParityAuthority.verifyPolicy(mirrorPolicy);

        Path temp = Files.createTempDirectory("v24-production-mirror-parity-");
        AuthorityGenerationStore store = new AuthorityGenerationStore(temp.resolve("authority-generation.json"));
        UnifiedAuthorityGenerationRoot root = new UnifiedAuthorityGenerationRoot(store);
        Map<String, RootBoundAuthorityAdapter> adapters = AuthorityAdapterRegistry.bind(kernelPolicy, root);

        Map<String, Object> initial = store.status();
        String initialOwnerHash = ownerHash(initial);
        Map<String, RootBoundAuthorityAdapter.Token> initialTokens = tokens(adapters);

        List<Object> replayWindows = new ArrayList<>();
        replayWindows.add(buildReplayWindow("REPLAY-WINDOW-001", 1, adapters));
        replayWindows.add(buildReplayWindow("REPLAY-WINDOW-002", 2, adapters));

        QueueAuthority shadowDrainQueue = adapters.get("INVOCATION").queue("v24.26-drain-window");
        String drainInputHash = Hashing.canonicalHash(Map.of("request", "pre-prepare-inflight"));
        shadowDrainQueue.registerItem("ITEM-DRAIN-001", "DV-DRAIN-001", 1);
        shadowDrainQueue.enqueue("ITEM-DRAIN-001", QueueAuthority.Stage.AGENT1, drainInputHash, 1);
        QueueAuthority.Claim prePrepareClaim = shadowDrainQueue.claim(
            QueueAuthority.Stage.AGENT1, "shadow-pre-prepare-worker", 60_000L
        );
        require(prePrepareClaim != null, "pre_prepare_claim_required");

        String sourceCommit = "0123456789abcdef0123456789abcdef01234567";
        String releaseHash = Hashing.canonicalHash(Map.of("release", "v24.26-production-mirror-parity"));
        Map<String, Object> prepared = store.prepare(
            text(initial.get("stateHash")), sourceCommit, releaseHash, generationProof(sourceCommit, releaseHash)
        );
        String preparedOwnerHash = ownerHash(prepared);
        Map<String, RootBoundAuthorityAdapter.Token> preparedTokens = tokens(adapters);

        boolean staleInformationBlocked = staleBlocked(
            adapters.get("INFORMATION"), initialTokens.get("INFORMATION"), "MIRROR_STALE_INFORMATION"
        );
        boolean staleTemporalBlocked = staleBlocked(
            adapters.get("TEMPORAL"), initialTokens.get("TEMPORAL"), "MIRROR_STALE_TEMPORAL"
        );
        boolean staleMutationBlocked = staleBlocked(
            adapters.get("MUTATION"), initialTokens.get("MUTATION"), "MIRROR_STALE_MUTATION"
        );
        QueueAuthority.CommitResult staleDrainCommit = shadowDrainQueue.completeAndHandoff(
            prePrepareClaim,
            Hashing.canonicalHash(Map.of("output", "must-not-commit-after-rotation")),
            QueueAuthority.Stage.AGENT2
        );
        boolean staleInvocationBlocked = !staleDrainCommit.accepted()
            && "STALE_GENERATION".equals(staleDrainCommit.reason());
        boolean staleGenerationBlocked = staleInformationBlocked
            && staleInvocationBlocked
            && staleTemporalBlocked
            && staleMutationBlocked;
        boolean inFlightDrainVerified = staleInvocationBlocked;

        boolean freshGenerationAdmissible = verifyFreshPreparedGeneration(adapters, preparedTokens);

        Map<String, Object> rolledBack = store.rollback(
            text(prepared.get("stateHash")), "v24_26_production_mirror_rollback_window"
        );
        String rollbackOwnerHash = ownerHash(rolledBack);
        boolean preparedGenerationInvalidAfterRollback = staleBlocked(
            adapters.get("TEMPORAL"), preparedTokens.get("TEMPORAL"), "MIRROR_PREPARED_AFTER_ROLLBACK"
        );
        boolean rollbackWindowVerified = preparedGenerationInvalidAfterRollback;
        boolean productionOwnerBoundaryStable = initialOwnerHash.equals(preparedOwnerHash)
            && initialOwnerHash.equals(rollbackOwnerHash);
        boolean productionMutationAllowed = Boolean.TRUE.equals(rolledBack.get("productionMutationAllowed"));

        LinkedHashMap<String, Object> replayEvidence = new LinkedHashMap<>();
        replayEvidence.put("schema", ProductionMirrorParityAuthority.EVIDENCE_SCHEMA);
        replayEvidence.put("version", ProductionMirrorParityAuthority.VERSION);
        replayEvidence.put("evidenceSource", "REPOSITORY_REPLAY");
        replayEvidence.put("windows", replayWindows);
        replayEvidence.put("inFlightDrainVerified", inFlightDrainVerified);
        replayEvidence.put("staleGenerationBlocked", staleGenerationBlocked);
        replayEvidence.put("freshGenerationAdmissible", freshGenerationAdmissible);
        replayEvidence.put("rollbackWindowVerified", rollbackWindowVerified);
        replayEvidence.put("preparedGenerationInvalidAfterRollback", preparedGenerationInvalidAfterRollback);
        replayEvidence.put("productionOwnerBoundaryStable", productionOwnerBoundaryStable);
        replayEvidence.put("productionMutationAllowed", productionMutationAllowed);
        replayEvidence.put("productionOwnerHash", initialOwnerHash);

        Map<String, Object> replayDecision = ProductionMirrorParityAuthority.evaluate(mirrorPolicy, replayEvidence);
        Map<String, Object> tamperedDecision = ProductionMirrorParityAuthority.evaluate(
            mirrorPolicy,
            ProductionMirrorParityAuthority.tamperOneSample(replayEvidence)
        );
        boolean mismatchEvidenceFailClosed = !Boolean.TRUE.equals(tamperedDecision.get("verified"))
            && number(tamperedDecision.get("mismatchCount")) > 0L
            && !Boolean.TRUE.equals(tamperedDecision.get("cutoverQualificationReady"));

        boolean externalEvidencePresent = args.length == 3;
        Map<String, Object> externalDecision = Map.of();
        boolean externalProductionMirrorParityProven = false;
        if (externalEvidencePresent) {
            Map<String, Object> externalEvidence = readObject(Path.of(args[2]));
            externalDecision = ProductionMirrorParityAuthority.evaluate(mirrorPolicy, externalEvidence);
            externalProductionMirrorParityProven = Boolean.TRUE.equals(
                externalDecision.get("externalProductionMirrorParityProven")
            );
        }

        boolean mechanismVerified = Boolean.TRUE.equals(replayDecision.get("mirrorMechanismVerified"))
            && Boolean.TRUE.equals(replayDecision.get("repositoryReplayMechanismOnly"))
            && !Boolean.TRUE.equals(replayDecision.get("externalProductionMirrorParityProven"))
            && staleGenerationBlocked
            && freshGenerationAdmissible
            && rollbackWindowVerified
            && productionOwnerBoundaryStable
            && !productionMutationAllowed
            && mismatchEvidenceFailClosed;

        String status = externalProductionMirrorParityProven
            ? "PRODUCTION_MIRROR_PARITY_PROVEN_OWNER_TRANSFER_GATE_REQUIRED"
            : "MIRROR_MECHANISM_VERIFIED_EXTERNAL_EVIDENCE_REQUIRED";

        LinkedHashMap<String, Object> report = new LinkedHashMap<>();
        report.put("schema", "v24.production_mirror_parity_rollback_window.verification.v1");
        report.put("version", ProductionMirrorParityAuthority.VERSION);
        report.put("verified", mechanismVerified);
        report.put("enforcementMode", "SHADOW");
        report.put("rootSource", "AuthorityGenerationStore");
        report.put("mirrorMechanismVerified", mechanismVerified);
        report.put("repositoryReplayMechanismOnly", true);
        report.put("replayWindowCount", replayWindows.size());
        report.put("staleInformationBlocked", staleInformationBlocked);
        report.put("staleInvocationBlocked", staleInvocationBlocked);
        report.put("staleTemporalBlocked", staleTemporalBlocked);
        report.put("staleMutationBlocked", staleMutationBlocked);
        report.put("staleGenerationBlocked", staleGenerationBlocked);
        report.put("inFlightDrainVerified", inFlightDrainVerified);
        report.put("freshGenerationAdmissible", freshGenerationAdmissible);
        report.put("rollbackWindowVerified", rollbackWindowVerified);
        report.put("preparedGenerationInvalidAfterRollback", preparedGenerationInvalidAfterRollback);
        report.put("productionOwnerBoundaryStable", productionOwnerBoundaryStable);
        report.put("productionMutationAllowed", productionMutationAllowed);
        report.put("mismatchEvidenceFailClosed", mismatchEvidenceFailClosed);
        report.put("externalEvidencePresent", externalEvidencePresent);
        report.put("externalProductionMirrorParityProven", externalProductionMirrorParityProven);
        report.put("externalDecision", externalDecision);
        report.put("productionAuthorityOwnershipChanged", false);
        report.put("authorityGrantCreated", false);
        report.put("cutoverQualificationReady", externalProductionMirrorParityProven);
        report.put("cutoverAllowed", false);
        report.put("status", status);
        report.put("nextRequiredGate", externalProductionMirrorParityProven
            ? "OWNER_TRANSFER_PREPARE_WITH_LIVE_ROLLBACK_GUARD"
            : "SEALED_EXTERNAL_PRODUCTION_MIRROR_RECEIPTS");
        report.put("initialOwnerHash", initialOwnerHash);
        report.put("preparedOwnerHash", preparedOwnerHash);
        report.put("rollbackOwnerHash", rollbackOwnerHash);
        report.put("replayDecision", replayDecision);
        report.put("policyHash", Hashing.canonicalHash(mirrorPolicy));
        report.put("kernelPolicyHash", Hashing.canonicalHash(kernelPolicy));
        report.put("verificationHash", Hashing.canonicalHash(report));

        if (!mechanismVerified) {
            throw new IllegalStateException(
                "v24_26_production_mirror_parity_rollback_window_failed:" + Json.canonical(report)
            );
        }
        System.out.println(Json.canonical(report));
    }

    private static Map<String, Object> buildReplayWindow(
        String windowId,
        int seed,
        Map<String, RootBoundAuthorityAdapter> adapters
    ) {
        RootBoundAuthorityAdapter.Token generation = adapters.get("INFORMATION").token();
        List<Object> samples = new ArrayList<>();

        for (int i = 1; i <= 3; i++) {
            LinkedHashMap<String, Map<String, Object>> fieldIndex = new LinkedHashMap<>();
            String canonicalField = "shop.sales." + seed + "." + i;
            fieldIndex.put(canonicalField, Map.of("canonicalField", canonicalField));
            Map<String, Object> aliasRegistry = Map.of(
                "schema", "rag.alias_registry.v1",
                "defaultDecision", "BLOCK",
                "entries", List.of(Map.of(
                    "canonicalField", canonicalField,
                    "aliases", List.of("sales-" + seed + "-" + i, "gmv-" + seed + "-" + i)
                ))
            );
            Map<String, Object> input = Map.of("aliasRegistry", aliasRegistry, "fieldIndex", fieldIndex);
            Map<String, String> production = V25RetrievalAuthority.verifyAliasIndex(aliasRegistry, fieldIndex);
            Map<String, String> shadow = adapters.get("INFORMATION").execute(
                adapters.get("INFORMATION").token(),
                "MIRROR_INFORMATION_" + seed + "_" + i,
                () -> V25RetrievalAuthority.verifyAliasIndex(aliasRegistry, fieldIndex)
            );
            samples.add(sample("INFORMATION", "INF-" + seed + "-" + i, input, production, shadow));
        }

        String[][] transitions = {
            {"待接收", "处理中"},
            {"处理中", "待复核"},
            {"已归档", "处理中"}
        };
        for (int i = 0; i < transitions.length; i++) {
            String from = transitions[i][0];
            String to = transitions[i][1];
            long version = seed + i + 1L;
            Map<String, Object> input = Map.of(
                "fromStatus", from, "toStatus", to,
                "currentVersion", version, "expectedVersion", version
            );
            Map<String, Object> production = TaskStateAuthority.decide(from, to, version, version);
            Map<String, Object> shadow = adapters.get("TEMPORAL").execute(
                adapters.get("TEMPORAL").token(),
                "MIRROR_TEMPORAL_" + seed + "_" + i,
                () -> TaskStateAuthority.decide(from, to, version, version)
            );
            samples.add(sample("TEMPORAL", "TMP-" + seed + "-" + (i + 1), input, production, shadow));
        }

        Map<String, Object> gateDefinitions = gateDefinitions();
        for (int i = 1; i <= 3; i++) {
            Map<String, Object> input = Map.of("ready", i != 2, "sequence", seed * 10 + i);
            GateEngine productionGate = new GateEngine(gateDefinitions);
            GateEngine shadowGate = new GateEngine(gateDefinitions);
            Map<String, Object> production = productionGate.evaluate("MIRROR_GATE", input);
            Map<String, Object> shadow = adapters.get("MUTATION").execute(
                adapters.get("MUTATION").token(),
                "MIRROR_MUTATION_" + seed + "_" + i,
                () -> shadowGate.evaluate("MIRROR_GATE", input)
            );
            samples.add(sample("MUTATION", "MUT-" + seed + "-" + i, input, production, shadow));
        }

        for (int i = 1; i <= 3; i++) {
            String itemId = "ITEM-MIRROR-" + seed + "-" + i;
            String contract = "v24.26-mirror-window-" + seed;
            String inputHash = Hashing.canonicalHash(Map.of("itemId", itemId, "seed", seed, "sample", i));
            QueueAuthority productionQueue = new QueueAuthority(new GenerationFencer(), contract);
            QueueAuthority shadowQueue = adapters.get("INVOCATION").queue(contract);
            productionQueue.registerItem(itemId, "DV-MIRROR-" + seed, i);
            shadowQueue.registerItem(itemId, "DV-MIRROR-" + seed, i);
            QueueAuthority.EnqueueResult productionEnqueue = productionQueue.enqueue(
                itemId, QueueAuthority.Stage.AGENT1, inputHash, i
            );
            QueueAuthority.EnqueueResult shadowEnqueue = shadowQueue.enqueue(
                itemId, QueueAuthority.Stage.AGENT1, inputHash, i
            );
            Map<String, Object> input = Map.of(
                "itemId", itemId,
                "stage", QueueAuthority.Stage.AGENT1.name(),
                "inputArtifactHash", inputHash,
                "priority", i
            );
            Map<String, Object> production = enqueueResult(productionEnqueue);
            Map<String, Object> shadow = enqueueResult(shadowEnqueue);
            samples.add(sample("INVOCATION", "INV-" + seed + "-" + i, input, production, shadow));
        }

        LinkedHashMap<String, Object> window = new LinkedHashMap<>();
        window.put("windowId", windowId);
        window.put("sealed", true);
        window.put("generationSeq", generation.generationSeq());
        window.put("generationHash", generation.generationHash());
        window.put("fencingToken", generation.fencingToken());
        window.put("samples", samples);
        window.put("sampleSetHash", Hashing.canonicalHash(samples));
        return window;
    }

    private static boolean verifyFreshPreparedGeneration(
        Map<String, RootBoundAuthorityAdapter> adapters,
        Map<String, RootBoundAuthorityAdapter.Token> preparedTokens
    ) {
        boolean information = "PASS".equals(text(adapters.get("INFORMATION").execute(
            preparedTokens.get("INFORMATION"), "FRESH_PREPARED_INFORMATION", () -> Map.of("decision", "PASS")
        ).get("decision")));
        boolean temporal = "PASS".equals(text(adapters.get("TEMPORAL").execute(
            preparedTokens.get("TEMPORAL"), "FRESH_PREPARED_TEMPORAL",
            () -> TaskStateAuthority.decide("待接收", "处理中", 7L, 7L)
        ).get("decision")));
        GateEngine gate = new GateEngine(gateDefinitions());
        boolean mutation = "PASS".equals(text(adapters.get("MUTATION").execute(
            preparedTokens.get("MUTATION"), "FRESH_PREPARED_MUTATION",
            () -> gate.evaluate("MIRROR_GATE", Map.of("ready", true, "sequence", 999))
        ).get("decision")));

        QueueAuthority queue = adapters.get("INVOCATION").queue("v24.26-fresh-prepared");
        String inputHash = Hashing.canonicalHash(Map.of("input", "fresh-prepared"));
        queue.registerItem("ITEM-FRESH-PREPARED", "DV-FRESH-PREPARED", 1);
        queue.enqueue("ITEM-FRESH-PREPARED", QueueAuthority.Stage.AGENT1, inputHash, 1);
        QueueAuthority.Claim claim = queue.claim(QueueAuthority.Stage.AGENT1, "fresh-prepared-worker", 60_000L);
        boolean invocation = claim != null && queue.completeAndHandoff(
            claim,
            Hashing.canonicalHash(Map.of("output", "fresh-prepared")),
            null
        ).accepted();
        return information && temporal && mutation && invocation;
    }

    private static Map<String, Object> sample(
        String domain,
        String sampleId,
        Object input,
        Object productionResult,
        Object shadowResult
    ) {
        LinkedHashMap<String, Object> sample = new LinkedHashMap<>();
        sample.put("sampleId", sampleId);
        sample.put("domain", domain);
        sample.put("inputHash", Hashing.canonicalHash(input));
        sample.put("productionResultHash", Hashing.canonicalHash(productionResult));
        sample.put("shadowResultHash", Hashing.canonicalHash(shadowResult));
        sample.put("shadowWriteAttempted", false);
        sample.put("productionOwnerUnchanged", true);
        sample.put("receiptHash", Hashing.canonicalHash(sample));
        return sample;
    }

    private static Map<String, Object> enqueueResult(QueueAuthority.EnqueueResult value) {
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        out.put("jobId", value.jobId());
        out.put("idempotencyKey", value.idempotencyKey());
        out.put("duplicateSuppressed", value.duplicateSuppressed());
        return out;
    }

    private static Map<String, Object> gateDefinitions() {
        return Map.of(
            "defaultDecision", "BLOCK",
            "gates", List.of(Map.of(
                "gateId", "MIRROR_GATE",
                "predicates", List.of(Map.of("path", "ready", "op", "equals", "value", true)),
                "passDecision", "PASS",
                "failDecision", "BLOCK"
            ))
        );
    }

    private static Map<String, RootBoundAuthorityAdapter.Token> tokens(
        Map<String, RootBoundAuthorityAdapter> adapters
    ) {
        LinkedHashMap<String, RootBoundAuthorityAdapter.Token> out = new LinkedHashMap<>();
        for (String domain : ProductionMirrorParityAuthority.DOMAINS.stream().sorted().toList()) {
            out.put(domain, adapters.get(domain).token());
        }
        return out;
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
                && expected.getMessage().startsWith("stale_root_bound_authority_generation:" + adapter.domain() + ":");
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

    private static Map<String, Object> readObject(Path path) throws Exception {
        return Json.object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
    }

    private static long number(Object value) {
        if (value instanceof Number number) return number.longValue();
        try {
            return Long.parseLong(text(value));
        } catch (Exception ignored) {
            return -1L;
        }
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private static void require(boolean condition, String error) {
        if (!condition) throw new IllegalStateException(error);
    }
}
