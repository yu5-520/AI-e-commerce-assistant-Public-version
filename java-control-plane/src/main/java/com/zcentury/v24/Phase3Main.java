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
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

/** V24.9-V24.15 executable verifier for Queue Split through Generation Fencing. */
public final class Phase3Main {
    private static final String VERSION = "24.15.0-phase3.1";
    private static final String CONTRACT_VERSION = "v24.agent-stage.contract.v1";

    private Phase3Main() {}

    public static void main(String[] args) throws Exception {
        Map<String, String> options = options(args);
        Path root = Paths.get(options.getOrDefault("root", ".")).toAbsolutePath().normalize();
        Path evidencePath = resolve(root, options.getOrDefault("evidence", "dist/v24-java-phase3/python-queue-baseline.json"));
        Path policyPath = resolve(root, options.getOrDefault("policy", "governance/v24/phase3-queue-authority-policy.json"));
        Path sqlPath = resolve(root, options.getOrDefault("sql", "governance/v24/phase3-postgresql-queue-schema.sql"));
        Path output = resolve(root, options.getOrDefault("output", "dist/v24-java-phase3/phase3-verification-report.json"));

        Map<String, Object> evidence = readObject(evidencePath);
        Map<String, Object> policy = readObject(policyPath);
        verifyEvidence(evidence);
        verifyPolicy(policy);
        verifySqlContract(sqlPath);

        Map<String, Object> idempotency = verifyIdempotency();
        Map<String, Object> claim = verifyConcurrentClaimSingleWinner();
        Map<String, Object> lease = verifyLeaseRecovery();
        Map<String, Object> handoff = verifyThreeStageHandoff();
        Map<String, Object> generation = verifyGenerationFencing();
        Map<String, Object> flow = verifyPipelineOverlapAndBackpressure(policy);

        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("schema", "v24.phase3_verification.v1");
        material.put("version", VERSION);
        material.put("verified", true);
        material.put("enforcementMode", "SHADOW");
        material.put("phaseCoverage", List.of(
            "V24.9_QUEUE_SPLIT",
            "V24.10_AGENT1_QUEUE",
            "V24.11_AGENT2_ASYNC",
            "V24.12_AGENT3_ASYNC",
            "V24.13_IDEMPOTENCY",
            "V24.14_CONCURRENCY_BACKPRESSURE",
            "V24.15_GENERATION_FENCING"
        ));
        material.put("queueSplitAuthority", "JAVA_SHADOW_STATE_JOB_ARTIFACT_OUTBOX_SEPARATED");
        material.put("agent1QueueAuthority", "JAVA_SHADOW_STAGE_INDEPENDENT");
        material.put("agent2QueueAuthority", "JAVA_SHADOW_STAGE_INDEPENDENT");
        material.put("agent3QueueAuthority", "JAVA_SHADOW_STAGE_INDEPENDENT");
        material.put("idempotencyAuthority", "JAVA_SHA256_SINGLE_JOB_PER_STAGE_INPUT");
        material.put("claimAuthority", "JAVA_SINGLE_WINNER_LEASE_MODEL");
        material.put("backpressureAuthority", "STAGE_LOCAL");
        material.put("generationAuthority", "JAVA_FENCING_TOKEN_SHADOW");
        material.put("pythonAgentProviderAuthorityUnchanged", true);
        material.put("pythonProductionQueueWriteAuthorityUnchanged", true);
        material.put("globalGenerationBarrierProductionStillEnabled", true);
        material.put("postgreSqlSourceOfTruthEnabled", false);
        material.put("javaProductionQueueCutoverEnabled", false);
        material.put("idempotencyTest", idempotency);
        material.put("concurrentClaimTest", claim);
        material.put("leaseRecoveryTest", lease);
        material.put("threeStageHandoffTest", handoff);
        material.put("generationFencingTest", generation);
        material.put("pipelineFlowTest", flow);
        material.put("pythonEvidenceHash", evidence.get("evidenceHash"));
        material.put("policyHash", Hashing.canonicalHash(policy));
        material.put("sqlContractHash", Hashing.fileHash(sqlPath));

        String verificationHash = Hashing.canonicalHash(material);
        LinkedHashMap<String, Object> report = new LinkedHashMap<>(material);
        report.put("verificationHash", verificationHash);
        Files.createDirectories(output.getParent());
        Files.writeString(output, Json.canonical(report) + "\n", StandardCharsets.UTF_8);
        System.out.println(Json.canonical(report));
    }

