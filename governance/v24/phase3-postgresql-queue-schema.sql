-- V24.9-V24.15 future PostgreSQL Source-of-Truth contract.
-- This file is validation-only in Phase3 Shadow; it is not applied to production yet.

CREATE TABLE IF NOT EXISTS v24_pipeline_item (
    item_id TEXT PRIMARY KEY,
    data_version TEXT NOT NULL,
    product_id TEXT,
    current_stage TEXT NOT NULL,
    status TEXT NOT NULL,
    generation_seq BIGINT NOT NULL,
    generation_hash TEXT NOT NULL,
    state_version BIGINT NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 50,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS v24_artifact (
    artifact_ref TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES v24_pipeline_item(item_id),
    stage TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    parent_artifact_ref TEXT,
    contract_version TEXT NOT NULL,
    generation_seq BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(item_id, stage, content_hash, contract_version)
);

CREATE TABLE IF NOT EXISTS v24_stage_job (
    job_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES v24_pipeline_item(item_id),
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    idempotency_key TEXT NOT NULL,
    input_artifact_hash TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    generation_seq BIGINT NOT NULL,
    generation_hash TEXT NOT NULL,
    fencing_token BIGINT NOT NULL,
    claim_owner TEXT,
    claim_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(stage, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_v24_stage_job_ready
    ON v24_stage_job(stage, status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_v24_stage_job_lease
    ON v24_stage_job(stage, status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_v24_pipeline_item_stage
    ON v24_pipeline_item(current_stage, status, priority);

CREATE TABLE IF NOT EXISTS v24_outbox_event (
    event_id TEXT PRIMARY KEY,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    generation_seq BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'READY',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);

-- Target claim shape for V24.14. The application owns the surrounding transaction:
-- SELECT job_id FROM v24_stage_job
-- WHERE stage = :stage AND status = 'READY'
-- ORDER BY priority, created_at
-- FOR UPDATE SKIP LOCKED
-- LIMIT :capacity;

-- Target fenced commit shape for V24.15:
-- UPDATE v24_stage_job
-- SET status='COMPLETED', updated_at=now()
-- WHERE job_id=:job_id
--   AND claim_id=:claim_id
--   AND generation_seq=:claim_generation_seq
--   AND fencing_token=:claim_fencing_token;
