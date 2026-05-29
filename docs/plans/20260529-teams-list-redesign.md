# Teams List Page Redesign (unify all_teams / teams2 / my_teams)

## Overview
Redesign the team-list page (`/race/<slug>/category/<id>/teams/`) to match the new
design in `scratch/Команды.html`, and unify it with `/race/<slug>/teams/` (all teams)
and `/race/<slug>/teams/my/` (my teams) onto a **single** new-design page.

- **Problem it solves**: three near-identical Bootstrap views (`AllTeamsView`,
  `TeamsView`, `MyTeamsView`) share one old `teams.html` (extends `website/base.html`).
  They don't match the new visual language already shipped for the race page.
- **Key benefit**: one modern page (extends `base-2.html`) with **client-side** search,
  category-chip filtering, and column sorting, plus a sidebar (Сводка + По категориям
  breakdown). Less duplication, consistent design.
- **Integration**: reuses the existing "race shell" CSS (`race.css` cover banner,
  cover-meta-card, pill, info-list) + the global `theme-2.css` shell, exactly like
  `race_page.html`. New view lives next to `RacePageView` in `apps.race`.

## Context (from discovery)
- **Files/components involved**:
  - `src/website/urls.py` — URL names `all_teams`, `teams2`, `my_teams`.
  - `src/website/views/views_.py` — `AllTeamsView` (~1730), `MyTeamsView` (~1794),
    `TeamsView` (~2232); all render `"teams.html"`. Exported in
    `src/website/views/__init__.py`.
  - `src/apps/race/views.py` — `RacePageView` (established new-design pattern; static
    `build_context`). `AppConfig` uses `label = "race_app"`.
  - `src/templates/teams.html` — old Bootstrap template (orphaned after this change).
    `src/templates/website/teams.html` is a **different** file — DO NOT touch.
  - `src/templates/race/race_page.html` — reference for cover/grid/sidebar markup.
  - `src/static/css/theme-2.css` — global shell (`.grid`, `.card`, `.btn*`,
    `.sidebar`, `.side-head`, `.container`, nav, footer-dark).
  - `src/static/css/race.css` — race-section pieces (`.cover-banner`, `.cover-image`,
    `.cover-meta-card`, `.cover-meta`, `.cover-title`, `.cover-actions`,
    `.pill[.success/.muted]`, `.info-list`, `.side-menu`, `.cats-menu`, posts).
    Comment (lines 2-3) confirms it does NOT duplicate theme-2 shell.
  - `scratch/Команды.html` — full mockup: teams-specific CSS (~lines 84-214) and the
    IIFE JS (~lines 341-471).
- **Models**:
  - `Team` (`src/website/models/models.py:302`): `start_number`, `teamname`, `city`,
    `category2` (FK `Category`), `paid_people`, `ucount`, `athlet1..athlet6`
    (deprecated but still the LIVE source for participant names — team-edit form writes
    them), `owner`. Name fallback: `Без названия {id} ({owner.last_name} {owner.first_name})`.
  - `Category` (`src/website/models/race.py:135`): `name`, `short_name`, `description`,
    `order`, `is_active`; `active_objects` manager.
- **Dependencies identified**:
  - `website` already imports from `apps.race` (`RacePageView`), so importing
    `RaceTeamsView` into `website/urls.py` follows the existing direction (no new cycle).
  - `src/website/views/team.py:161` uses `reverse("teams2", ...)` — URL name unchanged,
    stays valid.
- **Patterns observed**: category `team_count` annotation via `Subquery` over
  `Team(paid_people>0)` grouped by `category2`; new pages extend `base-2.html`, load
  page CSS via `{% block extra_head %}`, use a scoped wrapper class (e.g. `.race-page`).

## Development Approach
- **Testing approach**: Regular (code first, then tests) — UI-heavy change; backend
  view/context logic is the testable surface.
- Complete each task fully before the next; small, focused changes.
- **Every task includes new/updated tests** for code changes in that task (Django
  `TestCase` in `src/<app>/tests.py`). Tests cover success + edge cases.
- **All tests must pass before starting the next task.**
- Run `uv run pytest --reuse-db` after changes. Run `make format && make lint` before
  any commit (per project convention).
- Maintain URL-name backward compatibility (`all_teams`, `teams2`, `my_teams` keep
  resolving and returning 200).

