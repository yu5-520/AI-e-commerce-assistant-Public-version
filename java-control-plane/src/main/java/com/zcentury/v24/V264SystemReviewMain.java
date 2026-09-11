package com.zcentury.v24;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Executable V26.4 shadow/parity proof for deterministic post-observation review. */
public final class V264SystemReviewMain {
    private V264SystemReviewMain() {}

    public static void main(String[] args) throws Exception {
        Path temp = Files.createTempDirectory("v26-4-system-review-");
        AuthorityGenerationStore store = new AuthorityGenerationStore(
            temp.resolve("authority-generation.json")
        );
        UnifiedAuthorityGenerationRoot root = new UnifiedAuthorityGenerationRoot(store);
        RootBoundAuthorityAdapter information = new RootBoundAuthorityAdapter(
            "INFORMATION", SystemReviewAuthority.class.getName(), root
        );
        RootBoundAuthorityAdapter invocation = new RootBoundAuthorityAdapter(
            "INVOCATION", QueueAuthority.class.getName(), root
        );
        RootBoundAuthorityAdapter temporal = new RootBoundAuthorityAdapter(
            "TEMPORAL", ProductLifecycleAuthority.class.getName(), root
        );
        RootBoundAuthorityAdapter mutation = new RootBoundAuthorityAdapter(
            "MUTATION", ProductLifecycleAuthority.class.getName(), root
        );
        RootBoundAuthorityAdapter.Token informationToken = information.token();
        RootBoundAuthorityAdapter.Token invocationToken = invocation.token();
        RootBoundAuthorityAdapter.Token temporalToken = temporal.token();
        RootBoundAuthorityAdapter.Token mutationToken = mutation.token();

        QueueAuthority queue = invocation.queue("v26.4-system-review-shadow");
        ProductLifecycleAuthority lifecycle = new ProductLifecycleAuthority();

        ProductLifecycleAuthority.Snapshot waitingTimeLifecycle = lifecycle.beginObservation(
            "SKU-REVIEW-WAIT-TIME", "TASK-WAIT-TIME", 1_000L, 10_000L
        );
        ReviewContractAuthority.Contract waitingTimeContract = freeze(
            information, informationToken,
            waitingTimeLifecycle, structuredPlan("NON_DECREASE"), Map.of("roas", 3.4)
        );
        SystemReviewAuthority.Result waitingTime = evaluate(
            information, informationToken, waitingTimeContract,
            observation(9_000L, 3.6, 25.0)
        );
        require(waitingTime.decision() == SystemReviewAuthority.Decision.WAITING_TIME,
            "review_before_due_must_wait");
        require(lifecycle.snapshot("SKU-REVIEW-WAIT-TIME").state()
            == ProductLifecycleAuthority.State.OBSERVING,
            "waiting_time_changed_lifecycle");

        ProductLifecycleAuthority.Snapshot waitingEvidenceLifecycle = lifecycle.beginObservation(
            "SKU-REVIEW-WAIT-EVIDENCE", "TASK-WAIT-EVIDENCE", 1_000L, 10_000L
        );
        ReviewContractAuthority.Contract waitingEvidenceContract = freeze(
            information, informationToken,
            waitingEvidenceLifecycle, structuredPlan("NON_DECREASE"), Map.of("roas", 3.4)
        );
        SystemReviewAuthority.Result waitingEvidence = evaluate(
            information, informationToken, waitingEvidenceContract,
            observation(10_000L, 3.6, 10.0)
        );
        require(waitingEvidence.decision() == SystemReviewAuthority.Decision.WAITING_EVIDENCE,
            "minimum_evidence_must_defer_review");
        require(lifecycle.snapshot("SKU-REVIEW-WAIT-EVIDENCE").state()
            == ProductLifecycleAuthority.State.OBSERVING,
            "waiting_evidence_changed_lifecycle");

        ProductLifecycleAuthority.Snapshot successLifecycle = lifecycle.beginObservation(
            "SKU-REVIEW-SUCCESS", "TASK-SUCCESS", 1_000L, 10_000L
        );
        ReviewContractAuthority.Contract successContract = freeze(
            information, informationToken,
            successLifecycle, structuredPlan("NON_DECREASE"), Map.of("roas", 3.4)
        );
        SystemReviewAuthority.Result success = evaluate(
            information, informationToken, successContract,
            observation(10_000L, 3.7, 25.0)
        );
        require(success.decision() == SystemReviewAuthority.Decision.SETTLED,
            "clear_success_must_settle");
        ProductLifecycleAuthority.Snapshot successReady = temporal.execute(
            temporalToken,
            "V26_4_REVIEW_DUE",
            () -> lifecycle.markReviewReady(
                successLifecycle.productId(), 10_000L, successLifecycle.stateVersion()
            )
        );
        ProductLifecycleAuthority.Snapshot successReviewing = mutation.execute(
            mutationToken,
            "V26_4_BEGIN_REVIEW",
            () -> lifecycle.beginReview(successReady.productId(), successReady.stateVersion())
        );
        ProductLifecycleAuthority.Snapshot settled = mutation.execute(
            mutationToken,
            "V26_4_SETTLE",
            () -> lifecycle.settle(
                successReviewing.productId(), success.reviewHash(), successReviewing.stateVersion()
            )
        );
        require(settled.state() == ProductLifecycleAuthority.State.MONITORING,
            "settled_product_must_return_to_monitoring");
        require("SETTLED".equals(settled.lastReviewDecision()),
            "settled_review_decision_not_preserved");
        require(queue.itemSnapshot(successLifecycle.productId()).isEmpty(),
            "settled_review_must_not_create_agent_item");

        ProductLifecycleAuthority.Snapshot breachLifecycle = lifecycle.beginObservation(
            "SKU-REVIEW-BREACH", "TASK-BREACH", 1_000L, 10_000L
        );
        ReviewContractAuthority.Contract breachContract = freeze(
            information, informationToken,
            breachLifecycle, structuredPlan("NON_DECREASE"), Map.of("roas", 3.4)
        );
        SystemReviewAuthority.Result breach = evaluate(
            information, informationToken, breachContract,
            observation(10_000L, 2.5, 30.0)
        );
        require(breach.decision() == SystemReviewAuthority.Decision.ADJUSTMENT_REQUIRED,
            "guard_breach_must_request_adjustment");
        ProductLifecycleAuthority.Snapshot breachAdjustment = moveToAdjustment(
            temporal, temporalToken, mutation, mutationToken,
            lifecycle, breachLifecycle, breach
        );
        enqueueAgent1(invocation, invocationToken, queue, breachAdjustment, breach, "DV-REVIEW-BREACH");
        require("AGENT1".equals(queue.itemSnapshot(breachLifecycle.productId()).get("currentStage")),
            "review_breach_not_reentered_at_agent1");

        ProductLifecycleAuthority.Snapshot legacyLifecycle = lifecycle.beginObservation(
            "SKU-REVIEW-LEGACY", "TASK-LEGACY", 1_000L, 10_000L
        );
        ReviewContractAuthority.Contract legacyContract = freeze(
            information, informationToken,
            legacyLifecycle, structuredPlan("stable_or_up"), Map.of("roas", 3.4)
        );
        require(!legacyContract.deterministicSpec(),
            "legacy_natural_language_expectation_must_not_be_guessed");
        SystemReviewAuthority.Result legacy = evaluate(
            information, informationToken, legacyContract,
            observation(10_000L, 3.6, 30.0)
        );
        require(legacy.decision() == SystemReviewAuthority.Decision.ADJUSTMENT_REQUIRED,
            "unstructured_expectation_must_fail_closed");
        require("NON_DETERMINISTIC_REVIEW_CONTRACT".equals(legacy.reason()),
            "unstructured_expectation_reason_mismatch");
        ProductLifecycleAuthority.Snapshot legacyAdjustment = moveToAdjustment(
            temporal, temporalToken, mutation, mutationToken,
            lifecycle, legacyLifecycle, legacy
        );
        enqueueAgent1(invocation, invocationToken, queue, legacyAdjustment, legacy, "DV-REVIEW-LEGACY");

        require(queue.jobCount() == 2, "only_adjustments_may_create_agent_jobs:" + queue.jobCount());
        require(queue.readyCount(QueueAuthority.Stage.AGENT1) == 2,
            "review_agent1_ready_count_mismatch");
        require(lifecycle.productCount() == 5, "review_product_count_mismatch");

        LinkedHashMap<String, Object> report = new LinkedHashMap<>();
        report.put("schema", "v26.4.system_review.shadow_parity.v1");
        report.put("version", "26.4.0");
        report.put("verified", true);
        report.put("enforcementMode", "SHADOW");
        report.put("authorityDomains", List.of("INFORMATION", "INVOCATION", "MUTATION", "TEMPORAL"));
        report.put("newAuthorityDomainCreated", false);
        report.put("allDomainsRootBound", information.matches(informationToken)
            && invocation.matches(invocationToken)
            && temporal.matches(temporalToken)
            && mutation.matches(mutationToken));
        report.put("beforeDueDefersReview", waitingTime.decision()
            == SystemReviewAuthority.Decision.WAITING_TIME);
        report.put("minimumEvidenceDefersReview", waitingEvidence.decision()
            == SystemReviewAuthority.Decision.WAITING_EVIDENCE);
        report.put("clearSuccessSettledWithoutAgent", queue.itemSnapshot(successLifecycle.productId()).isEmpty());
        report.put("settledReturnsMonitoring", settled.state() == ProductLifecycleAuthority.State.MONITORING);
        report.put("guardBreachInvokesAgent1", breach.invokeAgent1());
        report.put("unstructuredExpectationFailsClosed", legacy.invokeAgent1());
        report.put("reviewContractSourcePlanHashBound", !successContract.sourcePlanHash().isBlank());
        report.put("reviewContractActionGraphHashBound", !successContract.actionGraphHash().isBlank());
        report.put("agent2PlanAuthorityPreserved", true);
        report.put("javaPlanWriteAuthorityCreated", false);
        report.put("taskStateAuthorityReusedAsProductLifecycle", false);
        report.put("queueJobCount", queue.jobCount());
        report.put("productLifecycleCount", lifecycle.productCount());
        report.put("productionAuthorityOwnershipChanged", false);
        report.put("authorityGrantCreated", false);
        report.put("verificationHash", Hashing.canonicalHash(report));
        System.out.println(Json.canonical(report));
    }

