PODMAN   ?= podman
REGISTRY ?= registry.lab.tk-sputnik.org
IMAGE    ?= kolco24
TAG      ?= latest

FULL_IMAGE = $(REGISTRY)/$(IMAGE):$(TAG)

.PHONY: build push build-push login backup restore

build:
	$(PODMAN) build -t $(FULL_IMAGE) --platform linux/amd64 .

push:
	$(PODMAN) push --format v2s2 $(FULL_IMAGE)

build-push: build push

login:
	$(PODMAN) login $(REGISTRY)

backup:
	docker compose -f docker-compose_v2.yml exec kolco24_django python manage.py backup_db

restore:
	docker compose -f docker-compose_v2.yml exec -it kolco24_django python manage.py restore_db --latest
