# Judge Scans Upload Endpoint (`POST /app/race/<race_id>/judge_scans/`)

## Overview

Add a new mobile-app endpoint that ingests a batch of **judge start/finish
bracelet scans** for a race. A judge at the start/finish line scans participant
bracelets (an alternative to self-marking); the station scans **all teams of the
race at once**, so the endpoint is race-scoped with **no `team_id`**.

The client is already implemented (`JudgeScanRepository` in the Android app) and
self-heals once the server exists. Contract:
`/Users/alff0x1f/src/kolco24_app_v2/docs/design/UPLOAD.md`, section
*"POST /app/race/<race_id>/judge_scans/"*.

This is a **near-clone of the existing `/track/` endpoint**
(`TrackUploadView`/`TrackPoint`) with two structural differences:

1. **No `team_id`** — race-scoped only; there is **no per-team membership check**.
2. **`source_install_id` comes from the signed body** (like `/marks/`
   `MarkUploadView`), not the `X-Install-Id` header.

Trust boundary is identical to `/track/` and `/marks/`: **build-HMAC-only**
(default `[SignedAppPermission]`, **not** the per-person bearer write layer),
`mobile-write` throttle (60/min, already configured).

## Context (from discovery)

- **Files involved** (all under `src/apps/mobile/`): `models.py`,
  `serializers.py`, `views.py`, `urls.py`, `tests.py`, new migration
  `migrations/0011_judgescan.py`; plus `CLAUDE.md` (invariant doc).
- **Related patterns found**:
  - `TrackPoint` model (`models.py:86`) — immutable/write-only, client-UUID PK,
    no `updated_at`, out of `versioning.py`, cross-app FK into `website`.
  - `TrackUploadView` (`views.py:333`) — resolve published race → validate →
    `bulk_create(ignore_conflicts=True)` → ack all ids.
  - `TrackPointSerializer`/`TrackUploadSerializer` (`serializers.py:63`/`97`) —
    field bounds, `max_length=500` batch cap, all-or-nothing 400.
  - `MarkUploadView` (`views.py:435`) — reads `source_install_id` from the
    **signed body** (the pattern this endpoint copies for provenance).
  - Test helpers: `_signed_post` (`tests.py:5073`), autouse
    `_clear_throttle_cache` fixture (`tests.py:16`), `test_trackpoint_*` /
    `test_track_upload_*` suites (`tests.py:6209`–`6790`).
- **Dependencies identified**: `settings.py` already defines the `mobile-write`
  throttle rate (`REST_FRAMEWORK.DEFAULT_THROTTLE_RATES`). No settings change.
- **Latest migration**: `migrations/0010_alter_markphoto_image.py`.

## Development Approach

- **Testing approach**: Regular (code first, then tests) — model/serializer/view
  then the test block, mirroring how the existing track suite is structured.
- Complete each task fully before moving to the next; run tests after each.
- **Every task includes new/updated tests** (success + error scenarios).
- **All tests must pass before starting the next task.**
- Maintain backward compatibility (purely additive — no existing route/model
  changes).
- `make format && make lint` before commit. **Never commit to master — branch
  first.**

## Testing Strategy

- **Unit tests**: pytest-style functions with `@pytest.mark.django_db`, `client`
  / `django_user_model` fixtures (project convention — not `TestCase`).
- **No e2e tests**: the project has no UI e2e suite for `/app/*`; the endpoint is
  exercised via signed-request integration tests through the Django test
  `client` (the established pattern for mobile endpoints).
- Run: `uv run pytest src/apps/mobile/tests.py`.

## Progress Tracking

- Mark completed items `[x]` immediately when done.
- `➕` prefix for newly discovered tasks; `⚠️` prefix for blockers.
- Keep this file in sync with actual work.

## Solution Overview

One new immutable model, one serializer pair, one view, one URL, one migration,
one test block, one CLAUDE.md paragraph. The endpoint stores every submitted scan
row idempotently (client-UUID PK + `ignore_conflicts`) and echoes all submitted
ids in `accepted[]`. Read-side scoring/dedup of repeated peaks per participant is
**out of scope** (a future task, per the contract) — ingestion only accepts and
stores.