## Testing Strategy
- **Unit tests** (required per task): view resolution + `build_context` output
  (which teams included, `mine` flag, `edit` URL gating, participants string, name
  fallback, category color index), anon `my_teams` redirect, superuser-sees-unpaid.
- **e2e tests**: project has no Playwright/Cypress suite — none added. Client-side JS
  (search/filter/sort) is validated manually (see Post-Completion).

## Progress Tracking
- Mark completed items `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.
- Update this plan if scope changes during implementation.

## Solution Overview
- **Single view** `RaceTeamsView` in `src/apps/race/views.py` (next to `RacePageView`)
  builds the full dataset once via a static `build_context(race, user)` and renders one
  template `src/templates/race/teams.html`. Three URL names point to it with an
  **initial filter** (`all` | `<category_id>` | `mine`).
- **Client-side everything**: server embeds all teams + categories as JSON `<script>`
  blocks; vanilla JS (`src/static/js/teams.js`) does search/filter/sort/render. No page
  reloads (~332 teams — fine).
- **One source of truth for category colors**: each ordered active category gets a
  `colorIdx = position % 8`, shared by chips, breakdown bars, and row badges.

## Technical Details

### `build_context(race, user)` returns
- `race` — the `Race` instance (for cover/title/actions; reuse `reg_open` like
  `race_page.html`: `race.reg_status == RegStatus.OPEN`).
- `categories` — active categories `order_by("order","id")`, annotated with `team_count`
  (Subquery over `Team(paid_people>0)`), plus `colorIdx` assigned in Python by index.
  NOTE: the Subquery returns `None` (not 0) for categories with zero paid teams — coerce
  `team_count or 0` everywhere it is used (chip counts, breakdown `count/max`, summary).
- `categories_json` (string, marked safe) — `[{id, label, count, colorIdx}]` where
  `label = short_name or name`, `count = team_count or 0`.
- `teams_json` (string, marked safe) — `[{num, name, city, parts, cnt, catId, mine, edit?}]`.
- Summary — use the **model helpers** (match `RacePageView`/`race_page.html`, single
  source of truth): `race.team_count()` (= `Team(category2__race, paid_people>0).count()`),
  `race.people_count()` (= `Sum(paid_people) or 0`), `category_count` = len(categories),
  `date` = `race.date`. Do NOT add a separate `Sum("category__team__paid_people")`
  annotation (it can disagree with the helpers).
- `initial_filter` — `"all"` | `str(category_id)` | `"mine"` (passed to template as a
  `data-initial` attribute).

### Which teams go in `teams_json`
- Base set: `Team.objects.filter(category2__race=race, paid_people__gt=0)`.
- If `user.is_authenticated`: **union** the user's own teams in this race (so the «Мои»
  chip is complete even for unpaid). De-duplicate by id.
- If `user.is_superuser`: include **all** teams in the race (preserves today's
  `all_teams` superuser behavior).
- Order by `category2__order`, `start_number`, `id`. `select_related("category2",
  "owner")` to avoid N+1.

### Per-row fields
- `num` = `start_number`.
- `name` — build in **Python** exactly as the old template does (`teams.html:118`),
  NOT via `Team.__str__` (which returns `id{id} - {start_number} {teamname}` and is the
  wrong string): `team.teamname or f"Без названия {team.id} ({team.owner.last_name} {team.owner.first_name})"`.
  (`owner` is a non-null FK; `select_related("owner")`.)
- `parts` = `", ".join(p for p in [athlet1..athlet6] if p)`. NOTE: this intentionally
  **normalizes** the old template's comma-no-space rendering
  (`teams.html:127-134`, `athlet1,athlet2`) to a clean `", "` join — acceptable for a
  redesign. `parts` is also the search field, so the separator is cosmetic for matching.
- `cnt` = if `paid_people != ucount` → `"{paid_people:g}/{ucount}"` else `"{paid_people:g}"`
  (mirror old template's `(paid/ucount)` vs `paid` display; keep it a plain string).
- `catId` = `category2_id`.
- `mine` = `user.is_authenticated and team.owner_id == user.id`.
- `edit` = `/team/{id}` ONLY when `user.is_superuser or team.owner_id == user.id`.

### URL wiring (`src/website/urls.py`)
- `all_teams`  → `RaceTeamsView.as_view()` (default initial `all`).
- `my_teams`   → `RaceTeamsView.as_view(initial="mine")`.
- `teams2`     → `RaceTeamsView.as_view()` (category id read from `category_id` kwarg).
- Anon hitting `my_teams` → redirect `reverse('login') + '?next=' + request.path`.
- Import `RaceTeamsView` from `apps.race.views`.
- **Category-id type**: the `teams2` route is `.../category/<category_id>/teams/` with
  NO `int:` converter (`urls.py:88`), so `category_id` arrives as a **string**. JSON
  `catId` serializes as an **int**. Set `initial_filter = str(category_id)` in the view,
  and in the JS compare category ids with explicit `String(...)` coercion on BOTH sides
  (`chip dataset.cat`, breakdown `dataset.cat`, and `t.catId`) — `"7" === 7` is false.
- **Behavior-parity note (intentional)**: today `teams2` shows only `paid_people>0`
  teams in that category. On the unified page an authenticated owner viewing `teams2`
  will now also see their own *unpaid* team within that category (it's in the dataset and
  the category filter matches). Accepted per the unification decision.

### Template `src/templates/race/teams.html`
- `{% extends "website/base-2.html" %}`, `{% load static %}`.
- `{% block extra_head %}` loads `race.css` **and** new `teams.css`.
- Wrapper `<div class="teams-page">` (NOT bare `.page`).
- `cover-banner` + `cover-meta-card`: breadcrumb (Кольцо 24 / Команды), `<h1>` race
  name, sub (place · date · N категорий). cover-actions — **mirror `race_page.html`
  lines 39-49 exactly** so the two pages agree: «К гонке» (`url 'race' race.slug`)
  always; inside a `reg_open` block, «Добавить команду» (`url 'add_team' race.slug`) for
  authenticated users and «Зарегистрироваться» (`url 'register'`) for anon; «Войти»
  (`url 'login'`) ghost button for anon. No PDF button.
- `.grid` → `main` (`.teams-card`: sticky `.teams-head` with `<h2>` + `.search`;
  `.cat-chips` row [JS]; `.teams-table` sortable thead + tbody [JS]; `.empty` state;
  `.teams-foot` count) + `aside.sidebar` (Сводка `.info-list`: Команд / Участников /
  Категорий / Дата; По категориям `.brk` card [JS] with «Сбросить» link).
- Two `<script type="application/json">` blocks: `teams-data`, `categories-data`.
  `data-initial` attribute on the page root for JS.
- `{% block footer_js_include %}` loads `static 'js/teams.js'`.

### CSS `src/static/css/teams.css` (NEW — teams-specific ONLY)
- Port from mockup ~lines 84-214: `.teams-card`, sticky `.teams-head`, `.search`,
  `.cat-chips` (**renamed** from mockup `.cat-bar` to avoid `race.css` `.cat-bar`
  collision) + `.chip`/`.chip.is-active`, `.teams-table` (sortable thead, tbody rows,
  `.col-*` widths, `.hide-cat`) + the mobile card-reflow `@media` blocks (720px / 600px),
  `.bib`, `.t-name`/`.t-parts`/`.t-city`, `.cat-badge` + `.cat-0..7` palette,
  `.cnt-badge`, `.brk`/`.brk-row`/`.brk-top`/`.brk-bar`, `.empty`, `mark`.
- Scope under `.teams-page` where practical. Do NOT re-declare cover/pill/info-list/grid
  /card/btn (come from `race.css` + `theme-2.css`).

### JS `src/static/js/teams.js` (NEW)
- Adapt mockup IIFE. Read `teams-data` + `categories-data`. Colors/labels come from
  `categories-data` (no hard-coded `CAT_ORDER`).
- State: `activeCat` (`'all'|catId|'mine'`), `query`, `sortKey`, `sortDir` — init from
  `data-initial`.
- Chips: «Все» (total) + «Мои» (only if any row has `mine:true`) + per-category
  (count + color dot). Breakdown: bars `count/max`, colored by `colorIdx`, click toggles
  filter; «Сбросить» resets to all + clears search.
- `render()`: filter by `activeCat` (category id match OR `mine===true`) then `query`
  (substring over `name+city+parts+num`), sort (`num`/`cnt` numeric; `name`/`city`/`cat`
  `localeCompare('ru')`), rebuild tbody with `<mark>` highlight + edit pencil when
  `row.edit` present; toggle `.hide-cat` when a single category is active; toggle empty
  state; update foot text with Russian pluralization. Debounce search ~120ms.

## What Goes Where
- **Implementation Steps** (checkboxes): view + context, URL wiring, template, CSS, JS,
  cleanup, tests.
- **Post-Completion** (no checkboxes): manual browser verification of client-side
  search/filter/sort/responsive; deploy collectstatic note.

## Implementation Steps

### Task 1: Add `RaceTeamsView` + `build_context` in `apps.race`

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/tests.py` (create if absent)

