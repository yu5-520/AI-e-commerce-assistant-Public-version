# Reality Bias Research Bootstrap

Temporary isolated bootstrap for the future independent research repository.

> This branch is **not** the product mainline. It exists only because the connected GitHub app cannot create a new repository. The final research repository must be separated from the V26 system-under-test.

## Frozen upstream

- P0: Research Freeze
- P1: Theory Contract
- P2: Research Repo Architecture
- P3.1: Pilot Hardening
- SUT baseline: `AI-e-commerce-assistant-Public-version@f9e131b4a5ad448efc28502a481e8ee999999f8e`

## Bootstrap goal

Before any paid pilot run, make these gates green:

1. schema validation
2. dummy model adapter
3. mock SUT paired replay
4. condition-isolation hash check
5. sanitized-evidence leakage check
6. retry/resume identity check
7. cost preflight
8. raw evidence immutability convention

## Research invariant

`Neutral Generation -> Freeze Proposal -> Same Proposal -> Different Runtime Authority -> Realization`

The runtime condition may change authority policy only. It must not change model input, frozen proposal, task contract, or canonical pre-state.

## Status

`PILOT_READY = false` until CI proves all bootstrap checks.