PODMAN   ?= docker
REGISTRY ?= registry.lab.tk-sputnik.org
IMAGE    ?= kolco24
TAG      ?= latest

FULL_IMAGE = $(REGISTRY)/$(IMAGE):$(TAG)

.PHONY: build push build-push login backup restore format

build:
	$(PODMAN) build -t $(FULL_IMAGE) --platform linux/amd64 .

push:
	$(PODMAN) push $(FULL_IMAGE)

build-push: build push

login:
	$(PODMAN) login $(REGISTRY)

format:
	uv run ruff check --fix src
	uv run black src
	uv run isort src

backup:
	docker compose -f docker-compose_v2.yml exec kolco24_django python manage.py backup_db

restore:
	docker compose -f docker-compose_v2.yml exec -it kolco24_django python manage.py restore_db --latest
