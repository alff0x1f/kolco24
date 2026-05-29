# Race Admin Add/Edit Page

## Overview

Add a single admin page for **creating** and **editing** a `Race`, styled after
`scratch/Админка - гонка.html`. The page lives in the `apps.race` app (next to
`RacePageView`), extends `website/base-2.html`, and lets an authorized admin edit all
scalar `Race` fields plus inline-manage the race's `Category` rows (create / edit / delete
/ reorder) in one form, saved atomically.

**Problem it solves**: today races and categories can only be edited through Django admin.
This gives race admins a purpose-built, on-brand page reachable straight from the race
page.

**Integration**: reuses the established `apps.race` conventions — `_safe_json` JSON-embed
blocks, the `base-2.html` template stack, and the `AddNewsPostView` authorization pattern.

## Context (from discovery)

- **Model**: `src/website/models/race.py`
  - `Race` — `name`, `code` (unique), `slug` (unique), `place`, `date`, `date_end`,
    `cost`, `is_active`, `reg_status`, `header_image`/`header_logo` (URL `CharField`s
    validated http/https in `Race.clean()`), `is_legend_visible`, `is_reg_open`,
    `is_teams_editable`, `is_photo_upload_enabled`.
  - `RegStatus` — 3 choices: `upcoming` / `open` / `sold_out`.
  - `RaceAdmin` — `race`, `user`, `role` (`Role.ADMIN` / `Role.MODERATOR`).
  - `Category` — `code`, `short_name`, `name`, `description`, `is_active`, `order`,
    `race` FK. `Category.active_objects` filters `is_active=True`.
- **Views**: `src/apps/race/views.py` — `RacePageView`, `RaceTeamsView`. Helpers
  `_safe_json(data)` (JSON for `<script>` embed) and `_categories_with_team_count(race)`.
- **Permissions**: `src/website/views/views_.py` — `is_race_admin(user, race)`,
  `is_admin(user)`. `AddNewsPostView` (same file) is the auth pattern to mirror
  (login redirect for anon, `HttpResponseForbidden` for non-admins, deferred import of
  `RacePageView` to dodge the circular dependency).
- **URLs**: `src/website/urls.py` — `add_post` (`race/<slug>/post/add/`) is registered
  *before* `name="race"` (`race/<slug>/`); follow that ordering.
- **Templates**: `src/templates/race/` — `race_page.html`, `teams.html`, both
  `{% extends "website/base-2.html" %}` with page CSS via `{% block extra_head %}`.
- **CSS/JS**: `src/static/css/`, `src/static/js/` (e.g. `teams.css`, `teams.js`).
- **Tests**: `src/apps/race/tests.py` — pytest + Django `TestCase`; helpers
  `_make_race`, `_make_category`, `_make_team`, `_script_json(html, script_id)`.
- **Scratch template**: `scratch/Админка - гонка.html` — standalone HTML with nav/footer,
  `<style>`, and a JS category repeater. Note: its drag handle (`⠿`) is **decorative** —
  no real drag-sort is implemented yet.

## Development Approach

- **Testing approach**: Regular (code first, then tests) — matches prior race plans.
- Complete each task fully before moving to the next.
- Make small, focused changes; run tests after each.
- **CRITICAL: every task MUST include new/updated tests** (success + error scenarios).
- **CRITICAL: all tests must pass before starting the next task.**
- **CRITICAL: update this plan file if scope changes during implementation.**
- Maintain backward compatibility (no model migrations; existing race/teams pages
  unaffected).

## Testing Strategy

- **Unit tests** in `src/apps/race/tests.py` for: access-control matrix, create flow, edit
  flow, category reconcile (add/edit/delete/reorder), cross-race id guard, field & row
  validation, and the `can_edit_race` context flag.
- **No e2e/Playwright** harness in this project — JS (drag-reorder, serialize-on-submit) is
  verified manually (see Post-Completion).

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix.
- Document issues/blockers with ⚠️ prefix.
- Keep this plan in sync with actual work.

