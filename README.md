# payments-gateway

A **manifest-driven payment orchestration platform**. Onboard a new payment
provider (Stripe, PayPal, a local PSP, a crypto rail) by `POST`-ing a
declarative manifest — **no code, no deploy, no engineer in the loop**.

This is an **architectural reference**, not production code. The repo shows
the design end to end: domain model, money handling, state machine, the
manifest contract, the engine that interprets it, idempotency, webhook
security, event delivery, persistence.

Start with **[`ARCHITECTURE.md`](ARCHITECTURE.md)**, then read
[`stripe_example.yaml`](stripe_example.yaml) alongside
[`docs/manifest-spec.md`](docs/manifest-spec.md).

## Layout

```
payments-gateway/
├── docs/
│   ├── architecture.md          ← long-form architecture
│   ├── manifest-spec.md         ← the ProviderManifest contract
├── manifests/
│   ├── stripe.yaml              ← reference manifest
│   ├── paypal.yaml              ← OAuth2 client-credentials example
│   └── _schema.json             ← JSON Schema validating any manifest
├── src/payments/
│   ├── domain/                  ← aggregates, value objects, state machine, events
│   ├── application/             ← use cases + ports (Hexagonal)
│   ├── providers/               ← manifest schema, engine, transformers, webhook
│   ├── api/                     ← FastAPI surface
│   └── infrastructure/          ← DB schema, outbox relay, secret stores
├── docker-compose.yml           ← Postgres + Redis + Redpanda + Vault
├── pyproject.toml
└── Makefile
```

## Quick reads

- **Domain shape:** `src/payments/domain/` — `Money`, `Payment`, state
  machine in `status.py`, events in `events.py`.
- **The manifest engine:** `src/payments/providers/` — `manifest/schema.py`
  declares the contract; `engine/executor.py` interprets it.
- **The API surface:** `src/payments/api/v1/` — payments, providers,
  webhooks.
- **Persistence model:** `src/payments/infrastructure/db/schema.sql`.

## Validate a manifest locally

```bash
python scripts/validate_manifests.py manifests/stripe.yaml
```

## Bring up dependencies

```bash
make up      # Postgres + Redis + Redpanda + Vault
make down
```

The application itself is intentionally not containerized into a runnable
demo — this repo's purpose is design review, not a working SaaS.
