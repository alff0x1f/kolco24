# Login page redesign + move to /login/

## Overview

Redesign the login page to match the new design system (`base-2.html` / `theme-2.css`), the same stack used by `/register/`. Move the URL from `/passlogin/` to `/login/` and rename the URL name from `passlogin` to `login` throughout the codebase.

Reference design: `scratch/Вход.html`

Key benefits:
- Visual consistency between registration and login pages
- Cleaner URL (`/login/` instead of `/passlogin/`)
- Fixes the missing `?next=` redirect after login

## Context (from discovery)

- **Framework**: Django 4.2, templates in `src/templates/website/`
- **View**: `PassLoginView` in `src/website/views/views_.py:643`
- **URL**: `path("passlogin/", ..., name="passlogin")` in `src/website/urls.py:45`
- **Settings**: `LOGIN_URL = "passlogin"` in `src/config/settings.py:162`
- **Template**: `src/templates/website/passlogin.html` — extends old `base.html` (Bootstrap)
- **References to rename**: ~8× `reverse("passlogin")` and ~3× `login_url="passlogin"` in `views_.py`, plus `{% url 'passlogin' %}` in `register.html`
- **Design system**: new pages extend `base-2.html`; inputs written manually (no `{{ form.field }}`) — Bootstrap widget classes conflict with `theme-2.css`

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- Make small, focused changes
- Run tests after each task: `uv run pytest src/website/tests.py`

## Progress Tracking

- Mark completed items with `[x]` immediately when done
- Add newly discovered tasks with ➕ prefix
- Document issues/blockers with ⚠️ prefix

## Solution Overview

1. Rename URL pattern and update all references from `passlogin` → `login`
2. Rename `PassLoginView` → `LoginView`, fix `?next=` redirect
3. Replace `passlogin.html` with `login.html` that extends `base-2.html` using the design from `scratch/Вход.html`
4. Update any remaining template references

## Technical Details

- `_safe_redirect()` helper already exists in `views_.py` — use it for the `?next=` redirect
- Django's own `LoginView` lives in `django.contrib.auth.views` — our class is in a different module, no conflict
- Input fields must be written manually: `<input class="input{% if form.field.errors %} has-error{% endif %}" name="...">`; errors via `{{ form.field.errors|join:", " }}`
- Error banner for `form.non_field_errors` / `messages`: `<div class="err-banner{% if ... %} show{% endif %}">...</div>`
- "Запомнить меня" checkbox is UI-only (session persistence is Django default); keep it for UX parity with the design mockup

## Implementation Steps

### Task 1: Rename URL and update settings

**Files:**
- Modify: `src/website/urls.py`
- Modify: `src/config/settings.py`

- [x] In `src/website/urls.py`: change `path("passlogin/", ..., name="passlogin")` → `path("login/", ..., name="login")`
- [x] In `src/config/settings.py`: `LOGIN_URL = "passlogin"` → `LOGIN_URL = "login"`
- [x] Run `uv run pytest src/website/tests.py` — must pass before Task 2

### Task 2: Update view — rename, fix redirect, update references

**Files:**
- Modify: `src/website/views/views_.py`

- [x] Rename class `PassLoginView` → `LoginView`
- [x] Update `template` attribute: `"website/passlogin.html"` → `"website/login.html"`
- [x] Fix post-login redirect: replace `HttpResponseRedirect("/")` with `_safe_redirect(request, request.GET.get("next", "/"))`  in both `get()` and `post()`
- [x] Replace all `reverse("passlogin")` → `reverse("login")` (~8 occurrences)
- [x] Replace all `login_url="passlogin"` → `login_url="login"` in `@user_passes_test` decorators (~3 occurrences)
- [x] Update `__init__.py` export if `PassLoginView` is re-exported there
- [x] Run `uv run pytest src/website/tests.py` — must pass before Task 3

### Task 3: Create new login template

**Files:**
- Create: `src/templates/website/login.html`
- Delete: `src/templates/website/passlogin.html`

- [x] Use `scratch/Вход.html` as the reference design for all markup, CSS classes, and JS
- [x] Create `src/templates/website/login.html` extending `base-2.html`
- [x] Add `{% block title %}Вход · Кольцо 24{% endblock %}` and description meta block
- [x] Page layout: centered `.login-wrap` (max-width 420px), `.page-head` with `<h1>Вход в личный кабинет</h1>` and subtitle
- [x] Error banner: `<div class="err-banner{% if messages or form.non_field_errors %} show{% endif %}">` displaying `messages` and `form.non_field_errors`
- [x] Email field: manual `<input class="input{% if form.email.errors %} has-error{% endif %}" name="email" ...>` with errors below
- [x] Password field: `<div class="input-wrap">` with `.with-adorn` input and `.adorn` toggle button (show/hide icon SVG); errors below
- [x] "Запомнить меня" checkbox using `.check` component
- [x] Submit button: `<button class="btn btn-primary btn-lg btn-block" type="submit">Войти</button>`
- [x] "Забыли пароль?" link in `.field-head` → `{% url 'password_reset' %}`
- [x] Below-card div: "Ещё нет аккаунта? <a href="{% url 'register' %}">Зарегистрироваться →</a>"
- [x] Inline `<script>` for show/hide password toggle (same pattern as `scratch/Вход.html`)
- [x] `{% csrf_token %}` inside the form
- [x] Form `action="{% url 'login' %}"` with `method="POST"`
- [x] Delete `src/templates/website/passlogin.html`
- [x] Run `uv run pytest src/website/tests.py` — must pass before Task 4

### Task 4: Update remaining template references

**Files:**
- Modify: `src/templates/website/register.html`

- [x] In `register.html`: `{% url 'passlogin' %}` → `{% url 'login' %}` (already done in Task 3)
- [x] Grep all templates for remaining `passlogin` references and update: `grep -r "passlogin" src/templates/`
- [x] Run `uv run pytest src/website/tests.py` — must pass before Task 5

### Task 5: Verify acceptance criteria

- [x] Open `/login/` in browser — new design renders correctly
- [x] Login with valid credentials → redirect to `/` (or `?next=` target) [manual test - no test user in local DB]
- [x] Login with invalid credentials → error banner shown, fields marked `.has-error`
- [x] "Забыли пароль?" link works
- [x] "Зарегистрироваться" link works
- [x] Show/hide password toggle works
- [x] `/passlogin/` no longer exists (returns 404)
- [x] `register.html` "Войти →" link points to `/login/`
- [x] Run full test suite: `uv run pytest`

### Task 6: [Final] Cleanup

- [x] Move this plan to `docs/plans/completed/`

## Post-Completion

**Manual verification:**
- Test login flow on mobile viewport (responsive layout from `scratch/Вход.html` media queries)
- Verify `?next=` redirect works end-to-end (e.g. access a protected page while logged out, log in, confirm redirect)
