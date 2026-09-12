package com.zcentury.v24;

import java.util.Map;
import java.util.LinkedHashMap;
import java.nio.file.*;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.util.function.BiFunction;
import java.util.concurrent.ConcurrentHashMap;

/**
 * V26 product-level lifecycle authority that exists outside Agent task state.
 *
 * V26.3 owns pre-Agent monitoring/observation admission. V26.4 extends that same
 * product authority through review readiness, deterministic review and adjustment.
 * TaskStateAuthority remains the separate operator/task execution state machine.
 */
final class ProductLifecycleAuthority {
    enum State {
        MONITORING,
        OBSERVING,
        REVIEW_READY,
        REVIEWING,
        ADJUSTMENT_REQUIRED
    }

    record Snapshot(
        String productId,
        State state,
        String activeTaskId,
        long observationStartedAtMillis,
        long reviewDueAtMillis,
        String lastReviewDecision,
        String lastReviewHash,
        long stateVersion,
        String reviewCommitJson
    ) {
        Snapshot(String productId, State state, String activeTaskId, long start, long due,
                 String decision, String reviewHash, long version) {
            this(productId, state, activeTaskId, start, due, decision, reviewHash, version, null);
        }
    }

    private final Map<String, Snapshot> products = new ConcurrentHashMap<>();

    private final Path storage;
    private static final Map<String, Object> LOCKS = new ConcurrentHashMap<>();

    // No-arg remains an explicit shadow/test adapter; production must supply durable storage.
    ProductLifecycleAuthority() { storage = null; }
    ProductLifecycleAuthority(Path storage) {
        this.storage = storage.toAbsolutePath().normalize();
    }

