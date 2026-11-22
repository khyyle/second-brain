-- Frozen pre-Django schema changes for version 1; database-adoption tests only.

CREATE TABLE manifest (
    file_path    TEXT PRIMARY KEY,
    sha256       TEXT NOT NULL,
    source_type  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    parse_lane   TEXT,
    raw_output   TEXT,
    ingested_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    content_hash TEXT,
    error        TEXT
);

CREATE INDEX idx_manifest_status ON manifest(status);

CREATE INDEX idx_manifest_hash ON manifest(sha256);

CREATE TABLE page_cache (
    page_hash    TEXT PRIMARY KEY,
    parse_lane   TEXT NOT NULL,
    raw_markdown TEXT NOT NULL,
    parsed_at    TEXT NOT NULL
);

CREATE TABLE triage (
    raw_path    TEXT PRIMARY KEY,
    decision    TEXT NOT NULL,
    confidence  REAL NOT NULL,
    reason      TEXT,
    triaged_at  TEXT NOT NULL
);

CREATE TABLE compiled (
    raw_path     TEXT PRIMARY KEY,
    compiled_at  TEXT NOT NULL
);

CREATE TABLE dismissed_duplicates (
    stem_a       TEXT NOT NULL,
    stem_b       TEXT NOT NULL,
    dismissed_at TEXT NOT NULL,
    PRIMARY KEY (stem_a, stem_b)
);

CREATE TABLE deferred_sources (
    raw_path     TEXT PRIMARY KEY,
    reason       TEXT NOT NULL,
    deferred_at  TEXT NOT NULL,
    retryable    INTEGER NOT NULL DEFAULT 1
);
