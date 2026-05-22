# Race Post Form and Race Admins

## Overview

Add ability for designated race administrators to publish news posts directly from the
`race/<slug>/` page. A post form (title + markdown content + optional image) appears at
the top of the news feed when the logged-in user is a race admin. Submits to a dedicated
`race/<slug>/post/add/` URL.

Also introduces a new `RaceAdmin` model that links a User to a Race with a role
(`admin` / `moderator`). Assigned via Django admin. Django superusers have NO special
access — only explicitly assigned race admins can post.

## Context (from discovery)

- **Model layer**: `src/website/models/race.py` — `Race`, `RaceLink`, `Category`. No admin concept yet.
- **News model**: `src/website/models/news.py` — `NewsPost` (title, content/content_html, image, race FK).
- **View**: `src/website/views/views_.py:386` — `RaceNewsView.get_context()` builds news feed context.
- **Template**: `src/templates/website/news.html` — news list left col, sidebar right col.
- **Naming conflict**: `admin.py` has a Django admin class already named `RaceAdmin` (line 213). Must rename it to `RaceModelAdmin` to free the name for the new model.
- **Tests**: `src/website/tests.py` — pytest functions, no class hierarchy needed.

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- **Every task MUST include tests** before moving on
- All tests must pass before starting next task

## Testing Strategy

- Unit tests for model, view authorization, and form in `src/website/tests.py`
- No e2e/Playwright tests in this project

## Progress Tracking

- Mark completed items with `[x]` immediately when done
- Add newly discovered tasks with ➕ prefix
- Document issues/blockers with ⚠️ prefix

## Solution Overview

1. New `RaceAdmin` model in `race.py` — stores `race`, `user`, `role` (admin/moderator).
2. Helper `is_race_admin(user, race)` — one-liner filter check.
3. `NewsPostForm` ModelForm — fields `title`, `content`, `image`.
4. `AddNewsPostView` — POST-only view at `race/<slug>/post/add/`.
5. `RaceNewsView.get_context()` gains optional `request` param to inject `post_form` for admins.
6. `news.html` renders the post card at the top of the feed when `post_form` present.
7. Django admin: rename `RaceAdmin→RaceModelAdmin`, add `RaceAdminInline` on Race page.

## What Goes Where

**Implementation Steps** — all changes in this codebase.

**Post-Completion** — manual: log in as a race admin on staging, verify form appears and
post publishes; verify non-admin sees no form.

---

## Implementation Steps

### Task 1: Add `RaceAdmin` model

**Files:**
- Modify: `src/website/models/race.py`
- Modify: `src/website/models/__init__.py`

- [x] Add `RaceAdmin` model to `src/website/models/race.py` after `RaceLink`:
  ```python
  class RaceAdmin(Model):
      class Role(TextChoices):
          ADMIN = "admin", "Администратор"
          MODERATOR = "moderator", "Модератор"
      race = ForeignKey(Race, on_delete=CASCADE, related_name="race_admins")
      user = ForeignKey(settings.AUTH_USER_MODEL, on_delete=CASCADE, related_name="race_admins")
      role = CharField(max_length=16, choices=Role.choices, default=Role.ADMIN)
      class Meta:
          unique_together = ("race", "user")
          verbose_name = "Администратор гонки"
          verbose_name_plural = "Администраторы гонки"
      def __str__(self):
          return f"{self.user} — {self.race} ({self.role})"
  ```
- [x] Add `from django.conf import settings` import if not present in `race.py`
- [x] Export `RaceAdmin` from `src/website/models/__init__.py` (add to `from .race import ...`)
- [x] Write tests: `test_race_admin_model_creation` — create RaceAdmin, verify str, unique_together constraint
- [x] Run tests — must pass before task 2

### Task 2: Generate and apply migration

**Files:**
- Create: `src/website/migrations/006X_raceadmin.py` (auto-generated)

- [x] Run `uv run python src/manage.py makemigrations website --name raceadmin`
- [x] Inspect generated migration for correctness (FK to auth.User, unique_together)
- [x] Run `uv run python src/manage.py migrate` (requires DB running: `docker compose up -d kolco24_db`)
- [x] Run tests — must pass before task 3

### Task 3: Fix admin.py naming conflict and add RaceAdmin inline

**Files:**
- Modify: `src/website/admin.py`

- [x] Rename existing `class RaceAdmin(admin.ModelAdmin)` → `class RaceModelAdmin(admin.ModelAdmin)` (the Django admin class for Race)
- [x] Update `admin.site.register(Race, RaceAdmin)` → `admin.site.register(Race, RaceModelAdmin)`
- [x] Import `RaceAdmin` model from `website.models` (the new model, not the admin class)
- [x] Add `RaceAdminInline(admin.TabularInline)` with `model = RaceAdmin`, `extra = 1`
- [x] Add `inlines = [RaceAdminInline]` to `RaceModelAdmin`
- [x] Run tests — must pass before task 4

