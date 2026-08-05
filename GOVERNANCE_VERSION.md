Unified Registry Governance Version: 23.2.4
Registry Manifest Contract Version: 23.0.0-alpha.1
Runtime Receipt Contract Version: 23.0.0-rc.1
Requirement Self-Update Contract Version: 23.1.0
Requirement Runner Version: 23.1.1
Post-Codegen PR Gate Version: 23.2.3
Active Agent1 Binding Version: 23.1.4
Repository Self-Update Skill Version: 23.2.0
Registry Root Migration Protocol Version: 23.2.3
Active Agent2 Owner Alignment Version: 23.2.4
Registry Root Hash: sha256:ff37e43cd374986b1edf1ff735e97d6b19c9635efd2a0167e68f2943444dcdbd
Production Gate Mode: selected_fail_closed
Production Gate Modules: release_governance, agent1_input_projection, agent1_runtime, agent2_runtime
Canonical Governance Document: docs/V23.2.0_REPOSITORY_SELF_UPDATE_SKILL.md

V23.2.0 moves pre-codegen module resolution and access scope into a repository-owned,
executable Skill Runtime. The Assistant translates product intent and performs only scoped
edits; the repository decides the active module chain, allowed read paths, allowed write
paths, required tests, diagnostics, receipts and final VERIFIED status.

Normal update transactions compile these hard constraints:

```text
repositoryWideSearchAllowed = false
fuzzyFilenameSearchAllowed = false
allowedWritePaths ⊆ allowedReadPaths
```

If active ownership is unresolved, the business transaction becomes
`BLOCKED_BY_PLATFORM_DIAGNOSTIC`. A separate `DIAG-*` transaction must be created and
verified before the original Requirement is resolved again. The Assistant may not replace
this state transition with manual fuzzy repository search.

The authoritative Agent1 chain from V23.1.4 remains unchanged:

```text
station_agent_worker_v2259
→ station_agent_worker_v22515
→ agent_runtime_hard_interface_v22515
→ agent_runtime_hard_interface_v22514
→ agent_runtime_hard_interface_v2257
→ agent_input_transport_v2258
→ agent_token_runtime_hash_exact_v2259
```

V23.2.0 does not rotate the registry root, alter Agent business logic, call a Provider,
mutate SQLite business data, deploy ECS or execute historical recovery.

V23.2.1 Gray/Production Binding Isolation:

- Gray preflight remains stdlib-only and derives owner evidence with AST and sealed hashes.
- Production startup executes the dynamic callable-owner probe in the prepared runtime.
- Gray and Production compare one stable owner-map Hash while retaining mode-specific evidence Hashes.
- Each selected module owns independent moduleErrors, loadStatus and bindingStatus.
- Any required-module failure still blocks deployment; errors no longer contaminate unrelated module receipts.

V23.2.3 Registry Root Migration Protocol:

- Ordinary business approvals still require the approved Registry Root to equal repository truth.
- A Registry migration requires an explicit `registryMigrationPlan` bound to the Base Root,
  exact Registry file paths, target modules, a migration reason and a deterministic plan Hash.
- Post-Codegen verification compiles the approved scope from the Base revision, then verifies
  the Head Registry Manifest, Base/Head Root transition, per-file content hashes and target
  module contract changes.
- Unlisted Registry paths, unchanged Roots, stale Base Roots, invalid Manifests and unrelated
  module contract changes remain fail-closed.
- Module contract hashes are module-local; the global Registry Root is retained as a separate
  receipt identity so an approved Registry migration does not rewrite every unrelated module Hash.

V23.2.4 Active Agent2 Owner Alignment:

- Registry and Runtime Projection now bind `agent2_runtime` to the production V22.5.15 facade.
- Module Contract hashes cover the active facade, runtime, contract, action-draft core,
  Hash Proof Bridge and Hash Execution Runtime.
- V230 remains available as the sealed release compatibility ingress, but it is no longer the
  Agent2 Registry owner or the sole Codegen target.
- Release compatibility identity and Agent2 module ownership are verified independently.
- This migration changes no Agent2 business output, calls no Provider, mutates no SQLite data
  and executes no historical task recovery.
