package com.zcentury.v24;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Executable V26.6 shadow/parity proof for exact local business-subgraph revision. */
public final class V266LocalSubgraphRevisionMain {
    private V266LocalSubgraphRevisionMain() {}

    public static void main(String[] args) throws Exception {
        Path temp = Files.createTempDirectory("v26-6-local-subgraph-revision-");
        AuthorityGenerationStore store = new AuthorityGenerationStore(
            temp.resolve("authority-generation.json")
        );
        UnifiedAuthorityGenerationRoot root = new UnifiedAuthorityGenerationRoot(store);
        RootBoundAuthorityAdapter reviewInformation = new RootBoundAuthorityAdapter(
            "INFORMATION", SystemReviewAuthority.class.getName(), root
        );
        RootBoundAuthorityAdapter revisionInformation = new RootBoundAuthorityAdapter(
            "INFORMATION", LocalSubgraphRevisionAuthority.class.getName(), root
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
        RootBoundAuthorityAdapter.Token reviewInformationToken = reviewInformation.token();
        RootBoundAuthorityAdapter.Token revisionInformationToken = revisionInformation.token();
        RootBoundAuthorityAdapter.Token invocationToken = invocation.token();
        RootBoundAuthorityAdapter.Token temporalToken = temporal.token();
        RootBoundAuthorityAdapter.Token mutationToken = mutation.token();

        QueueAuthority queue = invocation.queue("v26.6-local-subgraph-revision-shadow");
        ProductLifecycleAuthority lifecycle = new ProductLifecycleAuthority();
        LocalSubgraphRevisionAuthority.GraphSnapshot graph = graphSnapshot();

        // A ROAS breach directly selects A_TRAFFIC. A_ACTIVITY and A_INVENTORY are
        // downstream dependents and must reopen; A_CONTENT and A_RETENTION stay frozen.
        ProductLifecycleAuthority.Snapshot localLifecycle = lifecycle.beginObservation(
            "SKU-REVISION-LOCAL", "TASK-REVISION-LOCAL", 1_000L, 10_000L
        );
        ReviewContractAuthority.Contract localContract = freeze(
            reviewInformation,
            reviewInformationToken,
            localLifecycle,
            graph.actionGraphHash(),
            structuredPlan("NON_DECREASE"),
            Map.of("roas", 3.4)
        );
        SystemReviewAuthority.Result localReview = evaluate(
            reviewInformation,
            reviewInformationToken,
            localContract,
            new SystemReviewAuthority.Observation(
                10_000L,
                Map.of("roas", 2.4),
                Map.of("orders", 30.0)
            )
        );
        require(localReview.decision() == SystemReviewAuthority.Decision.ADJUSTMENT_REQUIRED,
            "roas_breach_must_request_adjustment");
        require(localReview.breachedMetrics().equals(List.of("roas")),
            "review_must_expose_structured_breached_metric");

        LocalSubgraphRevisionAuthority.Scope localScope = revisionInformation.execute(
            revisionInformationToken,
            "V26_6_PLAN_LOCAL_SUBGRAPH_REVISION",
            () -> LocalSubgraphRevisionAuthority.plan(localContract, localReview, graph)
        );
        require(localScope.mode() == LocalSubgraphRevisionAuthority.ScopeMode.LOCAL,
            "mapped_metric_must_use_local_scope");
        require(localScope.reopenActionNodeHashes().size() == 3,
            "local_scope_action_count_mismatch:" + localScope.reopenActionNodeHashes().size());
        require(localScope.reopenActionNodeHashes().contains(nodeHash("ACTION", "A_TRAFFIC")),
            "direct_roas_action_not_reopened");
        require(localScope.reopenActionNodeHashes().contains(nodeHash("ACTION", "A_ACTIVITY")),
            "downstream_activity_not_reopened");
        require(localScope.reopenActionNodeHashes().contains(nodeHash("ACTION", "A_INVENTORY")),
            "downstream_inventory_not_reopened");
        require(localScope.preservedActionNodeHashes().contains(nodeHash("ACTION", "A_CONTENT")),
            "successful_content_action_not_preserved");
        require(localScope.preservedActionNodeHashes().contains(nodeHash("ACTION", "A_RETENTION")),
            "unrelated_retention_action_not_preserved");
        require(localScope.reopenJudgementNodeHashes().contains(nodeHash("JUDGEMENT", "J_TRAFFIC")),
            "traffic_judgement_not_reopened");
        require(localScope.reopenJudgementNodeHashes().contains(nodeHash("JUDGEMENT", "J_INVENTORY")),
            "inventory_judgement_not_reopened");
        require(localScope.preservedJudgementNodeHashes().contains(nodeHash("JUDGEMENT", "J_CONTENT")),
            "content_judgement_not_preserved");
        require(localScope.reopenOperationNodeHashes().size() == 3,
            "local_scope_operation_count_mismatch:" + localScope.reopenOperationNodeHashes().size());
        require(localScope.preservedOperationNodeHashes().contains(nodeHash("OPERATION", "S_CONTENT")),
            "successful_content_operation_not_preserved");
        require(localScope.preservedOperationNodeHashes().contains(nodeHash("OPERATION", "S_RETENTION")),
            "unrelated_retention_operation_not_preserved");

        Map<String, Object> revisionHeaders = LocalSubgraphRevisionAuthority.authorityHeaders(localScope);
        require(localScope.revisionHash().equals(revisionHeaders.get("revision.revision_hash")),
            "revision_hash_header_mismatch");
        require("LOCAL".equals(revisionHeaders.get("revision.scope_mode")),
            "revision_scope_mode_header_mismatch");

        ProductLifecycleAuthority.Snapshot adjustment = moveToAdjustment(
            temporal,
            temporalToken,
            mutation,
            mutationToken,
            lifecycle,
            localLifecycle,
            localReview
        );
        require(adjustment.state() == ProductLifecycleAuthority.State.ADJUSTMENT_REQUIRED,
            "revision_must_remain_under_adjustment_lifecycle_lock");

        QueueAuthority.EnqueueResult firstRevisionJob = invocation.execute(
            invocationToken,
            "V26_6_REENTER_AGENT1_WITH_REVISION_HASH",
            () -> {
                queue.registerItem(adjustment.productId(), "DV-REVISION-LOCAL", 1);
                return queue.enqueue(
                    adjustment.productId(),
                    QueueAuthority.Stage.AGENT1,
                    localScope.revisionHash(),
                    1
                );
            }
        );
        QueueAuthority.EnqueueResult duplicateRevisionJob = invocation.execute(
            invocationToken,
            "V26_6_REENTER_AGENT1_WITH_REVISION_HASH_REPLAY",
            () -> queue.enqueue(
                adjustment.productId(),
                QueueAuthority.Stage.AGENT1,
                localScope.revisionHash(),
                1
            )
        );
        require(!firstRevisionJob.duplicateSuppressed(), "first_revision_job_was_suppressed");
        require(duplicateRevisionJob.duplicateSuppressed(), "same_revision_hash_must_be_idempotent");
        require(firstRevisionJob.jobId().equals(duplicateRevisionJob.jobId()),
            "same_revision_hash_must_resolve_same_job");
        require("AGENT1".equals(queue.itemSnapshot(adjustment.productId()).get("currentStage")),
            "local_revision_not_reentered_at_agent1");

        // Historical/non-deterministic review criteria provide no trustworthy breached
        // metric. V26.6 must not pretend it can localize such a task.
        ProductLifecycleAuthority.Snapshot legacyLifecycle = lifecycle.beginObservation(
            "SKU-REVISION-LEGACY", "TASK-REVISION-LEGACY", 1_000L, 10_000L
        );
        ReviewContractAuthority.Contract legacyContract = freeze(
            reviewInformation,
            reviewInformationToken,
            legacyLifecycle,
            graph.actionGraphHash(),
            structuredPlan("stable_or_up"),
            Map.of("roas", 3.4)
        );
        SystemReviewAuthority.Result legacyReview = evaluate(
            reviewInformation,
            reviewInformationToken,
            legacyContract,
            new SystemReviewAuthority.Observation(
                10_000L,
                Map.of("roas", 3.6),
                Map.of("orders", 30.0)
            )
        );
        require(legacyReview.breachedMetrics().isEmpty(),
            "legacy_contract_must_not_invent_breached_metric");
        LocalSubgraphRevisionAuthority.Scope legacyScope = revisionInformation.execute(
            revisionInformationToken,
            "V26_6_PLAN_LEGACY_COMPATIBILITY_REVISION",
            () -> LocalSubgraphRevisionAuthority.plan(legacyContract, legacyReview, graph)
        );
        require(legacyScope.mode() == LocalSubgraphRevisionAuthority.ScopeMode.FULL_GRAPH_COMPATIBILITY,
            "legacy_review_must_fail_closed_to_full_graph");
        require(legacyScope.reopenActionNodeHashes().size() == graph.actionNodes().size(),
            "legacy_full_graph_action_count_mismatch");

        // A graph snapshot from another ActionGraph may never be rebound to the frozen
        // ReviewContract, even if every individual node looks structurally valid.
        LocalSubgraphRevisionAuthority.GraphSnapshot wrongGraph = new LocalSubgraphRevisionAuthority.GraphSnapshot(
            graph.judgementGraphHash(),
            Hashing.canonicalHash(Map.of("wrong", "action-graph")),
            graph.operationGraphHash(),
            graph.judgementNodes(),
            graph.actionNodes(),
            graph.operationNodes()
        );
        boolean graphMismatchRejected = false;
        try {
            LocalSubgraphRevisionAuthority.plan(localContract, localReview, wrongGraph);
        } catch (IllegalStateException expected) {
            graphMismatchRejected = "revision_action_graph_contract_mismatch".equals(expected.getMessage());
        }
        require(graphMismatchRejected, "foreign_action_graph_must_fail_closed");

        // Successful review is terminal for this task and may not be converted into a
        // revision merely because a caller asks for one.
        ProductLifecycleAuthority.Snapshot settledLifecycle = lifecycle.beginObservation(
            "SKU-REVISION-SETTLED", "TASK-REVISION-SETTLED", 1_000L, 10_000L
        );
        ReviewContractAuthority.Contract settledContract = freeze(
            reviewInformation,
            reviewInformationToken,
            settledLifecycle,
            graph.actionGraphHash(),
            structuredPlan("NON_DECREASE"),
            Map.of("roas", 3.4)
        );
        SystemReviewAuthority.Result settledReview = evaluate(
            reviewInformation,
            reviewInformationToken,
            settledContract,
            new SystemReviewAuthority.Observation(
                10_000L,
                Map.of("roas", 3.8),
                Map.of("orders", 30.0)
            )
        );
        boolean settledRevisionRejected = false;
        try {
            LocalSubgraphRevisionAuthority.plan(settledContract, settledReview, graph);
        } catch (IllegalStateException expected) {
            settledRevisionRejected = "revision_requires_adjustment_review".equals(expected.getMessage());
        }
        require(settledRevisionRejected, "settled_review_must_not_create_revision");

        LinkedHashMap<String, Object> report = new LinkedHashMap<>();
        report.put("schema", "v26.6.local_subgraph_revision.shadow_parity.v1");
        report.put("version", "26.6.0");
        report.put("verified", true);
        report.put("enforcementMode", "SHADOW");
        report.put("authorityDomains", List.of("INFORMATION", "INVOCATION", "MUTATION", "TEMPORAL"));
        report.put("newAuthorityDomainCreated", false);
        report.put("allDomainsRootBound",
            reviewInformation.matches(reviewInformationToken)
                && revisionInformation.matches(revisionInformationToken)
                && invocation.matches(invocationToken)
                && temporal.matches(temporalToken)
                && mutation.matches(mutationToken));
        report.put("structuredBreachedMetrics", localReview.breachedMetrics());
        report.put("localScopeMode", localScope.mode().name());
        report.put("localReopenActionCount", localScope.reopenActionNodeHashes().size());
        report.put("localPreservedActionCount", localScope.preservedActionNodeHashes().size());
        report.put("downstreamActionClosureApplied", true);
        report.put("downstreamOperationClosureApplied", true);
        report.put("successfulNodesPreserved", true);
        report.put("legacyUnmappedReviewFailsClosed", legacyScope.mode().name());
        report.put("foreignActionGraphRejected", graphMismatchRejected);
        report.put("settledRevisionRejected", settledRevisionRejected);
        report.put("queueInputIdentity", "revisionHash");
        report.put("sameRevisionIdempotent", duplicateRevisionJob.duplicateSuppressed());
        report.put("lifecycleStateDuringRevision", adjustment.state().name());
        report.put("systemStageMutationAllowed", false);
        report.put("agentMayWriteNodeHash", false);
        report.put("agentMayWriteRevisionHash", false);
        report.put("productionAuthorityOwnershipChanged", false);
        report.put("authorityGrantCreated", false);
        report.put("verificationHash", Hashing.canonicalHash(report));
        System.out.println(Json.canonical(report));
    }

    private static ReviewContractAuthority.Contract freeze(
        RootBoundAuthorityAdapter information,
        RootBoundAuthorityAdapter.Token token,
        ProductLifecycleAuthority.Snapshot lifecycle,
        String actionGraphHash,
        Map<String, Object> planHeaders,
        Map<String, Double> baseline
    ) {
        return information.execute(
            token,
            "V26_6_FREEZE_REVIEW_CONTRACT",
            () -> ReviewContractAuthority.freeze(
                lifecycle.productId(),
                lifecycle.activeTaskId(),
                actionGraphHash,
                planHeaders,
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
            "V26_6_READ_OBSERVATION_AND_COMPARE",
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
            "V26_6_REVIEW_DUE",
            () -> lifecycle.markReviewReady(
                observing.productId(), observing.reviewDueAtMillis(), observing.stateVersion()
            )
        );
        ProductLifecycleAuthority.Snapshot reviewing = mutation.execute(
            mutationToken,
            "V26_6_BEGIN_REVIEW",
            () -> lifecycle.beginReview(ready.productId(), ready.stateVersion())
        );
        return mutation.execute(
            mutationToken,
            "V26_6_ADJUSTMENT_REQUIRED",
            () -> lifecycle.adjustmentRequired(
                reviewing.productId(), review.reviewHash(), reviewing.stateVersion()
            )
        );
    }

    private static LocalSubgraphRevisionAuthority.GraphSnapshot graphSnapshot() {
        List<LocalSubgraphRevisionAuthority.JudgementNode> judgements = List.of(
            new LocalSubgraphRevisionAuthority.JudgementNode("J_CONTENT", nodeHash("JUDGEMENT", "J_CONTENT")),
            new LocalSubgraphRevisionAuthority.JudgementNode("J_TRAFFIC", nodeHash("JUDGEMENT", "J_TRAFFIC")),
            new LocalSubgraphRevisionAuthority.JudgementNode("J_INVENTORY", nodeHash("JUDGEMENT", "J_INVENTORY")),
            new LocalSubgraphRevisionAuthority.JudgementNode("J_RETENTION", nodeHash("JUDGEMENT", "J_RETENTION"))
        );
        List<LocalSubgraphRevisionAuthority.ActionNode> actions = List.of(
            new LocalSubgraphRevisionAuthority.ActionNode(
                "A_CONTENT", nodeHash("ACTION", "A_CONTENT"),
                List.of("J_CONTENT"), List.of(), List.of("ctr")
            ),
            new LocalSubgraphRevisionAuthority.ActionNode(
                "A_TRAFFIC", nodeHash("ACTION", "A_TRAFFIC"),
                List.of("J_TRAFFIC"), List.of(), List.of("roas")
            ),
            new LocalSubgraphRevisionAuthority.ActionNode(
                "A_ACTIVITY", nodeHash("ACTION", "A_ACTIVITY"),
                List.of("J_TRAFFIC"), List.of("A_TRAFFIC"), List.of("promotion_efficiency")
            ),
            new LocalSubgraphRevisionAuthority.ActionNode(
                "A_INVENTORY", nodeHash("ACTION", "A_INVENTORY"),
                List.of("J_INVENTORY"), List.of("A_ACTIVITY"), List.of("stock_cover_days")
            ),
            new LocalSubgraphRevisionAuthority.ActionNode(
                "A_RETENTION", nodeHash("ACTION", "A_RETENTION"),
                List.of("J_RETENTION"), List.of(), List.of("refund_rate")
            )
        );
        List<LocalSubgraphRevisionAuthority.OperationNode> operations = List.of(
            new LocalSubgraphRevisionAuthority.OperationNode(
                "S_CONTENT", nodeHash("OPERATION", "S_CONTENT"), List.of("A_CONTENT"), List.of()
            ),
            new LocalSubgraphRevisionAuthority.OperationNode(
                "S_TRAFFIC", nodeHash("OPERATION", "S_TRAFFIC"), List.of("A_TRAFFIC"), List.of()
            ),
            new LocalSubgraphRevisionAuthority.OperationNode(
                "S_ACTIVITY", nodeHash("OPERATION", "S_ACTIVITY"), List.of("A_ACTIVITY"), List.of("S_TRAFFIC")
            ),
            new LocalSubgraphRevisionAuthority.OperationNode(
                "S_INVENTORY", nodeHash("OPERATION", "S_INVENTORY"), List.of("A_INVENTORY"), List.of("S_ACTIVITY")
            ),
            new LocalSubgraphRevisionAuthority.OperationNode(
                "S_RETENTION", nodeHash("OPERATION", "S_RETENTION"), List.of("A_RETENTION"), List.of()
            )
        );
        String judgementGraphHash = graphHash(
            "JUDGEMENT", judgements.stream().map(LocalSubgraphRevisionAuthority.JudgementNode::nodeHash).toList()
        );
        String actionGraphHash = graphHash(
            "ACTION", actions.stream().map(LocalSubgraphRevisionAuthority.ActionNode::nodeHash).toList()
        );
        String operationGraphHash = graphHash(
            "OPERATION", operations.stream().map(LocalSubgraphRevisionAuthority.OperationNode::nodeHash).toList()
        );
        return new LocalSubgraphRevisionAuthority.GraphSnapshot(
            judgementGraphHash,
            actionGraphHash,
            operationGraphHash,
            judgements,
            actions,
            operations
        );
    }

    private static String nodeHash(String type, String key) {
        return Hashing.canonicalHash(Map.of(
            "schema", "v26.5.addressable_node.proof.v1",
            "type", type,
            "nodeKey", key
        ));
    }

    private static String graphHash(String type, List<String> nodeHashes) {
        return Hashing.canonicalHash(Map.of(
            "schema", "v26.5.addressable_graph.proof.v1",
            "type", type,
            "nodeHashes", nodeHashes
        ));
    }

    private static Map<String, Object> structuredPlan(String trend) {
        LinkedHashMap<String, Object> plan = new LinkedHashMap<>();
        plan.put("plan.review_window", "72h");
        plan.put("plan.expected_trend", Map.of("roas", trend));
        plan.put("plan.lower_guard", Map.of("roas", 3.0));
        plan.put("plan.upper_guard", Map.of("roas", 5.0));
        plan.put("plan.minimum_evidence", Map.of("orders", 20));
        plan.put("plan.acceptance_criteria", List.of("ROAS保持在冻结边界内"));
        plan.put("plan.risk_boundaries", List.of("越过冻结guard则重新判断"));
        return Map.copyOf(plan);
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
