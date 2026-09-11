# V26.3 Pre-Agent Admission

## Purpose

V26.3 moves deterministic admission upstream of Agent1. Product data is no longer allowed to create a QueueAuthority pipeline item merely because a new observation arrived.

The system decides whether the observation is statistically worth attention; only admitted observations consume Agent compute.

## Authority boundary

```text
Business data
  -> ProductLifecycleAuthority
  -> VolatilityEnvelope
  -> PreAgentAdmissionGate   [INVOCATION authority]
       | SKIP  -> no PipelineItemState, no Agent job
       | ADMIT -> QueueAuthority.registerItem -> enqueue AGENT1
```

This does **not** add a fifth authority domain. Admission is bound to the existing INVOCATION root and uses the current Authority Generation token.

`TaskStateAuthority` remains unchanged. It owns post-task state; it is not reused as product lifecycle state.

## Product lifecycle in V26.3

The first cut intentionally exposes only the states required to prove upstream admission:

- `MONITORING`: product is not inside an active observation lock.
- `OBSERVING`: product is inside the observation window of an active task.

Review execution, task settlement, local stage revision and knowledge return are later phases.

## Statistical envelope

V26.3 does not encode category-specific business rules. `VolatilityEnvelope` evaluates a metric against its own baseline and historical volatility using:

- relative change from baseline;
- historical volatility rate;
- lower/upper multipliers;
- minimum sample requirement;
- persistence requirement;
- deterministic near-zero baseline protection.

Signals are deliberately business-neutral:

- `NORMAL`
- `LOWER_BREAK`
- `UPPER_BREAK`
- `INSUFFICIENT_SAMPLE`
- `INSUFFICIENT_BASELINE`
- `PERSISTENCE_NOT_MET`

Java detects change. Agent1 interprets business meaning.

## Admission semantics

Only a persistent, evidence-sufficient `LOWER_BREAK` or `UPPER_BREAK` is admitted in this phase.

Everything else is skipped before QueueAuthority. In particular, an `OBSERVING + NORMAL` product remains locked and creates no Agent1 work.

This preserves attribution: a product being observed is not repeatedly modified merely because fresh data arrived.

## Shadow/parity proof

`V263PreAgentAdmissionMain` is compiled into the existing sealed Java authority bundle and proves:

1. monitoring + normal -> SKIP;
2. observing + normal -> SKIP;
3. lower break + enough sample/persistence -> ADMIT;
4. upper break + enough sample/persistence -> ADMIT;
5. insufficient baseline -> SKIP;
6. insufficient sample -> SKIP;
7. persistence not met -> SKIP;
8. near-zero baseline -> fail closed without division;
9. admitted products enter the existing QueueAuthority at `AGENT1`;
10. skipped products create zero QueueAuthority item/job side effects.

The proof runs under the existing root-bound INVOCATION token. V26.3 therefore changes admission semantics without changing Agent topology, Queue handoff semantics, Authority Generation ownership or production mutation authority.

## Next phase boundary

V26.3 does not implement System Review. The next lifecycle phase can add deterministic `review_due -> System Review` and only re-enter Agent1 for mixed/deviant/ambiguous outcomes, while keeping this pre-Agent admission boundary intact.
