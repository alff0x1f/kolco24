# Photo contest: participant submissions

## Overview
- First step of the site photo contest: logged-in users upload photos into a contest nomination; organizers
  moderate them in `/admin/`.
- The contest is **not tied to races**: a separate app `apps.photo_contest` with no FK into `website.Race`/`Team`.
- Several contests, each with nominations. A per-user photo limit applies to each nomination.
- Originals are kept (admin-only, EXIF intact); the public site only ever shows a 1600 px EXIF-free preview.
- Voting, public gallery and results are **out of scope** (later steps).

## Context (from discovery)
- Unmerged branch `feature/photo-contest` has an `apps.photo_contest` tied to `website.Race` with pairwise voting.
  It is **not merged**; this plan starts fresh from `master`. Only image-processing ideas are reused:
  `git show feature/photo-contest:src/apps/photo_contest/views.py` (`_process_display_image`) and
  `.../forms.py` (`clean_image` decompression-bomb probe).
- `MEDIA_ROOT = src/media/` (`/app/media` in the container), served publicly by nginx (`location /media/` →
  `alias /app/media/`, `deploy/nginx.conf`). nginx mounts the media volume read-only.
- `docker-compose_v2.yml`: `kolco24_web` mounts `${DATA_LOCATION}/media:/app/media`; nginx `client_max_body_size 50m`.
- `Pillow>=10.0.0` already a dependency.
- Anonymous → passwordless login pattern: `reverse("account_start") + "?" + urlencode({"next": request.path})`
  (as in `AddTeam`).
- Home page: `src/website/views/community.py:HomeView`, template `src/templates/website/home.html`; home tests live in
  `src/website/test_publications.py`.
- New pages extend `base-2.html`, fields written by hand (no `{{ form.field }}`), page CSS in
  `src/static/css/<page>.css` under a scoped wrapper class.

## Development Approach
- **testing approach**: Regular (code first, then tests)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - tests are not optional - they are a required part of the checklist
  - write unit tests for new functions/methods
  - write unit tests for modified functions/methods
  - add new test cases for new code paths
  - update existing test cases if behavior changes
  - tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change (`uv run pytest --reuse-db src/apps/photo_contest`)
- run `make format` and `make lint` before each commit
- maintain backward compatibility

## Testing Strategy
- **unit tests**: required for every task, in `src/apps/photo_contest/tests.py` (pytest functions,
  `@pytest.mark.django_db`, `client`/`django_user_model` fixtures)
- images are generated with Pillow in the test; `MEDIA_ROOT` and `MEDIA_PRIVATE_ROOT` point at `tmp_path` via the
  `settings` fixture (an autouse fixture in the test module)
- size thresholds (`STORE_MAX_BYTES`, `UPLOAD_MAX_BYTES`, `MAX_PIXELS`) are read at call time so tests can
  `monkeypatch` them small instead of building 15–20 MB files
- no e2e suite in the project

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview
- New app `src/apps/photo_contest/` (`AppConfig.label = "photo_contest"`), mounted at `/photo/`.
- Three models: `PhotoContest` → `PhotoNomination` → `PhotoEntry`. An entry reaches its contest only through its
  nomination (one source of truth).
- Upload: one file per submit, ≤ 20 MB, JPEG/PNG/WebP. Stored original ≤ 15 MB (byte-for-byte if already small,
  otherwise re-encoded). Public preview 1600 px JPEG without EXIF.
- Originals live in a **private directory** (`MEDIA_PRIVATE_ROOT`, separate volume, never mounted into nginx) and are
  served by a Django view to `is_staff` only. Reason: originals carry EXIF/GPS; obscure-path-under-`/media/` was
  rejected.
- Moderation: `status` field, managed in `/admin/` (list + bulk approve/reject). No public gallery yet; a participant
  sees only their own photos and their status.
- Discovery: `/photo/` list page plus a home-page card while a contest is accepting photos.

## Technical Details