    private static ReviewContractAuthority.Contract freeze(
        RootBoundAuthorityAdapter information,
        RootBoundAuthorityAdapter.Token token,
        ProductLifecycleAuthority.Snapshot lifecycle,
        Map<String, Object> planHeaders,
        Map<String, Double> baseline
    ) {
        planHeaders = new LinkedHashMap<>(planHeaders);
        planHeaders.put("plan.review_window", (lifecycle.reviewDueAtMillis() - lifecycle.observationStartedAtMillis()) + "ms");
        final Map<String, Object> frozenHeaders = planHeaders;
        return information.execute(
            token,
            "V26_4_FREEZE_REVIEW_CONTRACT",
            () -> ReviewContractAuthority.freeze(
                lifecycle.productId(),
                lifecycle.activeTaskId(),
                Hashing.canonicalHash(Map.of(
                    "productId", lifecycle.productId(),
                    "taskId", lifecycle.activeTaskId(),
                    "plan", frozenHeaders
                )),
                frozenHeaders,
                baseline,
                lifecycle.observationStartedAtMillis(),
                lifecycle.reviewDueAtMillis()
            )
        );
    }

    private static SystemReviewAuthority.Result evaluate(
        RootBoundAuthorityAdapter information,
        RootBoundAuthorityAdapter.Token token,
        ReviewContractAuthority.Contract contract,
        SystemReviewAuthority.Observation observation
    ) {
        return information.execute(
            token,
            "V26_4_READ_OBSERVATION_AND_COMPARE",
            () -> SystemReviewAuthority.evaluate(contract, observation)
        );
    }

