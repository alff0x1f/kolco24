# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Local environment

**Container runtime**: use `docker`. Start the DB:

```bash
docker compose up -d kolco24_db
```

**`.env` file**: `src/config/settings.py` loads `src/.env` via `python-dotenv`. Copy from `deploy/kolco24.env.example`
and fill in secrets before running the server or tests:

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

- `website` — core domain: team registration, race management, payment processing (VTB, Yandex, Sberbank, SBP),
  checkpoint tracking, athlete profiles. Models are split into files under `src/website/models/`.
- `api` — DRF REST API consumed by the mobile app: member tag scanning, checkpoint events, team CRUD, CSV exports.
  `CheckpointSerializer` short-circuits `cost`/`description` to `0`/`""` for any КП with `is_legend_locked=True`
  (lock-only — no race-level flag) — locked КП must not leak cleartext through the scoring/scanning API; non-locked КП
  (any non-`hidden` type) serve cleartext.
- `donate` — donation flow built on top of `VTBPayment`.
- `demo` — static HTML mockups served at `/demo/home-multiple/`, `/demo/home-offseason/`, `/demo/home-single/`,
  `/demo/team-register/` for design review. No models or auth required. Templates live in `src/templates/demo/` (common
  templates dir), not in the app's own `templates/` folder.
- `config` — Django project config (settings, urls, wsgi).
- `apps.accounts` — all authentication (`AppConfig` `label="accounts"`), mounted at `/accounts/*` via `config/urls.py`.
  Holds the moved password auth (`LoginView`, `RegisterView`, `LogoutUserView`, `impersonate`/`stop_impersonate`, the
  `CustomPasswordReset*` CBVs, `EmailBackend`, the auth forms, and the private redirect/auth helpers incl.
  `_safe_redirect`) plus an email-first passwordless flow. URL names stay flat and unchanged (`login`, `register`,
  `logout`, `password_reset*`, `impersonate`, `stop_impersonate`) — only the paths moved, so `reverse()`/`{% url %}`/
  `LOGIN_URL` need no changes. `website/auth.py` and `website/forms.py` keep thin re-export shims (
  `from apps.accounts.backends import EmailBackend`; `from apps.accounts.forms import ...`) so old import sites still
  work. Templates live in `src/templates/accounts/` (extend `base-2.html`, manual form fields); Django's reset emails
  stay in `templates/registration/password_reset_email.*`. See the **Auth** note below for the passwordless flow +
  `EmailVerification` model.
- `apps.race` — race detail page (`/race/<slug>/`) and the unified teams-list page. **Owns the add-on models +
  migrations** (`src/apps/race/models.py`, `migrations/` — its first; `0001_initial` declares an explicit
  `dependencies = [("website", "0072_payment_vtb_payment")]` because the FKs cross into `website`): `RaceExtra`/
  `TeamExtra`/`PaymentExtra` (see the **Team add-ons** note below). All other models still remain in `website`. Entry
  points: `src/apps/race/views.py:RacePageView`, `RaceTeamsView`, and `RaceEditView`. Uses `label = "race_app"` in
  `AppConfig` to avoid Django app-registry collision with the `race` model label. `RacePageView.build_context` is also
  called by `website.views.views_.AddNewsPostView` via a deferred import to avoid a circular dependency.
  `RaceTeamsView` (template `src/templates/race/teams.html`, assets `src/static/css/teams.css` +
  `src/static/js/teams.js`) backs all three teams URL names (`all_teams`, `teams2`, `my_teams`, wired in
  `website/urls.py`); it embeds teams/categories as JSON `<script>` blocks and does search/filter/sort entirely
  client-side. `RaceEditView` (template `src/templates/race/race_form.html`, assets `src/static/css/race_form.css` +
  `src/static/js/race_form.js`, form `src/apps/race/forms.py:RaceForm`) is one CBV for both creating and editing a race,
  backing the `add_race` (`races/new/`) and `edit_race` (`race/<slug>/edit/`) URL names. Create is superuser-only; edit
  requires `can_edit_race(user, race)` (in `src/apps/race/permissions.py` — superuser, or `RaceAdmin` with
  `role=ADMIN`). It edits all scalar `Race` fields and inline-manages the race's `Category` rows (incl. `min_people`,
  `max_people`, `people_limit`), `RacePriceTier` rows, and `RaceExtra` («Доп-услуги») rows, posted as hidden
  `categories_json` + `price_tiers_json` + `extras_json` inputs and reconciled (add/update/delete, `order=index`) inside
  one `transaction.atomic()`. Add-on reconciliation (`_reconcile_extras` + `_validate_extra_rows`) uses a **softer**
  delete policy than categories: a row still referenced by any `TeamExtra` is force-deactivated (`is_active=False`)
  rather than deleted (and `PROTECT` on the FK is the backstop); `code` is validated `^[a-z_]+$`, unique-within-race,
  editable on create but read-only once saved. `RacePageView.build_context` exposes `can_edit_race` so `race_page.html`
  shows an "Редактировать" button (admins) and "+ Новая гонка" link (superusers).
  `RaceLegendEditView` (template `src/templates/race/legend_form.html`, assets `src/static/css/legend_form.css` +
  `src/static/js/legend_form.js`) is a bulk-edit spreadsheet page for a race's checkpoints, backing the `edit_legend`
  (`race/<slug>/legend/edit/`) URL name. Gated on `can_edit_race`. **Must save via `instance.save()`** — never
  `QuerySet.update()` — so the legend-crypto `post_save` signals fire (lock toggle creates/destroys `CheckpointSecret`
  and rebuilds bundles; a bulk update would bypass signals and leak cleartext). Deletion guard: a КП absent from the
  submitted payload is only deleted if it has no `CheckpointTag` rows — those represent physically provisioned NFC
  chips that would be silently destroyed; a tag-bearing КП raises `ValueError` and rolls back the whole save.
  `RaceLegendCodesView` (template `src/templates/race/legend_codes.html`, assets `src/static/css/legend_codes.css` +
  `src/static/js/legend_codes.js`) is a read-only table of per-tag NFC codes for field-crew provisioning, backing the
  `legend_codes` (`race/<slug>/legend/codes/`) URL name. It is the web twin of `manage.py export_legend_codes --race
  <id>` — same `CheckpointTag` queryset ordered by `checkpoint__number` then `id`, same `—` placeholder for tags without
  a `code` yet, same `nfc_uid / КП number / code(hex)` columns. The JS "Скопировать CSV" button builds RFC-4180 CSV
  from the rendered table and writes it to the clipboard. Gated on `can_edit_race`.
