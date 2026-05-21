# Race Slug URLs

## Overview

Add a `slug` field to the `Race` model so races are accessible via human-readable URLs
(`/race/kolco24/`) instead of numeric IDs (`/race/8/`). Old int-id URLs redirect 301 to
the slug URL for backward compatibility. Scope covers **all** `race/<id>/...` URL patterns.

## Context (from discovery)

- Files involved: `src/website/models/race.py`, `src/website/urls.py`,
  `src/website/views/views_.py`, `src/website/views/team.py`, 6 templates
- `Race` already has a `code` field (`CharField`, unique, max_length=15) — slug will be
  pre-populated from it via data migration
- URL patterns are a mix of `<race_id>` (string capture) and `<int:race_id>`; views use
  `race_id` as both URL parameter and Django ORM FK filter argument
- Latest migration: `0058_remove_race_time_limit_min.py`

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- Every task that changes code **must** include new or updated tests
- All tests must pass before starting the next task
- Run: `uv run pytest --reuse-db`

## Testing Strategy

- **Unit tests**: `src/website/tests.py` using `@pytest.mark.django_db`
- Test slug field exists, is unique, migration populates it from code
- Test `RaceIdRedirectView` returns 301 with correct `Location` header
- Test slug URL resolves correctly (200)
- Test old int URL redirects to slug URL (301)
- No e2e test infrastructure present in this project

## Progress Tracking

- Mark completed items with `[x]` immediately when done
- Add newly discovered tasks with ➕ prefix
- Document issues/blockers with ⚠️ prefix

## Solution Overview

Two sets of URL patterns:
- **Primary (named)**: `race/<slug:race_slug>/...` — views accept `race_slug`, look up
  `Race` via `get_object_or_404(Race, slug=race_slug)`
- **Redirect (anonymous)**: `race/<int:race_id>/...` → `RaceIdRedirectView` (301) that
  replaces the numeric segment with the race's slug in the request path

Internal ORM FK filters (`category2__race_id=race.id`) are unchanged — only the URL
parameter name and DB lookup key change.

## Technical Details

- New field: `slug = SlugField("URL-slug", max_length=50, unique=True, blank=True)`
- Data migration: `Race.objects.filter(slug="").update(slug=F("code"))` — safe because
  `code` is already unique and slug-compatible
- `RaceIdRedirectView` replaces first occurrence of `/race/<id>/` with `/race/<slug>/`
  in `request.path`, then issues `HttpResponsePermanentRedirect`
- Index URL hardcodes slug: after Task 1 determine race id=8's slug by running
  `uv run python src/manage.py shell -c "from website.models import Race; print(Race.objects.get(pk=8).code)"`

## What Goes Where

**Implementation Steps** (`[ ]` checkboxes): all code changes below.

**Post-Completion** (no checkboxes, manual):
- Confirm old numeric URLs (e.g., `/race/8/`) redirect correctly in production
- Update any external links, bookmarks, sitemap.xml if present

---

## Implementation Steps

### Task 1: Add slug field to Race model + migrations

**Files:**
- Modify: `src/website/models/race.py`
- Create: `src/website/migrations/0059_race_slug.py` (via makemigrations)
- Create: `src/website/migrations/0060_race_slug_populate.py` (data migration)

- [x] add `slug = SlugField("URL-slug", max_length=50, unique=True, blank=True)` to
  `Race` model in `src/website/models/race.py`
- [x] run `uv run python src/manage.py makemigrations website --name race_slug` to
  generate `0059_race_slug.py`
- [x] create `0060_race_slug_populate.py` data migration:
  ```python
  from django.db import migrations
  from django.db.models import F

  def populate_slug(apps, schema_editor):
      Race = apps.get_model("website", "Race")
      Race.objects.filter(slug="").update(slug=F("code"))

  class Migration(migrations.Migration):
      dependencies = [("website", "0059_race_slug")]
      operations = [migrations.RunPython(populate_slug, migrations.RunPython.noop)]
  ```
- [x] run `uv run python src/manage.py migrate` and confirm all races have non-empty slug
- [x] verify race id=8 slug value:
  `uv run python src/manage.py shell -c "from website.models import Race; print(Race.objects.get(pk=8).slug)"`
  → note this value for use in Task 2 (index URL): **kolco24_2025**
