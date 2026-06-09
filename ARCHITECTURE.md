# Payment Gateway — Architecture & System Design

> A multi-tenant payment orchestration platform where **any payment provider
> (Stripe, PayPal, Adyen, a local PSP, a crypto rail) is onboarded by POSTing
> a YAML/JSON manifest** — no code, no deploy, no engineer in the loop. The
> same minute, charges flow through the new provider and webhooks land in the
> event bus.

This document is the **system design**: capacity, components, data flow,
storage, scaling, security, observability, trade-offs. A reference manifest
([`stripe.yaml`](stripe_example.yaml)) sits next to this file as a working example.
The full implementation skeleton, ADRs, and tests live in
[`payments-gateway/`](payments-gateway/).

---

## 1. Problem Statement & Success Criteria

### Problem

We integrate with payment providers for a living. Each integration today is
weeks of engineering — an adapter class, a deploy pipeline, a webhook handler,
a secret rotation flow, observability wiring. The set of providers a merchant
wants to use is **open-ended and changes by country, by quarter, by
regulation**. Engineering throughput on integrations is the limiting factor.

### Success criteria

| #   | Criterion                                                            | Measurable                                                          |
|-----|----------------------------------------------------------------------|---------------------------------------------------------------------|
| S1  | Add a new provider via API only                                      | Time from `POST /v1/providers` to first successful charge < 1 min   |
| S2  | No code change for the **majority** of providers (REST/JSON + standard auth + standard webhook signing) | ~85% of integrations are "manifest-only"                            |
| S3  | Money correctness end-to-end                                          | Zero duplicate charges in production; verified by chaos tests       |
| S4  | Survive PSP outages                                                  | Retries with backoff + circuit breakers; tested via fault injection |
| S5  | Auditable                                                            | Append-only `transactions` ledger reconstructible at any point      |
| S6  | Scale to the target workload                                         | 5 000 RPS sustained, P99 API latency < 250 ms (excl. PSP RTT)       |
| S7  | Multi-tenant secret isolation                                        | Per-merchant, per-provider-instance secrets in Vault, never in app DB |

---

## 2. Solution — TL;DR

**Every PSP, no matter how exotic, ultimately exposes the same shape:**

```
Authenticate → Send a request → Read a response → Receive a webhook → Verify it → Map status
```

We treat that shape as **data, not code**. A `ProviderManifest` (see
[`stripe.yaml`](stripe_example.yaml)) is a declarative contract describing one PSP:
auth, endpoints, request bodies, response mapping, webhook signature scheme,
status normalization, retry policy. A single in-process **Provider Engine**
interprets *any* manifest.

Onboarding a provider becomes:

```
POST /v1/providers   (manifest + secrets)   →   201 Created
POST /v1/payments    (provider_instance_id) →   201 Created  ← real charge
```

No code, no deploy, no engineer.

> The same pattern that powers n8n, Zapier, and Kong plugins — applied to the
> payment domain, with FinTech-grade rigor on money, idempotency, state, and
> webhook security.

---

## 3. System Design

### 3.1 Capacity assumptions

These numbers drive every sizing decision below.

| Dimension                            | Target                                 |
|--------------------------------------|----------------------------------------|
| Sustained RPS (mutating)             | 5 000                                  |
| Peak RPS (bursts ≤ 60s)              | 15 000                                 |
| Payments / day                       | ~400M                                  |
| Avg PSP RTT                          | 200–800 ms (P99 8 s — we time out)     |
| Webhook RPS                          | ~5× the payment RPS (3DS, redeliveries)|
| P99 API latency (excluding PSP RTT)  | < 250 ms                               |
| P99 webhook ack latency              | < 50 ms                                |
| Storage growth (`payments` + `transactions`) | ~50 GB/day at peak             |
| Outbox lag SLO                       | P99 < 2 s, alert at 30 s               |
| Recovery point objective (RPO)       | 0 (synchronous Postgres replication)   |
| Recovery time objective (RTO)        | < 5 min for stateless tier, < 15 min for DB failover |

### 3.2 High-level architecture (C4 — context + container)

