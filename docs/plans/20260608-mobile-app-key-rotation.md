# Mobile API Secret Rotation via `X-App-Key-Id`

## Overview
Support **multiple active signing secrets** on the server so a secret can be
rotated without instantly breaking older app builds. The mobile client sends an
`X-App-Key-Id` header (e.g. `android-v1` / `ios-v1` / `android-v2`); the server
looks up the matching secret in a key-id → secret map and verifies the HMAC
signature with it.

- **Problem it solves:** today there is one `MOBILE_APP_SECRET`. Changing it is a
  hard cutover — every client built against the old secret breaks the instant the
  env changes. With a keyed map, two (or more) secrets are active at once, so a
  new app version (`*-v2`) and the previous one (`*-v1`) both verify during the
  overlap window; the old key is retired only once no installs report it.
- **Key benefit:** zero-downtime secret rotation aligned with app releases.
- **Integration:** purely within `apps.mobile`. The signing **canonical is
  unchanged** and `signing.py` gets only a docstring fix — key selection lives
  only in `permissions.py`. Self-contained: `/api/`, `donate`, `website` untouched.

## Context (from discovery)
- **Files/components involved:**
  - `src/config/settings.py:46` — `MOBILE_APP_SECRET = os.getenv("MOBILE_APP_SECRET", "")` (to be replaced).
  - `src/apps/mobile/permissions.py` — `SignedAppPermission.has_permission` (pure crypto check, fail-closed, neutral 403, stashes `request.app_meta`).
  - `src/apps/mobile/signing.py` — `build_canonical`/`sign`/`verify` — **NOT modified**.
  - `src/apps/mobile/models.py` — `AppInstall` (per-install stats).
  - `src/apps/mobile/admin.py` — read-only `AppInstallAdmin`.
  - `src/apps/mobile/views.py` — `AppAPIView._record_install` (`update_or_create` + `F()` increment, best-effort `try/except`).
  - `src/apps/mobile/tests.py` — pytest-style; **two** request builders: `_signed_headers(method, path, secret, body=b"")` (line 225, `HTTP_X_*` dict for the Django `client`) **and** `_signed_get_request(secret=SECRET, ts=None, path=PATH, extra_headers=None)` (line 98, a `RequestFactory` request driving `SignedAppPermission.has_permission()` directly in the unit tests at lines 116-196). ~60 sites set `settings.MOBILE_APP_SECRET`; fail-closed tests at 117 (`""`), 123 (`None`), 321 (`""`).
  - `src/apps/mobile/README.md`, `CLAUDE.md:54` (apps.mobile paragraph), `deploy/kolco24.env.example:48`, **root `README.md:39`** (stale `MOBILE_APP_SECRET` comment), `src/apps/mobile/signing.py:5` (docstring references `MOBILE_APP_SECRET`).
- **Related patterns found:**
  - Latest mobile migration is `0002_drop_install_id_db_index` → new one is `0003`.
  - `MOBILE_APP_*` settings read from env in `settings.py`; `MOBILE_APP_TS_WINDOW = 300` is a hardcoded constant (not env).
  - Neutral-403 contract: any verification failure returns `403 {"detail": "Forbidden"}` with no hint which check failed.
  - `request.app_meta` keys: `install_id`/`platform`/`app_version`/`ip` — add `key_id`.
- **Dependencies identified:** none new. Reuses existing signing/permission/stats machinery and DRF.

## Development Approach
- **testing approach**: Regular (code first, then tests within the same task).
- complete each task fully before moving to the next; small, focused changes.
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task (success + error scenarios).
- **CRITICAL: all tests must pass before starting the next task.**
- **CRITICAL: update this plan file when scope changes during implementation.**
- run `uv run pytest src/apps/mobile/tests.py` after each task; run `make format && make lint` before commit.
- maintain self-containment (do not touch `/api/`, `donate`, `website`).

## Testing Strategy
- **unit tests**: required for every task. `permissions.py` gets request-level tests through the Django test `client` with signed headers; the `MOBILE_APP_KEYS` settings parsing is covered indirectly (empty/unknown-key cases) and the model field via a stats-recording test.
- **e2e tests**: project has no UI e2e harness for this backend API — N/A. Request-level pytest cases through `client` are the integration layer.
- helper `_signed_headers(method, path, secret, body=b"", key_id="test-v1")` builds the canonical + signature and returns the header dict **including** `X-App-Key-Id`.

## Progress Tracking
- mark completed items `[x]` immediately when done.
- add newly discovered tasks with ➕ prefix; document blockers with ⚠️ prefix.
- keep this plan in sync with actual work.

