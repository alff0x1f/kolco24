# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Local environment

**Container runtime**: use `docker`. Start the DB:
```bash
docker compose up -d kolco24_db
```

**`.env` file**: `src/config/settings.py` loads `src/.env` via `python-dotenv`. Copy from `deploy/kolco24.env.example` and fill in secrets before running the server or tests:
```bash
cp deploy/kolco24.env.example src/.env
```
Without `.env`, most env vars will be `None` (DB password, VTB keys, etc.) and tests/server will fail.

## Commands

```bash
# Development
docker compose up -d kolco24_db   # start local DB
uv run python src/manage.py migrate
uv run python src/manage.py runserver 0:8080

# Tests
uv run pytest                    # full suite
uv run pytest --reuse-db         # faster iteration (reuse DB between runs)
uv run pytest src/website/tests.py::ClassName::test_method  # single test

# Format & lint (run before every commit)
make format                      # auto-fix: ruff --fix, black, isort
make lint                        # verify: ruff, black --check, isort --check, flake8

# Docker build & push
make build-push           # build + push to registry.lab.tk-sputnik.org
make build-push TAG=v1.2.3
make login                # auth to registry
```

## Architecture

Django 4.2 project. Source lives entirely under `src/`, with `manage.py` at `src/manage.py`.

**Apps:**

- `website` — core domain: team registration, race management, payment processing (VTB, Yandex, Sberbank, SBP), checkpoint tracking, athlete profiles. Models are split into files under `src/website/models/`.
- `api` — DRF REST API consumed by the mobile app: member tag scanning, checkpoint events, team CRUD, CSV exports.
- `donate` — donation flow built on top of `VTBPayment`.
- `demo` — static HTML mockups served at `/demo/home-multiple/`, `/demo/home-offseason/`, `/demo/home-single/` for design review. No models or auth required. Templates live in `src/templates/demo/` (common templates dir), not in the app's own `templates/` folder.
- `config` — Django project config (settings, urls, wsgi).

**Payments** integrate with four providers: VTB (OAuth), Yandex Wallet, Sberbank (phone transfer), SBP. Each has its own model (`VTBPayment`, `YandexPayment`, etc.) in `website/models/`. Credentials come from env vars — see `deploy/kolco24.env.example`.

**Email** goes through `django-mailer` (`EMAIL_BACKEND = "mailer.backend.DbBackend"`): messages are queued in the DB and sent by the `kolco24_runmailer` container running `manage.py runmailer`.

**Static files** are served by WhiteNoise from `STATIC_ROOT = src/staticfiles/` (populated by `collectstatic` at Docker build time). `STATICFILES_DIRS` points to `src/static/` (source assets).

**Settings**: `src/config/settings.py` reads all secrets from env vars via `python-dotenv`. For production, values go in `deploy/kolco24.env` (copy from `deploy/kolco24.env.example`).

## Code Style

Black 88-char limit, `isort` with Black profile. `ruff` and `flake8` share ignore rules from `setup.cfg` (`W503`, `E722`; `F401` ignored in `__init__.py`).

Tests use Django `TestCase`, live in `src/<app>/tests.py`. `DJANGO_SETTINGS_MODULE = config.settings` is set automatically by `pyproject.toml`.
