# Marks Upload — `POST /app/race/<race_id>/marks/`

## Overview

Backend implementation of the checkpoint-take ingestion endpoint for the Android
app. A team's phone uploads a **batch of checkpoint-take events** (взятия КП); the
server validates, stores the full device-provenance, computes a per-mark
`verified` flag (proof the КП was physically scanned), and acks the accepted
client ids for the app's idempotent retry loop.

- **Problem it solves**: today the app records takes into local Room with
  `uploadedCloud = 0` and waits for a backend endpoint to exist. This endpoint
  closes that loop — the existing client self-heals with no app change.
- **Wire contract is already frozen** in `scratch/UPLOAD.md` (the
  `POST /app/race/<race_id>/marks/` section) and the Android client is already
  implemented (`kolco24_app_v2`: `MarkRepository`, `MarkDao`, `MarkDtos.kt`,
  `ApiClient.uploadMarks`). **This task is backend-only** and must match that
  contract exactly.
- **Integration**: a near-clone of the existing `/track/` endpoint
  (`TrackPoint` / `TrackUploadView`) — same `AppAPIView` base, same build-HMAC
  auth envelope, same `mobile-write` throttle, same client-UUID-PK + `bulk_create`
  idempotency pattern, same out-of-`versioning.py` posture. **One deliberate
  divergence**: where `TrackPoint` is strictly immutable (`ignore_conflicts`), a
  `Mark` is **enrichment-upserted** (`update_conflicts`) because the client
  re-sends the same `id` to deliver a late GPS fix / roster — see the idempotency
  note in Solution Overview.

## Context (from discovery)

- **Files/components involved**:
  - `src/apps/mobile/models.py` — add `Mark` + `MarkPresent` (next to `TrackPoint`).
  - `src/apps/mobile/serializers.py` — add `MarkUploadSerializer` and nested
    serializers (next to `TrackUploadSerializer`, reusing `FiniteFloatField`).
  - `src/apps/mobile/views.py` — add `MarkUploadView` (mirror `TrackUploadView`).
  - `src/apps/mobile/urls.py` — wire `name="marks"`.
  - `src/apps/mobile/migrations/0008_*.py` — new migration (cross-app FKs into
    `website`, like `0007_trackpoint`).
  - `src/apps/mobile/tests.py` — new tests (reuse `_signed_post` at line 4958 and
    the autouse `_clear_throttle_cache` fixture at line 16).
  - `src/apps/mobile/README.md` + root `CLAUDE.md` — docs.
- **Related patterns found**:
  - `TrackPoint` (`models.py:86`): client-UUID PK, immutable, no `updated_at`,
    out of `versioning.py`; cross-app FKs `team`/`race` to `website`.
  - `TrackUploadView` (`views.py:319`): `get_object_or_404(Race, is_published=True)`
    → serializer 400 → team-in-race check (`category2__race_id=race_id`) → 404 →
    `bulk_create(ignore_conflicts=True)` → `200 {"accepted": [...]}`. Build-HMAC
    only (`permission_classes = [SignedAppPermission]`), `throttle_scope =
    "mobile-write"`.
  - `TrackUploadSerializer` / `TrackPointSerializer` (`serializers.py:63`):
    `FiniteFloatField` (rejects NaN/inf), int bounds to `2^63−1`, `min_value=0`
    on physically non-negative magnitudes, `altitude` unbounded, `max_length=500`
    batch cap, all-or-nothing validation.
  - `bid` definition (`legend_crypto.py:128`): `bid =
    hashlib.sha256(code).hexdigest()[:16]` where `code` is the tag's 16 raw bytes.
    On the wire `cp_code` is the **hex** of those bytes, so the server check is
    `sha256(bytes.fromhex(cp_code)).hexdigest()[:16] == tag.bid`.
- **Dependencies identified**: `website.Team`, `website.Race`,
  `website.models.checkpoint.CheckpointTag` (for the `verified` bid lookup).
  Latest migration is `0007_trackpoint` → new one is `0008`.