### Models (`src/apps/photo_contest/models.py`)
- `PhotoContest`: `title` (200), `slug` (unique), `description` (TextField, blank), `submission_until`
  (DateTimeField), `max_photos_per_user` (PositiveIntegerField, default 3, **per nomination**), `is_published`
  (default False; turning it on opens submission), `created_at`, `updated_at`.
  `is_submission_open` = `is_published and timezone.now() <= submission_until`. `ordering = ["-submission_until"]`.
- `PhotoNomination`: `contest` FK CASCADE `related_name="nominations"`, `title` (200), `description` (blank),
  `order` (default 0). `ordering = ["order", "pk"]`.
- `PhotoEntry`: `nomination` FK **PROTECT** `related_name="entries"`, `uploaded_by` FK user CASCADE
  `related_name="photo_entries"`, `caption` CharField(300, blank), `original` FileField(storage=private storage,
  `max_length=255`), `preview` ImageField (default storage, `max_length=255`), `width`, `height` (of the stored
  original), `size_bytes`, `status` `pending`/`approved`/`rejected` (default `pending`, labels «На модерации» /
  «Принято» / «Отклонено»), `created_at`. Index `(nomination, status)`. `ordering = ["-created_at"]`.
- File layout — one folder per contest, split by nomination, so an archived contest is backed up with one copy
  (`rsync media_private/photo_contest/<contest_id>/`):
  - original: `MEDIA_PRIVATE_ROOT/photo_contest/<contest_id>/<nomination_id>/<uuid4 hex>.<ext>` (`ext` from the
    stored format: `jpg`/`png`/`webp`)
  - preview: `MEDIA_ROOT/photo_contest/<contest_id>/<nomination_id>/<same uuid>.jpg`
  - ids, not slugs: a path is fixed at upload time, and ids never change. Names are built explicitly in
    `create_entry` (a helper `entry_dir(nomination)`), not via `upload_to` callables.
  - consequence: `PhotoEntry.nomination` is **read-only in admin** — moving an entry would leave its files in the old
    folder. A photo in the wrong nomination is rejected and re-uploaded.
- `width`/`height` mean **as displayed** (after EXIF orientation) on both paths: the byte-for-byte path swaps header
  dims when Orientation is 5–8.
- Private storage (`src/apps/photo_contest/storage.py`): `PrivateStorage(FileSystemStorage)` whose `base_location`/
  `location` are plain properties reading `settings.MEDIA_PRIVATE_ROOT` **on every access**, and whose `url()` raises
  `NotImplementedError` (no `/media/` URL can ever be built for a private file). Passed as a module-level callable
  `private_storage()` so migrations stay path-free. Note: Django 4.2 calls the `storage=` callable **once** at model
  class creation — the callable alone is not lazy, which is why the properties are needed for test overrides.
- Deleting a contest that still has entries is blocked by `PROTECT` on `PhotoEntry.nomination` — intended (no silent
  mass deletion of photos); clear entries first.

### Settings
- `MEDIA_PRIVATE_ROOT = os.getenv("MEDIA_PRIVATE_ROOT", os.path.join(BASE_DIR, "media_private/"))`
  → `/app/media_private` in the container.
- `INSTALLED_APPS += ["apps.photo_contest"]`; `config/urls.py`: `path("photo/", include("apps.photo_contest.urls"))`
  placed before `path("", include("website.urls"))`.

### Image processing (`src/apps/photo_contest/images.py`, pure functions)
- Constants: `UPLOAD_MAX_BYTES = 20 MB`, `STORE_MAX_BYTES = 15 MB`, `MAX_PIXELS = 50_000_000` (above 48 Мп phones;
  one decoded RGB frame ≈ 150 MB),
  `PREVIEW_MAX_SIDE = 1600`, `FALLBACK_MAX_SIDE = 6000`, `QUALITY_STEPS = (92, 88, 85, 80)`,
  `ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}`.
