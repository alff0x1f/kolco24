# Mobile /app/* — Fix 403s & Log/Record Failed Auth Attempts

## Overview
Two problems in `apps.mobile` (the HMAC-signed mobile-app endpoints under `/app/*`):

1. **A malformed `MOBILE_APP_KEYS` fails silently.** The root-cause 403 (an invalid-JSON `.env`
   value with unquoted keys/values) has **already been fixed** in the local `src/.env` and is out of
   scope here. What remains: `settings.py` `json.loads(...)` raising `JSONDecodeError` is caught and
   silently collapses to `MOBILE_APP_KEYS = {}`, which makes `SignedAppPermission.has_permission`
   fail closed → every request 403. We make this misconfiguration **loud** (a WARNING at import time)
   so the next bad env (e.g. in `deploy/kolco24.env`) is diagnosable instead of silent.
2. **Failed 403 attempts leave no trace** — no log line, no DB row — so param brute-forcing of the
   mobile API is invisible. We want both a server-side warning log and an aggregated DB record.

Benefits: endpoints work again; misconfigured `MOBILE_APP_KEYS` is loud instead of silent; abuse of
`/app/*` is observable in the admin without leaking the failure reason to the client (the 403 stays
the neutral `"Forbidden"`).

## Context (from discovery)
- **Files/components involved:**
  - `src/config/settings.py:52-61` — `MOBILE_APP_KEYS` parsing (fail-closed to `{}`, silent).
  - `src/apps/mobile/permissions.py` — `SignedAppPermission` (6 `return False` points), `_client_ip`.
  - `src/apps/mobile/views.py` — `AppAPIView` base (`initial` → `_record_install`, best-effort pattern).
  - `src/apps/mobile/models.py` — `AppInstall` (aggregate-via-`update_or_create` + `F()+1` pattern).
  - `src/apps/mobile/admin.py` — `AppInstallAdmin` (read-only, no add/change/delete).
  - `src/apps/mobile/migrations/` — latest is `0003_appinstall_key_id` → new migration is `0004`.
  - `src/apps/mobile/tests.py` — pytest-style tests (1581 lines).
- **Related patterns found:**
  - Best-effort stats writes wrapped in `try/except` + `logger.exception` (`_record_install`).
  - Aggregate rows via `update_or_create(defaults=...)` then `.filter(...).update(count=F("count")+1)`.
  - Read-only admin (`has_add/change/delete_permission` → `False`), `list_filter`, `search_fields`.
- **Dependencies identified:** DRF (`BasePermission`, `APIView.permission_denied` hook), Postgres
  (kolco24_db), `django.db.models.F`.

## Development Approach
- **Testing approach: Regular** (code first, then tests) — matches the existing pytest-style suite.
- Complete each task fully before the next; small, focused changes.
- **Every code task includes new/updated tests** (success + error/edge), as separate checklist items.
- **All tests pass before starting the next task.**
- Tests are **pytest-style functions** with `@pytest.mark.django_db` and `client` fixtures — NOT
  Django `TestCase` subclasses (see CLAUDE.md Code Style).
- Keep `SignedAppPermission`'s documented contract: it does **no DB writes** (only stashes a reason).
- The client-facing 403 must stay the neutral `{"detail": "Forbidden"}` — no reason leaks outward.
- Run `make format && make lint` before any commit (user rule).

## Testing Strategy
- **Unit/integration tests** (`src/apps/mobile/tests.py`): required for every code task.
  - A request with a bad signature / unknown key-id / missing headers → 403 **and** creates an
    `AppAuthFailure` row with the correct `reason`; repeat increments `count` (same `(ip,key_id,reason)`).
  - A request with a different `key_id` or `reason` creates a **separate** row (key granularity).
  - The 403 response body stays `{"detail": "Forbidden"}` (reason not leaked).
  - A valid signed request still 200s and does **not** create an `AppAuthFailure` row.
- **No e2e/UI tests** — this is a backend-only, no-template change.

