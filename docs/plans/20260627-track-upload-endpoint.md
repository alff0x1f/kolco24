# GPS Track Upload Ingestion Endpoint (`POST /app/race/<race_id>/track/`)

## Overview
- Add the **backend ingestion endpoint** for GPS tracks that the Android app uploads during a race. This is the
  server counterpart to the completed app-side plan at
  `/Users/alff0x1f/src/kolco24_app_v2/docs/plans/completed/20260627-live-track-upload.md` — the app already builds,
  signs, batches, and retries; the endpoint it POSTs to (`POST /app/race/<race_id>/track/`) does not yet exist.
- The endpoint validates a batch of points, stores them idempotently, and acks the accepted ids. Race organizers
  will later see teams move in near-real-time — but the **organizer-facing map/dashboard that reads tracks back is a
  separate, later task and is explicitly OUT of scope here**. This task is ingestion only: receive, store, ack.
- Integrates into the existing `apps.mobile` signed-endpoint stack: it reuses `AppAPIView` (build-HMAC gate +
  best-effort install stats), the `ClientIPScopedRateThrottle`, and the established serializer/view/urls layout.

## Context (from discovery)
- **Files/components involved:**
  - `src/apps/mobile/models.py` — add `TrackPoint` (alongside `AppInstall`/`MobileToken`).
  - `src/apps/mobile/migrations/0007_trackpoint.py` — new migration (latest is `0006_mobiletoken`).
  - `src/apps/mobile/serializers.py` — add `TrackPointSerializer` + `TrackUploadSerializer`.
  - `src/apps/mobile/views.py` — add `TrackUploadView(AppAPIView)` (mirror `TagCreateView` structure).
  - `src/apps/mobile/urls.py` — add `path("race/<int:race_id>/track/", TrackUploadView.as_view(), name="track")`.
  - `src/apps/mobile/tests.py` — add the test block (reuse `_signed_post` / `_signed_headers`, replicate the
    `_clear_throttle_cache` autouse fixture context).
  - `src/apps/mobile/README.md` + `CLAUDE.md` — docs (final task).
- **Related patterns found:**
  - `AppAPIView` (`views.py:53`): `authentication_classes = []`, `permission_classes = [SignedAppPermission]`,
    records install stats in `initial()` after permissions pass. Inheriting it is all that's needed for the
    build-HMAC-only gate — **do not** add `IsMobileUser`.
  - `TagCreateView` (`views.py:206`): the closest sibling — a signed POST that resolves `race_id` from the URL,
    validates a body serializer, writes via the ORM, and returns a JSON ack. Mirror its shape (minus the per-person
    permission layer and minus the crypto-signal handling).
  - `request.app_meta["install_id"]` is set by `SignedAppPermission` (`permissions.py:139-140`, truncated to 64
    chars) **before** the view runs — the source for the `install_id` column, zero app change.
  - `ClientIPScopedRateThrottle` (`throttling.py`) + `throttle_scope = "mobile-write"` (60/min) is the existing
    write bucket used by `TagCreateView`; reuse it.
  - Test helpers: `_signed_post(client, path, secret, body_bytes)` (`tests.py:4958`) and
    `_signed_headers(method, path, secret, body=b"", key_id="test-v1")` (`tests.py:497`, hardcodes
    `HTTP_X_INSTALL_ID="install-abc"`). `SECRET` + `settings.MOBILE_APP_KEYS = {"test-v1": SECRET}` is the per-test
    setup. The `_clear_throttle_cache` autouse fixture (`tests.py:16`) calls `cache.clear()` around every test.
- **Dependencies identified:**
  - `website.models.models.Team`, `website.models.race.Race` — FK targets; `Team` relates to `Race` via
    `category2__race_id` (used in the team-in-race check, same join `TeamsView` uses).
  - DRF (`rest_framework`) — serializers, `Response`, `status`, `APIView` (via `AppAPIView`).
  - No new third-party deps. No `cryptography`/signals/`versioning.py` involvement (write-only, immutable rows).

## Development Approach
- **Testing approach:** Regular (code first, then tests) — each task writes its own tests before the next task.
- Complete each task fully before moving on; small focused changes.
- **Every code task includes tests.** Model → migration sanity + field round-trip via the endpoint; serializer →
  validation cases; view → the full request matrix (auth, happy path, idempotency, install_id, 404s, 400).
