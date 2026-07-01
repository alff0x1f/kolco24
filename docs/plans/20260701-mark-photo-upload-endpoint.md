# Mark Photo-Frame Upload Endpoint

## Overview

Implement the one remaining endpoint from the app upload contract
(`kolco24_app_v2/docs/design/UPLOAD.md`):

```
POST /app/race/<race_id>/mark/<mark_id>/photo/<frame_id>
```

It receives **one raw JPEG frame** (binary body, not JSON) for a photo-mark,
stores it idempotently keyed by `(race_id, mark_id, frame_id)`, and returns
`201` on a new frame / `200` on an idempotent re-send. The client posts one
request per frame; a mark of `method="photo"` may have several frames.

The sibling batch endpoints (`POST .../marks/` and `POST .../track/`) already
exist — this plan mirrors their `AppAPIView` + `SignedAppPermission` (build-HMAC)
trust boundary. Only the write path is in scope; the organizer-facing read/render
side is a separate future task.

## Context (from discovery)

- **Project**: Django 4.2 + DRF. Mobile endpoints live in `src/apps/mobile/`
  (`label = "mobile"`, mounted at `/app/*`). Tests are pytest-style with
  `@pytest.mark.django_db`.
- **Files/components involved**:
  - `src/apps/mobile/models.py` — `Mark` (line 132, the parent), `TrackPoint`
    (line 86, the immutable-model precedent).
  - `src/apps/mobile/views.py` — `AppAPIView` (line 64), `TrackUploadView`
    (line 329), `MarkUploadView` (line 431).
  - `src/apps/mobile/permissions.py` — `SignedAppPermission` (line 75); reads
    `request.body` at line 134 (so HMAC over raw bytes already works).
  - `src/apps/mobile/urls.py`, `src/apps/mobile/throttling.py`
    (`ClientIPScopedRateThrottle`), `src/apps/mobile/tests.py`.
  - `src/config/settings.py` — `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`
    (line 198), no `DATA_UPLOAD_MAX_MEMORY_SIZE` set yet (Django default 2.5 MB),
    `MEDIA_ROOT`/`MEDIA_URL` (lines 252–253).
- **Related patterns found**:
  - `website/models/news.py:107` — existing `ImageField(upload_to=...)`
    precedent for filesystem media.
  - `docker-compose_v2.yml:40` — `/app/media` is a persisted volume;
    `deploy/nginx.conf` serves `/media/` and caps bodies at `client_max_body_size
    50m`.
  - Test helpers already present: `_signed_post(client, path, secret,
    body_bytes, key_id="test-v1")` (tests.py:4958, signs the **raw body bytes**),
    autouse `_clear_throttle_cache` fixture (tests.py:16).
- **Dependencies identified**: no new packages. `django.core.files.base.ContentFile`
  for writing bytes to a `FileField`.

## Development Approach

- **Testing approach**: Regular (code first, then tests) — chosen for this plan.
- Complete each task fully before moving to the next.
- Make small, focused changes; follow the existing `AppAPIView` conventions.
- **Every task includes new/updated tests** — success and error scenarios.
- **All tests must pass before starting the next task.**
- Run `make format && make lint` before finishing (project convention).
- Maintain backward compatibility — this is purely additive (new model, new URL,
  one global settings relaxation).

## Testing Strategy

- **Unit/integration tests**: pytest-style in `src/apps/mobile/tests.py`,
  reusing the autouse `_clear_throttle_cache` fixture (mandatory — the new
  throttled endpoint shares the single test-client IP, so counts bleed across
  tests otherwise).
- **A new `image/jpeg` signed-POST helper is required.** The existing
  `_signed_post` (tests.py:4958) hardcodes `content_type="application/json"`
  (line 4962), so it would **not** exercise the binary path this endpoint exists
  for — a test posting JPEG bytes as `application/json` could pass even if the
  view wrongly read `request.data` (that would ParseError-400, not the 415 the
  gotcha describes). Add `_signed_photo_post(...)` (or a `content_type` param on
  `_signed_post`) that sends `Content-Type: image/jpeg` and signs the raw bytes,
  and use it in **every** photo test so gotcha #1 is genuinely covered.
- Use `tmp_path` / `override_settings(MEDIA_ROOT=...)` so tests don't write into
  the repo `media/` dir.
