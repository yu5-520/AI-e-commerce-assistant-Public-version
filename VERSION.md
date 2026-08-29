Current Product Version: 22.4.0
Public API Version: 22.4.0
Public Contract Version: 22.4.0
Runtime Mode: single_release_sealed_runtime

Deployment Single Authority Version: 22.5.4
Three-Agent Responsibility Model: 22.5.0
Three-Agent State Machine Version: 22.5.5
Execution Lock Version: 22.5.5
Agent1 Input Semantic Version: 22.5.8
Agent1 Input Schema: agent_input.agent1.v3
Hash-Directed Artifact Runtime: 22.5.9
Agent1 Strict Token Runtime: 22.5.9
Active Hard Interface Facade: 22.5.9
Active Station Worker Metadata: 22.5.9
Frontend View Artifact Version: 22.5.9
Frontend View Manifest Version: 22.5.9
Interface Documentation Sync: 22.5.10
Canonical Interface Document: docs/V22.5.9_INTERFACE_AND_MIGRATION.md

Knowledge Promotion & Review Audit Version: 25.10.0
Knowledge Lifecycle Authority Version: 25.11.0
Knowledge Index Manifest & Head Version: 25.12.0
RAG Quantification & Retrieval Observability Version: 25.13.0
RAG Eval & Regression Authority Version: 25.14.0
Chinese RAG Knowledge Center Version: 25.15.0
V25 Physical RAG Provider Cutover: false
V25 Vector Index Required: false
V25 New Agent Runtime: false

V24 Production Authority Bundle Version: 24.21.0
V24 Java Runtime: Temurin 17.0.20.1+1 embedded jlink
V24 Java Enforcement Mode: READY_NO_AUTHORITY
V24 Java Production Mutation: false
V24 Atomic Five-Authority Cutover: pending
V24 Deployment Authority Transfer: false
V24 Legacy Removal: disabled

Release Identity Contract: release.identity.v1
Release Manifest Contract: release.manifest.v1
Data Identity Contract: data.identity.v1
Data Lineage Contract: release.data-lineage.v1
Release Seal Mode: hash_manifest_lite
Release Artifact Origin: successful push to main only
Release Python Runtime: 3.11.9
Bootstrap Verifier Runtime: Python 3.6 compatible
Root Verifier Rotation: explicit root authorization only
Gray Evidence Semantic Binding: required
SQLite Data Lineage: required after sealed deployment
Production Dependency Contract: requirements.lock
Gray-Test Dependency Contract: requirements-dev.lock
Pipeline Artifact Contract Version: 22.3.0
Station Truth Contract Version: 22.2.5
Public Task DTO Version: 22.2.3

Repository Commit identifies source. Release Hash identifies the complete sealed file set. Dependency Lock Hash and the live environment Hash identify the exact Python environment. Test Run Hash identifies packaged gray-test evidence. Data Lineage binds the live SQLite schema and rollback backup to the deployed Release Hash. A deployable Artifact additionally requires a successful `push` run on `main`, with Workflow SHA, Artifact identity, Manifest `sourceCommit` and test-attestation `sourceCommit` all bound to the same commit.

# V25.10-V25.15 — Versioned Knowledge, Quantification and Eval Layer

The V25 knowledge subsystem is versioned independently from the public product/API and deployment authority. It retains the existing physical RAG provider and Three-Agent runtime while adding governed immutable knowledge revisions, lifecycle authority, manifest/head binding, receipt-bound quantification, immutable EvalSet/EvalRun regression authority, and the Chinese RAG Knowledge Center.

```text
Knowledge promotion/review audit       25.10.0
Knowledge lifecycle authority          25.11.0
Knowledge index manifest/head          25.12.0
RAG quantification/observability        25.13.0
RAG Eval/regression authority           25.14.0
Chinese RAG Knowledge Center            25.15.0
```

These versions do not replace `Current Product Version`, `Public API Version`, the V22.5 deployment authority, the physical RAG provider, or the existing Agent runtime. V25 knowledge cannot create a system fact, direct database mutation from the Knowledge Center is forbidden, and Active knowledge revisions cannot be edited in place.

