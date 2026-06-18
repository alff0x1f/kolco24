# Mobile member-tags read endpoint

## Overview
- Add a signed, read-only mobile-app endpoint that serves the member-tag
  (participant bracelet) pool: `GET /app/race/<race_id>/member_tags/`.
- The app scans participant bracelets at checkpoints and needs the
  `nfc_uid → number` identity to resolve a scan offline. This read currently
  lives in the public `api` app (`GET /api/member_tag/`); the mobile app should
  serve it under its own signed, ETag-cached, per-race contract. A bracelet
  scan (`touch`) does not move the ETag on its own; only provisioning edits and
  day-scale window-membership shifts do (see the membership note in Technical
  Details).
- The URL is **per-race** to reserve a hook for a future "different chip set per
  race" capability — but today the chip pool is **global** (one set physically
  reused across races), so `race_id` is accepted and not yet used for filtering.
- The existing `GET /api/member_tag/` (and POST create + `/api/member_tag/touch/`)
  stay **unchanged** — only the read is *added* to the mobile app, not removed
  from `api`.

## Context (from discovery)
- `src/website/models/tag.py` — `Tag` model: `number`, `nfc_uid` (unique,
  normalized to stripped+upper in `save()`), `last_seen_at`. **No race FK** — a
  single global pool.
- `src/api/views/tag.py` — `MemberTagListCreateView` (GET filtered
  `last_seen_at >= now-30d`, POST create), `MemberTagTouchView` (POST, bumps
  `last_seen_at` via `save(update_fields=["last_seen_at"])`).
- `src/api/serializers/tag.py` — `TagSerializer` (`[id, number, nfc_uid,
  last_seen_at]`). Stays as-is for the api app.
- `src/apps/mobile/views.py` — `AppAPIView` base + `SignedAppPermission`. Per-race
  views guard with `get_object_or_404(Race, pk=race_id, is_published=True)`.
  Conditional-GET pattern: build quoted ETag, return `HttpResponseNotModified()`
  with the `ETag` header on an `If-None-Match` match, else `Response(...)` with
  the `ETag` header. Note: a `TagSerializer` already exists in
  `apps/mobile/serializers.py` (the **legend** per-`CheckpointTag` serializer) —
  the new member-tag serializer must use a distinct name to avoid a clash.
- `src/apps/mobile/versioning.py` — `teams_version`/`races_version`/
  `legend_version`: `blake2b(digest_size=8)` hex over `MAX(updated_at)|COUNT`,
  single-source contract shared by the ETag and the `sync` manifest. `None`
  aggregates render as the literal `"None"` (empty queryset is stable).
  `races_version` is global and deliberately **absent** from the per-race
  `sync` manifest.
- `src/apps/mobile/urls.py` — per-race routes (`races`, `race/<id>/legend`,
  `race/<id>/teams`, `race/<id>/sync`).
- `src/apps/mobile/README.md`, `src/apps/mobile/tests.py` — endpoint docs +
  field-set/auth/ETag tests to mirror.

## Development Approach
- **Testing approach**: Regular (code first, then tests) — selected to mirror the
  existing mobile-app test suite, which pins each endpoint's behavior after it
  exists.
- Complete each task fully (including its tests) before the next; all tests pass
  before moving on.
- Small, focused changes; backward compatibility preserved (`api` endpoint
  untouched).
- Run `make format && make lint` before any commit (per project rules / memory).

## Testing Strategy
- **Unit tests** (`src/apps/mobile/tests.py`, pytest-style with
  `@pytest.mark.django_db`, mirroring the existing signed-request test helpers):
  required for every task that changes code.
- No UI / e2e tests — this is a backend JSON endpoint consumed by the native app.

## Progress Tracking
- Mark completed items `[x]` immediately when done.
- `➕` prefix for newly discovered tasks; `⚠️` prefix for blockers.
- Keep this file in sync if scope shifts.

## Solution Overview
- **`Tag.updated_at`** (`auto_now`) is added so the mobile fingerprint can be
  built from a field that moves on **provisioning** edits (add / renumber /
  remove) but **not** on scans.
- The deliberate split: `last_seen_at` moves on every scan (`touch`);
  `updated_at` moves only on a real `save()`. `MemberTagTouchView` keeps
  `save(update_fields=["last_seen_at"])` (no `"updated_at"`) — an **intentional
  carve-out** from CLAUDE.md's "update_fields discipline", because a scan must
  not move `MAX(updated_at)` and so cannot churn the mobile ETag on its own;
  otherwise every bracelet tap would churn the version and trigger re-downloads
  mid-race.
- **Precise ETag claim** (avoid overstating): a scan does **not** move
  `MAX(updated_at)`, so it cannot change the ETag *on its own*. The ETag can
  still shift when scan activity advances `MAX(last_seen_at)` enough to change
  **window membership** (the `COUNT` term — oldest chips age past the floor).
  That is day-scale, not per-scan churn (see the membership note below).
