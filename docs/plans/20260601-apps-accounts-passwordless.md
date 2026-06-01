# apps/accounts: move auth + email-first passwordless login

## Overview

Competitions are infrequent; users get logged out and a year later don't remember
whether they ever registered. The "Добавить команду" button must not force a
Вход-vs-Регистрация choice. This plan:

1. **Creates `src/apps/accounts/`** — a new Django app and moves ALL existing auth
   into it (login, register, logout, password reset, impersonate, `EmailBackend`,
   the auth forms + templates), keeping behavior identical.
2. **Adds an email-first passwordless flow**: the user enters an email; we send ONE
   email containing both a **6-digit code** and a **magic link** backed by a single
   `EmailVerification` row. Either path logs them in — creating the account inline if
   the email is new, logging in if it exists. No account-enumeration leak.

Password login is kept as a secondary option; the passwordless flow becomes the
promoted entry point.

**Problem it solves**: removes the dead-end where a returning user hits "email уже
занят" on register or "забыл пароль" on login. **Integration**: `AddTeam` already
redirects anon users to `login?next=…`; the race-page button will point at the new
`account_start` entry so the whole "Добавить команду → email → код/ссылка → команда"
path works with `?next=` preserved end-to-end.

## Context (from discovery)

**History — read before implementing:** A self-rolled magic-link login (`FastLogin`
on `/login/`) was **removed** on 2026-05-28 (see
`docs/plans/completed/20260528-remove-magic-link-login.md`) and replaced by the
current password `LoginView`. The old one was key-link-only with no OTP, no unified
new/existing logic, and no anti-enumeration. This plan is a deliberate, more complete
re-introduction — NOT a revival. The old `send_login_email`/`FastLoginForm`/
`FastLogin` were deleted, so there is **no name collision** with the new
`apps/accounts/emails.py:send_login_email` or model.

**Code to move (current locations):**
- `src/website/views/views_.py`: `LoginView` (~629), `RegisterView` (~186),
  `LogoutUserView` (~663), `impersonate` (~710), `stop_impersonate` (~763), plus
  shared helpers `_safe_redirect` (def ~678), `_get_auth_backend`,
  `_login_without_credentials`, `_find_user_for_impersonation`, `_mark_field_invalid`.
  ✓ Verified: all 7 `_safe_redirect` call sites (232, 634, 645, 655, 751, 756, 782) are
  inside these auth views; `AddTeam`/`TeamPayment` do NOT use it (they redirect with
  `reverse("login") + "?next=…"`). So `_safe_redirect` moves WHOLLY into `accounts` and
  is deleted from `website` — no copy, no dead code.
- `src/website/views/auth.py`: `CustomPasswordResetView` / `…DoneView` /
  `…ConfirmView` / `…CompleteView`.
- `src/website/auth.py`: `EmailBackend` (authenticates by `email__iexact`).
- `src/website/forms.py`: `LoginForm`, `RegForm`, `CustomPasswordResetForm`,
  `CustomSetPasswordForm`, `ImpersonateForm` (line 28 has a hardcoded `/login/`).
- Templates `src/templates/website/{login,register,password_reset*,impersonate}.html`.
- `src/website/urls.py` (auth `path()` entries + their imports);
  `src/website/views/__init__.py` (export list).

**Patterns / conventions found:**
- `src/apps/race/` is the precedent feature app (`apps.py` with `label="race_app"`,
  views-only). `accounts` mirrors the layout but **has a model** (gets migrations).
- Templates: common dir `src/templates/` with `APP_DIRS=True`; new auth templates go
  to `src/templates/accounts/` (like `templates/demo/`). They extend `base-2.html` and
  write form fields **manually** (no `{{ form.field }}` — Bootstrap class conflicts;
  see CLAUDE.md). `from_email = "Кольцо24 <org@kolco24.ru>"` matches the reset view.
- Email goes through `EMAIL_BACKEND = "mailer.backend.DbBackend"` (django-mailer,
  delivered by the `kolco24_runmailer` container). `DbBackend` queues to its own
  `Message` table and does NOT populate `django.core.mail.outbox` — email tests
  override the backend to locmem (see Testing Strategy).
- `auth_user.email` has a case-insensitive unique index (migration `0065`); user
  creation must wrap `create_user` in `transaction.atomic()` and catch `IntegrityError`.