- `ImageRejected(ValueError)` carries the Russian user message.
- `probe(file) -> (format, width, height)`: header-only `Image.open`; size > `UPLOAD_MAX_BYTES` →
  «Файл больше 20 МБ»; format not allowed or `UnidentifiedImageError` (incl. HEIC) → «Поддерживаются JPEG, PNG и
  WebP»; pixels over `MAX_PIXELS` → «Слишком большое разрешение изображения»; `DecompressionBombError`/`OSError` →
  «Не удалось прочитать изображение». Always `seek(0)` afterwards. Width/height returned as displayed.
- `make_original(file, fmt) -> (bytes, ext, width, height)`: if `file.size <= STORE_MAX_BYTES` → raw bytes
  unchanged, dims from the header. Otherwise decode, `ImageOps.exif_transpose`, RGB, and save as JPEG at full
  resolution trying each of `QUALITY_STEPS` until ≤ `STORE_MAX_BYTES`, passing the transposed image's EXIF
  (`exif=img.getexif().tobytes()` — orientation already reset by `exif_transpose`). If still too big, `thumbnail`
  to `FALLBACK_MAX_SIDE` and save at q85. `close()`/`del` intermediate images to keep peak memory down.
- `make_preview(file) -> bytes`: open, for JPEG `img.draft("RGB", (PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))` before load
  (cheap DCT-scaled decode), `exif_transpose`, alpha composited on white, RGB, `thumbnail(PREVIEW_MAX_SIDE)`, JPEG
  q85, no `exif=`.
- `probe` only reads the header, so a truncated/corrupt body can still fail later: both `make_original` and
  `make_preview` wrap decode/encode and turn `OSError`/`SyntaxError`/`ValueError`/`DecompressionBombError` into
  `ImageRejected("Не удалось прочитать изображение.")`.
- Constants are looked up as module attributes at call time (`images.UPLOAD_MAX_BYTES`, no default-arg binding, no
  `from images import UPLOAD_MAX_BYTES` elsewhere) so `monkeypatch.setattr(images, …)` works everywhere.

### Upload flow (`src/apps/photo_contest/services.py:create_entry`)
- Form (`forms.py:PhotoUploadForm`, plain `forms.Form`): `nomination` (ModelChoiceField limited to the contest's
  nominations — another contest's id is a form error), `image` (FileField; `clean_image` calls `probe`, maps
  `ImageRejected` to a field error), `caption` (max 300, optional).
- `create_entry(contest, nomination, user, file, caption)`: build original + preview bytes **before** the
  transaction (CPU work outside the lock), then inside `transaction.atomic()`:
  `PhotoNomination.objects.select_for_update().get(pk=…)` → count `PhotoEntry` for (nomination, user) excluding
  `rejected` → `≥ max_photos_per_user` raises `LimitReached` → save both files → create row. Shape:
  `try: with transaction.atomic(): … except BaseException: delete written files; raise` — the `try` wraps the whole
  atomic block so a commit-time error also cleans up. `ImageRejected` from processing propagates to the view, which
  turns it into an `image` field error (re-render, 200).
- Limit message: «В номинации «X» уже N фото — это максимум».

### Deletion
- `post_delete` signal on `PhotoEntry` captures the two file names + storages and deletes them via
  `transaction.on_commit(...)` — a rolled-back delete keeps its files. Covers the participant delete view, admin
  queryset delete and the cascade from deleting a user. Missing files are ignored.

### URLs (`src/apps/photo_contest/urls.py`) and views
- `GET /photo/` `photo_contests`: published contests, open first, then the rest by `-submission_until`.
- `GET /photo/<slug>/` `photo_contest`: unpublished → 404 unless `is_staff` (then banner «Конкурс не опубликован»).
  Shows title, description, deadline, nominations. Anonymous: «Войти и отправить фото» → `account_start?next=`.
  Logged-in + open: upload form; nominations at the limit are `disabled` in the `<select>`. Closed: «Приём закрыт».
  «Мои фото»: own entries of this contest (`select_related("nomination")`) with preview, nomination, caption, status;
  delete button while open.
