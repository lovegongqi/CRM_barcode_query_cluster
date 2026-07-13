CREATE TABLE app_accounts (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    permissions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE runtime_config (
    scope TEXT PRIMARY KEY,
    value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE barcode_records (
    barcode TEXT PRIMARY KEY,
    object_key TEXT NOT NULL DEFAULT '',
    object_sha256 TEXT NOT NULL DEFAULT '',
    fields_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    product_name TEXT NOT NULL DEFAULT '',
    product_code TEXT NOT NULL DEFAULT '',
    current_dealer TEXT NOT NULL DEFAULT '',
    service_dealer TEXT NOT NULL DEFAULT '',
    service_closed BOOLEAN,
    latest_service_order TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    archive_time TIMESTAMPTZ,
    current_dealer_override TEXT NOT NULL DEFAULT '',
    transfer_updated_at TIMESTAMPTZ,
    query_node_id TEXT NOT NULL DEFAULT '',
    query_slot_id TEXT NOT NULL DEFAULT '',
    query_updated_at TIMESTAMPTZ,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_barcode_records_updated
    ON barcode_records(updated_at DESC);
CREATE INDEX idx_barcode_records_filters
    ON barcode_records(archived, current_dealer, service_dealer, service_closed);

CREATE TABLE product_rules (
    prefix TEXT PRIMARY KEY,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    source_barcode TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE distributors (
    name TEXT PRIMARY KEY,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    last_used_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE crm_credentials (
    owner_key TEXT PRIMARY KEY,
    username TEXT NOT NULL DEFAULT '',
    password_ciphertext TEXT NOT NULL DEFAULT '',
    remember BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cluster_nodes (
    node_id TEXT PRIMARY KEY,
    node_name TEXT NOT NULL,
    node_role TEXT NOT NULL,
    public_url TEXT NOT NULL DEFAULT '',
    query_workers INTEGER NOT NULL CHECK (query_workers BETWEEN 1 AND 10),
    transfer_workers INTEGER NOT NULL CHECK (transfer_workers BETWEEN 1 AND 10),
    database_role TEXT NOT NULL DEFAULT '',
    replication_lag_bytes BIGINT,
    last_seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_cluster_nodes_expires
    ON cluster_nodes(expires_at);

CREATE TABLE crm_slots (
    node_id TEXT NOT NULL REFERENCES cluster_nodes(node_id) ON DELETE CASCADE,
    slot_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('query', 'transfer')),
    logged_in BOOLEAN NOT NULL DEFAULT FALSE,
    busy BOOLEAN NOT NULL DEFAULT FALSE,
    current_item_id TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    last_seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (node_id, slot_id)
);

CREATE TABLE jobs (
    id UUID PRIMARY KEY,
    type TEXT NOT NULL CHECK (
        type IN ('query', 'transfer', 'service_close', 'library_lookup')
    ),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'succeeded', 'partial', 'failed', 'cancelled')
    ),
    created_by TEXT NOT NULL DEFAULT '',
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT UNIQUE,
    total INTEGER NOT NULL DEFAULT 0 CHECK (total >= 0),
    completed INTEGER NOT NULL DEFAULT 0 CHECK (completed >= 0),
    succeeded INTEGER NOT NULL DEFAULT 0 CHECK (succeeded >= 0),
    failed INTEGER NOT NULL DEFAULT 0 CHECK (failed >= 0),
    stop_requested BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_jobs_status_created
    ON jobs(status, created_at);

CREATE TABLE job_items (
    id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    item_key TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'leased', 'running', 'succeeded', 'failed', 'cancelled', 'skipped')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_expires_at TIMESTAMPTZ,
    idempotency_key TEXT UNIQUE,
    external_ref TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_job_items_claim
    ON job_items(kind, status, lease_expires_at, created_at)
    WHERE status IN ('pending', 'failed', 'leased', 'running');

CREATE TABLE job_logs (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    page TEXT NOT NULL DEFAULT '',
    node_id TEXT NOT NULL DEFAULT '',
    slot_id TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_job_logs_job_id_id
    ON job_logs(job_id, id);

CREATE TABLE object_records (
    object_key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE migration_runs (
    id UUID PRIMARY KEY,
    source_node TEXT NOT NULL,
    status TEXT NOT NULL,
    counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    hashes_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
