# Race map: filter tracks by device

## Overview
- A team can carry 2+ phones. Each phone records its own track, so one team has several tracks on `/race/<slug>/map/`.
- Today all segments of a team are drawn in one color, and there is no way to tell the phones apart or hide one of them.
- Goal: when a selected team has 2+ devices, show a submenu under its sidebar row with one checkbox per device. The checkbox hides or shows that device's polylines.
- Scope: tracks only. The team marker is unchanged (latest point across all devices). The «Взятия КП» layer is unchanged. Track color stays one per team, and line style does not change.

## Context (from discovery)
- Backend: `src/apps/race/views.py:RaceMapTrackView` already groups points into sessions keyed by `(install_id, segment_id)` and returns `{"segments": [{install_id, segment_id, points}]}`.
- `RaceMapPositionsView` returns one row per team (`DISTINCT ON (team_id)`). It is **not** changed.
- Device metadata: `src/apps/mobile/models.py:AppInstall` (`install_id` unique, `platform`, `app_version`, `last_seen`). It is written best-effort, so a row may be missing. There is no FK from `TrackPoint` to it.
- Frontend: `src/static/js/race_map.js`. State lives in `tracks[teamId] = {polylines, bySessionKey}`. `renderSidebar()` rewrites `innerHTML` on every 20 s poll. `drawTrack` runs after the sidebar is already rendered. `appendLivePoint` creates a new polyline for an unknown session.
- Tests: `src/apps/race/tests.py`, helper `_make_track_point(team, race, point_id, **kwargs)` (~line 4416), track tests from ~line 4597.
- There are no JS tests in the project.

## Development Approach
- **testing approach**: Regular (code first, then tests)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task**
- **CRITICAL: update this plan file when scope changes during implementation**
- work on a feature branch, never on `master`
- run `make format && make lint` before every commit

## Testing Strategy
- **unit tests**: pytest-style functions in `src/apps/race/tests.py` for the new `devices` field.
- **e2e tests**: the project has none. JS behavior is checked by hand (see Post-Completion).

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep plan in sync with actual work done

## Solution Overview
- Devices come **with the track** (option A). The track is fetched only when a team is selected, and the submenu is only shown for a selected team, so no extra request and no change to the positions contract.
- The server computes per-device stats in the loop it already runs over the team's points, plus one `AppInstall` query for `platform`.
- The client keeps hidden-device state in JS (`tracks[teamId].hidden`), not in the DOM, so the 20 s sidebar re-render keeps the checkboxes.
- Deselecting a team drops all its state. Re-selecting shows all devices again.

## Technical Details

Track response:

```json
{
  "segments": [{"install_id": "…", "segment_id": "…", "points": [[lat, lon], …]}],
  "devices": [
    {"install_id": "…", "index": 1, "platform": "android",
     "first_gps_time_ms": 1727000000000, "last_gps_time_ms": 1727003600000, "points": 412}
  ]
}
```

- `devices` sorted by `first_gps_time_ms`, tie-break `install_id`. `index` starts at 1.
- `points` = raw point count, before thinning.
- `last_gps_time_ms` = max `gps_time_ms` of the device.
- `platform` from `AppInstall`; `""` when there is no row.
- `devices` is always present (even with one device, `[]` with no points). The client decides whether to show the submenu.
- Empty `install_id` (`""`) is its own device, same as in `segments`.