```
                   ┌─────────────────────────────────────┐
                   │            Merchant systems         │
                   └───────────────────┬─────────────────┘
                                       │ HTTPS, Bearer + HMAC-signed body
                                       ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │                         PAYMENT GATEWAY                             │
   │                                                                     │
   │   ┌────────────────────┐    ┌──────────────────────────────────┐   │
   │   │  REST API tier      │    │  Worker tier                      │   │
   │   │  (stateless,        │    │  - outbox relay                   │   │
   │   │   FastAPI replicas) │    │  - webhook processor              │   │
   │   │                     │    │  - retry / saga driver            │   │
   │   └────────┬────────────┘    └───────────────┬──────────────────┘   │
   │            │ Application layer (use cases)   │                       │
   │            │ Domain layer (aggregates)       │                       │
   │            │ Provider Engine (interpreter)   │                       │
   │            ▼                                 ▼                       │
   │   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────┐    │
   │   │ Postgres 16      │   │ Redis 7          │   │ Kafka /      │    │
   │   │  primary + read  │   │  cache, idem-    │   │ Redpanda     │    │
   │   │  replicas        │   │  potency, locks  │   │  payments.v1 │    │
   │   │  outbox, ledger  │   │  invalidation    │   │  webhooks.v1 │    │
   │   └──────────────────┘   └──────────────────┘   └──────┬───────┘    │
   │                                                         │            │
   │   ┌──────────────────┐                                  │            │
   │   │ HashiCorp Vault  │  KV v2  + transit                │            │
   │   └──────────────────┘                                  │            │
   └─────────────────┬─────────────────────────────┬─────────┼────────────┘
                     │ outbound HTTPS              │ inbound │ downstream
                     │ (Provider Engine)           │ webhooks│ consumers
                     ▼                             │         │ (ledger,
              ┌──────────────┐                     │         │ antifraud,
              │  PSPs        │─────────────────────┘         │ analytics,
              │  Stripe,     │                               │ BI)
              │  PayPal,     │                               │
              │  Adyen, …    │                               │
              └──────────────┘                               ▼
```

**Key boundaries:**

- API tier is **stateless** — replicas behind an L7 load balancer.
- Postgres is the **system of record**. Kafka is a *derived* event log fed by
  the transactional outbox, never written directly from the request path.
- Provider Engine lives **in-process** in both API and worker tiers — same
  interpreter, different triggers (REST call vs. retry tick).
- Vault is the **only** place plaintext provider secrets ever live at rest.

### 3.3 Deployment topology

```
Region A (active)                          Region B (warm standby)
─────────────────                          ──────────────────────
  LB (cloud)                                 LB (cloud)
    ↓                                          ↓
  API tier   (8–32 replicas, HPA on CPU)    API tier   (cold/2 replicas)
  Worker tier (4–16 replicas)               Worker tier (cold)
    ↓                                          ↓
  Postgres primary  ◀──── streaming repl ───  Postgres replica (sync)
  Postgres read replicas (x2)
  Redis cluster (3 shards × 2 replicas)
  Redpanda (3 brokers)                      Redpanda (3 brokers, mirrored)
  Vault HA (3 nodes, integrated storage)    Vault HA (3 nodes, perf replica)
```

**Sizing at 5k RPS** (single region):
- API: ~12 replicas × 2 vCPU / 2 GB. Headroom ≥ 2× for bursts.
- Worker: ~8 replicas. Outbox relay sharded by `id % N`.
- Postgres: db.r6g.4xlarge equivalent, dedicated NVMe, autovacuum tuned.
- Connection pool: PgBouncer transaction mode, ≤ 200 server connections.

### 3.4 Data flow — Create payment

```
Merchant                  API replica            Provider Engine        PSP
   │                          │                         │                 │
   │  POST /v1/payments       │                         │                 │
   │  + Bearer + HMAC + Idem  │                         │                 │
   ├─────────────────────────▶│                         │                 │
   │                          │ check idem cache (Redis)                  │
   │                          │ → miss → BEGIN tx                         │
   │                          │ INSERT payment (PENDING)                  │
   │                          │ INSERT transaction (PENDING)              │
   │                          │ load ProviderInstance + manifest          │
   │                          │ load secrets (Vault, scoped, ephemeral)   │
   │                          │                         │                 │
   │                          │  execute("charge", payment, secrets)      │
   │                          ├────────────────────────▶│                 │
   │                          │                         │ render templates│
   │                          │                         │ HTTP POST       │
   │                          │                         ├────────────────▶│
   │                          │                         │  (RTT 200-800ms)│
   │                          │                         │◀────────────────┤
   │                          │                         │ parse response  │
   │                          │                         │ map status      │
   │                          │◀────────────────────────┤ EngineResult    │
   │                          │ apply state transition                    │
   │                          │ UPDATE payment                            │
   │                          │ UPDATE transaction (SUCCESS/FAILED)       │
   │                          │ INSERT outbox(events)                     │
   │                          │ COMMIT tx                                 │
   │                          │ idempotency_keys ← record response        │
   │  201 Created             │                                           │
   │  { status: CAPTURED }    │                                           │
   │◀─────────────────────────┤                                           │
   │                          │ (later, async)                            │
   │                          │ Outbox relay → Kafka payments.v1          │
```

