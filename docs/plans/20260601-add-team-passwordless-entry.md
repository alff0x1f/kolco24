# Route the "Войти и добавить команду" path through passwordless login

## Overview

On the race page (`/race/<slug>/`) the anon CTA «Войти и добавить команду» links to
`add_team`; `AddTeam` then redirects logged-out users to the **password** `login`
page — the exact Вход-vs-Регистрация dead-end the passwordless work set out to kill.
This plan repoints that one entry to the email-first passwordless flow
(`account_start` → `account_verify` / `magic_link`) and gives the passwordless pages
race-aware copy so the journey reads «добавить команду на «<гонка>» → email → код/ссылка
→ форма команды».

**Problem it solves:** a returning user who forgot whether they ever registered no
longer hits a password wall. **Integration:** `?next=request.path` is already preserved
end-to-end by the passwordless views and `_complete_login`'s `_safe_redirect`, so after
login the user lands straight back on `/race/<slug>/teams/add/`. No new auth machinery —
this is the deferred follow-up of
`docs/plans/completed/20260601-apps-accounts-passwordless.md` (read it for the full
passwordless design: `EmailVerification`, the three views, `_complete_login`,
`send_login_email`).

## Context (from discovery)

**Files/components involved:**
- `src/website/views/views_.py` — `AddTeam.get` (line ~1596) and `AddTeam.post`
  (line ~1615) both do `HttpResponseRedirect(reverse("login") + f"?next={request.path}")`.
- `src/apps/accounts/views.py` — `StartView` (`account_start`, reads `next` from
  GET/POST), `VerifyView` (`account_verify`, reads `next` from session key
  `PENDING_NEXT_KEY = "accounts_next"`), `MagicLinkView` (`magic_link`, email-only, no
  page — left untouched). Already imports `from website.models import Race`.
- `src/templates/accounts/start.html`, `verify.html` — extend `website/base-2.html`,
  reuse `.login-page`/`.login-wrap`/`.card`/`.card-pad`/`.sub`/`.below` from
  `src/static/css/theme-2.css` (no per-page CSS file). Manual form fields (no
  `{{ form.field }}`), per CLAUDE.md.
- `src/apps/race/tests.py` — `test_race_page_anon_sees_login_and_add_button`
  (line ~505) asserts the button points at `reverse("add_team")` and that «Войти и
  добавить команду» is present.

**Related patterns found:**
- Race display field is `race.name`. URL names `race` (`race/<slug>/`) and `add_team`
  (`race/<slug>/teams/add/`) both take kwarg `race_slug`. The back link is rendered in
  templates as `{% url 'race' race.slug %}`; tests assert it via the equivalent
  `reverse("race", args=[slug])`.
- `?next=` is carried through `StartView` → session → `VerifyView` /
  `MagicLinkView` → `_complete_login` → `_safe_redirect(request, next_url or "/")`
  (validated against `request.get_host()`).

**Dependencies identified:** none new. No model, migration, settings, or `LOGIN_URL`
change. Other `reverse("login")` sites (TeamPayment, EditTeamView, transfer/delete,
race-admin) are explicitly **out of scope** and stay on password login.

## Development Approach

- **testing approach:** Regular (code first, then tests), consistent with the repo's
  completed plans. Tests are pytest-style functions with `@pytest.mark.django_db` and
  `client`/`django_user_model` fixtures (not `TestCase`), living in `src/<app>/tests.py`.
- complete each task fully before the next; make small, focused changes.
- **CRITICAL: every task includes new/updated tests** (success + error/edge cases).
- **CRITICAL: all tests pass before starting the next task.**
- **CRITICAL: update this plan file if scope changes during implementation.**
- Gate every commit with `make format && make lint && uv run pytest` (run pytest from
  `src/`). Maintain backward compatibility (URL names, the unchanged button copy).

## Testing Strategy

- **unit tests:** required per task. Pytest-style, `@pytest.mark.django_db`. The
  redirect-target assertions need no email backend override.
- **recovering the 6-digit code in tests:** the raw code is **never stored** — the row
  keeps only `code_hash = make_password(raw_code)` (`apps/accounts/models.py`), and a
  second `create_for(email)` inside the 60 s `RESEND_COOLDOWN` returns `(existing, None)`.
  So a test that has already `client.post`ed to `account_start` **cannot** read the code
  off the DB row. Two valid patterns, pick per test:
  - **(a) parse the email** — decorate with `@override_settings(EMAIL_BACKEND=
    "django.core.mail.backends.locmem.EmailBackend")` (django-mailer's `DbBackend` does
    NOT populate `mail.outbox`); the start POST queues exactly one message, so extract the
    6 digits from `mail.outbox[0].body`. Use this for the true end-to-end path that drives
    `account_start`.
  - **(b) own the `create_for` call** — do NOT POST to `account_start`; instead call
    `obj, raw_code = EmailVerification.create_for(email)` in the test, set
    `session["accounts_pending_email"]`/`session["accounts_next"]`, and POST `raw_code` to
    `account_verify` (mirrors `tests.py:test_verify_correct_code_logs_in_existing_user_*`).
    Faster; use for verify-only assertions.
