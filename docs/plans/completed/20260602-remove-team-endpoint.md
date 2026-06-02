# Remove the legacy `/team/` (`my_team`) endpoint cluster

## Overview
`/team/` (`path("team/", views.my_team, name="my_team")`) is an old self-redirect into the legacy
year-2024 team editor. The live flow is now `edit_team` (`/team/<team_id>/`, `EditTeamView`) plus the
`my_teams` listing page. The whole `my_team` family — the superuser race-day screens
(`team_predstart`/`team_start`/`team_finish`), their list views (`teams_predstart`/`teams_start`/
`teams_finish`), the `new_team` create shim, and the `team_admin`/`TeamFormAdmin` editor — is reachable
only from itself and is dead/stale. This plan removes the entire cluster.

**Benefit:** deletes a large block of dead code (1 view module section, 1 form class, 7 templates,
1 JS file, ~7 URL patterns), removing confusing duplicate "team editor" surfaces and shrinking the
attack/maintenance surface. Behavior-preserving for all live flows.

## Context (from discovery / brainstorm)
- **Files/components involved:**
  - `src/website/urls.py` — URL patterns
  - `src/website/views/views_.py` — view functions + a forms-import line
  - `src/website/views/__init__.py` — re-export list
  - `src/website/forms.py` — `TeamForm.init_vals`, `TeamFormAdmin`, defensive `TeamForm.__init__`
  - `src/templates/website/` — 7 templates
  - `src/static/js/team_admin.js`
  - `src/website/tests.py` — 3 tests
- **Patterns found:** the `my_team` view is shared by `team_predstart/start/finish` (they call
  `my_team(request, teamid, template)`); `team_admin`/`TeamFormAdmin`/`team_admin.js` are referenced
  **only** by the four superuser templates being deleted, so they go orphaned transitively.
- **Already-dead code:** `re_path("^team/(?P<teamid>[0-9a-f]{16})/", views.my_team)` (urls.py L103) is
  shadowed by `path("team/<team_id>/", EditTeamView, name="edit_team")` (L23), which matches first —
  removing it is behavior-preserving.

## Development Approach
- **Testing approach:** Regular (this is a code-removal task — no new functionality; the test work is
  repoint/delete/genericize, then prove the full suite stays green).
- Complete each task fully before the next; keep changes small and focused.
- **Every task that touches code is followed by running the relevant tests; the full suite must pass
  before starting the next task.**
- Removal-specific rule: after each deletion task, run `uv run pytest` and confirm no import errors,
  no `NoReverseMatch`, no broken-template errors.
- Update this plan file if scope changes during implementation.

## Testing Strategy
- **Unit/integration tests:** the project's pytest suite (`uv run pytest`) is the safety net. The three
  affected tests live in `src/website/tests.py`:
  - `test_login_required_redirects_to_login_url` — **repoint** off `/team/`.
  - `test_my_team_post_does_not_500` — **delete** (endpoint gone).
  - `test_team_form_defensive_init_with_querydict` — **keep**, genericize docstring.
- **No e2e suite** in this project — nothing to add there.
- **Lint:** `make lint` (ruff/black/isort/flake8) catches orphaned `F401` imports left after deletions.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep this plan in sync with actual work done

## Solution Overview
Pure deletion, executed in dependency-safe order so the tree never references something already gone:
1. delete templates + JS (leaf assets) →
2. remove URL patterns (so nothing routes to soon-deleted views) →
3. delete view functions + form code →
4. fix the `__init__.py` re-export list + `views_.py` import block →
5. update the three tests →
6. verify (grep sweep + pytest + lint).

**Kept untouched (do not remove):** `edit_team`, `move_team_member`, `pay_team`, `team_points`,
`teams` (`/teams/`) + `teams.html`, the `my_teams` listing, `Team.new_team(...)` model method, and the
`team_start_log`/`team_finish_log` models + `api/views/teams.py` (all unrelated to the removed views).

