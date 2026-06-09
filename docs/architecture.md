# Architecture

> Read this end to end. It is the contract between the design and the code.

## 1. Problem statement

We need a payment gateway that lets a business **plug in any payment provider
at runtime**, via API, without an engineer writing an adapter. The user
delivers a description of the provider; the platform immediately starts
accepting charges, refunds, captures, and webhooks for it.

The hard part is not the HTTP — it is doing this while preserving the four
non-negotiable properties of a payment system:

1. **Money is never lost or duplicated.** Idempotency end to end.
2. **State is consistent under failure.** Crashes, network partitions, retries.
3. **Webhooks are authenticated.** No signature, no state change.
4. **Everything is auditable.** Append-only history, who did what when.

A purely declarative approach naively trades these for convenience. The design
below keeps the declarative onboarding while enforcing the four properties at
the engine and infrastructure level, not in each manifest.

## 2. High-level shape

```
                          ┌─────────────────────────────────────┐
                          │            Merchant (you)           │
                          └───────────────┬─────────────────────┘
                                          │  HTTPS, API key + HMAC
                                          ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │                       payments-gateway (stateless)               │
   │ ┌────────────┐  ┌──────────────┐  ┌───────────────────────────┐ │
   │ │  REST API  │→ │ Application  │→ │   Provider Engine          │ │
   │ │ (FastAPI)  │  │  (use cases) │  │ (executes ProviderManifest)│ │
   │ └────────────┘  └──────┬───────┘  └────────────┬──────────────┘ │
   │                        │                       │                 │
   │                ┌───────▼───────┐       ┌───────▼───────┐         │
   │                │   Domain      │       │  HTTP egress  │         │
   │                │  (aggregates) │       │ (httpx + CB)  │         │
   │                └───────┬───────┘       └───────┬───────┘         │
   │                        │                       │                 │
   └────────────────────────┼───────────────────────┼─────────────────┘
                            │                       │
                ┌───────────▼─────────┐    ┌────────▼─────────┐
                │ Postgres            │    │  PSP             │
                │  payments,          │    │ (Stripe, PayPal, │
                │  transactions,      │    │  CryptoCloud, …) │
                │  outbox,            │    └────────┬─────────┘
                │  providers,         │             │  webhook
                │  webhook_events     │             ▼
                └───────────┬─────────┘    ┌──────────────────┐
                            │              │ Webhook ingress  │
                            ▼              │ (verifier per    │
                   ┌─────────────────┐     │  manifest)       │
                   │ Outbox relay    │     └────────┬─────────┘
                   │ (worker)        │              │
                   └────────┬────────┘              │
                            │                       │
                            ▼                       ▼
                ┌───────────────────────────────────────────┐
                │      Kafka  (payments.v1, webhooks.v1)    │
                └───────────────────┬───────────────────────┘
                                    ▼
                          downstream consumers
                       (ledger, antifraud, BI, …)
```

Stateless API replicas. Postgres is the system of record. Kafka is the external
event bus, fed by the **transactional outbox**, never by direct
write-through-write. Provider Engine is the only thing that talks to PSPs.

## 3. Architectural style — Hexagonal + DDD light

```
src/payments/
  domain/          ← pure Python, no I/O. Aggregates, value objects,
                     state machine, domain events.
  application/     ← use cases (commands & queries). Orchestrates the
                     domain and the ports. Knows about transactions and
                     the unit of work, nothing about HTTP or SQL.
  providers/       ← Provider Engine. Speaks "ProviderManifest" — a
                     port from the domain's perspective.
  infrastructure/  ← adapters: SQLAlchemy, Kafka, Vault, httpx, Redis.
  api/             ← FastAPI surface. Maps HTTP ↔ application commands.
  workers/         ← outbox relay, webhook reprocessor, retry workers.
```

Why hexagonal here and not a more elaborate stack (CQRS+ES throughout, full
Clean Arch with mappers everywhere)? Because **payments is a write-heavy domain
with a small, stable read model**. The added abstraction of CQRS+ES across the
whole system would inflate complexity without paying for itself; we use it
*selectively* (see §7). Hexagonal gives us testability and substitutability
where it matters: provider engine, secret store, event bus.

