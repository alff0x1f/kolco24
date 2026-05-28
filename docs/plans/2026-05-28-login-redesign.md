# Login page redesign + move to /login/

**Goal:** Redesign the login page (`passlogin`) to match the new design system (base-2.html / theme-2.css, same as `/register/`), and move the URL from `/passlogin/` to `/login/`.

**Reference design:** `scratch/Вход.html`

---

## Tasks

### 1. URL and settings

- [ ] `src/website/urls.py`: change `path("passlogin/", ..., name="passlogin")` → `path("login/", ..., name="login")`
- [ ] `src/config/settings.py`: `LOGIN_URL = "passlogin"` → `LOGIN_URL = "login"`

### 2. View (`src/website/views/views_.py`)

- [ ] Rename class `PassLoginView` → `LoginView`
- [ ] Fix post-login redirect: read `request.GET.get("next")` and use `_safe_redirect()` instead of hardcoded `HttpResponseRedirect("/")`
- [ ] Update the `template` attribute to `"website/login.html"`
- [ ] Update all `reverse("passlogin")` calls (~8 occurrences) → `reverse("login")`
- [ ] Update all `login_url="passlogin"` in `@user_passes_test` decorators (~3 occurrences) → `login_url="login"`

### 3. Template

- [ ] Create `src/templates/website/login.html` (replaces `passlogin.html`)
- [ ] Extends `base-2.html`
- [ ] Layout from `scratch/Вход.html`: centered card 420px max-width, page header with title + subtitle
- [ ] Fields (written manually, no `{{ form.field }}` Django widgets — see CLAUDE.md):
  - Email input with `has-error` class on errors
  - Password input with show/hide toggle button (`.adorn` button inside `.input-wrap`)
  - "Запомнить меня" checkbox (`.check` component)
- [ ] Error display: `form.non_field_errors` and `messages` → `.err-banner` block (no Bootstrap classes)
- [ ] "Забыли пароль?" link → `{% url 'password_reset' %}`
- [ ] Below-card "Ещё нет аккаунта? Зарегистрироваться →" → `{% url 'register' %}`
- [ ] Show/hide password JS (inline `<script>`)
- [ ] Delete old `src/templates/website/passlogin.html`

### 4. Update template references

- [ ] `src/templates/website/register.html`: `{% url 'passlogin' %}` → `{% url 'login' %}`
- [ ] Search for any other template references to `passlogin` and update

---

## Notes

- Do NOT use `{{ form.field }}` — Django widgets emit Bootstrap classes that conflict with `theme-2.css`. Write inputs manually (see CLAUDE.md).
- Error inputs use `class="input has-error"`, errors shown below via `{{ form.field.errors|join:", " }}`.
- The `_safe_redirect()` helper already exists in `views_.py` — use it for the `next` redirect.
- Django's own `LoginView` lives in `django.contrib.auth.views` — our renamed class is in a different module, no conflict.
