# Race visibility flags and team form field toggles

## Overview
- Add six per-race settings: show the teams list, hide the race on the site, hide the race in the mobile app, and ask or not ask for the team name, city and club.
- Organizers need races that are reachable only by direct link, races that do not publish the team list, and races where these team fields are not needed.
- All settings are switches on the race edit page. Defaults keep every existing race as it is today.

## Context (from discovery)
- Model and form: `src/website/models/race.py`, `src/apps/race/forms.py`, `src/templates/race/race_form.html`. Latest `website` migration is `0101_tag_race`.
- Site race lists and feeds: `src/website/views/community.py`, `src/website/models/news.py`.
- Mobile race list: `src/apps/mobile/views.py` (`RaceListView`), `src/apps/mobile/versioning.py` (`races_version`).
- Teams page: `src/apps/race/views.py` (`RaceTeamsView`, backs `all_teams`, `teams2`, `my_teams`), `src/templates/race/teams.html`, `src/static/js/teams.js`.
- Race page: `src/templates/race/race_page.html`, `src/templates/race/_race_header.html`.
- Team form: `src/website/forms.py` (`TeamForm`), `src/website/views/views_.py` (`AddTeam`), `src/website/views/team.py` (`EditTeamView`), `src/templates/website/add_team.html`, `src/templates/website/edit_team.html`.
- Patterns: `base-2.html` pages write inputs by hand; `add_team.html` and `edit_team.html` are duplicated on purpose (no partial); `is_race_admin` lives in `website/views/views_.py`.
- `«Название»`, `«Город»` and `«Клуб»` are already optional on the server (`required=False`, `blank=True`). This plan adds per-race hiding only.

## Development Approach
- **testing approach**: Regular (code first, then tests, within each task)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - add new test cases for new code paths
  - update existing test cases if behavior changes
  - tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change
- maintain backward compatibility
- after the Task 1 migration run `uv run pytest --create-db` once; `--reuse-db` will not pick up the new columns
- run `make format` and `make lint` before any commit
- commit only when asked

## Testing Strategy
- **unit tests**: required for every task. Pytest-style functions with `@pytest.mark.django_db`, in the test file named in each task.
- **e2e tests**: the project has no UI e2e suite.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview
- Six `BooleanField`s on `Race`, one migration. This matches the existing `is_teams_editable` and `is_photo_upload_enabled` flags and keeps list filters simple.
- `is_hidden` and `is_hidden_in_app` are independent. Neither blocks direct access: the race page, registration and per-race `/app/race/<id>/...` endpoints keep working, so phones can still sync during an event.
- `show_teams` is enforced on the server. Other teams never reach the page JSON. One helper decides access, and both the page and its links use it.
- A hidden team field is removed from `TeamForm`, so a forged POST value is ignored and the stored value survives an edit.

## Technical Details

### New `Race` fields

| Field | Default | Label |
|---|---|---|
| `show_teams` | `True` | Показать команды |
| `is_hidden` | `False` | Скрытая гонка |
| `is_hidden_in_app` | `False` | Скрыть в приложении |
| `ask_team_name` | `True` | Спрашивать название |
| `ask_team_city` | `True` | Спрашивать город |
| `ask_team_club` | `True` | Спрашивать клуб |

### `is_hidden` scope
- Removed from: `get_featured_race`, `upcoming_races` on home and in the standalone post sidebar, `RaceListView`, the home news feed, the article list.
- Kept in: the race page, registration, direct post links, and `«Личный кабинет»`. `unfinished_races()` does not change; only `upcoming_races` filters it.
- `NewsPost.visible()` does not change, because the race page and the post detail page use it. New `in_feed()` adds the hidden filter and must keep posts with `race=NULL`.

### `show_teams` access (when the flag is off)

| User | Access | Teams page | Tab | Race page sidebar rows |
|---|---|---|---|---|
| Superuser or race admin/moderator | `all` | All teams, as today | With count | Links |
| Signed in, owns a team in this race | `own` | Own teams only; page counts cover own teams | No count | Plain text |
| Anyone else | `none` | Redirect to race page | Hidden | Plain text |