## 4. Domain model

### 4.1 Aggregates

- **Payment** — the merchant's intent to receive money. Holds amount,
  currency, customer reference, current status, chosen provider instance.
  Root aggregate.
- **Transaction** — an atomic interaction with a PSP (`authorize`, `capture`,
  `refund`, `void`). Append-only; a Payment has many Transactions over its
  lifetime. This is the audit trail.
- **ProviderInstance** — a specific configured instance of a provider
  manifest: "Stripe — production account A", "Stripe — test account",
  "CryptoCloud — main". Holds the secret reference, not the secret itself.
- **WebhookEvent** — raw inbound webhook + verification result + processing
  state. Append-only.

### 4.2 Value objects

- **Money** — `(amount_minor: int, currency: ISO4217)`. Never a float. All
  arithmetic is integer arithmetic on minor units. `Money` knows its currency
  and refuses cross-currency operations without an explicit FX step.
- **PaymentMethodToken** — opaque, provider-side token (Stripe `pm_...`,
  PayPal nonce, etc.). We never see raw PAN.
- **IdempotencyKey** — opaque string, per-merchant, scoped to a route.

### 4.3 State machine

```
                  ┌────────────────────────────────────────────────┐
                  │                                                ▼
   ┌─────────┐    ┌──────────┐    ┌────────────┐    ┌──────────┐  ┌────────┐
   │ PENDING │───▶│ AUTHORIZED│──▶│  CAPTURED  │──▶│ SETTLED  │  │ FAILED │
   └────┬────┘    └────┬─────┘    └─────┬──────┘    └────┬─────┘  └────────┘
        │              │                 │                │
        │              │                 ▼                ▼
        │              │           ┌──────────┐    ┌─────────────┐
        │              │           │ REFUNDED │    │ CHARGEBACK  │
        │              │           └──────────┘    └─────────────┘
        │              ▼
        │       ┌─────────────┐
        └──────▶│ACTION_REQUIRED│  (3DS / redirect / OTP)
                └─────────────┘
```

- Transitions are enforced in the aggregate, not in SQL. Invalid transitions
  raise `IllegalStateTransition`.
- A **separately-tracked "intent" state** (PENDING/ACTION_REQUIRED) is kept
  distinct from the **financial state** (AUTHORIZED/CAPTURED/SETTLED) so that
  3DS/SCA flows do not pollute the money state.
- The aggregate emits **domain events** on every transition. The application
  layer persists them to the outbox in the same DB transaction.

### 4.4 Why a separate Transaction entity (vs. just mutating Payment)

A Payment can have multiple captures (split capture), multiple refunds
(partial refunds), and provider-side retries. Squashing all that into the
Payment row destroys the audit trail and makes reconciliation impossible.
`transactions` is the **append-only ledger** of provider interactions. Money
flows are reconstructed by replaying transactions, not by trusting the
current value of `payments.status`.

## 5. The Provider Engine

This is the part that makes the platform fit the requirement.

### 5.1 Provider Manifest

Declarative, versioned document describing a provider. See
[`docs/manifest-spec.md`](manifest-spec.md) for the full schema. A condensed
view:

```yaml
provider:
  code: stripe
  name: Stripe
  version: 1.0.0
  manifest_schema: 1

auth:
  type: bearer
  secrets: [api_key, webhook_secret]    # names of fields stored in SecretStore

capabilities: [charge, capture, refund, void, webhook]

operations:
  charge:
    method: POST
    url: https://api.stripe.com/v1/payment_intents
    headers:
      Authorization: "Bearer {{ secrets.api_key }}"
      Idempotency-Key: "{{ payment.idempotency_key }}"
    body:
      encoding: form
      fields:
        amount:          "{{ payment.amount_minor }}"
        currency:        "{{ payment.currency | lower }}"
        payment_method:  "{{ payment.method_token }}"
        confirm:         "true"
    response:
      success_when: "$.status in ['succeeded','requires_action','processing']"
      mapping:
        provider_payment_id: "$.id"
        status:              "$.status"
        next_action:         "$.next_action"
    status_mapping:
      succeeded:        CAPTURED
      processing:       PENDING
      requires_action:  ACTION_REQUIRED
      canceled:         FAILED

webhook:
  signature:
    scheme: stripe_v1
    header: Stripe-Signature
    secret: webhook_secret
    tolerance_seconds: 300
  event_id_path: "$.id"
  event_type_path: "$.type"
  event_mapping:
    payment_intent.succeeded:        payment.captured
    payment_intent.payment_failed:   payment.failed
    charge.refunded:                 payment.refunded
    charge.dispute.created:          payment.chargeback

retry_policy:
  max_attempts: 5
  backoff: exponential
  base_ms: 200
  cap_ms: 30000
  retry_on: [timeout, 5xx, 429]
```

### 5.2 What the engine does with this

```
┌──────────────────────────────────────────────────────────────────┐
│                      Provider Engine                             │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │  Manifest    │  │  Template    │  │   HTTP egress           │  │
│  │  validator   │→ │  renderer    │→ │  (httpx + retry + CB)   │  │
│  │ (JSON Schema)│  │ (Jinja sand) │  │                          │  │
│  └──────────────┘  └──────────────┘  └────────┬───────────────┘  │
│                                                │                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────▼───────────────┐  │
│  │ Status       │← │  Response    │← │  Response parser        │  │
│  │ normalizer   │  │  mapper      │  │  (JSONPath / XPath)    │  │
│  └──────────────┘  └──────────────┘  └────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Webhook verifier (HMAC-SHA256 / RSA / Stripe-v1 / custom)  │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

- **Templates** use a *sandboxed* Jinja2 environment: no Python builtins, no
  attribute access on arbitrary objects, an allow-list of filters
  (`lower`, `upper`, `minor_units`, `iso_date`, `b64`, `sha256`, ...). The
  manifest cannot escape the sandbox.
- **JSONPath** (jsonpath-ng) for response mapping. For XML providers, an XPath
  variant. The choice is declared per-operation (`response.parser: jsonpath`).
- **Signature schemes** are a fixed enum (`hmac_sha256`, `hmac_sha512`,
  `stripe_v1`, `paypal_certificate`, `none`). The manifest **picks** a scheme;
  it does not get to ship code. New schemes are added in the engine — that is
  the single integration point that requires an engineer, and it is a tiny
  one.
- **Status normalization** maps provider statuses to the domain state machine.
  Any unmapped status raises a `ProviderProtocolError` and the transaction is
  parked for manual review — we never silently drop into an unknown state.

### 5.3 Why "manifest + fixed scheme enum" and not arbitrary code

We refuse to execute arbitrary user-supplied code at the engine layer. Letting
manifest authors ship arbitrary Python or JS — Lua, WASM, RestrictedPython,
whatever — buys flexibility at the cost of:

- a permanent supply-chain risk vector,
- harder reasoning about retries and side effects,
- harder static analysis and audit.

Instead, the engine ships with a **rich, closed set of primitives** (template
filters, JSONPath, signature schemes, retry strategies). 95% of providers fit.
The remaining 5% — providers with bespoke handshake flows — are implemented
as **first-class adapters in `src/payments/providers/native/`**, with a clear
escape hatch documented in ADR-0001. We trade a sliver of flexibility for a
much stronger security and audit story, which is the right trade in FinTech.

### 5.4 Lifecycle of a provider

```
POST /v1/providers   (manifest + secrets)
        │
        ▼
1. Validate against JSON Schema.            ─── reject early
2. Dry-run: render templates with a synthetic Payment.   ─── reject early
3. Health probe: optional `operations.healthcheck`.      ─── reject early
4. Persist:
     - manifest row (versioned, immutable)
     - provider_instance row (links to secret_ref)
     - secrets via SecretStore (Vault KV)
