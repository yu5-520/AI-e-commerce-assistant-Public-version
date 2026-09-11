# V26.6 Local Subgraph Revision

V26.6 connects V26.4 System Review to the addressable V26.5 business graph. It does not create another Agent, another SystemStage, or a fifth authority domain. It adds a deterministic system-owned revision scope between `ADJUSTMENT_REQUIRED` and Agent1 re-entry.

## Problem closed by V26.6

V26.4 could determine that a task needed adjustment, but its re-entry identity only carried the review result. It could not prove which Judgement, Action, or Operation nodes should be reopened. That meant a review such as "ROAS outcome failed" still had to return to Agent1 without an exact business-subgraph boundary.

V26.5 made the graph addressable:

```text
JudgementNode#hash
   ↓ supports_action
ActionNode#hash
   ↓ implemented_by
OperationNode#hash
```

V26.6 uses those identities to turn review evidence into a deterministic revision scope.

## Core rule

```text
System Review
  ↓
structured breachedMetrics
  ↓
LocalSubgraphRevisionAuthority
  ↓
exact reopen NodeHashes + preserved NodeHashes
  ↓
revisionHash
  ↓
QueueAuthority / Agent1
```

The model does not choose `nodeHash`, `edgeHash`, or `revisionHash`.

## Local selection

For a deterministic mapped breach:

1. select Action nodes whose frozen `plan.affected_metrics` intersect the structured breached metrics;
2. reopen downstream Action nodes that depend on those selected Actions;
3. reopen the Judgement nodes referenced by the reopened Actions;
4. reopen Operation nodes implementing the reopened Actions;
5. reopen downstream Operation dependencies;
6. preserve every unrelated successful node;
7. hash the complete immutable scope into `revisionHash`.

The closure is intentionally downstream. An upstream prerequisite that already succeeded remains preserved unless another explicit lineage rule selects it.

Example:

```text
A_CONTENT      (CTR)                         PRESERVED
A_TRAFFIC      (ROAS)        ← direct breach REOPEN
    ↓
A_ACTIVITY     (depends A_TRAFFIC)           REOPEN
    ↓
A_INVENTORY    (depends A_ACTIVITY)          REOPEN

A_RETENTION    (refund_rate)                 PRESERVED
```

The corresponding Operation nodes follow the same dependency closure. Supporting Judgement nodes are reactivated only for reopened Actions.

## Fail-closed compatibility

Local revision is allowed only when the review provides a trustworthy structured metric and that metric maps into the frozen Action graph.

V26.6 falls back to `FULL_GRAPH_COMPATIBILITY` when:

- a historical/non-deterministic review has no structured breached metric;
- the breached metric cannot be mapped to an Action node;
- an Action selected for revision cannot be mapped to an Operation node.

This fallback preserves V26.4 behavior. The system never invents a local scope merely to appear precise.

A graph whose `actionGraphHash` does not equal the immutable hash frozen in the ReviewContract is rejected outright. It cannot be rebound to the review after the fact.

## Lifecycle

No new lifecycle state is introduced.

```text
OBSERVING
  → REVIEW_READY
  → REVIEWING
  → ADJUSTMENT_REQUIRED
       ↓ explicit revision invocation
       Agent1 (revisionHash-scoped)
```

`ADJUSTMENT_REQUIRED` remains the product-level lock while the revision is being reasoned about. Normal product updates still cannot bypass the lifecycle gate. After the revised task is executed, the existing lifecycle can enter a new `OBSERVING` window.

## Queue identity

`QueueAuthority` remains business-agnostic. It receives only the immutable `revisionHash` as the Agent1 input identity. The Queue does not interpret the subgraph.

This gives two useful properties:

- the same revision scope is idempotent and deduplicates to the same Agent1 job;
- changing any parent graph, reopened node, preserved node, review result, or breached metric produces a different revision identity.

## Registered authority fields

V26.6 registers system-owned structural headers under `revision.*`, including:

- `revision.review_hash`
- `revision.scope_mode`
- `revision.scope_reason`
- `revision.breached_metrics`
- `revision.parent_judgement_graph_hash`
- `revision.parent_action_graph_hash`
- `revision.parent_operation_graph_hash`
- `revision.reopen_judgement_node_hashes`
- `revision.reopen_action_node_hashes`
- `revision.reopen_operation_node_hashes`
- `revision.preserved_*_node_hashes`
- `revision.revision_hash`

Owner: `java-control-plane`.

Agents may read the scope needed for their legal work, but Agent1/2/3 may not author or expand it.

## Existing authority roots

V26.6 reuses the same roots:

- `INFORMATION`: read frozen ReviewContract, System Review and addressable graph identities;
- `TEMPORAL`: preserve the existing review/lifecycle timing boundary;
- `MUTATION`: preserve product lifecycle transitions and graph revision lineage;
- `INVOCATION`: enqueue Agent1 only with the system-compiled `revisionHash`.

No fifth authority domain is created.

## Verification

`V266LocalSubgraphRevisionMain` proves that:

- System Review emits structured `breachedMetrics` instead of requiring string parsing;
- a ROAS breach selects the exact direct Action;
- downstream Action and Operation dependencies are reopened;
- unrelated successful Action/Operation nodes remain preserved;
- only supporting Judgement nodes are reactivated;
- a foreign ActionGraph hash is rejected;
- a settled review cannot be converted into a revision;
- legacy/unmapped review contracts fail closed to full-graph compatibility;
- identical `revisionHash` re-entry is idempotent in QueueAuthority;
- the product remains under `ADJUSTMENT_REQUIRED` lifecycle lock;
- SystemStage topology and production authority ownership remain unchanged.

The proof runs once in the hosted V26 authority gate and again from the existing sealed V24 Production Authority bundle before Release Hash Seal acceptance.
