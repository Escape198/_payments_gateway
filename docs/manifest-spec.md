# ProviderManifest — specification

> The manifest is the **single source of truth** for a provider's behavior.
> It is versioned, immutable once published, and validated against a JSON Schema
> at registration time and at engine start-up.

The reference JSON Schema lives at [`manifests/_schema.json`](../manifests/_schema.json).
This document is the human-readable counterpart.

## 1. Top-level shape

```yaml
provider:           # identity
auth:               # how we authenticate to the PSP
capabilities:       # which operations this provider supports
operations:         # per-capability: URL, body, response mapping
webhook:            # signature scheme + event mapping
status_mapping:     # provider status → domain status
retry_policy:       # outbound retry config
limits:             # rate limits, max body size
```

Each block is described below.

## 2. `provider`

```yaml
provider:
  code: stripe                   # immutable URL-safe identifier
  name: Stripe                   # display name
  version: 1.0.0                 # semver of THIS manifest, bumped on every change
  manifest_schema: 1             # schema version, used by the engine to know how to parse
  vendor_docs: https://stripe.com/docs/api
  contact: payments@merchant.example
```

- `code` is unique. Different accounts of the same provider are
  `ProviderInstance.account_alias`, not different `code`s.
- `version` is part of the **primary key of the persisted manifest**. Active
  payments stay bound to the manifest version they were created under, so
  upgrading a manifest is safe.

## 3. `auth`

```yaml
auth:
  type: bearer | basic | api_key_header | api_key_query | hmac_request | mtls | oauth2_cc
  secrets:           # field names the merchant must supply at registration
    - api_key
    - webhook_secret
```

Concrete shapes per `type`:

```yaml
# bearer
auth:
  type: bearer
  token_secret: api_key          # used as Authorization: Bearer {token}

# basic
auth:
  type: basic
  username_secret: client_id
  password_secret: client_secret

# api_key_header
auth:
  type: api_key_header
  header: X-Api-Key
  secret: api_key

# hmac_request — sign each outbound request body
auth:
  type: hmac_request
  algorithm: sha256
  header: X-Signature
  secret: signing_key
  payload: "{{ request.method }}\n{{ request.path }}\n{{ request.body }}\n{{ now_unix }}"

# oauth2_cc — client credentials, engine manages the token lifecycle
auth:
  type: oauth2_cc
  token_url: https://psp.example/oauth/token
  client_id_secret: client_id
  client_secret_secret: client_secret
  scope: "payments.write"
  # the engine caches the token, refreshes 60s before expiry, transparently
```

The set of `type`s is **closed** (enum). New auth types are an engine change,
not a manifest change.

## 4. `capabilities`

```yaml
capabilities: [charge, capture, void, refund, payout, webhook, healthcheck]
```

The application layer routes only to providers that declare the capability it
needs. Asking for `refund` on a `charge`-only provider returns
`409 ProviderDoesNotSupportOperation`.

## 5. `operations`

Each named operation has the same shape.

```yaml
operations:
  charge:
    method: POST                                   # GET/POST/PUT/PATCH/DELETE
    url: https://api.stripe.com/v1/payment_intents
    headers:                                       # rendered with template
      Authorization: "Bearer {{ secrets.api_key }}"
      Idempotency-Key: "{{ idempotency.outbound_key }}"
    body:
      encoding: json | form | xml | none
      fields:                                      # rendered with template
        amount:         "{{ payment.amount_minor }}"
        currency:       "{{ payment.currency | lower }}"
        payment_method: "{{ payment.method_token }}"
    response:
      parser: jsonpath | xpath
      success_when: "$.status in ['succeeded','requires_action','processing']"
      mapping:
        provider_payment_id: "$.id"
        status:              "$.status"
        next_action:         "$.next_action"
        error_code:          "$.last_payment_error.code"
        error_message:       "$.last_payment_error.message"
    status_mapping:
      succeeded:        CAPTURED
      processing:       PENDING
      requires_action:  ACTION_REQUIRED
      canceled:         FAILED
    idempotency:
      mechanism: header | body | none
      target: "Idempotency-Key"                    # header name OR jsonpath into body
      key_template: "{{ payment.id }}:{{ attempt }}"
```

### 5.1 Template context

The Jinja2 sandbox exposes a fixed object graph:

| Object         | Fields                                                                                          |
|----------------|-------------------------------------------------------------------------------------------------|
| `payment`      | `id`, `merchant_id`, `amount_minor`, `currency`, `method_token`, `customer_ref`, `metadata`     |
| `secrets`      | `<name>` for each declared `auth.secrets` entry (loaded on demand from SecretStore)             |
| `idempotency`  | `client_key`, `outbound_key`, `attempt`                                                         |
| `request`      | `method`, `path`, `body` (post-render, available only for `hmac_request` auth signing)          |
| `now_unix`     | int                                                                                              |
| `now_iso`      | ISO-8601 UTC                                                                                     |
| `env`          | `gateway_base_url` only — no host environment access                                             |