### Task 4: Add `NewsPostForm`

**Files:**
- Modify: `src/website/forms.py`

- [ ] Add `NewsPostForm(forms.ModelForm)` for `NewsPost`, fields: `title`, `content`, `image`
- [ ] Import `NewsPost` in forms.py if not already imported
- [ ] Write tests: `test_news_post_form_valid` (with title+content), `test_news_post_form_invalid` (empty title)
- [ ] Run tests — must pass before task 5

### Task 5: Add `AddNewsPostView` and helper `is_race_admin`

**Files:**
- Modify: `src/website/views/views_.py`

- [ ] Add helper function `is_race_admin(user, race) -> bool`:
  ```python
  def is_race_admin(user, race):
      if not user.is_authenticated:
          return False
      return RaceAdmin.objects.filter(race=race, user=user).exists()
  ```
- [ ] Import `RaceAdmin`, `NewsPostForm` in views file
- [ ] Add `AddNewsPostView(View)`:
  - `post()` method only (GET → `HttpResponseNotAllowed`)
  - Check `is_race_admin(request.user, race)` → 403 if not
  - Bind `NewsPostForm(request.POST, request.FILES)`, save with `race=race`
  - On success: `redirect("race", race_slug=race_slug)`
  - On form invalid: re-render `RaceNewsView` with form errors in context
- [ ] Write tests:
  - `test_add_post_by_race_admin` — admin posts successfully, post appears in DB
  - `test_add_post_unauthorized` — anonymous user gets 403
  - `test_add_post_non_admin_user` — authenticated but not admin gets 403
- [ ] Run tests — must pass before task 6

### Task 6: Update `RaceNewsView` to inject post form for admins

**Files:**
- Modify: `src/website/views/views_.py`

- [ ] Change `RaceNewsView.get()` to pass `request` to `get_context()`:
  `context = self.get_context(race, request.user)`
- [ ] Update `get_context(race, user=None)` signature
- [ ] Inside `get_context`: if `user` and `is_race_admin(user, race)` → add `"post_form": NewsPostForm()` to context
- [ ] Write test: `test_race_news_view_shows_form_for_admin` — admin GET includes `post_form`; non-admin does not
- [ ] Run tests — must pass before task 7

### Task 7: Update `news.html` template

**Files:**
- Modify: `src/templates/website/news.html`

- [ ] Add post form card above `{% for news in news_list %}`, rendered only when `post_form` is set:
  ```html
  {% if post_form %}
  <div class="card mb-4">
      <div class="card-header"><span class="h6">Новый пост</span></div>
      <div class="card-body">
          <form action="{% url 'add_post' race.slug %}" method="post" enctype="multipart/form-data">
              {% csrf_token %}
              <div class="form-group">
                  {{ post_form.title.label_tag }}
                  {{ post_form.title }}
              </div>
              <div class="form-group">
                  {{ post_form.content.label_tag }}
                  {{ post_form.content }}
              </div>
              <div class="form-group">
                  {{ post_form.image.label_tag }}
                  {{ post_form.image }}
              </div>
              <button type="submit" class="btn btn-primary">Опубликовать</button>
          </form>
      </div>
  </div>
  {% endif %}
  ```
- [ ] (No unit test needed for template rendering beyond Task 6 coverage)

### Task 8: Register URL `race/<slug>/post/add/`

**Files:**
- Modify: `src/website/urls.py`

- [ ] Import `AddNewsPostView` in `urls.py`
- [ ] Add URL pattern before `race/<slug:race_slug>/`:
  ```python
  path("race/<slug:race_slug>/post/add/", AddNewsPostView.as_view(), name="add_post"),
  ```
- [ ] Run full test suite: `uv run pytest` — all must pass

### Task 9: Verify acceptance criteria

- [ ] All requirements from Overview are implemented
- [ ] Race admin sees form on `race/<slug>/` — non-admin does not
- [ ] POST to `race/<slug>/post/add/` creates `NewsPost` linked to correct race
- [ ] Anonymous and non-admin users get 403 on POST
- [ ] Run full test suite: `uv run pytest`
- [ ] Lint: `uv run ruff check src && uv run black --check src && uv run isort --check src`

### Task 10: [Final] Cleanup

- [ ] Move this plan to `docs/plans/completed/`

## Post-Completion

**Manual verification:**
- Log in as race admin on staging → open `race/<slug>/` → confirm form appears at top of feed
- Submit a post with title, text, and image → verify it appears in feed immediately
- Log in as regular user → confirm no form visible
- Log out → confirm no form visible
- Check Django admin: Race detail page shows `RaceAdmin` inline for assigning admins