- [x] Add `RaceTeamsView(View)` with class attr `initial = None` and
      `get(self, request, race_slug, category_id=None)`.
- [x] Resolve `race` by slug (404 if missing). Use the model helpers
      `race.team_count()` / `race.people_count()` for summary — do NOT add a
      `Sum("category__team__paid_people")` annotation.
- [x] Implement static `build_context(race, user)` returning categories (with
      `team_count or 0` + `colorIdx`), `categories_json`, `teams_json`, summary stats —
      per Technical Details. Serialize with `json.dumps(..., ensure_ascii=False)` +
      `mark_safe`. `select_related("category2","owner")`.
- [x] Team selection: base `paid_people>0`; union owner's teams if authenticated; all
      teams if superuser; de-dupe by id; build per-row fields (name built in Python per
      template `teams.html:118`, `parts` clean `", "` join, `cnt`, `mine`, `edit` gating).
- [x] Determine `initial_filter`: `str(category_id)` if `category_id` set, else
      `self.initial or "all"`; if `initial == "mine"` and anon → redirect to login with
      `?next=`. Render `"race/teams.html"`.
- [x] Write tests: `build_context` returns expected teams for (a) anon (paid only),
      (b) owner sees own unpaid, (c) superuser sees all; `mine`/`edit` flags correct;
      participants string from `athlet1..6`; name fallback string; `colorIdx` = index % 8;
      **category with zero paid teams → `count == 0` (not None)**.