## Solution Overview

1. **Permission helper** `can_edit_race(user, race)` in `views_.py`: superuser → always;
   else `RaceAdmin` with `role=Role.ADMIN` for that race. (Create is superuser-only,
   enforced in the view, not this helper.)
2. **`RaceForm`** — `ModelForm` over `Race` scalar fields (`src/apps/race/forms.py`).
   Gives `code`/`slug` uniqueness with self-exclusion on edit and runs `Race.clean()`
   (URL validation) automatically. No widget `attrs` — template renders inputs manually.
3. **`RaceEditView`** — one CBV for both create and edit. GET renders the form; POST
   validates `RaceForm` + parses `categories_json`, reconciles categories, all inside one
   `transaction.atomic()`, then redirects to the race page.
4. **Categories** travel as a single hidden `categories_json` input; existing rows are
   embedded via `_safe_json` as `<script id="categories-data" type="application/json">`.
   Reconcile by `id`: present-with-id → update; no id → create; existing id absent from
   payload → delete; `order = array index`.
5. **Template** `race_form.html` (extends `base-2.html`), scoped wrapper
   `.race-form-page` (never bare `.page`). Manual field rendering with inline errors;
   `reg_status` `<select>` from `RegStatus.choices`; image fields as URL inputs + live
   `<img>` preview.
6. **CSS** `race_form.css` (scratch `<style>` scoped under `.race-form-page`) and **JS**
   `race_form.js` (repeater + char counters + slug auto-gen + publish-status toggle +
   real drag-reorder + serialize-categories-on-submit).
7. **Entry point** — `RacePageView.build_context` adds `can_edit_race`; `race_page.html`
   shows an "Редактировать" button for admins and "+ Новая гонка" for superusers.
8. **URLs** — `races/new/` (`add_race`) and `race/<slug>/edit/` (`edit_race`, before
   `name="race"`).

## Technical Details

- **`categories_json` row shape**: `{"id": <int|null>, "code", "short_name", "name",
  "description", "is_active": <bool>}`. Order is implicit (array position).
- **Reconcile algorithm** (inside `transaction.atomic()`):
  1. Parse JSON → must be a `list` (else form-level error).
  2. Validate each row: `code` required & ≤15, `name` required & ≤50, `short_name` ≤15,
     `description` ≤150; duplicate `code` within payload → row error. Collect errors into
     `{row_index: {field: msg}}`.
  3. If form **and** all rows valid: `form.save()`; build a map of existing
     `Category` ids for this race; for each row by index — update existing (only if id
     belongs to this race), else create; track seen ids; delete this race's categories
     whose id is not in the seen set; set `order = index`.
- **Cross-race guard**: a row `id` not belonging to the current race is treated as a new
  category (id ignored), never an update/delete of another race's row.
- **Empty list allowed**: a race may legitimately have zero categories.
- **Re-render on error**: echo the submitted `categories_json` back into the
  `<script id="categories-data">` block so unsaved rows survive, plus pass
  `category_errors` for inline display and `form` for field errors.
- **`is_create` flag** toggles title/breadcrumb ("Создание" vs "Редактирование") and the
  `ID · код` sub-line (edit only; the model has no `created` timestamp, so omit "создана").

## What Goes Where

- **Implementation Steps** (`[ ]`): all code, templates, assets, and unit tests in this repo.
- **Post-Completion** (no checkboxes): manual browser verification of JS behavior
  (drag-reorder, live preview, serialize-on-submit) and any future migration to real file
  uploads for header image/logo.

## Implementation Steps

### Task 1: Permission helper `can_edit_race`

**Files:**
- Modify: `src/website/views/views_.py`
- Modify: `src/apps/race/tests.py`

- [ ] add `can_edit_race(user, race)` next to `is_race_admin`: `False` if not
      authenticated; `True` if `user.is_superuser`; else
      `RaceAdmin.objects.filter(race=race, user=user, role=RaceAdmin.Role.ADMIN).exists()`