- With the flag on, every user has `all` and today's behaviour is unchanged.
- `my_teams` for an anonymous user keeps its login redirect, which runs first.
- The race page `«Команды»` sidebar card and the `команд` and `участников` tiles stay visible for everyone, with counters and `«осталось X из Y»`.
- Unchanged by the flag: `/app/race/<id>/teams/`, `category_results`, organizer pages, legacy `/api/race/<id>/teams/` and `teams.csv`, `results-deprecated`.

### Team form and list display
- Off toggles remove `teamname`, `city` or `organization` from `TeamForm.fields`. With an unresolved `race_id` all fields stay.
- If all three toggles are off, the whole `«Команда»` card section is not rendered.
- ➕ On the edit page a superuser still gets the section, because it also holds the `«Стартовый номер»` input.
- Do not add a `«необязательно»` label to `«Название»` or `«Город»`. Labels stay as they are.
- `ask_team_city` off removes the `«Город»` column from the teams list.
- `ask_team_name` off changes only the teams list fallback for an unnamed team, from `«Без названия 123 (Иванов Иван)»` to `«Иванов Иван»`. A stored team name is still shown. The race page owned-teams panel, the home panel and the protocol keep their current names.

## What Goes Where
- **Implementation Steps**: code changes, tests and documentation in this repository.
- **Post-Completion**: manual checks and deployment.

## Implementation Steps

### Task 1: Add the six race flags and their switches

**Files:**
- Modify: `src/website/models/race.py`
- Create: `src/website/migrations/0102_race_visibility_flags.py`
- Modify: `src/apps/race/forms.py`
- Modify: `src/templates/race/race_form.html`
- Modify: `src/apps/race/tests.py`

- [x] add the six `BooleanField`s to `Race` with the labels and defaults above
- [x] generate the migration with `makemigrations`
- [x] add the fields to `RaceForm.Meta.fields`
- [x] add six switches to the `toggle-list` in `race_form.html`, next to `«Команды редактируемы»`
- [x] write test: the create page renders the default states (`show_teams` and `ask_team_*` checked, `is_hidden` and `is_hidden_in_app` not checked)
- [x] write test: saving the edit form stores checked and unchecked switches
- [x] run `uv run pytest --create-db` - must pass before next task

### Task 2: Hide `is_hidden` races from site lists and feeds

**Files:**
- Modify: `src/website/models/news.py`
- Modify: `src/website/views/community.py`
- Modify: `src/website/test_publications.py`

- [x] add `NewsPostQuerySet.in_feed()`: `visible()` plus `exclude(race__is_hidden=True)`
- [x] use `in_feed()` in `HomeView` and `ArticleListView`; leave the post detail view on `visible()`
- [x] filter `is_hidden=False` in `get_featured_race` and `RaceListView`
- [x] set `upcoming_races` to `unfinished.filter(is_hidden=False)[:3]` and keep `owned_team_groups` on the unfiltered `unfinished`
- [x] filter `is_hidden=False` before slicing `upcoming_races` in `PublicationDetailView` (standalone post sidebar)
- [x] write tests: a hidden race is absent from the home banner, the upcoming list and all catalog sections
- [x] write tests: its posts are absent from the home feed and the article list, and posts with no race stay in both
- [x] write tests: the race page with its news and a direct post link still work, and `«Личный кабинет»` still lists the owner's team
- [x] write test: a hidden race is absent from the sidebar of a standalone post
- [x] run tests - must pass before next task

### Task 3: Hide `is_hidden_in_app` races from `/app/races/`

**Files:**
- Modify: `src/apps/mobile/versioning.py`
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/tests.py`

- [x] add one shared queryset function in `versioning.py` for published races not hidden in the app
- [x] use it in `races_version()` and in the mobile `RaceListView`
- [x] write test: a race with the flag on is absent from `/app/races/`
- [x] write test: toggling the flag changes the ETag
- [x] write test: a per-race endpoint (for example `/app/race/<id>/legend/`) still answers for that race
- [x] write test: `is_hidden=True` alone leaves the race in `/app/races/`
- [x] run tests - must pass before next task

### Task 4: Enforce `show_teams` on the teams page

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/templates/race/teams.html`
- Modify: `src/apps/race/tests.py`