## Development Approach

- **Testing approach**: Regular (code first, then tests) — mirrors how the
  `/track/` endpoint was built; the contract is frozen so behavior is unambiguous.
- Complete each task fully before moving to the next; small focused changes.
- **CRITICAL: every task MUST include new/updated tests** (success + error/edge).
- **CRITICAL: all tests must pass before starting the next task.**
- Tests are **pytest-style functions** with `@pytest.mark.django_db` and the
  `client`/`django_user_model` fixtures — never Django `TestCase`.
- Run `make format && make lint` before committing.
- **Do NOT use python3/sed to edit sources — only direct edits** (per project rule).

## Testing Strategy

- **unit tests**: required for every task — model round-trips, serializer
  validation matrix, view behavior (status codes, idempotency, `verified`).
- **e2e tests**: none — this is a JSON API with no UI. The view-level signed-request
  tests (via `_signed_post`) are the integration layer.
- Throttle isolation: any test hitting the throttled endpoint relies on the
  autouse `_clear_throttle_cache` fixture (`tests.py:16`) calling `cache.clear()`
  before/after each test (all test requests share one client IP). It is already
  module-wide autouse, so new tests in `tests.py` inherit it automatically — do
  **not** add a second copy.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep this plan in sync with actual work

## Solution Overview

- **Two normalized models.** `Mark` is one row per take report (PK = client UUID
  = idempotency key); `MarkPresent` is one row per present member, FK back to
  `Mark`, with `unique_together("mark", "number_in_team")` so child inserts are
  conflict-safe on re-send. Chosen over a single JSON row so the roster is
  queryable for the future (out-of-scope) scoring/reattribution task.
- **`verified` computed at ingest** with a single per-batch prefetch of the race's
  `CheckpointTag.bid` values — no extra query per mark.
- **Idempotent enrichment merge (NOT strict-immutable).** The client deliberately
  **re-sends the same `id`** when data lands mid-request — a GPS fix
  (`attachLocation`) or a new member (`addMember`) arrives after the DTO was
  serialized but before the row was marked uploaded, so the first POST carries
  `location=null` / a partial `present[]` and a later POST carries the enriched
  payload (client refs: `MarkRepository.kt:229` two-guard re-upload;
  `MarkRepositoryUploadTest.kt` `uploadPending_gpsArrivesAfterFetchBeforeMark_*`
  and `_memberAddsAfterFetchBeforeMark_*`). A pre-filter-and-skip design would
  **permanently lose** the late GPS/roster. So a repeat `id` must **merge**:
  upsert the `Mark` scalar fields to the newer payload and **add** any missing
  `MarkPresent` rows. Enrichment is monotonic (location goes null→value, `present`
  only grows, `complete` false→true; the take-moment times and `cp_*` are stable),
  so a blind **last-write-wins upsert** of the scalar fields is correct and
  simplest — no field-level "only if more complete" logic. Mechanism:
  `Mark.objects.bulk_create(objs, update_conflicts=True, unique_fields=["id"],
  update_fields=[all scalars except id/created_at])` (Postgres `ON CONFLICT (id)
  DO UPDATE`, one atomic statement — no pre-filter `SELECT`), then
  `MarkPresent.objects.bulk_create(present_objs, ignore_conflicts=True)` for **all**
  marks (additive: the `unique_together` skips slots already stored; an existing
  slot's snapshot is stable per `number_in_team` in the live client, so it is
  never rewritten). `accepted` echoes **all** submitted ids.
  - **In-batch `id` de-dup (the one `update_conflicts` gotcha).** Unlike
    `ignore_conflicts`, a single `INSERT … ON CONFLICT (id) DO UPDATE` that
    contains the **same `id` twice** raises Postgres `CardinalityViolation`
    ("cannot affect row a second time") → 500. The real client never sends
    in-batch dup ids (its `unuploaded*` query returns distinct PKs), but the
    serializer must not let a malformed body 500. So the view **de-dups `marks`
    by `id` before building the upsert objects, keeping the last occurrence**
    (consistent with last-write-wins); `accepted` still echoes the originally
    submitted ids. `MarkPresent` is unaffected — it uses `ignore_conflicts`,
    which tolerates in-batch dups.
