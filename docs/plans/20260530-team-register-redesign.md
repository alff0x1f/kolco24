# Team Registration (Add/Edit) Page Redesign

## Overview
Migrate the team add/edit pages from the old Bootstrap design (`base.html`) to the
new `base-2.html` design system (Rubik font, custom CSS tokens, vanilla JS), following
the demo mockup at `src/templates/demo/team-register.html`.

The redesign also removes two pieces of brittle hardcoding:
- **Allowed team sizes** ("Сколько вас?") move from a hardcoded JS `switch` on category
  id into real `Category.min_people` / `Category.max_people` fields.
- **The price ladder** ("Стартовый взнос") moves from hardcoded template rows into a
  real `RacePriceTier` model, with `Race.current_price` as the single source of truth
  for the charged amount (falling back to `Race.cost` for races without tiers).

Add and edit get **two separate, self-contained templates** (`add_team.html`,
`edit_team.html`) — the shared form body is intentionally duplicated (no partial),
while CSS/JS behavior lives in shared static files.

The key money rule preserved throughout: **edit mode may require a top-up (доплата)** —
e.g. team grows 4 → 5 people or buys extra maps — and only the delta is charged:
`(ucount − paid_people) × current_price + (map_count − map_count_paid) × 200`.

## Context (from discovery)
Files/components involved:
- Demo mockup (markup/JS/CSS reference, standalone): `src/templates/demo/team-register.html`
- Current shared template (Bootstrap, serves both add & edit): `src/templates/website/add_team.html`
- Add view: `src/website/views/views_.py` — `class AddTeam` (~line 1729), `get`/`post`
- Edit view: `src/website/views/team.py` — `class EditTeamView` (`get`/`post`), `TeamMemberMoveView`
- Form: `src/website/forms.py` — `class TeamForm` (~line 462): `teamname`, `city`,
  `organization`, `ucount` (ChoiceField 2..6), `category2_id` (built in `__init__`),
  `athlet1..6`, `birth1..6` (with `clean_birthN`), `map_count`
- Models: `src/website/models/race.py` — `class Race` (`cost`, `reg_status`,
  `is_teams_editable`, `slug`), `class Category` (no team-size field today),
  `class RegStatus` (`upcoming`/`open`/`sold_out`)
- `src/website/models/models.py` — `class Team` (~302): `ucount`, `paid_people` (Float),
  `map_count`, `map_count_paid`, `can_be_deleted` (`additional_charge` exists but is
  deprecated — not used by the redesign)
- Admin: `src/website/admin.py` — `CategoryAdmin` (~232, plain ModelAdmin),
  `RaceModelAdmin` (~219, already uses `inlines=[RaceAdminInline]`)
- URLs: `src/website/urls.py` — `add_team` (`race/<slug>/teams/add/` → `AddTeam`),
  `edit_team` (`team/<team_id>/` → `EditTeamView`), `my_teams`, `teams2`, `move_team_member`
- New design base: `src/templates/website/base-2.html` + `src/static/css/theme-2.css`;
  page CSS loaded via `{% block extra_head %}`
- Convention reference (base-2 manual field rendering): `src/templates/website/register.html`
- JSON-island + separate static assets pattern: `src/templates/race/teams.html` +
  `src/static/js/teams.js` + `src/static/css/teams.css`
- Tests: `src/website/tests.py` (pytest-style functions with `client` fixture;
  existing `test_edit_team_redirect_uses_slug`)

Related patterns found:
- base-2 pages MUST hand-render inputs (`<input class="input...">`); `{{ form.field }}`
  emits Bootstrap `form-control` which conflicts with `theme-2.css` (CLAUDE.md rule).
- Page-specific CSS goes to `src/static/css/<page>.css`; do NOT redefine `.page`
  (theme-2 owns it) — use a scoped wrapper class.
- The демо JS config drives a live-cost sidebar, segmented count control, member-row
  reveal, and a maps stepper.

Dependencies identified:
- Both views currently render the same `add_team.html` — splitting requires the edit
  view to switch its `render()` target.
- Payment calc in both views uses `race.cost`; this becomes `race.current_price`.

## Development Approach
- **Testing approach**: Regular (code first, then tests) — chosen for this UI/model work.
- Complete each task fully before moving to the next.
- Make small, focused changes.
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task.
- **CRITICAL: all tests must pass before starting the next task.**
- **CRITICAL: update this plan file when scope changes during implementation.**
- Run tests after each change: `uv run pytest --reuse-db`.
- Before any commit: `make format && make lint` (project memory rule).
- Maintain backward compatibility: races without price tiers must keep working via the
  `race.cost` fallback; categories without explicit sizes default to 2..6.

