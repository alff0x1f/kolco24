# Race map_url for mobile apps

## Overview
- iOS (`RaceDto.mapUrl`) and Android (`docs/design/API.md`) already read an optional `map_url` from
  `GET /app/races/`: the URL of the race's offline raster `.mbtiles` basemap, downloaded without HMAC.
  `null` means "no map" (the app falls back to the online OSM basemap).
- The server does not send the field yet. This plan adds it: a manually entered value on `Race`,
  edited on the race edit page, served by `RaceListSerializer`.
- The file itself is placed by hand (`scp` to `/app/media/maps/`, served by nginx `/media/`) or lives on
  any external HTTPS host. No upload, no MBTiles generation.

## Context (from discovery)
- `src/website/models/race.py` — `Race`; `Race.clean()` (line ~78) already validates `header_image`/`header_logo`
  (URL or root-relative path). Last migration: `website/0098_alter_checkpointtag_check_method`.
- `src/apps/race/forms.py:RaceForm` — `Meta.fields` list of scalar `Race` fields; `_post_clean` runs `Race.clean()`.
- `src/templates/race/race_form.html` — manual base-2 inputs; `header_image` block at ~143–161.
- `src/apps/mobile/serializers.py:285` — `RaceListSerializer` (ModelSerializer, fixed field list).
- `src/apps/mobile/versioning.py:100` — `races_version()` = `blake2b(MAX(updated_at)|COUNT)`; legend already uses a
  `_TEAMS_SCHEMA_VERSION` (`versioning.py:43`, folded in at `:92`, with a history comment) — the pattern to copy
  for a shape-change cache bust.
- Tests: `src/apps/mobile/tests.py` (races field set ~1161, `races_version` tests ~1366),
  `src/website/tests.py` (`header_image` clean tests ~548), `src/apps/race/tests.py` (race edit POST fixture ~1422).
- Nginx: `/media/` → `/app/media/`, `client_max_body_size 50m` (irrelevant — no upload).

## Development Approach
- **testing approach**: Regular (code first, then tests), pytest functions with `@pytest.mark.django_db`
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run `make format` + `make lint` before committing
- maintain backward compatibility (field is optional; empty → `null`)

## Testing Strategy
- **unit tests**: required for every task
- **e2e tests**: none in this project

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep plan in sync with actual work done

## Solution Overview
- `Race.map_url` is a `CharField` (not `URLField`, which would reject root-relative paths).
- Accepted values: empty; a root-relative path (`/…`, but **not** `//…` — protocol-relative would point at
  another host); an absolute URL with scheme **`https` only**. No `.mbtiles` extension check (a CDN link may
  have none).
- The server returns the value **as is**. A root-relative path is resolved by the client against its API base
  URL (in Android LAN mode that resolves to the LAN server for free). The map-enabled app builds are not in
  production yet; they get relative-path support before release.
- `races_version()` gains a `_RACES_SCHEMA_VERSION` prefix: adding the field changes no DB row, so a client
  holding a pre-deploy ETag would otherwise get 304 forever and never see `map_url`.

## Technical Details
- Model: `map_url = CharField("Оффлайн-карта (MBTiles)", max_length=500, blank=True, default="")`.
- Validation in `Race.clean()`:
  - `""` → OK
  - starts with `//` or `/\` → error (`\` is treated as `/` by WHATWG/OkHttp, so `/\host` is protocol-relative too)
  - starts with `/` → OK
  - else must pass `URLValidator(schemes=["https"])` → otherwise error
    «Введите https-URL или путь от корня (/media/maps/…).»
- Serializer: `map_url = serializers.SerializerMethodField()`, `get_map_url` returns `obj.map_url or None`;
  update the class docstring (it says «no images»; now also carries the map link).
- Versioning: `_RACES_SCHEMA_VERSION = 1` with a `# History: 1 = map_url added` comment, modelled on
  `_TEAMS_SCHEMA_VERSION`; raw hash input becomes `f"{_RACES_SCHEMA_VERSION}|{max_updated}|{count}"`.
  Bump whenever `RaceListSerializer` fields change.
