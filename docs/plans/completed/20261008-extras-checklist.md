# Лист выдачи доп-услуг

## Overview
- Printable A4 sheet for handing out one paid add-on (map, transfer, breakfast, …) per sheet — the add-on twin of
  `race/<slug>/checklist/` (`RaceChecklistView`).
- Problem: organizers hand out paid add-ons at the start but have no list of who paid for what and how many.
- Integration: new organizer-only page next to «Лист выдачи» in the race page's admin card; same gate
  (`_load_race_for_admin`), same CSS (`checklist.css`), same `?category=`/`?column=` behaviour.

## Context (from discovery)
- `src/apps/race/views.py:1453` — `RaceChecklistView` (pattern to follow; builds `members` inline from `athlet1…6`).
- `src/apps/race/views.py:1510` — `_selected_category(request, race)` helper (reuse).
- `src/apps/race/views.py:128` — `RacePageView.build_context` (`can_edit_race` at ~179).
- `src/apps/race/teams_admin.py:18` — `start_number_key(team)` (numeric start-number sort).
- `src/apps/race/models.py` — `RaceExtra` (`race`, `code`, `name`, `order`, `is_active`, `ordering=["order","id"]`),
  `TeamExtra` (`team`, `race_extra`, `count`, `count_paid`).
- `src/templates/race/checklist.html` — standalone template (no base), `checklist.css`.
- `src/templates/race/race_page.html:97` — «Лист выдачи» button in `.admin-actions`.
- `src/website/urls.py:111` — `race_checklist` URL.
- `src/apps/race/tests.py` — checklist tests ~7963; helpers `_make_race`, `_make_category`, `_make_team`, `_ta_admin`.
- `TeamExtra.objects` does **not** go through `TeamManager`, so soft-deleted teams must be excluded explicitly
  (`team__is_deleted=False`).

## Development Approach
- **testing approach**: Regular (code first, then tests in the same task)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run `make format` + `make lint` before committing

## Testing Strategy
- **unit tests**: pytest functions with `@pytest.mark.django_db` in `src/apps/race/tests.py`, using `client` /
  `django_user_model` and existing helpers.
- no e2e tests in the project.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix

## Solution Overview
- One add-on per sheet, chosen by `?extra=<code>`.
- Quantity to hand out = `TeamExtra.count_paid` only (`free_per_team` deliberately ignored — free maps are handed out
  by organizers without the list).
- Template duplicated from `checklist.html` (independent evolution, like `teams_admin.html`); the member-name rule is
  extracted into one helper `_team_members(team)` shared by both views.

## Technical Details
- URL `race/<slug:race_slug>/extras-checklist/`, name `race_extras_checklist`, view `RaceExtrasChecklistView`.
- Extras list: `RaceExtra.objects.filter(race=race).order_by("order", "id")` — inactive included (they may carry paid
  units), shown as `name (откл.)` in the select.
- Selected extra: the one whose `code == request.GET["extra"]`; missing/unknown → first extra; no extras at all →
  `extra=None`, page renders 200 with «У гонки нет доп-услуг».
- Rows query:
  ```python
  TeamExtra.objects.filter(
      race_extra=extra,
      count_paid__gt=0,
      team__is_deleted=False,
      team__category2__race=race,  # excludes category2=None (would 500 on .code)
  ).select_related("team", "team__owner", "team__category2")
  ```
  plus `team__category2=selected_category` when `?category=` is valid; sorted by `start_number_key(te.team)`.
- Row dict: `id`, `number`, `name` (`_team_display_name(team)`), `category` (`category2.code`), `count`
  (`count_paid`), `members` (`_team_members(team)`).
- Context: `race`, `extras`, `extra`, `rows`, `total` (sum of `count`), `categories`, `selected_category`, `column`
  (`?column=` stripped, `[:30]`, default «Выдано»).