- [ ] write test: superuser → `True` for any race
- [ ] write test: `RaceAdmin(role=ADMIN)` → `True` for own race, `False` for another race
- [ ] write test: `RaceAdmin(role=MODERATOR)` → `False`; anonymous/regular user → `False`
- [ ] run tests — must pass before next task

### Task 2: `RaceForm` ModelForm

**Files:**
- Create: `src/apps/race/forms.py`
- Modify: `src/apps/race/tests.py`

- [ ] create `RaceForm(forms.ModelForm)` with `Meta.model = Race` and fields: `name`,
      `code`, `slug`, `place`, `date`, `date_end`, `cost`, `header_image`, `header_logo`,
      `reg_status`, `is_active`, `is_legend_visible`, `is_reg_open`, `is_teams_editable`,
      `is_photo_upload_enabled` (no widget `attrs` — manual rendering in template)
- [ ] write test: valid data → `form.is_valid()` and `save()` creates a `Race`
- [ ] write test: duplicate `code`/`slug` (vs another race) → form invalid with field error
- [ ] write test: editing an instance keeps its own `code`/`slug` valid (self-exclusion)
- [ ] write test: invalid `header_image` URL → field error (via `Race.clean()`)
- [ ] run tests — must pass before next task

### Task 3: `RaceEditView` — GET + auth

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/tests.py`

- [ ] add `RaceEditView(View)` with a `_load_and_authorize(request, race_slug)` helper:
      create (no slug) requires `is_superuser`; edit loads `get_object_or_404(Race, ...)`
      and requires `can_edit_race`; anon → login redirect (`reverse("login") + "?next="`),
      forbidden → `HttpResponseForbidden` (mirror `AddNewsPostView`)
- [ ] implement `get`: build context (`form` = `RaceForm(instance=...)`, `is_create`,
      `categories_data` JSON via `_safe_json` from existing categories ordered by
      `order, id`, `reg_status_choices`, `race`) and render `race/race_form.html`
- [ ] write test: anonymous GET on edit and create → redirect to `login?next=`
- [ ] write test: regular user GET → 403; non-ADMIN RaceAdmin GET edit → 403
- [ ] write test: superuser GET create + ADMIN GET own-race edit → 200 with form context
- [ ] run tests — must pass before next task

### Task 4: `RaceEditView` — POST save + category reconcile

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/tests.py`

- [ ] implement `post`: re-run auth; bind `RaceForm(request.POST, instance=...)`; parse
      `categories_json`; validate rows; on full validity `form.save()` + reconcile
      categories (update/create/delete, `order=index`) inside `transaction.atomic()`;
      redirect to `reverse("race", kwargs={"race_slug": race.slug})`
- [ ] on any error: re-render with `form`, echoed `categories_json`, and `category_errors`
- [ ] write test: superuser create flow → `Race` created with correct fields + redirect
- [ ] write test: edit flow updates scalar fields incl. `reg_status` round-trip
- [ ] write test: category reconcile — edit one + add one + omit one → update/create/delete
      happen and `order` matches array position
- [ ] write test: cross-race `id` in payload → treated as new (not hijacked)
- [ ] write test: validation rollback — malformed JSON → form-level error, race unchanged;
      row missing `code`/`name` → row error, full rollback (race not updated)
- [ ] write test: non-ADMIN RaceAdmin POST other race → 403; ADMIN POST create → 403
- [ ] run tests — must pass before next task

### Task 5: URL routes

**Files:**
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [ ] import `RaceEditView`; add `path("races/new/", RaceEditView.as_view(),
      name="add_race")`
- [ ] add `path("race/<slug:race_slug>/edit/", RaceEditView.as_view(), name="edit_race")`
      *before* the `name="race"` route
- [ ] write test: `reverse("add_race")` and `reverse("edit_race", ...)` resolve to
      `RaceEditView`
- [ ] run tests — must pass before next task