### 5.2 Template filters (allow-list)

`lower`, `upper`, `trim`, `b64`, `b64_url`, `sha256`, `sha512`, `hmac_sha256`,
`hmac_sha512`, `iso_date`, `minor_units`, `from_minor_units`, `json`, `urlencode`.

Anything outside this list raises `TemplateSecurityError`.

### 5.3 `success_when` and `status_mapping`

- `success_when` decides whether the response **counts as a non-error
  response** (i.e. the call reached the PSP and was understood). HTTP-level
  failures (timeouts, 5xx) are handled by `retry_policy` and never reach
  `success_when`.
- `status_mapping` then maps the provider's domain status onto our state
  machine. Unmapped statuses raise `ProviderProtocolError` and the
  transaction is parked for manual review — **never silently downgraded**.

### 5.4 Idempotency declaration is mandatory for money operations

For `charge`, `capture`, `refund`, `payout`, the engine **rejects the manifest
at registration** if `idempotency.mechanism == none` AND the provider does
not declare equivalent server-side semantics via `idempotency.server_enforced: true`.

## 6. `webhook`

```yaml
webhook:
  signature:
    scheme: hmac_sha256 | stripe_v1 | paypal_certificate | none
    header: Stripe-Signature       # for hmac and stripe_v1
    secret: webhook_secret         # name in SecretStore
    tolerance_seconds: 300         # anti-replay window
  event_id_path:   "$.id"          # how to dedupe
  event_type_path: "$.type"        # how to route
  event_mapping:
    payment_intent.succeeded:      payment.captured
    payment_intent.payment_failed: payment.failed
    charge.refunded:               payment.refunded
    charge.dispute.created:        payment.chargeback
  payload_paths:
    payment_id_ref: "$.data.object.metadata.gateway_payment_id"
    amount_minor:   "$.data.object.amount"
    currency:       "$.data.object.currency"
```

### 6.1 `signature.scheme = none`

Disallowed for production providers; the engine emits a warning at registration
and forces the provider into "sandbox-only" mode. Webhooks without
authentication are a footgun and FinTech-grade systems must refuse them by
default.

### 6.2 `payload_paths.payment_id_ref`

This is how we correlate an inbound webhook to one of our Payments. Providers
that don't echo a merchant reference are still supported via
`provider_payment_id` matching, but the manifest must say so explicitly.

## 7. `status_mapping` (top-level fallback)

```yaml
status_mapping:                # applies when an operation does not declare its own
  succeeded: CAPTURED
  pending:   PENDING
  failed:    FAILED
```

## 8. `retry_policy`

```yaml
retry_policy:
  max_attempts: 5
  backoff: exponential | linear | fixed
  base_ms: 200
  cap_ms: 30000
  jitter: full                   # full | none
  retry_on:                       # categories, not raw HTTP codes
    - timeout
    - network_error
    - http_5xx
    - http_429
    - circuit_open               # special case, attempts a half-open probe
  do_not_retry_on:
    - http_400
    - http_401
    - http_404
```

Retries are governed by the engine, not by individual operations, so the retry
behavior is uniform and observable.

## 9. `limits`

```yaml
limits:
  request_timeout_ms: 8000       # upper bound on a single PSP call
  max_response_bytes: 1048576    # reject pathological responses
  outbound_rps: 50               # client-side throttle, per ProviderInstance
```

These are **safety rails**, separate from the PSP's own quota. Hitting the
local cap surfaces as `429 LocalRateLimit` to the merchant and is retried by
the worker.

## 10. Manifest evolution

- A manifest version is **immutable**. Edits create a new version row.
- Live payments stay pinned to the manifest version they were created under.
- A new manifest version becomes the default for new payments by setting
  `is_active=true` on its row; the previous version stays online for in-flight
  payments and webhook replays.
- A `PATCH /v1/providers/{code}` that publishes a new version is the supported
  upgrade path; nothing in the application layer should ever mutate a manifest
  row in place.

## 11. Validation lifecycle

```
POST /v1/providers
  │
  ▼
1. JSON Schema validation (manifests/_schema.json)
2. Semantic checks:
     - declared secrets exist in the request body
     - capabilities ⊆ operations.keys()
     - money operations have an idempotency mechanism
     - webhook.scheme != none OR provider marked sandbox
3. Template smoke test: render every operation with a synthetic Payment
4. Healthcheck (optional, if declared): real call to a no-side-effect endpoint
5. Persist + hot-publish
```

Steps 1-4 are pure and fast; we reject obviously broken manifests **before**
they hit the database, not after they break a real charge.