- **No e2e/UI tests** — this is a backend-only API endpoint; the project has no
  browser e2e suite for `/app/*`.
- Run the mobile suite with `uv run pytest src/apps/mobile/tests.py` and the full
  suite with `uv run pytest` at the end.

## Progress Tracking

- Mark completed items `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix.
- Document blockers with ⚠️ prefix.
- Keep this plan in sync with actual work.

## Solution Overview

- **New model `MarkPhoto`** (child of `Mark`) records one row per stored frame;
  the JPEG bytes live on the filesystem via a `FileField` at a deterministic,
  unguessable UUID path `mark_photos/<mark_id>/<frame_id>.jpg` on the persisted
  `/app/media` volume, served by nginx at `/media/`.
- **New view `MarkPhotoUploadView`** extends `AppAPIView` (build-HMAC-only, no
  per-person bearer — same trust boundary as `/marks/` and `/track/`). It reads
  `request.body` directly (binary), validates minimally (non-empty + size cap),
  and upserts idempotently by `(mark, frame_id)`.
- **Idempotency**: a `unique_together("mark", "frame_id")` guard plus a
  pre-check; a concurrent duplicate is caught via `IntegrityError` → `200`.
- **Immutable model**: like `TrackPoint`, `MarkPhoto` has no `updated_at` and is
  deliberately absent from `versioning.py` (nothing reads a version off it).

## Technical Details

### Model (`src/apps/mobile/models.py`)

```python
def _mark_photo_path(instance, filename):
    # Deterministic, unguessable (two client UUIDs). filename arg ignored.
    return f"mark_photos/{instance.mark_id}/{instance.frame_id}.jpg"


class MarkPhoto(models.Model):
    mark = models.ForeignKey(Mark, on_delete=models.CASCADE, related_name="photos")
    frame_id = models.CharField(max_length=64)
    image = models.FileField(upload_to=_mark_photo_path)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("mark", "frame_id")

    def __str__(self):
        return f"MarkPhoto(mark={self.mark_id} frame={self.frame_id})"
```

- No `race` field: `mark.race_id` carries it and the URL `race_id` is validated
  against the mark. `(mark, frame_id)` is equivalent to the contract's
  `(race, mark, frame_id)` because `mark_id` is a globally-unique client UUID.

### View (`src/apps/mobile/views.py`)

```python
PHOTO_MAX_BYTES = 10 * 1024 * 1024  # 10 MB app-level cap (nginx gates at 50m)


class MarkPhotoUploadView(AppAPIView):
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = "mobile-photo"

    # frame_id is interpolated into a filesystem path; it must be a safe stem.
    FRAME_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

    def post(self, request, race_id, mark_id, frame_id):
        get_object_or_404(Race, pk=race_id, is_published=True)          # 404
        mark = get_object_or_404(Mark, pk=mark_id, race_id=race_id)     # 404
        if not self.FRAME_ID_RE.match(frame_id):
            return Response({"detail": "bad frame_id"}, status=400)

        body = request.body  # raw JPEG — never touch request.data (415 on image/jpeg)
        if not body:
            return Response({"detail": "empty body"}, status=400)
        if len(body) > PHOTO_MAX_BYTES:
            return Response({"detail": "too large"}, status=413)

        if MarkPhoto.objects.filter(mark_id=mark_id, frame_id=frame_id).exists():
            return Response(status=200)  # idempotent — already stored

        photo = MarkPhoto(mark=mark, frame_id=frame_id)
        canonical = _mark_photo_path(photo, "")  # mark_photos/<mark_id>/<frame_id>.jpg
        # Reuse the deterministic name even if a crashed prior attempt left an
        # orphan canonical file (see below) — delete it so storage doesn't suffix
        # a new "_XXXX" copy. The bytes for a given frame_id are immutable, so an
        # overwrite is content-identical.
        if photo.image.storage.exists(canonical):
            photo.image.storage.delete(canonical)
        photo.image.save(f"{frame_id}.jpg", ContentFile(body), save=False)
        try:
            photo.save()
        except IntegrityError:
            # Concurrent duplicate: both passed the exists() pre-check, we lost
            # the unique_together race. Delete the file we just wrote so a rare
            # race doesn't leak storage, then ack idempotently.
            photo.image.delete(save=False)
            return Response(status=200)
        return Response(status=201)
