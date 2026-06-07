# Legend Endpoint ETag / Versioning

## Overview
Give the mobile-app legend endpoint (`GET /app/race/<int:race_id>/legend/`,
`name="mobile:legend"`) a strong `ETag` + conditional GET (`If-None-Match` → `304`)
and a version entry in the sync manifest — exactly the way the teams endpoint
already works via `teams_version()`. This is the "later redo" the README/CLAUDE.md
explicitly anticipated when they noted the legend was deliberately left unversioned.

- **Problem it solves:** the mobile client currently re-downloads the full legend
  on every poll; there is no cheap "did the legend change?" probe. Teams already
  has this (ETag + `versions.teams` in `/sync/`); legend is the gap.
- **Key benefit:** bandwidth savings on unchanged legends (304 short-circuits
  serialization) and a single signed `/sync/` probe that now reports both
  `teams` and `legend` versions.
- **Integration:** `apps/mobile/versioning.py` stays the single source of truth —
  `LegendView` wraps the bare fingerprint in quotes for the `ETag`, `SyncView`
  emits the bare value in `versions.legend`, mirroring the teams pattern so an
  ETag and a manifest probe can never disagree.

## Context (from discovery)
- **Files/components involved:**
  - `src/website/models/checkpoint.py` — `Checkpoint` model (no `updated_at` today)
  - `src/website/migrations/` — latest is `0076_athlet_updated_at`; new migration depends on it
  - `src/apps/mobile/versioning.py` — `teams_version()` lives here; add `legend_version()`
  - `src/apps/mobile/views.py` — `LegendView`, `SyncView`, reference `TeamsView`
  - `src/apps/mobile/serializers.py` — `LegendCheckpointSerializer` (unchanged)
  - `src/apps/mobile/tests.py` — teams ETag/304/version tests are the copy template
  - `src/apps/mobile/README.md` + `CLAUDE.md` — both assert legend is unversioned; update both
- **Related patterns found:**
  - `teams_version(race_id)` = `blake2b(digest_size=8)` hex over a `|`-joined raw
    string of `MAX(updated_at)`/`COUNT` aggregates; `None` renders as literal
    `"None"` so an empty race is stable.
  - `Athlet.updated_at` (`auto_now`) was added by migration `0076` *specifically*
    so a member rename moves the version — the exact precedent for adding
    `Checkpoint.updated_at` here.
  - `TeamsView.get` computes `quoted = f'"{teams_version(race_id)}"'`, returns
    `HttpResponseNotModified()` with `ETag` set on `If-None-Match` match, else sets
    `resp["ETag"] = quoted` on the full `Response`.
  - Test helpers: `_signed_headers(method, path, secret, body)` builds signed
    request headers; `_make_race_with_category(...)` sets up fixtures. Tests are
    pytest-style functions with `@pytest.mark.django_db`.
- **Dependencies identified:**
  - `CheckpointType.draft` (`website/models/enums.py`) — legend view already
    excludes draft checkpoints; the fingerprint must mirror that queryset.
  - `Race.is_legend_visible` — when `False` the view returns `[]`, so the flag
    must be folded into the fingerprint.

## Development Approach
- **Testing approach:** Regular (code first, then tests) — the implementation
  mirrors an existing, well-tested pattern; tests are copied/adapted from the
  teams suite in the same task.
- Complete each task fully before moving to the next.
- **CRITICAL: every task includes new/updated tests** — both success and edge cases.
- **CRITICAL: all tests pass before starting the next task.**
- Run `uv run pytest src/apps/mobile/tests.py` after each change.
- Maintain backward compatibility: the legend response body shape is unchanged;
  only response headers and the `/sync/` manifest gain fields (purely additive).

## Testing Strategy
- **Unit tests:** required for every task. `legend_version()` fingerprint behavior
  (stability + each input that should move it) and `LegendView`/`SyncView` HTTP
  behavior (ETag header, 304 short-circuit, manifest field).
- **e2e tests:** N/A — no UI change; this is a signed JSON API. The signed
  request-level tests in `apps/mobile/tests.py` are the integration layer.

## Progress Tracking
- Mark completed items `[x]` immediately when done.
- New tasks get a ➕ prefix; blockers get ⚠️.
- Keep this plan in sync if scope shifts during implementation.

## Solution Overview
1. Add `Checkpoint.updated_at = DateTimeField(auto_now=True)` + migration (mirrors
   `Athlet.updated_at` / `0076`) so in-place checkpoint edits are observable.