5. Hot-publish to in-memory registry (pub/sub on Redis).
6. From this moment, /v1/payments can target the new provider.
```

The whole flow is idempotent on `(provider_code, version, account_alias)`.

### 5.5 Routing — which provider handles a payment

Routing is a **separate concern** from the engine. The application layer asks
a `PaymentRouter` for a `ProviderInstance` given a payment. Initial
implementation: explicit `provider_code` on the request. Future: rule-based
routing (currency, country, BIN, amount, weighted round-robin, fallback
chains). This is intentionally pluggable so a smarter router can be dropped in
without touching the engine.

## 6. Idempotency — end to end

Three layers, and they are not optional.

1. **Client → us.** Every mutating request carries an `Idempotency-Key`
   header. We store `(merchant_id, key) → first_response_hash, status_code`
   in Redis (TTL 24h) and a durable copy in Postgres `idempotency_keys` (TTL
   30d, partitioned by month). A repeated request with the same key replays
   the recorded response — even if the body differs we return 409 with the
   original response hash. This protects against double-submit, retries,
   browser back button.

2. **Us → PSP.** Each outbound `charge` is sent with an idempotency key
   derived from `(payment_id, attempt_intent)`. If the PSP supports an
   `Idempotency-Key` header (Stripe, Adyen, PayPal v2) the manifest declares
   it. If not, the manifest declares an equivalent field. The engine refuses
   to call a `charge` operation whose manifest declares no idempotency
   mechanism — money operations without idempotency are a class of bug we
   reject at config time.

3. **PSP → us (webhooks).** Webhook events are deduplicated by
   `(provider_instance_id, event_id)` with a unique index. Re-deliveries are
   acknowledged with 200 and do nothing.

## 7. Consistency and event delivery — Outbox + saga

We use the **transactional outbox** pattern. Domain mutations and the events
they produce are written in the **same Postgres transaction**:

```
BEGIN;
  UPDATE payments SET status='AUTHORIZED', ... WHERE id=$1;
  INSERT INTO transactions (...);
  INSERT INTO outbox (aggregate_id, type, payload, created_at) VALUES (...);
COMMIT;
```

A separate `outbox_relay` worker tails `outbox` (logical replication or
polling with `FOR UPDATE SKIP LOCKED`) and publishes to Kafka with
**at-least-once** semantics. Consumers are idempotent on `event_id`.

Why not dual-write the DB and Kafka in the request path? Because there is no
correct way to atomically write to both. Either the DB commits and Kafka
fails — silent data loss — or Kafka publishes and the DB rolls back — phantom
events. Outbox makes Postgres the single source of truth, and Kafka becomes
a derived, eventually-consistent projection.

For multi-step flows that span the PSP (authorize → capture → settle), we use
a **lightweight saga** modeled as a state machine inside the Payment
aggregate, plus retry-with-backoff workers driven by the outbox. We
deliberately avoid a heavyweight workflow engine (Temporal, Camunda) — the
saga is short and bounded, and adding a separate orchestrator buys more
complexity than it pays back at this scale. ADR-0002 walks through the
trade-off.

## 8. Webhook ingress

```
PSP ──▶ POST /v1/webhooks/{provider_code}
            │
            ▼
    1. Look up ProviderInstance by code (or by routing token in path).
    2. Read manifest.webhook.signature scheme.
    3. Verify signature using SecretStore.get(provider_instance, "webhook_secret").
    4. On failure: 401, log, count metric, drop.
    5. On success:
         a. INSERT INTO webhook_events (id, provider_instance_id, raw, headers,
            received_at) ON CONFLICT DO NOTHING.
         b. Respond 200 immediately.   ← critical: never block PSP on our work
    6. A `webhook_processor` worker picks up the event, applies the manifest's
       event_mapping, drives the state machine, writes to outbox.
