# Competition Qwen Live Evidence

The competition runtime has two different proof layers:

1. **Deterministic runtime proof** — registry/lineage, precise runtime package, three-report E2E, ECS candidate smoke and release hash seal. These proofs do not require an external model credential.
2. **Real model-quality proof** — the three judge-facing sanitized XLSX reports are uploaded through the official upload endpoints and then processed by real Alibaba Cloud Bailian/Qwen calls through Agent1, Agent2 and Agent3.

## Credential boundary

The live-evidence workflow never stores a model credential in the repository or in an artifact. It accepts a credential from, in priority order:

- GitHub Actions secret `DASHSCOPE_API_KEY`, `BAILIAN_API_KEY` or `QWEN_API_KEY`;
- an already-provisioned self-hosted runner environment;
- repository-declared current/legacy ECS model environment locations, read as key/value data without `source`;
- a readable matching active application process, using the same provider/key fallback semantics as the runtime gateway.

Credential values are masked immediately and only injected into the isolated competition candidate process. Published evidence contains hashes and aggregate provider/audit metadata only.

## CI semantics

On `pull_request` and normal `push`, if no credential is provisioned, the workflow publishes a sanitized `credential-preflight.json` with:

- `available=false`
- `modelQualityProof=false`
- `realBailianRunStillRequired=true`

That state is **not** treated as model proof and does not replace the real run.

A manual `workflow_dispatch` run is strict: if no credential is available it fails. Once the credential is provisioned, the same workflow must reach a verified `competition.qwen_live_evidence.v1` attestation with real Bailian provider calls for Agent1, Agent2 and Agent3 before the model-quality proof is considered complete.

## Recommended one-time provisioning

For competition evidence, prefer a repository Actions secret named `DASHSCOPE_API_KEY`. This keeps the credential independent from the application repository, candidate state and production database, and allows the live evidence workflow to run without copying production business state.
