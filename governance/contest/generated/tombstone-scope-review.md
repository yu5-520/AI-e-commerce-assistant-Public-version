# Tombstone Scope Review

- Scope hash: `sha256:8c41c9805544cf12deb05ab1205ec06143d4c409ee2a0582f83a02fccbe58f16`
- Source reference count: 67
- Contest review reference count: 25
- Registered path seed count: 55
- Import-closure path count: 126

## Classification

- CORE_REVIEW: 25
- SUPPORT_REVIEW: 0
- ISOLATE_DEFER: 0
- OUTSIDE_CONTEST_SCOPE: 42

## Tombstone decisions

| Legacy path | References | Core | Support | Isolate | Outside | Recommendation |
|---|---:|---:|---:|---:|---:|---|
| `payload.creativeTestPlan` | 24 | 6 | 0 | 0 | 18 | `KEEP_IN_CONTEST_AND_MIGRATE` |
| `payload.imageDirections` | 1 | 0 | 0 | 0 | 1 | `OUTSIDE_CONTEST_SCOPE_NOT_BLOCKING` |
| `payload.selectedActionFamilyHint` | 37 | 19 | 0 | 0 | 18 | `KEEP_IN_CONTEST_AND_MIGRATE` |
| `payload.testPackages` | 5 | 0 | 0 | 0 | 5 | `OUTSIDE_CONTEST_SCOPE_NOT_BLOCKING` |

## Gate

`KEEP_IN_CONTEST_AND_MIGRATE` references block physical field deletion. References
outside the selected closure do not block contest-chain pruning, but remain preserved
until their owning files are separately isolated or removed by an approved transaction.

This report performs no runtime, database, provider, or physical deletion action.
