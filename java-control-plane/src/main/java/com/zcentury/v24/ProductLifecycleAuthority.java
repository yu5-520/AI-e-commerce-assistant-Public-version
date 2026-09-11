package com.zcentury.v24;

import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;

/**
 * V26.3 product-level lifecycle authority that exists before any Agent task is created.
 *
 * This authority deliberately does not reuse TaskStateAuthority: product lifecycle is
 * persistent business admission state, while TaskStateAuthority owns post-task state.
 */
final class ProductLifecycleAuthority {
    enum State { MONITORING, OBSERVING }

    record Snapshot(
        String productId,
        State state,
        String activeTaskId,
        long observationStartedAtMillis,
        long reviewDueAtMillis,
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
            previous == null ? 1L : previous.stateVersion() + 1L
        ));
    }

    Snapshot snapshot(String productId) {
        return products.get(requireText(productId, "product_id_required"));
    }

    int productCount() {
        return products.size();
    }

    private static String requireText(String value, String error) {
        if (value == null || value.isBlank()) throw new IllegalArgumentException(error);
        return value.trim();
    }
}