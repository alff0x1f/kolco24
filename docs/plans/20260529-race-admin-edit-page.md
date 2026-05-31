# Race Admin Add/Edit Page

> **Revised 2026-05-31** after rebasing onto `master`. Three merged PRs changed the data
> model since this plan was first written:
> - **#187 Remove is_reg_open** — the `Race.is_reg_open` field is gone. Drop it from the
>   form and do **not** port the scratch "Регистрация открыта" toggle.
> - **#185 Team Registration redesign** — `Category` gained `min_people`/`max_people`
>   (allowed team size, defaults 2/6) and a new `RacePriceTier` model + `Race.current_price`
>   were added. `cost` is now only a *fallback* price.
> - **#183 Teams List redesign** — `RaceTeamsView` now exists; `race_page.html` markup
>   changed (`cats-menu`, `cover-actions`).
>
> Per decisions taken during this revision, the page now **also** edits category
> `min_people`/`max_people` and inline-manages `RacePriceTier` rows (neither is in the
> scratch mockup — both are new UI).

## Overview

Add a single admin page for **creating** and **editing** a `Race`, styled after
`scratch/Админка - гонка.html`. The page lives in the `apps.race` app (next to
`RacePageView`), extends `website/base-2.html`, and lets an authorized admin edit all
scalar `Race` fields, inline-manage the race's `Category` rows (create / edit / delete /
reorder, incl. min/max team size) **and** its `RacePriceTier` rows (the price ladder), all
in one form, saved atomically.

**Problem it solves**: today races and categories can only be edited through Django admin.
This gives race admins a purpose-built, on-brand page reachable straight from the race
page.

**Integration**: reuses the established `apps.race` conventions — `_safe_json` JSON-embed
blocks, the `base-2.html` template stack, and the `AddNewsPostView` authorization pattern.

## Context (from discovery)