**The DB transaction stays open across the PSP RTT.** Trade-off in
`payments-gateway/docs/architecture.md` §7. Net: this is the lesser evil
vs. losing the result of a real charge to a crash between PSP call and DB
write. Tight timeouts (`limits.request_timeout_ms ≤ 10s`) and local rate
limits cap the blast radius.

### 3.5 Data flow — Webhook

```
PSP                  Webhook API           Webhook worker          DB / Kafka
 │                       │                       │                      │
 │ POST /v1/webhooks/X   │                       │                      │
 ├──────────────────────▶│                       │                      │
 │                       │ load manifest (cache) │                      │
 │                       │ fetch webhook_secret  │                      │
 │                       │ verify signature      │                      │
 │                       │   (fail → 401, log)   │                      │
 │                       │ INSERT webhook_events ON CONFLICT DO NOTHING │
 │ 200 OK                │                       │                      │
 │◀──────────────────────┤                       │                      │
 │  ≤ 50ms target        │                       │                      │
 │                       │  (worker tail)        │                      │
 │                       │                       │ pick up event        │
 │                       │                       │ apply event_mapping  │
 │                       │                       │ load payment, drive  │
 │                       │                       │ state machine        │
 │                       │                       │ outbox → Kafka       │
```

Persist-then-process is deliberate: the PSP must get a fast 200 or it
retries aggressively and eventually disables webhooks. All business logic
runs in the retriable worker.

---

## 4. The Provider Manifest

The manifest is the **single source of truth** for a provider's behavior.
The reference example for Stripe lives next to this document — see
[`stripe.yaml`](stripe_example.yaml). It shows:

- `provider` — identity + semver
- `auth` — Bearer with one named secret (`api_key`)
- `capabilities` — `charge / capture / void / refund / webhook`
- `operations.*` — for each capability: HTTP method, URL, templated headers
  & body, JSONPath response mapping, provider→domain status mapping,
  idempotency mechanism
- `webhook` — `stripe_v1` signature scheme, event-id path for dedup,
  event-type mapping into our domain event names
- `retry_policy` — exponential backoff, jitter, retry categories
- `limits` — per-call timeout, max response size, outbound RPS cap

**What's in the manifest:** *what* to send and *how to read* the answer.
**What's NOT in the manifest:** crypto code, retry orchestration, secret
storage, idempotency wiring. Those are engine concerns, written once,
applied to every manifest. See **§10** for what's in and out of scope for
"manifest-only" providers.

---

## 5. Core Invariants

### 5.1 Money

- Stored as `amount_minor BIGINT` + `currency CHAR(3)`. **No floats anywhere.**
- `Money` value object enforces same-currency arithmetic. Cross-currency =
  explicit `FxConversion` with quoted rate + timestamp. No implicit FX.
- Display formatting is presentation, never domain.

### 5.2 State machine (Payment aggregate)

```
                  ┌────────────────────────────────────────────┐
                  │                                            ▼
   ┌─────────┐    ┌───────────┐    ┌─────────┐    ┌─────────┐ ┌────────┐
   │ PENDING │───▶│ AUTHORIZED│───▶│ CAPTURED│───▶│ SETTLED │ │ FAILED │
   └────┬────┘    └────┬──────┘    └────┬────┘    └────┬────┘ └────────┘
        │              │                │              │
        │              │                ▼              ▼
        │              │          ┌─────────┐   ┌────────────┐
        │              │          │ REFUNDED│   │ CHARGEBACK │
        │              │          └─────────┘   └────────────┘
        │              ▼
        │     ┌──────────────────┐
        └────▶│ ACTION_REQUIRED  │ (3DS / redirect / OTP)
              └──────────────────┘
```

Transitions are enforced inside the aggregate. Invalid transitions raise
`IllegalStateTransition` — we never let SQL accept a status the domain
hasn't approved. ACTION_REQUIRED is a separate intent state from the
financial states, so 3DS/SCA doesn't pollute money state.

