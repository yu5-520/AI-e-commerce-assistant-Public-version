# P2 / P3.1 Bootstrap Status

**Branch:** `research/bootstrap-p2`  
**SUT baseline:** `f9e131b4a5ad448efc28502a481e8ee999999f8e`  
**Bootstrap CI:** `Research Bootstrap Smoke`  
**Seed Pack:** `P3.1-seed-v1` / `sha256:6705be93c5294635947e583541cb84e65ff23118ce97e85e6e0addd45f1e86a8`

## Passed

- [x] Isolated non-main research bootstrap branch
- [x] Benchmark / manifest / result schemas
- [x] Deterministic dummy model adapter
- [x] Neutral-generation model adapter contract with model/evaluation separation
- [x] Mock SUT paired replay
- [x] Same-proposal condition isolation check
- [x] Positive diagonal selective-authority smoke
- [x] Negative authorized-path false-block smoke
- [x] Sanitized evidence condition-leakage linter
- [x] Cost preflight primitive
- [x] Immutable run-identity + fail-closed resume contract
- [x] GitHub Actions compile + unit-test + smoke pipeline
- [x] CI pipelines use fail-closed `pipefail`; Python failure cannot be masked by `tee`
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
- [x] Latest true-green bootstrap CI includes Seed Pack + paired replay + V26 adapter + evidence artifact

## Not yet passed

- [ ] Independent GitHub research repository created (current GitHub connector cannot create repositories)
- [ ] P0/P1/P2/P3.1 full frozen documents copied into final research repo
- [ ] Real provider model adapter(s) with frozen model/version/decoding manifest
- [ ] V26 adapter expanded from contract smoke to normalized proposal -> real execution evidence
- [ ] Retry/resume persistence against an append-only result store (identity contract itself is complete)
- [ ] Raw evidence append-only/immutability enforcement in the final repo
- [ ] Evaluator calibration and kappa gate
- [ ] Formal Stage A Activation Gate
- [ ] Paid Stage A model runs

## Readiness flags

- `BOOTSTRAP_HARNESS_READY = true`
- `SEED_PACK_51_READY = true`
- `NEUTRAL_GENERATION_CONTRACT_READY = true`
- `RUN_IDENTITY_CONTRACT_READY = true`
- `V26_AUTHORITY_CONTRACT_SMOKE = true`
- `FORMAL_PILOT_READY = false`

## Next execution target

1. Add provider-neutral real model adapters and freeze experiment manifests without triggering paid runs.
2. Expand the V26 adapter from static authority-contract checks to normalized P3.1 execution evidence.
3. Implement append-only result persistence and formal Stage A Activation Gate.
4. Calibrate evaluator agreement before any scale-up.
5. Only after those gates are green, enable paid Stage A runs.