- **regression:** `src/apps/race/tests.py` anon-CTA test stays green (button still →
  `add_team`); full suite stays green.
- **e2e tests:** project has no Playwright/Cypress suite — none added.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope

## Solution Overview

- **Server-side repoint (chosen):** only `AddTeam`'s two anon redirects change from
  `reverse("login")` to `reverse("account_start")`. The race-page button and its copy
  are untouched; `?next=request.path` is unchanged. This covers GET, POST, and deep
  links with one minimal change and reuses the already-styled passwordless pages.
  (Rejected: inline email capture on the race page — more JS/design; repointing the
  button URL too — redundant with the view redirect and two places to sync.)
- **Race-aware copy (chosen):** a display-only helper resolves the `next` path back to
  its `Race` so `start`/`verify` can greet the user with the race they're registering
  for plus a back link. A garbage/non-`add_team` `next` yields `None` → today's generic
  copy. No auth decision depends on this lookup.

## Technical Details

**`_race_from_next(next_url)` (new, `src/apps/accounts/views.py`):**
- `from urllib.parse import urlsplit`; `from django.urls import resolve, Resolver404`.
- `path = urlsplit(next_url or "").path`; if falsy → return `None`.
- `try: match = resolve(path) except Resolver404: return None`.
- if `match.url_name != "add_team"` → return `None`.
- `slug = match.kwargs.get("race_slug")`; return `Race.objects.filter(slug=slug).first()`
  (`None` if missing). Display-only; never raises into the request.

**`AddTeam` repoint (`src/website/views/views_.py`):** in both `get` (~1596) and `post`
(~1615) replace `reverse("login")` with `reverse("account_start")`, keeping
`+ f"?next={request.path}"` verbatim.

**View context:** `StartView.get`/`.post` compute `race = _race_from_next(next_url)`
and add `"race": race` to the render context (alongside the existing `form`/`next`).
`VerifyView.get`/`.post` compute `race = _race_from_next(request.session.get(
PENDING_NEXT_KEY, ""))` and add `"race": race`. `MagicLinkView` unchanged.

**Templates (`start.html`, `verify.html`):**
- In the `.page-head`, when `race` is set, render the contextual sub-line
  `чтобы добавить команду на «{{ race.name }}»` (keep the existing generic `<p class="sub">`
  when `race` is unset — wrap in `{% if race %} … {% else %} … {% endif %}`).
- Under the card (inside/near the existing `.below` block) add, when `race` is set,
  `<a class="back-link" href="{% url 'race' race.slug %}">← Назад к гонке</a>`.
- Reuse existing theme-2.css classes; add at most a small `.back-link` rule to
  `theme-2.css` if the link needs spacing/color — no new stylesheet, no `.page` override.

## What Goes Where

- **Implementation Steps** (`[ ]`): the redirect repoint, the helper + context wiring,
  the template copy, tests, and the CLAUDE.md note.
- **Post-Completion** (no checkboxes): manual smoke of the full path against a running
  `runmailer`, and a note that other login-gated team actions still use password login.

## Implementation Steps

### Task 1: Repoint AddTeam anon redirects to passwordless start

**Files:**
- Modify: `src/website/views/views_.py`
- Modify: `src/apps/accounts/tests.py`

- [x] in `AddTeam.get` (~1596) change the anon redirect target from `reverse("login")`
      to `reverse("account_start")`, keeping `+ f"?next={request.path}"`
- [x] in `AddTeam.post` (~1615) make the same change
- [x] write test: anon `GET add_team` now 302s to a URL containing
      `reverse("account_start")` and `?next=<add_team path>` (and NOT `reverse("login")`)
- [x] write test: anon `POST add_team` likewise 302s to `account_start` with `?next=`
- [x] run `uv run pytest src/apps/accounts/tests.py` (from `src/`) — must pass before
      next task

### Task 2: Add `_race_from_next` helper + wire race context into start/verify views

**Files:**
- Modify: `src/apps/accounts/views.py`
- Modify: `src/apps/accounts/tests.py`

- [x] add `_race_from_next(next_url)` per Technical Details (urlsplit → `resolve` →
      guard `url_name == "add_team"` → `Race.objects.filter(slug=…).first()`;
      `try/except Resolver404`; returns `None` on any miss)
