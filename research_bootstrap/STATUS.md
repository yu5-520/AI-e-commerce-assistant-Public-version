# P2 / P3.1 Bootstrap Status

**Branch:** `research/bootstrap-p2`  
**SUT baseline:** `f9e131b4a5ad448efc28502a481e8ee999999f8e`  
**Bootstrap CI:** `Research Bootstrap Smoke`

## Passed

- [x] Isolated non-main research bootstrap branch
- [x] Benchmark / manifest / result schemas
- [x] Deterministic dummy model adapter
- [x] Mock SUT paired replay
- [x] Same-proposal condition isolation check
- [x] Positive diagonal selective-authority smoke
- [x] Negative authorized-path false-block smoke
- [x] Sanitized evidence condition-leakage linter
- [x] Cost preflight primitive
- [x] GitHub Actions compile + unit-test + smoke pipeline
- [x] Smoke evidence artifact upload
- [x] Real V26.1 field-authority import and contract receipt
- [x] V26 Information Authority smoke: Agent1 cannot write `snapshot.roas`; system can
- [x] V26 Invocation Authority smoke: Agent3 cannot write `system_stage.call_graph` or `systemStage`
- [x] V26 Temporal/Revision Authority smoke: Agent1 cannot write `revision.revision_hash`; system can
- [x] V26 positive owner paths: Agent1 judgement / Agent2 plan / Agent3 operation remain writable

## Not yet passed

- [ ] Independent GitHub research repository created (current GitHub connector cannot create repositories)
- [ ] P0/P1/P2/P3.1 full frozen documents copied into final research repo
- [ ] Full 51-case P3.1 Seed Pack
- [ ] Counterfactual and ambiguous calibration cases
- [ ] Real provider model adapter(s) with frozen model/version/decoding manifest
- [ ] V26 adapter expanded from contract smoke to normalized proposal -> real execution evidence
- [ ] Retry/resume persistence against immutable run identity
- [ ] Raw evidence append-only/immutability enforcement in the final repo
- [ ] Evaluator calibration and kappa gate
- [ ] Formal Stage A Activation Gate

## Readiness flags

- `BOOTSTRAP_HARNESS_READY = true`
- `V26_AUTHORITY_CONTRACT_SMOKE = true`
- `FORMAL_PILOT_READY = false`

## Next execution target

1. Build the full 51-case Seed Pack under frozen P1 rules.
2. Add neutral-generation manifest and real model adapter interface.
3. Expand V26 adapter from static authority-contract checks to P3.1 evidence normalization.
4. Only then enable paid Stage A runs.