### 5.3 Idempotency — three layers, all mandatory

| Layer            | Where                          | Mechanism                                                     |
|------------------|--------------------------------|---------------------------------------------------------------|
| Client → us      | Every mutating endpoint        | `Idempotency-Key` header → `(merchant_id, key)` cached & persisted; retries replay the response, body-hash mismatch → 409 |
| Us → PSP         | Outbound charge/capture/refund | Engine generates `{payment_id}:{attempt}`; injected per manifest's `idempotency.mechanism` — engine rejects manifests for money ops without one |
| PSP → us         | Webhook ingress                | `UNIQUE (provider_instance_id, external_event_id)` — duplicate redeliveries are 200-ack and no-op |

Skipping any one has a concrete failure mode (duplicate charges, double-fire
state machine). Cost of all three is one column + one header + one index;
cost of skipping one is unbounded. Detailed analysis in
[`payments-gateway/docs/adr/0003-idempotency-three-layers.md`](docs/adr/0003-idempotency-three-layers.md).

### 5.4 Webhook security — closed enum, fail-closed

Signature schemes are an **engine-side enum** (`hmac_sha256`,
`hmac_sha512`, `stripe_v1`, `paypal_certificate`, `none`). Manifest *picks*
one; manifest cannot ship crypto code. Verification runs before any state
change. Anti-replay enforced via timestamp tolerance window (default 300 s).
`signature.scheme: none` is rejected in production manifests.

---

## 6. Persistence Model

Postgres 16. Range-partitioned by month on hot tables.

```
providers (id, code, name, manifest_jsonb, version, schema_version, is_active, created_at)
  UNIQUE (code, version)

provider_instances (id, provider_id, account_alias, secret_ref, is_active, capabilities, created_at)
  UNIQUE (provider_id, account_alias)

payments (
  id UUID, merchant_id UUID, provider_instance_id UUID,
  idempotency_key TEXT,
  amount_minor BIGINT, currency CHAR(3),
  status TEXT, provider_payment_id TEXT, refunded_minor BIGINT,
  method_token TEXT, customer_ref TEXT, metadata JSONB,
  created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
) PARTITION BY RANGE (created_at);
  UNIQUE (merchant_id, idempotency_key)
  INDEX (merchant_id, created_at DESC)
  INDEX (provider_instance_id, status)

transactions (                       -- append-only ledger
  id UUID, payment_id UUID, kind TEXT, status TEXT,
  amount_minor BIGINT, currency CHAR(3),
  provider_payment_id TEXT,
  request JSONB, response JSONB,    -- redacted, no PII
  error_code TEXT, error_message TEXT, attempt INT,
  created_at TIMESTAMPTZ
) PARTITION BY RANGE (created_at);
  INDEX (payment_id, created_at)

outbox (id BIGSERIAL, aggregate_id UUID, type TEXT, payload JSONB,
        created_at TIMESTAMPTZ, published_at TIMESTAMPTZ)
  INDEX (created_at) WHERE published_at IS NULL

webhook_events (id, provider_instance_id, external_event_id, signature_status,
                raw JSONB, headers JSONB, processed_at, error, received_at)
  UNIQUE (provider_instance_id, external_event_id)

idempotency_keys (merchant_id, key, request_hash, response_status, response_body, created_at)
  PARTITION BY RANGE (created_at)   -- 30-day TTL via partition detach
```

**Why partition.** At 400M payments/day, autovacuum and index growth on a
single table become operationally hostile. Monthly partitions give
predictable VACUUM, cheap retention (`DETACH PARTITION`), and lock-free
TTL for `idempotency_keys`.

**Why append-only `transactions`.** A `Payment` row holds the *current
projected* state. The ground truth of money flow is the transaction log.
Reconciliation, audit, dispute response all read transactions, not
payments.

**Why JSONB for the manifest.** It's a document. Schema lives in the
Pydantic models + JSON Schema validator. JSONB lets us evolve the
manifest format without table migrations.

---

## 7. Event Delivery — Transactional Outbox

Domain mutations and the events they emit go to the DB in the **same
transaction**:

```sql
BEGIN;
  UPDATE payments SET status='CAPTURED', ... WHERE id=$1;
  INSERT INTO transactions (...);
  INSERT INTO outbox (aggregate_id, type, payload, created_at) VALUES (...);
COMMIT;
```

