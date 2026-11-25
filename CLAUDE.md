# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Main rule

Always use ASD-STE100 (Simplified Technical English) to answer me.

## Local environment

**Container runtime**: use `docker`. A local PostgreSQL must be reachable at whatever `DB_HOST`/`DB_PORT` the `.env`
names. The repo's `docker-compose_v2.yml` is the **deploy** stack (it needs `KOLCO24_IMAGE` and `DATA_LOCATION`, which
the local env files don't define), so don't expect `docker compose up -d kolco24_db` to work here — use the Postgres the
developer already runs locally, or an ad-hoc container matching the `.env`.

**`.env` file**: `src/config/settings.py` calls `load_dotenv()`, which searches **upwards from `src/config/`** — so
`src/.env` wins if it exists, otherwise the repo-root `.env` is used. The working local setup is the **root `.env`**;
do not create `src/.env`, it silently shadows the root one (different DB credentials → a confusing "works for me"
split). A template with every variable lives in `deploy/kolco24.env.example`.

Without a `.env`, most env vars will be `None` (DB password, VTB keys, etc.) and tests/server will fail.

## Commands

```bash
# Development (local Postgres must already be running — see above)
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

**No external network resources**: the server must also run offline at the race start (LAN mode), so pages load
nothing from the internet. The Rubik font is self-hosted: `src/static/css/fonts.css` + variable woff2 files in
`src/static/fonts/rubik/` (Google Fonts v31, cyrillic/latin subsets, OFL). Don't add Google Fonts or CDN links. The
one known exception is the map tiles in `race_map.js` (OSM/OpenTopoMap).

**Vendored editor assets**: EasyMDE is pinned to 2.18.0, Cropper.js to 1.6.2. Font Awesome is pinned
to 6.5.2; keep `fontawesome.min.css`, `solid.min.css`, and both solid webfonts
from that same release. Files under `src/static/vendor/leaflet/`, `src/static/vendor/cropperjs/`,
`src/static/vendor/easymde/`, and `src/static/vendor/fontawesome/` are vendored
upstream assets and are off-limits for edits. Use the modular Font Awesome CSS;
`all.min.css` is deliberately absent because it declares six `@font-face`
families and would require editing the upstream file to ship only the solid
webfont.

The shared `src/static/js/markdown-editor.js` initializes EasyMDE on the
`data-markdown-editor` hook. Both `src/templates/race/post_form.html` and
`src/templates/website/edit_page.html` load it along with the vendored
EasyMDE/Font Awesome assets. Keep `autoDownloadFontAwesome: false` in its
configuration: this is load-bearing because EasyMDE's already-loaded check
recognizes only an href containing `//maxcdn.bootstrapcdn.com/font-awesome/`,
so it cannot see the vendored stylesheet. Removing the flag silently restores
the CDN request while the icons continue to work.

## Architecture

Django 5.2 project. Source lives entirely under `src/`, with `manage.py` at `src/manage.py`. New apps go in
`src/apps/<name>/`; each needs a unique `AppConfig` label (e.g. `label = "race_app"`).

Detailed notes live in nested `CLAUDE.md` files, loaded when you work in that directory. **Read the relevant one
before changing code there**, even from another directory:

- `src/apps/race/CLAUDE.md` — race pages, admin pages (legend, codes, map, checklists, payments, starts, finishes),
  **payments, refunds, VTB, pricing, add-ons, promo codes, people limits, seat reservation, protocol snapshot**.
- `src/apps/mobile/CLAUDE.md` — signed `/app/*` API: HMAC auth, per-person tokens, ETags/versioning, legend
  encryption, track/marks/photo/judge-scan uploads, member-tag bind, K24 v2 bracelets.
- `src/apps/accounts/CLAUDE.md` — password + passwordless auth.
- `src/apps/photo_contest/CLAUDE.md` — photo contest, private originals, voting.
- `src/website/CLAUDE.md` — home owned-teams panel, team add/edit forms, add-team double submit.
- `src/templates/CLAUDE.md` — custom error pages.