- **Same trust boundary as the reads / `/track/`.** Build-HMAC only (no per-person
  bearer). `team_id` and `source_install_id` are spoofable and accepted as-is;
  the signed body makes them tamper-evident. `source_install_id` is read from the
  **signed body** (the contract's grouping key); the `X-Install-Id` header stays
  for `AppInstall` stats as today.

## Technical Details

### `Mark` model fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | `CharField(max_length=64, primary_key=True)` | client UUID, idempotency key |
| `team` | `FK(website.Team, CASCADE, related_name="marks")` | cross-app |
| `race` | `FK(website.Race, CASCADE, related_name="marks")` | cross-app |
| `source_install_id` | `CharField(max_length=64)` | from **body**, provenance key |
| `checkpoint_id` | `IntegerField` | **plain int, not FK** — unknown КП still accepted |
| `method` | `CharField(max_length=16)` | `nfc` / `photo` |
| `cp_code` | `CharField(max_length=64, blank=True)` | hex of КП code; blank for a future `photo` mark |
| `cp_nfc_uid` | `CharField(max_length=255, blank=True)` | scanned КП tag uid; blank for a future `photo` mark |
| `expected_count` | `IntegerField` | client opinion, stored as-is |
| `complete` | `BooleanField` | client opinion, stored as-is |
| `verified` | `BooleanField` | server-computed at ingest |
| `trusted_ms` | `BigIntegerField(null=True)` | |
| `wall_ms` | `BigIntegerField` | required — sole fallback when `trusted_ms` null |
| `elapsed_at` | `BigIntegerField(null=True)` | |
| `boot_count` | `IntegerField(null=True)` | |
| `loc_lat` … `loc_elapsed_at` | 7 flat nullable columns | whole `location` may be null |
| `created_at` | `DateTimeField(auto_now_add=True)` | |

7 location columns: `loc_lat` (`FloatField null`), `loc_lon` (`FloatField null`),
`loc_accuracy` (`FloatField null`), `loc_altitude` (`FloatField null`),
`loc_vertical_accuracy` (`FloatField null`), `loc_gps_time_ms`
(`BigIntegerField null`), `loc_elapsed_at` (`BigIntegerField null`).

**No `updated_at`** — unlike `TrackPoint` the row is **not** immutable (it is
enrichment-upserted on a repeat `id`, see the idempotency note), but it still
carries no `updated_at` and stays deliberately **out of** `versioning.py`/ETag/
`sync`: nothing reads a version off `Mark`, so there is no fingerprint to keep
fresh. `created_at` is `auto_now_add` and is **excluded from `update_fields`** so
an enrichment upsert preserves the original insert time.

### `MarkPresent` model fields

| Field | Type | Notes |
|-------|------|-------|
| `mark` | `FK(Mark, CASCADE, related_name="present")` | |
| `nfc_uid` | `CharField(max_length=255, null=True)` | sentinel = null |
| `code` | `CharField(max_length=64, null=True)` | member chip code (future) |
| `number` | `IntegerField` | global pool number; `0` = sentinel |
| `number_in_team` | `IntegerField` | slot in team |

> **Integer caps (400-not-500 discipline).** `checkpoint_id`, `expected_count`,
> `number`, `number_in_team`, **and `Mark.boot_count`** back 32-bit Postgres
> `IntegerField` columns. Their serializer fields **must** carry
> `max_value=2147483647` (2³¹−1; and `min_value=0` where physically
> non-negative) so an oversized-but-syntactically-valid int is a clean 400, not a
> `DataError`/500 at `bulk_create` — same hazard `TrackPointSerializer` guards
> (`serializers.py:84-87`). The BigInt columns (`trusted_ms`, `wall_ms`,
> `elapsed_at`, `loc_gps_time_ms`, `loc_elapsed_at`) use the wider `2**63-1` cap —
> do not cross the two up (a 2⁶³ value in `boot_count` still 500s).

`Meta: unique_together = ("mark", "number_in_team")`.

### `verified` computation

```python
bids_by_cp = {}   # checkpoint_id -> set of bid
for cp_id, bid in (
    CheckpointTag.objects.filter(checkpoint__race_id=race_id)
    .exclude(bid="")          # drop un-built rows (bid default ""), mirror legend view
    .values_list("checkpoint_id", "bid")
):
    bids_by_cp.setdefault(cp_id, set()).add(bid)

def is_verified(cp_id, cp_code_hex):
    try:
        digest = hashlib.sha256(bytes.fromhex(cp_code_hex)).hexdigest()[:16]
    except ValueError:
        return False           # non-hex cp_code
    return digest in bids_by_cp.get(cp_id, set())
```

Unknown `checkpoint_id`, mismatch, or bad hex → `verified=False`, **row still
stored**.

### View flow (`MarkUploadView(AppAPIView)`)

1. `get_object_or_404(Race, pk=race_id, is_published=True)` → 404.
2. `MarkUploadSerializer(data=request.data).is_valid(raise_exception=True)` → 400
   (all-or-nothing; oversized batch / bad id / malformed all land here).
3. `Team.objects.filter(pk=team_id, category2__race_id=race_id).exists()` else 404.
4. Build `bids_by_cp`; compute `verified` per mark.
5. **De-dup the batch by `id`** (keep last occurrence) before building objects —
   required because `update_conflicts` 500s on an in-batch duplicate conflict key
   (see the idempotency note). Map each mark's nested `location` dict to the 7
   flat `loc_*` fields (and `location is None` → all `loc_*=None`), and each
   mark's `present[]` to `MarkPresent` objects (`mark_id = mark["id"]`).
6. `transaction.atomic()` (parent upsert before child insert — FK ordering):
   - `Mark.objects.bulk_create(mark_objs, update_conflicts=True,
     unique_fields=["id"], update_fields=MARK_UPDATE_FIELDS)` where
     `MARK_UPDATE_FIELDS` = every scalar **except** `id` and `created_at` (so a
     repeat `id` last-write-wins-merges the enriched payload — GPS, grown
     `present` count via `expected_count`/`complete`, recomputed `verified` — and
     a new `id` inserts).
   - `MarkPresent.objects.bulk_create(present_objs, ignore_conflicts=True)` for the
     present rows of **all** marks in the batch (not just new ones); the
     `unique_together("mark", "number_in_team")` makes this additive — missing
     slots inserted, already-stored slots skipped.
7. `return Response({"accepted": [all submitted ids]}, status=200)` — `accepted`
   echoes the **originally submitted** ids (incl. any in-batch duplicate).

`throttle_classes = [ClientIPScopedRateThrottle]`, `throttle_scope =
"mobile-write"`, default `permission_classes = [SignedAppPermission]` (build-HMAC
only — inherit from `AppAPIView`, do **not** add the bearer layer).

### Serializers

- `MarkLocationSerializer` — nested, all `required=False, allow_null=True`;
  `lat`/`lon` `FiniteFloatField` bounded `[-90,90]`/`[-180,180]`; `accuracy`
  `FiniteFloatField(min_value=0)`; `altitude` `FiniteFloatField` **unbounded**;
  `vertical_accuracy` `FiniteFloatField(min_value=0)`; `gps_time_ms`/`elapsed_at`
  `IntegerField(min_value=0, max_value=2**63-1)`.
- `PresentMemberSerializer` — `nfc_uid` (`CharField(max_length=255,
  allow_null=True, required=False)`), `code` (`CharField(max_length=64,
  allow_null=True, required=False)`), `number` (`IntegerField(min_value=0,
  max_value=2147483647)`), `number_in_team` (`IntegerField(min_value=0,
  max_value=2147483647)`).
- `MarkSerializer` — `id` (`CharField(min_length=1, max_length=64)`),
  `checkpoint_id` (`IntegerField(min_value=0, max_value=2147483647)`), `method`
  (`ChoiceField(["nfc", "photo"])` — only the two contract values are accepted;
  any other value is a 400, by decision), `cp_code` (`CharField(max_length=64,
  allow_blank=True)`), `cp_nfc_uid` (`CharField(max_length=255,
  allow_blank=True)`), `expected_count` (`IntegerField(min_value=0,
  max_value=2147483647)`), `complete`
  (`BooleanField`), `trusted_ms` (`IntegerField, allow_null, min_value=0,
  max_value=2**63-1` — BigInt column), `elapsed_at` (same BigInt bounds),
  `boot_count` (`IntegerField, allow_null, min_value=0, max_value=2147483647` —
  **32-bit** column, so the 2³¹−1 cap, **not** 2⁶³−1),
  `wall_ms` (`IntegerField(min_value=0, max_value=2**63-1)`, required — BigInt),
  `present` (`PresentMemberSerializer(many=True, allow_empty=True)`), `location`
  (`MarkLocationSerializer(required=False, allow_null=True)` — `required=False`
  is load-bearing: the client serializes with kotlinx `encodeDefaults=false` and
  `MarkDto.location` defaults to `null`, so a fix-less take **omits** the key
  entirely rather than sending `location: null`).
- `MarkUploadSerializer` — `team_id` (`IntegerField(min_value=1,
  max_value=2147483647)`), `source_install_id`
  (`CharField(max_length=64)`), `marks` (`MarkSerializer(many=True,
  allow_empty=True, max_length=500)`).

### Status codes

| Code | When |
|------|------|
| 200 | accepted (incl. empty batch and full-idempotent re-send) |
| 400 | malformed JSON / missing field / bad id / non-`nfc`/`photo` method / >500 batch |
| 403 | bad signature / time window (POST **not** auto-retried client-side) |
| 404 | race not found/unpublished, or team not in race |
| 429 | throttle (`mobile-write`, 60/min) |

## What Goes Where

- **Implementation Steps** (`[ ]`): models, migration, serializers, view, url,
  tests, docs — all in this repo.
- **Post-Completion** (no checkboxes): deploy + a manual smoke test with the real
  app build; the read-side scoring/dedup/admin-reattribution feature is a separate
  future task.

## Implementation Steps

### Task 1: Add `Mark` + `MarkPresent` models and migration

**Files:**
- Modify: `src/apps/mobile/models.py`
- Create: `src/apps/mobile/migrations/0008_mark_markpresent.py` (via `makemigrations`)
- Modify: `src/apps/mobile/tests.py`

- [x] add `Mark` model after `TrackPoint` with all fields from the Technical
      Details table (client-UUID PK, cross-app `team`/`race` FKs
      `related_name="marks"`, plain `checkpoint_id` IntegerField, `cp_code`/
      `cp_nfc_uid` `blank=True`, 7 flat `loc_*`
      columns, `verified` BooleanField, **no `updated_at`**; the row is
      enrichment-upserted, not immutable — see the idempotency note); add a
      `__str__`. Define `MARK_UPDATE_FIELDS` (all scalars except `id`/`created_at`)
      next to the model for the view's upsert.
- [x] add `MarkPresent` model with `mark` FK (`related_name="present"`),
      nullable `nfc_uid`/`code`, `number`/`number_in_team`, and
      `Meta.unique_together = ("mark", "number_in_team")`.
- [x] generate the migration: `uv run python src/manage.py makemigrations mobile`
      (verify it is `0008`, declares the cross-app FKs into `website`, and a
      dependency on the latest `website` migration as `0007_trackpoint` does).
- [x] write model tests: `Mark` round-trips all fields incl. nulls; `MarkPresent`
      round-trips incl. sentinel (`nfc_uid=None`, `number=0`); `Mark`
      `bulk_create(update_conflicts=True, unique_fields=["id"], update_fields=...)`
      **upserts** a duplicate PK — assert a second create with the same `id` and an
      enriched payload (GPS + `complete=True`) overwrites the scalars while
      `created_at` is preserved; `MarkPresent` `unique_together` rejects a
      duplicate `(mark, number_in_team)` and `ignore_conflicts=True` collapses it.
- [x] run tests — must pass before next task:
      `uv run pytest src/apps/mobile/tests.py -k "mark" --reuse-db`

### Task 2: Add mark-upload serializers

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/tests.py`

- [x] add `MarkLocationSerializer`, `PresentMemberSerializer`, `MarkSerializer`,
      `MarkUploadSerializer` per the Technical Details (reuse the existing
      `FiniteFloatField`; mirror `TrackUploadSerializer` bounds and the `2**63-1`
      int caps; `method` as `ChoiceField(["nfc","photo"])`; `marks` `max_length=500`).
- [x] write serializer tests: valid full batch; omitted nullables resolve absent;
      explicit-null nullables accepted; `location=null` accepted; `present=[]`
      accepted; blank `cp_code`/`cp_nfc_uid` accepted (future photo mark);
      missing required field (`wall_ms`, `checkpoint_id`) → invalid;
      out-of-range `lat`/`lon` → invalid; NaN/inf floats → invalid; bad `method`
      → invalid; empty `id` → invalid; >500 marks → invalid; empty `marks` valid.
- [x] write bound tests (lock 400-not-500): oversized 32-bit ints —
      `checkpoint_id`/`expected_count`/`number`/`number_in_team`/`boot_count`
      > 2147483647 → invalid; a 2³¹..2⁶³ value in the BigInt fields
      (`trusted_ms`/`wall_ms`/`elapsed_at`) **valid**; oversized `nfc_uid` (>255)
      / `code` (>64) in `present` → invalid.
- [x] run tests — must pass before next task:
      `uv run pytest src/apps/mobile/tests.py -k "mark_upload_serializer or mark_serializer" --reuse-db`

### Task 3: Add `MarkUploadView` and wire the URL

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/urls.py`
- Modify: `src/apps/mobile/tests.py`

- [x] add `MarkUploadView(AppAPIView)` mirroring `TrackUploadView`: race 404 →
      serializer 400 → team-in-race 404 → build `bids_by_cp` → compute `verified`
      → **de-dup batch by `id` (keep last)** → **flatten each mark's `location`
      dict to the 7 `loc_*` fields (`location is None` → all `loc_*=None`)** and
      build `MarkPresent` objs from each mark's `present[]` (`mark_id=mark["id"]`)
      → `transaction.atomic()` **upsert** `Mark`
      (`bulk_create(update_conflicts=True, unique_fields=["id"],
      update_fields=MARK_UPDATE_FIELDS)`) then **additively** insert `MarkPresent`
      for all marks (`bulk_create(ignore_conflicts=True)`) →
      `200 {"accepted": [all originally submitted ids]}`. Set `throttle_classes =
      [ClientIPScopedRateThrottle]`, `throttle_scope = "mobile-write"`; store
      `source_install_id` from the validated **body**.
- [x] add `bids_by_cp` helper (module-level function or inline) using
      `CheckpointTag.objects.filter(checkpoint__race_id=race_id).exclude(bid="")
      .values_list("checkpoint_id", "bid")` and the `bytes.fromhex` try/except
      verify logic; skip the query when `marks` is empty.
- [x] add `path("race/<int:race_id>/marks/", MarkUploadView.as_view(),
      name="marks")` to `urls.py` and import the view.
- [x] write view tests (use `_signed_post` at `tests.py:4958`): signed happy-path
      200 with rows created and `accepted` == submitted ids; idempotent re-send
      (counts unchanged, still in `accepted`); **enrichment merge** — first POST
      with `location=null` + partial `present` (e.g. `[1]`), second POST with the
      **same `id`** carrying the GPS fix + grown `present` (e.g. `[1,2]`) +
      `complete=True`: assert the DB row ends with the **enriched** scalars
      (`loc_*` set, `complete=True`), the added present slot, the unchanged
      original slot, and a preserved `created_at` (no duplicate row); `verified`
      matrix (good code→True,
      bad hex→False, unknown cp→False, mismatch→False, row stored in all cases);
      `location=null` + null optional times stored; `present[]` sentinel row
      (`nfc_uid=null`, `number=0`) stored; team-not-in-race → 404; unpublished
      race → 404; bad signature → 403; >500 marks → 400; oversized 32-bit int
      (`checkpoint_id`/`number`/`number_in_team`/`expected_count`) → 400 (not
      500); empty batch → `200 {"accepted": []}`; throttle → 429; duplicate
      `number_in_team` on re-send collapsed by `unique_together`; blank
      `cp_code` (future photo mark) stored with `verified=False`; **in-batch
      duplicate `id`** (same `id` twice in one `marks` array, the second enriched)
      → 200, one stored row with the last occurrence's data, `id` in `accepted`
      (no `CardinalityViolation`/500); invalid `method` value → 400.
- [x] run tests — must pass before next task:
      `uv run pytest src/apps/mobile/tests.py -k "mark" --reuse-db`

### Task 4: Verify acceptance criteria

- [x] verify the response shape matches `scratch/UPLOAD.md`
      (`{"accepted": [...ids...]}`) and the client `MarkUploadResponse`.
- [x] verify every "accept and store" edge case from the contract is covered by a
      test (null fields, unknown cp, bad hex, wrong-team roster accepted, empty
      batch, repeat id), and that a repeat `id` **enriches** rather than dropping
      late data (the GPS/member-adds merge test exists and passes).
- [x] confirm `Mark` is absent from `versioning.py` (no ETag/`sync` impact).
- [x] run full mobile suite: `uv run pytest src/apps/mobile/tests.py --reuse-db` (370 passed)
- [x] run full suite: `uv run pytest` (761 passed)
- [x] `make format && make lint` clean.

### Task 5: Update documentation

**Files:**
- Modify: `src/apps/mobile/README.md`
- Modify: `CLAUDE.md`
- Modify: this plan (move to completed)

- [x] add a "Marks upload" section to `src/apps/mobile/README.md` describing the
      endpoint, models, `verified` semantics, and the **enrichment-merge**
      idempotency (why a repeat `id` upserts rather than no-ops), matching the
      depth of the existing Track upload docs.
- [x] add a "Marks upload" invariant block to `CLAUDE.md` (the `apps.mobile`
      architecture note) matching the style/depth of the existing "Track upload"
      entry: build-HMAC-only, `source_install_id` from body, `verified` rule,
      normalized `Mark`/`MarkPresent`, **enrichment-upsert** (NOT immutable —
      `update_conflicts` for `Mark`, additive `ignore_conflicts` for `MarkPresent`;
      contrast with `TrackPoint`) + out-of-`versioning.py`, accept-and-store edge
      cases.
- [x] move this plan to `docs/plans/completed/`.

## Post-Completion

*Items requiring manual intervention or external systems — informational only.*

**Manual verification**:
- Smoke-test against a real app build: record a take offline, confirm it uploads
  on reconnect and the row appears with the expected `verified` value.
- Confirm the cleartext-LAN local-server target (if used at the event) validates
  the same signed headers, per the contract's LAN note.

**Future / out-of-scope tasks** (separate plans):
- Read-side scoring/dedup (`DISTINCT checkpoint_id` among `verified` takes).
- Admin views for wrong-team detection and device reattribution
  (`source_install_id` → correct team).
- `method="photo"` file upload (separate multipart endpoint).
- Member-chip `code` resolution once bracelets carry codes.
