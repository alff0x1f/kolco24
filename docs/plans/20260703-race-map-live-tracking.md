# Race map: live team positions + on-demand tracks

## Overview

Organizer-only page «Карта гонки» (`race/<slug>/map/`): a Leaflet map showing the **last known
position of every team** in the race (auto-refreshed by polling every 20 s) and, on click, the
team's **full GPS track** (server-thinned, split into segments). This is the read side of the
existing `/app/race/<id>/track/` upload endpoint — `TrackPoint` rows are write-only today; this
page is the "organizer-facing map that reads tracks back" the mobile README defers to a later task.

Problem it solves: during a race organizers have no way to see where teams are or whether their
phones are still uploading. The page gives live monitoring plus per-team track review.

## Context (from discovery)

- Data: `apps.mobile.models.TrackPoint` (`src/apps/mobile/models.py:86`) — PK = client UUID,
  FKs `team`/`race`, `lat`/`lon`, `accuracy`, `gps_time_ms` (BigInt ms epoch), `segment_id`,
  `install_id`, `created_at`. Immutable, no `updated_at`, not in `versioning.py` — nothing here
  changes that.
- Scale: 50–100 teams × 1 point/15 s → ~500k rows per 24 h race. Full tracks can't be served raw;
  positions query needs `DISTINCT ON` + a composite index.
- Permission: `can_edit_race(user, race)` in `src/apps/race/permissions.py`; the auth pattern to
  mirror is `RaceLegendEditView._load_and_authorize` (`src/apps/race/views.py:904`) — anon →
  redirect to `login` with `?next=`, non-admin → 403.
- URLs: race pages are wired in `src/website/urls.py` (see `edit_legend`/`legend_codes` around
  line 66).
- Frontend pattern: `legend_form.html` (extends `website/base-2.html`, breadcrumb, page CSS via
  `extra_head`) + JSON config island read by a vanilla-JS file (`team-form.js`/`teams.js` pattern —
  no inline template vars in JS).
- No map library in the project yet; Leaflet gets vendored (no CDN), tiles from OSM.

## Decisions (agreed in brainstorm — do not re-litigate)

- Access: organizers only (`can_edit_race`), same UX as legend edit (anon → login redirect,
  non-admin → 403).
- Map: Leaflet + OSM tiles, vendored into `src/static/vendor/leaflet/`; OpenTopoMap as a switchable
  second base layer.
- Default view: last-position markers for all teams, polling every 20 s; a team's track loads only
  on click (multiple teams selectable). No КП on the map (Checkpoint has no coordinates — out of
  scope). No SSE/WebSocket/Channels.
- Track thinning on the server: keep a point only if ≥ 30 s (30 000 ms of `gps_time_ms`) passed
  since the previously kept one; always keep each segment's last point. Split polylines by
  recording session — the pair `(install_id, segment_id)`.
- Positions response includes **all** teams of the race; teams with no points get `lat`/`lon:
  null` (sidebar shows them as «не шлют трек»), likewise `install_id`/`segment_id`.
- No accuracy-based outlier filtering (deliberate — raw picture first, threshold later if needed).

## Development Approach

- **testing approach**: Regular (code first, then tests in the same task)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every code-bearing backend task MUST include new/updated tests**
  - tests are not optional - they are a required part of the checklist
  - tests cover both success and error scenarios
  - the two asset-only tasks are the explicit exceptions: Task 2 (vendored static files) has no
    testable code; Task 6 (JS/CSS — no JS test infra in the project) carries a mandatory manual
    smoke checklist instead, executed in Task 7
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change
- work on a feature branch (never commit to master); `make format && make lint` before every commit
- `uv run pytest --reuse-db` for iteration; DB via `docker compose up -d kolco24_db`

## Testing Strategy

- **unit tests**: pytest-style functions in `src/apps/race/tests.py` (`@pytest.mark.django_db`,
  `client`/`django_user_model` fixtures) — the project convention; no Django `TestCase`.
- `distinct("team_id")` is Postgres-only — fine, the test suite runs on Postgres.
- no e2e framework in the project — JS behavior is verified manually (Post-Completion).

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep plan in sync with actual work done

## Solution Overview

Three views in `src/apps/race/views.py`, all gated by `can_edit_race` via the same
`_load_and_authorize` shape as `RaceLegendEditView`; three URL names in `src/website/urls.py`:

- `race_map` — `race/<slug:race_slug>/map/` — HTML page (`src/templates/race/map.html`);
- `race_map_positions` — `race/<slug:race_slug>/map/positions/` — JSON, last point per team;
- `race_map_track` — `race/<slug:race_slug>/map/track/<int:team_id>/` — JSON, one team's track.

Frontend: full-viewport Leaflet map + narrow sidebar (team list, search, freshness), vanilla JS
polling `positions` every 20 s, per-team track fetch on click.

## Technical Details

**Positions query** (one `DISTINCT ON` + one teams query):

```python
last_points = (
    TrackPoint.objects.filter(race_id=race.id)
    .order_by("team_id", "-gps_time_ms", "-created_at", "-id")
    .distinct("team_id")
)
teams = Team.objects.filter(category2__race_id=race.id)  # default manager excludes deleted
```

Response — a JSON list over **all** teams (team name + start number; note `RaceTeamsView` uses the
key `num` — this endpoint picks its own keys, Task 6's JS must mirror them exactly), merged with
the last point where present:

```json
[{"team_id": 1, "name": "…", "number": "12", "lat": 55.1, "lon": 61.2,
  "gps_time_ms": 1750000000000, "received_at": "2026-07-03T10:00:00+00:00",
  "install_id": "…", "segment_id": "…"}, …]
```

The extra `-created_at`/`-id` in `order_by` are deterministic tie-breakers: two phones of one team
can upload different points with the same `gps_time_ms`, and bare `DISTINCT ON` would pick either
row per request (marker flicker). `install_id`/`segment_id` are included so the JS live-append can
detect that a new recording session started (see Frontend below).

`received_at` = the last point's `created_at` (server receive time) — the UI uses it to grey out
stale markers (phone stopped uploading); emit it as `.isoformat()` (plain `JsonResponse` won't
serialize a raw `datetime`). Teams without points: `lat`/`lon`/`gps_time_ms`/`received_at` all
`null`.

**Track query + thinning** (pure Python over `values_list`, no extra deps):

```python
points = (
    TrackPoint.objects.filter(race_id=race.id, team_id=team.id)
    .order_by("gps_time_ms")
    .values_list("install_id", "segment_id", "lat", "lon", "gps_time_ms")
)
```

Group into segments by the pair `(install_id, segment_id)` — the model doc
(`src/apps/mobile/models.py:99`) defines the pair, not `segment_id` alone, as the session key
(two phones of one team must not merge into one line). Segments ordered by their first point's
`gps_time_ms`;
within a segment keep a point iff `gps_time_ms - last_kept >= 30_000`; always append the segment's
final point (dedupe if it was already kept). Response: `{"segments": [[[lat, lon], …], …]}` —
~1 500 points / ~80 KB for a 24 h track instead of 5 760 / ~300 KB. Team not in this race → 404
(resolve the team through `category2__race_id=race.id`).

**Index**: `TrackPoint.Meta.indexes = [models.Index(fields=["race", "team", "-gps_time_ms"], name="mobile_tp_race_team_ts")]`
(short name on purpose — Django caps index names at 30 chars) —
serves both the `DISTINCT ON (team_id) … ORDER BY gps_time_ms DESC` positions query and the
per-team track scan. One new migration in `apps.mobile`. Model stays otherwise untouched (still
immutable, still out of `versioning.py`).

**Frontend**:

- `map.html` extends `website/base-2.html` (breadcrumb «Кольцо 24 / {{ race.name }} / Карта»),
  loads `css/race_map.css` in `extra_head`, vendored Leaflet CSS/JS, `js/race_map.js`, and a
  `<script type="application/json" id="raceMapConfig">` island with `positionsUrl` / `trackUrlTemplate`.
- `race_map.js` (vanilla, no jQuery): init Leaflet with OSM base layer + OpenTopoMap in
  `L.control.layers`; fetch positions → circle markers (`L.divIcon` with the team number),
  one-time `fitBounds` over non-null positions (none → default center + «пока нет данных» hint);
  `setInterval(20_000)` with an in-flight guard (skip tick if the previous fetch hasn't resolved);
  marker with `received_at` older than 10 min → greyed CSS class; sidebar: counter «трек шлют
  N из M», substring search, group «не шлют трек» for `lat: null` teams; click on row or marker →
  toggle track (fetch once, draw segment polylines in a color from a ~10-color cycling palette,
  highlight marker; second click removes); on each poll tick, for each selected team compare the
  position's `(install_id, segment_id)` with the one remembered at track fetch — same session →
  append the point to that session's polyline; changed (phone reconnect / new recording) → start a
  fresh polyline from the new point instead of drawing a bogus straight line across the map (no
  track re-fetch either way).
- `race_map.css` scoped under `.race-map` (never define a bare `.page` — `theme-2.css` owns it).
- Vendored Leaflet 1.9.4 (`leaflet.js`, `leaflet.css`, `images/`) in `src/static/vendor/leaflet/`
  — vendored assets are off-limits for edits, served by WhiteNoise like everything else.

## What Goes Where

- **Implementation Steps**: migrations, views, URLs, template, CSS/JS, tests, docs.
- **Post-Completion**: manual browser check with real app data, prod deploy.

## Implementation Steps

### Task 1: Composite index on TrackPoint

**Files:**
- Modify: `src/apps/mobile/models.py`
- Create: `src/apps/mobile/migrations/00XX_trackpoint_race_team_time_idx.py` (via `makemigrations`)
- Modify: `src/apps/mobile/tests.py`

- [ ] add `Meta.indexes = [models.Index(fields=["race", "team", "-gps_time_ms"], name="mobile_tp_race_team_ts")]` to `TrackPoint` (keep the docstring's "not in versioning" invariants intact; Django caps index names at 30 chars)
- [ ] `uv run python src/manage.py makemigrations mobile` — verify the migration only adds the index
- [ ] write test: index name present in `TrackPoint._meta.indexes` (guards accidental removal)
- [ ] run `uv run pytest src/apps/mobile/tests.py` — must pass before task 2

### Task 2: Vendor Leaflet

**Files:**
- Create: `src/static/vendor/leaflet/leaflet.js`
- Create: `src/static/vendor/leaflet/leaflet.css`
- Create: `src/static/vendor/leaflet/images/*` (marker/layers icons from the dist)

- [ ] download Leaflet 1.9.4 dist (js + css + images) into `src/static/vendor/leaflet/`
- [ ] verify `leaflet.css` icon paths resolve relative to the vendored `images/` dir (they are relative in the dist — no edits to vendored files)
- [ ] no tests (static assets only); `git add` and confirm `collectstatic` would pick them up via `STATICFILES_DIRS` (no config change needed)

### Task 3: Positions endpoint (`race_map_positions`)

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [ ] add `RaceMapPositionsView(View)` with the `_load_and_authorize` pattern (anon → login redirect with `?next=`, non-admin → 403, race by slug → 404)
- [ ] implement the `DISTINCT ON (team_id)` last-point query (with the `-created_at`, `-id` tie-breakers) + all-teams merge; `JsonResponse` list per **Technical Details** incl. `install_id`/`segment_id` (null point fields for teams without points; `safe=False` or wrap in `{"teams": […]}` — pick one and mirror in JS)
- [ ] wire `race/<slug:race_slug>/map/positions/` → name `race_map_positions` in `src/website/urls.py`
- [ ] write tests: anon redirect, plain user 403, superuser and `RaceAdmin(role=ADMIN)` 200
- [ ] write tests: team with 3 points returns the max-`gps_time_ms` one; team without points has `lat is None`; a point from another race's team never appears; two points with equal `gps_time_ms` → the tie-breaker picks the same row on repeated requests (deterministic)
- [ ] run `uv run pytest src/apps/race/tests.py` — must pass before task 4

### Task 4: Track endpoint (`race_map_track`)

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Modify: `src/apps/race/tests.py`

- [ ] add `RaceMapTrackView(View)`: same authorize, resolve team via `Team.objects.filter(category2__race_id=race.id, pk=team_id)` → 404 if absent
- [ ] implement segment grouping by `(install_id, segment_id)` (segments ordered by first point's `gps_time_ms`) + 30 s thinning with always-keep-last-of-segment; return `{"segments": [[[lat, lon], …], …]}`
- [ ] wire `race/<slug:race_slug>/map/track/<int:team_id>/` → name `race_map_track`
- [ ] write tests: points 10 s apart collapse (kept count matches expectation), segment's last point always present, two `segment_id`s → two segments ordered by time, same `segment_id` from two `install_id`s → two segments (session key is the pair), points ordered by `gps_time_ms` within a segment
- [ ] write tests: team from another race → 404; anon/non-admin gating (redirect/403)
- [ ] run `uv run pytest src/apps/race/tests.py` — must pass before task 5

### Task 5: Map page view + template

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Create: `src/templates/race/map.html`
- Modify: `src/apps/race/tests.py`

- [ ] add `RaceMapView(View)` rendering `race/map.html` with `race` + the JSON config island data (`positionsUrl` via `reverse`; `trackUrlTemplate` via `reverse(..., kwargs={"team_id": 0}).replace("/0/", "/{team_id}/")` — `reverse` can't leave a placeholder)
- [ ] wire `race/<slug:race_slug>/map/` → name `race_map`
- [ ] create `map.html`: extends `website/base-2.html`, breadcrumb like `legend_form.html`, full-viewport map container + sidebar skeleton, `#raceMapConfig` JSON island; CSS (Leaflet + `race_map.css`) in `{% block extra_head %}`, JS (Leaflet + `race_map.js`) in `{% block footer_js_include %}` (same blocks as `legend_form.html`)
- [ ] write tests: page access trio (anon redirect / user 403 / admin 200), response contains the config island with both URLs
- [ ] run `uv run pytest src/apps/race/tests.py` — must pass before task 6

### Task 6: Frontend JS + CSS

**Files:**
- Create: `src/static/js/race_map.js`
- Create: `src/static/css/race_map.css`

- [ ] `race_map.css`: `.race-map` scoped layout — map fills viewport minus header, sidebar column, marker styles (numbered circle, `.is-stale` grey, `.is-selected` highlight), sidebar rows/search/counter
- [ ] `race_map.js`: config island parse, Leaflet init (OSM + OpenTopoMap in layer control, with the required attribution strings © OpenStreetMap contributors / © OpenTopoMap (CC-BY-SA) in the tile layer options), positions fetch + marker render, one-time `fitBounds`, empty-race hint
- [ ] polling: `setInterval` 20 s with in-flight guard; update marker positions in place; stale (>10 min by `received_at`) → grey
- [ ] sidebar: counter «трек шлют N из M», substring search, «не шлют трек» group; row ↔ marker click both toggle track selection
- [ ] track toggle: fetch `race_map_track`, draw segment polylines (cycling ~10-color palette), remove on second click; remember the team's current `(install_id, segment_id)` from positions at fetch time
- [ ] live append: on poll tick, same session key → append point to that polyline; changed key → start a new polyline from the new point (never connect across sessions)
- [ ] no unit tests (no JS test infra in project — the explicit exception noted in Development Approach); the manual smoke checklist in Task 7 is the mandatory verification; keep JS free of inline template vars (config island only)

### Task 7: Verify acceptance criteria

- [ ] verify all Overview requirements: markers + polling, track on click, multi-select, stale indication, teams-without-track group, organizer-only gating
- [ ] manual smoke: `docker compose up -d kolco24_db`, seed a race + teams + `TrackPoint` rows (shell), open `race/<slug>/map/` as superuser, verify markers/track/polling in browser
- [ ] run full test suite: `uv run pytest`
- [ ] run `make format && make lint`

### Task 8: [Final] Update documentation

- [ ] update `CLAUDE.md` (`apps.race` section): document `RaceMapView`/`RaceMapPositionsView`/`RaceMapTrackView`, URL names, the thinning rule, the vendored Leaflet, and the new `TrackPoint` index (note it's still out of `versioning.py`)
- [ ] update `src/apps/mobile/README.md` if it references the organizer map as "future task" — point to the new page
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**Manual verification:**
- watch a real race (or the app's emulator feed) for one polling hour: marker movement, stale
  greying, track append correctness after phone reconnects (session key change must start a new
  polyline — no straight line across the map)
- eyeball positions/track endpoint latency on prod data volume (~500k rows) — the composite index
  should keep both under ~100 ms; if not, revisit with `EXPLAIN`

**External:**
- OSM/OpenTopoMap tile usage policy: organizer-only page, 1–3 concurrent users — well within free
  usage; no API keys to configure
- prod deploy: plain migrate + collectstatic, no env changes
