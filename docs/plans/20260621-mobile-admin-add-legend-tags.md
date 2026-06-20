# Mobile Admin: Add Legend Tags via Per-Person Login

## Overview
Let field crew act as admins **inside the mobile app** to add legend tags
(`CheckpointTag` — bind a scanned NFC chip to an existing КП). Today
`src/apps/mobile/` is signed **read-only**, authenticates the **build** itself
(per-build HMAC, `SignedAppPermission`), and has **no user accounts**. This adds:

1. a **per-person password login** (`POST /app/login/`) that mints a revocable
   opaque bearer token (general-purpose — may serve regular users later);
2. a **layered write permission stack** (build-HMAC + person identity + per-race
   authorization);
3. an **online-only** tag-creation endpoint that reuses the existing server-side
   legend-crypto signals (server stays the single source of crypto).

**Problem solved:** provisioning NFC chips currently requires the web legend
editor / `manage.py`. Crew in the field need to bind chips to КП on the spot.

**Scope is ADD-ONLY:** no tag edit/delete, no КП editing, no offline queue, no
client-side crypto.

## Context (from discovery)
- **App:** `src/apps/mobile/` (`label = "mobile"`, mounted at `/app/*`). Files:
  `views.py`, `permissions.py`, `signing.py`, `serializers.py`, `versioning.py`,
  `signals.py`, `legend_crypto.py`, `crypto.py`, `models.py`, `urls.py`,
  `tests.py`, `README.md`.
- **Existing auth:** `SignedAppPermission.has_permission` already builds the
  canonical string over `request.body` via `signing.build_canonical` — so body
  signing **structurally already works**; the `RawPostDataException` caveat in
  CLAUDE.md is a verification point, not new code. On success it stashes
  `request.app_meta` (incl. `key_id`, `ip`); on failure `request.app_denial` +
  neutral 403 (raised in `AppAPIView.permission_denied`). It does **no DB
  writes**.
- **`AppAPIView`** (`views.py`): `authentication_classes = []`,
  `permission_classes = [SignedAppPermission]`; `initial()` runs permissions then
  best-effort `_record_install`.
- **Authorization helper:** `src/apps/race/permissions.py:can_edit_race(user,
  race)` — superuser or `RaceAdmin(role=ADMIN)`. Same check used by the web
  `RaceLegendEditView`.
- **`CheckpointTag`** (`src/website/models/checkpoint.py`): `point` FK,
  `nfc_uid` (normalized strip+upper in `save()`, raises on blank), `check_method`
  (default `"offline"`), `code` = **`BinaryField`** (16 raw bytes, hex it for
  responses), `bid` = `sha256(code)[:16]`, `bundle_blob`, `unlocks` M2M,
  `updated_at` (`auto_now`). `post_save(CheckpointTag)` signal drives
  `ensure_code`/`build_bundle` (recursion guard keyed on the sentinel
  `update_fields` set `{"code","bid","bundle_blob","updated_at"}`).
- **Password auth path:** `authenticate(request, username=email,
  password=password)` via `apps.accounts.backends.EmailBackend` (`email__iexact`),
  exactly as `apps/accounts/views.py:LoginView`.
- **DRF:** installed (`rest_framework`), JSON-only renderer. No `authtoken`,
  no JWT, no throttling configured yet.

## Development Approach
- **Testing approach:** Regular (implementation + tests within each task).
- Complete each task fully (impl + tests passing) before the next.
- **Every task includes new/updated tests** — success **and** error/edge cases.
- **All tests must pass before starting the next task.**
- Run `make format && make lint` before committing (per project rules).
- Test command: `uv run pytest` (or `uv run pytest --reuse-db` for iteration).
- Tests are **pytest-style functions** with `@pytest.mark.django_db` and
  `client`/`django_user_model` fixtures — mirror `src/apps/mobile/tests.py`.
- Maintain backward compatibility: read endpoints and the existing build-HMAC
  contract are untouched.