- Tests are pytest-style with `@pytest.mark.django_db`, `client`/`django_user_model`
  fixtures. `DJANGO_SETTINGS_MODULE = config.settings` (set in `pyproject.toml`).
  pytest needs `src` on the path (run from repo root via `uv run pytest`).

**Dependencies identified:** `LOGIN_URL = "login"`, ~14 `reverse("login")` call sites,
~20 `{% url 'login' %}`/etc. in templates — ALL name-based, so keeping URL names flat
means they need no changes. Only `website/forms.py:28` hardcodes `/login/`.

## Development Approach

- **testing approach**: **Regular** (code first, then tests) — consistent with the
  repo's completed plans and the refactor-heavy nature of Tasks 1–3.
- For the pure-move tasks (1–3), the required "tests" are the **existing full suite
  staying green** (regression). Behavior of the views (responses, redirects-by-name,
  auth) is unchanged, but URL *paths* move to `/accounts/*`, so the ~15 hardcoded path
  literals in `src/website/tests.py` are updated accordingly in Task 3 — an expected,
  mechanical test change, not a behavior change in the views.
- For the new feature tasks (4–6), every task ends with new unit tests (success +
  error/edge cases) before moving on.
- **CRITICAL: all tests must pass before starting the next task.**
- **CRITICAL: update this plan file when scope changes during implementation.**
- Gate every commit with `make format && make lint && uv run pytest` (standing rule).
- Make small, focused changes; maintain backward compatibility (URL names, imports).

## Testing Strategy

- **unit tests**: required for Tasks 4–6 (model, email helper, passwordless views).
  Pytest-style, `@pytest.mark.django_db`. ⚠️ The project's `EMAIL_BACKEND` is
  django-mailer's `DbBackend`, which queues to its own `Message` table and does NOT
  populate `django.core.mail.outbox`. Email tests must decorate with
  `@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")`
  to assert on `mail.outbox` (the simpler path; alternatively assert on
  `mailer.models.Message.objects`).
- **regression tests**: existing `src/website/tests.py` and `src/apps/race/tests.py`
  must stay green after the move (`reverse("login"/"register"/…)` now resolve to
  `/accounts/*`; password login + impersonate still work).
- **e2e tests**: project has no Playwright/Cypress suite — none added.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope

## Solution Overview

- New `apps.accounts` app holds all auth + the passwordless flow.
- **URL strategy**: paths move to `/accounts/*`; **URL names stay flat and unchanged**
  (`login`, `register`, `logout`, `password_reset*`, `impersonate`) → no namespace, no
  redirects from old paths, no churn in `reverse()`/templates/`LOGIN_URL`.
- **Storage**: one DB model `EmailVerification` (Approach A — chosen over stateless
  signing / cache because only a row cleanly supports code + link + attempt-limiting +
  revocation together).
- **One join point** `_complete_login()` is shared by the code path and the link path:
  mark consumed → look up user by email → log in (exists) or create inline (new) →
  safe-redirect to `next`.

## Technical Details

**`EmailVerification` (`apps/accounts/models.py`):**
- `email` (`EmailField`, `db_index`, stored normalized lower-case), `code_hash`
  (`CharField(128)`, `make_password` of the 6-digit code — raw code never stored),
  `purpose` (`CharField`, default `"login"`), `created_at` (`auto_now_add`),
  `expires_at` (`DateTimeField`), `attempts` (`PositiveSmallIntegerField`, default 0),
  `consumed_at` (nullable). `Meta.indexes`: `(email, purpose, consumed_at)`.
- Constants: `CODE_TTL = timedelta(minutes=15)`, `MAX_ATTEMPTS = 5`,
  `RESEND_COOLDOWN = timedelta(seconds=60)`.
- Code: 6 digits via `secrets.randbelow(1_000_000)` zero-padded; verified with
  `check_password`.
- Magic link carries **no token column** — the URL embeds
  `TimestampSigner().sign(str(pk))`; the row's `expires_at`/`consumed_at` enforce
  lifetime + single use.
- Methods: `create_for(email, purpose="login") -> (obj, raw_code | None)` (refuses a
  new code and returns `(existing, None)` if an alive row was created < 60 s ago —
  anti-bombing), `verify_code(raw) -> bool` (increments `attempts`, rejects when dead),
  `mark_consumed()`, `is_alive` property (`consumed_at is None and now < expires_at and
  attempts < MAX_ATTEMPTS`). Email normalized via `BaseUserManager().normalize_email`
  + `.lower()`.