- **Model**: `src/website/models/race.py`
  - `Race` — `name`, `code` (unique), `slug` (unique), `place`, `date`, `date_end`,
    `cost`, `is_active`, `reg_status`, `header_image`/`header_logo` (URL `CharField`s
    validated http/https in `Race.clean()`), `is_legend_visible`, `is_teams_editable`,
    `is_photo_upload_enabled`. (`is_reg_open` was **removed** in #187.)
  - `Race.current_price` (property) is the source of truth for the charged per-person
    price: the active `RacePriceTier`, falling back to `Race.cost` when there are no
    tiers. `Race.price_tier_ladder()` returns `[{"tier", "status"}]` for display. So `cost`
    is now a **fallback**, not necessarily the price participants pay.
  - `RegStatus` — 3 choices: `upcoming` / `open` / `sold_out`. (NB: the scratch `<select>`
    lists stale `closed`/`finished` options — ignore them; render from `RegStatus.choices`.)
  - `RaceAdmin` — `race`, `user`, `role` (`Role.ADMIN` / `Role.MODERATOR`).
  - `Category` — `code`, `short_name`, `name`, `description`, `is_active`, `order`,
    `min_people` (default 2), `max_people` (default 6), `race` FK.
    `Category.active_objects` filters `is_active=True`. `min_people`/`max_people` drive the
    allowed team-size control on the registration page — editable here per the revision.
  - `RacePriceTier` — `race` FK (`related_name="price_tiers"`), `price` (IntegerField,
    per-person ₽), `active_until` (DateField, inclusive), `order` (default 0). `Meta.ordering
    = ["active_until", "order"]`. The price ladder, inline-managed here.
- **Views**: `src/apps/race/views.py` — `RacePageView`, `RaceTeamsView`. Helpers
  `_safe_json(data)` (JSON for `<script>` embed) and `_categories_with_team_count(race)`.
  `is_race_admin` is already imported; `build_context` already has a
  `if user is not None and is_race_admin(user, race):` admin block to extend.
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
  `<style>`, and a JS category repeater. Notes: its drag handle (`⠿`) is **decorative**
  (no real drag-sort); its category rows have **no** min/max-people columns and there is
  **no** price-tier editor — both are new UI to add; its "Регистрация открыта" toggle
  (`#f-regopen`) is **obsolete** (field removed) and must not be ported.

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
  flow, category reconcile (add/edit/delete/reorder, incl. `min_people`/`max_people`),
  price-tier reconcile (add/edit/delete), cross-race id guard (categories **and** tiers),
  field & row validation, and the `can_edit_race` context flag.
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
   Includes `cost` (now the **fallback** price), excludes the removed `is_reg_open`.
3. **`RaceEditView`** — one CBV for both create and edit. GET renders the form; POST
   validates `RaceForm` + parses `categories_json` **and** `price_tiers_json`, reconciles
   both, all inside one `transaction.atomic()`, then redirects to the race page.
4. **Categories** travel as a single hidden `categories_json` input; existing rows are
   embedded via `_safe_json` as `<script id="categories-data" type="application/json">`.
   Each row now carries `min_people`/`max_people`. Reconcile by `id`: present-with-id →
   update; no id → create; existing id absent from payload → delete; `order = array index`.
5. **Price tiers** travel the same way as a hidden `price_tiers_json` input, embedded as
   `<script id="price-tiers-data" type="application/json">`, reconciled by `id` with the
   identical add/update/delete algorithm; `order = array index` (the model still sorts
   primarily by `active_until`). This is **new UI** with no scratch counterpart.
6. **Template** `race_form.html` (extends `base-2.html`), scoped wrapper
   `.race-form-page` (never bare `.page`). Manual field rendering with inline errors;
   `reg_status` `<select>` from `RegStatus.choices`; image fields as URL inputs + live
   `<img>` preview; category rows extended with min/max-people inputs; a new "Ценовые
   периоды" section repeater for price tiers.
7. **CSS** `race_form.css` (scratch `<style>` scoped under `.race-form-page`) and **JS**
   `race_form.js` (category + price-tier repeaters + char counters + slug auto-gen +
   publish-status toggle + real drag-reorder + serialize-both-on-submit).
8. **Entry point** — `RacePageView.build_context` adds `can_edit_race`; `race_page.html`
   shows an "Редактировать" button for admins and "+ Новая гонка" for superusers.
9. **URLs** — `races/new/` (`add_race`) and `race/<slug>/edit/` (`edit_race`, before
   `name="race"`).

## Technical Details

- **`categories_json` row shape**: `{"id": <int|null>, "code", "short_name", "name",
  "description", "is_active": <bool>, "min_people": <int>, "max_people": <int>}`. Order is
  implicit (array position).
- **`price_tiers_json` row shape**: `{"id": <int|null>, "price": <int>,
  "active_until": "YYYY-MM-DD"}`. Order is implicit (array position).
- **Reconcile algorithm** (inside one `transaction.atomic()` covering both lists):
  1. Parse each JSON → must be a `list` (else form-level error on that field).
  2. Validate each **category** row: `code` required & ≤15, `name` required & ≤50,
     `short_name` ≤15, `description` ≤150; `min_people`/`max_people` positive ints with
     `min_people ≤ max_people`; duplicate `code` within payload → row error.
  3. Validate each **price-tier** row: `price` required positive int; `active_until`
     required & a valid `YYYY-MM-DD` date. Collect errors into `{row_index: {field: msg}}`.
  4. If form **and** all category rows **and** all tier rows valid: `form.save()`; then for
     each list build a map of existing ids for this race; for each row by index — update
     existing (only if id belongs to this race), else create; track seen ids; delete this
     race's rows whose id is not in the seen set; set `order = index`.
- **Cross-race guard**: a row `id` (category **or** tier) not belonging to the current race
  is treated as a new row (id ignored), never an update/delete of another race's row.
- **Empty list allowed**: a race may legitimately have zero categories and/or zero tiers
  (with zero tiers, `Race.current_price` falls back to `cost`).
- **Re-render on error**: echo both submitted payloads back into the
  `<script id="categories-data">` / `<script id="price-tiers-data">` blocks so unsaved rows
  survive, plus pass `category_errors` and `price_tier_errors` for inline display and
  `form` for field errors.
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

- [x] add `can_edit_race(user, race)` next to `is_race_admin`: `False` if not
      authenticated; `True` if `user.is_superuser`; else
      `RaceAdmin.objects.filter(race=race, user=user, role=RaceAdmin.Role.ADMIN).exists()`
- [x] write test: superuser → `True` for any race
- [x] write test: `RaceAdmin(role=ADMIN)` → `True` for own race, `False` for another race
- [x] write test: `RaceAdmin(role=MODERATOR)` → `False`; anonymous/regular user → `False`
- [x] run tests — must pass before next task

### Task 2: `RaceForm` ModelForm

**Files:**
- Create: `src/apps/race/forms.py`
- Modify: `src/apps/race/tests.py`

- [x] create `RaceForm(forms.ModelForm)` with `Meta.model = Race` and fields: `name`,
      `code`, `slug`, `place`, `date`, `date_end`, `cost` (fallback price), `header_image`,
      `header_logo`, `reg_status`, `is_active`, `is_legend_visible`, `is_teams_editable`,
      `is_photo_upload_enabled` (no widget `attrs` — manual rendering in template; **do not
      include the removed `is_reg_open`**)
- [x] write test: valid data → `form.is_valid()` and `save()` creates a `Race`
- [x] write test: duplicate `code`/`slug` (vs another race) → form invalid with field error
- [x] write test: editing an instance keeps its own `code`/`slug` valid (self-exclusion)
- [x] write test: invalid `header_image` URL → field error (via `Race.clean()`)
- [x] run tests — must pass before next task

### Task 3: `RaceEditView` — GET + auth

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/tests.py`

- [x] add `RaceEditView(View)` with a `_load_and_authorize(request, race_slug)` helper:
      create (no slug) requires `is_superuser`; edit loads `get_object_or_404(Race, ...)`
      and requires `can_edit_race`; anon → login redirect (`reverse("login") + "?next="`),
      forbidden → `HttpResponseForbidden` (mirror `AddNewsPostView`)
- [x] implement `get`: build context (`form` = `RaceForm(instance=...)`, `is_create`,
      `categories_data` JSON via `_safe_json` from existing categories ordered by
      `order, id` — each row incl. `min_people`/`max_people`, `price_tiers_data` JSON via
      `_safe_json` from `race.price_tiers.all()` (`active_until` as `"YYYY-MM-DD"`),
      `reg_status_choices`, `race`) and render `race/race_form.html`
- [x] write test: anonymous GET on edit and create → redirect to `login?next=`
- [x] write test: regular user GET → 403; non-ADMIN RaceAdmin GET edit → 403
- [x] write test: superuser GET create + ADMIN GET own-race edit → 200 with form context
- [x] run tests — must pass before next task

### Task 4: `RaceEditView` — POST save + category reconcile

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/tests.py`

- [x] implement `post`: re-run auth; bind `RaceForm(request.POST, instance=...)`; parse
      `categories_json` **and** `price_tiers_json`; validate rows; on full validity
      `form.save()` + reconcile categories (incl. `min_people`/`max_people`) **and** price
      tiers (update/create/delete, `order=index`) inside one `transaction.atomic()`;
      redirect to `reverse("race", kwargs={"race_slug": race.slug})`
- [x] on any error: re-render with `form`, echoed `categories_json` + `price_tiers_json`,
      and `category_errors` + `price_tier_errors`
- [x] write test: superuser create flow → `Race` created with correct fields + redirect
- [x] write test: edit flow updates scalar fields incl. `reg_status` round-trip
- [x] write test: category reconcile — edit one + add one + omit one → update/create/delete
      happen, `order` matches array position, and `min_people`/`max_people` round-trip
- [x] write test: price-tier reconcile — edit one + add one + omit one → update/create/delete
      happen and `Race.current_price` reflects the new active tier
- [x] write test: cross-race `id` in either payload → treated as new (not hijacked)
- [x] write test: validation rollback — malformed JSON → form-level error, race unchanged;
      category row missing `code`/`name` or `min_people > max_people` → row error, full
      rollback; price-tier row with non-positive `price` or bad `active_until` → row error,
      full rollback (race not updated)
- [x] write test: non-ADMIN RaceAdmin POST other race → 403; ADMIN POST create → 403
- [x] run tests — must pass before next task

### Task 5: URL routes

**Files:**
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [x] import `RaceEditView`; add `path("races/new/", RaceEditView.as_view(),
      name="add_race")`
- [x] add `path("race/<slug:race_slug>/edit/", RaceEditView.as_view(), name="edit_race")`
      *before* the `name="race"` route
- [x] write test: `reverse("add_race")` and `reverse("edit_race", ...)` resolve to
      `RaceEditView`
- [x] run tests — must pass before next task

### Task 6: `race_form.html` template

**Files:**
- Create: `src/templates/race/race_form.html`
- Modify: `src/apps/race/tests.py`

- [x] port `scratch/Админка - гонка.html` body into `{% block content %}`; drop its
      `<nav>`/`<footer>`/`<style>`/`<script>`; `{% extends "website/base-2.html" %}`;
      link CSS in `{% block extra_head %}`, JS in `{% block footer_js_include %}`
- [x] rename the form's `page` class to `.race-form-page` (scoped wrapper, never bare
      `.page`); add CSRF token and `method="post"`