- [x] Run tests — must pass before Task 2.

### Task 2: Repoint URLs to `RaceTeamsView`

**Files:**
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [x] Import `RaceTeamsView` from `apps.race.views`.
- [x] Point `all_teams`, `my_teams` (`initial="mine"`), `teams2` to `RaceTeamsView`;
      remove old `views.AllTeamsView/TeamsView/MyTeamsView` references in url patterns.
- [x] Write tests: `all_teams`, `teams2`, `my_teams` resolve to `RaceTeamsView` and
      return 200 for a seeded race; anon `my_teams` → 302 to login with `next`;
      `teams2` with valid category renders; invalid race/category → 404.
- [x] Write tests: rendered page exposes the right `data-initial`
      (`"all"` / `"mine"` / `"<id>"`) per URL name, and both embedded JSON blocks parse
      via `json.loads` (catches escaping/serialization issues).
- [x] Run tests — must pass before Task 3.

### Task 3: New template `race/teams.html`

**Files:**
- Create: `src/templates/race/teams.html`

➕ NOTE (Task 2): a **minimal** `src/templates/race/teams.html` stub already exists
(extends `base-2.html`, wrapper `.teams-page` with `data-initial`, and the two JSON
`<script>` blocks `teams-data` / `categories-data`) so Task 2's render tests pass.
Task 3 should **expand/replace** it with the full design markup below (do not assume an
empty file).

- [x] Extend `base-2.html`; `extra_head` loads `race.css` + `teams.css`; wrapper
      `.teams-page` with `data-initial`.
- [x] Build cover-banner + cover-meta-card (breadcrumb, h1, sub, cover-actions per
      decision 5) mirroring `race_page.html`.
- [x] Build `.grid` → main `.teams-card` (sticky head + search, `.cat-chips`,
      `.teams-table` sortable thead + empty tbody, `.empty`, `.teams-foot`).
- [x] Build `aside.sidebar`: Сводка `info-list` (Команд/Участников/Категорий/Дата) +
      По категориям `.brk` card with «Сбросить».
- [x] Emit `teams-data` + `categories-data` JSON `<script>` blocks; load `js/teams.js`
      via `footer_js_include`.
- [x] Verify rendering via a view test asserting key markup is present (chips
      container, table, both JSON script ids, `data-initial`).
- [x] Run tests — must pass before Task 4.

### Task 4: New stylesheet `teams.css`

**Files:**
- Create: `src/static/css/teams.css`

- [x] Port teams-specific rules from mockup; rename chips container to `.cat-chips`
      (avoid `race.css` `.cat-bar` collision); scope under `.teams-page` where practical.