- `apps.mobile` — **signed mobile-app endpoints** for iOS/Android (`label = "mobile"`, mounted at `/app/*`
  via `config/urls.py`). The **read** path is self-contained: touches neither `/api/` nor `donate`/`website` — but the
  **track-upload and marks-upload writes** (`TrackPoint`/`Mark`+`MarkPresent`, see the **Track upload** and **Marks
  upload** invariants below) are the documented exceptions, holding
  cross-app FKs into `website.Team`/`website.Race` (like `apps.race`). The reads are accountless — the
  app authenticates **itself** (per-build HMAC); a thin **write layer** adds a per-person bearer token on top (login +
  legend-tag create — see the **Per-person write layer** invariant below). Two further POSTs —
  `POST /app/race/<id>/track/` (`track`) and `POST /app/race/<id>/marks/` (`marks`) — are **build-HMAC-only** like the
  reads (NOT part of the per-person write layer); see the **Track upload** / **Marks upload** invariants below. Full
  design (background-sync model,
  two-server lease/handoff, secret-rotation runbook, 403 reason codes) lives in `src/apps/mobile/README.md`; exact
  response fields are pinned by the serializers and the field-set tests in `tests.py`. Invariants to preserve:
    - **Auth**: per-build shared secret selected by `X-App-Key-Id` from `MOBILE_APP_KEYS` (JSON key-id → secret map env,
      parsed once at startup, **fail-closed** — a missing/malformed map means every `/app/*` request 403s; a
      non-empty-but-bad value also logs a settings-import `WARNING`). HMAC-SHA256 over the canonical
      `method + "\n" + full_path + "\n" + ts + "\n" + sha256_hex(body)` (`signing.py`); replay window ±
      `MOBILE_APP_TS_WINDOW` = 300 s — a hardcoded constant in `settings.py`, not an env var; no nonce. **Body signing
      works for POST too** (the canonical already folds `sha256_hex(body)`): the `RawPostDataException` caveat does not
      bite because Django buffers `request.body`, so `SignedAppPermission` reading it before DRF parses `request.data` is
      safe (pinned by `test_signed_permission_reads_body_before_drf_parse`).
    - **Neutral failures, no writes in the permission**: every verification failure returns the same
      `403 {"detail": "Forbidden"}` — no hint which check failed. `SignedAppPermission` does no DB writes; the log +
      aggregated `AppAuthFailure` row happen in `AppAPIView.permission_denied()`, and `AppInstall` stats are recorded
      best-effort in `initial()` after permissions pass (a stats-write failure never breaks a response; a denied request
      never writes `AppInstall`).
    - **Per-person write layer** (login + legend-tag create, on top of the build HMAC): a **layered permission stack** —
      build HMAC → person identity → per-race authorization. `MobileToken` (`models.py`) is a revocable **opaque** bearer
      (not JWT — instant revoke via `revoked_at` is the priority for an admin credential): only `sha256(raw)` is stored
      in `token_hash` (unique-indexed), the raw `secrets.token_urlsafe(32)` is returned **once** at login (a high-entropy
      token needs no salt/slow hash); `is_active = revoked_at is None and expires_at > now`; TTL `MOBILE_TOKEN_TTL =
      timedelta(days=30)` in `settings.py`. **Not** ETag-fingerprinted → no `updated_at`. Helpers in `tokens.py`
      (`generate_token`/`hash_token`/`resolve_token` — `resolve_token` best-effort stamps `last_used_at`). Permissions
      (`permissions.py`): `IsMobileUser` resolves `Authorization: Bearer <token>` → `request.mobile_user` +
      `request.mobile_token` (identity only, authorizes nothing; stack **after** `SignedAppPermission`);
      `CanEditRaceLegend` reads `view.kwargs["race_id"]`, loads `Race` (missing → 404), returns
      `apps.race.permissions.can_edit_race` (same superuser-or-`RaceAdmin(role=ADMIN)` as the web `RaceLegendEditView`);
      it reads `request.mobile_user` defensively (`getattr(..., None)` → `False`/403, not 500) and must stack **after**
      `IsMobileUser`. **Actionable-vs-neutral error split**: the anonymous build layer stays a neutral
      `403 {"detail":"Forbidden"}` (no brute-force hint), but the authenticated layers are actionable — an invalid/
      expired/revoked token raises `MobileTokenInvalid` (an `APIException` with `status_code = 401`; needed because with
      `authentication_classes = []` a raised `NotAuthenticated` renders as 403), and a non-admin user gets an actionable
      403. Endpoints (all POST, `views.py`): `POST /app/login/` (gated by `SignedAppPermission` **only** — even the
      password endpoint is build-only; authenticates email+password via `EmailBackend`, mints a `MobileToken`, returns
      `{token, expires_at}`; failures are enumeration-safe — wrong password and unknown email both 401 with the **same**
      message, malformed body 400); `POST /app/logout/` (`SignedAppPermission + IsMobileUser`; flips `revoked_at` on the
      **presented** token only, other tokens of the user stay valid); `POST /app/race/<id>/tags/`
      (`SignedAppPermission + IsMobileUser + CanEditRaceLegend`; the online-only NFC-chip→КП provisioning endpoint). Tag
      create takes body `{checkpoint_id, nfc_uid}` where **`checkpoint_id` is the checkpoint `id`** (`number` is not unique
      per race, hence id) — the single wire vocabulary across `/app/`: a checkpoint's stable id is always `checkpoint_id`,
      its display number always `number`, no key overloaded. Resolves a **non-`hidden`** КП by id within the race (404),
      normalizes `nfc_uid` (`.strip().upper()`),
      then: same `nfc_uid` on the **same** КП → idempotent 200; on a **different** КП → 409 (no auto-rebind); else
      `CheckpointTag(checkpoint, nfc_uid, created_by=request.mobile_user)` created in `transaction.atomic()` via
      `instance.save()` (**fires the legend-crypto signals** — server stays the single crypto source; catches
      `IntegrityError` from the **global** `CheckpointTag.nfc_uid` unique constraint (migration `website/0089`, which
      replaced the old `unique_together("point","nfc_uid")` — a UID is now globally unique; `0089` aborts with a
      `RuntimeError` if any duplicate UIDs exist, requiring manual resolution before the migration can proceed) → re-query: same КП ⇒ idempotent 200, different КП ⇒ 409).
      The legacy superuser `api` write `CheckpointTagCreateView` (`api/views/tag.py`) likewise translates that
      `IntegrityError` to a 409. The mobile `TagCreateSerializer.nfc_uid` caps `max_length=255` (mirrors the column) so
      an oversized UID is a 400, not a DB-insert 500. Response
      `{bid, checkpoint_id: cp.id, number: cp.number, nfc_uid, code: code.hex()}` (201, or 200 idempotent; both ids under
      honest keys) — the same key set the legacy `api` `CheckpointTagCreateView` now returns; a row with `bid==""`/`code is None`
      (created bypassing signals) is repaired via `build_bundle` before responding (no `None.hex()` 500) — the repair
      runs under `select_for_update()` and re-checks the row inside the lock so two concurrent repairs can't mint
      different codes (the first response could otherwise be written to the chip before the second overwrites it).
      Token resolution (`tokens.py:resolve_token`) also rejects a token whose owner is `is_active=False`, so disabling a
      compromised admin cuts off provisioning instantly without waiting out the 30-day TTL.
      `CheckpointTag.created_by` (`FK(AUTH_USER_MODEL, null, SET_NULL, related_name="provisioned_tags")`, migration
      `website/0088`) is **not** in any version fingerprint, but creating a tag still moves `versions.legend`/the legend
      ETag via `CheckpointTag.updated_at`. **Throttling** (first use here): IP-scoped
      `ClientIPScopedRateThrottle` (`throttling.py`) — a thin `ScopedRateThrottle` subclass that overrides only
      `get_ident` to key on the un-spoofable `_client_ip` (last `X-Forwarded-For` entry, the one nginx appends), so a
      client can't rotate a forged XFF prefix for a fresh bucket (DRF's stock `get_ident` trusts the *first* entry
      unless `NUM_PROXIES` is set). It still reads no `request.data`/`request.body`, so the body-read ordering hazard
      stays closed. `throttle_scope` is set **per view** (`mobile-login` 5/min on login, `mobile-write` 60/min on tag
      create); rates in `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` (no global `DEFAULT_THROTTLE_CLASSES`); needs the new
      `CACHES` (`LocMemCache`) added for it. **Test isolation**: the
      `autouse` `_clear_throttle_cache` fixture in `src/apps/mobile/tests.py` calls `cache.clear()` before/after every
      test to prevent throttle counts leaking across tests (all test requests share the same client IP); any new test
      module that exercises throttled mobile endpoints must replicate this fixture.
    - **Endpoints** (reads all GET; writes are the three POSTs in the **Per-person write layer** invariant below; see
      `urls.py`): `/app/races/` (published races), `/app/race/<id>/teams/` (teams **plus the
      embedded category catalogue** — deliberately no separate categories endpoint; inactive categories included so
      every `category2` id resolves), `/app/race/<id>/legend/` (checkpoints **plus a per-tag `tags` array** — `bid → checkpoint_id`
      identity for **every** tag (open + locked), plus `iv`/`ct` for the offline legend unlock on locked КП only — see
      the **Legend encryption** invariant below; the legend is **always served** for a published race (no race-level
      visibility gate), with `type="hidden"` КП excluded), `/app/race/<id>/member_tags/` (the participant-bracelet pool —
      `{number, nfc_uid}` per `Tag` for offline scan resolution; the chip pool is **global** today (one physical set
      reused across races) so `race_id` is accepted but **not used for filtering** — a reserved hook for a future
      per-race chip set; the served set is a **data-anchored** window `last_seen_at >= MAX(last_seen_at) − 30d` (not
      wall-clock `now()`, so an idle race is stable; a never-scanned pool returns everything); the api read
      `GET /api/member_tag/` stays unchanged — this only *adds* the read to the mobile app), `/app/race/<id>/sync/`
      (pure version manifest — no data, no
      ETag; lease/handoff stubbed: `data_source` from
      `MOBILE_DATA_SOURCE` env, default `"cloud"`, `lease_expires_at` always `null`).
    - **Conditional GET**: races/teams/legend/member_tags set a strong `ETag` on **every** exit path; a matching
      `If-None-Match`
      short-circuits to `304` with no serialization. `versioning.py` is the **single source of truth** for both the
      ETags and the `sync` manifest: each fingerprint is a `blake2b(digest_size=8)` hex over `MAX(updated_at)|COUNT`
      aggregates computed over the exact queryset the view serves (`None` aggregates render as `"None"`, so an empty
      race is stable) — **except `member_tags_version()`**, which folds in the sorted active ID list (see **Member
      tags** below), and **except `legend_version()`**, which additionally prefixes a `_LEGEND_SCHEMA_VERSION` integer
      so a response-shape change (fields added or removed) forces a cache bust at deploy time even when no DB row
      changed; bump `_LEGEND_SCHEMA_VERSION` whenever the legend response shape changes (`LegendCheckpointSerializer` or the
      per-tag `TagSerializer` fields) (current value: 3, bumped to 2 when `color` was added, to 3 when the `tags[]` identity
      key was renamed `point` → `checkpoint_id`). Deliberately **no `versions.categories`** — category edits must move `versions.teams`;
      `races_version()` is global and deliberately absent from the per-race `sync` manifest (the races list is the app's
      entry point, probed via its own conditional GET).
    - **Member tags** (`active_member_tags()` + `member_tags_version()` in `versioning.py`; `MemberTagSerializer` in
      `serializers.py` — distinct name from the legend per-`CheckpointTag` `TagSerializer`): the fingerprint hashes the
      **actual served field values** — `blake2b` over the canonical JSON encoding of `[(id, number, nfc_uid), …]`
      ordered by `id` over the same data-anchored window `active_member_tags()` serves (single-source contract;
      JSON encoding avoids any ambiguity from special characters in `nfc_uid`). Hashing field values (not just
      `MAX(updated_at)|COUNT|IDs`) ensures a same-COUNT identity swap (one chip ages past the 30-day floor while a
      touch brings another in) and a same-COUNT field edit under concurrent provisioning (where one write's timestamp
      falls below the existing `MAX(updated_at)`) both change the fingerprint and avoid a stale 304.
      `Tag` gained an `auto_now` `updated_at` field **so the version moves on provisioning edits (add / renumber /
      remove) but not on scans**: `MemberTagTouchView` (`api/views/tag.py`) keeps
      `save(update_fields=["last_seen_at"])` and **intentionally omits `"updated_at"`** — a deliberate carve-out from
      the `update_fields` discipline, so a bracelet tap can't churn the mobile ETag and trigger mid-race re-downloads.
      The ETag can still shift day-scale when scan activity advances `MAX(last_seen_at)` enough to age the oldest chips
      past the 30-day floor (membership change, not per-scan churn). `member_tags_version()` is **global** (no
      `race_id`, like `races_version`) but — unlike `races_version` — **is** included in the per-race `sync` manifest,
      because it's served at a per-race URL and the app needs one sync poll to learn what to refetch for the race it's
      syncing. The **legend** fingerprint additionally folds in two more
      `MAX(updated_at)|COUNT` aggregates over the same hidden-excluded checkpoints — `CheckpointSecret` (re-seal / enc
      appear/disappear on a lock toggle) and `CheckpointTag` (code/unlocks/bundle/check_method) — so the legend ETag /
      `versions.legend` move on any lock toggle, re-seal, or tag edit. The legend fingerprint is `legend_version(race_id)`
      alone — there is no race-level visibility flag folded in. The legend is **build-independent**: the
      ciphertext/bundles are precomputed and stored in the DB (not keyed by the per-build secret), so `legend_version`
      takes **no** `key_id` and two builds share the legend ETag (an `X-App-Key-Id` rotation no longer
      re-fetches the legend).
    - **Legend encryption** (`src/apps/mobile/crypto.py`, `legend_crypto.py`, `signals.py`; models in
      `src/website/models/checkpoint.py`): a **locked** checkpoint (`Checkpoint.is_legend_locked=True`) hides its
      `cost`/`description` behind envelope encryption so the app can only decrypt them **after physically scanning that
      КП's NFC code** — fully offline. Threat model: the adversary is the app in a participant's hands; a DB leak is out
      of scope, so there is **no at-rest encryption and no master key** — only what leaves the server is encrypted.
      Scheme (AES-256-GCM + HKDF via the `cryptography` package — a direct `pyproject.toml` dependency since this
      feature; `crypto.py:seal`/`unseal`/`derive_wrap_key`; `unseal` is **not** named `open` to avoid
      shadowing the builtin/tripping flake8): each locked КП is sealed with its own random 32-B `content_key` into
      `CheckpointSecret` (`O2O → Checkpoint`, `related_name="secret"`; `enc_blob={"iv","ct"}` over `{cost, description}`,
      `aad=str(cp.id)`); each `CheckpointTag` (its FK to `Checkpoint` is `CheckpointTag.checkpoint`, `related_name="tags"`,
      renamed from `point` in migration `website/0090`) carries a random 16-B `code` (written into the tag's NFC user memory
      out-of-band) and a per-tag `bundle_blob` = AES-GCM of `{cp_id: content_key}` over the tag's `unlocks` set, wrap
      key `HKDF(code)`, `aad=bid` where **`bid = sha256(code).hexdigest()[:16]`** (16 hex chars — the mobile client must
      compute it identically). `unlocks` is an M2M (`related_name="unlocked_by"`); an **empty** `unlocks` means the tag
      unlocks its own `checkpoint` (runtime `[tag.checkpoint]` default in `build_bundle`). The envelope (content-key indirection) is
      what lets one code open a configurable subset of КП with overlaps without re-encrypting a КП per code. Ciphertext
      + bundles are **precomputed and stored** so ETags stay stable. The service layer (`legend_crypto.py`:
      `seal_checkpoint`/`ensure_code`/`build_bundle`) is driven by **signals** (`post_save(Checkpoint)` re-seals + on a
      lock toggle rebuilds bundles of `cp.tags.all() ∪ cp.unlocked_by.all()`; `post_save(CheckpointTag)` +
      `m2m_changed(unlocks)` rebuild the tag's bundle) with a **recursion guard** (`post_save(CheckpointTag)`
      early-returns when `update_fields == {"code","bid","bundle_blob","updated_at"}`, the service's sentinel set; a
      thread-local flag fences the `m2m_changed` path). Bulk ops bypass signals — backfill/repair via
      `manage.py rebuild_legend_crypto [--race <id>] [--regenerate-codes]`; dump codes for tag provisioning via
      `manage.py export_legend_codes --race <id>`. Admin lock/unlock + rebuild-bundle bulk actions **must iterate and
      `save()`/call the service** (never `queryset.update()`, which skips the signals → no secret → cleartext leak). The
      serializer (`LegendCheckpointSerializer`) branches: locked → `{id, number, type, color, enc}`, open →
      `{id, number, type, color, cost, description}` — **both branches carry `color`** (`Checkpoint.color`, a named
      `CheckpointColor` token `red`/`blue`/`green`/`yellow`/`orange`/`purple`/`""`, **not secret**, the mobile app maps
      it to its own palette; it lives on `Checkpoint` (not in `enc_blob`/bundles) so the legend ETag /
      `versions.legend` move on a color edit via `Checkpoint.updated_at` `auto_now` — the reconcile must stay a plain
      `instance.save()` not `update_fields` omitting `"updated_at"`; adding `color` also bumped `_LEGEND_SCHEMA_VERSION`
      to 2 in `versioning.py` (schema-change cache bust — see **Conditional GET** above)); the online
      `api.CheckpointSerializer` intentionally does **not** carry `color`. `TagSerializer` emits **one entry per `CheckpointTag`**:
      `{bid, checkpoint_id, check_method}` for **every** tag (identity, open + locked) plus `iv`/`ct` from
      `bundle_blob` (`None` for open tags — identity-only, not decryptable). The legend view's tag queryset no longer
      excludes `bundle_blob=None` (so open-КП tags are emitted) and adds `.exclude(bid="")` (drops un-built rows created
      bypassing signals); response key is `tags` (was `bundles`).
      **`tag_hash` is gone** — offline КП identity is now `bid → checkpoint_id` for every tag (open + locked); the online
      scoring/scan path lives in the `api` app and matches by `nfc_uid` (unchanged). Per the **`update_fields` discipline** above, every service
      `save(update_fields=[...])` on `CheckpointTag`/`CheckpointSecret` includes `"updated_at"`.
    - **`update_fields` discipline**: the fingerprints rely on `auto_now` `updated_at` fields on `Team`/`Athlet`/
      `Checkpoint`/`CheckpointTag`/`CheckpointSecret`/`Category`/`Race`/`Tag`. Any `save(update_fields=[...])` on these models **must** include `"updated_at"`,
      otherwise the version/ETag goes stale (e.g. the auto `OPEN → SOLD_OUT` `reg_status` flips in
      `check_vtb_payments.py` and `website/models/models.py` include it). **Exception**: `MemberTagTouchView` deliberately
      omits `"updated_at"` from `save(update_fields=["last_seen_at"])` — see the **Member tags** invariant above.
    - **`nfc_uid` normalized invariant**: `Tag.nfc_uid` and `CheckpointTag.nfc_uid` are auto-normalized (stripped and uppercased) by their
      `save()` overrides. `save()` with or without `update_fields` still runs the override (Django calls the Python method regardless).
      The one case that bypasses the override entirely is `QuerySet.update(nfc_uid=...)` — that generates raw SQL without calling `save()`,
      so callers **must normalize the value themselves** (`.strip().upper()`) before passing it to `update()`. Lookups against `nfc_uid`
      should use the plain exact-match (`nfc_uid=value.strip().upper()`) rather than `__iexact`, since stored values are always stripped
      and uppercase.
    - **Track upload** (`POST /app/race/<id>/track/`, name `track` — the **third POST**, but **build-HMAC-only**: gated
      by `AppAPIView`'s default `[SignedAppPermission]`, **NOT** the per-person write layer — track recording runs on
      participants' phones, no bearer token; throttle `mobile-write`, 60/min). GPS-track ingestion for the Android app:
      receive a batch, store idempotently, ack the accepted ids — the organizer-facing map that reads tracks back is a
      separate later task, **out of scope** here. `TrackPoint` (`models.py`) is **write-only / immutable**: one row per
      GPS fix, **PK = the client-generated UUID** (`id`) so the idempotency key *is* the PK and upsert collapses to
      `bulk_create(objs, ignore_conflicts=True)` (a re-sent point hits a PK conflict and is silently skipped — no
      secondary index, no `update_or_create`). It is the documented exception to `apps.mobile`'s "self-contained" read
      path — it holds **cross-app FKs** into `website.Team`/`website.Race` (like `apps.race`). **No `updated_at`,
      deliberately not in `versioning.py`** (immutable rows never touch the fingerprint/`sync` machinery). `install_id`
      (from `request.app_meta["install_id"]`, the `X-Install-Id` header set by `SignedAppPermission` before the view
      runs) is stored for device attribution (a team recording from two phones is separable: `segment_id` distinguishes
      recording sessions, `install_id` groups a phone's sessions). **`accepted` = every submitted id** (with
      `ignore_conflicts=True` a point is either newly inserted or already present — both are success for the client's
      retry loop). **Validation is all-or-nothing (400 on a bad batch)**, not partial-accept: `TrackPointSerializer`/
      `TrackUploadSerializer` (`serializers.py`) bound `lat ∈ [-90,90]`, `lon ∈ [-180,180]`, `min_value=0` on the
      physically-non-negative magnitudes (`accuracy`/`gps_time_ms`/`elapsed_at`/`trusted_ms`/`vertical_accuracy`/
      `boot_count`), `altitude` **unbounded** (may be below sea level), `id`/`segment_id` `min_length=1`, `points`
      `max_length=500` (the app's batch cap). Flow (`views.py:TrackUploadView`): resolve a published `Race` (404) →
      validate body (400) → check the team is in the race (`category2__race_id=race_id`, else 404) →
      `bulk_create(ignore_conflicts=True)` → 200 `{"accepted": [all submitted ids]}`. Same trust boundary as the reads:
      a genuine build can post a track for any `team_id` in the race; `team_id`/`install_id` are spoofable, accepted
      as-is.
    - **Marks upload** (`POST /app/race/<id>/marks/`, name `marks` — a **fourth POST**, also **build-HMAC-only** like
      `/track/`: gated by `AppAPIView`'s default `[SignedAppPermission]`, NOT the per-person write layer — takes run on
      participants' phones, no bearer token; throttle `mobile-write`, 60/min). Checkpoint-take (взятие КП) ingestion for
      the Android app: receive a batch, store, compute a per-mark `verified`, ack the accepted ids — the read-side
      scoring/dedup/reattribution feature is a separate future task, **out of scope** here. **Two normalized models**
      (`models.py`, cross-app FKs into `website.Team`/`website.Race` like `apps.race`): `Mark` is one row per take report,
      **PK = the client UUID** (`id`, the idempotency key); `MarkPresent` is one row per present member, FK back
      (`related_name="present"`) with `unique_together("mark","number_in_team")` so child inserts are conflict-safe on
      re-send (normalized over a JSON column so the roster is queryable for the future scoring task; member identity is the
      physical chip `nfc_uid`/`code`, not the client `number`; `{nfc_uid:null, number:0}` is the "no snapshot"
      sentinel). `Mark` has `checkpoint_id` as a **plain int, not an FK** (an unknown КП is still accepted — data is never
      lost), `cp_code`/`cp_nfc_uid` `blank=True` (blank for a future `photo` mark), 7 flat nullable `loc_*` columns (the
      whole `location` may be null), and a server-computed `verified`. **No `updated_at`, deliberately out of
      `versioning.py`** (nothing reads a version off `Mark`) — but unlike `TrackPoint` the row is **NOT immutable**.
      **One deliberate divergence from `/track/`: enrichment-upsert, not strict-immutable.** The client deliberately
      **re-sends the same `id`** when a late GPS fix (`attachLocation`) or a new roster member (`addMember`) lands after
      the DTO was serialized but before the row was marked uploaded — the first POST carries `location=null`/a partial
      `present[]`, a later POST carries the enriched payload. A pre-filter-and-skip design would **permanently lose** the
      late data, so a repeat `id` must **merge**. Enrichment is monotonic (location null→value, `present` only grows,
      `complete` false→true; take-moment times and `cp_*` are stable), so a blind **last-write-wins upsert** of the
      scalars is correct and simplest: `Mark.objects.bulk_create(objs, update_conflicts=True, unique_fields=["id"],
      update_fields=MARK_UPDATE_FIELDS)` (Postgres `ON CONFLICT (id) DO UPDATE`, `MARK_UPDATE_FIELDS` = every scalar
      **except** `id` and `created_at` so the original insert time is preserved), then
      `MarkPresent.objects.bulk_create(present_objs, ignore_conflicts=True)` for **all** marks (additive — the
      `unique_together` skips already-stored slots, inserts the late roster members). `accepted` echoes **all** submitted
      ids. **In-batch `id` de-dup (the one `update_conflicts` gotcha)**: a single `INSERT … ON CONFLICT (id) DO UPDATE`
      with the same `id` twice raises Postgres `CardinalityViolation` → 500. The real client never sends in-batch dup ids,
      but a malformed body must not 500, so the view **de-dups `marks` by `id` (keeping the last occurrence)** before
      building the upsert objects; `accepted` still echoes the originally-submitted ids (`MarkPresent` is unaffected — it
      uses `ignore_conflicts`, which tolerates in-batch dups). **`verified` computed at ingest** with a single per-batch
      prefetch (`_bids_by_checkpoint`, no per-mark query): true iff `sha256(bytes.fromhex(cp_code)).hexdigest()[:16]`
      matches a `CheckpointTag.bid` of the claimed `checkpoint_id` (`cp_code` is the **hex** of the tag's 16 raw bytes) —
      proof the КП was physically scanned. Unknown КП / mismatch / bad-hex / blank `cp_code` → `verified=False`, **row
      still stored**; `.exclude(bid="")` drops un-built tags (mirrors the legend view). **Same trust boundary as the
      reads / `/track/`**: build-HMAC only, `team_id`/`source_install_id` spoofable and accepted as-is — but
      `source_install_id` is read from the **signed body** (the contract's provenance key), the `X-Install-Id` header
      stays for `AppInstall` stats. Serializers (`MarkUploadSerializer`/`MarkSerializer`/`PresentMemberSerializer`/
      `MarkLocationSerializer`) reuse `FiniteFloatField` and mirror `TrackUploadSerializer` bounds (`lat`/`lon`
      `[-90,90]`/`[-180,180]`, `altitude` unbounded, `min_value=0` on non-negative magnitudes, `marks` `max_length=500`,
      all-or-nothing 400); **400-not-500 caps**: 32-bit `IntegerField` columns (`checkpoint_id`/`expected_count`/`number`/
      `number_in_team`/**`boot_count`**) carry `max_value=2147483647`, BigInt fields (`trusted_ms`/`wall_ms`/`elapsed_at`/
      `loc_gps_time_ms`/`loc_elapsed_at`) the wider `2**63-1` — don't cross the two (a 2⁶³ value in `boot_count` still
      500s). `location` is `required=False` (load-bearing: the client uses kotlinx `encodeDefaults=false`, so a fix-less
      take **omits** the key rather than sending `location:null`); `method` is `ChoiceField(["nfc","photo"])` (any other
      value 400). Flow (`views.py:MarkUploadView`): resolve published `Race` (404) → validate (400) → team-in-race check
      (`category2__race_id`, else 404) → build `bids_by_cp` + compute `verified` → de-dup batch + flatten `location` to
      `loc_*` (`location is None` → all `loc_*=None`) + build `MarkPresent` objs → `transaction.atomic()` (parent upsert
      before child insert) → 200 `{"accepted": [all submitted ids]}`. Empty `marks` → early ack `[]` (tag-bid query
      skipped).
    - **Photo upload** (`POST /app/race/<id>/mark/<mark_id>/photo/<frame_id>`, name `mark_photo` — a **fifth POST**, also
      **build-HMAC-only** like `/track/`/`/marks/`: gated by `AppAPIView`'s default `[SignedAppPermission]`, NOT the
      per-person write layer; throttle scope `mobile-photo`, `120/min`). Stores one raw JPEG frame for a
      `method="photo"` `Mark` — the client posts one request per frame; the organizer-facing read/render side is a
      separate future task, **out of scope** here. **Binary body, not JSON**: the view reads `request.body` directly
      and never touches `request.data` (DRF's default parsers don't handle `image/jpeg`; touching `.data` 415s) —
      `SignedAppPermission` already reads `request.body` before the view (Django buffers it), so HMAC-over-raw-bytes
      works unchanged. `MarkPhoto` (`models.py`, child FK `mark → Mark` CASCADE `related_name="photos"`) is stored at
      a deterministic, unguessable path `mark_photos/<mark_id>/<frame_id>.jpg` (two client UUIDs) via a `FileField`
      on the persisted `/app/media` volume; **no `race` field** — `mark.race_id` carries it, and the URL `race_id` is
      validated against the mark by the `Mark.objects.get(pk=mark_id, race_id=race_id)` lookup (404 on mismatch).
      **No `updated_at`, deliberately absent from `versioning.py`** — same immutable-model precedent as `TrackPoint`.
      **Idempotent by `(mark, frame_id)`**: `unique_together` plus an `exists()` pre-check short-circuits a re-send to
      `200`; a concurrent duplicate that slips past the pre-check is caught via `IntegrityError` on the constraint and
      also acked `200` (deleting the file it just wrote so a rare race doesn't leak storage). **Orphan-file guard**: the
      file is written by `image.save()` *before* the row is committed, so a crash in that window leaves a canonical
      file with no row; on retry the view `storage.exists()`/`storage.delete()`s any stale file at the canonical path
      first so a re-post reuses the deterministic name instead of getting a storage-suffixed `_XXXX` copy. **`frame_id`
      charset guard**: `frame_id` is interpolated into a filesystem path, so the view validates it against
      `^[A-Za-z0-9_-]{1,64}$` and 400s before touching storage — but the URL's `<str:frame_id>` converter (`[^/]+`)
      already 404s an empty or slash-containing segment at the routing layer before the view runs, so the view guard
      only fires for a single bad-charset segment (e.g. `a.jpg`); `mark_id` needs no such guard since a hostile value
      simply matches no `Mark` row (404). Flow: `Race` 404 (published only) → `Mark` 404 (`pk=mark_id, race_id=race_id`)
      → frame_id charset 400 → empty-body 400 → oversized 413 (`PHOTO_MAX_BYTES = 10 MB`) → idempotent 200 → save 201 /
      `IntegrityError` 200. **`DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024`** (`settings.py`) had to be raised above
      the Django default 2.5 MB — it's enforced the moment `request.body` is first read, which happens inside
      `SignedAppPermission` **before** the view, so an under-cap JPEG over the old 2.5 MB default would have hit an
      opaque `RequestDataTooBig` 400 before the explicit 413 ever ran; this is a **global** relaxation (any non-
      multipart POST may now buffer up to 12 MB), accepted because nginx already gates all bodies at `client_max_body_
      size 50m`. **URL has no trailing slash** (diverging from every other `/app/` route) because the signed canonical
      string is the request's `full_path` and must match the client byte-for-byte. Not admin-registered, matching the
      `Mark`/`TrackPoint`/`MarkPresent` convention (only the stats models `AppInstall`/`AppAuthFailure` are registered).
      A frame arriving before its parent `Mark` 404s — contract-safe, since the client only drains a mark's frames
      after that mark's own upload is acknowledged, and treats a photo `404` as transient (self-heals on the next sync).

New feature apps that don't fit in `website` live under `src/apps/<name>/`. Each needs a unique `AppConfig` label (e.g.
`label = "race_app"`).

**Template stacks**: `src/templates/website/` has two base templates. `base.html` + `src/static/css/theme.css` —
Bootstrap-based, used by all pages except registration and login. `base-2.html` + `src/static/css/theme-2.css` — custom
CSS (Rubik font, vanilla JS), used by `register.html`, `login.html`, `start.html`, and `verify.html`. New pages matching
the new design should extend `base-2.html`. Page-specific CSS goes in `src/static/css/<page>.css` and is loaded via
`{% block extra_head %}`. Do not define a bare `.page` class in page-specific CSS — `theme-2.css` already defines it.
Use a scoped wrapper class (e.g. `.race-page`).

**Custom error pages**: `src/templates/{404,403,500}.html` (themed «Сбились с маршрута»), sharing
`src/static/css/error.css` (scoped under `.error-page`). Django's default handlers auto-load them by name — no
`handler404/500/403` wiring in `config/urls.py`. `404.html`/`403.html` extend `base-2.html` (rendered with a
`RequestContext`). `500.html` is **standalone by design** — a full `<!doctype html>` doc with hardcoded asset URLs, no
`{% static %}`/`{% url %}`/`{% footer_menu %}`/`{{ user }}`, so it renders with an empty context and a down DB.

**Auth**: lives in `apps.accounts` (mounted at `/accounts/*`). `LOGIN_URL = "login"` in `settings.py` points Django's
`@login_required` and `user_passes_test` decorators to `LoginView` (URL name `login`, now at `/accounts/login/`);
`AUTHENTICATION_BACKENDS` uses `apps.accounts.backends.EmailBackend`, which authenticates by `email__iexact`. URL names
are flat and unchanged from before the move (`login`, `register`, `logout`, `password_reset*`, `impersonate`), so
name-based `reverse()`/`{% url %}` callers are unaffected; only the paths changed, and old `/login/`, `/register/` now
404 (no redirects, by decision). `website/auth.py` re-exports `EmailBackend` and `website/forms.py` re-exports the auth
forms as shims.

**Passwordless login** (email-first, the promoted entry point — password login is kept as a secondary option). One DB
model `EmailVerification` (`apps/accounts/models.py`) backs both a 6-digit code and a magic link from a single row:
`code_hash` (`make_password` of the code — the raw code is never stored), `expires_at` (`CODE_TTL = 15 min`),
`attempts` (`MAX_ATTEMPTS = 5`), `consumed_at`; the magic link has no token column — the URL embeds
`TimestampSigner().sign(str(pk))` and the row's `expires_at`/`consumed_at` enforce lifetime + single use.
`create_for(email, purpose)` returns `(obj, raw_code)` and refuses a new code within `RESEND_COOLDOWN = 60 s` (returns
`(existing, None)` — anti-bombing); `verify_code` increments `attempts` and rejects dead rows; `is_alive` gates
everything. Flow (`/accounts/start|verify|link/<signed>/`, names `account_start`/`account_verify`/`magic_link`, all
carry `?next=`): `StartView` issues a code and emails it via `apps/accounts/emails.py:send_login_email` (code + signed
absolute magic-link URL, both `.txt`/`.html` alts, templates in `templates/accounts/email/`), responding neutrally for
known/unknown emails (no account-enumeration leak); `VerifyView` (code) and `MagicLinkView` (link) both funnel into the
shared `_complete_login(request, email, next_url)` — `mark_consumed` → log in existing user or inline-create (atomic,
catching `IntegrityError` from the case-insensitive email unique index, random password, username deduped from the
local-part) → app-local `_safe_redirect` against `request.get_host()` (so a tampered unsigned `next` falls back to `/`).
The link must not rely on the session (may open in another browser). Email send failures are logged, not raised. Email
tests must override `EMAIL_BACKEND` to locmem (django-mailer's `DbBackend` does not populate `mail.outbox`). The
race-page «Зарегистрировать команду» button (shown to anon users) — `AddTeam`'s anon redirect (`views_.py`, both `get`
and `post`) — enters the passwordless `account_start` (not password `login`), carrying `?next=request.path`;
`StartView`/`VerifyView` derive race context from that `next` path via `_race_from_next(next_url)` (`urlsplit` →
`resolve` → guard `url_name == "add_team"` → `Race.objects.filter(slug=…).first()`, display-only, never raises) so
`start.html`/`verify.html` greet «…добавить команду на «<гонка>»» with a back link to the race. Other login-gated team
actions (`EditTeamView`, member transfer/delete) still redirect to password `login`.

**Form fields in `base-2.html` pages**: Do not use `{{ form.field }}` — Django widgets emit `class="form-control"` (
Bootstrap) which conflicts with `theme-2.css`. Write fields manually:
`<input class="input{% if form.field.errors %} has-error{% endif %}" name="field_name" value="{{ form.field.value|default:'' }}">`,
with errors shown via `{{ form.field.errors|join:", " }}` beneath each input.

**Team add/edit pages** (`add_team.html`, `edit_team.html`) are two standalone base-2 templates — the shared form body
is intentionally duplicated (no partial); only behavior/styling is shared via `src/static/css/team-form.css` (scoped
under `.team-register`) and `src/static/js/team-form.js`. The JS reads no inline template vars: both views emit a JSON
config island `<script type="application/json" id="teamFormConfig">` (`currentPrice`, `paidPeople`, `isEdit`,
`raceRemaining`, `currentCategoryId`, `bypassLimits`, and
`extras: [{code, name, price, freePerTeam, count, countPaid}, …]`) and the JS renders one stepper per add-on (hidden
`extra_<code>` inputs, each bounded `countPaid … (ucount − freePerTeam)`) and computes the live доплата-aware total
mirroring the backend `compute_team_charge` (
`max(0, (ucount − paidPeople) × currentPrice + Σ active extras max(0, count − countPaid) × price)`). `team-form.js` and
`apps/race/pricing.py` carry cross-reference comments pointing at each other (the client/server-mirror rule).
`raceRemaining` is `null` for unlimited races; `currentCategoryId` is `null` on add. Category `<option>` elements carry
`data-remaining` (free slots, empty string = unlimited) and `data-current="1"` (team's own category) — `team-form.js`
uses these to disable full categories in the dropdown and cap the segmented size control; the team's own category is
never disabled. The segmented team-size control reads allowed counts from each category `<option>`'s `data-counts`
attribute (no hardcoded `switch`). `AddTeam` (`views_.py`) and `EditTeamView` (`team.py`) share context-building
helpers (`build_category_options` / `build_team_form_context` in `views_.py`); `EditTeamView` renders `edit_team.html`
and adds edit-only sections (payment history, member transfer, delete). Server-side guards (in `TeamForm.clean`) enforce
`ucount ∈ [category.min_people, category.max_people]` and, per active extra, `count ≤ max(0, ucount − free_per_team)`
plus the edit-only `count ≥ count_paid` ("can't reduce a partly-paid add-on") — the client controls only cap values in
the browser. `TeamForm.__init__` resolves the race **defensively** (a non-id/`None` `race_id` must yield
`self.extras = []` and add no fields rather than 500).

**Email uniqueness**: `auth_user.email` has a case-insensitive unique index (migration `0065_unique_user_email`).
Registration views wrap `User.objects.create_user` in `transaction.atomic()` and catch `IntegrityError` to surface a
field error on `email`. `EmailBackend` catches `MultipleObjectsReturned` defensively. Any code that creates users must
handle `IntegrityError` from this constraint.

**Payments**: models exist for four providers — VTB (OAuth), Yandex Wallet, Sberbank (phone transfer), SBP — each in
`website/models/` (`VTBPayment`, `PaymentsYa`, etc.). Credentials come from env vars — see `deploy/kolco24.env.example`.
The live flow is VTB `sbp2`, confirmed automatically by the `check_vtb_payments` polling command (
`_settle_race_payment`). The legacy manual-verification stack (the admin `/payments/` confirm/cancel page, the
user-facing sberbank/sbp "I paid, here is my card" templates, and the older Yandex API stubs `new_payment`/
`paymentinfo`/`getcost`/`yandexinform`/`success`) was **removed** — those routes now 404. The `Payment`/`PaymentLog`/
`PaymentsYa`/`SbpPaymentRecipient` tables and admin registrations are kept for history.

**VTB `order_id`s** (race-fee and donations) are random ULIDs — `ORDER_<ulid>` for race fees, `SPUTNIK_<ulid>` for
donations — minted by the single generator `VTBPayment.new_order_id(prefix)` (`website/models/vtb.py`). They are
globally unique across environments, which matters because dev and prod share VTB credentials (and thus the VTB
`order_id` namespace). Reconciliation (the polling command `check_vtb_payments`) follows an explicit FK to find the
domain object — `Payment.vtb_payment` (OneToOne, `on_delete=SET_NULL`, `related_name="race_payment"`) for race fees,
`DonateRequest.payment` for donations — never by parsing the id. The legacy `ORDER_<int>` int-parse survives only as a
fallback in `_resolve_race_payment` for pre-deploy in-flight payments and can be dropped once those settle.

**Team pricing & sizes** (`website/models/race.py`): `RacePriceTier` (FK `race`, `related_name="price_tiers"`; `price`,
`active_until` inclusive `DateField`, `order`) holds the price ladder. `Race.current_price` is the single source of
truth for the charged per-person amount — it returns the earliest tier with `active_until >= today`, the last tier when
all are past, or falls back to `Race.cost` when the race has no tiers. `Race.price_tier_ladder()` returns
`[{"tier", "status"}]` (`past`/`active`/`future`) for display. When charging, set `cost_now = race.current_price` for
BOTH the people-count multiplier AND the stored `Payment.cost_per_person` — `Team.update_team` back-calculates
`paid_for = withdraw_amount / cost_per_person`, so the two must stay identical. `Team.additional_charge` is deprecated —
do not add it to the formula. Allowed team sizes come from `Category.min_people`/`max_people` (defaults 2/6), not a
hardcoded JS `switch`.

**Team add-ons / доп-услуги** (`src/apps/race/models.py`, `src/apps/race/pricing.py`): a generic per-race "extra"
mechanism — teams buy optional units (maps, transfer, breakfast, …) during add/edit, charged together with the race fee
in one VTB/SBP payment. Three relational models: `RaceExtra` (per-race catalogue: `code`/`name`/`price`/`free_per_team`/
`order`/`is_active`, `unique_together("race","code")`), `TeamExtra` (per-team desired-vs-paid: `count`/`count_paid`, FK
to `RaceExtra` `on_delete=PROTECT`), `PaymentExtra` (per-payment snapshot of the delta a payment covers: `count` +
`unit_price` price-snapshot, generalizes the old `Payment.map`). **Single quantity rule for every add-on:**
`max = ucount − free_per_team` (maps `free=2`; transfer/breakfast `free=0`). Three shared helpers replace the two
duplicated maps formulas: `compute_team_charge(team, race) -> (total, lines)` (race-fee term plus
`Σ active extras max(0, count − count_paid) × price`), `upsert_team_extras(team, cleaned_data, race)` (writes
`TeamExtra.count` from the form's `extra_<code>` fields), and `create_team_payment(request, team, race)` (builds the
`Payment` + `PaymentExtra` rows, mints the VTB order, returns the redirect — or `None` when cost is 0 so the caller
redirects to its own success URL; sets/asserts `payment_method="sbp2"` because once extras are present `payment_amount`
deliberately diverges from `paid_for × cost_per_person`, so these must never flow through the partial Yandex
`update_team` back-calc). Both `AddTeam` and `EditTeamView` call `upsert_team_extras` then `create_team_payment`. *
*Reconciliation** (`check_vtb_payments`, VTB PAID path) credits add-ons from the per-payment snapshot — loops
`payment.extras`, `get_or_create(TeamExtra)`, `count_paid += pe.count` (clamping `count` up) — inside the existing
`transaction.atomic()`; idempotency lives in the command (the `status == done` guard + the outer already-`PAID`
exclusion), **not** in `PaymentExtra` (it has no consumed flag — `Payment.status` is the idempotency token). An admin
enables a new add-on by adding a row on the race edit page (no code change, no migration). The **legacy standalone**
transfer (`race/8/transfer/`) and breakfast (`race/<slug>/breakfast/…`) registration pages + their `Transfer`/
`BreakfastRegistration` models/tables were **removed** (migration `0073_delete_transfer_breakfast`; routes now 404, no
redirects) — the `RaceExtra` `code="transfer"`/`"breakfast"` add-ons above are the supported mechanism.

**Maps are now the `code="map"` extra** (`price=200`, `free_per_team=2`), backfilled by data migration
`apps/race/migrations/0002_migrate_maps_to_extras`. The legacy `Team.map_count`/`Team.map_count_paid`, `Payment.map`,
and the `FREE_MAPS`/`MAP_PRICE` constants in `website/forms.py` are **deprecated but still present** — their usages are
gone but the column/constant definitions are pending removal in a deferred follow-up migration (kept through at least
one deploy cycle so a payment created pre-deploy and confirmed post-deploy still reconciles).

**People limits** (`website/models/race.py`): two `IntegerField`s — `Race.people_limit` (cap across the whole race) and
`Category.people_limit` (cap within a category), both `default=0` where `0 = unlimited` (distinct from per-team
`Category.max_people`). Occupancy = **paid + reserved**: `Category.people_count()` / `Race.people_count()` sum
`paid_people` over non-deleted teams (`TeamManager` already excludes `is_deleted`); `Category.reserved_people()` /
`Race.reserved_people()` add *live reservations* (see **Seat reservation** below); `remaining_people()` returns `None`
when unlimited. `Category.remaining_people(exclude_team=...)` self-excludes the team being edited. The capacity **gate
lives in `TeamForm.clean()`** (reused by both `AddTeam` and `EditTeamView`, which pass `team=` +
`bypass_limits=request.user.is_superuser`; superusers skip the gate): the race-check blocks only *growth* (
`needed = new_ucount − team.paid_people`), the category-check blocks entering a full category or growing inside a full
one (a pure 2→2 move into a category with room is allowed). Auto `sold_out`: `update_team()` flips `Race.reg_status`
`OPEN → SOLD_OUT` once the race cap is reached at payment confirmation — **no auto-reopening** when slots free up (
Option B). Limits are configured on the race edit page (`RaceForm.people_limit` + per-row `Category.people_limit`).
Note: `api`-app team creation does **not** enforce these limits.

**Seat reservation** (`website/models/race.py`): a team that *started* paying must hold its seats until the payment
confirms, otherwise a payment confirmed 5–10 min later could push the race past its cap (the old paid-basis caveat). A
*live reservation* is any `Payment` with `status="draft"` whose `created_at` is within `RESERVATION_TTL` (a module-level
`timedelta`, currently **20 min**) — `create_team_payment` already mints exactly such a draft when the user is
redirected to VTB, so no new "start reservation" code path exists. `Race.reserved_people(exclude_team=…)` /
`Category.reserved_people(exclude_team=…)` sum, **per distinct team** (a re-submit's extra drafts don't multiply the
reservation), `max(0, ucount − paid_people)` — the seats that payment will fill (full `ucount` on add, the delta on a
top-up). `remaining_people()` = `people_limit − people_count() − reserved_people()`, self-excluding the edited team's
own paid **and** reserved seats. Nothing else changed: the `TeamForm.clean()` gate, the displayed `raceRemaining`/
`data-remaining` caps, and `bypass_limits` all tighten automatically because they already route through
`remaining_people()`. `reg_status` SOLD_OUT still flips on **paid** fill only (no reservation-driven open/close churn).
Two accepted caveats: (1) **fail-safe TTL** — the 20-min window is our estimate of the VTB order's life, not the real
expiry; if a draft outlives or dies before it, the seat is held at most ~20 min (never longer, never overbooks past the
window); (2) **double add-submit** — `AddTeam.post` creates a fresh `Team` per POST, so submitting the *add* form twice
can briefly reserve seats against the same user (self-resolves in ≤20 min), not fixed.

**Email** goes through `django-mailer` (`EMAIL_BACKEND = "mailer.backend.DbBackend"`): messages are queued in the DB and
sent by the `kolco24_runmailer` container running `manage.py runmailer`.

**Static files** are served by WhiteNoise from `STATIC_ROOT = src/staticfiles/` (populated by `collectstatic` at Docker
build time). `STATICFILES_DIRS` points to `src/static/` (source assets).

**Settings**: `src/config/settings.py` reads all secrets from env vars via `python-dotenv`. For production, values go in
`deploy/kolco24.env` (copy from `deploy/kolco24.env.example`).

## Code Style

Black 88-char limit, `isort` with Black profile. `ruff` and `flake8` share ignore rules from `setup.cfg` (`W503`,
`E722`; `F401` ignored in `__init__.py`).

Tests live in `src/<app>/tests.py` and use **pytest-style** functions with `@pytest.mark.django_db` and `client`/
`django_user_model` fixtures — not Django `TestCase` subclasses. `DJANGO_SETTINGS_MODULE = config.settings` is set
automatically by `pyproject.toml`.