# V22.5.10 — Interface Contract Synchronization

## Purpose

V22.5.10 synchronizes documentation and API self-description with the already active V22.5.9 Hash-directed runtime. It does not introduce another Agent, queue, Worker or deployment authority.

## Current layered identity

```text
Public product/API                    22.4.0
Deployment authority                 22.5.4
Three-Agent state machine             22.5.5
Agent1 input semantics                22.5.8
Hash-directed execution               22.5.9
Frontend View Artifact/Manifest       22.5.9
Interface documentation               22.5.10
```

These values describe different contracts and must not be collapsed into one mutable version field.

## Canonical interface

```text
docs/V22.5.9_INTERFACE_AND_MIGRATION.md
```

Historical documents remain in the repository as migration and responsibility evidence, but they explicitly point to the canonical current interface.

## API self-description

`/api/version` and `/api/system/agent-pipeline-status` must expose:

```text
publicApiVersion
stateMachineVersion
agent1InputSchema
agent1InputSemanticVersion
hashDirectedArtifactRuntimeVersion
interfaceDocumentationVersion
executionIndex
batchManifestContract
frontendViewManifestContract
cachedOutputRebindingAllowed = false
```

# V22.5.9 — Hash-Directed Artifact Runtime

## Execution ownership

```text
exact input Artifact
→ inputContentHash validation
→ executionHash
→ finite execution claim
→ Provider call if new
→ immutable single-item output Artifact
→ acceptedOutputRef
```

One `executionHash` may publish only one accepted output Artifact.

## Exact replay

```text
same executionHash
→ validate accepted output Artifact
→ return the same output Artifact
→ zero Provider calls
```

A change to input Hash, stage, Schema, projection, Prompt, Policy, Provider, model or generation parameters creates a new execution Hash.

## Removed business cache behavior

The historical V22.5.3 behavior below is superseded:

```text
semantic Item Cache hit
→ rebind old output to current package/product identity
```

Current rule:

```text
llm_item_result_cache_v211
→ no Agent business-result ownership

artifact_execution_index_v2259
→ exact execution replay owner
```

Old business output identity rebinding is forbidden.

## Agent1 batch identity

```text
maximum items per Provider call = 8
mandatory item identity = itemExecutionId + inputContentHash
```

Only a raw Provider response that entirely omits an expected `itemExecutionId` is a true missing item. Hash mismatch, missing Hash, duplicate ID, extra ID and invalid decision output fail closed without missing-item retry.

## Frontend View identity

```text
View Head → manifestHash → module contentHash
```

An unchanged Hash is not retransferred or rerendered. A previous snapshot keeps its old Hash and old `dataVersion`; it cannot be relabelled as current.

# V22.5.8 — Agent1 Evidence and Output Semantics

```text
schema = agent_input.agent1.v3
projectionVersion = 22.5.8
```

`sourceLineageValidation` is the single source-identity owner. `crossValidation` remains metric-only. All current signals and inner trend semantics are preserved.

Decision aliases such as `observation`, `attention`, `watch`, `monitor` and `hold` normalize to `observe` only after exact V22.5.9 execution identity validation passes.

# V22.5.5 — Execution-Lock Three-Agent State Machine

Agent1 advances an `act` item only when it locks:

```text
one primary problem
one primary action
one primary owner
one primary execution target
```

Agent2 operates inside that lock. Agent3 creates a company-aware SOP inside Agent1/Agent2/authority boundaries. Task Mapping remains deterministic and adds no business steps.

# V22.5.4 — Single Deployment Authority

## Deployment ownership

```text
Repository
└── unique deployment-process authority

GitHub Actions release-<exact commit>
└── unique deployment carrier

Root Verifier
└── unique trust root

/usr/local/libexec/ai-ecommerce/deploy-bootstrap
└── unique server bootstrap

/etc/ai-ecommerce-assistant/deployment.env
└── unique non-secret server runtime contract

/opt/ai-ecommerce-assistant/shared
└── unique mutable runtime state
```