- The served set uses a **data-anchored** window: `last_seen_at >=
  MAX(last_seen_at) - 30 days` (not wall-clock `now()`), so an idle race has a
  perfectly stable set; the window floor only advances with real scan activity.
  Empty / never-scanned pool (`MAX is None`) returns the whole pool.
- A shared `active_member_tags()` helper feeds **both** the view and the
  fingerprint (single-source contract), so the ETag can never disagree with the
  body.
- `member_tags_version()` is **global** (no `race_id`, like `races_version`) but
  — unlike `races_version` — **is** included in the per-race `sync` manifest,
  because it's served at a per-race URL and the app needs one sync poll to learn
  what to refetch for the race it's syncing.

## Technical Details
- **Model**: `Tag.updated_at = models.DateTimeField(auto_now=True,
  verbose_name="Изменён")`. Migration backfills existing rows with a one-off
  default of `django.utils.timezone.now` (Django prompts for this when adding an
  `auto_now` field to a non-empty table).
- **Window membership note**: because the served set is filtered by
  `last_seen_at`, advancing `MAX(last_seen_at)` can age the oldest chips past the
  floor → `COUNT` drops → fingerprint shifts. This is *real* membership change,
  caught by `COUNT` (not `MAX(updated_at)`), and is gradual (day-scale), not
  per-scan churn. Accepted.
- **Serializer**: `MemberTagSerializer(ModelSerializer)`, `fields = ["number",
  "nfc_uid"]` — no `id`/`last_seen_at` leak. Distinct name from the existing
  legend `TagSerializer`.
- **View**: `MemberTagsView(AppAPIView)`, GET-only, signed. Guards
  `get_object_or_404(Race, pk=race_id, is_published=True)` (consistent with the
  other per-race views), then the standard quoted-ETag / `If-None-Match` /
  `HttpResponseNotModified()` short-circuit. `race_id` is otherwise unused —
  commented as the reserved hook for per-race chip sets.
- **URL**: `path("race/<int:race_id>/member_tags/", MemberTagsView.as_view(),
  name="member_tags")`.
- **Fingerprint**: `member_tags_version()` → `blake2b(digest_size=8)` over
  `f"{MAX(updated_at)}|{COUNT}"` of `active_member_tags()`.
- **Sync**: add `"member_tags": member_tags_version()` to the `SyncView`
  manifest's `versions` dict.

## What Goes Where
- **Implementation Steps**: model field + migration, serializer, versioning
  helper + shared queryset helper, view + URL, sync manifest wiring, README,
  tests.
- **Post-Completion**: native-app client work to consume the new endpoint and
  (eventually) stop calling `/api/member_tag/`; any later per-race chip-set
  model. Not part of this plan.

## Implementation Steps

### Task 1: Add `Tag.updated_at` field + migration

**Files:**
- Modify: `src/website/models/tag.py`
- Create: `src/website/migrations/00XX_tag_updated_at.py` (via `makemigrations`)

- [x] add `updated_at = models.DateTimeField(auto_now=True, verbose_name="Изменён")` to `Tag`
- [x] run `uv run python src/manage.py makemigrations website` (Django auto-backfills `auto_now` fields with the migration-time clock — no interactive default prompt needed)
- [x] verify the migration applies cleanly: `uv run python src/manage.py migrate`
- [x] write/extend a test asserting `Tag.updated_at` is set on create and advances on a provisioning `save()` (e.g. renumber)
- [x] assert existing rows are backfilled non-null after migrate (field is non-nullable; `auto_now` backfills the migration-time clock so the DB enforces non-null)
- [x] run tests — must pass before next task

### Task 2: Document the `touch` carve-out from update_fields discipline

**Files:**
- Modify: `src/api/views/tag.py`
- Modify: `src/api/tests.py`

