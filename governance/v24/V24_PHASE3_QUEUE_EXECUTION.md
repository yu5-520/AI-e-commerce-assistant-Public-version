# V24.9-V24.15 — Java Queue Runtime Phase 3

## Goal

Replace the current *scheduler model* before replacing Python Agent implementations.
The existing competition runtime remains the production writer during this phase; the
Java control plane must first prove the queue semantics that will later own production.

## Current Python baseline

- one station worker owns the Agent runtime loop;
- `run_agent_pipeline_tick_hard()` selects one stage per tick;
- Agent1 is blocked while higher-priority downstream work exists;
- `pipeline_items` carries business state, queue state, payload and Artifact refs;
- one process-wide Runtime Generation Barrier serializes complete worker iterations
  against Reset.

## V24.9 Queue Split

The target model separates:

1. `PipelineItem` — authoritative business position/state only;
2. `StageJob` — executable work, priority, claim, lease and idempotency only;
3. `Artifact` — immutable stage input/output only;
4. `OutboxEvent` — atomic next-stage handoff intent only.

The PostgreSQL target DDL is frozen in `phase3-postgresql-queue-schema.sql` but is not
applied to the competition database in Shadow mode.

## V24.10-V24.12 Stage queues

Agent1, Agent2 and Agent3 have independent queue capacities. A completed Agent1 item
can create Agent2 work immediately; Agent1 does not wait for the full Agent1 batch or
for Agent2/Agent3 backlog to drain. Python still owns the Provider calls.

## V24.13 Idempotency

`idempotencyKey = SHA256(stage + itemId + inputArtifactHash + contractVersion)`.
Repeated delivery is allowed; repeated business execution is not. The target database
constraint is `UNIQUE(stage,idempotency_key)`.

## V24.14 Concurrency and backpressure

- Stage worker pools are independent.
- Claim is single-winner.
- Target database claim is `FOR UPDATE SKIP LOCKED`.
- Lease expiry requeues the job with an incremented attempt.
- Backpressure is stage-local; a slow Agent3 may extend total completion time but may
  not stop Agent1 from accepting ready work.

## V24.15 Generation fencing

Each claim carries `generationSeq`, `generationHash` and `fencingToken`. A Reset rotates
these identities. An old Provider result may return, but its commit is rejected as
`STALE_GENERATION`; system-wide waiting is no longer required by the target model.

## Phase3 release boundary

Phase3 is mergeable only when the Java verifier proves:

- split state/job/artifact/outbox model;
- duplicate enqueue -> one StageJob;
- concurrent claim -> one winner;
- expired lease recovery;
- Agent1 -> Agent2 -> Agent3 immediate handoff model;
- Agent1/Agent2 and Agent2/Agent3 execution overlap;
- Agent3 backpressure does not alter Agent1 completion schedule;
- stale generation commit is blocked;
- current Python production queue/Provider write authority is unchanged.

The next authority-cutover step can then replace the Python stage scheduler without
rewriting Agent1/Agent2/Agent3 model logic.