- All tests must pass before the next task. Maintain backward compatibility (no changes to existing endpoints).
- Run `make format && make lint` before considering the work done (repo convention).

## Testing Strategy
- **Unit tests:** pytest-style functions in `src/apps/mobile/tests.py` (the repo convention — `@pytest.mark.django_db`,
  `client`/`settings`/`django_user_model` fixtures, **not** `TestCase` subclasses).
  - Throttled endpoint → the test module's `_clear_throttle_cache` autouse fixture already covers it (all mobile
    tests share one client IP); no new fixture needed, just keep the new tests in `tests.py`.
  - Use `_signed_post` for valid signed POSTs. For the **install_id stamping** test, the default helper hardcodes
    `HTTP_X_INSTALL_ID="install-abc"` — vary it by passing an override (extend the test to build headers with a
    custom `HTTP_X_INSTALL_ID`, e.g. a thin local helper or by merging `{"HTTP_X_INSTALL_ID": ...}` into
    `_signed_headers(...)` output before `client.post`).
- **e2e tests:** none — this project has no UI e2e harness; the endpoint is server-only.

## Progress Tracking
- Mark completed items `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.
- Keep this file in sync if scope changes.

## Solution Overview
- **Model:** one `TrackPoint` row per GPS fix, **PK = the client-generated UUID** (`id`). Because the idempotency
  key *is* the primary key, upsert collapses to `bulk_create(objs, ignore_conflicts=True)` — a re-sent point hits a
  PK conflict and is silently skipped (idempotent success), no `update_or_create` loop, no extra unique index.
- **Device attribution:** store `install_id` (from `request.app_meta["install_id"]`, the `X-Install-Id` header)
  so a team recording from two phones is separable — `segment_id` (a random UUID in production) distinguishes
  *recording sessions*, `install_id` groups a phone's many sessions into "phone A" vs "phone B". Zero app change.
- **`accepted` = every submitted id.** With `ignore_conflicts=True`, a point is either newly inserted or already
  present — both are success from the client's retry loop, which only wants confirmation it can stop resending. Ack
  the whole batch (not just freshly-inserted rows).
- **Validation is all-or-nothing (400 on a bad batch), not partial-accept.** The app *supports* partial accept, but
  the server doesn't need to exercise it — a malformed/out-of-range point is a client bug that should surface loudly
  as a 400, not get half-swallowed. Keeps the serializer a plain nested serializer with field bounds.
- **Auth:** build HMAC only (`AppAPIView` default `[SignedAppPermission]`), exactly like the read endpoints. Track
  recording runs on participants' phones (not admins) — no bearer token. Same trust boundary as the reads: a genuine
  build can post a track for any `team_id` in the race; `team_id`/`install_id` are spoofable, accepted as-is.
- **No versioning/ETag:** rows are immutable and the endpoint is write-only, so `TrackPoint` stays out of
  `versioning.py`, has **no `updated_at`**, and never touches the fingerprint/`sync` machinery.

## Technical Details
- **Model** (`apps.mobile`, FKs cross into `website` like `apps.race` does):
  ```python
  class TrackPoint(models.Model):
      id = models.CharField(max_length=64, primary_key=True)        # client UUID = idempotency key = PK
      team = models.ForeignKey("website.Team", on_delete=models.CASCADE, related_name="track_points")
      race = models.ForeignKey("website.Race", on_delete=models.CASCADE, related_name="track_points")
      install_id = models.CharField(max_length=64)                  # device attribution (X-Install-Id)
      segment_id = models.CharField(max_length=64)                  # recording session (random UUID)
      lat = models.FloatField()
      lon = models.FloatField()
      accuracy = models.FloatField()
      altitude = models.FloatField(null=True)
      vertical_accuracy = models.FloatField(null=True)
      gps_time_ms = models.BigIntegerField()
      trusted_ms = models.BigIntegerField(null=True)
      elapsed_at = models.BigIntegerField()
      boot_count = models.IntegerField(null=True)
      created_at = models.DateTimeField(auto_now_add=True)          # server receive time
  ```
  **No secondary index.** Ingestion runs zero reads on `TrackPoint` — the idempotent
  upsert relies on the automatic primary-key index, nothing else. A composite like
  `(race, team, install_id, segment_id)` would serve only the future organizer-map
  read, and its correct shape (likely a trailing time column — `trusted_ms` /
  `gps_time_ms` / `elapsed_at` — for chronological segment ordering) can't be known
  until that query exists. **Defer index design to the read-endpoint task.**
- **Serializers** (`serializers.py`):
  ```python
  class TrackPointSerializer(serializers.Serializer):
      # id / segment_id are opaque client strings (the Kotlin DTO type is `String`,
      # not UUID — production mints UUIDs but the server must not police the client
      # id format; `id` is just the PK). min_length=1 only guards against an empty PK.
      id = serializers.CharField(max_length=64, min_length=1)
      segment_id = serializers.CharField(max_length=64, min_length=1)
      lat = serializers.FloatField(min_value=-90, max_value=90)
      lon = serializers.FloatField(min_value=-180, max_value=180)
      # GPS magnitudes are physically non-negative (meters / epoch-ms / monotonic-ms /
      # boot counter); min_value=0 honours the plan's "400 on out-of-range" promise.
      accuracy = serializers.FloatField(min_value=0)
      altitude = serializers.FloatField(required=False, allow_null=True)  # may be negative (below sea level)
      vertical_accuracy = serializers.FloatField(required=False, allow_null=True, min_value=0)
      gps_time_ms = serializers.IntegerField(min_value=0)
      trusted_ms = serializers.IntegerField(required=False, allow_null=True, min_value=0)
      elapsed_at = serializers.IntegerField(min_value=0)
      boot_count = serializers.IntegerField(required=False, allow_null=True, min_value=0)

  class TrackUploadSerializer(serializers.Serializer):
      team_id = serializers.IntegerField()
      # max_length=500 matches the app's batch cap; an oversized batch is a 400
      # (all-or-nothing), bounding memory + the single bulk_create insert.
      points = TrackPointSerializer(many=True, allow_empty=True, max_length=500)  # empty -> acks []
  ```
- **View** (`views.py`):
  ```python
  class TrackUploadView(AppAPIView):
      throttle_classes = [ClientIPScopedRateThrottle]
      throttle_scope = "mobile-write"

      def post(self, request, race_id):
          get_object_or_404(Race, pk=race_id, is_published=True)
          serializer = TrackUploadSerializer(data=request.data)
          serializer.is_valid(raise_exception=True)                 # 400 on malformed/out-of-range
          team_id = serializer.validated_data["team_id"]
          points = serializer.validated_data["points"]
          if not Team.objects.filter(pk=team_id, category2__race_id=race_id).exists():
              return Response({"detail": "Команда не найдена в этой гонке"},
                              status=status.HTTP_404_NOT_FOUND)
          install_id = request.app_meta["install_id"]
          objs = [TrackPoint(id=p["id"], team_id=team_id, race_id=race_id,
                             install_id=install_id, segment_id=p["segment_id"], lat=p["lat"],
                             lon=p["lon"], accuracy=p["accuracy"], altitude=p.get("altitude"),
                             vertical_accuracy=p.get("vertical_accuracy"), gps_time_ms=p["gps_time_ms"],
                             trusted_ms=p.get("trusted_ms"), elapsed_at=p["elapsed_at"],
                             boot_count=p.get("boot_count"))
                  for p in points]
          TrackPoint.objects.bulk_create(objs, ignore_conflicts=True)
          return Response({"accepted": [p["id"] for p in points]}, status=status.HTTP_200_OK)
  ```
  Notes: `install_id = request.app_meta["install_id"]` is safe — `SignedAppPermission` always sets `app_meta` before
  the view runs on a verified request. Empty `points` → `bulk_create([])` is a harmless no-op, acks `[]`.
- **Wire contract (must match the app exactly):** body `{team_id: int, points: [...]}`, up to 500 points/batch;
  point fields `id, segment_id, lat, lon, accuracy, altitude?, vertical_accuracy?, gps_time_ms, trusted_ms?,
  elapsed_at, boot_count?`; response `{"accepted": [id, ...]}`.

## What Goes Where
- **Implementation Steps** (`[ ]`): the model + migration, serializers, view, url wiring, tests, and docs — all in
  this repo.
- **Post-Completion** (no checkboxes): deploying so the live endpoint exists (the real gate on the app's uploads
  landing), and on-device end-to-end verification against a deployed build.

## Implementation Steps

### Task 1: Add the `TrackPoint` model + migration

**Files:**
- Modify: `src/apps/mobile/models.py`
- Create: `src/apps/mobile/migrations/0007_trackpoint.py`
- Modify: `src/apps/mobile/tests.py`

- [x] add the `TrackPoint` model to `models.py` exactly as in **Technical Details** (PK = client `id`, FKs to
      `website.Team`/`website.Race`, `install_id`, `segment_id`, the GPS fields with the documented nullability,
      `created_at` `auto_now_add`, **no secondary index** — see Technical Details). Add a class docstring noting: PK
      is the client UUID (idempotency key), it is **write-only / immutable** with **no `updated_at`** and deliberately
      **not** in `versioning.py`.
- [x] generate the migration: `uv run python src/manage.py makemigrations mobile` → verify it is named/renamed
      `0007_trackpoint.py`. `makemigrations` **auto-adds** the dependencies (the latest `mobile` migration
      `0006_mobiletoken` and the `website` migration for the `Team`/`Race` FK targets) — just verify they're present,
      do **not** hand-write a specific `website` migration number (unlike `apps.race/0001` which pinned `0072` only
      because it was that app's first migration)
- [x] write a test: applying migrations + `TrackPoint.objects.create(...)` round-trips all fields (incl. null
      `altitude`/`vertical_accuracy`/`trusted_ms`/`boot_count`) and the PK accepts a UUID string
- [x] write a test for the behavior the endpoint relies on: a second `bulk_create([...], ignore_conflicts=True)`
      with an already-stored `id` **silently no-ops** (row count unchanged, original row's fields untouched). Keep
      this distinct from a plain `TrackPoint.objects.create(id=same)`, which **raises `IntegrityError`** (assert that
      separately if you cover it) — do not conflate the two in one test
- [x] run `uv run pytest src/apps/mobile/tests.py` — must pass before next task

### Task 2: Add the request serializers

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] add `TrackPointSerializer` (nested, with `lat` bounds `-90..90`, `lon` bounds `-180..180`, `id`/`segment_id`
      `min_length=1`, the non-negative `min_value=0` bounds on `accuracy`/`vertical_accuracy`/`gps_time_ms`/
      `trusted_ms`/`elapsed_at`/`boot_count` (**not** `altitude` — it may be negative), nullable fields
      `required=False, allow_null=True`) and `TrackUploadSerializer` (`team_id` IntegerField,
      `points = TrackPointSerializer(many=True, allow_empty=True, max_length=500)`) — exactly as in
      **Technical Details**
- [ ] write tests: a valid batch validates and `validated_data` carries the parsed points (incl. omitted nullable
      fields resolving to absent/`None`)
- [ ] write tests: missing required field (e.g. no `lat`) → invalid; out-of-range `lat`/`lon` → invalid; a
      **negative** `accuracy`/`gps_time_ms`/`elapsed_at`/`trusted_ms`/`boot_count`/`vertical_accuracy` → invalid (the
      `min_value=0` bounds); empty-string `id`/`segment_id` → invalid (`min_length=1`); a **negative `altitude`** →
      **valid** (altitude is unbounded); empty `points` list → **valid** (acks `[]`); a `points` list **over 500** →
      invalid (the `max_length` cap)
- [ ] run `uv run pytest src/apps/mobile/tests.py` — must pass before next task

### Task 3: Add `TrackUploadView` + URL wiring

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/urls.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] add `TrackUploadView(AppAPIView)` to `views.py` (inherit the default `[SignedAppPermission]` — **do not** add
      `IsMobileUser`; `throttle_classes = [ClientIPScopedRateThrottle]`, `throttle_scope = "mobile-write"`),
      implementing the `post(self, request, race_id)` flow from **Technical Details**. Add a docstring noting it is
      the **third POST** but **build-HMAC-only** (not part of the per-person write layer), and that `install_id`
      comes from `request.app_meta`, `accepted` = all submitted ids, validation is all-or-nothing.
- [ ] import `TrackPoint`, `TrackUploadSerializer`, `Team`, `Race` as needed (mirror existing import style in
      `views.py`)
- [ ] wire `urls.py`: import `TrackUploadView`, add
      `path("race/<int:race_id>/track/", TrackUploadView.as_view(), name="track")`
- [ ] write test: **403 without a valid HMAC signature** (e.g. wrong secret / tampered sig) — build-level gate holds
- [ ] write test: **happy path** → 200 `{"accepted": [all submitted ids]}` and rows persisted with correct field
      values (assert a couple of fields incl. `race_id`/`team_id`/`install_id` from the signed header)
- [ ] write test: **idempotency** — POST the same batch twice → both 200, same `accepted`, no duplicate rows
- [ ] write test: **install_id stamping** — two signed POSTs with **different** `HTTP_X_INSTALL_ID` → rows carry the
      respective `install_id`. This test **bypasses `_signed_post`** (which exposes no extra-header arg): call
      `_signed_headers(...)`, merge a custom `HTTP_X_INSTALL_ID`, and `client.post` directly. Safe because
      `install_id` is **outside** the signed canonical (`build_canonical` folds only method + path + ts + body), so
      overriding it post-signing does **not** invalidate the signature — no re-signing needed
- [ ] write test: **team not in race** → 404; **unpublished race** → 404; **nonexistent race** → 404
- [ ] write test: **malformed / out-of-range point** (bad `lat`, negative `accuracy`, missing field) → 400, and an
      **over-500-point batch** → 400 (end-to-end through the endpoint, not just the serializer)
- [ ] write test: **nullable round-trip** — a point with `altitude`/`trusted_ms`/`boot_count` omitted or `null`
      stores `NULL` and still 200s
- [ ] run `uv run pytest src/apps/mobile/tests.py` — must pass before next task

### Task 4: Verify acceptance criteria
- [ ] confirm the wire contract matches the app exactly (path, body keys, point field names/nullability, response
      `{"accepted": [...]}`) against `kolco24_app_v2` `TrackDtos.kt` / `ApiClient.kt`
- [ ] confirm auth is build-HMAC-only (no bearer required) and the throttle scope is `mobile-write`
- [ ] run the full mobile suite: `uv run pytest src/apps/mobile/tests.py`
- [ ] run the full project suite: `uv run pytest`
- [ ] run `make format && make lint` — must be clean

### Task 5: [Final] Update documentation
- [ ] add a subsection to `src/apps/mobile/README.md` documenting `POST /app/race/<id>/track/` (build-HMAC-only,
      body/response contract, `TrackPoint` model incl. client-id-as-PK idempotency + `install_id` device
      attribution, `accepted`=all-ids, all-or-nothing validation, no versioning/ETag)
- [ ] add a one-line bullet to the `CLAUDE.md` `apps.mobile` section noting the **third POST** (`track`) alongside
      `login` + tag-create, but **build-HMAC-only** — NOT part of the per-person write layer; immutable rows, no
      `updated_at`/versioning. Also note `apps.mobile` now has a **write model (`TrackPoint`) with FKs into
      `website`** — the existing "self-contained / touches neither `/api/` nor `website`" wording describes the
      *read* path; the track write is the documented exception (cross-app FKs like `apps.race`)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only*

**External system updates:**
- **Deploy:** the endpoint must be deployed for the app's already-shipped uploads to land. Until then the app
  change is inert-but-safe (points stay pending client-side and back-fill once the endpoint is live) — this is the
  real gate on organizers eventually *seeing* live data (which is itself a separate, later task).

**Manual verification:**
- Against a deployed build: start a recording on-device, confirm the first batch POSTs (200, `accepted` echoes the
  ids) and subsequent batches arrive ~10 min apart; confirm rows appear with the correct `team`/`race`/`install_id`.
- Two phones for one team: confirm rows are separable by `install_id` (and by `segment_id` within each phone).
- Offline → online: airplane-mode mid-race, confirm points accumulate client-side and back-fill (in 500-point
  batches) with no duplicates once coverage returns (idempotency holds end-to-end).
