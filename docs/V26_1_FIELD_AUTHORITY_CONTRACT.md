# V26.1 Field Authority Contract

V26.1 is the authority/contract foundation for the V26 business-intelligence migration. It is intentionally registered through the existing runtime registry and runtime contract guard; it does not create a second runtime or mutate the sealed contract-lineage root.

## Control rule

`Header = Authority`  
`Type = Contract`  
`Reference = Lineage`  
`Natural Language = Active Surface`

The authority validator checks exact registered field headers, owner/read/write permissions, declared types/ranges and reference namespaces. It does **not** inspect, require, sanitize or rewrite business wording inside `SEMANTIC` fields.

## Numeric authority

- `FACT`: immutable observed facts; system/control-plane write only.
- `DERIVED`: deterministic system calculations; system/control-plane write only.
- `JUDGEMENT_NUMERIC`: Agent1-owned judgement weights/confidence; not operating-plan numbers.
- `PLAN`: Agent2-owned operating numbers. Agent2 is the sole model-side writer.
- Agent3 may explain/use approved plan values only through registered plan references.

No Agent may mutate Snapshot facts. Agent2 may propose plan numbers but may not rewrite Snapshot/Derived values. Agent3 may not originate or modify PLAN values.

## Stage authority

`SystemStage` and `OperationStage` are different contracts.

- `SystemStage` is owned by the Java/control plane. Models cannot create system Agents, nodes or call edges.
- `OperationStage` is the Agent3 business-operation surface. Its dynamic business expansion is permitted by the V26.1 authority contract, but the actual multi-stage Operation Plane migration belongs to V26.2.

This keeps the system call graph deterministic while allowing the business execution surface to become more expressive.

## Compatibility boundary

V26.1 registers and activates the new authority contract without rewriting the current V23/V24 Agent graph or the sealed lineage registry. Existing legacy `agent1.*`, `agent2.*` and `agent3.*` runtime contracts continue to run during this authority-foundation phase. V26.2 will migrate business outputs into the new registered `judgement.*`, `plan.*`, `operation.*` and `operation_stage.*` surfaces.

This boundary is deliberate: V26.1 changes authority semantics first; V26.2 changes Agent business semantics after the authority gate exists.

## Regression expectations

V26.1 must prove that:

- an unregistered header fails closed;
- Agent3 cannot write an Agent2 PLAN field;
- Agent2 cannot mutate FACT or DERIVED fields;
- Agent1 judgement weights remain distinct from PLAN numbers;
- valid Agent3 plan references are accepted while non-plan references fail;
- a `SEMANTIC` field is accepted regardless of business phrasing when its header/type/owner are valid;
- Agent3 cannot write `SystemStage` but can write registered `OperationStage` headers;
- existing execution identity, hash lineage, Generation fencing and release gates remain unchanged.
