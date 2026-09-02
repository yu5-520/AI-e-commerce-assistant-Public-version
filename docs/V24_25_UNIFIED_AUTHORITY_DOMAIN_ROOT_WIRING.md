# V24.25 Unified Authority Domain Root Wiring

V24.25 moves the Unified Authority design from a shared Generation identity to runtime-bound domain admission.

## What changes

Each registered authority domain is wrapped by a `RootBoundAuthorityAdapter`:

- `INFORMATION` -> `V25RetrievalAuthority`
- `INVOCATION` -> `QueueAuthority`
- `TEMPORAL` -> `TaskStateAuthority`
- `MUTATION` -> `GateEngine`

The adapter consumes `generationSeq / generationHash / fencingToken` from the single `UnifiedAuthorityGenerationRoot`, which itself reads `AuthorityGenerationStore`.

A domain adapter may not issue or rotate Authority Generation.

## Runtime invariant

For a domain operation to be accepted:

1. the caller presents the current Root generation identity;
2. the adapter verifies it before deterministic domain execution;
3. the domain operation executes without receiving generation-writer authority;
4. the adapter verifies the same generation again after execution;
5. a stale or changed generation fails closed.

`INVOCATION` additionally binds `QueueAuthority` to a Root-bound `GenerationFencer`, so queue claims and commits use the same Root identity.

## Parity evidence

V24.25 separates two evidence sources:

- legacy deterministic semantic parity: direct deterministic implementation vs Root-bound Shadow wrapper;
- Python/Java mirror parity: the existing V24 Phase2 exporter/verifier is rerun unchanged.

The two receipts are combined only in the V24.25 verification gate.

## Cutover boundary

V24.25 does **not** transfer production ownership.

The verified state is:

`ROOT_WIRING_VERIFIED_EXTERNAL_MIRROR_REQUIRED`

The next gate is:

`PRODUCTION_MIRROR_PARITY_AND_ROLLBACK_WINDOW`

Until that gate is proven:

- production mutation remains forbidden;
- Java production ownership remains forbidden;
- no authority grant is created;
- external production mirror parity is not claimed;
- cutover remains disabled.
