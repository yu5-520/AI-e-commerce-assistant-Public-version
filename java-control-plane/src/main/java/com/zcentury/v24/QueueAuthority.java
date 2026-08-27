package com.zcentury.v24;

import java.util.ArrayList;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.PriorityBlockingQueue;
import java.util.concurrent.atomic.AtomicLong;

/**
 * V24.9-V24.15 dependency-free shadow Queue Authority.
 *
 * The model deliberately separates business state (PipelineItem), execution work
 * (StageJob), immutable IO (ArtifactRecord), and handoff intent (OutboxEvent).
 * It is an executable contract for the later PostgreSQL/worker cutover; it does not
 * mutate the current Python competition runtime in Phase3 Shadow.
 */
final class QueueAuthority {
    enum Stage { AGENT1, AGENT2, AGENT3, COMPLETE }
    enum JobStatus { READY, RUNNING, COMPLETED, RETRY, FAILED }

    record ArtifactRecord(
        String artifactRef,
        String itemId,
        Stage stage,
        String contentHash,
        String parentArtifactHash,
        String contractVersion,
        long generationSeq
    ) {}

    record EnqueueResult(String jobId, String idempotencyKey, boolean duplicateSuppressed) {}

    record Claim(
        String jobId,
        String itemId,
        Stage stage,
        String claimId,
        String claimOwner,
        GenerationFencer.Fence fence,
        int attemptCount
    ) {}

    record CommitResult(
        boolean accepted,
        String reason,
        String itemId,
        Stage completedStage,
        Stage nextStage,
        String outputArtifactRef,
        long itemStateVersion
    ) {}

    record OutboxEvent(
        String eventId,
        String itemId,
        Stage completedStage,
        Stage nextStage,
        String dedupeKey,
        String payloadHash,
        long generationSeq
    ) {}

    static final class PipelineItemState {
        final String itemId;
        final String dataVersion;
        final int priority;
        private Stage currentStage;
        private String status;
        private long generationSeq;
        private String generationHash;
        private long stateVersion;

        PipelineItemState(String itemId, String dataVersion, int priority, GenerationFencer.Snapshot generation) {
            this.itemId = itemId;
            this.dataVersion = dataVersion;
            this.priority = priority;
            this.currentStage = Stage.AGENT1;
            this.status = "READY";
            this.generationSeq = generation.generationSeq();
            this.generationHash = generation.generationHash();
            this.stateVersion = 1L;
        }

        synchronized void advance(Stage next, GenerationFencer.Snapshot generation) {
            this.currentStage = next;
            this.status = next == Stage.COMPLETE ? "COMPLETED" : "READY";
            this.generationSeq = generation.generationSeq();
            this.generationHash = generation.generationHash();
            this.stateVersion += 1L;
        }

        synchronized Map<String, Object> snapshot() {
            LinkedHashMap<String, Object> out = new LinkedHashMap<>();
            out.put("itemId", itemId);
            out.put("dataVersion", dataVersion);
            out.put("priority", priority);
            out.put("currentStage", currentStage.name());
            out.put("status", status);
            out.put("generationSeq", generationSeq);
            out.put("generationHash", generationHash);
            out.put("stateVersion", stateVersion);
            return out;
        }
    }

    private static final class StageJob implements Comparable<StageJob> {
        final String jobId;
        final String itemId;
        final Stage stage;
        final int priority;
        final String idempotencyKey;
        final String inputArtifactHash;
        final String contractVersion;
        final long createdSequence;
        final long generationSeq;
        final String generationHash;
        final long fencingToken;
        volatile JobStatus status = JobStatus.READY;
        volatile String claimOwner;
        volatile String claimId;
        volatile long leaseExpiresAtMillis;
        volatile int attemptCount;

        StageJob(
            String jobId,
            String itemId,
            Stage stage,
            int priority,
            String idempotencyKey,
            String inputArtifactHash,
            String contractVersion,
            long createdSequence,
            GenerationFencer.Snapshot generation
        ) {
            this.jobId = jobId;
            this.itemId = itemId;
            this.stage = stage;
            this.priority = priority;
            this.idempotencyKey = idempotencyKey;
            this.inputArtifactHash = inputArtifactHash;
            this.contractVersion = contractVersion;
            this.createdSequence = createdSequence;
            this.generationSeq = generation.generationSeq();
            this.generationHash = generation.generationHash();
            this.fencingToken = generation.fencingToken();
        }

        @Override
        public int compareTo(StageJob other) {
            int byPriority = Integer.compare(priority, other.priority);
            if (byPriority != 0) return byPriority;
            return Long.compare(createdSequence, other.createdSequence);
        }
    }