    private static Map<String, Object> verifyIdempotency() {
        GenerationFencer fencer = new GenerationFencer();
        QueueAuthority queue = new QueueAuthority(fencer, CONTRACT_VERSION);
        queue.registerItem("ITEM-IDEMP", "DV-PHASE3", 10);
        String inputHash = Hashing.canonicalHash(Map.of("input", "same"));
        QueueAuthority.EnqueueResult first = null;
        for (int i = 0; i < 10; i++) {
            QueueAuthority.EnqueueResult result = queue.enqueue("ITEM-IDEMP", QueueAuthority.Stage.AGENT1, inputHash, 10);
            if (i == 0) first = result;
            else require(result.duplicateSuppressed(), "duplicate_enqueue_must_be_suppressed:" + i);
        }
        require(first != null && !first.duplicateSuppressed(), "first_enqueue_must_create_job");
        require(queue.jobCount() == 1, "idempotency_unique_job_count_mismatch:" + queue.jobCount());
        require(queue.duplicateSuppressedCount() == 9L, "duplicate_suppressed_count_mismatch");
        return Map.of(
            "duplicateAttempts", 10L,
            "uniqueJobCount", queue.jobCount(),
            "duplicateSuppressedCount", queue.duplicateSuppressedCount(),
            "verified", true
        );
    }

    private static Map<String, Object> verifyConcurrentClaimSingleWinner() throws Exception {
        GenerationFencer fencer = new GenerationFencer();
        QueueAuthority queue = new QueueAuthority(fencer, CONTRACT_VERSION);
        queue.registerItem("ITEM-CLAIM", "DV-PHASE3", 1);
        queue.enqueue("ITEM-CLAIM", QueueAuthority.Stage.AGENT1, Hashing.canonicalHash(Map.of("claim", 1L)), 1);

        int attempts = 16;
        ExecutorService pool = Executors.newFixedThreadPool(8);
        try {
            ArrayList<Callable<QueueAuthority.Claim>> tasks = new ArrayList<>();
            for (int i = 0; i < attempts; i++) {
                final int worker = i;
                tasks.add(() -> queue.claim(QueueAuthority.Stage.AGENT1, "claim-worker-" + worker, 30_000L));
            }
            List<Future<QueueAuthority.Claim>> futures = pool.invokeAll(tasks);
            int winners = 0;
            for (Future<QueueAuthority.Claim> future : futures) if (future.get() != null) winners++;
            require(winners == 1, "concurrent_claim_winner_count_mismatch:" + winners);
            return Map.of("attempts", attempts, "winners", winners, "verified", true);
        } finally {
            pool.shutdownNow();
        }
    }

    private static Map<String, Object> verifyLeaseRecovery() {
        GenerationFencer fencer = new GenerationFencer();
        QueueAuthority queue = new QueueAuthority(fencer, CONTRACT_VERSION);
        queue.registerItem("ITEM-LEASE", "DV-PHASE3", 5);
        queue.enqueue("ITEM-LEASE", QueueAuthority.Stage.AGENT1, Hashing.canonicalHash(Map.of("lease", 1L)), 5);
        QueueAuthority.Claim first = queue.claim(QueueAuthority.Stage.AGENT1, "lease-worker-1", 0L);
        require(first != null, "lease_first_claim_missing");
        int recovered = queue.recoverExpired(Long.MAX_VALUE);
        require(recovered == 1, "expired_lease_not_recovered:" + recovered);
        QueueAuthority.Claim second = queue.claim(QueueAuthority.Stage.AGENT1, "lease-worker-2", 30_000L);
        require(second != null, "lease_second_claim_missing");
        require(second.attemptCount() == 2, "lease_attempt_count_mismatch:" + second.attemptCount());
        return Map.of("recovered", recovered, "secondAttempt", second.attemptCount(), "verified", true);
    }