### Task 6: `race_form.html` template

**Files:**
- Create: `src/templates/race/race_form.html`
- Modify: `src/apps/race/tests.py`

- [ ] port `scratch/Админка - гонка.html` body into `{% block content %}`; drop its
      `<nav>`/`<footer>`/`<style>`/`<script>`; `{% extends "website/base-2.html" %}`;
      link CSS in `{% block extra_head %}`, JS in `{% block footer_js_include %}`
- [ ] rename the form's `page` class to `.race-form-page` (scoped wrapper, never bare
      `.page`); add CSRF token and `method="post"`
- [ ] render every `Race` field manually with `value="{{ form.field.value|default:'' }}"`,
      `class="control{% if form.field.errors %} has-error{% endif %}"`, errors via
      `{{ form.field.errors|join:", " }}` beneath each input
- [ ] render `reg_status` `<select>` looping `reg_status_choices` with `selected` on the
      current value; image fields as URL `<input>` + live `<img>` preview pane
- [ ] embed categories as `<script id="categories-data" type="application/json">`
      ({{ categories_data }}); add hidden `<input name="categories_json">`; toggle
      title/breadcrumb/sub-line by `is_create`
- [ ] write test: edit GET renders the form with current values and the
      `categories-data` JSON (use `_script_json` helper)
- [ ] run tests — must pass before next task

### Task 7: `race_form.css` + `race_form.js`

**Files:**
- Create: `src/static/css/race_form.css`
- Create: `src/static/js/race_form.js`

- [ ] extract scratch `<style>` into `race_form.css`, scope all rules under
      `.race-form-page`; reuse tokens already defined in `race.css` where they match,
      drop duplicates
- [ ] port scratch JS into `race_form.js`: category repeater reading rows from
      `#categories-data` (not the hardcoded seed), char counters, slug auto-gen,
      publish-status toggle, live image preview
- [ ] implement **real** drag-reorder of category rows (scratch handle was decorative)
- [ ] add submit handler serializing current rows (incl. `id` for existing) into the
      hidden `categories_json` input
- [ ] (no unit tests — JS verified manually; see Post-Completion)

### Task 8: Entry points on the race page

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/templates/race/race_page.html`
- Modify: `src/apps/race/tests.py`

- [ ] in `RacePageView.build_context`, set `context["can_edit_race"] =
      can_edit_race(user, race)` (guard `user is not None`)
- [ ] in `race_page.html`, show an "Редактировать" button (→ `edit_race`) when
      `can_edit_race`, and a "+ Новая гонка" link (→ `add_race`) when `user.is_superuser`
- [ ] write test: admin context → `can_edit_race=True` and button renders; regular user →
      `False`/absent and no button
- [ ] run tests — must pass before next task

### Task 9: Verify acceptance criteria
- [ ] verify every Overview requirement is implemented (create + edit, all fields,
      inline categories, access rules, entry points)
- [ ] verify edge cases: empty category list, cross-race id, malformed JSON, duplicate
      code, rollback on row error
- [ ] run full suite: `uv run pytest`
- [ ] run `make format && make lint`

### Task 10: [Final] Documentation
- [ ] update `CLAUDE.md` `apps.race` description to mention `RaceEditView`,
      `race_form.html`, `race_form.css`/`race_form.js`, and the `add_race`/`edit_race`
      URL names
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only.*

**Manual verification:**
- In a browser, confirm category drag-reorder updates row order and that the new order
  persists after save (`order` reflects final positions).
- Confirm adding/removing category rows and submitting round-trips correctly via
  `categories_json`; confirm unsaved rows survive a validation error.
- Confirm header image/logo URL inputs drive the live `<img>` preview.
- Confirm the "Редактировать" / "+ Новая гонка" buttons appear only for the right users.

**Future work (out of scope):**
- Migrate `header_image`/`header_logo` from URL `CharField`s to real file uploads
  (`ImageField` + MEDIA handling + migration) — the UI is intentionally styled toward this.
