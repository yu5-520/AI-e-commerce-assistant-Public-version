# Contest Governance Consolidated Review

- State: `REVIEW_READY_FOR_HUMAN_APPROVAL`
- Consolidated hash: `sha256:f1efe99fa15eca67e9c2c306f635f2431a34890832a7eefd22c33e11723d0e99`
- Graph hash: `sha256:95cc643fcf7b468bf86e1dbbfd65fe29fc40392849aa2b752a07413226902d00`
- Selection hash: `sha256:d6d19db916506c22542bbf68e01871330eae0efd110d9abd9a9d05d82e071c7e`

## Module selection

- KEEP_CORE: 14
- KEEP_SUPPORT: 2
- ISOLATE: 7
- REMOVE_CANDIDATE: 0
- REVIEW_REQUIRED: 0

## Resolved machine gates

- `REGISTRY_DOCUMENT_HASHES`: `VERIFIED` — True
- `LAYERED_ROOT_COMPOSITION`: `VERIFIED` — True
- `RUNNER_DISPATCH`: `VERIFIED` — {'registeredModuleCount': 23, 'runnerDriftCount': 0}
- `Z_REGISTRY_PROTOCOL_ADAPTER`: `VERIFIED_RUNTIME_SWITCH_NOT_AUTHORIZED` — {'adapterHash': 'sha256:0aa85e368a399f7ab2227b58b23ba90c51aa5ad8352cc20ae3499bf9b1af65ed', 'normalizedRegistryRootHash': 'sha256:6eeeec1afa3ff5acd14c569f8e2c9f6b39453654ee46ba078d5a8b49b8cbe95f', 'sourceDocumentHashesVerified': True, 'zCompilerVerified': True}
- `UNREGISTERED_FIELD_SCOPE_PARTITION`: `VERIFIED` — {'sourceCandidateCount': 500, 'partitionCandidateCount': 500, 'scopeHash': 'sha256:c2b581e523ec730a454903f0f3b155ad5fc0845f3f9a1b919e37ab5f4c675d84'}
- `TOMBSTONE_SCOPE_PARTITION`: `VERIFIED` — {'sourceReferenceCount': 67, 'partitionReferenceCount': 67, 'scopeHash': 'sha256:8c41c9805544cf12deb05ab1205ec06143d4c409ee2a0582f83a02fccbe58f16'}
- `REGISTERED_ONLY_ISOLATION_SIMULATION`: `VERIFIED_PHYSICAL_CHANGE_NOT_AUTHORIZED` — {'transactionHash': 'sha256:dacdb65cf2604a3adeef743dadf6db2c142319a72bc104cb848df55d85a5ef50', 'simulationHash': 'sha256:aa744a2227dc5dcb5bff7b01626add8561ff839f575f3cca1b4a563255db3656', 'assertions': {'classificationsStable': True, 'excludedPathsOutsideKeepClosure': True, 'graphHashStable': True, 'keepRunnerEvidenceVerified': True, 'noRunnerDriftOutsideIsolateSet': True, 'rootCompositionVerified': True, 'safeKeepModulesStable': True, 'selectionHashStable': True}}

## Human review required

- Core unregistered field candidate occurrences: 172
- Core Tombstone reference occurrences: 25
- Core Tombstone keys requiring migration: 2

### Tombstone migration blockers

| Legacy path | References | Recommendation |
|---|---:|---|
| `payload.creativeTestPlan` | 24 | `KEEP_IN_CONTEST_AND_MIGRATE` |
| `payload.selectedActionFamilyHint` | 37 | `KEEP_IN_CONTEST_AND_MIGRATE` |

## Isolation decision

Seven REGISTERED_ONLY modules are logically isolated. Detached-worktree
simulation confirms that excluding their two implementation files keeps the
Graph Hash, Selection Hash, module classifications, core/support Runner gates,
and layered Registry-root composition stable. Physical deletion remains
unauthorized until human approval.

## Approval boundary

Approval may authorize the next transaction to migrate the two blocking legacy
fields and isolate the seven REGISTERED_ONLY modules. It does not authorize
promotion to main, public release, database mutation, provider calls, or any
other physical deletion.
