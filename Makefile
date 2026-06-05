PODMAN   ?= docker
REGISTRY ?= registry.lab.tk-sputnik.org
IMAGE    ?= kolco24
TAG      ?= latest

FULL_IMAGE = $(REGISTRY)/$(IMAGE):$(TAG)

.PHONY: build push build-push login backup restore format lint test

build:
	$(PODMAN) build -t $(FULL_IMAGE) --platform linux/amd64 .

push:
	$(PODMAN) push $(FULL_IMAGE)

build-push: build push

login:
	$(PODMAN) login $(REGISTRY)

test:
	uv run pytest

format:
	uv run ruff check --fix src
	uv run black src
	uv run isort src

lint:
	uv run ruff check src
	uv run black src --check
	uv run isort src --check-only
	uv run flake8 src

backup:
	docker compose -f docker-compose_v2.yml exec kolco24_django python manage.py backup_db

restore:
	docker compose -f docker-compose_v2.yml exec -it kolco24_django python manage.py restore_db --latest
