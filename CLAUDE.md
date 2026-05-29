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
- `apps.race` — race detail page (`/race/<slug>/`) and the unified teams-list page. Views only; no models or migrations — all models remain in `website`. Entry points: `src/apps/race/views.py:RacePageView` and `RaceTeamsView`. Uses `label = "race_app"` in `AppConfig` to avoid Django app-registry collision with the `race` model label. `RacePageView.build_context` is also called by `website.views.views_.AddNewsPostView` via a deferred import to avoid a circular dependency. `RaceTeamsView` (template `src/templates/race/teams.html`, assets `src/static/css/teams.css` + `src/static/js/teams.js`) backs all three teams URL names (`all_teams`, `teams2`, `my_teams`, wired in `website/urls.py`); it embeds teams/categories as JSON `<script>` blocks and does search/filter/sort entirely client-side.

New feature apps that don't fit in `website` live under `src/apps/<name>/`. Each needs a unique `AppConfig` label (e.g. `label = "race_app"`).

**Template stacks**: `src/templates/website/` has two base templates. `base.html` + `src/static/css/theme.css` — Bootstrap-based, used by all pages except registration and login. `base-2.html` + `src/static/css/theme-2.css` — custom CSS (Rubik font, vanilla JS), used by `register.html` and `login.html`. New pages matching the new design should extend `base-2.html`. Page-specific CSS goes in `src/static/css/<page>.css` and is loaded via `{% block extra_head %}`. Do not define a bare `.page` class in page-specific CSS — `theme-2.css` already defines it. Use a scoped wrapper class (e.g. `.race-page`).

**Auth**: `LOGIN_URL = "login"` in `settings.py` points Django's `@login_required` and `user_passes_test` decorators to `LoginView` at `/login/`. The URL name is `login`; `passlogin` was removed. `LoginView` authenticates by email via `website.auth.EmailBackend`.

**Form fields in `base-2.html` pages**: Do not use `{{ form.field }}` — Django widgets emit `class="form-control"` (Bootstrap) which conflicts with `theme-2.css`. Write fields manually: `<input class="input{% if form.field.errors %} has-error{% endif %}" name="field_name" value="{{ form.field.value|default:'' }}">`, with errors shown via `{{ form.field.errors|join:", " }}` beneath each input.

**Team add/edit pages** (`add_team.html`, `edit_team.html`) are two standalone base-2 templates — the shared form body is intentionally duplicated (no partial); only behavior/styling is shared via `src/static/css/team-form.css` (scoped under `.team-register`) and `src/static/js/team-form.js`. The JS reads no inline template vars: both views emit a JSON config island `<script type="application/json" id="teamFormConfig">` (`currentPrice`, `paidPeople`, `mapCountPaid`, `mapPrice`, `freeMaps`, `isEdit`) and the JS computes the live доплата-aware total mirroring the backend formula `max(0, (ucount − paidPeople) × currentPrice + (maps − mapCountPaid) × mapPrice)`. The segmented team-size control reads allowed counts from each category `<option>`'s `data-counts` attribute (no hardcoded `switch`). `AddTeam` (`views_.py`) and `EditTeamView` (`team.py`) share context-building helpers (`build_category_options` / `build_team_form_context` in `views_.py`); `EditTeamView` renders `edit_team.html` and adds edit-only sections (payment history, member transfer, delete). Server-side guards (in `TeamForm.clean`) enforce `ucount ∈ [category.min_people, category.max_people]` and `map_count ≤ max(0, ucount − free_maps)` — the client controls only cap values in the browser.

**Email uniqueness**: `auth_user.email` has a case-insensitive unique index (migration `0065_unique_user_email`). Registration views wrap `User.objects.create_user` in `transaction.atomic()` and catch `IntegrityError` to surface a field error on `email`. `EmailBackend` catches `MultipleObjectsReturned` defensively. Any code that creates users must handle `IntegrityError` from this constraint.

**Payments** integrate with four providers: VTB (OAuth), Yandex Wallet, Sberbank (phone transfer), SBP. Each has its own model (`VTBPayment`, `YandexPayment`, etc.) in `website/models/`. Credentials come from env vars — see `deploy/kolco24.env.example`.

**Team pricing & sizes** (`website/models/race.py`): `RacePriceTier` (FK `race`, `related_name="price_tiers"`; `price`, `active_until` inclusive `DateField`, `order`) holds the price ladder. `Race.current_price` is the single source of truth for the charged per-person amount — it returns the earliest tier with `active_until >= today`, the last tier when all are past, or falls back to `Race.cost` when the race has no tiers. `Race.price_tier_ladder()` returns `[{"tier", "status"}]` (`past`/`active`/`future`) for display. When charging, set `cost_now = race.current_price` for BOTH the people-count multiplier AND the stored `Payment.cost_per_person` — `Team.update_team` back-calculates `paid_for = withdraw_amount / cost_per_person`, so the two must stay identical. `Team.additional_charge` is deprecated — do not add it to the formula. Allowed team sizes come from `Category.min_people`/`max_people` (defaults 2/6), not a hardcoded JS `switch`. Maps: 200 ₽ each, 2 free per team (mirrored as a constant in the view and the JS config).

**Email** goes through `django-mailer` (`EMAIL_BACKEND = "mailer.backend.DbBackend"`): messages are queued in the DB and sent by the `kolco24_runmailer` container running `manage.py runmailer`.

**Static files** are served by WhiteNoise from `STATIC_ROOT = src/staticfiles/` (populated by `collectstatic` at Docker build time). `STATICFILES_DIRS` points to `src/static/` (source assets).

**Settings**: `src/config/settings.py` reads all secrets from env vars via `python-dotenv`. For production, values go in `deploy/kolco24.env` (copy from `deploy/kolco24.env.example`).

## Code Style

Black 88-char limit, `isort` with Black profile. `ruff` and `flake8` share ignore rules from `setup.cfg` (`W503`, `E722`; `F401` ignored in `__init__.py`).

Tests use Django `TestCase`, live in `src/<app>/tests.py`. `DJANGO_SETTINGS_MODULE = config.settings` is set automatically by `pyproject.toml`.