## Solution Overview
- **One keyed map, parsed once.** `settings.MOBILE_APP_KEYS` is a `dict[str, str]`
  parsed from a JSON env var at process start. The permission does an in-memory
  `dict.get(key_id)` — no DB/IO on the hot path; the check stays pure crypto.
- **`X-App-Key-Id` is mandatory; `MOBILE_APP_SECRET` is removed entirely.** Clean
  break — the app is unreleased, so there are no real old clients to protect; no
  fallback, no legacy key. Missing header → neutral 403.
- **Canonical unchanged.** A tampered `key_id` selects a different secret, so
  `verify` fails — the key-id is implicitly bound by the signature. Adding it to
  the canonical would be redundant (YAGNI). All key-selection logic is in
  `permissions.py`; `signing.py` gets a docstring-only fix (no logic/canonical
  change) and its unit tests are not touched.
- **Observability for safe retirement.** `AppInstall.key_id` records the last
  key-id each install used, so before dropping `*-v1` an admin can confirm no
  install still reports it.

### Key design decisions & rationale
- **JSON-in-env over DB storage:** secrets never reach DB dumps/replicas/admin;
  the permission stays I/O-free; consistent with the other `MOBILE_APP_*` settings;
  runtime rotation via admin isn't needed (rotation coincides with an app release,
  which is already a deploy event). DB storage would add a secret-at-rest problem
  and a per-request query/cache for a benefit we don't need.
- **Hardened parsing:** a missing or malformed `MOBILE_APP_KEYS` yields `{}`
  (caught `json.JSONDecodeError`), and only non-empty string secrets are kept —
  so a misconfigured deploy fails closed (every request 403), exactly like the old
  empty-secret behavior.

## Technical Details
- **Settings (`settings.py`)** — replace the `MOBILE_APP_SECRET` line with:
  Add `import json` to the **top stdlib import group** (next to `import os`) — NOT
  inside the replacement block, or isort/flake8 (`make lint`) fails. Then replace
  the `MOBILE_APP_SECRET` line (~46) with:
  ```python
  _raw_mobile_keys = os.getenv("MOBILE_APP_KEYS", "")
  try:
      _parsed_mobile_keys = json.loads(_raw_mobile_keys) if _raw_mobile_keys else {}
  except json.JSONDecodeError:
      _parsed_mobile_keys = {}
  # keep only non-empty string secrets — fail-closed hygiene
  MOBILE_APP_KEYS = {
      k: v for k, v in _parsed_mobile_keys.items() if isinstance(v, str) and v
  }
  ```
  `MOBILE_APP_TS_WINDOW` / `MOBILE_DATA_SOURCE` are unchanged. Note: the
  `JSONDecodeError`-guard and the `isinstance/non-empty` filter are **fail-closed
  hardening** against an operator typo at boot (malformed env shouldn't 500 the
  process — it yields `{}` → every request 403). They run only at import, so the
  dict-injection test seam can't exercise them directly; the empty-`{}` fail-closed
  test (Task 5) is the closest behavioral coverage. Kept the `v`-only filter (dropped
  the redundant key `isinstance`) to stay minimal.
- **Permission (`permissions.py`)** `has_permission`:
  ```python
  keys = getattr(settings, "MOBILE_APP_KEYS", {}) or {}
  if not keys:
      return False  # fail closed: misconfigured deploy never leaks data

  key_id = request.headers.get("X-App-Key-Id")
  sig = request.headers.get("X-App-Sig")
  ts = request.headers.get("X-App-Ts")
  install = request.headers.get("X-Install-Id")
  if not key_id or not sig or not ts or not install:
      return False

  secret = keys.get(key_id)
  if not secret:
      return False  # unknown key-id → neutral 403, no hint

  # ... existing ts-window check, build_canonical(...), verify(secret, ...) ...

  request.app_meta = {
      "install_id": install[:64],
      "platform": request.headers.get("X-App-Platform", "")[:16],
      "app_version": request.headers.get("X-App-Version", "")[:32],
      "key_id": key_id[:32],
      "ip": _client_ip(request),
  }
  ```
  Update the module docstring: it references `MOBILE_APP_SECRET` — point it at the
  keyed map instead.
- **Model (`models.py`)** — add to `AppInstall`:
  `key_id = models.CharField(max_length=32, blank=True)`. Migration `0003` (one
  `AddField`, `blank=True`, no default needed beyond the implicit `""`).
- **Stats (`views.py`)** — `_record_install`: add `key_id=meta.get("key_id", "")`
  to the `update_or_create` `defaults` (matching how `platform`/`app_version` are
  written).
- **Admin (`admin.py`)** — add `"key_id"` to `list_display`, add
  `list_filter = ("key_id", "platform")`, and add `"key_id"` to `readonly_fields`.
