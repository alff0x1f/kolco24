# Repository Guidelines

## Project Structure & Module Organization

`src/` holds the Django stack: configuration in `src/kolco24/`, site apps in `src/website/`, and REST/admin APIs in
`src/api/`. Shared templates and assets live in `src/templates/` and `src/static/`, while user uploads land in
`src/media/`. Keep sample exports and fixtures in `dumps/`; leave the mounted Postgres volume in `db/` untouched.
Reference materials and HTTP traces sit in `docs/`, `http/`, and banking artifacts under `VTB/`.

## Build, Test, and Development Commands

Create and activate a virtualenv (`python3 -m venv .venv && source .venv/bin/activate`), then install dependencies with
`pip install -r requirements.txt`. Start databases via `docker-compose -f docker-compose-dbs.yml up -d`, run migrations
with `python src/manage.py migrate`, and serve locally using `python src/manage.py runserver 0:8080`. Use `pytest` for
the test suite and `ruff check src`, `black --check src`, plus `isort --check src` before pushing.

## Coding Style & Naming Conventions

Format code with Black’s 88-character limit and four-space indentation; let `isort` manage import blocks using the
bundled Black profile. Prefer snake_case for modules and functions, PascalCase for Django models/forms, and explicit
names for management commands. `ruff` and `flake8` share the ignore list from `setup.cfg`, so resolve warnings unless
the rule is explicitly suppressed.

## Testing Guidelines

Tests live beside their apps (`src/api/tests.py`, `src/website/tests.py`) and are auto-discovered by `pytest` per
`pyproject.toml`. Name files `test_<feature>.py`, keep cases focused on single behaviors, and lean on Django `TestCase`
for database coverage. When iterating, run `pytest --reuse-db`; seed data with fixtures or factories rather than
rewriting the `db/` volume.

## Commit & Pull Request Guidelines

Write imperative commit subjects that mirror the existing history, optionally adding scopes and PR numbers (e.g.
`Add soft-delete handling for teams (#111)`). Include follow-up details or migration notes in the body and group
unrelated changes into separate commits. Pull requests should link issues, outline verification steps, and attach UI or
API evidence (screenshots, curl snippets); confirm `pytest` and the lint suite are clean before requesting review.

## Environment & Configuration

Copy `src/kolco24/settings.py.example` to `src/kolco24/settings.py`, set a unique `SECRET_KEY`, and adjust template
directories for your machine. Certificate bundles stay in `certs/` and `ru-trust-bundle.pem`; coordinate before
replacing them. Store local secrets in untracked `.env` files to keep credentials out of version control.