    private static Map<String, Object> verifyThreeStageHandoff() {
        GenerationFencer fencer = new GenerationFencer();
        QueueAuthority queue = new QueueAuthority(fencer, CONTRACT_VERSION);
        queue.registerItem("ITEM-FLOW", "DV-PHASE3", 20);
        String seed = Hashing.canonicalHash(Map.of("seed", "snapshot"));
        queue.enqueue("ITEM-FLOW", QueueAuthority.Stage.AGENT1, seed, 20);

        QueueAuthority.Claim a1 = queue.claim(QueueAuthority.Stage.AGENT1, "agent1-worker", 30_000L);
        require(a1 != null, "agent1_claim_missing");
        QueueAuthority.CommitResult c1 = queue.completeAndHandoff(a1, Hashing.canonicalHash(Map.of("agent1", "ok")), QueueAuthority.Stage.AGENT2);
        require(c1.accepted() && c1.nextStage() == QueueAuthority.Stage.AGENT2, "agent1_handoff_failed:" + c1);

        QueueAuthority.Claim a2 = queue.claim(QueueAuthority.Stage.AGENT2, "agent2-worker", 30_000L);
        require(a2 != null, "agent2_claim_missing");
        QueueAuthority.CommitResult c2 = queue.completeAndHandoff(a2, Hashing.canonicalHash(Map.of("agent2", "ok")), QueueAuthority.Stage.AGENT3);
        require(c2.accepted() && c2.nextStage() == QueueAuthority.Stage.AGENT3, "agent2_handoff_failed:" + c2);

        QueueAuthority.Claim a3 = queue.claim(QueueAuthority.Stage.AGENT3, "agent3-worker", 30_000L);
        require(a3 != null, "agent3_claim_missing");
        QueueAuthority.CommitResult c3 = queue.completeAndHandoff(a3, Hashing.canonicalHash(Map.of("agent3", "ok")), null);
        require(c3.accepted() && c3.nextStage() == QueueAuthority.Stage.COMPLETE, "agent3_completion_failed:" + c3);

        Map<String, Object> item = queue.itemSnapshot("ITEM-FLOW");
        require("COMPLETE".equals(text(item.get("currentStage"))), "pipeline_item_not_complete:" + item);
        require(number(item.get("stateVersion")) == 4L, "pipeline_item_version_mismatch:" + item);
        require(queue.artifactCount() == 3, "artifact_count_mismatch:" + queue.artifactCount());
        require(queue.outboxCount() == 2, "outbox_handoff_count_mismatch:" + queue.outboxCount());
        return Map.of(
            "agent1ToAgent2", true,
            "agent2ToAgent3", true,
            "agent3ToComplete", true,
            "artifactCount", queue.artifactCount(),
            "outboxHandoffCount", queue.outboxCount(),
            "finalStateVersion", number(item.get("stateVersion")),
            "verified", true
        );
    }

    private static Map<String, Object> verifyGenerationFencing() {
        GenerationFencer fencer = new GenerationFencer();
        QueueAuthority queue = new QueueAuthority(fencer, CONTRACT_VERSION);
        queue.registerItem("ITEM-GEN", "DV-G1", 1);
        queue.enqueue("ITEM-GEN", QueueAuthority.Stage.AGENT1, Hashing.canonicalHash(Map.of("generation", 1L)), 1);
        QueueAuthority.Claim claim = queue.claim(QueueAuthority.Stage.AGENT1, "old-generation-worker", 30_000L);
        require(claim != null, "generation_claim_missing");
        long oldSeq = claim.fence().generationSeq();
        GenerationFencer.Snapshot rotated = fencer.rotate("phase3_reset_test");
        require(rotated.generationSeq() > oldSeq, "generation_did_not_rotate");
        QueueAuthority.CommitResult result = queue.completeAndHandoff(
            claim,
            Hashing.canonicalHash(Map.of("stale", "result")),
            QueueAuthority.Stage.AGENT2
        );
        require(!result.accepted(), "stale_generation_commit_must_be_blocked");
        require("STALE_GENERATION".equals(result.reason()), "stale_generation_reason_mismatch:" + result.reason());
        return Map.of(
            "oldGenerationSeq", oldSeq,
            "newGenerationSeq", rotated.generationSeq(),
            "staleCommitAccepted", false,
            "reason", result.reason(),
            "verified", true
        );
    }

