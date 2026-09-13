-- V26.9.C Promotion Gate / feedback lifecycle audit extension.
-- Existing v269b_experience_items remains the single lifecycle authority. These tables
-- record reviewed decisions and content-addressed enabled-set Head transitions only.

CREATE TABLE IF NOT EXISTS v269c_promotion_reviews (
    review_id TEXT PRIMARY KEY,
    experience_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
    rationale TEXT NOT NULL,
    gate_hash TEXT NOT NULL,
    gate_payload TEXT NOT NULL,
    supersedes_experience_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experience_id) REFERENCES v269b_experience_items(experience_id),
    FOREIGN KEY(supersedes_experience_id) REFERENCES v269b_experience_items(experience_id)
);

CREATE INDEX IF NOT EXISTS idx_v269c_promotion_reviews_experience
ON v269c_promotion_reviews(experience_id,created_at);

CREATE TABLE IF NOT EXISTS v269c_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    experience_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    review_id TEXT,
    policy_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experience_id) REFERENCES v269b_experience_items(experience_id),
    FOREIGN KEY(review_id) REFERENCES v269c_promotion_reviews(review_id)
);

CREATE INDEX IF NOT EXISTS idx_v269c_lifecycle_events_experience
ON v269c_lifecycle_events(experience_id,created_at);

CREATE TABLE IF NOT EXISTS v269c_domain_head_events (
    head_event_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    experience_id TEXT NOT NULL,
    lifecycle_event_id TEXT NOT NULL,
    previous_head TEXT NOT NULL,
    next_head TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experience_id) REFERENCES v269b_experience_items(experience_id),
    FOREIGN KEY(lifecycle_event_id) REFERENCES v269c_lifecycle_events(event_id)
);

CREATE INDEX IF NOT EXISTS idx_v269c_domain_head_events_domain
ON v269c_domain_head_events(domain,created_at);
