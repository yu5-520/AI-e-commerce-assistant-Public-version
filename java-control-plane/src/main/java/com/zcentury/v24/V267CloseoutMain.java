package com.zcentury.v24;

import java.nio.file.Files;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Executable recovery, lifecycle, review and identity regressions on real classes. */
public final class V267CloseoutMain {
    private static void require(boolean value, String reason) { if (!value) throw new IllegalStateException(reason); }
    private static void rejected(Runnable operation) {
        try { operation.run(); } catch (IllegalArgumentException | IllegalStateException expected) { return; }
        throw new IllegalStateException("invalid_operation_accepted");
    }
    public static void main(String[] args) throws Exception {
        var root = Files.createTempDirectory("v267-closeout-");
        var file = root.resolve("lifecycle.json");
        var first = new ProductLifecycleAuthority(file);
        first.monitor("S1/P1");
        var observed = first.beginObservation("S1/P1", "T1", 1000, 10000, 1);
        rejected(() -> first.monitor("S1/P1"));
        rejected(() -> first.beginObservation("S1/P1", "T2", 1000, 10000));
        var recovered = new ProductLifecycleAuthority(file);
        require(recovered.snapshot("S1/P1").equals(observed), "durable_state_not_recovered");
        rejected(() -> recovered.markReviewReady("S1/P1", 10000, 1));
        var ready = recovered.markReviewReady("S1/P1", 10000, observed.stateVersion());
        rejected(() -> first.markReviewReady("S1/P1", 10000, observed.stateVersion()));
        var reviewing = recovered.beginReview("S1/P1", ready.stateVersion());
        recovered.adjustmentRequired("S1/P1", "review-1", reviewing.stateVersion());
        rejected(() -> first.monitor("S1/P1"));

        var breakout = VolatilityEnvelope.evaluate(new VolatilityEnvelope.Input(100, 20, .1, 2, 2, 100, 10, 3, 2));
        require(!PreAgentAdmissionGate.decide(observed, breakout).admitted(), "observation_lock_bypassed");
        Map<String,Object> plan = new LinkedHashMap<>();
        plan.put("plan.review_window", "9s");
        plan.put("plan.lower_guard", Map.of("roas", 2.0));
        plan.put("plan.upper_guard", Map.of("roas", 5.0));
        plan.put("plan.minimum_evidence", Map.of("samples", 10.0));
        plan.put("plan.acceptance_criteria", List.of(Map.of("metric", "roas", "constraint", "lower_guard")));
        var contract = ReviewContractAuthority.freeze("S1/P1", "T1", "graph", plan, Map.of(), 1000, 10000);
        require(contract.deterministicSpec(), "structured_contract_rejected");
        var a = SystemReviewAuthority.evaluate(contract, new SystemReviewAuthority.Observation(10000, Map.of("roas", 3.0), Map.of("samples", 12.0)));
        var b = SystemReviewAuthority.evaluate(contract, new SystemReviewAuthority.Observation(11000, Map.of("roas", 3.0), Map.of("samples", 12.0)));
        require(a.decision() == SystemReviewAuthority.Decision.SETTLED, "valid_review_not_settled");
        require(a.reviewHash().equals(b.reviewHash()), "retry_time_changes_business_identity");
        plan.put("plan.risk_boundaries", List.of("人工确认库存安全"));
        var unsupported = ReviewContractAuthority.freeze("S1/P1", "T1", "graph", plan, Map.of(), 1000, 10000);
        require(!unsupported.deterministicSpec(), "unexecuted_risk_accepted");
        plan.remove("plan.risk_boundaries"); plan.put("plan.upper_guard", Map.of("roas", 1.0));
        require(!ReviewContractAuthority.freeze("S1/P1", "T1", "graph", plan, Map.of(), 1000, 10000).deterministicSpec(), "contradictory_guards_accepted");
        System.out.println(Json.canonical(Map.of("schema", "v26.7.closeout.proof.v1", "verified", true,
            "durableRecovery", true, "staleWriterRejected", true, "observationLock", true,
            "uncompiledRiskBlocksSettlement", true, "retryIdentityStable", true,
            "productionAuthorityTransferred", false)));
    }
}
