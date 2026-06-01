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
- `demo` — static HTML mockups served at `/demo/home-multiple/`, `/demo/home-offseason/`, `/demo/home-single/`, `/demo/team-register/` for design review. No models or auth required. Templates live in `src/templates/demo/` (common templates dir), not in the app's own `templates/` folder.
- `config` — Django project config (settings, urls, wsgi).
- `apps.accounts` — all authentication (`AppConfig` `label="accounts"`), mounted at `/accounts/*` via `config/urls.py`. Holds the moved password auth (`LoginView`, `RegisterView`, `LogoutUserView`, `impersonate`/`stop_impersonate`, the `CustomPasswordReset*` CBVs, `EmailBackend`, the auth forms, and the private redirect/auth helpers incl. `_safe_redirect`) plus an email-first passwordless flow. URL names stay flat and unchanged (`login`, `register`, `logout`, `password_reset*`, `impersonate`, `stop_impersonate`) — only the paths moved, so `reverse()`/`{% url %}`/`LOGIN_URL` need no changes. `website/auth.py` and `website/forms.py` keep thin re-export shims (`from apps.accounts.backends import EmailBackend`; `from apps.accounts.forms import ...`) so old import sites still work. Templates live in `src/templates/accounts/` (extend `base-2.html`, manual form fields); Django's reset emails stay in `templates/registration/password_reset_email.*`. See the **Auth** note below for the passwordless flow + `EmailVerification` model.
- `apps.race` — race detail page (`/race/<slug>/`) and the unified teams-list page. Views only; no models or migrations — all models remain in `website`. Entry points: `src/apps/race/views.py:RacePageView`, `RaceTeamsView`, and `RaceEditView`. Uses `label = "race_app"` in `AppConfig` to avoid Django app-registry collision with the `race` model label. `RacePageView.build_context` is also called by `website.views.views_.AddNewsPostView` via a deferred import to avoid a circular dependency. `RaceTeamsView` (template `src/templates/race/teams.html`, assets `src/static/css/teams.css` + `src/static/js/teams.js`) backs all three teams URL names (`all_teams`, `teams2`, `my_teams`, wired in `website/urls.py`); it embeds teams/categories as JSON `<script>` blocks and does search/filter/sort entirely client-side. `RaceEditView` (template `src/templates/race/race_form.html`, assets `src/static/css/race_form.css` + `src/static/js/race_form.js`, form `src/apps/race/forms.py:RaceForm`) is one CBV for both creating and editing a race, backing the `add_race` (`races/new/`) and `edit_race` (`race/<slug>/edit/`) URL names. Create is superuser-only; edit requires `can_edit_race(user, race)` (in `src/apps/race/permissions.py` — superuser, or `RaceAdmin` with `role=ADMIN`). It edits all scalar `Race` fields and inline-manages the race's `Category` rows (incl. `min_people`, `max_people`, `people_limit`) and `RacePriceTier` rows, posted as hidden `categories_json` + `price_tiers_json` inputs and reconciled (add/update/delete, `order=index`) inside one `transaction.atomic()`. `RacePageView.build_context` exposes `can_edit_race` so `race_page.html` shows an "Редактировать" button (admins) and "+ Новая гонка" link (superusers).

New feature apps that don't fit in `website` live under `src/apps/<name>/`. Each needs a unique `AppConfig` label (e.g. `label = "race_app"`).

**Template stacks**: `src/templates/website/` has two base templates. `base.html` + `src/static/css/theme.css` — Bootstrap-based, used by all pages except registration and login. `base-2.html` + `src/static/css/theme-2.css` — custom CSS (Rubik font, vanilla JS), used by `register.html` and `login.html`. New pages matching the new design should extend `base-2.html`. Page-specific CSS goes in `src/static/css/<page>.css` and is loaded via `{% block extra_head %}`. Do not define a bare `.page` class in page-specific CSS — `theme-2.css` already defines it. Use a scoped wrapper class (e.g. `.race-page`).

**Auth**: lives in `apps.accounts` (mounted at `/accounts/*`). `LOGIN_URL = "login"` in `settings.py` points Django's `@login_required` and `user_passes_test` decorators to `LoginView` (URL name `login`, now at `/accounts/login/`); `AUTHENTICATION_BACKENDS` uses `apps.accounts.backends.EmailBackend`, which authenticates by `email__iexact`. URL names are flat and unchanged from before the move (`login`, `register`, `logout`, `password_reset*`, `impersonate`), so name-based `reverse()`/`{% url %}` callers are unaffected; only the paths changed, and old `/login/`, `/register/` now 404 (no redirects, by decision). `website/auth.py` re-exports `EmailBackend` and `website/forms.py` re-exports the auth forms as shims.