- `POST /photo/<slug>/upload/` `photo_upload`: anon → `account_start?next=<contest page>`; unpublished → 404 (staff
  included — no uploads into an unpublished contest); closed → message + redirect; invalid form → re-render the
  contest page with errors (status 200); `LimitReached` → field error on `nomination`; success → message «Фото
  отправлено на модерацию» + redirect to the contest page.
- `POST /photo/<slug>/entry/<id>/delete/` `photo_delete`: login required; entry must belong to the contest and to the
  user, else 404; closed → message + redirect, nothing deleted.
- `GET /photo/entry/<id>/original/` `photo_original`: only users with `photo_contest.view_photoentry` (superusers
  pass), else 404 — plain `is_staff` is not enough, staff may lack photo-contest rights.
  `FileResponse(entry.original.open("rb"))` (Content-Type guessed from the name); missing file on disk → 404.
- The contest page and the invalid-upload re-render share one context builder (`_contest_context`). Per-nomination
  own counts come from one `annotate(Count("entries", filter=Q(entries__uploaded_by=user) & ~Q(entries__status="rejected")))`.
- Upload form: `method="post"`, `enctype="multipart/form-data"`, `accept=...` — asserted in a test, because the test
  client always sends multipart and would hide a missing `enctype`.
- Accepted: no throttle on upload/delete cycles (each upload costs a decode); revisit if abused.
- Templates in `src/templates/photo_contest/`: `list.html`, `contest.html`; CSS `src/static/css/photo_contest.css`
  scoped under `.photo-contest`. File input `accept="image/jpeg,image/png,image/webp"`.

### Admin (`src/apps/photo_contest/admin.py`)
- `PhotoContestAdmin`: `prepopulated_fields = {"slug": ("title",)}`, `PhotoNominationInline` (tabular), read-only
  field showing the contest's folders (`photo_contest/<id>/` under `MEDIA_PRIVATE_ROOT` and `MEDIA_ROOT`) for backups.
- `PhotoEntryAdmin`: list `thumbnail` (preview `<img>` ~80 px), contest (via nomination), nomination, author email,
  status, created_at; `list_filter` = `nomination__contest`, `nomination`, `status`; `list_select_related`;
  actions «Принять» / «Отклонить» (plain `queryset.update(status=…)` — no signals depend on it). `nomination`,
  `uploaded_by`, `original` and
  `preview` are in `readonly_fields` (no replace/clear widget — a replacement would skip processing and EXIF
  stripping); the original is shown only as a link to `photo_original`, the preview as an `<img>`.

### Home page
- `HomeView` adds `open_photo_contests` = published contests with `submission_until >= now` (one query, ordered by
  `submission_until`). Partial `src/templates/website/_home_photo_contests.html`, wrapped in
  `{% if open_photo_contests %}` — no empty state. Styling reuses existing `community.css` card classes.

## What Goes Where
- **Implementation Steps**: code, migrations, templates, tests, compose/env example, docs in this repo.
- **Post-Completion**: creating the data directory on the server, manual phone testing.

## Implementation Steps

### Task 1: App scaffold, models, settings

**Files:**
- Create: `src/apps/photo_contest/__init__.py`
- Create: `src/apps/photo_contest/apps.py`
- Create: `src/apps/photo_contest/models.py`
- Create: `src/apps/photo_contest/storage.py`
- Create: `src/apps/photo_contest/migrations/__init__.py`
- Create: `src/apps/photo_contest/migrations/0001_initial.py`
- Create: `src/apps/photo_contest/tests.py`
- Modify: `src/config/settings.py`
- Modify: `.gitignore`

- [x] create the app with `PhotoContestConfig` (`name="apps.photo_contest"`, `label="photo_contest"`)
- [x] add `PrivateStorage` (settings read on access, `url()` raises) + `private_storage()` callable
- [x] add `PhotoContest`, `PhotoNomination`, `PhotoEntry` as in Technical Details
- [x] add `MEDIA_PRIVATE_ROOT` and the app to `INSTALLED_APPS` in `settings.py`; add `src/media_private/` to `.gitignore`
- [x] generate migration `0001_initial` (`uv run python src/manage.py makemigrations photo_contest`)
- [x] write tests: `is_submission_open` (unpublished / open / past deadline), nomination ordering, `PROTECT` on
      deleting a nomination / contest with entries; with `MEDIA_PRIVATE_ROOT` overridden to `tmp_path`, a file saved
      to `original` lands under `tmp_path`; `original.url` raises