## Testing Strategy
- **Unit/integration tests:** required every task, in `src/apps/mobile/tests.py`.
- Reuse the existing test helper(s) that build a valid signed request (find the
  current signing helper in `tests.py`; extend it to sign POST bodies).
- **No UI/e2e** layer in this app (JSON API only) — N/A.

## Progress Tracking
- Mark completed items `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.
- Keep this file in sync with actual work.

## Solution Overview
Layer a per-person credential on top of the per-build HMAC instead of replacing
it (Approach A from the brainstorm):

- **Login** (`POST /app/login/`) is gated by `SignedAppPermission` **only** (build
  HMAC, no token yet) → so even the password endpoint is reachable only from our
  builds. It authenticates email+password and mints a `MobileToken` row, returning
  the **raw token once**.
- **Identity** (`IsMobileUser`) resolves `Authorization: Bearer <token>` →
  `request.mobile_user`. Generic, reusable; authorizes nothing.
- **Authorization** (`CanEditRaceLegend`) reads `view.kwargs["race_id"]`, loads
  the race, returns `can_edit_race(request.mobile_user, race)`.
- **Tag create** (`POST /app/race/<race_id>/tags/`) creates the `CheckpointTag`
  via `instance.save()` so the existing crypto signals fire; returns
  `{bid, point, nfc_uid, code(hex)}` for the app to write into the chip.

## Key Design Decisions & Rationale
- **Opaque DB token, not JWT.** Revocation is the priority for an admin
  credential (lost phone / removed crew); a JWT blocklist defeats statelessness
  and reintroduces the per-request DB hit. Opaque token revokes instantly via
  `revoked_at` and gives `last_used_at` audit.
- **sha256 of a 256-bit random token (no salt / no slow hash).** Token hashing
  ≠ password hashing: a high-entropy `secrets.token_urlsafe(32)` is not
  brute-forceable, so a fast unsalted hash is correct and is stronger than DRF
  authtoken (plaintext). Lookup by indexed `token_hash`.
- **`/app/login/` is general (not admin-specific).** Any valid user gets a
  token; admin capability is decided per-action by `can_edit_race`. Keeps the
  door open for regular-user features without rework.
- **Reuse `can_edit_race`, no new role/flag.** Same concept as web legend edit.
- **Actionable errors for the authenticated layers; neutral 403 for the
  anonymous build layer.** Deliberate split: build-HMAC failures stay opaque to
  probes; login/token/authorization failures tell the human what to do.
- **Multiple concurrent tokens, individually revocable** (row-per-login).
- **Legend ETag moves for free.** `versioning.legend_version` already folds
  `CheckpointTag` `MAX(updated_at)|COUNT`; creating a tag bumps it — no
  `versioning.py` change. `created_by` adds no fingerprint concern.

## Technical Details
- **`MobileToken`** (`src/apps/mobile/models.py`):
  - `user` FK → `settings.AUTH_USER_MODEL`, `on_delete=CASCADE`,
    `related_name="mobile_tokens"`.
  - `token_hash` `CharField(max_length=64, unique=True)` — sha256 hex of the raw
    token (`unique=True` already creates the index; no extra `Meta.indexes`).
  - `created_at` `DateTimeField(auto_now_add=True)`.
  - `last_used_at` `DateTimeField(null=True, blank=True)`.
  - `expires_at` `DateTimeField()`.
  - `revoked_at` `DateTimeField(null=True, blank=True)`.
  - `@property is_active`: `revoked_at is None and expires_at > now`.
  - **Not** an ETag-fingerprinted model → no `updated_at` discipline.
  - **Out of scope:** `platform`/`install_id`/device metadata columns (YAGNI —
    `AppInstall` already records install stats; add later if needed).
- **Token TTL:** `MOBILE_TOKEN_TTL = timedelta(days=30)` constant in
  `settings.py` (sibling of `MOBILE_APP_TS_WINDOW`).
- **Token generation/verification** (small helper module, e.g.
  `src/apps/mobile/tokens.py` or inline in `models.py`):
  - `raw = secrets.token_urlsafe(32)`; `token_hash =
    hashlib.sha256(raw.encode()).hexdigest()`.
  - Resolve: hash the presented bearer, `MobileToken.objects.get(token_hash=…)`,
    check `is_active`; best-effort `save(update_fields=["last_used_at"])`.
- **`CheckpointTag.created_by`**: `ForeignKey(AUTH_USER_MODEL, null=True,
  blank=True, on_delete=SET_NULL, related_name="provisioned_tags")`. Set on
  create from `request.mobile_user`.
- **Endpoints / `urls.py`:**
  - `path("login/", LoginView.as_view(), name="login")`
  - `path("logout/", LogoutView.as_view(), name="logout")`
  - `path("race/<int:race_id>/tags/", TagCreateView.as_view(), name="tag_create")`
- **Throttling** (first use here — keep minimal): login is already behind the
  build-HMAC, so at-scale brute force needs a valid build secret. Use a **plain
  IP-scoped `ScopedRateThrottle`** (no subclass, no `request.data`/email read —
  avoids the body-read-ordering hazard). Add `DEFAULT_THROTTLE_RATES =
  {"mobile-login": "5/min", "mobile-write": "60/min"}` to `REST_FRAMEWORK` and
  set `throttle_classes = [ScopedRateThrottle]` + `throttle_scope` **per view**
  (do **not** set a global `DEFAULT_THROTTLE_CLASSES`). **Prerequisite:** DRF
  throttling needs a cache backend; there is **no `CACHES` config** today — add
  an explicit `CACHES` (Django default `LocMemCache` is acceptable for this rate
  ceiling; note it is per-process, so counts are approximate under multiple
  workers). An email-keyed throttle is intentionally **deferred to
  Post-Completion** (YAGNI given the build-HMAC gate).
- **КП identity — use `id`, not `number`.** `Checkpoint.number` is a plain
  `IntegerField`, **not unique per race**, so a number lookup risks
  `MultipleObjectsReturned` / wrong-КП binding. The app already has each КП's
  `id` from the synced legend, so the request sends `point` = **checkpoint id**.
  Resolve as `Checkpoint.objects.get(id=point, race=race)` excluding
  `type="hidden"` → 404 if not in this race / hidden.
- **`nfc_uid` validation in the serializer.** Reject blank in the serializer
  (400) — the model's `save()` raises `ValueError` on blank, which would surface
  as a 500 if it reached the DB layer.
- **Tag-create flow:**
  1. resolve `race` (published) → 404 if missing/unpublished.
  2. validate body `{point, nfc_uid}` via serializer; 400 on missing/blank.
  3. resolve `Checkpoint` by **id** within race, **exclude `type="hidden"`** →
     404 if not found.
  4. normalize `nfc_uid` = `.strip().upper()`.
  5. existing `CheckpointTag(nfc_uid=…)`: on the **same** КП → **idempotent** 200
     with the existing tag; on a **different** КП → **409** (don't auto-rebind).
  6. else create `CheckpointTag(point=cp, nfc_uid=…,
     created_by=request.mobile_user)` inside `transaction.atomic()` and **catch
     `IntegrityError`** (the `unique_together("point","nfc_uid")` constraint +
     concurrent double-tap) → on conflict, re-query the `(point, nfc_uid)` row and
     return the idempotent 200. Use **`instance.save()`** (never `QuerySet` —
     must fire `post_save` → `ensure_code`/`build_bundle`). Re-fetch to get
     `code`/`bid`.
  7. response `{bid, point: cp.number, nfc_uid, code: code.hex() if code else
     None}`, 201 (or 200 on idempotent hit). If an idempotent-hit row has
     `bid == ""`/`code is None` (created bypassing signals), rebuild via the
     service before responding rather than calling `None.hex()`.

## What Goes Where
- **Implementation Steps** (`[ ]`): models, migrations, permissions, views,
  urls, settings, serializers, tests, docs — all in this repo.
- **Post-Completion** (no checkboxes): mobile-client work (sending the bearer +
  body signature, writing `code` to chip), provisioning real `RaceAdmin` rows,
  deploy/env notes.

## Implementation Steps

### Task 1: `MobileToken` model + token helpers + migration

**Files:**
- Modify: `src/apps/mobile/models.py`
- Create: `src/apps/mobile/tokens.py`
- Create: `src/apps/mobile/migrations/000X_mobiletoken.py` (via `makemigrations`)
- Modify: `src/apps/mobile/tests.py`

- [x] add `MobileToken` model (fields per Technical Details) with `is_active`
      property; `token_hash` is `unique=True` (no separate `Meta.indexes`).
- [x] add `MOBILE_TOKEN_TTL = timedelta(days=30)` to `src/config/settings.py`.
- [x] create `tokens.py`: `generate_token() -> (raw, token_hash)`,
      `hash_token(raw)`, `resolve_token(raw) -> MobileToken | None` (checks
      `is_active`, best-effort `last_used_at`).
- [x] generate migration: `uv run python src/manage.py makemigrations mobile`.
- [x] write tests: token generate/hash roundtrip; `is_active` true/false for
      fresh/expired/revoked; `resolve_token` returns None for unknown/expired/
      revoked and the row for valid (and stamps `last_used_at`).
- [x] run tests — must pass before next task.

### Task 2: `POST /app/login/` — password login mints a token

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/urls.py`
- Modify: `src/config/settings.py` (throttle scopes)
- Modify: `src/apps/mobile/tests.py`