- [x] add a comment at `MemberTagTouchView`'s `tag.save(update_fields=["last_seen_at"])` explaining it **intentionally omits** `"updated_at"` so scans stay invisible to the mobile fingerprint (cross-reference the mobile endpoint)
- [x] confirm no behavior change (still bumps only `last_seen_at`)
- [x] add a test in `src/api/tests.py` (match the file's existing `APITestCase` class style, e.g. `MemberTagAPITestCase`, **not** the CLAUDE.md pytest style — consistency with neighbors wins here) asserting a `touch` advances `last_seen_at` but leaves `Tag.updated_at` unchanged
- [x] run tests — must pass before next task

### Task 3: Add `MemberTagSerializer`

**Files:**
- Modify: `src/apps/mobile/serializers.py`

- [x] add `MemberTagSerializer(serializers.ModelSerializer)` with `model = Tag`, `fields = ["number", "nfc_uid"]` (distinct name from the existing legend `TagSerializer`)
- [x] import `Tag` from `website.models` as needed
- [x] write a serializer-level test asserting output keys are exactly `{number, nfc_uid}` for a sample tag
- [x] run tests — must pass before next task

### Task 4: Add `active_member_tags()` + `member_tags_version()` to versioning

**Files:**
- Modify: `src/apps/mobile/versioning.py`

- [x] add `active_member_tags()` helper: `MAX(last_seen_at)`; if `None` return `Tag.objects.all()`, else `Tag.objects.filter(last_seen_at__gte=newest - timedelta(days=30))`
- [x] add `member_tags_version()`: `blake2b(digest_size=8)` over `f"{MAX(updated_at)}|{COUNT}"` of `active_member_tags()` (no `race_id`)
- [x] add a docstring noting: global today / gains `race_id` later; and **why** this version (unlike `races_version`) **is** in the per-race sync manifest (served at a per-race URL)
- [x] write tests: stable hash for an unchanged pool; provisioning `save()` (renumber) changes it; a `touch` that does not change membership does **not** change it; empty pool yields a stable (`"None"`-based) hash
- [x] run tests — must pass before next task

### Task 5: Add `MemberTagsView` + URL

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/urls.py`

- [x] add `MemberTagsView(AppAPIView)` GET-only: `get_object_or_404(Race, pk=race_id, is_published=True)`; build `quoted = f'"{member_tags_version()}"'`; `If-None-Match` → `HttpResponseNotModified()` with `ETag` header; else `Response({"member_tags": MemberTagSerializer(active_member_tags(), many=True).data})` with `ETag` header
- [x] add a comment marking `race_id` as the reserved (currently-unused) hook for per-race chip sets
- [x] import `member_tags_version` + `active_member_tags` from `.versioning`, `MemberTagSerializer` from `.serializers`, `Tag` as needed
- [x] add the URL route `name="member_tags"` to `apps/mobile/urls.py`
- [x] write tests: signed GET returns 200 with `member_tags` list + `ETag`; unsigned/bad-key → `403 {"detail": "Forbidden"}`; `If-None-Match` with the ETag → `304` (no body); unpublished/missing race → `404`; field-set is exactly `{number, nfc_uid}`; filter excludes a tag older than `MAX-30d` and includes a fresh one; never-scanned pool returns all
- [x] in the filter/boundary test, construct tags with **explicit** `last_seen_at` values straddling the 30-day floor (do not rely on auto-set save-time clocks) so the window math is deterministic and non-flaky
- [x] run tests — must pass before next task

### Task 6: Wire `member_tags` into the sync manifest

**Files:**
- Modify: `src/apps/mobile/views.py` (SyncView)

- [x] add `"member_tags": member_tags_version()` to the `SyncView` `versions` dict
- [x] update the `SyncView` docstring to mention the new `member_tags` manifest key (and why it's per-race-manifest-included unlike `races`)
- [x] write/extend a test asserting `versions.member_tags` is present and **equals** the member-tags endpoint's ETag value (bare vs quoted accounted for)
- [x] run tests — must pass before next task

### Task 7: Update mobile README

**Files:**
- Modify: `src/apps/mobile/README.md`

- [ ] add `/app/race/<id>/member_tags/` to the endpoints list: global pool today / `race_id` reserved for per-race sets; the `MAX(last_seen_at) − 30d` data-anchored window; the deliberate `touch`/`updated_at` carve-out; and that its version is in the sync manifest
- [ ] (docs-only task — no new tests; covered by Task 5/6 tests)

### Task 8: Verify acceptance criteria
- [ ] verify the endpoint serves the pool, is signed, ETag-cached, and per-race-URL-scoped (Overview goals)
- [ ] verify `/api/member_tag/` GET + POST + `touch` are unchanged (no behavior diff)
- [ ] verify a `touch` that does **not** change window membership does not change the mobile ETag; a provisioning edit does (do not assert the over-broad "any scan never changes the ETag")
- [ ] run full suite: `uv run pytest`
- [ ] run `make lint`

### Task 9: [Final] Update documentation + close out
- [ ] update `CLAUDE.md` mobile-app section: add the `member_tags` endpoint to the invariants/endpoint list, note the global-pool-today / `race_id`-reserved design, the data-anchored window, the `touch`/`updated_at` carve-out, and the manifest inclusion
- [ ] run `make format && make lint`
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only.*

**External system updates:**
- Native iOS/Android client: implement consuming `GET /app/race/<id>/member_tags/`
  (signed request + ETag caching + reading `versions.member_tags` from the sync
  manifest), then migrate off `/api/member_tag/` for the read.

**Future capability (out of scope here):**
- Per-race chip sets: introduce a `TagSet` (named set; `Race` → optional
  `TagSet`; null = global pool) or a `Tag`↔`Race` M2M, then make
  `active_member_tags()` / `member_tags_version()` take `race_id`. The mobile URL
  and app contract do not change.