- [x] run tests - must pass before task 2

### Task 2: Image processing module

**Files:**
- Create: `src/apps/photo_contest/images.py`
- Modify: `src/apps/photo_contest/tests.py`

- [x] implement `ImageRejected`, `probe`, `make_original`, `make_preview` with module constants read at call time
- [x] write tests: small JPEG stored byte-for-byte; PNG and WebP accepted
- [x] write tests: over `STORE_MAX_BYTES` (monkeypatched small) → JPEG ≤ limit, same resolution, EXIF kept;
      fallback downscale path when quality steps are not enough
- [x] write tests: preview long side ≤ 1600 and no EXIF; EXIF orientation 6 rotates the result
- [x] write tests for errors: over `UPLOAD_MAX_BYTES`, GIF/garbage bytes, pixels over `MAX_PIXELS`, truncated JPEG
      (valid header, cut body) → `ImageRejected` from `make_original`/`make_preview`
- [x] write tests: alpha PNG preview composited on white; `width`/`height` are as-displayed for orientation 6
- [x] run tests - must pass before task 3

### Task 3: Upload form, create service, file cleanup

**Files:**
- Create: `src/apps/photo_contest/forms.py`
- Create: `src/apps/photo_contest/services.py`
- Create: `src/apps/photo_contest/signals.py`
- Modify: `src/apps/photo_contest/apps.py`
- Modify: `src/apps/photo_contest/tests.py`

- [x] implement `PhotoUploadForm` (nomination scoped to the contest, `clean_image` via `probe`, caption ≤ 300)
- [x] implement `create_entry` with `select_for_update` on the nomination, limit check excluding `rejected`,
      `LimitReached`, and cleanup of written files on failure
- [x] add `post_delete` handler removing both files; connect it in `AppConfig.ready()`
- [x] write tests: form rejects a nomination of another contest, oversized/unsupported file, long caption
- [x] write tests: `create_entry` writes original to `<private>/photo_contest/<contest_id>/<nomination_id>/` and
      preview to `<media>/photo_contest/<contest_id>/<nomination_id>/` with the same uuid, sets
      width/height/size; limit per nomination (other nomination still allowed; rejected entries not counted)
- [x] write tests: failure inside the atomic block (patched `objects.create` raising) leaves no files
- [x] write tests (`django_capture_on_commit_callbacks(execute=True)`): deleting an entry (instance, queryset, user
      cascade) removes files; a rolled-back delete keeps them
- [x] run tests - must pass before task 4

### Task 4: Public pages, upload/delete/original views

**Files:**
- Create: `src/apps/photo_contest/urls.py`
- Create: `src/apps/photo_contest/views.py`
- Create: `src/templates/photo_contest/list.html`
- Create: `src/templates/photo_contest/contest.html`
- Create: `src/static/css/photo_contest.css`
- Modify: `src/config/urls.py`
- Modify: `src/apps/photo_contest/tests.py`

- [x] add URLs and views `photo_contests`, `photo_contest`, `photo_upload`, `photo_delete`, `photo_original`
- [x] build templates on `base-2.html` with hand-written fields, disabled full nominations, «Мои фото» block, messages
- [x] mount `path("photo/", include(...))` in `config/urls.py` before `website.urls`
- [x] write tests: list shows only published, open first; contest page 404 for unpublished (user) / 200 + banner
      (staff); anon sees login link with `next`; «Мои фото» lists only own entries; closed contest shows no form
- [x] write tests: upload — anon redirect to `account_start?next=`, unpublished 404, closed refused (nothing saved),
      invalid form re-renders with errors, truncated image → field error (200, no files), limit error, success creates
      a pending entry and redirects