An **outbox relay** worker tails the outbox with `FOR UPDATE SKIP LOCKED`
and publishes to Kafka with **at-least-once** semantics. Consumers are
idempotent on `event_id` (UUIDv7).

**Why not dual-write the DB and Kafka.** There is no correct ordering.
DB commits and Kafka fails → silent data loss. Kafka publishes and DB
rolls back → phantom events. Outbox makes Postgres the single source of
truth and Kafka a derived projection. Detailed alternatives (Temporal,
event sourcing) and the rationale to reject them at this scope are in
[`payments-gateway/docs/adr/0002-outbox-not-temporal.md`](docs/adr/0002-outbox-not-temporal.md).

**Operational rails.**
- `outbox_lag_seconds` is an SLO and an alert.
- `outbox_dead_letter` catches messages that fail to publish after N
  attempts. Visible, re-driveable, not silently lost.

---

## 8. Security Model

| Vector                          | Control                                                                                          |
|---------------------------------|--------------------------------------------------------------------------------------------------|
| Merchant API auth               | `Authorization: Bearer <api_key>` **plus** `X-Signature: hmac_sha256(api_secret, ts + body)` with `X-Timestamp` and 300 s skew window. Bearer alone is insufficient against URL/log leakage. |
| Provider secrets                | Vault KV v2, addressed by `secret_ref`. App fetches per-request, never logs, never caches beyond request scope. Transit engine used where we never need plaintext (e.g. HMAC compute). |
| Webhook authenticity            | Manifest-declared scheme, engine-side implementation, fail-closed. Anti-replay via timestamp tolerance + event-id dedup. |
| Manifest registration auth      | Admin scope on the merchant API key. New manifests run through schema + semantic validation + dry-run + optional healthcheck before persistence. |
| Template injection              | Jinja `ImmutableSandboxedEnvironment` with empty globals and an allow-list of filters. No attribute escape, no builtins. |
| Outbound TLS                    | CA pinning per declared PSP host. Surprise CA change → blocked, logged.                          |
| PII / PAN                       | We never touch PAN. Merchants tokenize via provider-hosted fields (Stripe.js, PayPal SDK). Transaction request/response JSON is redacted on a per-manifest allow-list before persistence. |
| Manifest cannot ship code       | Closed enums for `auth.type`, `webhook.signature.scheme`, `body.encoding`, `response.parser`, `retry_on`. Adding a primitive is an engine PR with threat-model review. |

Vault dependency is real; mitigated by HA + KV read replication + a
documented degraded mode behind a circuit breaker.

---

## 9. Observability & SLOs

**Traces.** OpenTelemetry stitched from `incoming HTTP → use case → engine
→ outbound PSP call → DB → outbox → Kafka`. Every PSP call is a span with
`{provider_code, operation, attempt, status_code}`.

**RED metrics.** Per operation and per provider:
`requests_total{provider, op, status}`,
`duration_seconds_bucket{provider, op}`,
`errors_total{provider, op, category}`.

**Business metrics.**
`payments_total{provider, status}`,
`webhook_events_total{provider, type, sig_status}`,
`outbox_lag_seconds`, `idempotency_replay_total`.

**SLOs (initial).**

| SLO                                                | Target  | Window |
|----------------------------------------------------|---------|--------|
| API availability                                   | 99.95%  | 30 d   |
| API latency P99 (ex-PSP)                           | < 250ms | 5 m    |
| Webhook ack P99                                    | < 50ms  | 5 m    |
| Outbox publish lag P99                             | < 2s    | 5 m    |
| Webhook→state-machine latency P99 (in-app)         | < 500ms | 5 m    |
| Duplicate-charge incidents                         | 0       | rolling|

---

## 10. What "any provider via manifest" really means

A direct answer to the brief: **yes, the headline pattern is "new provider
= a manifest"**, with this honest scoping.

### 10.1 Manifest-only — ~85% of real PSPs

Provider fits if it speaks:
- REST + JSON (or REST + form), and
- one of: Bearer / Basic / API-key header / API-key query / HMAC request / OAuth2 client credentials / mTLS, and
- one of: HMAC-SHA256/512 / Stripe-v1 for webhook signature, and
- statuses that map onto our state machine.

Covers Stripe, PayPal, Adyen, Checkout.com, Mollie, Square, most local
PSPs, most crypto gateways (CryptoCloud, NowPayments, Coinbase Commerce).
**Onboarding time: minutes** — `POST /v1/providers` → dry-run +
healthcheck → live.

### 10.2 New engine primitive needed — one PR, then manifest-only