**Passwordless login** (email-first, the promoted entry point — password login is kept as a secondary option). One DB model `EmailVerification` (`apps/accounts/models.py`) backs both a 6-digit code and a magic link from a single row: `code_hash` (`make_password` of the code — the raw code is never stored), `expires_at` (`CODE_TTL = 15 min`), `attempts` (`MAX_ATTEMPTS = 5`), `consumed_at`; the magic link has no token column — the URL embeds `TimestampSigner().sign(str(pk))` and the row's `expires_at`/`consumed_at` enforce lifetime + single use. `create_for(email, purpose)` returns `(obj, raw_code)` and refuses a new code within `RESEND_COOLDOWN = 60 s` (returns `(existing, None)` — anti-bombing); `verify_code` increments `attempts` and rejects dead rows; `is_alive` gates everything. Flow (`/accounts/start|verify|link/<signed>/`, names `account_start`/`account_verify`/`magic_link`, all carry `?next=`): `StartView` issues a code and emails it via `apps/accounts/emails.py:send_login_email` (code + signed absolute magic-link URL, both `.txt`/`.html` alts, templates in `templates/accounts/email/`), responding neutrally for known/unknown emails (no account-enumeration leak); `VerifyView` (code) and `MagicLinkView` (link) both funnel into the shared `_complete_login(request, email, next_url)` — `mark_consumed` → log in existing user or inline-create (atomic, catching `IntegrityError` from the case-insensitive email unique index, random password, username deduped from the local-part) → app-local `_safe_redirect` against `request.get_host()` (so a tampered unsigned `next` falls back to `/`). The link must not rely on the session (may open in another browser). Email send failures are logged, not raised. Email tests must override `EMAIL_BACKEND` to locmem (django-mailer's `DbBackend` does not populate `mail.outbox`).

**Form fields in `base-2.html` pages**: Do not use `{{ form.field }}` — Django widgets emit `class="form-control"` (Bootstrap) which conflicts with `theme-2.css`. Write fields manually: `<input class="input{% if form.field.errors %} has-error{% endif %}" name="field_name" value="{{ form.field.value|default:'' }}">`, with errors shown via `{{ form.field.errors|join:", " }}` beneath each input.

