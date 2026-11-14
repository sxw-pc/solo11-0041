"""SQLite 谱系档案的表结构定义。"""

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS crawl_run (
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    pages INTEGER NOT NULL DEFAULT 0,
    discovered INTEGER NOT NULL DEFAULT 0,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    PRIMARY KEY (run_id, source)
);

CREATE TABLE IF NOT EXISTS dataset (
    dataset_uid TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    category TEXT,
    publisher TEXT,
    publisher_norm TEXT,
    temporal_start TEXT,
    temporal_end TEXT,
    geography TEXT,
    license TEXT,
    update_frequency TEXT,
    field_count INTEGER NOT NULL DEFAULT 0,
    resource_count INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL,
    first_run_id TEXT NOT NULL,
    last_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_instance (
    instance_id TEXT PRIMARY KEY,
    dataset_uid TEXT NOT NULL REFERENCES dataset(dataset_uid),
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_item_id TEXT,
    title TEXT,
    category TEXT,
    publisher TEXT,
    temporal_start TEXT,
    temporal_end TEXT,
    geography TEXT,
    license TEXT,
    update_frequency TEXT,
    field_count INTEGER NOT NULL DEFAULT 0,
    resource_count INTEGER NOT NULL DEFAULT 0,
    extraction_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    first_run_id TEXT NOT NULL,
    last_run_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source, source_url)
);

CREATE TABLE IF NOT EXISTS field (
    field_id TEXT PRIMARY KEY,
    dataset_uid TEXT NOT NULL REFERENCES dataset(dataset_uid),
    name TEXT NOT NULL,
    type TEXT,
    description TEXT,
    ordinal INTEGER NOT NULL,
    source_selector TEXT,
    location TEXT,
    fingerprint TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (dataset_uid, ordinal)
);

CREATE TABLE IF NOT EXISTS resource (
    resource_id TEXT PRIMARY KEY,
    dataset_uid TEXT NOT NULL REFERENCES dataset(dataset_uid),
    format TEXT,
    size_bytes INTEGER,
    size_raw TEXT,
    url TEXT,
    source_selector TEXT,
    location TEXT,
    ordinal INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (dataset_uid, ordinal)
);

CREATE TABLE IF NOT EXISTS raw_archive (
    raw_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    kind TEXT NOT NULL,
    file_path TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    byte_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    content_type TEXT,
    outcome TEXT NOT NULL DEFAULT 'ok',
    UNIQUE (run_id, url, kind)
);

CREATE TABLE IF NOT EXISTS field_transform (
    transform_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    dataset_uid TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    rule TEXT NOT NULL,
    before_value TEXT,
    after_value TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (instance_id, field_name, rule)
);

CREATE TABLE IF NOT EXISTS validation_finding (
    finding_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    dataset_uid TEXT NOT NULL,
    instance_id TEXT,
    field_name TEXT,
    severity TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (dataset_uid, instance_id, field_name, rule_id)
);

CREATE TABLE IF NOT EXISTS merge_decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    dataset_uid TEXT NOT NULL,
    member_refs TEXT NOT NULL,
    basis TEXT NOT NULL,
    score REAL NOT NULL,
    manual_override INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (dataset_uid)
);

CREATE INDEX IF NOT EXISTS idx_dataset_category ON dataset(category);
CREATE INDEX IF NOT EXISTS idx_dataset_publisher ON dataset(publisher);
CREATE INDEX IF NOT EXISTS idx_dataset_temporal ON dataset(temporal_start, temporal_end);
CREATE INDEX IF NOT EXISTS idx_finding_severity ON validation_finding(severity);
CREATE INDEX IF NOT EXISTS idx_finding_dataset ON validation_finding(dataset_uid);
CREATE INDEX IF NOT EXISTS idx_raw_url ON raw_archive(url);
"""
