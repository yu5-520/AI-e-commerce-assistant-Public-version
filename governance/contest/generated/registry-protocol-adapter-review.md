# Product Registry → Z Protocol Review

- State: `PROTOCOL_ADAPTER_VERIFIED_RUNTIME_SWITCH_NOT_AUTHORIZED`
- Adapter hash: `sha256:0aa85e368a399f7ab2227b58b23ba90c51aa5ad8352cc20ae3499bf9b1af65ed`
- Normalized Registry root: `sha256:6eeeec1afa3ff5acd14c569f8e2c9f6b39453654ee46ba078d5a8b49b8cbe95f`
- Source document hashes verified: `True`
- Z compiler verified: `True`

## Document mappings

| Document | Mapping | Source collection/id | Target collection/id | Records |
|---|---|---|---|---:|
| `fields.json` | `PASSTHROUGH` | `fields/fieldId` | `fields/fieldId` | 63 |
| `schemas.json` | `PASSTHROUGH` | `schemas/schemaId` | `schemas/schemaId` | 18 |
| `interfaces.json` | `PASSTHROUGH` | `interfaces/interfaceId` | `interfaces/interfaceId` | 6 |
| `modules.json` | `PASSTHROUGH` | `modules/moduleId` | `modules/moduleId` | 23 |
| `ownership.json` | `COLLECTION_AND_IDENTITY_ADAPTER` | `rules/scope` | `ownership/ownershipId` | 13 |
| `migrations.json` | `PASSTHROUGH` | `migrations/migrationId` | `migrations/migrationId` | 2 |
| `stations.json` | `PASSTHROUGH` | `stations/stationId` | `stations/stationId` | 7 |
| `tombstones.json` | `COLLECTION_AND_IDENTITY_ADAPTER` | `candidates/legacyPath` | `tombstones/tombstoneId` | 4 |

## Authority boundary

The normalized documents exist only in an ephemeral verification directory.
The product Registry, source manifest, runtime projection, database, provider
state, and deployed runtime are unchanged.