- Template: `<title>{{ extra.name }} — {{ race.name }}[ — {{ selected_category.code }}]</title>`; h1
  `{{ race.name }}[ · category]` + `<span>{{ extra.name }} · N ком. · M шт.</span>`; columns
  № · ID · Команда · Категория · Кол-во · Участники · {{ column }}; empty state
  «Нет команд с оплаченной услугой «{{ extra.name }}».».
- Template guards everything extra-specific (title part, h1 span, table, empty row) with `{% if extra %}`; the
  `{% else %}` branch renders «У гонки нет доп-услуг»; title falls back to «Доп-услуги — {{ race.name }}».
- Race page: `build_context` adds `has_extras = RaceExtra.objects.filter(race=race).exists()` computed **only** inside
  the `can_edit_race` branch (public visitors must not pay for the query; default `False`); button «Выдача доп-услуг» after «Лист выдачи» only when `has_extras`.

## What Goes Where
- **Implementation Steps**: view, URL, template, race-page button, tests, CLAUDE.md.
- **Post-Completion**: print check in a real browser.

## Implementation Steps

### Task 1: Extract `_team_members` helper

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/tests.py`

- [x] add `_team_members(team)` returning `[" ".join(n.split()) for n in (athlet1…6) if n.strip()]`
- [x] use it in `RaceChecklistView.get`
- [x] (optional) switch `RaceChecklistView` to `_selected_category` instead of its inline copy
- [x] run existing checklist tests (members rendering) — must still pass unchanged
- [x] check `test_checklist_rows_sorted_numerically_with_members` (tests.py:~7978) covers whitespace/empty slots; add a
      test only if it doesn't
- [x] run tests - must pass before task 2

### Task 2: `RaceExtrasChecklistView` + URL + template

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Create: `src/templates/race/extras_checklist.html`
- Modify: `src/apps/race/tests.py`

- [x] implement view as in Technical Details (gate via `_load_race_for_admin`, reuse `_selected_category`)
- [x] register URL `race_extras_checklist` next to `race_checklist` (import the view)
- [x] create template from `checklist.html`: extra select (with «(откл.)»), category select, column input, print
- [x] tests: anon → redirect to `login`; plain user → 403; superuser without `RaceAdmin` row → 403;
      `RaceAdmin(ADMIN)` → 200
- [x] tests: only `count_paid > 0` rows (team with `count=2, count_paid=0` absent); soft-deleted team absent
- [x] tests: default extra = first by `order`; `?extra=` with a code that exists **only** on another race (e.g.
      `transfer` there, absent here) → falls back to own first extra, no foreign rows; inactive extra with paid units selectable and listed
- [x] tests: `?category=` narrows; numeric start-number sort (`"9"` before `"10"`); `total` = sum of `count_paid`;
      `?column=` default «Выдано»
- [x] tests: race without extras → 200, message shown, `rows == []`, no ««»» artifacts
- [x] tests: team with `category2=None` and a paid `TeamExtra` → page 200, team not listed
- [x] run tests - must pass before task 3

### Task 3: Race page button

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/templates/race/race_page.html`
- Modify: `src/apps/race/tests.py`

- [x] add `has_extras` to `RacePageView.build_context`
- [x] add «Выдача доп-услуг» link after «Лист выдачи», wrapped in `{% if has_extras %}`
- [x] tests: admin sees link when race has a `RaceExtra`; no link when race has none; non-admin sees no link
- [x] run tests - must pass before task 4

### Task 4: Verify acceptance criteria
- [x] verify all requirements from Overview are implemented
- [x] run full test suite: `uv run pytest`
- [x] run `make format` and `make lint`

### Task 5: [Final] Update documentation
- [x] add a short paragraph on `RaceExtrasChecklistView` next to `RaceChecklistView` in CLAUDE.md (one add-on per
      sheet, `count_paid` only, inactive extras included, soft-deleted excluded explicitly, `_team_members` shared)
- [x] move this plan to `docs/plans/completed/`

## Post-Completion
**Manual verification**:
- open the page for a race with maps/transfer, print to PDF, check the A4 layout and the file name from `<title>`.