- **Docs / env** — `deploy/kolco24.env.example`: replace `MOBILE_APP_SECRET=` with
  ```
  # JSON map of key-id -> shared secret. Each app build ships one key-id + its secret.
  # Keep >=2 keys active during a rotation overlap; drop a key only when no install reports it.
  MOBILE_APP_KEYS={"android-v1":"","ios-v1":""}
  ```
  `README.md` + `CLAUDE.md` apps.mobile paragraph: document `MOBILE_APP_KEYS`, the
  mandatory `X-App-Key-Id` header (add it to the headers table), the rotation
  workflow, and `AppInstall.key_id`.

## What Goes Where
- **Implementation Steps** (`[ ]`): settings parsing, permission key-selection, model field + migration, stats wiring, admin, test migration + new cases, docs.
- **Post-Completion** (no checkboxes): generate/distribute the real per-build secrets, hand the mobile devs the `key_id` + signing recipe, set `MOBILE_APP_KEYS` in the deploy env, the actual rotation run.

## Implementation Steps

### Task 1: Settings — `MOBILE_APP_KEYS` parsing (remove `MOBILE_APP_SECRET`)

**Files:**
- Modify: `src/config/settings.py`

- [ ] add `import json` near the existing stdlib imports
- [ ] replace `MOBILE_APP_SECRET = os.getenv(...)` (line ~46) with the
      `MOBILE_APP_KEYS` parse block (try/except `JSONDecodeError` → `{}`; keep only
      non-empty string secrets)
- [ ] confirm `uv run python src/manage.py check` passes (no test code yet — parsing
      is exercised by the permission tests in Task 2/5)
- [ ] run `uv run pytest src/apps/mobile/tests.py` — **expected to fail** here since
      tests still set `MOBILE_APP_SECRET`; this is fixed in Task 5. Note the failure
      set so Task 5 can confirm it clears. (Do not proceed to commit mid-way.)

> ⚠️ Tasks 1–5 form one logically atomic change (removing `MOBILE_APP_SECRET`
> breaks the suite until the permission + tests are migrated). Land them together;
> the per-task test runs are progress checks, and the suite is green again at the
> end of Task 5.

### Task 2: Permission — key-id selection (+ `signing.py` docstring)

**Files:**
- Modify: `src/apps/mobile/permissions.py`
- Modify: `src/apps/mobile/signing.py` (docstring-only)

- [ ] update the `permissions.py` module docstring to reference the `MOBILE_APP_KEYS` map instead of `MOBILE_APP_SECRET`
- [ ] replace the single-secret read with `keys = getattr(settings, "MOBILE_APP_KEYS", {}) or {}`; empty → `return False`
- [ ] read `X-App-Key-Id` alongside the existing headers; any missing (incl. key_id) → `return False`
- [ ] `secret = keys.get(key_id)`; `None`/empty → `return False` (unknown key-id, neutral)
- [ ] keep the existing ts-window check and `verify(secret, canonical, sig)` flow
- [ ] add `"key_id": key_id[:32]` to `request.app_meta`
- [ ] fix the **`signing.py:5` docstring** stale `MOBILE_APP_SECRET` reference → "the per-build shared secret selected by `X-App-Key-Id`". This is the **only** change to `signing.py` — no logic/canonical change (so "canonical unchanged" still holds; the `signing.py` unit tests are untouched)
- [ ] (behavioral tests land in Task 5 — the suite is migrated wholesale there)

### Task 3: Model field + migration

**Files:**
- Modify: `src/apps/mobile/models.py`
- Create: `src/apps/mobile/migrations/0003_appinstall_key_id.py` (generated)

- [ ] add `key_id = models.CharField(max_length=32, blank=True)` to `AppInstall`
- [ ] generate: `uv run python src/manage.py makemigrations mobile` (verify `0003`, single `AddField`, depends on `0002_drop_install_id_db_index`)
- [ ] apply: `uv run python src/manage.py migrate`
- [ ] `uv run python src/manage.py makemigrations --check --dry-run` is clean
- [ ] (model is covered by the stats-recording test in Task 5)