## Technical Details

**Body** (`application/json`):
```json
{
  "source_install_id": "b3c4…uuid",
  "scans": [
    {
      "id": "0f9c…uuid",
      "event_type": "start",
      "participant_number": 101,
      "nfc_uid": "04F1E2D3C4B5A6",
      "wall_ms": 1718900000000,
      "trusted_ms": 1718900000123,
      "elapsed_at": 9876543,
      "boot_count": 7
    }
  ]
}
```
**Response** `200`: `{ "accepted": ["0f9c…uuid", …] }` (all submitted ids).

**Locked decisions:**
- **Idempotency**: immutable, TrackPoint-style
  `bulk_create(ignore_conflicts=True)`. A re-sent `id` is silently skipped. No
  enrichment. **No `updated_at`; deliberately out of `versioning.py`** (never
  touches ETag/`sync`). Model **not** admin-registered.
- **`nfc_uid`**: normalized on store (`.strip().upper()`) in the **view** (before
  building objs; `bulk_create` bypasses `save()` overrides anyway) to match the
  member `Tag` pool format for post-facto resolution. **Deliberate divergence
  from `Mark.cp_nfc_uid` / `MarkPresent.nfc_uid`, which are stored raw** (and are
  absent from CLAUDE.md's "`nfc_uid` normalized invariant"). Justified: judge
  scans resolve participants against the normalized member `Tag` pool by uid, so
  normalizing at ingest makes that read-side match direct. Must be called out in
  both the model docstring and the CLAUDE.md invariant so it reads as a chosen
  behavior, not an inconsistency.
- **All-or-nothing 400, NOT partial-accept.** The contract prose
  (`UPLOAD.md`) describes a partial-accept response for this endpoint, but the
  real `TrackUploadView`/`MarkUploadView` are all-or-nothing
  (`serializer.is_valid(raise_exception=True)` → one bad row 400s the whole
  batch), and CLAUDE.md documents them as such despite the same contract
  language. This plan **matches the codebase**, not the prose. Accepted risk: a
  single malformed scan row 400s the batch and the client drops it (potential
  loss of the valid rows in that batch) — consistent with the sibling upload
  endpoints, so no new behavior. The Task 7 CLAUDE.md invariant must say
  **"all-or-nothing 400"**, mirroring the Track/Marks invariants, **not** echo
  the contract's "partial-accept" wording.
- **Field rules**: `nfc_uid` required non-blank; `participant_number`
  `min_value=0`, `max_value=2147483647`; `event_type`
  `ChoiceField(["start","finish"])`; `wall_ms` required BigInt; `trusted_ms` /
  `elapsed_at` nullable BigInt (`2^63-1` cap); `boot_count` nullable 32-bit
  (`2^31-1` cap); `scans` `max_length=500`, all-or-nothing 400; empty batch acks
  `[]`.
- **No `team_id`** anywhere → **no team-not-in-race test** (the one omission vs
  the track suite).

## What Goes Where

- **Implementation Steps** (checkboxes): model, serializers, view+URL, migration,
  tests, CLAUDE.md — all within this repo.
- **Post-Completion** (no checkboxes): deploy note (migration must run), and the
  Android client self-heals with no change once the route is live.

## Implementation Steps

### Task 1: Add `JudgeScan` model

**Files:**
- Modify: `src/apps/mobile/models.py`

- [x] Add `JudgeScan` model after `MarkPhoto`: `id` `CharField(max_length=64,
      primary_key=True)`; `race` `FK("website.Race", on_delete=CASCADE,
      related_name="judge_scans")`; `source_install_id` `CharField(max_length=64)`;
      `event_type` `CharField(max_length=16)`; `participant_number`
      `IntegerField()`; `nfc_uid` `CharField(max_length=255)`; `wall_ms`
      `BigIntegerField()`; `trusted_ms` `BigIntegerField(null=True)`; `elapsed_at`
      `BigIntegerField(null=True)`; `boot_count` `IntegerField(null=True)`;
      `created_at` `DateTimeField(auto_now_add=True)`.
- [x] Docstring modeled on `TrackPoint`: immutable/write-only, client-UUID PK =
      idempotency key, **no `updated_at` / out of `versioning.py`**, **no `team`
      FK because the judge station is race-wide**, `nfc_uid` normalized by the
      view (not a `save()` override, since `bulk_create` bypasses it).
- [x] Add `__str__` returning
      `f"JudgeScan({self.id} race={self.race_id} {self.event_type})"`.
- [x] Generate the migration (Task 4 verifies it applies).

### Task 2: Add serializers

**Files:**
- Modify: `src/apps/mobile/serializers.py`

- [x] Add `JudgeScanSerializer` (one scan) mirroring `TrackPointSerializer`
      style: `id` `CharField(max_length=64, min_length=1)`; `event_type`
      `ChoiceField(choices=["start","finish"])`; `participant_number`
      `IntegerField(min_value=0, max_value=2147483647)`; `nfc_uid`
      `CharField(max_length=255, allow_blank=False, trim_whitespace=True)`;
      `wall_ms` `IntegerField(min_value=0, max_value=9223372036854775807)`;
      `trusted_ms`/`elapsed_at` `IntegerField(required=False, allow_null=True,
      min_value=0, max_value=9223372036854775807)`; `boot_count`
      `IntegerField(required=False, allow_null=True, min_value=0,
      max_value=2147483647)`.
- [x] Add `JudgeScanUploadSerializer` (body): `source_install_id`
      `CharField(max_length=64)`; `scans` `JudgeScanSerializer(many=True,
      allow_empty=True, max_length=500)`.
- [x] Docstrings noting the two divergences from the track pair (no `team_id`;
      `source_install_id` is the provenance key from the signed body) and the
      `event_type` choice constraint.
- [x] Write serializer tests (valid batch; omitted vs explicit-null nullables;
      missing required field; out-of-range magnitudes; empty `id`; over-500;
      **bad `event_type` choice**; empty `scans` valid) — see Task 5.
- [x] Run tests — must pass before next task.

### Task 3: Add view and URL route

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/urls.py`

- [x] In `views.py`, import `JudgeScan` from `.models` and the two serializers.
- [x] Add `JudgeScanUploadView(AppAPIView)` modeled on `TrackUploadView`:
      `throttle_classes=[ClientIPScopedRateThrottle]`,
      `throttle_scope="mobile-write"`; docstring covering build-HMAC-only trust
      boundary, race-scoped (no team check), `source_install_id` from body,
      immutable `ignore_conflicts` idempotency, out of `versioning.py`.
- [x] `post(self, request, race_id)`: `get_object_or_404(Race, pk=race_id,
      is_published=True)` → validate with `JudgeScanUploadSerializer` (400) →
      **no team check** → read `source_install_id` from validated data → build
      `JudgeScan` objs, normalizing `nfc_uid` via `.strip().upper()` →
      `JudgeScan.objects.bulk_create(objs, ignore_conflicts=True)` →
      `Response({"accepted": [s["id"] for s in scans]}, status=200)`. Empty batch
      acks `[]` (no special-casing needed — an empty `bulk_create` is a no-op).
- [x] In `urls.py`, import `JudgeScanUploadView` and add
      `path("race/<int:race_id>/judge_scans/", JudgeScanUploadView.as_view(),
      name="judge_scans")`.
- [x] Write view tests (happy-path persists + acks; nfc_uid normalization;
      source_install_id read from body) — see Task 5.
- [x] Run tests — must pass before next task.

### Task 4: Generate and apply the migration

**Files:**
- Create: `src/apps/mobile/migrations/0011_judgescan.py`

- [x] `uv run python src/manage.py makemigrations mobile` (produces
      `0011_judgescan`, depending on `0010_alter_markphoto_image` and the
      relevant `website` migration for the `Race` FK).
- [x] Inspect the generated migration: single `CreateModel`, PK is the
      `CharField` `id`, no extra indexes beyond the FK.
- [x] `uv run python src/manage.py migrate` against the local DB to confirm it
      applies cleanly.
- [x] `uv run python src/manage.py makemigrations --check --dry-run` reports no
      pending changes.
- [x] Run tests (`--reuse-db` will pick up the new migration) — must pass before
      next task.

### Task 5: Add the `JudgeScan` test block

**Files:**
- Modify: `src/apps/mobile/tests.py`

- [x] Add a `JudgeScan` section mirroring `test_trackpoint_*` /
      `test_track_upload_*`, reusing `_signed_post` and the autouse
      `_clear_throttle_cache` fixture. **Model**: round-trip all fields;
      round-trip non-null optionals; `bulk_create(ignore_conflicts=True)`
      idempotency; duplicate-PK create raises.
- [x] **Serializer** tests: valid batch; omitted nullables resolve absent;
      explicit-null nullables; missing required field invalid; out-of-range
      magnitudes invalid; empty `id` invalid; over-500 invalid; **bad
      `event_type` invalid**; empty `scans` valid.
- [x] **View** tests: wrong signature → 403; happy-path persists + acks all ids;
      idempotent no-duplicate-rows; **`nfc_uid` normalization** (lowercase input
      stored uppercase); **`source_install_id` read from body**; unpublished race
      → 404; nonexistent race → 404; malformed scan → 400; empty `scans` acks
      `[]`; nullable round-trip through the view.
- [x] Add an oversized-`source_install_id` serializer test (>64 chars → 400 via
      `max_length=64`) — cheap and worth it since it is body-sourced here (the
      track/marks suites omit it because their install_id is header-sourced).
- [x] **Explicitly no team-not-in-race test** (endpoint has no `team_id`) — add a
      one-line comment noting this deliberate divergence from the track suite.
- [x] Run `uv run pytest src/apps/mobile/tests.py` — all pass before next task.

### Task 6: Verify acceptance criteria

- [x] Endpoint round-trips a full contract body (all fields incl. nullables) and
      acks every id.
- [x] Idempotent re-send produces no duplicate rows and still acks.
- [x] `nfc_uid` stored normalized; `source_install_id` sourced from the signed
      body; `event_type` restricted to `start`/`finish`.
- [x] Build-HMAC-only: valid signature succeeds, bad signature 403; no bearer
      required.
- [x] `JudgeScan` is absent from `versioning.py` (grep confirms) and not
      admin-registered (grep `admin.py`).
- [x] `make lint` clean; `uv run pytest src/apps/mobile/tests.py` fully green.

### Task 7: [Final] Update documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: this plan (move to completed)

- [x] Add a **"Judge scans upload"** invariant paragraph to the `apps.mobile`
      section of `CLAUDE.md`, mirroring the **"Track upload"** invariant:
      `POST /app/race/<id>/judge_scans/` (name `judge_scans`), build-HMAC-only
      (NOT the per-person write layer), `mobile-write` throttle; race-scoped with
      **no `team_id`**; `source_install_id` from the signed body; `JudgeScan`
      immutable/write-only with client-UUID PK, `bulk_create(ignore_conflicts=
      True)`, **no `updated_at` / out of `versioning.py`**, not admin-registered;
      **all-or-nothing 400** (one bad row 400s the batch — use the Track/Marks
      wording, NOT the contract's "partial-accept"); `nfc_uid` normalized on store
      (**note the deliberate divergence from `Mark.cp_nfc_uid`, stored raw**);
      `verified`/scoring/per-participant dedup are out of scope (future read-side
      task).
- [x] `mkdir -p docs/plans/completed` and move this plan there.

## Post-Completion

*Informational — no checkboxes.*

**Deployment:**
- The new migration `0011_judgescan` must run on deploy (standard `migrate`
  step). Purely additive — no data backfill, no downtime concern.

**Client:**
- The Android `JudgeScanRepository` self-heals: once the route returns `200`, the
  pending rows (`uploadedLocal/uploadedCloud = 0`) upload on the next 60-second
  tick with **no client change**.

**Out of scope (future tasks, per the contract):**
- Read-side scoring, per-`participant_number` peak dedup, and admin
  reattribution/rendering of judge scans.
