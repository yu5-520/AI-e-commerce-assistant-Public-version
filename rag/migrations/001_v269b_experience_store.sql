-- V26.9.B migration ledger baseline.
-- The canonical DDL lives in rag/schema/v269b_experience_store.sql and is executed
-- before this ledger statement by v269_experience_store_service.ensure_experience_store.
CREATE TABLE IF NOT EXISTS v269b_experience_migrations (
    migration_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    migration_hash TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
