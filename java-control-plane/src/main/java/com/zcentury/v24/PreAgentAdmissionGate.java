package com.zcentury.v24;

import java.util.Objects;

/**
 * V26.3 deterministic admission gate in front of QueueAuthority/Agent1.
 *
 * Java decides whether the current product observation deserves Agent compute.
 * Business meaning remains outside this gate and is interpreted by Agent1 only after ADMIT.
 */
final class PreAgentAdmissionGate {
    enum Decision { ADMIT, SKIP }

    record Result(
        Decision decision,
        String reason,
        ProductLifecycleAuthority.State lifecycleState,
        VolatilityEnvelope.Signal signal
    ) {
        boolean admitted() {
            return decision == Decision.ADMIT;
        }
    }

    private PreAgentAdmissionGate() {}

    static Result decide(
        ProductLifecycleAuthority.Snapshot lifecycle,
        VolatilityEnvelope.Evaluation evaluation
    ) {
        Objects.requireNonNull(lifecycle, "product_lifecycle_snapshot_required");
        Objects.requireNonNull(evaluation, "volatility_evaluation_required");

        if (evaluation.signal() == VolatilityEnvelope.Signal.LOWER_BREAK
            || evaluation.signal() == VolatilityEnvelope.Signal.UPPER_BREAK) {
            return new Result(
                Decision.ADMIT,
                evaluation.signal().name(),
                lifecycle.state(),
                evaluation.signal()
            );
        }

        String reason;
        if (evaluation.signal() == VolatilityEnvelope.Signal.NORMAL) {
            reason = lifecycle.state() == ProductLifecycleAuthority.State.OBSERVING
                ? "OBSERVATION_LOCK_NORMAL"
                : "MONITORING_NO_BREAK";
        } else {
            reason = evaluation.signal().name();
        }
        return new Result(Decision.SKIP, reason, lifecycle.state(), evaluation.signal());
    }
}