## Technical Details
The defensive `TeamForm.__init__` `try/except int(race_id)` (forms.py L364) is **kept** as generic
robustness — only its comment (L360–363) is rewritten to drop the `my_team` reference. The `teams` view
(views_.py L541–642) **stays**; its now-unused `template=""` parameter may optionally be dropped since it
will only ever be `""` after `teams_predstart` is removed.

## What Goes Where
- **Implementation Steps** (`[ ]`): all deletions, edits, and test updates — fully achievable in this repo.
- **Post-Completion** (no checkboxes): manual prod sanity-check that no external bookmark/integration
  depends on `/team/`, `/team_admin/`, `/newteam/`, `/team_predstart|start|finish/<id>/`.

## Implementation Steps

### Task 1: Delete leaf templates and JS

**Files:**
- Delete: `src/templates/website/my_team.html`
- Delete: `src/templates/website/team_predstart.html`
- Delete: `src/templates/website/team_start.html`
- Delete: `src/templates/website/team_finish.html`
- Delete: `src/templates/website/teams_predstart.html`
- Delete: `src/templates/website/teams_start.html`
- Delete: `src/templates/website/teams_finish.html`
- Delete: `src/static/js/team_admin.js`

- [x] delete the 7 templates listed above
- [x] delete `src/static/js/team_admin.js`
- [x] confirm no surviving template `{% include %}`/`{% extends %}`/`{% url %}`s any of these
      (grep `team_admin`, the 7 filenames) — must be empty
- [x] run `uv run pytest` — suite still green (no template not-found at import/collection time)

### Task 2: Remove URL patterns

**Files:**
- Modify: `src/website/urls.py`

- [x] delete L22 `path("team/", views.my_team, name="my_team")`
- [x] delete L26 `path("team_admin/", views.team_admin, name="team_admin")`
- [x] delete the already-commented `teams_predstart/start/finish` lines (L100–102)
- [x] delete L103 `re_path("^team/(?P<teamid>[0-9a-f]{16})/", views.my_team)` (already dead — shadowed by `edit_team`)
- [x] delete L104–106 `re_path` for `team_predstart` / `team_start` / `team_finish`
- [x] delete L108 `path("newteam/", views.new_team, name="new_team")`
- [x] keep `edit_team`, `move_team_member`, `pay_team`, `team_points`, `teams` intact
- [x] run `uv run pytest` — no `NoReverseMatch` from surviving `{% url %}`/`reverse()` callers
      (production templates confirmed clean via grep; the only 2 NoReverseMatch failures are
      test-internal `my_team` references in `test_login_required_redirects_to_login_url` and
      `test_my_team_post_does_not_500`, both slated for repoint/delete in Task 5; 323 other tests pass)

### Task 3: Delete view functions and form code

**Files:**
- Modify: `src/website/views/views_.py`
- Modify: `src/website/forms.py`

- [x] in `views_.py`, delete `my_team` (~774–835), `team_predstart` (~838–841),
      `team_start` (~844–847), `team_finish` (~850–853), `team_admin` (~856–867)
- [x] in `views_.py`, delete `teams_predstart` (~645–646), `teams_start` (~649–689),
      `teams_finish` (~692–732), `new_team` (~1085–1091)
- [x] in `views_.py`, remove `TeamFormAdmin` from the forms-import block (~L40)
- [x] keep the `teams` view (~541–642); drop its now-unused `template=""` param (after
      `teams_predstart` is gone it is only ever called arg-less) and hardcode `"website/teams.html"` (~L642)
- [x] in `forms.py`, delete `TeamForm.init_vals` (~440–478)
- [x] in `forms.py`, delete `TeamForm.access_possible` (~479–487) — confirmed via grep its only caller
      was `my_team`; no other callers, removed.
- [x] in `forms.py`, delete the entire `TeamFormAdmin` class (~681–879, incl. its `clean`/`init_vals`/`save`)
- [x] in `forms.py`, **keep** the defensive `TeamForm.__init__` `try/except int(race_id)` (~L364);
      rewrite the comment (~L360–363) to drop the `my_team` mention (frame as generic robustness
      against a bad/`None` `race_id`)
