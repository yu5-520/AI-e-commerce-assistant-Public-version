# V24.25 Root Wiring Invariants

1. `AuthorityGenerationStore` remains the single durable Authority Generation writer.
2. `UnifiedAuthorityGenerationRoot` is the only generation source exposed to authority-domain consumers.
3. A `RootBoundAuthorityAdapter` is a consumer, never a generation issuer.
4. The model cannot create, rotate, widen, or revive Authority Generation.
5. A domain operation must validate generation both before and after deterministic execution.
6. An old generation fails closed for Information, Invocation, Temporal and Mutation authority.
7. Root rotation does not mutate the legacy production owner lane during Shadow.
8. Root binding must not change deterministic domain semantics.
9. Python/Java mirror evidence stays independently verifiable.
10. V24.25 cannot transfer production ownership or remove the legacy production path.