- [x] write test: upload form has `method="post"`, `enctype="multipart/form-data"` and the `accept` list
- [x] write tests: delete — owner ok (files gone), foreign entry 404, closed refused; original — superuser and staff
      with `view_photoentry` 200 with image bytes; staff without the permission, plain user, anon 404; missing file 404
- [x] run tests - must pass before task 5

### Task 5: Admin moderation

**Files:**
- Create: `src/apps/photo_contest/admin.py`
- Modify: `src/apps/photo_contest/tests.py`

- [x] register `PhotoContestAdmin` with nomination inline and slug prepopulation
- [x] register `PhotoEntryAdmin` with thumbnail, filters, `list_select_related`, approve/reject actions, original link
- [x] write tests: changelist and change page render for a superuser; approve/reject actions change status; change
      page has no file input for `original`/`preview`, `nomination` is read-only, links to `photo_original`;
      contest change page shows its folder paths
- [x] run tests - must pass before task 6

### Task 6: Home page card

**Files:**
- Modify: `src/website/views/community.py`
- Modify: `src/templates/website/home.html`
- Create: `src/templates/website/_home_photo_contests.html`
- Modify: `src/static/css/community.css` (only if existing card classes are not enough)
- Modify: `src/website/test_publications.py`

- [x] add `open_photo_contests` to `HomeView` context (one query)
- [x] add the partial and include it in `home.html` under `{% if open_photo_contests %}`
- [x] write tests: card shown for a published open contest with a link to `/photo/<slug>/`; hidden for unpublished
      and past-deadline contests
- [x] run tests - must pass before task 7

### Task 7: Deploy configuration

**Files:**
- Modify: `docker-compose_v2.yml`
- Modify: `deploy/kolco24.env.example`
- Create: `.dockerignore`

- [x] add `${DATA_LOCATION}/media_private:/app/media_private` to `kolco24_web` only (not nginx, not other services
      unless they need it)
- [x] add `.dockerignore` excluding at least `src/media_private/`, `src/media/`, `src/staticfiles/`, `.env`,
      `deploy/kolco24.env` (no `.dockerignore` exists; `Dockerfile` does `COPY src ./`, so local originals with GPS
      would otherwise be baked into the pushed image); check the Dockerfile doesn't need any excluded path
- [x] document `MEDIA_PRIVATE_ROOT` (optional, default `/app/media_private`) in `kolco24.env.example`
- [x] check `docker compose -f docker-compose_v2.yml config` parses with dummy `KOLCO24_IMAGE`/`DATA_LOCATION`/
      `TRUSTED_PROXY_CIDR`
- [x] run tests - must pass before task 8

### Task 8: Verify acceptance criteria
- [x] verify all requirements from Overview are implemented
- [x] verify edge cases are handled (limit race, closed contest, unpublished contest, foreign entry, bad file)
- [x] run full test suite: `uv run pytest`
- [x] run `make format` and `make lint`
- [x] smoke-test locally with `runserver`: contest created, `/photo/`, contest page, CSS, home card, original 404 checked
  ⚠️ real phone upload through a browser not done here — see Post-Completion

### Task 9: [Final] Update documentation
- [x] add a short `apps.photo_contest` section to `CLAUDE.md` (no race link, `PrivateStorage` reads settings on
      access and has no `url()`, `MEDIA_PRIVATE_ROOT` + `.dockerignore`, files deleted on commit, size rules,
      per-nomination limit, `view_photoentry` gate on originals, moderation in admin, voting out of scope)
- [x] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification**:
- upload from a real iPhone (HEIC camera setting) and Android in Safari/Chrome: confirm the browser converts to JPEG
  given the `accept` list; if not, users get «Поддерживаются JPEG, PNG и WebP» — consider `pillow-heif` later
- upload a 48 Мп photo and a 15–20 MB camera JPEG: check time and memory of the gunicorn worker
- confirm `/media_private/...` and the original path are not reachable through nginx

**External system updates**:
- on the server: create `${DATA_LOCATION}/media_private` (owned by the app user) before deploying
- include `media_private` in the server backup routine alongside `media`