- [x] add `src/website/admin.py`: ensure `slug` is visible/editable in `RaceAdmin`
  (add to `fields` or `list_display` if a `ModelAdmin` exists)
- [x] write test `test_race_slug_populated`: create Race with code="test-race", run
  migration equivalent, assert `race.slug == "test-race"`
- [x] write test `test_race_slug_unique`: two races with same slug raises IntegrityError
- [x] run `uv run pytest --reuse-db` — must pass before Task 2

---

### Task 2: Add RaceIdRedirectView + slug URL patterns + update views

This task changes URL patterns and view signatures atomically (they are tightly coupled).

**Files:**
- Modify: `src/website/views/views_.py`
- Modify: `src/website/urls.py`

**2a — Add `RaceIdRedirectView` to `views_.py`:**

- [x] add after existing imports in `views_.py`:
  ```python
  from django.http import HttpResponsePermanentRedirect

  class RaceIdRedirectView(View):
      def get(self, request, race_id, **kwargs):
          race = get_object_or_404(Race, pk=race_id)
          new_path = request.path.replace(f"/race/{race_id}/", f"/race/{race.slug}/", 1)
          qs = f"?{request.GET.urlencode()}" if request.GET else ""
          return HttpResponsePermanentRedirect(new_path + qs)
  ```

**2b — Update view signatures and lookups (all views in `views_.py` that take `race_id` from URL):**

For each view below, rename parameter `race_id` → `race_slug` and change DB lookup
to `get_object_or_404(Race, slug=race_slug)`. Where the int PK is needed for ORM filters,
add `race_id = race.id` immediately after the lookup.

- [x] `TeamMemberRaceLogView.get(request, race_id)` → `race_slug`
- [x] `RaceNewsView.get(request, race_id)` → `race_slug`
- [x] `BreakfastView.get(request, race_id)` and `.post(request, race_id)` → `race_slug`
- [x] `BreakfastAdminView.get` and `.post` → `race_slug`; update
  `reverse("breakfast_admin", args=[race.id])` → `reverse("breakfast_admin", args=[race.slug])`
- [x] `BreakfastPaidListView.get` → `race_slug`
- [x] `AllTeamsView.get(request, race_id)` → `race_slug`; add `race_id = race.id` for ORM filters
- [x] `MyTeamsView.get(request, race_id)` → `race_slug`; add `race_id = race.id`
- [x] `AddTeam.get` and `.post` → `race_slug`; update
  `reverse("add_team", args=[race_id])` → `reverse("add_team", args=[race.slug])`
  and `reverse("my_teams", args=[race_id])` → `reverse("my_teams", args=[race.slug])`
  and context `"race_id": race_id` → `"race_id": race.id`
  and `TeamForm(race_id, ...)` → `TeamForm(race.id, ...)`
- [x] `AllTeamsResultView.get(request, race_id, category_id)` → `race_slug`; add `race_id = race.id`
- [x] `TeamsView.get(request, race_id, category_id)` → `race_slug`; add `race_id = race.id`
- [x] `TeamsViewCsv.get(request, race_id, category_id)` → `race_slug`; add `race_id = race.id`
- [x] leave `upload_photo(request, race_id)` and `PointTagsView` unchanged (internal API)

**2c — Update `urls.py`:**

- [x] add `RaceIdRedirectView` to imports from `.views`
- [x] replace the block of `race/<race_id>/...` patterns with slug-based primary + int-id redirects
  (int-id redirects placed before slug patterns since slug matches integers too)
- [x] update index redirect (line 18) using the slug value determined in Task 1 step 5:
  `lambda request: redirect("race", race_slug="kolco24_2025")`
- [x] update `race/8/transfer/` hardcoded pattern — leave it as-is (no slug needed)
- [x] convert `race_member_logs` to slug as well for consistency

**2d — Tests:**

- [x] write `test_race_id_redirect_main`: GET `/race/8/` → 301 to `/race/<slug>/`
- [x] write `test_race_id_redirect_teams`: GET `/race/8/teams/` → 301 to `/race/<slug>/teams/`
- [x] write `test_race_slug_news_view`: GET `/race/<slug>/` → 200
- [x] run `uv run pytest --reuse-db` — must pass before Task 3