- [x] Include `.cat-0..7` palette, `.teams-table` `.col-*` + `.hide-cat`, and the mobile
      reflow `@media` (720px / 600px).
- [x] Do NOT duplicate cover/pill/info-list/grid/card/btn (from race.css + theme-2.css).
- [x] (No unit test — CSS; covered by manual verification in Post-Completion.)

### Task 5: New script `teams.js`

**Files:**
- Create: `src/static/js/teams.js`

- [x] Adapt mockup IIFE: read both JSON blocks; init state from `data-initial`.
- [x] Build chips (Все + Мои[conditional] + per-category) and breakdown bars (colors
      from `colorIdx`); wire «Сбросить». Coerce category ids to `String(...)` for chip/
      breakdown `dataset.cat` and when comparing against `t.catId` (`data-initial` is a
      string; JSON `catId` is an int).
- [x] Implement `render()`: filter (category/mine) + query substring + sort
      (numeric vs `localeCompare('ru')`) + `<mark>` highlight + edit pencil + `.hide-cat`
      + empty state + Russian-plural foot text; debounce search ~120ms.
- [x] (No unit test — client JS; covered by manual verification in Post-Completion.)

### Task 6: Remove old views, exports, and orphaned template

**Files:**
- Modify: `src/website/views/views_.py`
- Modify: `src/website/views/__init__.py`
- Delete: `src/templates/teams.html` (after grep confirms no refs)

- [ ] Grep the repo for `AllTeamsView`, `TeamsView`, `MyTeamsView`, and
      `"teams.html"` to confirm no remaining references outside what we change.
- [ ] Remove `AllTeamsView`, `MyTeamsView`, `TeamsView` from `views_.py` and their
      exports in `__init__.py`. Do NOT hand-prune imports — let `make lint` (ruff/flake8
      `F401`) flag genuinely-unused ones, since `Sum`/`OuterRef`/`Subquery`/`Count`/
      `reverse`/`HttpResponseRedirect` are shared by many other views in this module.
- [ ] Confirm `src/templates/teams.html` is rendered ONLY by the three removed views
      (`views_.py:1791/1845/2280`) and there is no `{% include %}` of it, then delete it.
      Leave the unrelated `website/teams*.html` family intact — `website/teams.html`
      (via `views_.py:867` `"website/teams%s.html"`), `website/teams_start.html` (914),
      `website/teams_finish.html` (957), `website/teams_protocol.html` (985).
- [ ] Confirm `src/website/views/team.py:161` `reverse("teams2", ...)` still resolves.
- [ ] Run full suite `uv run pytest --reuse-db` — must pass.

### Task 7: Verify acceptance criteria
- [ ] All three URL names render the new page; `teams2` pre-selects its category chip;
      `my_teams` pre-selects «Мои»; anon `my_teams` redirects to login.
- [ ] `teams_json` correctness across anon / owner / superuser; participants + name
      fallback + `cnt` display correct.
- [ ] Run full test suite: `uv run pytest --reuse-db`.
- [ ] `make format && make lint` clean.

### Task 8: [Final] Docs + housekeeping
- [ ] Update `CLAUDE.md` if a new convention emerged (e.g. teams page in `apps.race`,
      `teams.css`/`teams.js` assets) — only if genuinely new.
- [ ] Move this plan to `docs/plans/completed/`.

## Post-Completion
*Items requiring manual intervention or external systems — informational only.*

**Manual verification** (no automated e2e in this project):
- Run `uv run python src/manage.py runserver 0:8080`, open `/race/<slug>/teams/`:
  - Search filters across name/city/participants/number with `<mark>` highlight.
  - Category chips filter; selecting one hides the Категория column.
  - «Мои» chip appears only when logged in with ≥1 team and filters correctly.
  - Column sorting toggles asc/desc (№ & Чел. numeric; name/city/cat ru-locale).
  - Breakdown bars reflect counts; clicking toggles filter; «Сбросить» resets + clears
    search; empty state shows when no matches.
  - Responsive: table reflows to cards at ≤720px / ≤600px.
  - `teams2` URL lands pre-filtered to its category; `my_teams` pre-filtered to «Мои».

**Deployment note:**
- New static assets (`css/teams.css`, `js/teams.js`) require `collectstatic` (runs at
  Docker build time; WhiteNoise serves from `STATIC_ROOT`). No migration needed.
