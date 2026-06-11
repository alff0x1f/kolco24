# Mobile teams endpoint: embed race categories

## Overview

The mobile app consumes `GET /app/race/<race_id>/teams/`, where each team carries
`category2` as a bare FK id — but no `/app/*` endpoint exposes the categories
themselves, so the app cannot resolve the id into a name or build a category
filter. This plan embeds the race's category list into the existing teams
response (no new endpoint): `{"race": id, "categories": [...], "teams": [...]}`.

Use case (agreed in brainstorm): display + filter only. Category fields exposed:
`id`, `code`, `short_name`, `name`, `order` — no `is_active`/`description`/size
fields (YAGNI). Backward compatible: only a new `categories` key is added,
`race`/`teams` are unchanged.

## Context (from discovery)

- `src/apps/mobile/views.py:TeamsView` — teams endpoint with conditional GET
  (strong ETag = quoted `teams_version`, `If-None-Match` → empty 304 before any
  serialization).
- `src/apps/mobile/versioning.py:teams_version` — single source of truth for
  both the teams ETag and the sync manifest's `versions.teams`; currently
  `blake2b` over `MAX(Team.updated_at)|MAX(Athlet.updated_at)|COUNT(Athlet)|COUNT(Team)`.
- `src/website/models/race.py:Category` — has `code`/`short_name`/`name`/
  `order`/`is_active` but **no `updated_at`** (unlike `Team`/`Athlet`/`Checkpoint`).
- Established pattern for version-bearing timestamps: `Athlet.updated_at`
  (migration `0076`), `Checkpoint.updated_at` (`0077`) — both `auto_now=True`.
- Verified: `_reconcile_categories` in `src/apps/race/views.py` saves categories
  with a full `instance.save()` (no `update_fields`), so `auto_now` will fire;
  no existing `Category.save(update_fields=...)` call sites anywhere.

## Development Approach

- **testing approach**: Regular (code first, then tests) — matches project
  convention (pytest-style functions, `@pytest.mark.django_db`).
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in
  that task; tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** — run
  `uv run pytest src/apps/mobile/tests.py --reuse-db` per task,
  `uv run pytest` at the end
- **CRITICAL: update this plan file when scope changes during implementation**
- run `make format && make lint` before committing
- maintain backward compatibility (additive response change only)

## Testing Strategy

- **unit tests**: `src/apps/mobile/tests.py`, pytest-style with the signed-request
  helpers already used there (HMAC headers). Every task lists its tests as
  separate checklist items.
- **e2e tests**: none in this project — not applicable.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep plan in sync with actual work done

## Solution Overview

Option A from brainstorm (vs. a separate `/categories/` endpoint or nesting the
category object per team): categories are useless without teams and needed in
the same screen, so they ride inside the teams resource — one fetch, one ETag,
one `versions.teams` key. This mirrors the web `RaceTeamsView`, which embeds
teams + categories as JSON islands.

Key decisions:

- **All of the race's categories are included, including `is_active=False`** —
  a team may reference a deactivated category and its id must still resolve.
  `is_active` itself is not exposed.
- Ordering: `order_by("order", "id")` — the `order` field exists for display
  (race-edit writes `order = index`); the model's `Meta.ordering = ["code"]` is
  wrong for this purpose.
- **No `versions.categories` key in the sync manifest**: since categories ship
  inside the teams response, a category change must move the teams ETag /
  `versions.teams` — achieved by folding category state into `teams_version`.
- `Category.updated_at` (`auto_now`) is required so a rename/reorder moves
  `MAX(Category.updated_at)` — `COUNT` alone only catches add/delete.

## Technical Details

- Migration `website/0078_category_updated_at`: `updated_at = DateTimeField(auto_now=True)`
  (same shape as `0076`/`0077`).
- `teams_version` raw string becomes:
  `MAX(Team.updated_at)|MAX(Athlet.updated_at)|COUNT(Athlet)|COUNT(Team)|MAX(Category.updated_at)|COUNT(Category)`
  with the category aggregate over `Category.objects.filter(race_id=race_id)` —
  **no `is_active` filter**, the exact queryset the view serves (single-source
  contract). `None` aggregates keep rendering as `"None"`.