    private Snapshot compute(String id, BiFunction<String, Snapshot, Snapshot> mutation) {
        if (storage == null) return products.compute(id, mutation);
        synchronized (LOCKS.computeIfAbsent(storage.toString(), ignored -> new Object())) {
            try {
                Files.createDirectories(storage.getParent());
                try (FileChannel channel = FileChannel.open(Path.of(storage + ".lock"),
                        StandardOpenOption.CREATE, StandardOpenOption.WRITE);
                     var lock = channel.lock()) {
                    load();
                    Snapshot next = mutation.apply(id, products.get(id));
                    Map<String, Snapshot> updated = new LinkedHashMap<>(products);
                    updated.put(id, next);
                    LinkedHashMap<String, Object> rows = new LinkedHashMap<>();
                    updated.forEach((key, v) -> {
                        LinkedHashMap<String, Object> row = new LinkedHashMap<>();
                        row.put("productId", v.productId()); row.put("state", v.state().name());
                        row.put("activeTaskId", v.activeTaskId());
                        row.put("observationStartedAtMillis", v.observationStartedAtMillis());
                        row.put("reviewDueAtMillis", v.reviewDueAtMillis());
                        row.put("lastReviewDecision", v.lastReviewDecision()); row.put("lastReviewHash", v.lastReviewHash());
                        row.put("stateVersion", v.stateVersion());
                        row.put("reviewCommitJson", v.reviewCommitJson()); rows.put(key, row);
                    });
                    byte[] bytes = Json.canonical(Map.of("rows", rows, "hash", Hashing.canonicalHash(rows)))
                        .getBytes(StandardCharsets.UTF_8);
                    Path temp = Files.createTempFile(storage.getParent(), "lifecycle-", ".tmp");
                    try {
                        try (FileChannel out = FileChannel.open(temp, StandardOpenOption.WRITE)) {
                            var buffer = java.nio.ByteBuffer.wrap(bytes);
                            while (buffer.hasRemaining()) out.write(buffer);
                            out.force(true);
                        }
                        Files.move(temp, storage, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
                        try (FileChannel dir = FileChannel.open(storage.getParent(), StandardOpenOption.READ)) { dir.force(true); }
                    } finally { Files.deleteIfExists(temp); }
                    products.clear(); products.putAll(updated);
                    return next;
                }
            } catch (java.io.IOException exc) { throw new IllegalStateException("lifecycle_storage_failure", exc); }
        }
    }

    private void load() throws java.io.IOException {
        products.clear();
        if (!Files.exists(storage)) return;
        Map<String, Object> document = Json.object(Json.parse(Files.readString(storage)));
        Map<String, Object> rows = Json.object(document.get("rows"));
        if (!Hashing.canonicalHash(rows).equals(document.get("hash"))) throw new IllegalStateException("lifecycle_storage_hash_mismatch");
        rows.forEach((key, value) -> {
            Map<String, Object> row = Json.object(value);
            products.put(key, new Snapshot(key, State.valueOf((String) row.get("state")),
                (String) row.get("activeTaskId"), ((Number) row.get("observationStartedAtMillis")).longValue(),
                ((Number) row.get("reviewDueAtMillis")).longValue(), (String) row.get("lastReviewDecision"),
                (String) row.get("lastReviewHash"), ((Number) row.get("stateVersion")).longValue(), (String) row.get("reviewCommitJson")));
        });
    }

    Snapshot monitor(String productId) {
        String id = requireText(productId, "product_id_required");
        return compute(id, (ignored, previous) -> {
            if (previous != null) {
                if (previous.state() != State.MONITORING) throw new IllegalStateException("lifecycle_reset_forbidden");
                return previous;
            }
            return new Snapshot(
            id,
            State.MONITORING,
            null,
            0L,
            0L,
            previous == null ? null : previous.lastReviewDecision(),
            previous == null ? null : previous.lastReviewHash(),
            previous == null ? 1L : previous.stateVersion() + 1L
        ); });
    }

    Snapshot beginObservation(
        String productId,
        String activeTaskId,
        long observationStartedAtMillis,
        long reviewDueAtMillis
    ) {
        return beginObservation(productId, activeTaskId, observationStartedAtMillis, reviewDueAtMillis, -1L);
    }

    Snapshot beginObservation(String productId, String activeTaskId, long observationStartedAtMillis,
                              long reviewDueAtMillis, long expectedVersion) {
        String id = requireText(productId, "product_id_required");
        String taskId = requireText(activeTaskId, "active_task_id_required");
        if (observationStartedAtMillis < 0L) {
            throw new IllegalArgumentException("observation_start_invalid");
        }
        if (reviewDueAtMillis < observationStartedAtMillis) {
            throw new IllegalArgumentException("review_due_before_observation_start");
        }
        return compute(id, (ignored, previous) -> {
            if (previous != null) {
                if (previous.stateVersion() != expectedVersion) throw new IllegalStateException("lifecycle_observation_version_mismatch");
                if (previous.state() != State.MONITORING) throw new IllegalStateException("lifecycle_reset_forbidden");
            }
            return new Snapshot(
            id,
            State.OBSERVING,
            taskId,
            observationStartedAtMillis,
            reviewDueAtMillis,
            previous == null ? null : previous.lastReviewDecision(),
            previous == null ? null : previous.lastReviewHash(),
            previous == null ? 1L : previous.stateVersion() + 1L
        ); });
    }

    Snapshot markReviewReady(String productId, long nowMillis, long expectedVersion) {
        if (nowMillis < 0L) throw new IllegalArgumentException("review_now_invalid");
        return transition(
            productId,
            expectedVersion,
            State.OBSERVING,
            State.REVIEW_READY,
            current -> {
                if (nowMillis < current.reviewDueAtMillis()) {
                    throw new IllegalStateException("review_window_not_due");
                }
                return copy(current, State.REVIEW_READY, current.lastReviewDecision(), current.lastReviewHash());
            }
        );
    }

    Snapshot beginReview(String productId, long expectedVersion) {
        return transition(
            productId,
            expectedVersion,
            State.REVIEW_READY,
            State.REVIEWING,
            current -> copy(current, State.REVIEWING, current.lastReviewDecision(), current.lastReviewHash())
        );
    }

    Snapshot settle(String productId, String reviewHash, long expectedVersion) {
        String hash = requireText(reviewHash, "review_hash_required");
        return transition(
            productId,
            expectedVersion,
            State.REVIEWING,
            State.MONITORING,
            current -> new Snapshot(
                current.productId(),
                State.MONITORING,
                null,
                0L,
                0L,
                "SETTLED",
                hash,
                current.stateVersion() + 1L
            )
        );
    }

    Snapshot adjustmentRequired(String productId, String reviewHash, long expectedVersion) {
        String hash = requireText(reviewHash, "review_hash_required");
        return transition(
            productId,
            expectedVersion,
            State.REVIEWING,
            State.ADJUSTMENT_REQUIRED,
            current -> new Snapshot(
                current.productId(),
                State.ADJUSTMENT_REQUIRED,
                current.activeTaskId(),
                current.observationStartedAtMillis(),
                current.reviewDueAtMillis(),
                "ADJUSTMENT_REQUIRED",
                hash,
                current.stateVersion() + 1L
            )
        );
    }

    /** Commit review, lifecycle outcome and replayable dispatch intent in one durable write.
     * Caller must use the existing MUTATION authority adapter; this grants no authority.
     * No queue acknowledgement is persisted: QueueAuthority is still an in-memory adapter.
     */
    Snapshot commitReview(ReviewContractAuthority.Contract contract,
                          SystemReviewAuthority.Observation observation,
                          LocalSubgraphRevisionAuthority.GraphSnapshot graph, long expectedVersion) {
        if (storage == null) throw new IllegalStateException("review_commit_requires_durable_storage");
        var review = SystemReviewAuthority.evaluate(contract, observation);
        var scope = review.invokeAgent1() ? LocalSubgraphRevisionAuthority.plan(contract, review, graph) : null;
        String requestHash = Hashing.canonicalHash(Map.of("contractHash", contract.contractHash(),
            "reviewHash", review.reviewHash(), "scopeHash", scope == null ? "" : scope.revisionHash(),
            "expectedVersion", expectedVersion));
        return compute(contract.productId(), (ignored, current) -> {
            if (current == null) throw new IllegalStateException("review_lifecycle_missing");
            if (current.reviewCommitJson() != null) {
                var stored = Json.object(Json.parse(current.reviewCommitJson()));
                if (requestHash.equals(stored.get("requestHash"))) return current;
            }
            if (current.stateVersion() != expectedVersion) throw new IllegalStateException("review_commit_stale_version");
            if (current.state() != State.OBSERVING && current.state() != State.REVIEW_READY
                && current.state() != State.REVIEWING) throw new IllegalStateException("review_commit_wrong_state");
            if (!contract.taskId().equals(current.activeTaskId())
                || contract.frozenAtMillis() != current.observationStartedAtMillis()
                || contract.reviewDueAtMillis() != current.reviewDueAtMillis()) {
                throw new IllegalStateException("review_contract_lifecycle_mismatch");
            }
            if (review.decision() == SystemReviewAuthority.Decision.WAITING_TIME
                || review.decision() == SystemReviewAuthority.Decision.WAITING_EVIDENCE) return current;
            var receipt = new LinkedHashMap<String, Object>();
            receipt.put("schema", "v26.review_commit.v1"); receipt.put("requestHash", requestHash);
            receipt.put("productId", contract.productId()); receipt.put("taskId", contract.taskId());
            receipt.put("contractHash", contract.contractHash()); receipt.put("reviewHash", review.reviewHash());
            receipt.put("decision", review.decision().name()); receipt.put("reason", review.reason());
            receipt.put("observedAtMillis", observation.observedAtMillis());
            receipt.put("metrics", observation.metrics()); receipt.put("evidence", observation.evidence());
            receipt.put("breachedMetrics", review.breachedMetrics());
            receipt.put("beforeVersion", current.stateVersion()); receipt.put("afterVersion", current.stateVersion() + 1);
            receipt.put("dispatchStatus", scope == null ? "NOT_REQUIRED" : "PENDING");
            receipt.put("revisionHash", scope == null ? null : scope.revisionHash());
            receipt.put("revisionHeaders", scope == null ? Map.of() : LocalSubgraphRevisionAuthority.authorityHeaders(scope));
            receipt.put("productionActivationVerified", false);
            receipt.put("receiptHash", Hashing.canonicalHash(receipt));
            boolean settled = scope == null;
            return new Snapshot(current.productId(), settled ? State.MONITORING : State.ADJUSTMENT_REQUIRED,
                settled ? null : current.activeTaskId(), settled ? 0 : current.observationStartedAtMillis(),
                settled ? 0 : current.reviewDueAtMillis(), review.decision().name(), review.reviewHash(),
                current.stateVersion() + 1, Json.canonical(receipt));
        });
    }

    /** Replay the durable intent into the existing shadow queue; never mark it delivered.
     * A production consumer must durably accept the task before acknowledging the intent.
     */
    QueueAuthority.EnqueueResult replayReviewDispatch(String productId, long expectedVersion,
        RootBoundAuthorityAdapter invocation, RootBoundAuthorityAdapter.Token token, QueueAuthority queue) {
        if (!"INVOCATION".equals(invocation.domain())) throw new IllegalStateException("review_dispatch_wrong_domain");
        QueueAuthority.EnqueueResult[] result = new QueueAuthority.EnqueueResult[1];
        invocation.execute(token, "V26_REPLAY_REVIEW_DISPATCH", () -> {
            compute(productId, (ignored, current) -> {
                if (current == null || current.stateVersion() != expectedVersion
                    || current.state() != State.ADJUSTMENT_REQUIRED || current.reviewCommitJson() == null) {
                    throw new IllegalStateException("review_dispatch_state_mismatch");
                }
                var receipt = Json.object(Json.parse(current.reviewCommitJson()));
                String revision = (String) receipt.get("revisionHash");
                if (!"PENDING".equals(receipt.get("dispatchStatus")) || revision == null) {
                    throw new IllegalStateException("review_dispatch_not_pending");
                }
                String item = "revision:" + revision;
                queue.registerItem(item, "review:" + current.lastReviewHash(), 1);
                result[0] = queue.enqueue(item, QueueAuthority.Stage.AGENT1, revision, 1);
                return current;
            });
            return result[0];
        });
        return result[0];
    }

    Snapshot snapshot(String productId) {
        String id = requireText(productId, "product_id_required");
        if (storage != null) {
            synchronized (LOCKS.computeIfAbsent(storage.toString(), ignored -> new Object())) {
                try { load(); } catch (java.io.IOException exc) { throw new IllegalStateException("lifecycle_storage_failure", exc); }
            }
        }
        return products.get(id);
    }

    Snapshot beginNextObservation(String productId, String taskId, long start, long due, long expectedVersion) {
        if (start < 0 || due < start) throw new IllegalArgumentException("observation_window_invalid");
        String task = requireText(taskId, "active_task_id_required");
        return transition(productId, expectedVersion, State.ADJUSTMENT_REQUIRED, State.OBSERVING,
            current -> {
                if (current.reviewCommitJson() != null
                    && "PENDING".equals(Json.object(Json.parse(current.reviewCommitJson())).get("dispatchStatus"))) {
                    throw new IllegalStateException("revision_dispatch_durable_acceptance_required");
                }
                if (task.equals(current.activeTaskId())) throw new IllegalStateException("revision_requires_new_task_identity");
                return new Snapshot(current.productId(), State.OBSERVING, task, start, due,
                    current.lastReviewDecision(), current.lastReviewHash(), current.stateVersion() + 1);
            });
    }

    int productCount() {
        return products.size();
    }

    private Snapshot transition(
        String productId,
        long expectedVersion,
        State requiredState,
        State targetState,
        java.util.function.Function<Snapshot, Snapshot> mutation
    ) {
        String id = requireText(productId, "product_id_required");
        return compute(id, (ignored, current) -> {
            if (current == null) throw new IllegalStateException("product_lifecycle_missing:" + id);
            if (current.stateVersion() != expectedVersion) {
                throw new IllegalStateException(
                    "product_lifecycle_version_mismatch:" + expectedVersion + ":" + current.stateVersion()
                );
            }
            if (current.state() != requiredState) {
                throw new IllegalStateException(
                    "product_lifecycle_state_mismatch:" + requiredState + ":" + current.state()
                );
            }
            Snapshot next = mutation.apply(current);
            if (next.state() != targetState) {
                throw new IllegalStateException("product_lifecycle_target_state_mismatch");
            }
            return next;
        });
    }

    private static Snapshot copy(
        Snapshot current,
        State state,
        String lastReviewDecision,
        String lastReviewHash
    ) {
        return new Snapshot(
            current.productId(),
            state,
            current.activeTaskId(),
            current.observationStartedAtMillis(),
            current.reviewDueAtMillis(),
            lastReviewDecision,
            lastReviewHash,
            current.stateVersion() + 1L
        );
    }

    private static String requireText(String value, String error) {
        if (value == null || value.isBlank()) throw new IllegalArgumentException(error);
        return value.trim();
    }
}