**Flow / URLs (`/accounts/…`, all carry `?next=`):**
- `account_start` `start/` — GET email form; POST → `create_for` → `send_login_email`
  → stash `accounts_pending_email` + `accounts_next` in session → redirect to verify.
  Neutral response for known/unknown email.
- `account_verify` `verify/` — GET shows "код отправлен на <email>" + code field +
  resend; POST → `verify_code` → `_complete_login(next from session)`.
- `magic_link` `link/<signed>/` — GET → unsign pk (BadSignature → reject), load row,
  `is_alive` → `_complete_login(next from query param)`. (Link must NOT rely on the
  session — it may open in another browser.) Only the pk is signed; `next` rides as an
  unsigned query param and is safe because `_complete_login` → `_safe_redirect`
  validates it against `request.get_host()` (a tampered `next` falls back to `/`). Do
  NOT sign `next` into the token.
- resend action: call `create_for`; if it returns a raw code, `send_login_email` again;
  if it returns `(existing, None)` (within the 60 s cooldown — no raw code is
  recoverable since only the hash is stored), send nothing and show a neutral "письмо
  уже отправлено, проверьте почту" message. Stays neutral re: enumeration.
- Anon-only guard on start/verify bounces authed users to `next`.

**`_complete_login(request, email, next_url)`:** `mark_consumed()`; `User` by
`email__iexact` → `auth_login` (backend `apps.accounts.backends.EmailBackend`); if
none, create inline in `transaction.atomic()` catching `IntegrityError`, unguessable
password (`django.utils.crypto.get_random_string(32)` — `make_random_password` is
deprecated in Django 4.2), username from email local-part
deduped like `RegisterView.get_next_username`; profile row auto-created by the existing
signal. Then app-local `_safe_redirect(request, next_url or "/")`. No name/phone step.

**Email (`apps/accounts/emails.py:send_login_email(request, verification, code,
next_url)`):** `EmailMultiAlternatives` (`.txt` + `.html` alt) via the existing
django-mailer backend; subject from `login_code_subject.txt`. Magic-link absolute URL
via `request.build_absolute_uri(reverse("magic_link", args=[signed]))` with `next`
appended as a query param. Send failure is logged; the user still reaches verify.

## What Goes Where

- **Implementation Steps** (`[ ]`): all code, templates, migration, tests, docs.
- **Post-Completion** (no checkboxes): prod `migrate`, manual smoke of the email in a
  running `runmailer`, optional follow-up to repoint the race-page button copy.

## Implementation Steps

### Task 1: Scaffold `apps/accounts` + wiring + move `EmailBackend`

**Files:**
- Create: `src/apps/accounts/__init__.py`
- Create: `src/apps/accounts/apps.py`
- Create: `src/apps/accounts/backends.py`
- Create: `src/apps/accounts/migrations/__init__.py`
- Modify: `src/website/auth.py`
- Modify: `src/config/settings.py`

- [x] create `apps.py` → `AccountsConfig(name="apps.accounts", label="accounts",
      verbose_name="Accounts")`
- [x] move `EmailBackend` from `src/website/auth.py` into
      `src/apps/accounts/backends.py` verbatim
- [x] replace `src/website/auth.py` body with shim:
      `from apps.accounts.backends import EmailBackend  # noqa: F401`
- [x] `settings.py`: add `"apps.accounts"` to `INSTALLED_APPS`; change
      `AUTHENTICATION_BACKENDS` entry to `"apps.accounts.backends.EmailBackend"`
- [x] run `uv run python src/manage.py check` — no errors
- [x] regression: `uv run pytest` — full suite green (auth still works via shim)

### Task 2: Move auth forms with re-exports

**Files:**
- Create: `src/apps/accounts/forms.py`
- Modify: `src/website/forms.py`

- [ ] move `LoginForm`, `RegForm`, `CustomPasswordResetForm`, `CustomSetPasswordForm`,
      `ImpersonateForm` (and their imports/helpers) into `apps/accounts/forms.py`
- [ ] fix the hardcoded `/login/` (was `website/forms.py:28`) to `/accounts/login/`
- [ ] re-export from `src/website/forms.py`:
      `from apps.accounts.forms import (LoginForm, RegForm, CustomPasswordResetForm,
      CustomSetPasswordForm, ImpersonateForm)  # noqa: F401`
- [ ] run `uv run python src/manage.py check` — no errors
- [ ] regression: `uv run pytest` — full suite green (existing form imports unaffected)