- [x] fix orphaned imports precisely (do NOT bulk-delete on the F401 hint):
      - `views_.py` `from time import gmtime, strftime, time` → `from time import gmtime, strftime`
      - `forms.py` `TeamAdminLog` import dropped (the `TeamAdminLog` **model** stays)
      - `forms.py` now-unused `from datetime import timedelta` dropped (was only used by `TeamFormAdmin`)
      - ⚠️ `PaymentsYa` and `PaymentLog` in `views_.py` **survive** (still used) — left untouched
      - `make lint` confirms no remaining `F401`
- [x] run `uv run pytest` — 323 pass; only the 2 known test-internal `my_team` failures remain
      (`test_login_required_redirects_to_login_url`, `test_my_team_post_does_not_500`), both slated
      for Task 5. No import errors, no `NameError`.

### Task 4: Fix re-export list

**Files:**
- Modify: `src/website/views/__init__.py`

- [x] remove from the `from .views_ import (...)` list: `my_team`, `new_team`, `team_admin`,
      `team_finish`, `team_predstart`, `team_start`, `teams_finish`, `teams_predstart`, `teams_start`
      (done in Task 3 — required for the package to import after the view deletions)
- [x] keep `teams`, `team_admin`'s neighbors that survive, and all other exports intact
- [x] run `uv run pytest` — package imports cleanly

### Task 5: Update the three affected tests

**Files:**
- Modify: `src/website/tests.py`

- [x] `test_login_required_redirects_to_login_url` (~L371): repoint `client.get("/team/")` to
      `client.get("/page/<slug>/edit/")` (e.g. `/page/x/edit/`) — `edit_page` is a surviving
      `@login_required` view, preserving the decorator→`LOGIN_URL` semantic; keep asserts
      `status_code == 302` and `"/accounts/login/" in response.url`
- [x] `test_my_team_post_does_not_500` (~L2247): delete (its endpoint no longer exists)
- [x] `test_team_form_defensive_init_with_querydict` (~L2258): keep; genericize the docstring **and**
      the inline comment at ~L2263 (`# race_id is a QueryDict, as my_team passes it`) to drop the
      `my_team` reference (the form must still tolerate a non-int/`None` `race_id`)
- [x] run `uv run pytest src/website/tests.py` — all three behave as expected

### Task 6: Verify acceptance criteria
- [x] grep sweep returns empty for: word-boundary `my_team` (NOT `my_teams`), `team_admin`,
      `TeamFormAdmin`, the `new_team` **view** (NOT `Team.new_team` model method),
      `teams_predstart`/`teams_start`/`teams_finish`, and the 7 deleted template filenames
      (all `src/` hits gone; remaining matches are only in `docs/plans/*` and `CLAUDE.md`)
- [x] confirm survivors untouched: `edit_team`, `my_teams`, `teams`/`teams.html`,
      `Team.new_team` model method (`models.py:418`, callers in `apps/accounts/forms.py`),
      `team_start_log`/`team_finish_log`
- [x] run full suite: `uv run pytest` — 324 passed
- [x] run `make format && make lint` — no Makefile tracked; ran the underlying tools directly
      (ruff --fix/black/isort, then ruff/black --check/isort --check/flake8): all clean, no changes

### Task 7: [Final] Documentation
- [x] update `CLAUDE.md` if any removed item is referenced there (removed the stale year-2023/2024
      superuser `my_team` editor note; rewrote the defensive `TeamForm.__init__` rationale to drop the
      `my_team` mention and frame it as generic robustness; dropped `TeamFormAdmin` from the
      people-limits note. The `my_teams` survivor reference left intact.)
- [x] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only*

**Manual verification:**
- Sanity-check production for inbound traffic/bookmarks to the removed paths (`/team/`, `/team_admin/`,
  `/newteam/`, `/team_predstart|start|finish/<id>/`). These now 404 by decision (matching the existing
  "old paths 404, no redirects" precedent from the auth move). No redirect shims are being added.
- No mobile-app impact expected: the app uses the `api` namespace, not these web routes.