- Wire example: `{"id": 12, …, "reg_status": "open", "map_url": "https://kolco24.ru/media/maps/r12.mbtiles"}`.

## What Goes Where
- **Implementation Steps**: model + migration, validation, form/template, serializer, versioning, tests, docs.
- **Post-Completion**: client support for root-relative paths, Android doc update, putting real files in place.

## Implementation Steps

### Task 1: Add `Race.map_url` field with validation

**Files:**
- Modify: `src/website/models/race.py`
- Create: `src/website/migrations/0099_race_map_url.py`
- Modify: `src/website/tests.py`

- [ ] add `map_url` CharField to `Race`
- [ ] extend `Race.clean()` with the `map_url` rules above (separate from the `header_image` loop — different rules)
- [ ] generate migration `uv run python src/manage.py makemigrations website -n race_map_url`
- [ ] write tests: `clean()` accepts `""`, `https://example.com/r.mbtiles`, `/media/maps/r.mbtiles`
- [ ] write tests: `clean()` rejects `http://example.com/r.mbtiles`, `//evil.com/r.mbtiles`, `ftp://example.com/r`,
      `/\evil.com/r.mbtiles`, `not-a-url` with an error on `map_url`
- [ ] run `uv run pytest src/website/tests.py` - must pass before next task

### Task 2: Edit `map_url` on the race edit page

**Files:**
- Modify: `src/apps/race/forms.py`
- Modify: `src/templates/race/race_form.html`
- Modify: `src/apps/race/tests.py`

- [ ] add `"map_url"` to `RaceForm.Meta.fields`
- [ ] add a manual `<input class="control mono…" name="map_url" type="text" maxlength="500">` in its own small
      card «Мобильное приложение» (not inside the «Шапка страницы» card), with error display and hint
      «https://… или /media/maps/&lt;файл&gt;.mbtiles»
- [ ] add `"map_url"` to `_race_form_data` (~1413) — not required (a missing blank CharField cleans to `""`),
      but keeps the fixture a full form
- [ ] write test: editing a race with a valid `map_url` saves it
- [ ] write test: an invalid `map_url` (`http://…`) re-renders the form with a field error and does not save
- [ ] run `uv run pytest src/apps/race/tests.py` - must pass before next task

### Task 3: Serve `map_url` in `GET /app/races/` and bust the races ETag

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/versioning.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] add `map_url` to `RaceListSerializer` (empty string → `null`)
- [ ] add `_RACES_SCHEMA_VERSION = 1` and fold it into `races_version()`; update the docstring
- [ ] update the races field-set test (~1161) to include `map_url`
- [ ] write tests: empty → `null`; `https://…` and `/media/maps/…` returned as is
- [ ] write test: saving a new `map_url` moves `races_version()`
- [ ] run `uv run pytest src/apps/mobile/tests.py` - must pass before next task

### Task 4: Verify acceptance criteria
- [ ] verify all requirements from Overview are implemented
- [ ] verify edge cases (`//host`, `http`, empty) are handled
- [ ] run full test suite: `uv run pytest`
- [ ] run `make format` and `make lint`

### Task 5: [Final] Update documentation
- [ ] `src/apps/mobile/README.md`: add `map_url` to the `/app/races/` table row (~949) and describe its semantics
      (null / https / root-relative resolved by the client, downloaded without HMAC); also update the races
      fingerprint line (~702) to mention the schema prefix
- [ ] `CLAUDE.md`: note `_RACES_SCHEMA_VERSION` under **Conditional GET** (bump on `RaceListSerializer` shape change);
      don't restate current constant values there (the legend value in CLAUDE.md is already stale)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**Manual verification:**
- set an absolute `https://` `map_url` on a test race, check the iOS and Android apps download the map
- check an app that cached the races ETag before deploy gets a 200 with `map_url` after deploy

**External system updates:**
- iOS and Android: resolve a root-relative `map_url` against the API base URL (today it fails the https guard /
  OkHttp `HttpUrl` parsing), before the map feature ships
- `android-kolco24/docs/design/API.md`: remove «сервер пока не отдаёт»
- place real `.mbtiles` files in `${DATA_LOCATION}/media/maps/` on the server (served by nginx `/media/`)
