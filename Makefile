.PHONY: up down logs schema-check

up:
	docker compose up -d

down:
	docker compose down -v

logs:
	docker compose logs -f

schema-check:
	python scripts/validate_manifests.py manifests/*.yaml
