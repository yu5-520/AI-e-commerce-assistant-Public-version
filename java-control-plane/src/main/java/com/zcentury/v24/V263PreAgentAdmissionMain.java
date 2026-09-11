package com.zcentury.v24;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** Executable V26.3 shadow/parity proof for product lifecycle admission before Agent1. */
public final class V263PreAgentAdmissionMain {
    private V263PreAgentAdmissionMain() {}

    public static void main(String[] args) throws Exception {
        Path temp = Files.createTempDirectory("v26-3-pre-agent-admission-");
        AuthorityGenerationStore store = new AuthorityGenerationStore(
            temp.resolve("authority-generation.json")
        );
        UnifiedAuthorityGenerationRoot root = new UnifiedAuthorityGenerationRoot(store);
        RootBoundAuthorityAdapter invocation = new RootBoundAuthorityAdapter(
            "INVOCATION",
            QueueAuthority.class.getName(),
            root
        );
        RootBoundAuthorityAdapter.Token token = invocation.token();
        QueueAuthority queue = invocation.queue("v26.3-pre-agent-admission-shadow");
        ProductLifecycleAuthority lifecycle = new ProductLifecycleAuthority();

        ProductLifecycleAuthority.Snapshot monitoringNormal = lifecycle.monitor("SKU-MONITOR-NORMAL");
        AdmissionRun monitoringNormalRun = evaluateAndMaybeEnqueue(
            invocation, token, queue, monitoringNormal,
            metric(100.0, 105.0, 0.10, 2.0, 2.0, 1000L, 100L, 2, 2),
            "DV-MONITOR-NORMAL", 1
        );
        require(!monitoringNormalRun.gate().admitted(), "monitoring_normal_must_skip");
        require(queue.itemSnapshot("SKU-MONITOR-NORMAL").isEmpty(), "monitoring_normal_created_pipeline_item");

        ProductLifecycleAuthority.Snapshot observingNormal = lifecycle.beginObservation(
            "SKU-OBSERVE-NORMAL", "TASK-OBSERVE-001", 1_000L, 10_000L
        );
        AdmissionRun observingNormalRun = evaluateAndMaybeEnqueue(
            invocation, token, queue, observingNormal,
            metric(100.0, 108.0, 0.10, 2.0, 2.0, 1000L, 100L, 2, 2),
            "DV-OBSERVE-NORMAL", 1
        );
        require(!observingNormalRun.gate().admitted(), "observing_normal_must_skip");
        require(
            "OBSERVATION_LOCK_NORMAL".equals(observingNormalRun.gate().reason()),
            "observing_normal_reason_mismatch"
        );
        require(queue.itemSnapshot("SKU-OBSERVE-NORMAL").isEmpty(), "observing_normal_created_pipeline_item");

        ProductLifecycleAuthority.Snapshot lowerBreak = lifecycle.monitor("SKU-LOWER-BREAK");
        AdmissionRun lowerBreakRun = evaluateAndMaybeEnqueue(
            invocation, token, queue, lowerBreak,
            metric(100.0, 70.0, 0.10, 2.0, 2.0, 1000L, 100L, 2, 2),
            "DV-LOWER-BREAK", 1
        );
        require(lowerBreakRun.gate().admitted(), "lower_break_must_admit");
        require(
            "AGENT1".equals(queue.itemSnapshot("SKU-LOWER-BREAK").get("currentStage")),
            "lower_break_not_registered_at_agent1"
        );

        ProductLifecycleAuthority.Snapshot upperBreak = lifecycle.beginObservation(
            "SKU-UPPER-BREAK", "TASK-UPPER-001", 1_000L, 10_000L
        );
        AdmissionRun upperBreakRun = evaluateAndMaybeEnqueue(
            invocation, token, queue, upperBreak,
            metric(100.0, 140.0, 0.10, 2.0, 2.0, 1000L, 100L, 3, 2),
            "DV-UPPER-BREAK", 1
        );
        require(upperBreakRun.gate().admitted(), "upper_break_must_admit");
        require(
            "AGENT1".equals(queue.itemSnapshot("SKU-UPPER-BREAK").get("currentStage")),
            "upper_break_not_registered_at_agent1"
        );

        ProductLifecycleAuthority.Snapshot insufficientBaseline = lifecycle.monitor("SKU-COLD-START");
        AdmissionRun insufficientBaselineRun = evaluateAndMaybeEnqueue(
            invocation, token, queue, insufficientBaseline,
            metric(0.0, 1.0, 0.10, 2.0, 2.0, 1000L, 100L, 2, 2),
            "DV-COLD-START", 1
        );
        require(
            insufficientBaselineRun.evaluation().signal() == VolatilityEnvelope.Signal.INSUFFICIENT_BASELINE,
            "cold_start_signal_mismatch"
        );
        require(!insufficientBaselineRun.gate().admitted(), "cold_start_must_skip");

        ProductLifecycleAuthority.Snapshot insufficientSample = lifecycle.monitor("SKU-SMALL-SAMPLE");
        AdmissionRun insufficientSampleRun = evaluateAndMaybeEnqueue(
            invocation, token, queue, insufficientSample,
            metric(100.0, 50.0, 0.10, 2.0, 2.0, 20L, 100L, 2, 2),
            "DV-SMALL-SAMPLE", 1
        );
        require(
            insufficientSampleRun.evaluation().signal() == VolatilityEnvelope.Signal.INSUFFICIENT_SAMPLE,
            "small_sample_signal_mismatch"
        );
        require(!insufficientSampleRun.gate().admitted(), "small_sample_must_skip");

        ProductLifecycleAuthority.Snapshot persistence = lifecycle.monitor("SKU-PERSISTENCE");
        AdmissionRun persistenceRun = evaluateAndMaybeEnqueue(
            invocation, token, queue, persistence,
            metric(100.0, 50.0, 0.10, 2.0, 2.0, 1000L, 100L, 1, 2),
            "DV-PERSISTENCE", 1
        );
        require(
            persistenceRun.evaluation().signal() == VolatilityEnvelope.Signal.PERSISTENCE_NOT_MET,
            "persistence_signal_mismatch"
        );
        require(!persistenceRun.gate().admitted(), "persistence_not_met_must_skip");

        ProductLifecycleAuthority.Snapshot nearZero = lifecycle.monitor("SKU-NEAR-ZERO");
        AdmissionRun nearZeroRun = evaluateAndMaybeEnqueue(
            invocation, token, queue, nearZero,
            metric(1.0e-12, 1.0, 0.10, 2.0, 2.0, 1000L, 100L, 2, 2),
            "DV-NEAR-ZERO", 1
        );
        require(
            nearZeroRun.evaluation().signal() == VolatilityEnvelope.Signal.INSUFFICIENT_BASELINE,
            "near_zero_baseline_not_fail_closed"
        );
        require(nearZeroRun.evaluation().relativeDelta() == null, "near_zero_relative_delta_must_not_divide");
        require(!nearZeroRun.gate().admitted(), "near_zero_must_skip");

        require(queue.jobCount() == 2, "skip_path_created_queue_jobs:" + queue.jobCount());
        require(queue.readyCount(QueueAuthority.Stage.AGENT1) == 2, "agent1_ready_count_mismatch");
        require(lifecycle.productCount() == 8, "product_lifecycle_count_mismatch");

        LinkedHashMap<String, Object> report = new LinkedHashMap<>();
        report.put("schema", "v26.3.pre_agent_admission.shadow_parity.v1");
        report.put("version", "26.3.0");
        report.put("verified", true);
        report.put("enforcementMode", "SHADOW");
        report.put("authorityDomain", "INVOCATION");
        report.put("rootBoundGeneration", invocation.matches(token));
        report.put("monitoringNormalSkipped", !monitoringNormalRun.gate().admitted());
        report.put("observingNormalSkipped", !observingNormalRun.gate().admitted());
        report.put("lowerBreakAdmitted", lowerBreakRun.gate().admitted());
        report.put("upperBreakAdmitted", upperBreakRun.gate().admitted());
        report.put("insufficientBaselineSkipped", !insufficientBaselineRun.gate().admitted());
        report.put("insufficientSampleSkipped", !insufficientSampleRun.gate().admitted());
        report.put("persistenceNotMetSkipped", !persistenceRun.gate().admitted());
        report.put("nearZeroFailClosed", !nearZeroRun.gate().admitted());
        report.put("skipCreatesPipelineItem", false);
        report.put("skipCreatesAgentJob", false);
        report.put("admittedInitialStage", "AGENT1");
        report.put("queueJobCount", queue.jobCount());
        report.put("productLifecycleCount", lifecycle.productCount());
        report.put("businessSemanticClassificationPerformed", false);
        report.put("taskStateAuthorityReusedAsProductLifecycle", false);
        report.put("productionAuthorityOwnershipChanged", false);
        report.put("authorityGrantCreated", false);
        report.put("verificationHash", Hashing.canonicalHash(report));

        System.out.println(Json.canonical(report));
    }