2. Add `legend_version(race_id)` to `versioning.py`: `blake2b` over
   `"{MAX(updated_at)}|{COUNT}|{is_legend_visible}"`, aggregated over the
   **draft-excluded** queryset the view serializes.
3. `LegendView` gains ETag + `If-None-Match`/304, ETag set on all exit paths.
4. `SyncView.versions` gains `"legend"`.
5. Update `README.md` + `CLAUDE.md` to reflect the legend is now versioned.

### Key design decisions & rationale
- **`legend_version(race_id)` takes a bare `race_id`** (not the `Race` object the
  views already hold) to match `teams_version`'s signature and keep `versioning.py`
  the single source of truth. Cost: one extra trivial `values_list` query for the
  flag — accepted.
- **Draft checkpoints excluded from the fingerprint** (matching the served
  queryset). Consequence: editing a *draft* checkpoint does **not** move the
  version — correct, since it isn't in the response. A `kp → draft` transition
  *does* move it because `COUNT` drops by one.
- **`is_legend_visible` folded in** so a hide/show moves the version even though
  the body just toggles between the full list and `[]`. The ETag is set even on
  the hidden-empty response, so a later un-hide is detected.
- **`CheckpointTag` stays out of scope** — the legend never exposes tags, so a tag
  edit deliberately does not move the version. Keep this caveat documented (the
  README's existing "tags stay unversioned" note still holds).

## Technical Details
- **Fingerprint queryset:**
  ```python
  Checkpoint.objects.filter(race_id=race_id)
      .exclude(type=CheckpointType.draft.value)
      .aggregate(max_updated=Max("updated_at"), count=Count("id"))
  ```
  plus `Race.objects.filter(pk=race_id).values_list("is_legend_visible", flat=True).first()`.
- **Raw string:** `f"{max_updated}|{count}|{visible}"`; `None` aggregates render as
  literal `"None"` (empty/all-draft race stays stable, non-crashing — like teams).
- **ETag format:** `quoted = f'"{legend_version(race_id)}"'` — bare hex wrapped in
  double quotes (strong validator), identical convention to teams.
- **304 path:** `HttpResponseNotModified()` with `resp["ETag"] = quoted`, no
  serialization.
- **`SyncView`:** `"versions": {"teams": teams_version(race_id), "legend": legend_version(race_id)}`.

## What Goes Where
- **Implementation Steps** (`[ ]`): model field + migration, versioning helper,
  view wiring, tests, docs — all within this repo.
- **Post-Completion** (no checkboxes): deploy/migrate ordering note, mobile-client
  follow-up to actually send `If-None-Match`.

## Implementation Steps

### Task 1: Add `Checkpoint.updated_at` field + migration

**Files:**
- Modify: `src/website/models/checkpoint.py`
- Create: `src/website/migrations/0077_checkpoint_updated_at.py`

- [x] Add `updated_at = models.DateTimeField(auto_now=True)` to the `Checkpoint`
      model (place near the other fields; no `verbose_name` needed, matches
      `Athlet.updated_at`).
- [x] Generate the migration: `uv run python src/manage.py makemigrations website`
      (verify it produces `0077_checkpoint_updated_at` depending on
      `0076_athlet_updated_at`; mirror the `0076` body — single `AddField`).
- [x] Run `uv run python src/manage.py migrate` against the local DB to confirm it
      applies cleanly.
- [x] Sanity check: `uv run python src/manage.py makemigrations --check --dry-run`
      reports no missing migrations.
- [x] Run `uv run pytest src/apps/mobile/tests.py` — existing suite still green
      (no behavior change yet).

### Task 2: Add `legend_version(race_id)` to versioning.py

**Files:**
- Modify: `src/apps/mobile/versioning.py`
- Modify: `src/apps/mobile/tests.py`

- [x] Import `Checkpoint` and `CheckpointType` (and `Race`) in `versioning.py`
      alongside the existing `Athlet`/`Team` imports.
- [x] Add `legend_version(race_id)`: aggregate `Max("updated_at")`/`Count("id")`
      over the draft-excluded checkpoint queryset, fetch `is_legend_visible` via
      `values_list(..., flat=True).first()`, build
      `raw = f"{max_updated}|{count}|{visible}"`, return
      `hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()`.
- [x] Add a module docstring/comment note that `legend_version` is the single
      source of truth for the legend ETag + `versions.legend` (mirror the existing
      `teams_version` framing), and a one-line comment that the `is_legend_visible`
      re-query is **deliberate** — the helper keeps a bare `race_id` signature
      rather than accepting the view's `Race`, so a future reader shouldn't
      "optimize" it and break the `race_id` single-source-of-truth contract.
- [x] Write test: `legend_version` is **stable** across two calls for an unchanged
      race (incl. an empty race → no crash, deterministic).
- [x] Write tests: version **moves** on (a) checkpoint `description` edit,
      (b) checkpoint add, (c) checkpoint remove, (d) `kp → draft` flip
      (`COUNT` drops), (e) `draft → kp` flip (`COUNT` rises and the un-drafted
      row's `updated_at` enters `MAX` — the "checkpoint becomes visible" event a
      client must detect), (f) `race.is_legend_visible` toggle.
- [x] Write test: editing a **draft** checkpoint's description does **not** move
      the version (draft excluded).
- [x] Run `uv run pytest src/apps/mobile/tests.py -k legend_version` — must pass
      before Task 3.

### Task 3: Wire ETag/304 into LegendView and `versions.legend` into SyncView

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] Import `legend_version` from `.versioning` (next to `teams_version`).
- [ ] In `LegendView.get`: after `get_object_or_404`, compute
      `quoted = f'"{legend_version(race_id)}"'`; if
      `request.headers.get("If-None-Match") == quoted` return
      `HttpResponseNotModified()` with `resp["ETag"] = quoted`.
