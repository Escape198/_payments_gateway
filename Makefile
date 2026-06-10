.PHONY: up down logs seed demo test lint typecheck schema-check

up:
	docker compose up -d --build

down:
	docker compose down -v

logs:
	docker compose logs -f gateway fake-psp

seed:
	python scripts/seed_fake_provider.py

demo:
	python scripts/demo_flow.py

test:
	pytest -q

lint:
	ruff check src tests

typecheck:
	mypy src

schema-check:
	python scripts/validate_manifests.py manifests/*.yaml