## Progress Tracking
- Mark completed items `[x]` immediately when done.
- New tasks: `➕` prefix. Blockers: `⚠️` prefix. Keep this file in sync with actual work.

## Solution Overview
- **Problem #1:** make the parse failure (and the non-empty-but-empty-after-filter case) emit a
  `WARNING` at settings import time so a misconfigured deploy is loud, not silent. (The local `.env`
  JSON itself is already fixed and out of scope.)
- **Problem #2 — Variant A (chosen in brainstorm):**
  - `SignedAppPermission` stays pure: a `_deny(request, reason, key_id)` helper stashes
    `request.app_denial = {reason, key_id, ip, path, install}` and returns `False`. Each of the 6
    `return False` points becomes `return self._deny(...)`.
  - `AppAPIView` overrides DRF's `permission_denied()` hook (called by `check_permissions` on a
    `False` permission, *before* the response is built) to log a `WARNING` and write an aggregated
    DB row, then `super().permission_denied(...)` raises `PermissionDenied` → neutral 403.
  - DB record is an **aggregate** keyed by `(ip, key_id, reason)` (chosen over row-per-attempt to
    bound table growth — brute force = thousands of requests), mirroring `AppInstall`'s
    `update_or_create` + `F()+1` pattern. Read-only admin like `AppInstallAdmin`.

## Technical Details
- **Reason codes** (one per `return False` in `permissions.py`): `no_keys`, `missing_headers`,
  `unknown_key`, `bad_ts`, `expired_ts`, `bad_sig`. Default `"unknown"` if `app_denial` is unset.
- **`AppAuthFailure` model:**
  ```python
  class AppAuthFailure(models.Model):
      ip = models.GenericIPAddressField(null=True, blank=True)
      key_id = models.CharField(max_length=32, blank=True)   # claimed, may be spoofed
      reason = models.CharField(max_length=32)
      count = models.PositiveIntegerField(default=0)
      first_seen = models.DateTimeField(auto_now_add=True)
      last_seen = models.DateTimeField(auto_now=True)
      last_path = models.CharField(max_length=255, blank=True)
      last_install_id = models.CharField(max_length=64, blank=True)
      class Meta:
          unique_together = ("ip", "key_id", "reason")
  ```
  **Accepted caveat:** with `ip=None`, Postgres treats NULLs as distinct in the unique constraint,
  but `update_or_create(ip=None, ...)` matches via `ip__isnull=True` at the ORM level, so normal
  flow dedups correctly; a micro-race on concurrent null-ip inserts is acceptable (best-effort stats).
