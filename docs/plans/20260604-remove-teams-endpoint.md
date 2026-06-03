# Remove the legacy `/teams/` endpoint

## Overview
Remove the old `/teams/` endpoint — a hardcoded teams listing (`year="2024"` /
`dist` / `category` string filters) rendered by `website/teams.html`. It was
superseded long ago by the slug-based `race/<slug:race_slug>/teams/`
(`RaceTeamsView`, URL name `all_teams`), which does search/filter/sort
client-side off live data.

This is a **pure deletion**: one URL route, one view function, one re-export,
one template. No models, no migrations, no DB tables involved.

While here, also sweep the **adjacent dead** `teams_protocol` view in the same
file — it has no URL pointing at it, is **not exported** in
`views/__init__.py`, and its template `website/teams_protocol.html` **does not
exist** (it would 500 if ever reached). Removing it together keeps the file
clean and avoids a second pass.

Decisions confirmed in brainstorm:
1. **Old URL just 404s** — the route is deleted, no redirect. Matches the
   project's documented "routes now 404, no redirects, by decision" convention
   (login/register, manual-payment stack, transfer/breakfast removals).
2. **No sensible redirect target anyway** — `/teams/` carries no race in the
   path and renders fixed 2024 data, so there is nothing to redirect to.
3. **Sweep the dead `teams_protocol`** in the same PR.

## Context (from discovery)

**Files/components involved (footprint verified via grep):**
- `src/website/urls.py:21` — `path("teams/", views.teams, name="teams")`. The
  line-22 comment "Int-id redirects must come before slug patterns" refers to
  the redirect block **below** it, **not** to teams — leave it.
- `src/website/views/views_.py` — `def teams(request)` (lines ~172–273, renders
  `website/teams.html`) and `def teams_protocol(request)` (lines ~276–301,
  unreachable: no URL, not exported, template missing).
- `src/website/views/__init__.py:19` — the `teams,` entry in the
  `from .views_ import (...)` block. (`teams_protocol` is **not** in this list.)
- `src/templates/website/teams.html` — used **only** by the removed `teams`
  view.

**Related patterns found:**
- Sibling chore PRs `20260602-remove-team-endpoint.md` and
  `20260603-remove-transfer-breakfast.md` establish the deletion style: remove
  in dependency order, old routes 404, verify by grep + full suite.
- Tests are **pytest-style functions** with `@pytest.mark.django_db` and the
  `client` fixture — not Django `TestCase`.

**Import safety (verified):** no orphaned imports after removal. `timedelta`
(used at line ~296 inside `teams_protocol`) is still used at lines ~334–421;
`Team` / `render` are used throughout the file. **Do not touch** the
`from datetime import datetime, timedelta, timezone` line or other imports.

**Verified absences (low blast radius):**
- **No callers of the URL name** — no `{% url 'teams' %}`, no
  `reverse("teams")`/`redirect("teams")` anywhere. (Distinct from the live
  `teams2` / `my_teams` / `all_teams` / `api_teams` names — all kept.)
- **No template references** `website/teams.html` — nothing `extends`/`include`s
  it; only the deleted `teams` view `render()`s it.
- **No tests** target the legacy route, the `teams` name, `teams_protocol`, or
  `teams.html`. The hit at `tests.py:91-93` is the unrelated int→slug redirect
  for `race/<id>/teams/` and stays untouched.

## Development Approach
- **Testing approach**: Regular (code first). This is a deletion; the only test
  added is an **optional regression test** asserting the route now 404s. There
  is no legacy behavior worth re-testing.
- Work top-down so the tree never references a removed symbol:
  views → export → url → template → test → verify.
- Run `make format && make lint` before committing (project requirement).

## Testing Strategy
- **Unit tests**: one small regression test added (`/teams/` → 404). No other
  tests change; the existing suite must stay green, proving nothing depended on
  the removed symbols.
- **No e2e**: project has no UI e2e harness; pytest is the gate.
- Full gate: `uv run pytest --reuse-db` must pass before the plan is done.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep this plan in sync with actual work

## Solution Overview
Delete dead code in dependency order. Remove both view functions from
`views_.py` first, then the stale `teams` re-export, then the URL route, then
the orphaned template. Add a one-line 404 regression test mirroring the existing
redirect test. Verify with `grep` that nothing dangles, `manage.py check` is
clean, and the full suite passes.

