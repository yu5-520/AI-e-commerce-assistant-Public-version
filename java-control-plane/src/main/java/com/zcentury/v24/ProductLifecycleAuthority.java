package com.zcentury.v24;

import java.util.Map;
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
        long stateVersion
    ) {}

    private final Map<String, Snapshot> products = new ConcurrentHashMap<>();

    Snapshot monitor(String productId) {
        String id = requireText(productId, "product_id_required");
        return products.compute(id, (ignored, previous) -> new Snapshot(
            id,
            State.MONITORING,
            null,
            0L,
            0L,
            previous == null ? null : previous.lastReviewDecision(),
            previous == null ? null : previous.lastReviewHash(),
            previous == null ? 1L : previous.stateVersion() + 1L
        ));
    }

    Snapshot beginObservation(
        String productId,
        String activeTaskId,
        long observationStartedAtMillis,
        long reviewDueAtMillis
    ) {
        String id = requireText(productId, "product_id_required");
        String taskId = requireText(activeTaskId, "active_task_id_required");
        if (observationStartedAtMillis < 0L) {
            throw new IllegalArgumentException("observation_start_invalid");
        }
        if (reviewDueAtMillis < observationStartedAtMillis) {
            throw new IllegalArgumentException("review_due_before_observation_start");
        }
        return products.compute(id, (ignored, previous) -> new Snapshot(
            id,
            State.OBSERVING,
            taskId,
            observationStartedAtMillis,
            reviewDueAtMillis,
            previous == null ? null : previous.lastReviewDecision(),
            previous == null ? null : previous.lastReviewHash(),
            previous == null ? 1L : previous.stateVersion() + 1L
        ));
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

    Snapshot snapshot(String productId) {
        return products.get(requireText(productId, "product_id_required"));
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
        return products.compute(id, (ignored, current) -> {
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