    private final GenerationFencer generationFencer;
    private final String contractVersion;
    private final Map<String, PipelineItemState> items = new ConcurrentHashMap<>();
    private final Map<String, StageJob> jobs = new ConcurrentHashMap<>();
    private final Map<String, String> idempotencyIndex = new ConcurrentHashMap<>();
    private final Map<String, ArtifactRecord> artifacts = new ConcurrentHashMap<>();
    private final ConcurrentLinkedQueue<OutboxEvent> outbox = new ConcurrentLinkedQueue<>();
    private final EnumMap<Stage, PriorityBlockingQueue<StageJob>> queues = new EnumMap<>(Stage.class);
    private final AtomicLong sequence = new AtomicLong();
    private final AtomicLong duplicateSuppressed = new AtomicLong();
    private final Object enqueueLock = new Object();

    QueueAuthority(GenerationFencer generationFencer, String contractVersion) {
        this.generationFencer = Objects.requireNonNull(generationFencer);
        this.contractVersion = Objects.requireNonNull(contractVersion);
        queues.put(Stage.AGENT1, new PriorityBlockingQueue<>());
        queues.put(Stage.AGENT2, new PriorityBlockingQueue<>());
        queues.put(Stage.AGENT3, new PriorityBlockingQueue<>());
    }

    PipelineItemState registerItem(String itemId, String dataVersion, int priority) {
        return items.computeIfAbsent(itemId, ignored ->
            new PipelineItemState(itemId, dataVersion, priority, generationFencer.current())
        );
    }

    EnqueueResult enqueue(String itemId, Stage stage, String inputArtifactHash, int priority) {
        if (stage == Stage.COMPLETE) throw new IllegalArgumentException("complete_stage_not_queueable");
        if (!items.containsKey(itemId)) throw new IllegalArgumentException("pipeline_item_missing:" + itemId);
        LinkedHashMap<String, Object> material = new LinkedHashMap<>();
        material.put("stage", stage.name());
        material.put("itemId", itemId);
        material.put("inputArtifactHash", inputArtifactHash);
        material.put("contractVersion", contractVersion);
        String idempotencyKey = Hashing.canonicalHash(material);

        synchronized (enqueueLock) {
            String existingId = idempotencyIndex.get(idempotencyKey);
            if (existingId != null) {
                duplicateSuppressed.incrementAndGet();
                return new EnqueueResult(existingId, idempotencyKey, true);
            }
            long seq = sequence.incrementAndGet();
            String jobId = "JOB-" + idempotencyKey.substring("sha256:".length(), "sha256:".length() + 20).toUpperCase();
            StageJob job = new StageJob(
                jobId, itemId, stage, priority, idempotencyKey,
                inputArtifactHash, contractVersion, seq, generationFencer.current()
            );
            jobs.put(jobId, job);
            idempotencyIndex.put(idempotencyKey, jobId);
            queue(stage).offer(job);
            return new EnqueueResult(jobId, idempotencyKey, false);
        }
    }

    Claim claim(Stage stage, String owner, long leaseMillis) {
        PriorityBlockingQueue<StageJob> queue = queue(stage);
        while (true) {
            StageJob job = queue.poll();
            if (job == null) return null;
            synchronized (job) {
                if (job.status != JobStatus.READY && job.status != JobStatus.RETRY) continue;
                GenerationFencer.Snapshot current = generationFencer.current();
                if (job.generationSeq != current.generationSeq()
                    || job.fencingToken != current.fencingToken()
                    || !job.generationHash.equals(current.generationHash())) {
                    job.status = JobStatus.FAILED;
                    continue;
                }
                job.status = JobStatus.RUNNING;
                job.attemptCount += 1;
                job.claimOwner = owner;
                job.claimId = "CLM-" + sequence.incrementAndGet();
                job.leaseExpiresAtMillis = System.currentTimeMillis() + Math.max(0L, leaseMillis);
                return new Claim(
                    job.jobId,
                    job.itemId,
                    job.stage,
                    job.claimId,
                    owner,
                    generationFencer.fence(),
                    job.attemptCount
                );
            }
        }
    }