    private static Map<String, Object> verifyPipelineOverlapAndBackpressure(Map<String, Object> policy) {
        Map<String, Object> stages = Json.object(policy.get("stages"));
        int a1Capacity = capacity(stages, "AGENT1");
        int a2Capacity = capacity(stages, "AGENT2");
        int a3Capacity = capacity(stages, "AGENT3");
        PipelineFlowSimulator normal = new PipelineFlowSimulator(a1Capacity, a2Capacity, a3Capacity, 5L, 7L, 11L);
        PipelineFlowSimulator slowAgent3 = new PipelineFlowSimulator(a1Capacity, a2Capacity, a3Capacity, 5L, 7L, 100L);
        Map<String, Object> baseline = normal.simulate(18);
        Map<String, Object> stressed = slowAgent3.simulate(18);
        require(Boolean.TRUE.equals(baseline.get("crossStagePipelineOverlap")), "cross_stage_pipeline_overlap_required:" + baseline);
        require(number(baseline.get("maxConcurrentStages")) >= 2L, "multiple_stages_must_overlap:" + baseline);
        require(number(baseline.get("agent1Finish")) == number(stressed.get("agent1Finish")), "agent3_backpressure_must_not_stop_agent1");
        require(number(stressed.get("pipelineFinish")) > number(baseline.get("pipelineFinish")), "slow_agent3_should_extend_pipeline_finish");
        LinkedHashMap<String, Object> result = new LinkedHashMap<>();
        result.put("itemCount", baseline.get("itemCount"));
        result.put("crossStagePipelineOverlap", baseline.get("crossStagePipelineOverlap"));
        result.put("maxConcurrentStages", baseline.get("maxConcurrentStages"));
        result.put("normalAgent1Finish", baseline.get("agent1Finish"));
        result.put("slowAgent3Agent1Finish", stressed.get("agent1Finish"));
        result.put("normalPipelineFinish", baseline.get("pipelineFinish"));
        result.put("slowAgent3PipelineFinish", stressed.get("pipelineFinish"));
        result.put("agent3BackpressureIsolatedFromAgent1", true);
        result.put("verified", true);
        return result;
    }

    private static void verifyEvidence(Map<String, Object> evidence) {
        String declared = text(evidence.get("evidenceHash"));
        LinkedHashMap<String, Object> material = new LinkedHashMap<>(evidence);
        material.remove("evidenceHash");
        require(declared.equals(Hashing.canonicalHash(material)), "phase3_python_evidence_hash_mismatch");
        require("PYTHON_UNCHANGED".equals(text(evidence.get("productionQueueWriteAuthority"))), "python_queue_write_boundary_changed");
        Map<String, Object> baseline = Json.object(evidence.get("legacyBaseline"));
        require(Boolean.TRUE.equals(baseline.get("singleWorkerOwnership")), "legacy_single_worker_baseline_required");
        require(Boolean.FALSE.equals(baseline.get("secondWorkerAllowed")), "legacy_second_worker_must_be_false");
        require(Boolean.TRUE.equals(baseline.get("globalGenerationBarrier")), "legacy_generation_barrier_required");
        require(Boolean.TRUE.equals(baseline.get("agent1BlockedByDownstream")), "legacy_downstream_stage_barrier_required");
        require(Boolean.TRUE.equals(baseline.get("pipelineItemsMultiplexesStateAndQueue")), "legacy_pipeline_item_multiplexing_required");
        Map<String, Object> stageOrder = Json.object(evidence.get("stageOrder"));
        require(stageOrder.containsKey("agent1_pending"), "legacy_agent1_stage_missing");
        require(stageOrder.containsKey("agent2_running"), "legacy_agent2_stage_missing");
        require(stageOrder.containsKey("agent2_completed"), "legacy_agent2_completed_stage_missing");
    }

