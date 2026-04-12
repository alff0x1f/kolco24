PODMAN   ?= podman
REGISTRY ?= registry.lab.tk-sputnik.org
IMAGE    ?= kolco24
TAG      ?= latest

FULL_IMAGE = $(REGISTRY)/$(IMAGE):$(TAG)

.PHONY: build push build-push login

build:
	$(PODMAN) build -t $(FULL_IMAGE) --platform linux/amd64 .

push:
	$(PODMAN) push --format v2s2 $(FULL_IMAGE)

build-push: build push

login:
	$(PODMAN) login $(REGISTRY)
