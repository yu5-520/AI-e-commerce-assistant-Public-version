# V26.2 Business Graph Cutover

V26.2 does not replace the proven Agent execution runtime. It moves live business semantics onto authority-bearing graphs while preserving exact Artifact identity, ExecutionHash, Generation fencing, Java control-plane ownership and the fixed three-Agent system topology.

## Cutover rule

`SystemGraph = fixed control topology`

`BusinessGraph = active model surface`

The system continues to own where an Agent may run, what data it may see, which stage it occupies, what permissions it has and what mutations are legal. Inside that legal surface, the model may use a larger reasoning, strategy and execution-planning surface.

Legacy `agent1.*`, `agent2.*` and `agent3.*` business objects remain compatibility mirrors during migration. They no longer define the V26 business authority. The authority-bearing surfaces are:

- Agent1 → `JudgementGraph` → `judgement.*`
- Agent2 → `ActionGraph` → `plan.*`
- Agent3 → `OperationGraph` → `operation.*` + dynamic `operation_stage.*`

## Agent1 / JudgementGraph

Agent1 may express the primary issue, secondary signals, signal conflicts, possible causes, rejected hypotheses, ignored signals, evidence references, confidence and recommended direction. The authority layer validates exact headers, types and ownership; it does not keyword-score or rewrite business reasoning.

Observed facts and deterministic derived metrics remain outside Agent1 write authority.

## Agent2 / ActionGraph

Agent2 owns business strategy and PLAN values. It may compare candidate strategies, select a strategy, produce parameter packs, expected trends, review windows, guard bounds, evidence requirements and acceptance criteria.

Agent2 may not rewrite Snapshot facts, deterministic DERIVED metrics or Agent1's system-locked execution identity/authority boundary.

## Agent3 / OperationGraph

Agent3 converts the accepted strategy into a dynamic business operation plane. `operationStages` may contain as many business stages as the task requires. A stage may describe dependencies, inputs, steps, tool capabilities, artifacts, acceptance criteria, rollback, affected fields/metrics and review entry.

Dynamic OperationStage count does **not** grant system authority. Agent3 still cannot create Agent4, mutate the Agent call graph, create a SystemStage or bypass Java permissions.

Agent3 consumes Agent2 plan values through registered `plan.*` references. It does not originate or rewrite PLAN numbers.

## Compatibility and replay

New provider calls are prompted to emit dynamic `operationStages`. Historical exact Agent3 outputs may not contain this field. During the cutover, V26.2 deterministically projects each already-authoritative historical `executionStep` into one compatibility OperationStage. This projection does not call another model and does not change the old execution identity.

## Runtime installation

The V26.2 bridge is installed through the existing active runtime facade, following the repository's existing `install_*` migration pattern. Historical Agent cores continue to own their proven exact execution, normalization and compatibility logic; V26.2 wraps their prompt/normalization seams and compiles authority graphs from the same accepted provider result.

Installation order is fail-closed:

1. runtime identity / field authority guards;
2. V26.2 Business Graph bridge;
3. existing hash-routed RAG bridge;
4. Agent3 runtime + semantic repair.

## Non-regression boundary

V26.2 must not change:

- the fixed system Agent topology;
- Java/control-plane ownership of SystemStage;
- `itemExecutionId + inputContentHash` provider-output identity;
- ExecutionHash audit authority;
- Generation fencing;
- Release Hash Seal semantics;
- FACT / DERIVED write ownership;
- Agent3 PLAN-number `reference_only` rule.

The intended generation change is therefore:

> the control plane stays strict, while the legal business active surface becomes larger.