- `TeamsView.get` 304 path unchanged: matching `If-None-Match` still returns an
  empty `HttpResponseNotModified` with the ETag and **no serialization** of
  either queryset.
- `update_fields` discipline now extends to `Category`: any future
  `Category.save(update_fields=[...])` must include `"updated_at"`.
- Deploy effect: the fingerprint formula change invalidates every stored client
  ETag once → a single harmless re-fetch.

## What Goes Where

- **Implementation Steps** (`[ ]` checkboxes): migration, versioning, serializer,
  view, tests, docs — all in this repo.
- **Post-Completion** (no checkboxes): mobile-app client work (consume
  `categories`), production migration on deploy.

## Implementation Steps

### Task 1: Add `Category.updated_at` and fold categories into `teams_version`

**Files:**
- Modify: `src/website/models/race.py`
- Create: `src/website/migrations/0078_category_updated_at.py`
- Modify: `src/apps/mobile/versioning.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] add `updated_at = DateTimeField(auto_now=True)` to `Category`
      (`src/website/models/race.py`), mirroring `Athlet`/`Checkpoint`
- [ ] generate migration `0078_category_updated_at`
      (`uv run python src/manage.py makemigrations website`)
- [ ] in `teams_version` add the third aggregate
      `Category.objects.filter(race_id=race_id).aggregate(max_updated=Max("updated_at"), count=Count("id"))`
      and append `|{max_updated}|{count}` to `raw`; import `Category`
- [ ] update the `teams_version` docstring (formula, what moves it: category
      rename/reorder via `updated_at`, add/delete via `COUNT`) and the module
      docstring if needed
- [ ] write test: fingerprint moves on category rename, on category add, on
      category delete
- [ ] write test: race with zero categories → stable fingerprint, no crash
      (`None` aggregate path)
- [ ] run `uv run pytest src/apps/mobile/tests.py --reuse-db` — must pass before task 2

### Task 2: `CategorySerializer` + embed `categories` in `TeamsView`

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] add `CategorySerializer` (ModelSerializer over `website.models.race.Category`,
      fields `["id", "code", "short_name", "name", "order"]`) to
      `src/apps/mobile/serializers.py`
- [ ] in `TeamsView.get`, build
      `Category.objects.filter(race=race).order_by("order", "id")` (no
      `is_active` filter) and return
      `{"race": race_id, "categories": [...], "teams": [...]}`; leave the
      `If-None-Match` short-circuit untouched (no queryset evaluation on 304)
- [ ] update the `TeamsView` docstring
- [ ] write test: `categories` present with exactly the 5 expected fields,
      sorted by `order, id`
- [ ] write test: a deactivated (`is_active=False`) category referenced by a
      team appears in `categories`
- [ ] write test: ETag/304 interplay — after a category rename a stale
      `If-None-Match` gets `200` with the new list, the fresh ETag gets `304`
- [ ] write test: sync manifest `versions.teams` equals the bare teams ETag and
      moves after a category edit
- [ ] run `uv run pytest src/apps/mobile/tests.py --reuse-db` — must pass before task 3

### Task 3: Verify acceptance criteria

- [ ] verify the response shape is additive (existing `race`/`teams` unchanged)
- [ ] verify the 304 path serializes nothing (no `categories` query on 304)
- [ ] run full test suite: `uv run pytest`
- [ ] run `make format && make lint`

### Task 4: [Final] Update documentation

**Files:**
- Modify: `src/apps/mobile/API.md`
- Modify: `src/apps/mobile/README.md` (if it describes the teams response)
- Modify: `CLAUDE.md`

- [ ] `API.md`: document the `categories` block in the teams endpoint (fields,
      `order, id` ordering, "includes inactive categories" note)
- [ ] `README.md`: update the teams response example if present
- [ ] `CLAUDE.md`: update the `apps.mobile` teams-endpoint shape and the
      `teams_version` formula; add `Category` to the `update_fields` discipline
      list; mention migration `0078`
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**External system updates:**
- Mobile app client: start consuming `categories` to resolve `category2` ids
  and render the filter (no server coordination needed — additive change).
- Deploy: run migration `0078` in production; all client-stored teams ETags
  invalidate once (formula change) — expected one-time re-fetch, no action.
