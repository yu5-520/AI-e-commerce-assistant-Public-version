package com.zcentury.v24;

/**
 * V26.3 deterministic statistical change detector used before Agent1.
 *
 * It detects whether a metric moved outside its own historical volatility envelope.
 * It does not decide whether the move is good, bad, risky or opportunistic.
 */
final class VolatilityEnvelope {
    static final double NEAR_ZERO = 1.0e-9;

    enum Signal {
        NORMAL,
        LOWER_BREAK,
        UPPER_BREAK,
        INSUFFICIENT_SAMPLE,
        INSUFFICIENT_BASELINE,
        PERSISTENCE_NOT_MET
    }

    record Input(
        double baseline,
        double currentValue,
        double historicalVolatilityRate,
        double lowerMultiplier,
        double upperMultiplier,
        long sampleSize,
        long minimumSample,
        int persistenceObserved,
        int persistenceRequired
    ) {}

    record Evaluation(
        Signal signal,
        Double relativeDelta,
        Double lowerBreakDelta,
        Double upperBreakDelta,
        long sampleSize,
        long minimumSample,
        int persistenceObserved,
        int persistenceRequired,
        String reason
    ) {}

    private VolatilityEnvelope() {}

    static Evaluation evaluate(Input input) {
        if (input == null) throw new IllegalArgumentException("volatility_input_required");
        validate(input);

        if (input.sampleSize() < input.minimumSample()) {
            return result(input, Signal.INSUFFICIENT_SAMPLE, null, null, null, "minimum_sample_not_met");
        }
        if (Math.abs(input.baseline()) <= NEAR_ZERO) {
            return result(input, Signal.INSUFFICIENT_BASELINE, null, null, null, "baseline_near_zero");
        }

        double relativeDelta = (input.currentValue() - input.baseline()) / input.baseline();
        double lowerBreakDelta = -(input.historicalVolatilityRate() * input.lowerMultiplier());
        double upperBreakDelta = input.historicalVolatilityRate() * input.upperMultiplier();

        Signal candidate = Signal.NORMAL;
        if (relativeDelta < lowerBreakDelta) {
            candidate = Signal.LOWER_BREAK;
        } else if (relativeDelta > upperBreakDelta) {
            candidate = Signal.UPPER_BREAK;
        }

        if (candidate != Signal.NORMAL && input.persistenceObserved() < input.persistenceRequired()) {
            return result(
                input,
                Signal.PERSISTENCE_NOT_MET,
                relativeDelta,
                lowerBreakDelta,
                upperBreakDelta,
                candidate.name().toLowerCase() + "_persistence_not_met"
            );
        }

        return result(
            input,
            candidate,
            relativeDelta,
            lowerBreakDelta,
            upperBreakDelta,
            candidate == Signal.NORMAL ? "within_volatility_envelope" : candidate.name().toLowerCase()
        );
    }

    private static Evaluation result(
        Input input,
        Signal signal,
        Double relativeDelta,
        Double lowerBreakDelta,
        Double upperBreakDelta,
        String reason
    ) {
        return new Evaluation(
            signal,
            relativeDelta,
            lowerBreakDelta,
            upperBreakDelta,
            input.sampleSize(),
            input.minimumSample(),
            input.persistenceObserved(),
            input.persistenceRequired(),
            reason
        );
    }

    private static void validate(Input input) {
        if (!Double.isFinite(input.baseline()) || !Double.isFinite(input.currentValue())) {
            throw new IllegalArgumentException("metric_value_not_finite");
        }
        if (!Double.isFinite(input.historicalVolatilityRate()) || input.historicalVolatilityRate() < 0.0) {
            throw new IllegalArgumentException("historical_volatility_invalid");
        }
        if (!Double.isFinite(input.lowerMultiplier()) || input.lowerMultiplier() < 0.0) {
            throw new IllegalArgumentException("lower_multiplier_invalid");
        }
        if (!Double.isFinite(input.upperMultiplier()) || input.upperMultiplier() < 0.0) {
            throw new IllegalArgumentException("upper_multiplier_invalid");
        }
        if (input.sampleSize() < 0L || input.minimumSample() < 0L) {
            throw new IllegalArgumentException("sample_size_invalid");
        }
        if (input.persistenceObserved() < 0 || input.persistenceRequired() < 1) {
            throw new IllegalArgumentException("persistence_invalid");
        }
    }
}