### Task 4: Stats wiring + admin

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/admin.py`

- [ ] in `_record_install`, add `key_id=meta.get("key_id", "")` to the `update_or_create` `defaults`
- [ ] in `AppInstallAdmin`: add `"key_id"` to `list_display`, add `list_filter = ("key_id", "platform")`, add `"key_id"` to `readonly_fields`
- [ ] (admin has no behavior tests in this suite; the field write is asserted in Task 5)

### Task 5: Migrate test suite + new rotation cases

**Files:**
- Modify: `src/apps/mobile/tests.py`

- [ ] extend **`_signed_headers(...)`** (line 225) with a `key_id="test-v1"` param; emit `X-App-Key-Id` in the `HTTP_X_*` dict
- [ ] extend **`_signed_get_request(...)`** (line 98) with a `key_id="test-v1"` param; set the `X-App-Key-Id` header on the `RequestFactory` request (this helper drives the direct-permission unit tests at lines 116-196)
- [ ] replace every `settings.MOBILE_APP_SECRET = SECRET` (~60 sites, **both** the direct-permission tests at 116-196 and the client tests) with `settings.MOBILE_APP_KEYS = {"test-v1": SECRET}`
- [ ] migrate the fail-closed cases: `test_permission_empty_secret_fails_closed` (117) and `test_legend_empty_secret_fails_closed` (321) → `settings.MOBILE_APP_KEYS = {}`
- [ ] **`test_permission_none_secret_fails_closed` (123)**: there is no "None secret" analogue under the map model — repurpose it as `test_permission_unset_keys_fails_closed` setting `settings.MOBILE_APP_KEYS = {}` (the `getattr(..., {}) or {}` unset path), or delete it if redundant with the empty-`{}` case. Decide during impl; do not leave it setting the removed `MOBILE_APP_SECRET`.
- [ ] write test: **two active keys** (`{"android-v1": S1, "ios-v1": S2}`) — a request signed with `S1`+`android-v1` AND a request signed with `S2`+`ios-v1` both return 200 (the rotation-overlap proof)
- [ ] write test: unknown `key_id` (not in map) → 403 `{"detail": "Forbidden"}`
- [ ] write test: missing `X-App-Key-Id` header → 403 (pass `key_id=None`/strip the header)
- [ ] write test: valid `key_id` but signature made with the **wrong** secret → 403
- [ ] write test: empty `MOBILE_APP_KEYS` (`{}`) → 403 (fail-closed)
- [ ] write test: a verified request records `AppInstall.key_id` == the request's key-id
- [ ] run `uv run pytest src/apps/mobile/tests.py` — full mobile suite green (clears the Task 1 failures)

### Task 6: Verify acceptance criteria
- [ ] verify rotation works: two active keys both verify; removing a key 403s requests using it
- [ ] verify fail-closed: empty/malformed `MOBILE_APP_KEYS` → every request 403
- [ ] verify neutral 403 (no hint) for missing/unknown key-id, bad sig, expired ts
- [ ] verify `signing.py` has no logic/canonical change (docstring-only) and its unit tests are untouched and still pass
- [ ] run full suite: `uv run pytest`
- [ ] run `make format && make lint`
- [ ] confirm `uv run python src/manage.py makemigrations --check --dry-run` is clean

### Task 7: [Final] Update documentation
- [ ] `deploy/kolco24.env.example`: replace `MOBILE_APP_SECRET=` with the `MOBILE_APP_KEYS={...}` example + rotation comment
- [ ] **root `README.md:39`**: update the stale `MOBILE_APP_SECRET` setup comment → `MOBILE_APP_KEYS` (JSON map, empty/malformed → all `/app/*` return 403)
- [ ] `src/apps/mobile/README.md`: update line 4 (`MOBILE_APP_SECRET` → keyed map) and the `X-App-*` header note (line 8, add `X-App-Key-Id`); document the rotation workflow + `AppInstall.key_id` (this README is prose, not a header table)
- [ ] `CLAUDE.md` apps.mobile paragraph: replace `MOBILE_APP_SECRET` with the keyed map, add `X-App-Key-Id` to the **header enumeration** (the `X-Install-Id`/`X-App-Platform`/… list — the only place headers are enumerated), note `AppInstall.key_id` + migration `0003`, keep the "canonical unchanged / key-id selects the secret" rationale
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — no checkboxes, informational only.*

**External system updates:**
- Generate a distinct strong secret per app build (`android-v1`, `ios-v1`, …) and
  put them all in `MOBILE_APP_KEYS` in `deploy/kolco24.env`. Each value must match
  the secret compiled into the corresponding client build.
- Hand the mobile devs the client recipe: the binary ships its own `key_id` + the
  paired secret, sends `X-App-Key-Id` plus the existing signing headers; the
  canonical string is unchanged.

**Rotation runbook (operational):**
1. Build the new app with key-id `*-v2` and a fresh secret; add `*-v2` to
   `MOBILE_APP_KEYS` alongside `*-v1`; deploy. Both verify.
2. Ship the new app; wait until the `AppInstall` admin shows no installs still
   reporting `*-v1` (filter by `key_id`).
3. Remove `*-v1` from `MOBILE_APP_KEYS`; deploy. Stragglers on the old build now
   get 403 (expected end-of-life).
