# P2 / P3.1 Bootstrap Status

**Branch:** `research/bootstrap-p2`  
**SUT baseline:** `f9e131b4a5ad448efc28502a481e8ee999999f8e`  
**Bootstrap CI:** `Research Bootstrap Smoke`  
**Latest evidence run:** `34852448014` / head `bc9495f1fa501b5e0721bb09eaebae90f1ff0d66` / `success`  
**Evidence artifact:** `research-bootstrap-smoke` / artifact `10351284153` / `sha256:92d300a41cfd8fba58467e0f652a1c7db8c87bcb2462904b6e3770fa494f2c0d`  
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
- [x] Immutable run identity bound to experiment, case, generation, condition, SUT commit and Authority policy hash
- [x] Tamper-evident append-only JSONL result store with hash-chain verification
- [x] Resume persistence integrated with append-only store
- [x] Resume returns stored result without re-executing the run
- [x] Resume fails closed when generation hash, condition, SUT commit or Authority policy hash drifts
- [x] Machine-readable resume persistence smoke evidence emitted to CI artifact
- [x] Cohen's kappa evaluator-calibration gate implementation
- [x] 30-item blinded evaluator calibration packet generator
- [x] Calibration packet hides bias family, expected Authority and runtime condition from evaluator-visible content
- [x] Formal Stage A Activation Gate implementation
- [x] Activation Gate explicitly requires operator paid-execution opt-in even after all research prerequisites pass
- [x] GitHub Actions compile + unit-test + smoke pipeline
- [x] CI pipelines use fail-closed `pipefail`; Python failure cannot be masked by `tee`
- [x] Research control suite covers manifest tamper, paid fail-closed, append-store integrity, identity-bound resume, kappa threshold and activation gate
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
- [x] V26 contract-probe adapter normalizes P3.1 proposal -> execution evidence without starting full application runtime
- [x] All positive Seed Pack cases show selective matched-Authority blocking in the V26 contract-probe adapter
- [x] All negative Seed Pack cases pass the corresponding real V26 owner/type path without false blocking
- [x] Latest true-green CI includes Seed Pack, blinded calibration packet, paired replay, resume persistence, V26 Authority smoke, V26 normalized replay, Stage A gate and evidence artifact

## Current Stage A blocked prerequisites

The formal gate is intentionally not allowed to self-upgrade these external prerequisites:

- [ ] `manifest_frozen`: freeze a concrete provider/model/version/decoding manifest against the actual research commit
- [ ] `provider_adapter_bound`: bind that frozen manifest to a configured provider endpoint + secret without changing the contract
- [ ] `evaluator_calibration_passed`: complete actual independent double evaluation of the blinded packet and meet the predeclared sample/kappa threshold

Until all three are true, both `FORMAL_PILOT_READY` and `paid_execution_allowed` remain false.

## Not yet passed

- [ ] Independent GitHub research repository created (current GitHub connector cannot create repositories)
- [ ] P0/P1/P2/P3.1 full frozen documents copied into final research repo
- [ ] Concrete provider/model manifest frozen against a specific externally callable model version
- [ ] Provider secret/endpoint binding validated without changing the frozen manifest
- [ ] OS/object-store level raw-evidence immutability policy in the final research runtime (application-level hash-chain immutability is ready)
- [ ] Actual evaluator labels collected for the blinded 30-item calibration packet
- [ ] Evaluator kappa/sample gate passed with real labels
- [ ] Formal Stage A Activation Gate pass
- [ ] Explicit operator opt-in for paid execution
- [ ] Paid Stage A model runs

## Readiness flags

- `BOOTSTRAP_HARNESS_READY = true`
- `SEED_PACK_51_READY = true`
- `NEUTRAL_GENERATION_CONTRACT_READY = true`
- `RUN_IDENTITY_CONTRACT_READY = true`
- `RESUME_PERSISTENCE_READY = true`
- `MANIFEST_CONTRACT_READY = true`
- `PROVIDER_HTTP_ADAPTER_READY = true`
- `APPEND_STORE_PRIMITIVE_READY = true`
- `BLINDED_CALIBRATION_PACKET_READY = true`
- `EVALUATOR_KAPPA_GATE_READY = true`
- `STAGE_A_GATE_IMPLEMENTED = true`
- `V26_AUTHORITY_CONTRACT_SMOKE = true`
- `V26_NORMALIZED_CONTRACT_REPLAY_READY = true`
- `FORMAL_PILOT_READY = false`
- `PAID_EXECUTION_ALLOWED = false`

## Next execution target

1. Freeze one concrete provider/model/version/decoding manifest after the model choice is fixed.
2. Validate provider endpoint/secret binding against that frozen manifest while keeping paid execution disabled.
3. Collect two independent label sets for the 30-item blinded calibration packet and run the predeclared kappa gate.
4. Re-evaluate Stage A Activation Gate; it must remain blocked until every prerequisite is evidenced.
5. Only after the gate is READY and the operator explicitly opts in, enable paid Stage A runs.
