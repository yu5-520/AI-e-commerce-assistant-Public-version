# P2 / P3.1 Bootstrap Status

**Branch:** `research/bootstrap-p2`  
**SUT baseline:** `f9e131b4a5ad448efc28502a481e8ee999999f8e`  
**Bootstrap CI:** `Research Bootstrap Smoke`  
**Seed Pack:** `P3.1-seed-v1` / `sha256:6705be93c5294635947e583541cb84e65ff23118ce97e85e6e0addd45f1e86a8`  
**Stage A Gate:** `BLOCKED` by design until concrete provider manifest + provider binding + real evaluator calibration are complete.

## Passed

- [x] Isolated non-main research bootstrap branch
- [x] Benchmark / manifest / result schemas
- [x] Deterministic dummy model adapter
- [x] Neutral-generation model adapter contract with model/evaluation separation
- [x] Provider-neutral OpenAI-compatible HTTP adapter implemented; network/paid execution fail-closed by default
- [x] Frozen experiment-manifest contract with content-hash self-verification and tamper rejection
- [x] Mock SUT paired replay
- [x] Same-proposal condition isolation check
- [x] Positive diagonal selective-authority smoke
- [x] Negative authorized-path false-block smoke
- [x] Sanitized evidence condition-leakage linter
- [x] Cost preflight primitive
- [x] Immutable run-identity + fail-closed resume contract
- [x] Tamper-evident append-only JSONL result-store primitive with idempotent run replay and conflict rejection
- [x] Cohen's kappa evaluator-calibration gate implementation
- [x] Formal Stage A Activation Gate implementation
- [x] Activation Gate explicitly requires operator paid-execution opt-in even after all research prerequisites pass
- [x] GitHub Actions compile + unit-test + smoke pipeline
- [x] CI pipelines use fail-closed `pipefail`; Python failure cannot be masked by `tee`
- [x] Research control suite: manifest tamper, paid fail-closed, append-store integrity, kappa threshold, activation gate
- [x] Smoke evidence artifact upload
- [x] Full 51-case P3.1 Seed Pack generated and hash frozen
- [x] Per-bias Seed Pack composition: 6 positive + 6 negative + 3 counterfactual + 2 calibration
- [x] Model-visible case content physically separated from evaluation-only labels and fixture proposals
- [x] Explicit theory/condition leakage validation on Seed Pack
- [x] Real V26.1 field-authority module loaded without booting the full app runtime
- [x] V26 Information Authority smoke: Agent1 cannot write `snapshot.roas`; system can
- [x] V26 Invocation Authority smoke: Agent3 cannot write `system_stage.call_graph` or `systemStage`
- [x] V26 Temporal/Revision Authority smoke: Agent1 cannot write `revision.revision_hash`; system can
- [x] V26 positive owner paths: Agent1 judgement / Agent2 plan / Agent3 operation remain writable
- [x] Latest true-green bootstrap CI includes 11 unit/control tests + Seed Pack + paired replay + V26 adapter + Stage A gate + evidence artifact

## Current Stage A blocked prerequisites

The formal gate is intentionally not allowed to self-upgrade these external prerequisites:

- [ ] `manifest_frozen`: freeze a concrete provider/model/version/decoding manifest against the actual research commit
- [ ] `provider_adapter_bound`: bind that manifest to a configured provider endpoint + secret without changing the frozen contract
- [ ] `evaluator_calibration_passed`: run actual double-evaluator calibration and meet the predeclared kappa/sample threshold

Until all three are true, both `FORMAL_PILOT_READY` and `paid_execution_allowed` remain false.

## Not yet passed

- [ ] Independent GitHub research repository created (current GitHub connector cannot create repositories)
- [ ] P0/P1/P2/P3.1 full frozen documents copied into final research repo
- [ ] Concrete provider/model manifest frozen against a specific externally callable model version
- [ ] Provider secret/endpoint binding validated without changing the frozen manifest
- [ ] V26 adapter expanded from contract smoke to full P3.1 normalized proposal -> execution evidence replay
- [ ] Retry/resume runner integrated with the append-only store across interrupted executions
- [ ] OS/object-store level raw-evidence immutability policy in the final research repo/runtime
- [ ] Actual evaluator calibration dataset and kappa gate pass (calculator/gate code is ready)
- [ ] Formal Stage A Activation Gate pass (gate code is ready; current status is correctly BLOCKED)
- [ ] Explicit operator opt-in for paid execution
- [ ] Paid Stage A model runs

## Readiness flags

- `BOOTSTRAP_HARNESS_READY = true`
- `SEED_PACK_51_READY = true`
- `NEUTRAL_GENERATION_CONTRACT_READY = true`
- `RUN_IDENTITY_CONTRACT_READY = true`
- `MANIFEST_CONTRACT_READY = true`
- `PROVIDER_HTTP_ADAPTER_READY = true`
- `APPEND_STORE_PRIMITIVE_READY = true`
- `EVALUATOR_KAPPA_GATE_READY = true`
- `STAGE_A_GATE_IMPLEMENTED = true`
- `V26_AUTHORITY_CONTRACT_SMOKE = true`
- `FORMAL_PILOT_READY = false`
- `PAID_EXECUTION_ALLOWED = false`

## Next execution target

1. Expand the V26 adapter from static contract checks to normalized P3.1 execution evidence while keeping the full app runtime off.
2. Freeze one concrete provider/model/version/decoding manifest only after the provider/model choice is final.
3. Produce an actual double-evaluator calibration set and pass the predeclared kappa/sample gate.
4. Re-evaluate Stage A Activation Gate; it must remain blocked until every prerequisite is evidenced.
5. Only after the gate is READY and the operator explicitly opts in, enable paid Stage A runs.