```

Two properties this gives us:

- The endpoint is **fast and never holds the PSP**. PSPs that don't get a
  prompt 200 will retry aggressively and eventually disable webhooks.
- All business logic runs in the worker, where it is retriable and observable.
  A poison message lands in `webhook_events_dead` with the failure reason.

## 9. Money

- Stored as `amount_minor BIGINT NOT NULL` plus `currency CHAR(3)`. No floats
  anywhere. No `NUMERIC` (we explicitly want overflow to be impossible inside
  reasonable bounds — `BIGINT` covers 9 * 10^18 minor units, which is plenty).
- `Money` value object enforces same-currency arithmetic. Cross-currency
  conversion is an explicit `FxConversion` value object with a quoted rate and
  a quote timestamp. There is no implicit FX.
- Display formatting is a **presentation concern**, not a domain concern.

## 10. Security

- **API auth.** Merchants authenticate with `Authorization: Bearer <api_key>`
  *and* a request signature (`X-Signature: hmac-sha256(api_secret, timestamp + body)`)
  with a `X-Timestamp` header and a 5-minute clock skew window. Bearer alone
  is insufficient: if a merchant logs the URL with the key, replay protection
  via the signed timestamp still limits exposure.
- **Secrets.** Per-provider-instance secrets live in Vault KV v2, addressed by
  `secret_ref`. The `SecretStore` is the only component that reads them; the
  engine sees them only at template-render time, scoped to a single request,
  never logged, never serialized. Vault's `transit` engine handles
  HMAC/signing for webhook verification where we want never to expose the
  raw material to the app process.
- **Outbound TLS pinning.** We pin the CA bundle for PSP hosts declared in the
  manifest. A surprise CA change on a PSP host is logged and blocked.
- **PII.** Never log full request/response bodies for charge operations. The
  engine has a structured logger with a redaction allow-list per manifest
  field.
- **Webhook anti-replay.** Signature schemes that include a timestamp (Stripe,
  PayPal) are validated against a `tolerance_seconds` window. Event IDs are
  deduplicated as in §6.

## 11. Persistence — Postgres schema sketch

```
providers (id, code, name, manifest_jsonb, version, schema_version, is_active, created_at)
  unique (code, version)

provider_instances (id, provider_id, account_alias, secret_ref, is_active, created_at)
  unique (provider_id, account_alias)

payments (
  id UUID PRIMARY KEY,
  merchant_id UUID,
  provider_instance_id UUID,
  idempotency_key TEXT,
  amount_minor BIGINT,
  currency CHAR(3),
  status TEXT,           -- domain state
  customer_ref TEXT,
  method_token TEXT,
  metadata JSONB,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
) PARTITION BY RANGE (created_at);
  unique (merchant_id, idempotency_key)

transactions (                              -- append-only ledger
  id UUID PRIMARY KEY,
  payment_id UUID REFERENCES payments(id),
  kind TEXT,             -- AUTHORIZE/CAPTURE/REFUND/VOID
  amount_minor BIGINT,
  status TEXT,           -- SUCCESS/FAILED/PENDING
  provider_payment_id TEXT,
  request_jsonb JSONB,   -- redacted
  response_jsonb JSONB,  -- redacted
  error_code TEXT,
  created_at TIMESTAMPTZ
) PARTITION BY RANGE (created_at);

outbox (
  id BIGSERIAL PRIMARY KEY,
  aggregate_id UUID,
  type TEXT,
  payload JSONB,
  created_at TIMESTAMPTZ,
  published_at TIMESTAMPTZ
);  -- relayed by worker; rows GC'd after T+7d

webhook_events (
  id UUID PRIMARY KEY,
  provider_instance_id UUID,
  external_event_id TEXT,
  signature_status TEXT,
  raw_jsonb JSONB,
  headers_jsonb JSONB,
  processed_at TIMESTAMPTZ,
  error TEXT,
  received_at TIMESTAMPTZ
);
  unique (provider_instance_id, external_event_id)