**Apps:**

- `website` — core domain: teams, races, payments, checkpoints, athletes. Models split under `src/website/models/`.
- `api` — DRF REST API, only `GET /api/ping/` and `GET /api/contributors/` (bearer token) are left.
- `donate` — donations on top of `VTBPayment`.
- `demo` — static mockups at `/demo/*`; templates in `src/templates/demo/`.
- `config` — settings, urls, wsgi.
- `apps.accounts` (`/accounts/*`), `apps.race` (`label="race_app"`, owns add-on/promo/protocol models),
  `apps.mobile` (`/app/*`), `apps.photo_contest` (`/photo/`).

## Cross-cutting invariants

- **`update_fields` discipline**: `Team`/`Athlet`/`Checkpoint`/`CheckpointTag`/`CheckpointSecret`/`Category`/`Race`/
  `Tag` have `auto_now` `updated_at` that feeds mobile ETags. Any `save(update_fields=[...])` on them **must** include
  `"updated_at"`. Only exception: the bind re-stamp of `Tag.last_seen_at` in `apps.mobile` (it must not move the ETag).
- **Never `QuerySet.update()` checkpoints/tags**: legend-crypto `post_save` signals must fire, or a locked КП leaks
  cleartext. Iterate and `save()`.
- **`nfc_uid`** on `Tag`/`CheckpointTag` is stored `.strip().upper()` by `save()`. `QuerySet.update()`/`bulk_create`
  bypass it — normalize yourself. Look up with exact match, not `__iexact`.
- **`can_edit_race`** = a `RaceAdmin` row with `role=ADMIN` only. A superuser without such a row gets 403.
- **Race visibility flags**: `Race.is_hidden` affects site lists and feeds only (`NewsPost.in_feed()`; keep
  `visible()` for the race page and direct links). `is_hidden_in_app` affects `/app/races/` only
  (`versioning.listed_races`). `show_teams` gates the site teams page and its links only (`teams_access`); `/app/` and
  results ignore it. `ask_team_*` off removes the field from `TeamForm`, so `cleaned_data` may lack it;
  assign only if present.
- **Email uniqueness**: `auth_user.email` has a case-insensitive unique index. Code that creates users must wrap it in
  `transaction.atomic()` and handle `IntegrityError`.

## Templates and front-end

**Template stacks**: `src/templates/website/` has two base templates. `base.html` + `theme.css` is the legacy
Bootstrap stack. `base-2.html` + `theme-2.css` (Rubik, vanilla JS) is the new design; new pages extend it.
Page-specific CSS goes in `src/static/css/<page>.css` via `{% block extra_head %}`. Do not define a bare `.page`
class — `theme-2.css` already has it; use a scoped wrapper (e.g. `.race-page`).

**Form fields in `base-2.html` pages**: do not use `{{ form.field }}` (Bootstrap `form-control` classes). Write fields
by hand: `<input class="input{% if form.field.errors %} has-error{% endif %}" name="field_name"
value="{{ form.field.value|default:'' }}">`, errors via `{{ form.field.errors|join:", " }}` below each input.

## Infrastructure

- **Email** goes through `django-mailer` (`mailer.backend.DbBackend`), sent by the `kolco24_runmailer` container.
  Email tests must override `EMAIL_BACKEND` to locmem.
- **Static files**: WhiteNoise from `src/staticfiles/` (`collectstatic` at build); sources in `src/static/`.
- **Settings**: secrets from env vars via `python-dotenv`; production values in `deploy/kolco24.env`.

## Code Style

Black 88-char limit, `isort` with Black profile. `ruff` and `flake8` share ignore rules from `setup.cfg` (`W503`,
`E722`; `F401` ignored in `__init__.py`).

Tests live in `src/<app>/tests.py` and use **pytest-style** functions with `@pytest.mark.django_db` and `client`/
`django_user_model` fixtures — not Django `TestCase` subclasses. `DJANGO_SETTINGS_MODULE = config.settings` is set
automatically by `pyproject.toml`.