- [x] add a helper in `apps/race/views.py` that returns `all`, `own` or `none` for a user and race, per the access table
- [x] in `RaceTeamsView.get`, redirect `none` to the race page; this covers `all_teams`, `teams2` and `my_teams`
- [x] in `build_context`, for `own`, filter teams to `owner=user`
- [x] for `own`, show the number of own teams in the heading total; `teams.js` needs no change, because its chips and footer already count the rendered rows
- [x] write tests: each user type on `all_teams` and `teams2`, and `my_teams` for owner and anonymous user
- [x] write test: for `own`, the parsed `teams-data` JSON holds exactly the owner's teams
- [x] write test: with the flag on, behaviour is unchanged
- [x] run tests - must pass before next task

### Task 5: Update the race page and the teams tab

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/templates/race/_race_header.html`
- Modify: `src/templates/race/race_page.html`
- Modify: `src/apps/race/tests.py`

- [x] pass the access value into the race page and teams page context; check for any other page that includes `_race_header.html`
- [x] in `_race_header.html`, hide the tab for `none` and drop the count for `own`
- [x] in `race_page.html`, render `«Все N»` and the category rows as plain text unless access is `all`
- [x] write test: anonymous visitor sees the counters and `«осталось X из Y»` but no link to the teams URLs and no tab
- [x] write test: owner sees the tab without a count
- [x] write test: race admin sees the tab with a count and the links
- [x] run tests - must pass before next task

### Task 6: Hide team form fields per race

**Files:**
- Modify: `src/website/forms.py`
- Modify: `src/templates/website/add_team.html`
- Modify: `src/templates/website/edit_team.html`
- Modify: `src/website/tests.py`

- [x] in `TeamForm.__init__`, remove `teamname`, `city` or `organization` when the race's toggle is off; keep all fields for an unresolved `race_id`
- [x] wrap each input in `{% if %}` in both templates, and skip the `«Команда»` section when all three are off
- [x] write parametrized test over all three toggles: on add, a forged POST value for a hidden field is not saved
- [x] write parametrized test over all three toggles: on edit, a hidden field keeps its stored value
- [x] write test: the inputs are absent from the add and edit pages when off, and the section is absent when all three are off
- [x] write test: an invalid POST re-render does not bring hidden inputs back
- [x] write test: a form with `race_id=None` or a non-existent numeric `race_id` keeps all fields without an error
- [x] run tests - must pass before next task

### Task 7: Adjust the teams list display

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/templates/race/teams.html`
- Modify: `src/static/js/teams.js`
- Modify: `src/apps/race/tests.py`

- [x] add a `data-show-city` attribute on `.teams-page`, and skip the `«Город»` header and cell when it is off
- [x] in `build_context`, use `«Фамилия Имя»` as the unnamed-team name when `ask_team_name` is off; leave `_team_display_name` itself unchanged for other callers
- [x] write test: `«Город»` header is absent with `ask_team_city` off and present by default
- [x] write test: unnamed team name with the toggle off and on, and a stored name still shown with it off
- [x] run tests - must pass before next task

### Task 8: Verify acceptance criteria
- [x] verify all requirements from Overview are implemented
- [x] verify edge cases: all three field toggles off, `is_hidden` and `is_hidden_in_app` set independently, a race admin with no teams
- [x] run full test suite: `uv run pytest --create-db`
- [x] run `make format` and `make lint`

### Task 9: [Final] Update documentation
- [x] add a short note on the new flags to `CLAUDE.md`; keep it minimal, the file is already very large
- [x] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification**:
- On a race with `show_teams` off, open the teams page as anonymous user, owner, and race admin.
- Register a team on a race with all three field toggles off and confirm the redirect after payment lands on `my_teams`.
- Toggle `is_hidden_in_app` and confirm the mobile app's race list updates.

**Known limits**:
- `show_teams` does not gate legacy `/api/race/<id>/teams/`, `teams.csv`, `results-deprecated` or `/team/<id>/points/`, so it is not a guarantee against direct calls to those URLs.
- With `show_teams` off, the `«Переносы участников»` select on the owner's edit page still lists every team in the race. This is deliberate, so owners can still transfer places to another team.
