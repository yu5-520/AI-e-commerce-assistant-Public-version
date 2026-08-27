# V25 Unified RAG Phase3 — Knowledge Composition and Agent Migration

## Scope

V25.6-V25.9 moves knowledge selection from Agent-owned RAG payloads into one registered Knowledge Composition Table.

```text
Agent + business state
        |
        v
Knowledge Composition Table
        |
        v
registered canonical fields + fieldHash
        |
        v
V25 field-first retrieval / legacy provider adapter
        |
        v
one unified Agent knowledge envelope
```

## V25.6 Knowledge Composition Table

`governance/v25/knowledge-composition-table-v25.json` is the only declaration of which registered fields Agent1, Agent2 and the Agent3/SOP stage may request. Conditional groups accept deterministic predicates only. A field whose `fieldHash` is stale, whose consumer does not include the Agent, or whose predicate is unknown blocks the release gate.

## V25.7 Agent1

The active V22 Agent1 runtime entry is unchanged. V25 patches only its knowledge-context reference after the single V22 runtime installer has completed. Direct historical experience cards are removed from Agent1 because the V25 field registry does not authorize Agent1 as an experience-field consumer. Missing formal diagnostic knowledge remains visible as `insufficientEvidence`.

## V25.8 Agent2

The existing reviewed `rag_experience_cards` provider remains physically in place, but Agent2 no longer receives the old direct `ragContext` key. The provider output is placed behind `unifiedKnowledge.compatibilitySupplement` and tagged with the registered positive/negative experience fields. Compatibility supplements are explicitly non-authoritative and may not create system facts. Strategy fields are selected by the composition table from the Agent1-locked action family.

## V25.9 Agent3 / SOP stage

The competition runtime currently has one deterministic SOP compiler rather than a second semantic Agent3 runtime. V25.9 therefore migrates the Agent3 knowledge responsibility into the current SOP-stage projection and does not create a parallel Agent3 runtime. Company SOP, timing, brand, historical-case and execution-experience fields are selected through the same composition table. The deterministic SOP compiler still cannot invent business steps.

## Authority boundary

- Java: release-gate authority for composition-table legality.
- Python: production knowledge-ingress projection.
- Existing physical RAG provider: retained behind the unified adapter.
- V25.3-V25.5 retrieval order: unchanged.
- Runtime entrypoints: unchanged.
- Knowledge retrieval may not create system facts.
- Missing formal evidence stays visible; it is not silently filled.
