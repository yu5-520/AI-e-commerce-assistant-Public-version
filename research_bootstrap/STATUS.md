# P2 / P3.1 Bootstrap Status

**Branch:** `research/bootstrap-p2`  
**SUT baseline:** `f9e131b4a5ad448efc28502a481e8ee999999f8e`  
**Bootstrap CI:** `Research Bootstrap Smoke`  
**Latest evidence run:** `34853085088` / head `ca20fd93ce344f6e93124f998ed0a210504cf5ea` / `success`  
**Evidence artifact:** `research-bootstrap-smoke` / artifact `10351890553` / `sha256:5b435b72c7856bd37c835978ce417eb5f6ad64d47cfa78e3567cd67bd94cfd04`  
**Seed Pack:** `P3.1-seed-v1` / `sha256:6705be93c5294635947e583541cb84e65ff23118ce97e85e6e0addd45f1e86a8`  
**Stage A Gate:** `BLOCKED` by design until concrete provider manifest + real provider binding + real evaluator calibration are complete.

## Passed

- [x] Isolated non-main research bootstrap branch
- [x] Benchmark / manifest / result schemas
- [x] Deterministic dummy model adapter
- [x] Neutral-generation model adapter contract with model/evaluation separation
- [x] Provider-neutral OpenAI-compatible HTTP adapter implemented; network/paid execution fail-closed by default
- [x] Concrete manifest contract requires provider, model, version, decoding and endpoint hash
- [x] Frozen experiment-manifest contract with content-hash self-verification and tamper rejection
- [x] Concrete manifest freeze CLI implemented
- [x] Provider binding validator compares provider/model/version/adapter/decoding/endpoint hash exactly
- [x] Provider binding validator can require Secret presence without exposing Secret value
- [x] Synthetic provider-binding smoke proves binding contract with zero network requests and zero paid calls
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
- [x] Blank independent-evaluator label template generated with the packet
- [x] Calibration scoring CLI checks both inter-evaluator agreement and frozen-key accuracy
- [x] Calibration gate blocks the case where two evaluators agree with each other but agree on the wrong labels
- [x] Formal Stage A Activation Gate implementation
- [x] Activation Gate explicitly requires operator paid-execution opt-in even after all research prerequisites pass
- [x] GitHub Actions compile + unit-test + smoke pipeline
- [x] CI pipelines use fail-closed `pipefail`; Python failure cannot be masked by `tee`
- [x] Research control suite covers manifest tamper, endpoint binding, Secret presence, paid fail-closed, append-store integrity, identity-bound resume, evaluator agreement/accuracy and activation gate
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
- [x] Latest true-green CI includes Seed Pack, blinded calibration packet/template, paired replay, resume persistence, provider-binding contract smoke, V26 Authority smoke, V26 normalized replay, Stage A gate and evidence artifact

## Current Stage A blocked prerequisites

The formal gate is intentionally not allowed to self-upgrade these external prerequisites:

- [ ] `manifest_frozen`: choose real provider/model/version/endpoint/decoding and freeze the concrete manifest against the research commit
- [ ] `provider_adapter_bound`: configure the corresponding real endpoint + GitHub Secret and pass the zero-network binding validator
- [ ] `evaluator_calibration_passed`: obtain two genuinely independent label sets for the blinded 30-item packet and pass both the predeclared agreement and accuracy thresholds

Until all three are true, both `FORMAL_PILOT_READY` and `paid_execution_allowed` remain false.

## Not yet passed

- [ ] Independent GitHub research repository created (current GitHub connector cannot create repositories)
- [ ] P0/P1/P2/P3.1 full frozen documents copied into final research repo
- [ ] Concrete real-model manifest frozen
- [ ] Real provider Secret/endpoint binding passed
- [ ] OS/object-store level raw-evidence immutability policy in the final research runtime (application-level hash-chain immutability is ready)
- [ ] Two actual independent evaluator label files collected
- [ ] Evaluator agreement + accuracy gate passed with real labels
- [ ] Formal Stage A Activation Gate pass
- [ ] Explicit operator opt-in for paid execution
- [ ] Paid Stage A model runs

## Readiness flags

- `BOOTSTRAP_HARNESS_READY = true`
- `ZERO_COST_ENGINEERING_BOOTSTRAP_COMPLETE = true`
- `SEED_PACK_51_READY = true`
- `NEUTRAL_GENERATION_CONTRACT_READY = true`
- `RUN_IDENTITY_CONTRACT_READY = true`
- `RESUME_PERSISTENCE_READY = true`
- `MANIFEST_CONTRACT_READY = true`
- `MANIFEST_FREEZE_CLI_READY = true`
- `PROVIDER_HTTP_ADAPTER_READY = true`
- `PROVIDER_BINDING_CONTRACT_READY = true`
- `APPEND_STORE_PRIMITIVE_READY = true`
- `BLINDED_CALIBRATION_PACKET_READY = true`
- `BLINDED_LABEL_TEMPLATE_READY = true`
- `EVALUATOR_AGREEMENT_ACCURACY_GATE_READY = true`
- `STAGE_A_GATE_IMPLEMENTED = true`
- `V26_AUTHORITY_CONTRACT_SMOKE = true`
- `V26_NORMALIZED_CONTRACT_REPLAY_READY = true`
- `FORMAL_PILOT_READY = false`
- `PAID_EXECUTION_ALLOWED = false`

## Next execution target

1. Select the two concrete real models for P3.1 Stage A and freeze one manifest per model.
2. Configure the corresponding endpoint + GitHub Secret for each provider and run zero-network binding validation.
3. Give the blinded 30-item packet to two independent evaluators; collect two completed label files without exposing the calibration key.
4. Run `score_calibration_cli.py`; both agreement and key accuracy gates must pass.
5. Re-evaluate Stage A Activation Gate.
6. Only after the gate is READY and the operator explicitly opts in, enable paid Stage A runs.