    private static void verifyPolicy(Map<String, Object> policy) {
        require("SHADOW".equals(text(policy.get("enforcementMode"))), "phase3_must_start_shadow");
        Map<String, Object> queueModel = Json.object(policy.get("queueModel"));
        require("BUSINESS_STATE_ONLY".equals(text(queueModel.get("pipelineItem"))), "pipeline_item_split_required");
        require("EXECUTION_WORK_ONLY".equals(text(queueModel.get("stageJob"))), "stage_job_split_required");
        require("IMMUTABLE_INPUT_OUTPUT_ONLY".equals(text(queueModel.get("artifact"))), "artifact_split_required");
        Map<String, Object> concurrency = Json.object(policy.get("concurrency"));
        require(Boolean.FALSE.equals(concurrency.get("globalStageBarrierAllowed")), "global_stage_barrier_must_be_disallowed");
        require(Boolean.TRUE.equals(concurrency.get("stageWorkerPoolsIndependent")), "stage_worker_pools_must_be_independent");
        require(Boolean.FALSE.equals(concurrency.get("agent1BlockedByAgent2OrAgent3Backlog")), "agent1_backlog_block_must_be_false");
        Map<String, Object> idempotency = Json.object(policy.get("idempotency"));
        require("SHA-256".equals(text(idempotency.get("algorithm"))), "idempotency_sha256_required");
        require(Boolean.FALSE.equals(idempotency.get("duplicateBusinessExecutionAllowed")), "duplicate_business_execution_must_be_false");
        Map<String, Object> generation = Json.object(policy.get("generation"));
        require("FENCING_TOKEN".equals(text(generation.get("mode"))), "generation_fencing_token_required");
        require("BLOCK".equals(text(generation.get("staleGenerationCommit"))), "stale_generation_commit_must_block");
        Map<String, Object> boundary = Json.object(policy.get("productionBoundary"));
        require(Boolean.TRUE.equals(boundary.get("pythonProductionQueueWriteAuthorityUnchanged")), "python_queue_writer_boundary_required");
        require(Boolean.FALSE.equals(boundary.get("postgreSqlSourceOfTruthEnabled")), "postgres_not_enabled_in_phase3_shadow");
        require(Boolean.FALSE.equals(boundary.get("javaProductionQueueCutoverEnabled")), "java_queue_cutover_must_remain_false_in_shadow");
    }

    private static void verifySqlContract(Path sqlPath) throws IOException {
        require(Files.isRegularFile(sqlPath), "phase3_sql_contract_missing:" + sqlPath);
        String sql = Files.readString(sqlPath, StandardCharsets.UTF_8);
        for (String marker : List.of(
            "CREATE TABLE IF NOT EXISTS v24_pipeline_item",
            "CREATE TABLE IF NOT EXISTS v24_stage_job",
            "CREATE TABLE IF NOT EXISTS v24_artifact",
            "CREATE TABLE IF NOT EXISTS v24_outbox_event",
            "UNIQUE(stage, idempotency_key)",
            "FOR UPDATE SKIP LOCKED",
            "fencing_token"
        )) require(sql.contains(marker), "phase3_sql_marker_missing:" + marker);
    }

    private static int capacity(Map<String, Object> stages, String stage) {
        Map<String, Object> value = Json.object(stages.get(stage));
        long capacity = number(value.get("capacity"));
        require(capacity > 0L && capacity <= 100L, "invalid_stage_capacity:" + stage + ":" + capacity);
        return (int) capacity;
    }

    private static Map<String, Object> readObject(Path path) throws IOException {
        require(Files.isRegularFile(path), "json_file_missing:" + path);
        return Json.object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
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

    private static long number(Object value) {
        if (!(value instanceof Number number)) throw new IllegalArgumentException("number_required:" + value);
        return number.longValue();
    }

    private static String text(Object value) { return value == null ? "" : String.valueOf(value); }
    private static void require(boolean condition, String message) { if (!condition) throw new IllegalStateException(message); }
}