    CommitResult completeAndHandoff(Claim claim, String outputContentHash, Stage nextStage) {
        if (claim == null) return new CommitResult(false, "CLAIM_REQUIRED", null, null, nextStage, null, -1L);
        if (!generationFencer.matches(claim.fence())) {
            return new CommitResult(false, "STALE_GENERATION", claim.itemId(), claim.stage(), nextStage, null, itemVersion(claim.itemId()));
        }
        StageJob job = jobs.get(claim.jobId());
        if (job == null) return new CommitResult(false, "JOB_NOT_FOUND", claim.itemId(), claim.stage(), nextStage, null, -1L);
        synchronized (job) {
            if (job.status != JobStatus.RUNNING) {
                return new CommitResult(false, "JOB_NOT_RUNNING", claim.itemId(), claim.stage(), nextStage, null, itemVersion(claim.itemId()));
            }
            if (!Objects.equals(job.claimId, claim.claimId())) {
                return new CommitResult(false, "CLAIM_MISMATCH", claim.itemId(), claim.stage(), nextStage, null, itemVersion(claim.itemId()));
            }
            if (!generationFencer.matches(claim.fence())) {
                return new CommitResult(false, "STALE_GENERATION", claim.itemId(), claim.stage(), nextStage, null, itemVersion(claim.itemId()));
            }
            PipelineItemState item = items.get(claim.itemId());
            if (item == null) return new CommitResult(false, "PIPELINE_ITEM_MISSING", claim.itemId(), claim.stage(), nextStage, null, -1L);

            String contentHash = outputContentHash;
            if (contentHash == null || contentHash.isBlank()) {
                contentHash = Hashing.canonicalHash(Map.of("jobId", job.jobId, "attempt", job.attemptCount));
            }
            String artifactRef = "ART-" + contentHash.substring("sha256:".length(), "sha256:".length() + 20).toUpperCase();
            artifacts.putIfAbsent(
                artifactRef,
                new ArtifactRecord(
                    artifactRef, job.itemId, job.stage, contentHash,
                    job.inputArtifactHash, contractVersion, claim.fence().generationSeq()
                )
            );
            job.status = JobStatus.COMPLETED;

            Stage resolvedNext = nextStage == null ? Stage.COMPLETE : nextStage;
            item.advance(resolvedNext, generationFencer.current());
            if (resolvedNext != Stage.COMPLETE) {
                EnqueueResult next = enqueue(job.itemId, resolvedNext, contentHash, item.priority);
                String outboxDedupe = Hashing.canonicalHash(Map.of(
                    "itemId", job.itemId,
                    "completedStage", job.stage.name(),
                    "nextStage", resolvedNext.name(),
                    "nextJob", next.jobId()
                ));
                String eventId = "OBX-" + outboxDedupe.substring("sha256:".length(), "sha256:".length() + 20).toUpperCase();
                outbox.add(new OutboxEvent(
                    eventId, job.itemId, job.stage, resolvedNext,
                    outboxDedupe, contentHash, claim.fence().generationSeq()
                ));
            }
            long version = itemVersion(job.itemId);
            return new CommitResult(true, "COMMITTED", job.itemId, job.stage, resolvedNext, artifactRef, version);
        }
    }

    int recoverExpired(long nowMillis) {
        int recovered = 0;
        GenerationFencer.Snapshot current = generationFencer.current();
        for (StageJob job : jobs.values()) {
            synchronized (job) {
                if (job.status != JobStatus.RUNNING || job.leaseExpiresAtMillis > nowMillis) continue;
                if (job.generationSeq != current.generationSeq() || job.fencingToken != current.fencingToken()) {
                    job.status = JobStatus.FAILED;
                    continue;
                }
                job.status = JobStatus.RETRY;
                job.claimOwner = null;
                job.claimId = null;
                job.leaseExpiresAtMillis = 0L;
                queue(job.stage).offer(job);
                recovered++;
            }
        }
        return recovered;
    }

    Map<String, Object> itemSnapshot(String itemId) {
        PipelineItemState item = items.get(itemId);
        return item == null ? Map.of() : item.snapshot();
    }

    long itemVersion(String itemId) {
        Object value = itemSnapshot(itemId).get("stateVersion");
        return value instanceof Number number ? number.longValue() : -1L;
    }

    int jobCount() { return jobs.size(); }
    int artifactCount() { return artifacts.size(); }
    int outboxCount() { return outbox.size(); }
    long duplicateSuppressedCount() { return duplicateSuppressed.get(); }
    int readyCount(Stage stage) { return queue(stage).size(); }

    List<Map<String, Object>> jobSnapshots() {
        ArrayList<Map<String, Object>> out = new ArrayList<>();
        for (StageJob job : jobs.values()) {
            synchronized (job) {
                LinkedHashMap<String, Object> row = new LinkedHashMap<>();
                row.put("jobId", job.jobId);
                row.put("itemId", job.itemId);
                row.put("stage", job.stage.name());
                row.put("status", job.status.name());
                row.put("priority", job.priority);
                row.put("idempotencyKey", job.idempotencyKey);
                row.put("generationSeq", job.generationSeq);
                row.put("fencingToken", job.fencingToken);
                row.put("attemptCount", job.attemptCount);
                out.add(row);
            }
        }
        return out;
    }

    private PriorityBlockingQueue<StageJob> queue(Stage stage) {
        PriorityBlockingQueue<StageJob> value = queues.get(stage);
        if (value == null) throw new IllegalArgumentException("stage_not_queueable:" + stage);
        return value;
    }
}
