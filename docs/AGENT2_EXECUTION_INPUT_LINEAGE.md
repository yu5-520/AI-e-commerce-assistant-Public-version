# Agent2 exact compiled input binding

The pipeline prepares a canonical Agent2 input, then creates a versioned compiler
input before invoking the token runtime. Previously, if the runtime returned no
draft, proof lookup reverted to the canonical input. That lookup could hide both
an execution recorded under the compiler input and a failure before execution
registration. An empty canonical-input query therefore did not establish that no
provider call had happened.

The existing registered Agent2 runner now persists `agent2ExecutionInputRef` in
the task's reference column **before** the corresponding call. The canonical
`agent2DraftInputRef` remains the source parent. Binding uses the active claim ID
and running status as compare-and-set conditions; a different worker cannot
replace it. Regeneration and contract-repair attempts rotate the binding to their
own input before calling the runtime.

Proof lookup uses the bound input even when no in-memory draft was returned.
A returned draft for another input is rejected. Bound executions cannot fall
through to the old per-package proof path. Hash and accepted-output verification
remain owned by the existing strict bridge. No historical same-product scan is
added to the active execution path, and no accepted state is manufactured.

Preparation errors are attached to the affected package in `itemFailures`.
Failure handling retains these errors, or the provider's errors, instead of
overwriting them with `agent2_hash_accepted_execution_missing`. The existing
failure classification is also written to `last_error_code` for diagnostics.
Historical dead letters are not automatically requeued by this change.

## Deployment and one read-only diagnosis

Deploy the formal sealed main commit with the existing `deploy-ai-release` wrapper.
The prior exact release remains the rollback target. Then inspect the reported
task through the registered Artifact edges, including compiler descendants:

```bash
cd /opt/ai-ecommerce-assistant/current && .venv/bin/python config/deployment/agent2_lineage_status.py PI-5490DCAEA498973F
```

The tool opens SQLite in read-only mode, follows at most eight edge levels and
256 input nodes, and prints references, execution state and error diagnostics.
It does not invoke the application bootstrap, call a model, requeue a task or
change output acceptance. It can diagnose existing records which predate the new
binding. It does not expose report contents, prompts or full model outputs.

## Scope and remaining work

The `agent2_runtime` registry module declares the runtime, token runtime, failure
scheduler and diagnosis implementation. The existing BASE/TARGET compiler checks
`governance/agent2-execution-input-lineage-update-policy.json`; no compiler gate is
relaxed. Tests cover claimed binding, stale claims, exact compiled lookup when a
draft is absent, mismatched returned inputs and preparation-error propagation.

ECS diagnosis on 2026-09-10 followed the compiler descendant and found an
execution rejected with `contract_invalid_identity`: the stored provider plan
contained a packageId but no usable itemExecutionId or inputContentHash. The task
record predates the compiled-input binding release.

### Output identity contract 22.5.20.1

The base business prompt previously said to return only packageId and a business
channel, and said the system would inject execution identity. The exact runtime
then appended an instruction requiring both identity fields. The base prompt now
explicitly distinguishes system-owned business state from transport identity,
and the exact request includes an outputContract requiring both fields at plan
top level, copied from the corresponding input. All four business channels use
the same identity requirement. The gateway already preserves parsed provider
fields; no response identity is synthesized or rebound.

The token prompt version advances to 22.5.20.1. Both execution identity and
semantic cache contract include this version, separating new requests from old
contract results. Regression tests exercise the composed real prompt, per-item
input identities, all four result channels, and rejection without completion of
the observed identity-less response. This removes a demonstrated contradictory
instruction; it does not prove every future model response will satisfy the
contract. Historical dead letters remain unchanged until an explicit retry
through the existing task entry after deployment.

This release changes the existing Python Agent2 path. It does not implement Java
ownership of Gate/Task State, Queue/Generation, Runtime Admission or Frontend/SSE,
and it does not claim a Java production authority transfer. Those interfaces need
real state adapters before an ownership switch can execute business operations.