---

### Task 3: Update team.py reverse() calls

**Files:**
- Modify: `src/website/views/team.py`

The `EditTeamView` and related code in `team.py` call `reverse()` with integer race IDs.
These need to pass slugs instead. `team.category2.race_id` is an integer FK; we need
`team.category2.race.slug` which requires a JOIN.

- [x] in `EditTeamView` (or wherever `get_team` is called), ensure `select_related("category2__race")`
  is in the queryset (check `get_team` method in `team.py`)
- [x] `reverse("my_teams", args=[race.id])` (line 121) → `reverse("my_teams", args=[race.slug])`
- [x] `reverse("teams2", args=[team.category2.race_id, team.category2_id])` (line 161) →
  `reverse("teams2", args=[team.category2.race.slug, team.category2_id])`
- [x] `reverse("my_teams", args=[team.category2.race_id])` (line 195) →
  `reverse("my_teams", args=[team.category2.race.slug])`
- [x] context `"race_id": team.category2.race_id` (line 62) — used internally; update to
  `"race_id": team.category2.race_id` (unchanged — this is the int PK for TeamForm, not URL)
- [x] write test `test_edit_team_redirect_uses_slug`: after team edit, redirect location
  contains slug not int
- [x] run `uv run pytest --reuse-db` — must pass before Task 4

---

### Task 4: Update templates

**Files:**
- Modify: `src/templates/teams.html`
- Modify: `src/templates/website/breakfast.html`
- Modify: `src/templates/website/breakfast_admin.html`
- Modify: `src/templates/website/breakfast_paid_list.html`
- Modify: `src/templates/website/team_member_race_logs.html`
- Modify: `src/templates/website/news.html`

- [x] `src/templates/teams.html` line 182:
  `{% url 'add_team' race.id %}` → `{% url 'add_team' race.slug %}`
- [x] `src/templates/website/breakfast.html` line 55:
  `{% url 'breakfast' race.id %}` → `{% url 'breakfast' race.slug %}`
- [x] `src/templates/website/breakfast_admin.html` line 12:
  `{% url 'breakfast' race.id %}` → `{% url 'breakfast' race.slug %}`
- [x] `src/templates/website/breakfast_paid_list.html` line 12:
  `{% url 'breakfast_admin' race.id %}` → `{% url 'breakfast_admin' race.slug %}`
- [x] `src/templates/website/team_member_race_logs.html` line 12:
  `{% url 'race' race.id %}` → `{% url 'race' race.slug %}`
- [x] `src/templates/website/news.html` lines 32, 190:
  `{% url 'add_team' race.id %}` → `{% url 'add_team' race.slug %}`
- [x] grep all templates for any remaining `race.id` in `{% url %}` tags:
  `grep -rn "url.*race\.id" src/templates/`
- [x] run `uv run pytest --reuse-db` — must pass before Task 5

---

### Task 5: Verify acceptance criteria

- [ ] verify all `race/<race_id>/...` URL patterns now have slug equivalents
- [ ] verify old int-id URLs return 301, not 404 or 200
- [ ] verify slug URLs return 200
- [ ] verify templates render without `NoReverseMatch` errors
- [ ] run full test suite: `uv run pytest`
- [ ] run linting: `uv run ruff check src && uv run black --check src && uv run isort --check src && uv run flake8 src`

---

### Task 6: [Final] Update documentation

- [ ] move this plan to `docs/plans/completed/`
  (`mkdir -p docs/plans/completed && mv docs/plans/20260521-race-slug-urls.md docs/plans/completed/`)

## Post-Completion

**Manual verification:**
- Open `/race/8/` in browser → should 301-redirect to `/race/<slug>/`
- Open `/race/<slug>/` → should render race page normally
- Open `/race/8/teams/` → should 301-redirect to `/race/<slug>/teams/`
- Check Django Admin → Race form should show editable slug field

**External:**
- Update any hardcoded links in external docs or marketing materials
- If sitemap.xml exists, regenerate it with slug URLs