idempotency_keys (
  merchant_id UUID, key TEXT,
  request_hash TEXT, response_status INT, response_jsonb JSONB,
  created_at TIMESTAMPTZ,
  PRIMARY KEY (merchant_id, key)
) PARTITION BY RANGE (created_at);
```

- `payments` and `transactions` are **range-partitioned by month** for
  predictable VACUUM / index growth on high-RPS workloads (the requirement
  hint of ~5k RPS in the brief).
- Hot path queries use indexes on `(merchant_id, created_at DESC)` and
  `(provider_instance_id, status)`.
- `outbox` GC is mandatory — see ADR-0002.

## 12. Scaling story

- **API tier**: stateless, scaled behind a load balancer. Postgres connection
  pooling via PgBouncer (transaction mode).
- **Read scaling**: a Postgres read replica fronts `GET /v1/payments` and
  admin endpoints. Writes always hit primary.
- **Worker tier**: outbox relay, webhook processor, retry worker — each is a
  Kafka consumer group / Postgres partition leaser. Scales horizontally.
- **Postgres**: partitioning + index strategy (above). At ~5k RPS this is
  comfortable on a single primary with a couple of replicas; we have a
  documented plan for sharding by `merchant_id` if/when needed (ADR-0007).
- **Provider Engine**: in-process, no shared state. The manifest registry is
  cached locally and invalidated by a Redis pub/sub channel on POST/PATCH.
- **Webhooks**: stateless endpoint + worker, scaled independently. A bursty
  PSP redelivery storm hits the worker, not the API tier.

## 13. Observability

- **OpenTelemetry traces** stitched from `incoming HTTP → application use case
  → engine → outbound PSP call → DB → outbox → Kafka publish`. Each PSP call
  is a span with provider code, operation, response status, retry attempt.
- **RED metrics** per operation and per provider:
  `requests_total`, `errors_total`, `duration_seconds_bucket`.
- **Business metrics**:
  `payments_total{provider, status}`, `webhook_events_total{provider, type}`,
  `outbox_lag_seconds`.
- **Logs** are structured JSON, never include secrets or full card-context
  tokens, and carry the `trace_id` and `payment_id` correlation.

## 14. Testing strategy

- **Domain tests** — pure Python, no I/O. State machine, value objects, money
  invariants. Fast, exhaustive.
- **Engine tests** — feed a manifest + a mock `HttpClient`, assert the
  rendered request and the parsed response. The fake HTTP client returns
  canned responses keyed by URL.
- **Webhook tests** — feed raw bodies + headers, assert signature
  verification and event mapping.
- **Integration tests** — Postgres + Redis + Kafka in Docker. The **fake PSP**
  is a real HTTP service (FastAPI app) that behaves like a provider:
  predictable IDs, signed webhooks, configurable latency and error modes.
  No external network is required. The brief explicitly asks for this.
- **Contract tests** — for each shipped manifest (Stripe/PayPal), a recorded
  set of real PSP responses (sanitized) is replayed through the engine to
  guarantee the manifest still parses what the PSP actually sends.

## 15. What is intentionally *not* abstracted

- We do not abstract over Postgres. SQLAlchemy is used directly. Swapping the
  database is not a real requirement; pretending it is would buy nothing and
  cost a lot.
- We do not introduce a generic "Event" interface separate from domain events.
  The domain layer's events *are* the contract; the outbox payload is their
  JSON form.
- We do not wrap `httpx` in a homegrown HTTP abstraction. The engine has one
  HTTP egress component; substituting `httpx` for something else is a 50-line
  job, and that's fine.

These cuts are deliberate. Adding an abstraction layer because "we might want
to swap X someday" is a tax we refuse to pay until "someday" is on a roadmap.

## 16. Where to go next

- **[manifest-spec.md](manifest-spec.md)** — full ProviderManifest contract.
- **[adr/](adr/)** — the load-bearing decisions and their alternatives.
- **`src/payments/domain/`** — read the aggregate and state machine code.
- **`src/payments/providers/engine/`** — the executor; the heart of the system.
- **`manifests/*.yaml`** — what a real provider description looks like.