- [x] add a `LoginSerializer` (`email`, `password`) for input validation.
- [x] add `LoginView(AppAPIView)` (or subclass with only `SignedAppPermission`):
      `authenticate(request, username=email, password=password)`; on success mint
      `MobileToken` (`expires_at = now + MOBILE_TOKEN_TTL`), return
      `{token: raw, expires_at}` 200; on failure 401 generic message
      `"Неверный email или пароль"` (no enumeration); override to use POST.
- [x] wire `path("login/", …, name="login")` in `urls.py`.
- [x] add a `CACHES` setting (Django `LocMemCache`) — required for throttling.
- [x] add `DEFAULT_THROTTLE_RATES` (`mobile-login`, `mobile-write`) to
      `REST_FRAMEWORK`; set `throttle_classes = [ScopedRateThrottle]` +
      `throttle_scope = "mobile-login"` on `LoginView` (plain IP keying, **no**
      subclass, no `request.data` read). Do not set a global throttle class.
- [x] write tests: valid build-sig + correct creds → 200 + token, exactly one
      `MobileToken` row, `token_hash` stored (raw token **not** in DB), `is_active`.
- [x] write tests: wrong password and unknown email → 401 with the **same**
      message; missing/bad build signature → neutral 403; **malformed body**
      (missing email / non-JSON) → 400 (no 500, no throttle crash); throttle →
      429 after the limit.