Client:
- `tracks[teamId] = {polylines, bySessionKey, devices, hidden: {install_id: true}, byInstall: {install_id: [polyline]}}`.
- Submenu row text: `Устр. N · <platform> · X мин назад · N точек` (drop the platform part when empty). «X мин назад» is computed from `last_gps_time_ms` on each render, clamped at 0 (it is the phone's clock, which may run ahead; team-row staleness uses the server `received_at` — different clocks, on purpose). A device with no point for more than `STALE_MS` gets the stale (grey) style.
- **Device stats are as of track load, not live.** The positions poll returns one row per team (the newest fix across all phones), so it can't keep per-device stats right: the second phone would freeze and turn grey while still sending, and `points` would undercount (a phone uploads batches of up to 500 points). Stats refresh on deselect + reselect.
- Submenu markup: a **sibling** `<div class="rm-devices">` right after the team's `.rm-row` div, not inside it (`.rm-row` is flex, dims when stale, and toggles the team on click). Listeners are bound in `renderSidebar` after the `innerHTML` write, next to the `.rm-row` binding, on the `change` event. No `stopPropagation` needed.
- Checkboxes carry `data-team-id` + `data-device-idx` (array index into `tracks[teamId].devices`), never the raw `install_id`. `platform` goes through `escapeHtml()`. Both `install_id` and `platform` are unrestricted client-supplied strings (`X-Install-Id`, `X-App-Platform`).
- Toggle: `map.removeLayer(line)` / `line.addTo(map)` for every polyline in `byInstall[install_id]`.
- `appendLivePoint`:
  - new polyline for a hidden device → create it and register it in `byInstall`, but don't add it to the map;
  - unknown `install_id` → push `{install_id, index: devices.length + 1, platform: "", first/last = row.gps_time_ms, points: 1}` (the only live-derivable fact);
  - known device → stats unchanged.
- Empty `install_id` (`""`): shown in the submenu and toggled via `byInstall[""]`, but `sessionKey()` returns `null` for it, so it never gets live points. Same as today — leave it.
- After `drawTrack` → `renderSidebar()`, so the submenu shows up without waiting for the next poll.
- In `fetchPositions`, move `renderSidebar()` after the `appendLivePoint` loop, so a newly seen device appears in the same poll.

## What Goes Where
- **Implementation Steps**: backend change + tests, JS/CSS change, docs.
- **Post-Completion**: manual check in the browser.

## Implementation Steps

### Task 1: Add `devices` to the track endpoint

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/tests.py`

- [ ] in `RaceMapTrackView.get`, collect per-`install_id` `first_gps_time_ms`, `last_gps_time_ms` (max), raw `points` inside the existing loop over points
- [ ] fetch `platform` with one `AppInstall.objects.filter(install_id__in=...)` query; missing row → `""`
- [ ] build `devices` sorted by `(first_gps_time_ms, install_id)` with 1-based `index`; add it to the `JsonResponse`
- [ ] update the class docstring with the new field
- [ ] update existing track tests if they assert the exact response dict
- [ ] test: two devices → correct `index` order, `points` (raw, not thinned), `first/last`, `platform` from `AppInstall`
- [ ] test: device without an `AppInstall` row → `platform == ""`
- [ ] test: team with no points → `devices == []`
- [ ] test: device with empty `install_id` is its own device
- [ ] test: query count does not grow with the number of devices — capture the count for 1 device with `CaptureQueriesContext`, assert 3 devices give the same count (no hardcoded total; don't compare with the no-points case, where `install_id__in=[]` skips a query)
- [ ] tests pass distinct `gps_time_ms` values under 30 s apart (the `_make_track_point` default is one fixed value), so raw `points` differs from the thinned count
- [ ] run `uv run pytest src/apps/race/tests.py -k race_map` — must pass before task 2

### Task 2: Device submenu and toggle on the map page

**Files:**
- Modify: `src/static/js/race_map.js`
- Modify: `src/static/css/race_map.css`

- [ ] extend `drawTrack` to store `devices`, `hidden = {}`, `byInstall`; call `renderSidebar()` after drawing
- [ ] render the device submenu in `renderGroup` as a sibling `<div class="rm-devices">` after the team row, for a selected team with `devices.length >= 2`; checkboxes read state from `hidden`, keyed by device array index; `platform` escaped
- [ ] bind `change` listeners in `renderSidebar` to a `toggleDevice(teamId, idx)` that shows/hides that device's polylines
- [ ] update `appendLivePoint`: hidden device's new line stays off the map; add unknown `install_id` to `devices`; no live stats for known devices
- [ ] in `fetchPositions`, call `renderSidebar()` after the `appendLivePoint` loop
- [ ] add compact submenu styles as flat selectors (`.rm-devices`, `.rm-device`, `.rm-device.is-stale`) in `race_map.css`, matching the existing `.rm-*` style (mobile sidebar is only 260 px high)
- [ ] no JS tests exist — run the full Python suite to make sure nothing else broke: `uv run pytest`

### Task 3: Verify acceptance criteria
- [ ] a selected team with 2+ devices shows the submenu; with 1 device it does not
- [ ] toggling a device hides/shows only its polylines; the marker and marks layer do not change
- [ ] the checkbox state survives the 20 s poll re-render
- [ ] deselect + reselect shows all devices again
- [ ] run full test suite: `uv run pytest`
- [ ] run `make format && make lint`

### Task 4: [Final] Update documentation
- [ ] update the `RaceMapTrackView` part of `CLAUDE.md` (response now has `devices`; JS device submenu filters tracks only, marker unchanged)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**Manual verification**:
- create track points from two `install_id`s for one team (shell or two emulators), open `/race/<slug>/map/`, select the team
- toggle each device, wait for a poll, check the lines stay hidden and the checkboxes keep their state
- post a point from a third `install_id` while the team is selected — it must appear in the submenu