- [x] add the needed imports (`from urllib.parse import urlsplit`;
      extend the `django.urls` import with `resolve, Resolver404`)
- [x] in `StartView.get` and `.post` compute `race = _race_from_next(next_url)` and add
      `"race": race` to the template context
- [x] in `VerifyView.get` and `.post` compute
      `race = _race_from_next(request.session.get(PENDING_NEXT_KEY, ""))` and add
      `"race": race` to the template context
- [x] write test: `_race_from_next` returns the Race for a valid `add_team` path,
      `None` for a non-`add_team` path, `None` for a garbage/unresolvable string, and
      `None` for an empty value
- [x] write test: `GET account_start?next=<add_team url>` puts the Race in context
      (race-in-context asserted; the rendered-HTML `race.name` assertion lands in Task 3
      once the templates render it); a garbage `next` renders 200 with no race
- [x] run `uv run pytest src/apps/accounts/tests.py` (from `src/`) — must pass before
      next task

### Task 3: Race-aware copy in start.html and verify.html

**Files:**
- Modify: `src/templates/accounts/start.html`
- Modify: `src/templates/accounts/verify.html`
- Modify: `src/static/css/theme-2.css` (only if a `.back-link` rule is needed)
- Modify: `src/apps/accounts/tests.py`

- [x] `start.html`: in `.page-head`, `{% if race %}` show
      `чтобы добавить команду на «{{ race.name }}»` else keep the current generic `.sub`
- [x] `start.html`: in the `.below` area, `{% if race %}` add
      `<a class="back-link" href="{% url 'race' race.slug %}">← Назад к гонке</a>`
- [x] `verify.html`: apply the same two `{% if race %}` additions (contextual sub-line +
      back link), keeping the existing «Код отправлен на …» line
- [x] add a minimal `.back-link` rule to `theme-2.css` only if spacing/color needs it
      (no new stylesheet, no bare `.page` override) — not needed: `.below a` already
      styles the link (centering/color/weight); back link wrapped in `.below`
- [x] write test: `GET account_start?next=<add_team url>` renders both the contextual
      sub-line text and the `reverse("race", args=[slug])` href; with no/garbage `next`
      neither appears and the page is 200
- [x] write test (end-to-end, pattern (a) from Testing Strategy): under
      `@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")`,
      anon `GET add_team` → follow to `account_start` → `POST` the email → extract the
      6-digit code from `mail.outbox[0].body` → `POST` it to `account_verify` → assert the
      final redirect lands on the `add_team` path and the session is authenticated
- [x] run `uv run pytest src/apps/accounts/tests.py` (from `src/`) — must pass before
      next task

### Task 4: Update the race-app regression test comment

**Files:**
- Modify: `src/apps/race/tests.py`

- [ ] in `test_race_page_anon_sees_login_and_add_button` (~505) update the stale comment
      «The button points at add_team; the view redirects anon users to login.» to say the
      view now routes anon users through passwordless `account_start` (assertions
      unchanged — the button still targets `add_team`)
- [ ] run `uv run pytest src/apps/race/tests.py` (from `src/`) — must pass

### Task 5: Verify acceptance criteria

**Files:** —

- [ ] verify Overview requirements: anon `add_team` enters `account_start` (not
      password `login`); `?next=` round-trips so login lands back on `add_team`;
      start/verify show race context + back link when entered via a race and generic
      copy otherwise
- [ ] verify scope guard: `TeamPayment`, `EditTeamView`, transfer/delete and other
      `reverse("login")` sites are unchanged; `LOGIN_URL` is unchanged
- [ ] run full suite from `src/`: `uv run pytest`
- [ ] run `make format && make lint` — clean

### Task 6: [Final] Update documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/plans/20260601-add-team-passwordless-entry.md` (this file)

- [ ] add to the CLAUDE.md "Auth" note: the race-page «Войти и добавить команду» path —
      `AddTeam`'s anon redirect — now enters the passwordless `account_start` (not
      password `login`); `start`/`verify` derive race context from the `next` path via
      `_race_from_next`. Note other login-gated team actions still use password `login`.
- [ ] move this plan: `mv docs/plans/20260601-add-team-passwordless-entry.md
      docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention or external systems — informational only.*

**Manual verification:**
- With a running `kolco24_runmailer`, click «Войти и добавить команду» on a real race as
  a logged-out user: confirm the start page greets «…добавить команду на «<гонка>»», the
  email arrives with a working code and magic link, and both the code path and the link
  path land back on `/race/<slug>/teams/add/` authenticated.

**Known scope boundary (by decision):**
- Other login-gated team actions (`TeamPayment`, `EditTeamView`, member transfer/delete)
  still redirect to password `login`. Repointing them is a separate, optional follow-up.