```

**`frame_id` charset + routing (defense-in-depth).** `frame_id` is a client-supplied
string interpolated directly into `_mark_photo_path`, so validate it against
`^[A-Za-z0-9_-]{1,64}$` and return a clean `400` **before** touching storage. Note
the split with URL routing: the `<str:frame_id>` converter is `[^/]+`, so a
`frame_id` that is **empty or contains `/`** (e.g. `../x`) never routes to the view
at all — Django returns a **URL-level `404`**. The view guard therefore only fires
for a **single path segment with disallowed characters** (e.g. `a.b`, `a.jpg`,
`a~b`). Both outcomes are safe (nothing hostile reaches the filesystem path); the
tests must expect `404` for the unrouteable cases and `400` for the bad-charset
single-segment case. Keep `<str:frame_id>` (the contract `frame_id` is a slash-free
UUID); do **not** switch to `<path:frame_id>` — that would let slashes into the
storage path, which is exactly what the guard exists to prevent. `mark_id` needs no
guard — `get_object_or_404(Mark, pk=mark_id)` runs first, so a hostile `mark_id`
matches no row → `404` before any path is built.

**Orphan canonical file (crash window).** The file is written by `image.save()`
*before* the row is committed by `photo.save()`. If the process dies in that narrow
window, a canonical file `mark_photos/<mark_id>/<frame_id>.jpg` exists with no
`MarkPhoto` row. A retry sees no row (idempotency pre-check misses) and would, by
default, get a `get_available_name`-suffixed `<frame_id>_XXXX.jpg` — breaking the
deterministic-path guarantee. The `storage.exists() → storage.delete()` step above
reuses the canonical name on retry, so the deterministic path holds. (The residual
window — crash *between* delete and re-save — is self-correcting on the next retry
and is an accepted limitation.)

### Critical gotchas (must be honored)

1. **Read `request.body`, never `request.data`.** DRF's default parsers don't
   handle `image/jpeg`; touching `.data` raises `415`. `SignedAppPermission`
   already reads `request.body` (Django buffers it), so the HMAC signature over
   raw bytes works unchanged.
2. **`DATA_UPLOAD_MAX_MEMORY_SIZE` must be raised (global).** Its Django default
   is 2.5 MB and it is enforced the moment `request.body` is first read — which
   happens **inside `SignedAppPermission`, before the view**. A JPEG larger than
   2.5 MB would raise `RequestDataTooBig` (opaque `400`) before the explicit
   `413` ever runs. Set `DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024` (just
   above the 10 MB app cap). Tradeoff: it is global — any non-multipart POST may
   now buffer up to 12 MB in memory — but nginx already gates all bodies at 50 m,
   so the effective exposure is unchanged.
3. **URL pattern has NO trailing slash.** The contract path ends at `<frame_id>`
   (UPLOAD.md), and the signed canonical string is the `full_path`, so the route
   must match the client byte-for-byte. This deliberately diverges from the other
   `/app/` endpoints' trailing-slash convention — leave a comment saying so.
   `path("race/<int:race_id>/mark/<str:mark_id>/photo/<str:frame_id>",
   MarkPhotoUploadView.as_view(), name="photo")`.
4. **JSON-only renderer set (`Accept` header).** `REST_FRAMEWORK` sets
   `DEFAULT_RENDERER_CLASSES = [JSONRenderer]` (settings.py:190). DRF runs
   *response* content negotiation before the view; if the client sends
   `Accept: image/jpeg` on the upload, `DefaultContentNegotiation` finds no
   matching renderer and raises `406` before the view runs. Our responses carry
   no body (bare status), so this only bites on the client's `Accept` header —
   confirm the Android client sends no `Accept` (or `*/*`) and pin it with a test
   using the client's real headers. (This is the response-renderer analogue of
   gotcha #1, which is about the request *parser* / `request.data`.)

New imports for the view module: `re`, `django.core.files.base.ContentFile`,
`django.db.IntegrityError`.

### Parent-mark ordering (the `404` is contract-safe)

Returning `404` when the parent `Mark` has not arrived is only safe because the
client contract guarantees both halves:

- **metadata-first ordering** — the client starts draining a mark's frames only
  after that mark's `uploaded*` flag for the target is already `1`
  (UPLOAD.md §photo, "драйн кадров… стартует только когда uploadedLocal/
  uploadedCloud… уже 1"), so a frame should not normally race ahead of its
  `/marks/` batch;
- **`404` self-heal** — even if it does (or the endpoint is briefly down), the
  client treats a photo `404` as transient: `photosUploaded*` stays `0` and it
  **retries on the next upload** (UPLOAD.md §"Коды ответов (эндпоинт кадра)":
  `404` → "self-heal… повтор на следующей выгрузке").

So the server side needs no queue/hold for out-of-order frames — a plain `404`
is correct and the client re-drives it. Cite this in the view docstring so a
future reader doesn't "fix" the 404 into a speculative insert.

### Deliberate non-goals

- Endpoint does **not** check `mark.method == "photo"`; it accepts a frame for
  any existing mark (accept-and-store philosophy, same as `/marks/`/`/track/`).
- No JPEG magic-byte check, no Pillow re-encode.
- No auth-gated read view for retrieving frames (read/scoring side is future).
- No server-side hold/queue for a frame that arrives before its parent mark —
  the client's metadata-first ordering + `404` self-heal cover it (see above).

## What Goes Where

- **Implementation Steps** (checkboxes): model + migration, settings, view, URL
  wiring, admin registration, tests — all inside this repo.
- **Post-Completion** (no checkboxes): deploy note about the persisted media
  volume and the `mobile-photo` throttle rate; the app client already implements
  the caller (self-heals once the endpoint is live).

## Implementation Steps

### Task 1: Add the `MarkPhoto` model + migration

**Files:**
- Modify: `src/apps/mobile/models.py`
- Create: `src/apps/mobile/migrations/00NN_markphoto.py` (via makemigrations)

- [x] add `_mark_photo_path(instance, filename)` helper to `models.py`
- [x] add `MarkPhoto` model (FK `mark`→`Mark` CASCADE `related_name="photos"`,
      `frame_id` CharField(64), `image` FileField(upload_to=`_mark_photo_path`),
      `created_at` auto_now_add, `unique_together("mark", "frame_id")`, `__str__`)
- [x] add a docstring noting it is immutable (no `updated_at`, not in
      `versioning.py`), mirroring the `TrackPoint` note
- [x] generate the migration: `uv run python src/manage.py makemigrations mobile`
- [x] write a test that `MarkPhoto` persists and enforces the
      `unique_together("mark", "frame_id")` constraint (IntegrityError on dup)
- [x] run migration check + tests:
      `uv run python src/manage.py makemigrations --check --dry-run` and
      `uv run pytest src/apps/mobile/tests.py -k markphoto` — must pass

### Task 2: Settings — throttle scope + upload-size limit

**Files:**
- Modify: `src/config/settings.py`

- [x] add `"mobile-photo": "120/min"` to
      `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`
- [x] add `DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024` with a comment
      explaining it must exceed the app-level photo cap because
      `SignedAppPermission` reads `request.body` before the view (see gotcha 2)
- [x] confirm `MEDIA_ROOT`/`MEDIA_URL` already exist (they do, lines 252–253) —
      no change needed
- [x] write/extend a settings-level assertion test (or a focused endpoint test in
      Task 4) proving a >2.5 MB but <12 MB body is accepted rather than 400'd by
      `RequestDataTooBig` — added settings-level assertions
      (`test_data_upload_max_memory_size_raised_above_photo_cap`,
      `test_mobile_photo_throttle_rate_configured`); the full end-to-end proof
      through the permission layer is Task 4's dedicated checkbox once the view
      exists
- [x] run tests — must pass before next task

### Task 3: Add `MarkPhotoUploadView` + URL wiring

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/urls.py`

- [x] add `PHOTO_MAX_BYTES = 10 * 1024 * 1024` and `FRAME_ID_RE` and the `re` /
      `ContentFile` / `IntegrityError` imports to `views.py`
- [x] implement `MarkPhotoUploadView(AppAPIView)` per the Technical Details flow
      (Race 404 → Mark 404 → **frame_id charset 400** → empty 400 → oversized 413
      → idempotent 200 → save 201 / IntegrityError 200 with orphan-file cleanup),
      with `throttle_scope = "mobile-photo"` and a docstring cross-referencing
      gotchas 1, 3 & 4
- [x] add the URL route **without** a trailing slash, with an inline comment that
      the missing slash is intentional (byte-for-byte signed `full_path`)
- [x] **skip `admin.py`** — the data-model siblings `Mark`/`TrackPoint`/
      `MarkPresent` are deliberately not admin-registered (only the stats models
      `AppInstall`/`AppAuthFailure` are), so registering `MarkPhoto` would break
      convention, not follow it
- [x] add a `_signed_photo_post(...)` test helper sending `Content-Type:
      image/jpeg` over signed raw bytes (see Testing Strategy)
- [x] write tests for the happy path (`201`, file exists on disk under
      `mark_photos/<mark_id>/`, row created) and idempotent re-send (`200`, no
      duplicate row/file)
- [x] write tests for error/edge cases: unpublished race → 404, unknown race →
      404, mark-not-arrived → 404, mark-belongs-to-other-race → 404, empty body →
      400, bad/missing signature → 403, throttle over `mobile-photo` rate → 429
- [x] write **`frame_id` tests split by layer**: bad-charset single segment
      (`a.b`, `a.jpg`) → **view 400**; unrouteable (`../x`, trailing-empty) →
      **URL-level 404** (the `<str>` converter never reaches the view — do not
      assert 400 for these)
- [x] add a crash-orphan retry test: pre-create a canonical file
      `mark_photos/<mark_id>/<frame_id>.jpg` with no row, then POST — assert the
      row's `image.name` is the canonical path (no `_XXXX` suffix)
- [x] write **oversize tests pinned by size band**: a body in the 10–12 MB band
      → `413` (our explicit check), a body >12 MB → `400` (`RequestDataTooBig`
      raised in the permission before the view; the contract treats 400/413
      identically, so both are acceptable — the test just documents which fires)
- [x] add a test posting with the client's real `Accept` header (no `Accept` or
      `*/*`) asserting it is **not** a `406` (guards gotcha #4)
- [x] all photo tests use `_signed_photo_post`, the autouse
      `_clear_throttle_cache` fixture, and `override_settings(MEDIA_ROOT=tmp_path)`
- [x] run `uv run pytest src/apps/mobile/tests.py` — must pass before next task

### Task 4: Verify acceptance criteria

- [ ] verify all Overview requirements: binary JPEG accepted, idempotent by
      `(race, mark, frame)`, `201`/`200` semantics, build-HMAC-only auth
- [ ] verify all response codes from the contract table are covered by tests
      (200, 201, 400, 403, 404, 413, 429)
- [ ] verify a >2.5 MB frame is accepted (proves the `DATA_UPLOAD_MAX_MEMORY_SIZE`
      bump works end-to-end through the permission layer)
- [ ] run the full suite: `uv run pytest`
- [ ] run `make format && make lint` and fix any findings

### Task 5: Update documentation & finalize

**Files:**
- Modify: `CLAUDE.md` (mobile app section)
- Modify: `src/apps/mobile/README.md` (if it enumerates endpoints)

- [ ] add a "Photo upload" invariant paragraph to `CLAUDE.md` mirroring the
      "Track upload" / "Marks upload" notes (binary body, build-HMAC-only,
      idempotent `(mark, frame_id)`, immutable/out-of-`versioning.py`, no-trailing-
      slash URL, `DATA_UPLOAD_MAX_MEMORY_SIZE` dependency)
- [ ] update `src/apps/mobile/README.md` endpoint list if present
- [ ] move this plan to `docs/plans/completed/` (create dir if needed)

## Post-Completion

*Informational — external actions, no checkboxes.*

**Deploy notes:**
- The `/app/media` volume is already persisted (`docker-compose_v2.yml:40`) and
  nginx already serves `/media/` with `client_max_body_size 50m` — no infra
  change required. Confirm the volume has capacity for accumulated race photos.
- `DATA_UPLOAD_MAX_MEMORY_SIZE` is now 12 MB globally; note it in the deploy
  changelog since it relaxes a Django default for all endpoints (nginx remains
  the real 50 m gate).

**Client:**
- The Android client already implements the caller for this endpoint and
  self-heals (retries) once it goes live — no coordinated client release needed.

**Manual verification (optional):**
- After deploy, post a real ≤1600px JPEG frame for an existing `photo` mark and
  confirm the file lands at `mark_photos/<mark_id>/<frame_id>.jpg` and a re-send
  returns 200 without duplicating the file.
