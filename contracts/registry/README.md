# V23 Unified Registry Authority

Current governance release: `23.0.0`  
Runtime receipt contract: `23.0.0-rc.1`  
Production mode: `selected_fail_closed`

V23.0.0 establishes the repository registry as the source authority for field, Schema,
module, interface, station, ownership, migration, and tombstone identities. The final
registry root is:

```text
sha256:d604a0842c14f04d1f3963afa1fe3a1197519d72350021c6301c2f6b153323c5
```

Documents:

- `fields.json`: canonical field identities, owners, readers, and writers;
- `schemas.json`: registered field composition for active contracts;
- `modules.json`: business module runners, reads, writes, and dependencies;
- `interfaces.json`: public interface ownership;
- `stations.json`: station-to-module bindings;
- `ownership.json`: cross-layer authority rules;
- `migrations.json`: approved Schema migrations;
- `tombstones.json`: legacy-field retirement candidates;
- `registry-manifest.json`: deterministic SHA-256 root over all registry documents.

## Agent2 authority closure

V23.0.0 removes the inaccurate Agent2 registration that pointed to a symbol not defined
by the registered V22.5.21 repair module. The registered and production hard-gated
Agent2 runner is now:

```text
src.services.agent_runtime_hard_interface_v230_service:run_agent2_microbatch_hard
```

Its input projection authority is:

```text
src.services.agent_input_transport_v230_service:ensure_agent2_input_ref
schema = agent_input.agent2.v1
```

The registry now explicitly records `agent1DecisionIR` and `matrixDispatch`, so the live
Agent2 DTO no longer depends on unregistered semantic fields.

## Sealed production projection

The source registry is projected into the already sealed runtime path:

```text
contracts/registry/registry-manifest.json
→ config/v23_registry_runtime.json
→ src/services/registry_runtime_receipt_v23_service.py
```

The production receipt gate now covers:

```text
release_governance
agent1_runtime
agent2_runtime
```

For these modules, deployment verifies exact release identity, runner file and symbol,
implementation file Hashes, field/schema lists, moduleContractHash, gray receipt, and
production parity before Uvicorn starts.

## Hash bloodline

```text
fieldDefinitionHash / schemaContractHash
→ moduleContractHash
→ registryRootHash
→ runtimeProjectionHash
→ releaseHash / manifestHash
→ gray receiptHash / receiptSetHash
→ production receiptHash / receiptSetHash
→ verificationHash / comparisonHash
→ hardGateHash
```

V23.0.0 does not rotate the pinned V22.4 Root Verifier, alter SQLite business data, rerun
Agents, or retire tombstone candidates. It closes registry declarations and deployment
proofs first; legacy-field deletion remains a separately approved migration phase.