- [x] render every `Race` field manually with `value="{{ form.field.value|default:'' }}"`,
      `class="control{% if form.field.errors %} has-error{% endif %}"`, errors via
      `{{ form.field.errors|join:", " }}` beneath each input (used `default_if_none` so
      `cost=0` survives)
- [x] render `reg_status` `<select>` looping `reg_status_choices` (`RegStatus.choices`, not
      the scratch options) with `selected` on the current value; image fields as URL
      `<input>` + live `<img>` preview pane
- [x] extend the category repeater header + JS row template with `min_people`/`max_people`
      number inputs; do **not** port the scratch "Регистрация открыта" toggle (field removed)
- [x] add a new "Ценовые периоды" `<section>` (repeater with `active_until` date + `price`
      number inputs and an "Добавить период" button) mirroring the categories card layout
- [x] embed categories as `<script id="categories-data" type="application/json">`
      ({{ categories_data }}) and price tiers as
      `<script id="price-tiers-data" type="application/json">` ({{ price_tiers_data }}); add
      hidden `<input name="categories_json">` and `<input name="price_tiers_json">`; toggle
      title/breadcrumb/sub-line by `is_create`
- [x] write test: edit GET renders the form with current values and both the
      `categories-data` and `price-tiers-data` JSON (use `_script_json` helper)
