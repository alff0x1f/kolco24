# apps.accounts

Loaded on demand when working in this directory. Root rules are in `/CLAUDE.md`.

`apps.accounts` — all authentication (`AppConfig` `label="accounts"`), mounted at `/accounts/*` via `config/urls.py`.
Holds the moved password auth (`LoginView`, `RegisterView`, `LogoutUserView`, `impersonate`/`stop_impersonate`, the
`CustomPasswordReset*` CBVs, `EmailBackend`, the auth forms, and the private redirect/auth helpers incl.
`_safe_redirect`) plus an email-first passwordless flow. URL names stay flat and unchanged (`login`, `register`,
`logout`, `password_reset*`, `impersonate`, `stop_impersonate`) — only the paths moved, so `reverse()`/`{% url %}`/
`LOGIN_URL` need no changes. `website/auth.py` and `website/forms.py` keep thin re-export shims (
`from apps.accounts.backends import EmailBackend`; `from apps.accounts.forms import ...`) so old import sites still
work. Templates live in `src/templates/accounts/` (extend `base-2.html`, manual form fields); Django's reset emails
stay in `templates/registration/password_reset_email.*`. See the **Auth** note below for the passwordless flow +
`EmailVerification` model.

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