## Testing Strategy
- **Test style**: follow the existing pytest-function style in `src/website/tests.py`
  (`@pytest.mark.django_db` + `client` fixture), NOT Django `TestCase` — despite
  CLAUDE.md's wording, the real file is pytest-style. Don't switch styles mid-file.
- **Unit tests** (required every task): model field defaults, `Race.current_price`
  fallback + active-tier selection (incl. the same-day `active_until == today` inclusive
  boundary), tier-ladder status flags, backfill mapping, доплата delta calc.
- **View/integration tests** (Django test `client`): `GET add_team` / `GET edit_team`
  render the new templates (200, expected context keys, `data-counts` on options,
  edit-only sections present only in edit), `POST` charges the correct delta.
- **Money-path tests** (critical — added from plan review):
  - `Payment.cost_per_person == race.current_price` after a tier-priced POST.
  - out-of-range `ucount` and over-cap `map_count` are rejected server-side.
  - `is_teams_editable` vs `reg_status` combinations (closed-but-editable,
    open-but-not-editable) gate consistently.
- **E2E**: project has no Playwright/Cypress suite — none added. Manual browser
  verification is listed under Post-Completion.

## Progress Tracking
- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix.
- Document issues/blockers with ⚠️ prefix.
- Keep the plan in sync with actual work done.

## Solution Overview
High-level architecture:
1. **Data layer** — `Category.min_people`/`max_people` and a new `RacePriceTier`
   model with `Race.current_price` + a `price_tier_ladder()` helper that returns tiers
   tagged `past`/`active`/`future`. These are unit-testable and keep templates "dumb".
2. **View layer** — both `AddTeam` and `EditTeamView` build a unified context:
   `current_price`, `paid_people`, `map_count_paid`, `price_tiers` (tagged), category
   options carrying `data-counts`, and `reg_status`/`is_editable` flags. `EditTeamView`
   renders the new `edit_team.html`. The charged amount uses `current_price`.
3. **Presentation layer** — two standalone base-2 templates plus shared
   `team-form.css` / `team-form.js`. JS reads a JSON config island (no inline template
   vars) and computes the live, доплата-aware total mirroring the backend formula.

Key design decisions & rationale:
- **min/max ints over CSV** for team sizes — validated, admin-friendly, and all current
  data is *assumed* contiguous (`[2,3]`, `[4,5,6]`, `[2]`); the view expands the range
  back into the demo's `data-counts="2,3"` contract so the JS is unchanged.
  ⚠️ This contiguity is verified in Task 1 (read the current JS `switch` and confirm no
  non-contiguous set like `{2,4}` exists) before committing to min/max.
- **`current_price` with `cost` fallback** — single source of truth without breaking
  races that have no tiers yet.
- **Two duplicated templates over a partial** — explicit user choice; lower coupling,
  each file readable on its own. CSS/JS stay shared (behavior/styling, not markup).

## Technical Details
- `Category.min_people` (IntegerField, default 2), `Category.max_people` (IntegerField,
  default 6). Segment renders one button per `range(min, max+1)`.