The Root-owned command `/usr/local/sbin/deploy-ai-release` does not execute deployment logic from `current`. A successful sealed release installs the repository-owned bootstrap for the next deployment.

## Stable server commands

```bash
deploy-ai-release
deploy-ai-release <40-character-main-commit>
deploy-ai-release status
deploy-ai-release preflight
```

## Storage and downtime rules

1. The active service remains online during cleanup and disk preflight.
2. Abandoned `.incoming-*` directories are removed before a new candidate is allocated.
3. The current release is protected; unreferenced historical or failed release directories are removed before backup.
4. Old deployment backups are rotated before creating the new rollback backup.
5. SQLite `PRAGMA quick_check` and backup-space calculation run before service downtime.
6. Insufficient space fails closed before `systemctl stop`.
7. A failed candidate is removed automatically and the previous release is restored.
8. The server bootstrap updates only after API, Worker, environment and Data Identity verification pass.

# V22.5.3 — Historical Agent2 Cache Identity Hotfix

Document status: **historical; business-result rebinding superseded by V22.5.9**

V22.5.3 disabled Agent2 request-level business-result replay because its cache identity could omit current package identity. It temporarily retained exact per-item semantic cache and identity rebinding.

V22.5.9 removes this remaining business-result ownership from the legacy Item Cache. Historical cache rows may remain for audit but cannot select an active Agent output.

# V22.5.2 — Main-Push-Only Release Artifact

1. Pull-request runs validate but cannot create deployable `release-*` Artifacts.
2. Formal sealing runs only on `push` to `main`.
3. ECS accepts only `release-<exact main commit>` from a completed successful main-push workflow.
4. Workflow SHA, Artifact SHA, Manifest source commit and attestation source commit must be identical.
5. Mutable Git deployment, stale Artifact fallback and pull-request merge Artifacts fail closed.

# V22.5.0 — Three-Agent Responsibility Model

```text
report Artifact
→ deterministic facts
→ operatingEvidenceGraph
→ signalRef
→ agent1InputRef
→ Agent1 judgment and action-family lock
→ capabilityRef
→ agent2DraftInputRef
→ Agent2 differentiated action draft
→ agent2DraftRef
→ agent3SopInputRef
→ Agent3 company-aware SOP
→ agent3SopRef
→ deterministic task mapping
→ task admission and lifecycle
```

Agent1 owns operating judgment and observe/act. Agent2 owns vertical-category and platform-specific action drafts. Agent3 owns executable company-aware SOPs. Task Mapping adds no business steps beyond Agent3. Observe remains a legal terminal state and fallback templates remain forbidden.

# Current release chain

```text
main push commit
→ exact checkout identity
→ Python 3.11.9 and dependency closure
→ compile/static/runtime/full-pytest evidence
→ Manifest/Test Run/Dependency hashes
→ main-push-only sealed Artifact
→ immutable server bootstrap
→ Root Verifier
→ online storage preflight while service remains active
→ validated SQLite rollback backup
→ current symlink switch
→ API/Worker/environment/Data Lineage verification
→ repository-owned bootstrap refresh
→ current + previous release retention
```

# Removed failure patterns

```text
direct mutable branch deployment
server deployment logic copied into temporary shell scripts
server wrapper depending on current deployment code
system Python patch drift
service stopped before disk-space preflight
failed release directories accumulating after rollback
stale Artifact fallback
Agent2 request cache replaying stale package identity
Agent business output identity rebinding
runtime second lossy projection of materialized Agent1 input
Hash mismatch treated as missing item
one missing item rerunning accepted batch siblings
old View payload relabelled as new dataVersion
Agent2 draft treated as final SOP
```

# MVP boundary

V22.5 does not implement Merkle trees, Ed25519 release signatures or blue/green dual instances. It keeps a lightweight single-instance sealed deployment with exact Python identity, main-push-only Artifact transport, Root verification, SQLite lineage, immutable Agent Artifacts, exact execution replay and automatic rollback.