**Team add/edit pages** (`add_team.html`, `edit_team.html`) are two standalone base-2 templates — the shared form body is intentionally duplicated (no partial); only behavior/styling is shared via `src/static/css/team-form.css` (scoped under `.team-register`) and `src/static/js/team-form.js`. The JS reads no inline template vars: both views emit a JSON config island `<script type="application/json" id="teamFormConfig">` (`currentPrice`, `paidPeople`, `mapCountPaid`, `mapPrice`, `freeMaps`, `isEdit`, `raceRemaining`, `currentCategoryId`) and the JS computes the live доплата-aware total mirroring the backend formula `max(0, (ucount − paidPeople) × currentPrice + (maps − mapCountPaid) × mapPrice)`. `raceRemaining` is `null` for unlimited races; `currentCategoryId` is `null` on add. Category `<option>` elements carry `data-remaining` (free slots, empty string = unlimited) and `data-current="1"` (team's own category) — `team-form.js` uses these to disable full categories in the dropdown and cap the segmented size control; the team's own category is never disabled. The segmented team-size control reads allowed counts from each category `<option>`'s `data-counts` attribute (no hardcoded `switch`). `AddTeam` (`views_.py`) and `EditTeamView` (`team.py`) share context-building helpers (`build_category_options` / `build_team_form_context` in `views_.py`); `EditTeamView` renders `edit_team.html` and adds edit-only sections (payment history, member transfer, delete). Server-side guards (in `TeamForm.clean`) enforce `ucount ∈ [category.min_people, category.max_people]` and `map_count ≤ max(0, ucount − free_maps)` — the client controls only cap values in the browser.

**Email uniqueness**: `auth_user.email` has a case-insensitive unique index (migration `0065_unique_user_email`). Registration views wrap `User.objects.create_user` in `transaction.atomic()` and catch `IntegrityError` to surface a field error on `email`. `EmailBackend` catches `MultipleObjectsReturned` defensively. Any code that creates users must handle `IntegrityError` from this constraint.

**Payments** integrate with four providers: VTB (OAuth), Yandex Wallet, Sberbank (phone transfer), SBP. Each has its own model (`VTBPayment`, `YandexPayment`, etc.) in `website/models/`. Credentials come from env vars — see `deploy/kolco24.env.example`.

**VTB `order_id`s** (race-fee and donations) are random ULIDs — `ORDER_<ulid>` for race fees, `SPUTNIK_<ulid>` for donations — minted by the single generator `VTBPayment.new_order_id(prefix)` (`website/models/vtb.py`). They are globally unique across environments, which matters because dev and prod share VTB credentials (and thus the VTB `order_id` namespace). Reconciliation (the polling command `check_vtb_payments`) follows an explicit FK to find the domain object — `Payment.vtb_payment` (OneToOne, `on_delete=SET_NULL`, `related_name="race_payment"`) for race fees, `DonateRequest.payment` for donations — never by parsing the id. The legacy `ORDER_<int>` int-parse survives only as a fallback in `_resolve_race_payment` for pre-deploy in-flight payments and can be dropped once those settle.

**Team pricing & sizes** (`website/models/race.py`): `RacePriceTier` (FK `race`, `related_name="price_tiers"`; `price`, `active_until` inclusive `DateField`, `order`) holds the price ladder. `Race.current_price` is the single source of truth for the charged per-person amount — it returns the earliest tier with `active_until >= today`, the last tier when all are past, or falls back to `Race.cost` when the race has no tiers. `Race.price_tier_ladder()` returns `[{"tier", "status"}]` (`past`/`active`/`future`) for display. When charging, set `cost_now = race.current_price` for BOTH the people-count multiplier AND the stored `Payment.cost_per_person` — `Team.update_team` back-calculates `paid_for = withdraw_amount / cost_per_person`, so the two must stay identical. `Team.additional_charge` is deprecated — do not add it to the formula. Allowed team sizes come from `Category.min_people`/`max_people` (defaults 2/6), not a hardcoded JS `switch`. Maps: 200 ₽ each, 2 free per team (mirrored as a constant in the view and the JS config).

**People limits** (`website/models/race.py`): two `IntegerField`s — `Race.people_limit` (cap across the whole race) and `Category.people_limit` (cap within a category), both `default=0` where `0 = unlimited` (distinct from per-team `Category.max_people`). Occupancy is counted on a **paid basis**: `Category.people_count()` / `Race.people_count()` sum `paid_people` over non-deleted teams (`TeamManager` already excludes `is_deleted`), and `remaining_people()` returns `None` when unlimited. `Category.remaining_people(exclude_team=...)` self-excludes the team being edited. The capacity **gate lives in `TeamForm.clean()`** (reused by both `AddTeam` and `EditTeamView`, which pass `team=` + `bypass_limits=request.user.is_superuser`; superusers skip the gate): the race-check blocks only *growth* (`needed = new_ucount − team.paid_people`), the category-check blocks entering a full category or growing inside a full one (a pure 2→2 move into a category with room is allowed). Auto `sold_out`: `update_team()` flips `Race.reg_status` `OPEN → SOLD_OUT` once the race cap is reached at payment confirmation — **no auto-reopening** when slots free up (Option B). **Caveat:** paid-basis occupancy means concurrent unpaid drafts can briefly exceed the limit; accepted, not fixed. Limits are configured on the race edit page (`RaceForm.people_limit` + per-row `Category.people_limit`). Note: `api`-app team creation and `TeamFormAdmin` do **not** enforce these limits.

**Email** goes through `django-mailer` (`EMAIL_BACKEND = "mailer.backend.DbBackend"`): messages are queued in the DB and sent by the `kolco24_runmailer` container running `manage.py runmailer`.

**Static files** are served by WhiteNoise from `STATIC_ROOT = src/staticfiles/` (populated by `collectstatic` at Docker build time). `STATICFILES_DIRS` points to `src/static/` (source assets).

**Settings**: `src/config/settings.py` reads all secrets from env vars via `python-dotenv`. For production, values go in `deploy/kolco24.env` (copy from `deploy/kolco24.env.example`).

## Code Style

Black 88-char limit, `isort` with Black profile. `ruff` and `flake8` share ignore rules from `setup.cfg` (`W503`, `E722`; `F401` ignored in `__init__.py`).

Tests live in `src/<app>/tests.py` and use **pytest-style** functions with `@pytest.mark.django_db` and `client`/`django_user_model` fixtures — not Django `TestCase` subclasses. `DJANGO_SETTINGS_MODULE = config.settings` is set automatically by `pyproject.toml`.