## Technical Details

**Views to delete** (`src/website/views/views_.py`) — by symbol, not line
(deletions shift line numbers): `teams` and `teams_protocol`. Leave every other
function and all module-level imports intact (`timedelta`, `Team`, `render`,
etc. remain in use elsewhere).

**Re-export to remove** (`src/website/views/__init__.py`): drop `teams,` from
the `from .views_ import (...)` tuple. Keep `teams_api` and every other export.

**Route to remove** (`src/website/urls.py`): delete the single line
`path("teams/", views.teams, name="teams")`. All other `teams`-bearing routes
(`all_teams`, `my_teams`, `teams2`, the `RaceIdRedirectView` int routes,
`api_teams`, `api_teams_times`) are current and stay.

**Regression test** (`src/website/tests.py`):
```python
@pytest.mark.django_db
def test_legacy_teams_endpoint_returns_404(client):
    assert client.get("/teams/").status_code == 404
```

## What Goes Where
- **Implementation Steps** (checkboxes): all code/template deletions, the
  regression test, grep verification, and the test run — all doable in this repo.
- **Post-Completion** (no checkboxes): deploy note that `/teams/` now 404s.

## Implementation Steps

### Task 1: Delete the `teams` and `teams_protocol` views

**Files:**
- Modify: `src/website/views/views_.py`

- [x] delete `def teams(request)` (the hardcoded 2024 listing that renders `website/teams.html`)
- [x] delete `def teams_protocol(request)` (dead: no URL, not exported, template missing)
- [x] confirm no module-level imports are now orphaned — `timedelta`, `Team`, `render` all still used elsewhere; **do not** edit the import lines
- [x] sanity: `views_.py` lints clean (a full `manage.py check` still fails on the stale `__init__.py` export — fixed in Task 2)

### Task 2: Prune the views package re-export

**Files:**
- Modify: `src/website/views/__init__.py`

- [x] remove `teams,` from the `from .views_ import (...)` list
- [x] keep all surviving exports (`teams_api`, `AddTeam`, `RaceIdRedirectView`, …)

### Task 3: Remove the URL route

**Files:**
- Modify: `src/website/urls.py`

- [x] delete `path("teams/", views.teams, name="teams")`
- [x] leave the line-22 redirect-ordering comment and the `RaceIdRedirectView` block intact
- [x] `uv run python src/manage.py check` — must pass now (URLConf resolves, no missing view import)

### Task 4: Delete the template

**Files:**
- Delete: `src/templates/website/teams.html`

- [x] delete `src/templates/website/teams.html`
- [x] grep to confirm it is not `{% include %}`d / `{% extends %}`ed / `render()`ed elsewhere: `grep -rn "website/teams.html\|teams.html" src/` — only the now-deleted `render()` call and the `race.css` comment about `race/teams.html` should have referenced it

### Task 5: Add the 404 regression test

**Files:**
- Modify: `src/website/tests.py`

- [ ] add `test_legacy_teams_endpoint_returns_404` (pytest-style, `@pytest.mark.django_db`, `client` fixture) asserting `client.get("/teams/").status_code == 404`
- [ ] run it: `uv run pytest src/website/tests.py -k legacy_teams_endpoint --reuse-db` — passes

### Task 6: Verify acceptance criteria

- [ ] grep for legacy references: `grep -rn "views.teams\b\|name=\"teams\"\|'teams'\|teams_protocol\|website/teams.html" src/` — only intentional survivors remain (`teams_api`, `teams2`/`my_teams`/`all_teams`, the int redirects, `race/teams.html`)
- [ ] `uv run python src/manage.py check` — no issues
- [ ] full suite: `uv run pytest --reuse-db` — all pass (incl. the unchanged int→slug redirect test at `tests.py:91-93`)
- [ ] `make format && make lint` — all checks pass

### Task 7: [Final] Close out

- [ ] confirm `CLAUDE.md` needs no change (it does not document the `/teams/` endpoint — nothing to update)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only*

**Deploy notes:**
- After deploy, `/teams/` returns **404** by design (no redirect). The current
  teams page is `race/<slug>/teams/` (`all_teams`). Confirm no external
  bookmark/integration relies on the old path.