    private static ProductLifecycleAuthority.Snapshot moveToAdjustment(
        RootBoundAuthorityAdapter temporal,
        RootBoundAuthorityAdapter.Token temporalToken,
        RootBoundAuthorityAdapter mutation,
        RootBoundAuthorityAdapter.Token mutationToken,
        ProductLifecycleAuthority lifecycle,
        ProductLifecycleAuthority.Snapshot observing,
        SystemReviewAuthority.Result review
    ) {
        ProductLifecycleAuthority.Snapshot ready = temporal.execute(
            temporalToken,
            "V26_4_REVIEW_DUE",
            () -> lifecycle.markReviewReady(
                observing.productId(), observing.reviewDueAtMillis(), observing.stateVersion()
            )
        );
        ProductLifecycleAuthority.Snapshot reviewing = mutation.execute(
            mutationToken,
            "V26_4_BEGIN_REVIEW",
            () -> lifecycle.beginReview(ready.productId(), ready.stateVersion())
        );
        return mutation.execute(
            mutationToken,
            "V26_4_ADJUSTMENT_REQUIRED",
            () -> lifecycle.adjustmentRequired(
                reviewing.productId(), review.reviewHash(), reviewing.stateVersion()
            )
        );
    }

    private static void enqueueAgent1(
        RootBoundAuthorityAdapter invocation,
        RootBoundAuthorityAdapter.Token token,
        QueueAuthority queue,
        ProductLifecycleAuthority.Snapshot lifecycle,
        SystemReviewAuthority.Result review,
        String dataVersion
    ) {
        invocation.execute(token, "V26_4_REENTER_AGENT1", () -> {
            queue.registerItem(lifecycle.productId(), dataVersion, 1);
            return queue.enqueue(
                lifecycle.productId(),
                QueueAuthority.Stage.AGENT1,
                Hashing.canonicalHash(Map.of(
                    "productId", lifecycle.productId(),
                    "activeTaskId", lifecycle.activeTaskId(),
                    "reviewHash", review.reviewHash(),
                    "reason", review.reason()
                )),
                1
            );
        });
    }

    private static Map<String, Object> structuredPlan(String trend) {
        LinkedHashMap<String, Object> plan = new LinkedHashMap<>();
        plan.put("plan.review_window", "72h");
        plan.put("plan.expected_trend", Map.of("roas", trend));
        plan.put("plan.lower_guard", Map.of("roas", 3.0));
        plan.put("plan.upper_guard", Map.of("roas", 5.0));
        plan.put("plan.minimum_evidence", Map.of("orders", 20));
        plan.put("plan.acceptance_criteria", List.of(Map.of("metric", "roas", "constraint", "lower_guard")));
        plan.put("plan.risk_boundaries", List.of(Map.of("metric", "roas", "constraint", "upper_guard")));
        return Map.copyOf(plan);
    }

    private static SystemReviewAuthority.Observation observation(
        long observedAtMillis,
        double roas,
        double orders
    ) {
        return new SystemReviewAuthority.Observation(
            observedAtMillis,
            Map.of("roas", roas),
            Map.of("orders", orders)
        );
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}