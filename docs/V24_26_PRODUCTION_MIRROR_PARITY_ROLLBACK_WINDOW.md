# V24.26 Production Mirror Parity + Rollback Window

V24.26 moves the V24 authority migration from repository-only parity toward production-shaped mirror evidence while keeping the legacy production lane authoritative.

## Authority boundary

- Python / legacy runtime remains the production decision and mutation owner.
- Java V24 receives the same authority input projection in Shadow mode.
- Java produces a comparison receipt only; it cannot mutate production state.
- A mirror sample is valid only when `productionResultHash == shadowResultHash`.
- The mismatch budget is exactly zero.

## Four mirrored authority domains

- `INFORMATION`
- `INVOCATION`
- `TEMPORAL`
- `MUTATION`

Each sealed parity window must contain at least three samples for every domain. Two consecutive parity windows are required by the current policy.

## Two evidence levels

### Repository replay

Repository replay proves that the mirror mechanism, root binding, stale-generation rejection, in-flight drain rule and rollback invalidation work. It can never prove production cutover readiness by itself.

Expected state:

`MIRROR_MECHANISM_VERIFIED_EXTERNAL_EVIDENCE_REQUIRED`

### External production mirror

Only evidence with:

`evidenceSource = EXTERNAL_PRODUCTION_MIRROR`

may satisfy the production parity gate. External receipts are assembled by `scripts/build_v24_26_external_mirror_evidence.py` and then re-verified by Java.

Even when external parity is proven, V24.26 still leaves:

- `productionAuthorityOwnershipChanged = false`
- `productionMutationAllowed = false`
- `authorityGrantCreated = false`
- `cutoverAllowed = false`

The next state is only:

`PRODUCTION_MIRROR_PARITY_PROVEN_OWNER_TRANSFER_GATE_REQUIRED`

## Rollback window

The verification harness proves four temporal properties:

1. a claim admitted before Generation rotation cannot commit after rotation;
2. stale Information / Invocation / Temporal / Mutation tokens all fail closed;
3. fresh operations under the prepared Generation remain admissible;
4. rollback rotates Generation again, invalidating the prepared Generation.

This keeps migration semantics consistent with V24's `Shadow -> Parity -> Authority Transfer -> Legacy Removal` sequence.

## External receipt shape

Each append-only JSONL receipt contains:

- `windowId`
- `generationSeq`
- `generationHash`
- `fencingToken`
- `sampleId`
- `domain`
- `inputHash`
- `productionResultHash`
- `shadowResultHash`
- `shadowWriteAttempted = false`
- `productionOwnerUnchanged = true`

A separate control proof records drain, stale-generation, fresh-generation and rollback-window results. The builder only seals/group receipts; Java remains the final fail-closed decision authority.