### Task 3: Move auth views + helpers + templates; rewire URLs (pure refactor)

**Files:**
- Create: `src/apps/accounts/views.py`
- Create: `src/apps/accounts/urls.py`
- Create: `src/templates/accounts/{login,register,password_reset,password_reset_done,password_reset_confirm,password_reset_complete,impersonate}.html`
- Modify: `src/website/views/__init__.py`
- Modify: `src/website/views/views_.py`
- Modify: `src/website/urls.py`
- Modify: `src/config/urls.py`
- Delete: `src/website/views/auth.py`
- Delete: `src/templates/website/{login,register,password_reset*,impersonate}.html`

- [ ] move `LoginView`, `RegisterView`, `LogoutUserView`, `impersonate`,
      `stop_impersonate` + the password-reset CBVs into `apps/accounts/views.py`
- [ ] move the private helpers (`_safe_redirect`, `_get_auth_backend`,
      `_login_without_credentials`, `_find_user_for_impersonation`,
      `_mark_field_invalid`) wholly into the app and delete them from
      `website/views/views_.py` (verified: no `website` view calls them — all 7
      `_safe_redirect` sites are in the moved auth views)
- [ ] move the auth templates to `src/templates/accounts/`; point reset CBV
      `template_name` attrs at the new paths (keep `registration/password_reset_email.*`
      where Django's reset machinery expects them)
- [ ] create `apps/accounts/urls.py` with the auth routes using **flat unchanged
      names** (`login`, `register`, `logout`, `password_reset`, `password_reset_done`,
      `password_reset_confirm`, `password_reset_complete`, `impersonate`,
      `stop_impersonate`)
- [ ] `config/urls.py`: add `path("accounts/", include("apps.accounts.urls"))`
- [ ] `website/urls.py`: remove the auth `path()` entries and now-unused imports
      (`CustomPasswordReset*`, etc.)
- [ ] `website/views/__init__.py`: drop the moved names from the export list; delete
      `website/views/auth.py`; delete the moved classes/functions from `views_.py`
- [ ] update the ~15 hardcoded auth path literals in `src/website/tests.py` (lines
      231, 300, 307, 315, 326, 336, 351, 362, 370, 378, 382, 387, 395, 405, 1358):
      `/login/` → `/accounts/login/`, `/register/` → `/accounts/register/` (or switch
      to `reverse(...)`) — expected, mechanical change from the path move
- [ ] run `uv run python src/manage.py check` and `make lint` — catches dead imports
- [ ] regression: `uv run pytest` — full suite green; spot-check
      `reverse("login")`/`reverse("password_reset")` resolve under `/accounts/`. This
      green run marks the pure-refactor portion complete.

### Task 4: Add `EmailVerification` model + migration

**Files:**
- Create: `src/apps/accounts/models.py`
- Create: `src/apps/accounts/migrations/0001_initial.py`
- Create: `src/apps/accounts/tests.py`

- [ ] implement `EmailVerification` with the fields, constants, and `Meta.indexes`
      from Technical Details
- [ ] implement `create_for` (with 60 s resend cooldown reuse), `verify_code`
      (attempt counting, dead-row rejection), `mark_consumed`, `is_alive`, and email
      normalization
- [ ] `uv run python src/manage.py makemigrations accounts` →
      `0001_initial`; review it (`CreateModel` + index)
- [ ] `uv run python src/manage.py migrate`
- [ ] write tests: `create_for` issues a hashed 6-digit code; `verify_code` accepts the
      right code, rejects wrong code while incrementing `attempts`, rejects after
      `MAX_ATTEMPTS`, rejects when expired, rejects when consumed
- [ ] write tests: second `create_for` within 60 s reuses the row and returns no new
      raw code; email is normalized lower-case
- [ ] run `uv run pytest src/apps/accounts/tests.py` — must pass before next task

### Task 5: Email helper + templates

**Files:**
- Create: `src/apps/accounts/emails.py`
- Create: `src/templates/accounts/email/login_code_subject.txt`
- Create: `src/templates/accounts/email/login_code.txt`
- Create: `src/templates/accounts/email/login_code.html`
- Modify: `src/apps/accounts/tests.py`

- [ ] implement `send_login_email(request, verification, code, next_url)` building the
      signed magic-link absolute URL (with `next` query param) and rendering both alts
- [ ] write the three templates: prominent 6-digit code, magic-link button, "ссылка и
      код действуют 15 минут", "если вы не запрашивали — проигнорируйте"
- [ ] wrap `send()` so a send failure is logged but does not raise
- [ ] write tests (under `@override_settings(EMAIL_BACKEND=
      "django.core.mail.backends.locmem.EmailBackend")`): the helper queues exactly one
      message in `mail.outbox`; body contains the code and the signed link URL; the
      link round-trips through `TimestampSigner().unsign` to the row pk
- [ ] run `uv run pytest src/apps/accounts/tests.py` — must pass before next task

### Task 6: Passwordless views + URLs + templates + secondary-path links

**Files:**
- Modify: `src/apps/accounts/views.py`
- Modify: `src/apps/accounts/urls.py`
- Modify: `src/apps/accounts/forms.py`
- Create: `src/templates/accounts/start.html`
- Create: `src/templates/accounts/verify.html`
- Modify: `src/templates/accounts/login.html`
- Modify: `src/apps/accounts/tests.py`

- [ ] add `EmailStartForm` (email) and `CodeForm` (code) to `apps/accounts/forms.py`
- [ ] implement `StartView` (`account_start`), `VerifyView` (`account_verify`),
      `MagicLinkView` (`magic_link`), and the resend action; add the shared
      `_complete_login(request, email, next_url)` join point (inline user creation with
      `IntegrityError` handling + username dedup)
- [ ] wire the three URLs in `apps/accounts/urls.py`; anon-only guard on start/verify
- [ ] `start.html` (email form, low-emphasis "Войти по паролю" → `login?next=…`) and
      `verify.html` (code field + resend); add "Войти по коду из письма" →
      `account_start?next=…` link in `login.html` (manual fields, `base-2.html`)
- [ ] write tests (email assertions under the locmem `@override_settings`): `start`
      POST creates one row, queues one email (code + link), neutral response for known
      vs unknown email (no enumeration); resend within 60 s queues NO second email and
      stays neutral
- [ ] write tests: `verify` correct code logs in an existing user and redirects to
      `next`; unknown email creates user + profile and logs in; wrong code increments
      attempts; expired row rejected
- [ ] write tests: `magic_link` valid → login; tampered signature rejected;
      expired/consumed/reused rejected; `next` honored via both code and link;
      off-host `next` rejected by `_safe_redirect`; authed user at `start` bounced
- [ ] run `uv run pytest src/apps/accounts/tests.py` — must pass before next task

### Task 7: Verify acceptance criteria

**Files:** —

- [ ] verify Overview requirements: email-first entry, code + link from one row,
      inline create-or-login, `?next=` preserved end-to-end, no enumeration
- [ ] verify regression: `reverse("login"/"register"/"logout"/"password_reset")`
      resolve under `/accounts/`; password login still authenticates; impersonate
      still works; `apps/race/tests.py` "Войти и добавить команду" + anon-redirect
      tests still pass
- [ ] run full suite: `uv run pytest`
- [ ] run `make format && make lint` — clean

### Task 8: [Final] Update documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/plans/20260601-apps-accounts-passwordless.md` (this file)

- [ ] add a CLAUDE.md "Auth" note: auth now lives in `apps.accounts` (paths
      `/accounts/*`, flat URL names unchanged); the passwordless flow
      (`account_start`/`account_verify`/`magic_link`), `EmailVerification` model, and
      the `website/auth.py` + `website/forms.py` re-export shims
- [ ] update the CLAUDE.md "Architecture/Apps" list to mention `apps.accounts`
- [ ] move this plan: `mkdir -p docs/plans/completed && mv
      docs/plans/20260601-apps-accounts-passwordless.md docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention or external systems — informational only.*

**Deploy:**
- Run `manage.py migrate` on prod (creates `accounts_emailverification`). No data
  migration; no new env vars (`from_email` + django-mailer already configured).
- After deploy, smoke-test the email with a running `kolco24_runmailer`: request a code
  on `/accounts/start/`, confirm the email arrives with a working code and link.

**Manual verification:**
- Old paths `/login/`, `/register/` now 404 (no redirects, by decision) — confirm no
  external bookmarks/integrations depend on them.
- Verify the magic link works when opened in a different browser (no session reliance).

**Follow-up (out of scope here):**
- Optionally repoint the race-page "Войти и добавить команду" button at
  `account_start` instead of `login` and refresh the copy (small template tweak +
  update `apps/race/tests.py` assertions).
- Post-login name/phone capture step, code-cleanup cron, SMS/social login — deferred.