- **`_deny` payload** (all length-clamped to model field sizes): `reason`, `(key_id or "")[:32]`
  (coerce `None` → `""` so the `missing_headers`/`unknown_key` paths can't `TypeError`),
  `ip=_client_ip(request)`, `path=request.get_full_path()[:255]`, `install=(X-Install-Id or "")[:64]`.
- **`_record_denial` reads defensively**: read the stashed dict with `.get(...)` defaults
  (`d.get("key_id", "")`, `d.get("ip")`, `d.get("path", "")`, `d.get("install", "")`) so the
  `{"reason": "unknown"}` fallback (when `app_denial` is unset) can't `KeyError` the writer.
- **Ordering guarantee:** `permission_denied` runs inside `check_permissions` → inside `initial`,
  *before* `_record_install`. A denied request raises `PermissionDenied` and short-circuits, so it
  produces an `AppAuthFailure` row but **never** an `AppInstall` row (no double-write).
- **Neutral 403 preserved:** the reason flows only to the log/DB — never into
  `super().permission_denied(message=...)`, which keeps DRF raising `PermissionDenied(detail="Forbidden")`.
  With `authentication_classes = []`, `request.authenticators` is empty so DRF raises
  `PermissionDenied` (403), not `NotAuthenticated` (401).
- **`settings.py` warning:** `logging.getLogger("config.settings").warning("MOBILE_APP_KEYS malformed
  or empty; all /app/* requests will 403")` — fire when `_raw_mobile_keys` is non-empty but parse
  fails, OR when the filtered `MOBILE_APP_KEYS` is empty while `_raw_mobile_keys` was non-empty.
  Do **not** warn when the env var is simply unset (a legit config).

## What Goes Where
- **Implementation Steps** (`[ ]`): settings warning, model + migration, permissions,
  view hook, admin, tests.
- **Post-Completion** (no checkboxes): runserver restart + smoke-script verification (needs a running
  server + DB), and the production `deploy/kolco24.env` having valid JSON.

## Implementation Steps

### Task 1: Add the settings misconfig warning

**Files:**
- Modify: `src/config/settings.py`

- [x] Add a module-level `import logging` if not present; in the `except` branch of the
      `MOBILE_APP_KEYS` parse, log `logging.getLogger("config.settings").warning("MOBILE_APP_KEYS malformed or empty; all /app/* requests will 403")`
- [x] Also emit that same warning when `_raw_mobile_keys` was non-empty but the filtered
      `MOBILE_APP_KEYS` ends up empty (guard against the unset case — no warning when env is absent)
      (consolidated into a single `if _raw_mobile_keys and not MOBILE_APP_KEYS` guard so malformed
      JSON, which flows through both branches, warns once — covers except + filtered-empty cases)
- [x] (No unit test for settings-import logging — it runs once at import; the smoke run in
      Post-Completion confirms endpoints 200 with a valid env) — verified manually: malformed env
      logs the WARNING, valid env parses silently

### Task 2: Add the `AppAuthFailure` model + migration

**Files:**
- Modify: `src/apps/mobile/models.py`
- Create: `src/apps/mobile/migrations/0004_appauthfailure.py`

- [x] Add the `AppAuthFailure` model (fields + `Meta.unique_together = ("ip", "key_id", "reason")`
      + a `__str__` like `f"{self.ip} {self.key_id} {self.reason} x{self.count}"`)
- [x] Generate the migration: `uv run python src/manage.py makemigrations mobile` (verify it is named
      `0004_*` and creates the table + unique constraint)
- [x] Apply it locally: `uv run python src/manage.py migrate mobile`
- [x] Write a test: creating two `AppAuthFailure` rows differing only in `key_id` (or `reason`) both
      persist; a second `update_or_create` with the same `(ip, key_id, reason)` reuses the row
- [x] run tests - must pass before next task

### Task 3: Make `SignedAppPermission` stash the denial reason

**Files:**
- Modify: `src/apps/mobile/permissions.py`

- [ ] Add `_deny(self, request, reason, key_id="")` that sets `request.app_denial = {...}` (reason,
      `(key_id or "")[:32]`, `ip=_client_ip(request)`, `path=request.get_full_path()[:255]`,
      `install=(X-Install-Id or "")[:64]`) and `return False` — coerce `None` key_id to `""`
- [ ] Replace each of the 6 `return False` points with `return self._deny(request, "<reason>", key_id)`
      using the matching code: `no_keys`, `missing_headers`, `unknown_key`, `bad_ts`, `expired_ts`, `bad_sig`
      (pass the claimed `key_id` where available)
- [ ] Confirm the success path still sets `request.app_meta` and `return True` unchanged; permission
      still performs **no DB writes**
- [ ] Write a test asserting `request.app_denial["reason"]` is set correctly per failure mode (can be
      asserted indirectly via the DB row in Task 4's tests; if testing the permission in isolation,
      build a DRF `APIRequestFactory` request) — mark which approach is used
- [ ] run tests - must pass before next task

### Task 4: Record denials in `AppAPIView.permission_denied` (log + DB)

**Files:**
- Modify: `src/apps/mobile/views.py`

- [ ] Import `AppAuthFailure`; add `permission_denied(self, request, message=None, code=None)` that
      calls `self._record_denial(request)` then `super().permission_denied(request, message=message, code=code)`
- [ ] Add `_record_denial(request)`: read `d = getattr(request, "app_denial", {"reason": "unknown"})`
      using `.get(...)` for every key after that, `logger.warning("Mobile app 403: reason=%s ip=%s key_id=%s path=%s install=%s", ...)`
- [ ] In the same method, best-effort DB write wrapped in `try/except` + `logger.exception("Failed to
      record AppAuthFailure")`: `update_or_create(ip=d.get("ip"), key_id=d.get("key_id", ""),
      reason=d["reason"], defaults={last_path: d.get("path", ""), last_install_id: d.get("install", "")})`
      then `.filter(ip=, key_id=, reason=).update(count=F("count") + 1)` (mirror `_record_install`)
- [ ] Write tests: bad signature → 403, body is `{"detail": "Forbidden"}`, one `AppAuthFailure` row
      with `reason="bad_sig"`, `count == 1`; a second identical bad request → same row, `count == 2`
- [ ] Write tests: unknown key-id → row with `reason="unknown_key"`; missing headers (no `key_id`) →
      `reason="missing_headers"` and stored `key_id=""` (no crash on `None`); expired `ts` →
      `reason="expired_ts"`; a **valid** signed request → 200 and **no** `AppAuthFailure` row created
- [ ] Write test: a denied request creates an `AppAuthFailure` row but **no** `AppInstall` row
      (locks in the `permission_denied`-before-`_record_install` ordering)
- [ ] run tests - must pass before next task

### Task 5: Register `AppAuthFailureAdmin` (read-only)

**Files:**
- Modify: `src/apps/mobile/admin.py`

- [ ] Register `AppAuthFailure` with `list_display = ("ip", "key_id", "reason", "count", "last_seen",
      "last_path")`, `list_filter = ("reason", "key_id")`, `ordering = ("-count",)`, `search_fields =
      ("ip", "key_id")`, all fields `readonly_fields`, and `has_add/change/delete_permission → False`
- [ ] Write a test (or extend an admin test if one exists) that the admin changelist for
      `AppAuthFailure` loads (200) for a staff user — skip if the suite has no admin tests pattern
- [ ] run tests - must pass before next task

### Task 6: Verify acceptance criteria
- [ ] All `/app/*` 403s now produce a `WARNING` log + an aggregated `AppAuthFailure` row keyed by
      `(ip, key_id, reason)`; the client still gets a neutral `403 {"detail": "Forbidden"}`
- [ ] A malformed/empty `MOBILE_APP_KEYS` emits the settings WARNING (not silent)
- [ ] A valid signed request 200s and records `AppInstall` (unchanged behavior), no `AppAuthFailure`
- [ ] Run full mobile test file: `uv run pytest src/apps/mobile/tests.py`
- [ ] Run full suite: `uv run pytest`
- [ ] `make format && make lint` clean

### Task 7: [Final] Documentation
- [ ] Update CLAUDE.md `apps.mobile` section: note the new `AppAuthFailure` model (aggregate 403
      tracking, `(ip, key_id, reason)` key, read-only admin) and that `permission_denied` is the
      log+record hook (permission stays no-DB-writes); reason codes list
- [ ] Update `deploy/kolco24.env.example` comment if needed (the example already uses valid JSON)
- [ ] Move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring a running server / external systems — no checkboxes, informational only.*

**Manual verification:**
- Restart `runserver`, then run the smoke script (needs DB + server up):
  `uv run scripts/check_mobile_endpoints.py --base-url http://127.0.0.1:8000 --key-id android-v1 --secret V2qkPR5QAQ --race-id=8`
  → expect all four endpoints **200**, an `AppInstall` row appears, and **no** `AppAuthFailure` rows
  from the valid calls.
- Send a deliberately bad request (e.g. wrong secret) and confirm a `WARNING` line in the server
  console and an `AppAuthFailure` row in the admin with the expected `reason`.

**External system updates:**
- Ensure production `deploy/kolco24.env` has a **valid-JSON** `MOBILE_APP_KEYS` (same class of bug
  could exist there); the new settings WARNING will surface it in container logs on next deploy.