- [x] run tests — must pass before next task.

### Task 3: `IsMobileUser` permission (identity) + `POST /app/logout/`

**Files:**
- Modify: `src/apps/mobile/permissions.py`
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/urls.py`
- Modify: `src/apps/mobile/tests.py`

- [x] add `IsMobileUser(BasePermission)`: read `Authorization: Bearer <token>`,
      `resolve_token`, set `request.mobile_user` on success; return False (→ 401)
      otherwise. Identity only — no authorization.
- [x] make the 401 actionable: missing/expired/revoked token yields HTTP **401**
      (not the neutral 403). **Pin the mechanism** — with
      `authentication_classes = []`, a raised `NotAuthenticated`/`AuthenticationFailed`
      renders as **403** (DRF has no authenticator to emit `WWW-Authenticate`).
      So raise a small `APIException` subclass with `status_code = 401` (e.g.
      `MobileTokenInvalid`) from the permission. Document in the docstring.
- [x] add `LogoutView`: stack `[SignedAppPermission, IsMobileUser]`; flip
      `revoked_at = now()` on the presented token; 200.
- [x] wire `path("logout/", …, name="logout")`.
- [x] write tests: valid token → `request.mobile_user` resolved (assert via a
      probe/logout 200); missing header / expired / revoked token → 401; logout
      revokes (token then rejected, other tokens of same user still valid).
- [x] run tests — must pass before next task.

### Task 4: `CanEditRaceLegend` permission (per-race authorization)

**Files:**
- Modify: `src/apps/mobile/permissions.py`
- Modify: `src/apps/mobile/tests.py`

- [x] add `CanEditRaceLegend(BasePermission)`: read `view.kwargs["race_id"]`,
      load `Race` (404 if missing), return `can_edit_race(mobile_user, race)`
      (import from `apps.race.permissions`); False → 403 actionable. **Read
      `request.mobile_user` defensively** (`getattr(request, "mobile_user",
      None)`) and return False if absent — don't `AttributeError` (500) if the
      stack is reordered or the permission is tested in isolation. Document the
      required ordering (after `IsMobileUser`) in the docstring.
- [x] write tests (in isolation; the Task 6 endpoint exercises the full stack):
      superuser and `RaceAdmin(role=ADMIN)` → pass; MODERATOR and authenticated
      user without rights → 403; unknown `race_id` → 404; **no `mobile_user` on
      the request → False (403), not 500** — guards the defensive read.
- [x] run tests — must pass before next task.

### Task 5: `CheckpointTag.created_by` field + migration

**Files:**
- Modify: `src/website/models/checkpoint.py`
- Create: `src/website/migrations/00XX_checkpointtag_created_by.py`
- Modify: `src/apps/mobile/tests.py` (or `src/website/tests.py`)

- [x] add `created_by = ForeignKey(AUTH_USER_MODEL, null=True, blank=True,
      on_delete=SET_NULL, related_name="provisioned_tags")` to `CheckpointTag`.
- [x] confirm the field is **not** folded into any version fingerprint (it isn't;
      `legend_version` uses `MAX(updated_at)|COUNT`) — no `versioning.py` change.
- [x] generate migration: `uv run python src/manage.py makemigrations website`.
- [x] write test: a `CheckpointTag` saved with `created_by` set persists it; the
      legend-crypto `post_save` signal still produces `code`/`bid`/`bundle_blob`
      (i.e. adding the field didn't disturb the recursion guard / sentinel
      `update_fields`).
- [x] run tests — must pass before next task.

### Task 6: `POST /app/race/<race_id>/tags/` — tag creation endpoint

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/urls.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] add `TagCreateSerializer` (`point` int = **checkpoint id**, `nfc_uid` str)
      for input validation (400 on missing/blank `nfc_uid` — before the model
      `save()` blank-raise).
- [ ] add `TagCreateView` with `permission_classes = [SignedAppPermission,
      IsMobileUser, CanEditRaceLegend]`, `throttle_classes = [ScopedRateThrottle]`,
      `throttle_scope = "mobile-write"`.
- [ ] implement the flow (Technical Details §Tag-create): resolve published race
      → resolve non-hidden `Checkpoint` by **id** within race (404) → normalize
      `nfc_uid` → idempotent same-КП / 409 cross-КП → else create with
      `created_by` inside `transaction.atomic()` catching `IntegrityError`
      (unique_together / double-tap → re-query, idempotent 200) and
      `instance.save()` → response `{bid, point: cp.number, nfc_uid, code:
      code.hex() if code else None}`.
- [ ] wire `path("race/<int:race_id>/tags/", …, name="tag_create")`.
- [ ] write tests (happy path): full signed POST (build-sig over body + bearer +
      RaceAdmin) → 201, `CheckpointTag` created, `code`/`bid`/`bundle_blob`
      populated **via signals**, response carries hex `code` + `bid` + `point` +
      `nfc_uid`, `created_by` set to the user.
- [ ] write tests (idempotency/conflicts): re-POST same `nfc_uid`+same КП → no
      duplicate row, **no `IntegrityError` leak**, success; same `nfc_uid`
      different КП → 409; idempotent hit on a tag with `bid==""`/`code is None`
      → no `None.hex()` 500 (rebuilt); КП id not in race → 404; hidden КП → 404;
      `nfc_uid` normalization (lowercase/whitespace in → stored upper/stripped).
- [ ] write tests (legend version moves): capture `versions.legend` (or legend
      `ETag`) before/after create → changes; a follow-up `If-None-Match` with the
      **new** ETag → 304, with the **old** ETag → 200.
- [ ] run tests — must pass before next task.

### Task 7: Body-signing roundtrip on POST (lift GET-only restriction)

**Files:**
- Modify: `src/apps/mobile/signing.py` and/or `src/apps/mobile/permissions.py`
  (only if a fix is needed)
- Modify: `src/apps/mobile/tests.py`

- [ ] verify `SignedAppPermission` + DRF parse `request.data` correctly **after**
      the permission reads `request.body` for a JSON POST (no
      `RawPostDataException`). `build_canonical` already reads `request.body`
      first, so this should hold; if it fails, ensure body is read before DRF
      parsing. Add a focused note.
- [ ] reuse the existing `_signed_headers(method, path, secret, body=…)` helper
      (it **already** signs the body via `build_canonical`); ensure the signed
      bytes are **byte-identical** to the request body the test client sends
      (same encoding/content-type) so `sha256_hex(body)` matches.
- [ ] write test: POST with a correct body-inclusive signature → passes;
      tampered body (signature over a stale body) → neutral 403; empty-vs-present
      body both handled.
- [ ] run tests — must pass before next task.

### Task 8: Verify acceptance criteria
- [ ] all Overview requirements implemented: per-person password login, revocable
      token, layered permissions, online add-tag through existing crypto signals,
      actionable vs neutral error split, throttling.
- [ ] edge cases handled: enumeration-safe login, expired/revoked token,
      non-admin user, cross-КП conflict, hidden/absent КП, nfc normalization.
- [ ] run full suite: `uv run pytest`.
- [ ] `make format && make lint` clean.

### Task 9: [Final] Documentation
- [ ] update `src/apps/mobile/README.md`: new login/logout/tag endpoints, token
      model + lifecycle, layered permission stack, error semantics, throttling.
- [ ] update `CLAUDE.md` `apps.mobile` section: lift the "Body signing is
      GET-only" caveat; document `MobileToken`, `IsMobileUser`,
      `CanEditRaceLegend`, the write endpoint, `CheckpointTag.created_by`, and the
      neutral-vs-actionable error split.
- [ ] move this plan to `docs/plans/completed/`.

## Post-Completion
*Informational — external/manual, no checkboxes.*

**Mobile client work (separate codebase):**
- Send `Authorization: Bearer <token>` on write requests **plus** the existing
  build-HMAC headers, now signing the request **body** (`sha256_hex(body)` in the
  canonical) for POST.
- Store the raw token securely (keychain); on 401, re-run `/app/login/`.
- After a successful tag create, write the returned hex `code` into the chip's
  NFC user memory.

**Operational:**
- Provision real `RaceAdmin(role=ADMIN)` rows for crew who should add tags.
- Confirm a dedicated **admin build** key-id exists in `MOBILE_APP_KEYS` if you
  want to gate which builds can even reach `/app/login/` (optional — any valid
  build can currently call it).
- Decide/communicate the 30-day token TTL and the manual revoke lever
  (`revoked_at`) for lost devices.
- **Deferred:** email-keyed login throttle and a shared cache backend (Redis/
  Memcached) if per-process `LocMemCache` counts prove too loose under multiple
  workers. The build-HMAC gate makes this low-priority.

**Manual verification:**
- End-to-end on a device: login → scan chip → create tag → write code → offline
  unlock of that КП in the legend.
