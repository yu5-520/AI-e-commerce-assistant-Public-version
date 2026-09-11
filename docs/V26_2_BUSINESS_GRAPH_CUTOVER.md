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

---

# V26.5 Addressable Node / Edge Lineage

V26.5 keeps the V26.2 cutover model but removes the remaining competition-era assumption that one business task is represented by one aggregate judgement and one aggregate action. The business graph becomes a real addressable lineage graph.

## Authority model

The fixed system graph is still unchanged. V26.5 only expands the legal business graph:

```text
BusinessTask
  ↓
JudgementGraph
  J1 ─┐
  J2 ─┼─→ ActionGraph
  J3 ─┘      A1 → A2
                  ↓
             OperationGraph
               S1 → S2 → S3
```

The model may emit local keys and structural references. It may **not** emit `nodeHash` or `edgeHash`; those are deterministically compiled by the system after field-authority validation.

## Agent1 / addressable Judgement nodes

New provider output may include `judgementNodes[]`. Each node can carry its own reasoning, issue, signals, evidence, recommended direction, priority and confidence. Judgement-to-Judgement relations are structural and must resolve to an existing node in the same graph.

Every provider-authored Judgement node must resolve to evidence. An orphan relation, duplicate node key or unsupported relation fails closed.

The historical primary judgement fields remain available only as a compatibility projection for older pipeline components and exact replays.

## Agent2 / addressable Action nodes

Agent2 receives the complete `v26JudgementGraph` through the hard projected input. New provider output may include `actionNodes[]`.

Each Action node must cite one or more valid `judgementRefs`. Action nodes may also declare dependencies, conflicts, affected fields and affected metrics. This enables one Judgement to support many Actions and many Judgements to support one Action.

The old `lockedActionFamily` / `familyPayload` remains a single compatibility projection. It no longer means that the real business task is limited to one action.

## Agent2 semantic identity

V26.5 binds the complete JudgementGraph hash into Agent2 semantic reuse identity. Two Agent2 calls with the same legacy primary lock but different JudgementGraph lineage may no longer share the same semantic cache identity.

ExecutionHash remains unchanged and remains the execution/audit authority. The V26.5 change only prevents semantic-cache aliasing across different business graphs.

## Agent3 / Action-to-Operation lineage

Each `operationStages[]` entry can contain `actionRefs`. The system resolves those Action keys and creates Action→Operation lineage edges. Stage dependencies remain Operation→Operation edges.

Historical Agent3 exact outputs that do not contain `actionRefs` are not sent through another model. During migration, the system creates an explicitly marked compatibility action-ref projection from the already accepted ActionGraph.

## Transport compatibility

Some historical capability Artifacts may not contain a V26 JudgementGraph. V26.5 handles this fail-closed without reopening Agent1: it deterministically projects the same already-authoritative Agent1 handoff into one compatibility Judgement node. New multi-node output is preserved whenever the V26 graph is present.

## Runtime installation order

The active facade now installs:

1. runtime identity / field-authority guards;
2. V26.2 Business Graph bridge;
3. V26.5 Node / Edge lineage extension;
4. existing hash-routed RAG bridge;
5. Agent3 runtime + semantic repair.

V26.5 therefore does not fork a second Agent runtime. It extends the existing V26 migration layer while the old exact execution core stays intact.

## V26.5 hard boundaries

V26.5 must not change:

- `itemExecutionId + inputContentHash` output identity;
- ExecutionHash authority;
- Artifact transport identity;
- Java-owned SystemStage and call graph;
- Generation fencing;
- FACT / DERIVED ownership;
- Agent2 sole ownership of PLAN values;
- Agent3 PLAN-number `reference_only` rule.

The architectural rule is:

> the system does not limit how many legitimate judgements or actions the model may compose; it requires every judgement to have evidence, every action to have a source, every execution stage to have an authorized action lineage, and every expansion to be addressable by system-generated hashes.
