-- V26.9.B canonical Experience Store schema.
-- Runtime storage uses the existing ECS SQLite database. Source task/graph identity is
-- normalized once in v269b_experience_sources; the five experience domains reference it.

CREATE TABLE IF NOT EXISTS v269b_experience_sources (
    source_id TEXT PRIMARY KEY,
    source_task_id TEXT NOT NULL,
    decision_graph_hash TEXT,
    plan_graph_hash TEXT,
    operation_graph_hash TEXT,
    graph_contract_version TEXT NOT NULL,
    evaluation_version TEXT NOT NULL,
    evidence_refs TEXT NOT NULL,
    business_scope TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('runtime','seed')),
    source_version TEXT NOT NULL,
    source_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v269b_experience_items (
    experience_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    domain TEXT NOT NULL CHECK(domain IN (
        'experience_knowledge','decision_patterns','strategy_outcomes',
        'operation_patterns','evaluation_results'
    )),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN (
        'candidate','approved','enabled','disabled','superseded','seed'
    )),
    applicability TEXT NOT NULL,
    payload TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    supersedes_experience_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source_id) REFERENCES v269b_experience_sources(source_id),
    FOREIGN KEY(supersedes_experience_id) REFERENCES v269b_experience_items(experience_id)
);

CREATE INDEX IF NOT EXISTS idx_v269b_experience_items_domain_status
ON v269b_experience_items(domain,lifecycle_status,updated_at);

CREATE TABLE IF NOT EXISTS v269b_experience_knowledge (
    experience_id TEXT PRIMARY KEY,
    condition_key TEXT,
    metric TEXT,
    direction TEXT,
    category TEXT,
    knowledge_type TEXT NOT NULL,
    FOREIGN KEY(experience_id) REFERENCES v269b_experience_items(experience_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v269b_decision_patterns (
    experience_id TEXT PRIMARY KEY,
    decision_action_key TEXT,
    condition_key TEXT,
    metric TEXT,
    direction TEXT,
    category TEXT,
    decision_pattern TEXT NOT NULL,
    graph_value_score REAL,
    graph_value_metric_version TEXT,
    sample_count INTEGER NOT NULL DEFAULT 0 CHECK(sample_count >= 0),
    FOREIGN KEY(experience_id) REFERENCES v269b_experience_items(experience_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v269b_strategy_outcomes (
    experience_id TEXT PRIMARY KEY,
    decision_action_key TEXT NOT NULL,
    plan_action_key TEXT NOT NULL,
    category TEXT,
    strategy_type TEXT NOT NULL,
    baseline TEXT NOT NULL,
    expected TEXT NOT NULL,
    actual TEXT,
    prediction_error REAL,
    sample_count INTEGER NOT NULL DEFAULT 0 CHECK(sample_count >= 0),
    FOREIGN KEY(experience_id) REFERENCES v269b_experience_items(experience_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v269b_operation_patterns (
    experience_id TEXT PRIMARY KEY,
    plan_action_key TEXT NOT NULL,
    platform TEXT,
    execution_type TEXT NOT NULL,
    completion_status TEXT,
    rollback_occurred INTEGER CHECK(rollback_occurred IN (0,1) OR rollback_occurred IS NULL),
    sample_count INTEGER NOT NULL DEFAULT 0 CHECK(sample_count >= 0),
    FOREIGN KEY(experience_id) REFERENCES v269b_experience_items(experience_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v269b_evaluation_results (
    experience_id TEXT PRIMARY KEY,
    subject_experience_id TEXT,
    evaluation_id TEXT NOT NULL UNIQUE,
    metric_id TEXT NOT NULL,
    metric_version TEXT NOT NULL,
    numerator REAL,
    denominator REAL,
    value REAL,
    unit TEXT NOT NULL,
    observation_window TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0 CHECK(sample_count >= 0),
    missing_reason TEXT,
    formula_inputs TEXT NOT NULL,
    interpretation_limits TEXT NOT NULL,
    FOREIGN KEY(experience_id) REFERENCES v269b_experience_items(experience_id) ON DELETE CASCADE,
    FOREIGN KEY(subject_experience_id) REFERENCES v269b_experience_items(experience_id)
);

CREATE INDEX IF NOT EXISTS idx_v269b_decision_lookup
ON v269b_decision_patterns(condition_key,metric,direction,category,sample_count);
CREATE INDEX IF NOT EXISTS idx_v269b_strategy_lookup
ON v269b_strategy_outcomes(decision_action_key,category,strategy_type,sample_count);
CREATE INDEX IF NOT EXISTS idx_v269b_operation_lookup
ON v269b_operation_patterns(plan_action_key,platform,execution_type,sample_count);
CREATE INDEX IF NOT EXISTS idx_v269b_evaluation_metric
ON v269b_evaluation_results(metric_id,metric_version,sample_count);
