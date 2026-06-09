-- Postgres 16 schema for payments-gateway.
-- See docs/architecture.md §11 for the design rationale.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------- providers ----------------------------------------------------

CREATE TABLE providers (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    code            TEXT         NOT NULL,
    version         TEXT         NOT NULL,
    schema_version  INT          NOT NULL,
    manifest        JSONB        NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (code, version)
);
CREATE INDEX providers_code_active_idx
    ON providers (code) WHERE is_active;

CREATE TABLE provider_instances (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id     UUID         NOT NULL REFERENCES providers(id),
    account_alias   TEXT         NOT NULL,
    secret_ref      TEXT         NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    capabilities    TEXT[]       NOT NULL,
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (provider_id, account_alias)
);
CREATE INDEX provider_instances_active_idx
    ON provider_instances (provider_id) WHERE is_active;

-- ---------- merchants (kept minimal — outside the gateway domain) --------

CREATE TABLE merchants (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT         NOT NULL,
    api_key_hash    TEXT         NOT NULL,
    api_secret_hash TEXT         NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ---------- payments (range-partitioned by month) ------------------------

CREATE TABLE payments (
    id                   UUID         NOT NULL,
    merchant_id          UUID         NOT NULL REFERENCES merchants(id),
    provider_instance_id UUID         NOT NULL REFERENCES provider_instances(id),
    idempotency_key      TEXT         NOT NULL,
    amount_minor         BIGINT       NOT NULL CHECK (amount_minor >= 0),
    currency             CHAR(3)      NOT NULL,
    status               TEXT         NOT NULL,
    provider_payment_id  TEXT,
    refunded_minor       BIGINT       NOT NULL DEFAULT 0,
    method_token         TEXT         NOT NULL,
    customer_ref         TEXT,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE UNIQUE INDEX payments_merchant_idem_idx
    ON payments (merchant_id, idempotency_key);
CREATE INDEX payments_merchant_created_idx
    ON payments (merchant_id, created_at DESC);
CREATE INDEX payments_provider_status_idx
    ON payments (provider_instance_id, status);

-- ---------- transactions (append-only ledger; partitioned) ---------------

CREATE TABLE transactions (
    id                   UUID         NOT NULL,
    payment_id           UUID         NOT NULL,
    kind                 TEXT         NOT NULL,
    status               TEXT         NOT NULL,
    amount_minor         BIGINT       NOT NULL,
    currency             CHAR(3)      NOT NULL,
    provider_payment_id  TEXT,
    request              JSONB        NOT NULL,   -- redacted
    response             JSONB        NOT NULL,   -- redacted
    error_code           TEXT,
    error_message        TEXT,
    attempt              INT          NOT NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE INDEX transactions_payment_idx
    ON transactions (payment_id, created_at);

-- ---------- outbox -------------------------------------------------------

CREATE TABLE outbox (
    id            BIGSERIAL    PRIMARY KEY,
    aggregate_id  UUID         NOT NULL,
    type          TEXT         NOT NULL,
    payload       JSONB        NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    published_at  TIMESTAMPTZ
);
CREATE INDEX outbox_unpublished_idx
    ON outbox (created_at) WHERE published_at IS NULL;

CREATE TABLE outbox_dead_letter (
    id            BIGSERIAL    PRIMARY KEY,
    outbox_id     BIGINT       NOT NULL,
    error         TEXT         NOT NULL,
    failed_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ---------- webhook events ----------------------------------------------

CREATE TABLE webhook_events (
    id                    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_instance_id  UUID         NOT NULL REFERENCES provider_instances(id),
    external_event_id     TEXT         NOT NULL,
    signature_status      TEXT         NOT NULL,
    raw                   JSONB        NOT NULL,
    headers               JSONB        NOT NULL,
    processed_at          TIMESTAMPTZ,
    error                 TEXT,
    received_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (provider_instance_id, external_event_id)
);
CREATE INDEX webhook_events_unprocessed_idx
    ON webhook_events (received_at) WHERE processed_at IS NULL;

-- ---------- idempotency cache (partitioned, 30-day TTL) ------------------

CREATE TABLE idempotency_keys (
    merchant_id      UUID         NOT NULL,
    key              TEXT         NOT NULL,
    request_hash     TEXT         NOT NULL,
    response_status  INT          NOT NULL,
    response_body    JSONB        NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, key, created_at)
) PARTITION BY RANGE (created_at);

-- Bootstrap partitions for the current and next month. A scheduled job
-- creates future partitions and detaches older ones (>30 days).