- [ ] Set `resp["ETag"] = quoted` on **both** remaining exit paths: the
      `is_legend_visible=False` empty response and the full serialized response.
- [ ] In `SyncView.get`: change `versions` to
      `{"teams": teams_version(race_id), "legend": legend_version(race_id)}`.
- [ ] Write view tests (mirror teams): legend response carries an `ETag` header;
      `If-None-Match` with the current ETag → `304` with an explicit
      `response.content == b""` assertion + ETag echoed (mirror
      `test_teams_if_none_match_returns_304_empty_body`); stale `If-None-Match` →
      `200` with a new ETag; the hidden (`is_legend_visible=False`) response still
      carries an ETag.
- [ ] Write test: `/sync/` manifest includes `versions.legend`, and it equals the
      bare value the legend `ETag` wraps in quotes (mirror
      `test_sync_versions_teams_matches_teams_etag`).
- [ ] Run `uv run pytest src/apps/mobile/tests.py` — full mobile suite green before
      Task 4.

### Task 4: Update documentation

**Files:**
- Modify: `src/apps/mobile/README.md`
- Modify: `CLAUDE.md`

- [ ] `README.md` + `CLAUDE.md` legend-endpoint paragraph: state the legend now
      carries a strong `ETag` + `If-None-Match`/304, fingerprint =
      `legend_version(race_id)` over `MAX(Checkpoint.updated_at)|COUNT|is_legend_visible`
      (draft excluded). Remove the "legend stays unversioned / no ETag until a
      later redo" wording.
- [ ] `SyncView` paragraph: `versions` now carries **both** `teams` and `legend`.
- [ ] Note `Checkpoint.updated_at` was added (migration `0077`) for the same reason
      as `Athlet.updated_at` — to catch in-place edits.
- [ ] Keep/clarify the caveat: `CheckpointTag` (tags) stays out of scope — a tag
      edit does not move the legend version (legend never exposes tags).
- [ ] No code in this task → no new tests; re-run `uv run pytest src/apps/mobile/tests.py`
      to confirm nothing regressed.

### Task 5: Verify acceptance criteria
- [ ] Verify all Overview requirements: ETag present, 304 works, `versions.legend`
      present and consistent with the ETag, response body shape unchanged.
- [ ] Verify edge cases: empty race, all-draft race, hidden legend — all return a
      stable ETag and don't crash.
- [ ] Run full suite: `uv run pytest`.
- [ ] Run `make format && make lint`.
- [ ] Confirm `uv run python src/manage.py makemigrations --check --dry-run` is clean.

### Task 6: [Final] Wrap up
- [ ] Confirm `README.md` + `CLAUDE.md` reflect the new versioned legend.
- [ ] Move this plan to `docs/plans/completed/`.

## Post-Completion
*Informational — no checkboxes.*

**Deploy / migration ordering:**
- Migration `0077` adds a nullable-by-`auto_now` `DateTimeField`; existing rows get
  the current timestamp on first save. The first `legend_version` after deploy will
  reflect the migration time for untouched checkpoints — clients simply see one
  version bump and refetch once. No data backfill required.

**Mobile-client follow-up (external):**
- The iOS/Android client must start sending `If-None-Match` (echoing the stored
  ETag) on legend requests and reading `versions.legend` from `/sync/` to benefit
  from the 304 path. Server is backward compatible without it (always returns 200
  + ETag), so this can ship independently.