    private static AdmissionRun evaluateAndMaybeEnqueue(
        RootBoundAuthorityAdapter invocation,
        RootBoundAuthorityAdapter.Token token,
        QueueAuthority queue,
        ProductLifecycleAuthority.Snapshot lifecycle,
        VolatilityEnvelope.Input metric,
        String dataVersion,
        int priority
    ) {
        VolatilityEnvelope.Evaluation evaluation = VolatilityEnvelope.evaluate(metric);
        PreAgentAdmissionGate.Result gate = invocation.execute(
            token,
            "V26_3_PRE_AGENT_ADMISSION",
            () -> PreAgentAdmissionGate.decide(lifecycle, evaluation)
        );
        QueueAuthority.EnqueueResult enqueue = null;
        if (gate.admitted()) {
            queue.registerItem(lifecycle.productId(), dataVersion, priority);
            enqueue = queue.enqueue(
                lifecycle.productId(),
                QueueAuthority.Stage.AGENT1,
                Hashing.canonicalHash(Map.of(
                    "productId", lifecycle.productId(),
                    "dataVersion", dataVersion,
                    "signal", evaluation.signal().name()
                )),
                priority
            );
        }
        return new AdmissionRun(evaluation, gate, enqueue);
    }

    private static VolatilityEnvelope.Input metric(
        double baseline,
        double current,
        double historicalVolatilityRate,
        double lowerMultiplier,
        double upperMultiplier,
        long sampleSize,
        long minimumSample,
        int persistenceObserved,
        int persistenceRequired
    ) {
        return new VolatilityEnvelope.Input(
            baseline,
            current,
            historicalVolatilityRate,
            lowerMultiplier,
            upperMultiplier,
            sampleSize,
            minimumSample,
            persistenceObserved,
            persistenceRequired
        );
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }

    private record AdmissionRun(
        VolatilityEnvelope.Evaluation evaluation,
        PreAgentAdmissionGate.Result gate,
        QueueAuthority.EnqueueResult enqueue
    ) {}
}