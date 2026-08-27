# V25.7-V25.9 Artifact Knowledge Ingress Addendum

## Why the Phase3 migration must reach Agent input Artifacts

The production Agent chain does not read the legacy Python RAG helpers directly. Model execution consumes immutable `agent1InputRef` / `agent2InputRef` projection Artifacts through the hard semantic interface. Therefore replacing only old RAG helper functions would leave a bypass: a pre-V25 input Artifact could still be reused and carry legacy knowledge payloads.

V25 closes that bypass at the projection contract boundary.

## Formal runtime path

```text
Knowledge Composition Table
        -> registered field/hash plan
        -> Unified Knowledge Envelope
        -> agent1InputRef / agent2InputRef
        -> strict projection validation
        -> Token Runtime
        -> existing Agent1 / Agent2 provider call
```

The Token Runtime entrypoints are unchanged. V25 wraps the final prompt builders so they consume the `unifiedKnowledge` already sealed inside the validated Agent input Artifact.

## Agent1

`diagnosticRag` remains only as a compatibility parameter name for the existing prompt builder. After V25 it contains runtime guardrails only and cannot contain historical experience cards. The formal knowledge payload is `unifiedKnowledge`.

Agent1 knowledge composition is derived deterministically from the projected signal facts, including registered metric codes, organic-traffic decline flags, and whether cross-metric reasoning is required.

## Agent2

The legacy `ragContextSnapshot` remains only as audit metadata (`approvedCaseIds`, status, fingerprint and retrieval trace). `positiveExperienceCards`, `negativeCases`, and the old direct `agentInstruction` are removed before the input Artifact is sealed.

Reviewed legacy experience may still exist physically in the old provider, but it reaches Agent2 only as a compatibility supplement inside the registered V25 Unified Knowledge Envelope.

## Reuse rule

A pre-V25 Agent input Artifact does not contain:

- `knowledgeIngressVersion=25.9.0`
- `runtimeGuardrails`
- `unifiedKnowledge`
- `knowledgeEnvelopeHash`
- `knowledgeCompositionHash`

The V25 validator therefore rejects it as a reusable semantic input. The existing transport rebuild path creates a new immutable Artifact from the same authoritative source Artifact. No fallback to the old knowledge payload is permitted.

## Agent3 / SOP

V25.9 does not create a new semantic Agent3 runtime. The current deterministic SOP stage receives the Agent3 knowledge composition projection for audit and downstream task traceability while preserving the unique production runtime path.

## Completion condition

Phase3 is complete only when both of these gates pass:

1. Java release gate validates the Knowledge Composition Table, registered field hashes, consumer scopes and deterministic predicates.
2. Artifact ingress gate proves Agent1/Agent2 model-facing Artifacts require V25 knowledge envelopes and reject direct legacy knowledge payloads.

This keeps the boundary explicit: knowledge may inform an Agent, but it cannot create system facts, permissions or state transitions.
