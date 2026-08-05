# Tombstone Scope Review

- Scope hash: `sha256:46621754374608c7f2ff97b2c19f102339bea1a65ddc289e975279cd7f1f1f74`
- Source reference count: 42
- Contest review reference count: 0
- Registered path seed count: 55
- Import-closure path count: 126

## Classification

- CORE_REVIEW: 0
- SUPPORT_REVIEW: 0
- ISOLATE_DEFER: 0
- OUTSIDE_CONTEST_SCOPE: 42

## Tombstone decisions

| Legacy path | References | Core | Support | Isolate | Outside | Recommendation |
|---|---:|---:|---:|---:|---:|---|
| `payload.creativeTestPlan` | 18 | 0 | 0 | 0 | 18 | `OUTSIDE_CONTEST_SCOPE_NOT_BLOCKING` |
| `payload.imageDirections` | 1 | 0 | 0 | 0 | 1 | `OUTSIDE_CONTEST_SCOPE_NOT_BLOCKING` |
| `payload.selectedActionFamilyHint` | 18 | 0 | 0 | 0 | 18 | `OUTSIDE_CONTEST_SCOPE_NOT_BLOCKING` |
| `payload.testPackages` | 5 | 0 | 0 | 0 | 5 | `OUTSIDE_CONTEST_SCOPE_NOT_BLOCKING` |

## Gate

`KEEP_IN_CONTEST_AND_MIGRATE` references block physical field deletion. References
outside the selected closure do not block contest-chain pruning, but remain preserved
until their owning files are separately isolated or removed by an approved transaction.

This report performs no runtime, database, provider, or physical deletion action.