Provider needs something not yet in the closed primitive set:
- New auth scheme (e.g. AWS SigV4, JWT-per-request with RS256 rotation).
- New webhook signature scheme (JWS / RSA-PSS / Worldpay-style).
- New body encoding or response parser (SOAP/xmldsig, protobuf, CSV).

**Once-per-class engineer work.** After the primitive lands, every future
provider in that class is manifest-only.

### 10.3 Native adapter — ~5% — documented escape hatch

Provider is fundamentally unfit for declarative description:
- Multi-step handshakes with conditional logic between steps (some LatAm /
  India APMs, certain PIX flows).
- gRPC / streaming protocols.
- Multi-redirect UI flows where we must hold session state across hops.
- Legacy bank processing with custom binary protocols.

Lives in `providers/native/`, implements the same port the engine
implements. PR + deploy required. The hexagonal boundary means it's a
local change, not a redesign.

### 10.4 What happens when a manifest is wrong

The system fails **closed and visible**, never silently:

- Schema-invalid → rejected at `POST /v1/providers` (HTTP 400).
- Semantically invalid (e.g. money op without idempotency, `signature: none` in production) → rejected at registration.
- Template error → caught by dry-run before persistence.
- PSP returns an unmapped status → `ProviderProtocolError` → payment parked, alert fires, no transition.
- PSP responds in an unexpected format → response parser raises → transaction marked FAILED, payment fails, outbox event emitted.

We trade a small surface of provider weirdness for the guarantee that an
unknown state never sneaks into the financial state machine.

---

## 11. Trade-offs Explicitly Accepted

| Choice                                                | Cost                                                                 | Why we accept it                                                                  |
|-------------------------------------------------------|----------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| Manifest, not user code                               | ~5% of providers need a native adapter                               | Supply-chain + crypto + audit story is dramatically better. ADR-0001              |
| Outbox + simple saga, not Temporal                    | Manual retry/state code for new flows                                | One fewer heavyweight runtime; our flows are short and bounded. ADR-0002          |
| Three layers of idempotency, all mandatory            | One extra column + one extra header + DB I/O per request             | Skipping any one has unbounded cost (duplicate charges). ADR-0003                 |
| Vault as a hard dependency                            | Extra runtime to operate                                             | The alternative (encrypted blobs in Postgres) has worse audit + rotation story. ADR-0004 |
| Closed enum of webhook signature schemes              | Adding a scheme is an engine PR                                      | Manifest authors must never implement crypto. ADR-0005                            |
| Python at the gateway tier                            | Some CPU overhead vs Go                                              | Team productivity + the workload is I/O-bound. Hot path extractable per ADR-0007  |
| DB transaction open across PSP RTT                    | Held row locks for sub-second                                        | The alternative loses crash recovery for in-flight charges                        |
| Per-month partitioning instead of sharding (phase 2)  | Operational complexity grows with shard count someday                | Buys headroom past 5k RPS without paying the sharding cost prematurely. ADR-0007 |

### Explicitly out of scope

- A merchant UI / dashboard. API-first product; UI is a separate product on top.
- PCI-DSS card handling. We never touch PAN; tokens only.
- A double-entry ledger / accounting service. We emit settlement events; downstream ledger is a separate service.

These cuts keep the platform a **gateway**, not a billing/accounting monolith.

---

## 12. What's in this repo

- **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — this document.
- **[`stripe.yaml`](stripe_example.yaml)** — a working reference manifest. Read it
  alongside §4 above.
- **[`payments-gateway/`](payments-gateway/)** — the full skeleton:
  - `docs/architecture.md` — long-form architecture (engineering audience).
  - `docs/manifest-spec.md` — full manifest contract.
  - `docs/adr/` — seven ADRs with the load-bearing "why-not" reasoning.
  - `manifests/` — Stripe, PayPal, and a fake-PSP example.
  - `src/payments/` — domain, application, providers (engine), api,
    infrastructure layers.
  - `tests/` — 39 unit tests proving the core invariants (money,
    state machine, manifest validation, template sandbox, webhook
    signature schemes).
  - `docker-compose.yml` — Postgres + Redis + Redpanda + gateway +
    fake-PSP. `make up && make seed && make demo` runs the whole flow
    offline, no external credentials.

A senior engineer should be able to read this document, skim
[`stripe.yaml`](stripe_example.yaml), open `payments-gateway/src/payments/`, and
start contributing.