- Backfill mapping (from today's JS `switch`): category ids `8,16` → (2,3);
  `9,13,17,21,24` → (4,6); all others → (2,2). Implemented as a pure helper so it is
  unit-testable and reusable by the data migration.
- `RacePriceTier`: `race` (FK, `related_name="price_tiers"`), `price` (Int),
  `active_until` (DateField, inclusive), `order` (Int, default 0). Ordering by
  (`active_until`, `order`).
- `Race.current_price` (property): price of the active tier = earliest tier with
  `active_until >= today`; if none active (all past) use the last tier; if no tiers,
  return `self.cost`.
- `Race.price_tier_ladder()`: returns a list of `{tier, status}` where status ∈
  {`past`, `active`, `future`} for template rendering.
- **Charged amount — `cost_per_person` must move with the multiplier.** In both views the
  local `cost_now` both multiplies the people count AND is stored as
  `Payment.cost_per_person`. `Team.update_team` (models.py:287) back-calculates
  `paid_for = withdraw_amount / payment.cost_per_person` on partial payments. Therefore
  set `cost_now = race.current_price` (one change) so the multiplier and the stored
  per-person price stay identical — otherwise partial payments credit the wrong people
  count. Do NOT leave `cost_per_person = race.cost` while charging `current_price`.
- **`additional_charge` is deprecated — do NOT account for it.** The full charge is just
  `(ucount − paid_people) × current_price + (map_count − map_count_paid) × 200`. Don't
  add it to the formula, JS config, sidebar, or tests.
- JS config island `<script type="application/json" id="teamFormConfig">`:
  `{ currentPrice, paidPeople, mapCountPaid, mapPrice: 200, freeMaps: 2, isEdit }`.
- Live total (JS, mirrors backend): `max(0, (ucount − paidPeople) × currentPrice
  + (maps − mapCountPaid) × mapPrice)`. When result is a top-up in edit mode, header
  reads "К доплате" and a "уже оплачено за N чел." line shows when `paidPeople > 0`.
- Maps constant: 200 ₽/map, 2 free per team — kept as a hardcoded constant, mirrored in
  the view and the JS config.
- **Server-side guards (not just client-side).** The segmented control / stepper cap
  values in the browser only; the view/form must also enforce `ucount ∈
  [category.min_people, category.max_people]` and `map_count ≤ max(0, ucount − free_maps)`
  against a crafted POST.
- **Status flag vs server gate.** Add accepts/rejects on `race.is_teams_editable`
  (views_.py:1755) and charges only when `reg_status == OPEN` (views_.py:1776); edit gates
  on `is_teams_editable` (team.py:54,87). The visible race-chip tag + "регистрация
  закрыта" warning are driven by `reg_status`, but the actual submit gate stays on
  `is_teams_editable`. Keep these consistent in the template (disable submit on the same
  flag the server enforces); document the intended difference where they diverge.

## What Goes Where
- **Implementation Steps** (`[ ]`): model fields + migrations + backfill, model
  helpers, admin, view context changes, two templates, shared CSS/JS, tests.
- **Post-Completion** (no checkboxes): manual browser verification of the live-cost
  flow and SBP redirect; seeding real `RacePriceTier` rows for the active race via admin.

## Implementation Steps

### Task 1: Add team-size fields to Category (+ migration, backfill, admin)

**Files:**
- Modify: `src/website/models/race.py`
- Create: `src/website/migrations/00XX_category_team_sizes.py` (schema + data migration)
- Modify: `src/website/admin.py`
- Modify: `src/website/tests.py`

- [x] ⚠️ first verify contiguity: read the current JS `switch` in `add_team.html` and confirm every produced set is a contiguous range (no `{2,4}`); record the result here. If non-contiguous sets exist, stop and re-raise with the user before using min/max. — VERIFIED: switch produces `[2,3]` (ids 8,16), `[4,5,6]` (ids 9,13,17,21,24), default `[2]`; all contiguous, no gaps.
- [x] add `min_people` (IntegerField, default 2) and `max_people` (IntegerField, default 6) to `Category`
- [x] create migration: `uv run python src/manage.py makemigrations website` (schema), then add a `RunPython` data migration that backfills existing rows — `0067_category_max_people_category_min_people.py`
- [x] make the data migration **self-contained**: use `apps.get_model("website", "Category")` and an inlined mapping dict (ids 8,16→(2,3); 9,13,17,21,24→(4,6); else (2,2)); it is a no-op when those ids are absent (fresh/test DB). Do NOT import a function from `models/race.py` into the migration
- [x] add `min_people`, `max_people` to `CategoryAdmin.list_display` and fields
- [x] write tests: defaults on a new `Category` (2/6); the backfill mapping returns correct tuples for representative ids (test the mapping dict directly, mirroring the migration) — `test_category_team_size_defaults`, `test_category_backfill_mapping`
- [x] run tests — must pass before Task 2 — full suite 84 passed; `make format && make lint` clean

### Task 2: Add RacePriceTier model + Race.current_price + ladder helper (+ migration, admin)

**Files:**
- Modify: `src/website/models/race.py`
- Create: `src/website/migrations/00XX_race_price_tier.py`
- Modify: `src/website/admin.py`
- Modify: `src/website/tests.py`

- [x] add `class RacePriceTier(Model)` (`race` FK `related_name="price_tiers"`, `price` Int, `active_until` Date, `order` Int default 0; `Meta.ordering = ["active_until", "order"]`)
- [x] add `Race.current_price` property: active tier = earliest with `active_until >= today`; if all past, last tier; if no tiers, `self.cost` — uses shared `_active_tier_index` helper
- [x] add `Race.price_tier_ladder()` returning `[{"tier": t, "status": "past|active|future"}]`
- [x] create migration: `uv run python src/manage.py makemigrations website` — `0068_racepricetier.py`
- [x] register `RacePriceTier` as a `TabularInline` on `RaceModelAdmin` — `RacePriceTierInline`
- [x] write tests: `current_price` picks active tier; falls back to `cost` with no tiers; uses last tier when all past; the same-day boundary (`active_until == today`) counts as active (inclusive `>=`); `price_tier_ladder()` flags past/active/future correctly — also `price_tier_ladder` all-past + empty cases
- [x] run tests — must pass before Task 3 — full suite 91 passed; `make format && make lint` clean

### Task 3: Unify view context + switch charged cost to current_price + edit renders new template

**Files:**
- Modify: `src/website/views/views_.py` (`AddTeam`)
- Modify: `src/website/views/team.py` (`EditTeamView`)
- Modify: `src/website/forms.py` (`TeamForm` server-side guards)
- Modify: `src/website/tests.py`

- [x] in both views, build category options carrying `data-counts` (expanded from `min_people..max_people`) — pass a list of `{id, label, counts}` to the template (manual `<select>`, not the Django widget) — `build_category_options`/`build_team_form_context` in `views_.py`, reused by `EditTeamView`
- [x] add context keys to both views: `current_price`, `paid_people`, `map_count_paid`, `price_tiers` (from `price_tier_ladder()`), `reg_open`/`is_editable`, `reg_status`, `map_price=200`, `free_maps=2`
- [x] set `cost_now = race.current_price` in `AddTeam.post` and `EditTeamView.post` so the SAME variable both multiplies the people count and is stored as `Payment.cost_per_person` (do NOT leave `cost_per_person = race.cost`); доплата formula otherwise unchanged
- [x] add server-side validation (in `TeamForm` or the view): `ucount ∈ [category.min_people, category.max_people]` and `map_count ≤ max(0, ucount − free_maps)`; reject otherwise. `clean_map_count` now coerces to int; cross-field caps live in `TeamForm.clean`
- [x] keep the submit gate on the same flag the server enforces (`is_teams_editable`); the race-chip status tag is driven by `reg_status` (display only) — context exposes both `is_editable` and `reg_status`
- [x] switch `EditTeamView` `render()` target from `website/add_team.html` to `website/edit_team.html` in `get`, `post` success-invalid branch, and any other render — `edit_team.html` created as a working copy (Bootstrap) so the render succeeds; Task 6 rewrites it on base-2
- [x] write tests: `GET add_team` 200 with `current_price`/`price_tiers`/option `data-counts` in context; `GET edit_team` 200 renders `edit_team.html`; `EditTeamView.post` charges the correct delta when ucount grows (e.g. 4→5) and when maps added; no charge when nothing added
- [x] write money-path tests: `Payment.cost_per_person == current_price` after a tier-priced POST; out-of-range `ucount` and over-cap `map_count` rejected; `is_teams_editable`/`reg_status` gate combinations behave consistently
- [x] run tests — must pass before Task 4 — full suite 101 passed; `make format && make lint` clean

### Task 4: Shared static assets — team-form.css and team-form.js

**Files:**
- Create: `src/static/css/team-form.css`
- Create: `src/static/js/team-form.js`

- [x] port the demo's CSS into `team-form.css` under a scoped `.team-register` wrapper; do NOT redefine `.page` (theme-2 owns it) — only the demo's form-specific selectors ported (race-chip, section-title, seg, members, maps-row/stepper, foot, summary/sum/tier/note, reg-closed-warn, edit-only pay-history/danger-zone, anim); tokens/.page/.grid/.card/.btn/.form-grid/.field/.input/.check/.sidebar/.side-head come from theme-2 and are NOT redefined; keyframes renamed `teamFormRowIn`/`teamFormUp` to avoid global collision
- [x] port the demo IIFE into `team-form.js`: read counts from each option's `data-counts` (no `switch`); build the segmented control; show/hide member rows with reveal; maps stepper capped at `max(0, ucount − freeMaps)` — also syncs hidden `#ucountInput`/`#mapCountInput` so the real form submits the values
- [x] implement доплата-aware live cost reading the `#teamFormConfig` JSON island: `max(0, (ucount − paidPeople) × currentPrice + (maps − mapCountPaid) × mapPrice)`; header "К доплате" + "уже оплачено за N чел." line when `paidPeople > 0` — credit valued at `currentPrice` to match the backend formula exactly; lines sum to the clamped total
- [x] consent gates submit in add mode; gate skipped in edit mode; show "регистрация закрыта" warning when `reg_status != open` and amount due > 0 — warning element is rendered only when reg closed (server-driven) and JS toggles it on `due > 0`; optional `data-label-due`/`data-label-zero` on submit buttons swap text for Task 6's "Сохранить и доплатить"
- [x] write tests: none for static assets directly — behavior is covered via the template/integration tests in Tasks 5–6 (asset presence asserted there)
- [x] run tests — must pass before Task 5 — full suite 101 passed; `make format && make lint` clean

### Task 5: Rewrite add_team.html on base-2 (add flow)

**Files:**
- Modify: `src/templates/website/add_team.html`
- Modify: `src/website/tests.py`

- [ ] extend `base-2.html`; load `team-form.css` via `{% block extra_head %}` and `team-form.js` via `{% block footer_js_include %}`; wrap content in `.team-register`
- [ ] build header: breadcrumbs (→ `my_teams`), page-head, race-chip with real `race.name`/`race.date`/status tag from `reg_status`
- [ ] hand-render the form card: «Команда» (name/city/club), «Дистанция и состав» (manual category `<select>` with `data-counts`, segmented count, member rows 1–6, maps stepper); inputs use `name=` matching `TeamForm`, errors via `{{ team_form.field.errors|join:", " }}`
- [ ] render sidebar: live «К оплате» summary, real `price_tiers` ladder, «Нужна помощь?» contact; emit `#teamFormConfig` JSON island
- [ ] consent checkbox enabled and gating submit; foot copy "После сохранения — оплата через СБП"; `{% csrf_token %}`
- [ ] write tests: `GET add_team` renders `add_team.html`, contains the segmented control / `data-counts` / consent input / config island, links `team-form.js`+`team-form.css`
- [ ] run tests — must pass before Task 6

### Task 6: Create edit_team.html on base-2 (edit flow + edit-only sections)

**Files:**
- Create: `src/templates/website/edit_team.html`
- Modify: `src/website/tests.py`

- [ ] extend `base-2.html` (same assets/wrapper as add); duplicate the shared form body (no include, per decision)
- [ ] consent pre-checked & disabled; foot copy reflects edit/доплата; submit reads "Сохранить" / "Сохранить и доплатить" when amount due > 0
- [ ] edit-only section «История оплат» from `payments` (restyled to base-2 note/card idiom)
- [ ] edit-only section «Переносы участников» from `member_moves` + `team_move_form` (danger style), shown when `paid_people` and editable; posts to `move_team_member`. The view already builds `TeamMemberMoveForm(race_id=...)` — keep that kwarg; hand-render its `to_team`/`moved_people` fields per base-2 rules
- [ ] edit-only «Удалить команду» when `can_be_deleted` and owner/superuser — POST `delete_team=1` with JS confirm
- [ ] write tests: `GET edit_team` renders `edit_team.html`, shows edit-only sections (payments/move/delete) per state, consent disabled+checked; add page does NOT contain these sections
- [ ] run tests — must pass before Task 7

### Task 7: Verify acceptance criteria
- [ ] verify add & edit render on base-2 matching the demo (header, form card, sidebar)
- [ ] verify team sizes come from `Category.min_people/max_people` (segment + `data-counts`)
- [ ] verify price ladder comes from `RacePriceTier`; `current_price` drives both display and charged amount; `race.cost` fallback works for a tier-less race
- [ ] verify доплата: edit charges only the delta (ucount grow, maps added); no charge when unchanged; "регистрация закрыта" warning when applicable
- [ ] verify money path: `Payment.cost_per_person` equals the charged per-person price (so partial-payment `paid_for` back-calc is correct); server rejects out-of-range `ucount` / over-cap `map_count`
- [ ] run full suite: `uv run pytest --reuse-db`
- [ ] `make format && make lint`

### Task 8: [Final] Update documentation
- [ ] update CLAUDE.md if new patterns are worth recording (e.g. the `team-form.*` shared-asset + JSON-island convention, `RacePriceTier`/`current_price` source of truth)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only.*

**Manual verification:**
- Browser-test the live-cost sidebar in add and edit (count changes, maps stepper,
  consent gate) and the SBP redirect on submit (use a test/staging VTB config).
- Verify edit-only flows end to end: payment history display, member transfer
  (irreversible — confirm copy), and team deletion when `can_be_deleted`.
- Confirm responsive layout at ≤940px (sidebar stacks) and ≤600px (full-bleed cards).

**External system / data updates:**
- Seed real `RacePriceTier` rows for the active race via Django admin (price + dates);
  until then the race falls back to `Race.cost`.
- Review/adjust `Category.min_people/max_people` for the active race in admin after the
  backfill migration runs in production.
