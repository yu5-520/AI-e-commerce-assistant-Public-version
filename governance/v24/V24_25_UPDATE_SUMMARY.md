# V24.25 Update Summary

Status: Shadow-only cutover preparation.

Core changes:

- Runtime-bind all four Unified Authority domains to one `UnifiedAuthorityGenerationRoot`.
- Add before/after generation admission around deterministic domain execution.
- Keep Queue Authority on a Root-bound GenerationFencer.
- Preserve legacy deterministic semantics and rerun Python/Java mirror evidence.
- Reject all stale domain tokens after Root rotation.
- Prove legacy production-owner snapshot remains unchanged through prepare and rollback.
- Emit Root Binding Receipts and combined Cutover-Prepare evidence.

Verified target state:

`ROOT_WIRING_VERIFIED_EXTERNAL_MIRROR_REQUIRED`

Not enabled in V24.25:

- production authority transfer;
- Java production mutation authority;
- model-created authority grants;
- external production mirror cutover;
- legacy production path removal.

Next required gate:

`PRODUCTION_MIRROR_PARITY_AND_ROLLBACK_WINDOW`