- [x] run tests — must pass before next task

### Task 7: `race_form.css` + `race_form.js`

**Files:**
- Create: `src/static/css/race_form.css`
- Create: `src/static/js/race_form.js`

- [x] extract scratch `<style>` into `race_form.css`, scope all rules under
      `.race-form-page`; reuse tokens already defined in `race.css` where they match,
      drop duplicates (only `--primary-tint`/`--success-bg`/`--shadow-lg`/`--mono` —
      missing from theme-2 — re-declared, scoped to the wrapper)
- [x] port scratch JS into `race_form.js`: category repeater reading rows from
      `#categories-data` (not the hardcoded seed, incl. `min_people`/`max_people`), char
      counters, slug auto-gen, publish-status toggle, live image preview
- [x] add a price-tier repeater reading rows from `#price-tiers-data` (add/remove rows,
      `active_until` + `price` inputs)
- [x] implement **real** drag-reorder of category rows (scratch handle was decorative)
- [x] add a submit handler serializing current category rows (incl. `id`, `min_people`,
      `max_people`) into `categories_json` **and** current price-tier rows (incl. `id`,
      `price`, `active_until`) into `price_tiers_json`
- [x] (no unit tests — JS verified manually; see Post-Completion)

### Task 8: Entry points on the race page

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/templates/race/race_page.html`
- Modify: `src/apps/race/tests.py`

- [x] in `RacePageView.build_context`, set `context["can_edit_race"] =
      can_edit_race(user, race)` (guard `user is not None`; an admin block keyed on
      `is_race_admin` already exists there — extend it rather than adding a second check)
- [x] in `race_page.html`, show an "Редактировать" button (→ `edit_race`) when
      `can_edit_race`, and a "+ Новая гонка" link (→ `add_race`) when `user.is_superuser`
      (place them in the `cover-actions` block — markup was restructured in #183)
- [x] write test: admin context → `can_edit_race=True` and button renders; regular user →
      `False`/absent and no button
- [x] run tests — must pass before next task

### Task 9: Verify acceptance criteria
- [ ] verify every Overview requirement is implemented (create + edit, all fields,
      inline categories incl. min/max people, inline price tiers, access rules, entry points)
- [ ] verify edge cases: empty category list, empty tier list (price falls back to `cost`),
      cross-race id (categories + tiers), malformed JSON, duplicate code,
      `min_people > max_people`, bad `active_until`, rollback on row error
- [ ] run full suite: `uv run pytest`
- [ ] run `make format && make lint`

### Task 10: [Final] Documentation
- [ ] update `CLAUDE.md` `apps.race` description to mention `RaceEditView`,
      `race_form.html`, `race_form.css`/`race_form.js`, the `add_race`/`edit_race` URL
      names, and that the page inline-edits categories (incl. min/max people) and
      `RacePriceTier` rows
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only.*

**Manual verification:**
- In a browser, confirm category drag-reorder updates row order and that the new order
  persists after save (`order` reflects final positions).
- Confirm adding/removing category rows and submitting round-trips correctly via
  `categories_json` (incl. min/max people); confirm unsaved rows survive a validation error.
- Confirm adding/editing/removing price-tier rows round-trips via `price_tiers_json` and
  that the race page reflects the new active price (`Race.current_price`).
- Confirm header image/logo URL inputs drive the live `<img>` preview.
- Confirm the "Редактировать" / "+ Новая гонка" buttons appear only for the right users.

**Future work (out of scope):**
- Migrate `header_image`/`header_logo` from URL `CharField`s to real file uploads
  (`ImageField` + MEDIA handling + migration) — the UI is intentionally styled toward this.
