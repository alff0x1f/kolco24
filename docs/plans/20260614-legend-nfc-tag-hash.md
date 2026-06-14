# Legend NFC tag hashing (GET /app/race/<id>/legend/)

## Overview

Expose each checkpoint's NFC tags in the mobile legend endpoint, but as an
**HMAC hash** of `tag_id` — never the raw UID. This lets the legend be served at
any time (even before the race) without leaking the physical tag IDs: the app
matches a scanned UID by hashing it with the same per-build secret and comparing
to the hashes from the legend.

- **Problem solved:** today `LegendView` deliberately omits tags, so the app has
  no offline way to map a scanned NFC tag → checkpoint. Adding raw `tag_id`s
  would let anyone with the (signed) endpoint mine the tag IDs before the start.
- **Key benefit:** offline scan-to-checkpoint matching, with the raw UID never
  on the wire.
- **Integration:** rides inside the existing legend resource (nested `tags` per
  checkpoint), reuses the existing HMAC signing infra and the existing
  ETag/`If-None-Match` conditional-GET machinery.

## Context (from discovery)

- Files/components involved:
  - `src/apps/mobile/views.py` — `LegendView` (and `SyncView` for the version
    manifest); base `AppAPIView` stashes `request.app_meta`.
  - `src/apps/mobile/serializers.py` — `LegendCheckpointSerializer`.
  - `src/apps/mobile/versioning.py` — `legend_state(race_id)` /
    `legend_version(race_id)`, single source of truth for the legend ETag and
    `versions.legend`.
  - `src/apps/mobile/signing.py` — HMAC helpers (`sign`, `verify`, `sha256_hex`).
  - `src/apps/mobile/permissions.py` — `SignedAppPermission`, stashes
    `request.app_meta = {install_id, platform, app_version, key_id, ip}` (NOT
    the secret).
  - `src/website/models/checkpoint.py` — `CheckpointTag` (`point` FK → Checkpoint
    `related_name="tags"`, `tag_id`, `check_method`); **no `updated_at`**.
  - `src/apps/mobile/tests.py` — pytest-style tests.
  - `src/apps/mobile/README.md`, root `CLAUDE.md` — docs to update.
- Related patterns found:
  - Per-key secret selected by `X-App-Key-Id` from `settings.MOBILE_APP_KEYS`;
    `key_id` is already signature-verified by the permission before the view runs.
  - Version fingerprints use `blake2b(digest_size=8)` over
    `MAX(updated_at)|COUNT` aggregates of the exact served queryset; `None`
    renders as the literal `"None"`.
  - ETag set on **every** exit path; `If-None-Match` short-circuits to `304`
    before serialization.
  - Prior `updated_at` migrations: `Athlet` (0076), `Checkpoint` (0077),
    `Category` (0078), `Race` (0079).
- Dependencies identified: latest website migration is `0079_race_updated_at`;
  next is `0080`.

## Development Approach

- **Testing approach**: Regular (code first, then tests) — but every task ends
  with its tests, all passing, before the next task.
- Complete each task fully before moving on; small, focused changes.
- **CRITICAL: every task includes new/updated tests** (success + error/edge),
  and **all tests pass before the next task**.
- Run `uv run pytest src/apps/mobile/tests.py` after each change; keep this plan
  in sync if scope shifts.
- Maintain backward compatibility: legend response is additive (new `tags`
  field); `teams_version`/`races_version` untouched.

## Testing Strategy

- **Unit tests** (required per task): `tag_hash` helper; serializer field-set;
  versioning incl. `key_id` and tag aggregates; view behavior (hash value, no
  raw `tag_id`, ETag changes on tag edit, per-`key_id` divergence, 304 within a
  key_id, hidden legend still 200 empty).
- **E2E tests**: none — this is a backend signed-API change with no UI.

## Progress Tracking

- Mark completed items `[x]` immediately when done.
- New tasks: `➕` prefix. Blockers: `⚠️` prefix. Update plan if scope changes.

## Solution Overview

1. Add `updated_at` (auto_now) to `CheckpointTag` so tag edits are detectable.
2. Add a `tag_hash(secret, tag_id)` HMAC helper.
3. Fold the tag aggregates **and** the request's `key_id` into the legend
   fingerprint (`legend_state`/`legend_version`), so the ETag changes on any tag
   edit and differs per build (per secret) — preventing a stale-cache `304`
   after an app update changes the secret.
4. Nest a `tags` array (`{id, tag_hash, check_method}`) inside each checkpoint in
   the legend response; the view feeds the resolved secret via serializer
   context. `SyncView` threads `key_id` too, keeping `versions.legend` == legend
   ETag.

### Key design decisions & rationale

- **HMAC keyed by the per-build secret** (selected by `X-App-Key-Id`): the app
  already holds its secret, so nothing new to distribute. Stops JSON-mining of
  tag IDs. Residual risk — APK reverse-engineering yields the secret and the
  short UID space is then brute-forceable — is **accepted** for this threat model.
- **No time-gate**: hashing makes pre-start exposure safe, so tag exposure is
  not gated on race start. `is_legend_visible` still gates the **whole** legend
  exactly as today (hidden → `200` with empty `checkpoints`, ETag on every path).
- **`key_id` in the fingerprint**: tag hashes depend on the secret but the
  base fingerprint (`MAX|COUNT|visible`) does not. Without folding `key_id`, two
  builds would share an ETag with different bodies, and an install that updates
  to a new secret would send a matching `If-None-Match` → `304` → keep hashes
  computed with the **old** secret while scanning with the **new** one (zero
  matches). Folding `key_id` busts the cache across builds.

## Technical Details

- `tag_hash(secret, tag_id) -> hmac.new(secret.encode(), tag_id.encode(),
  hashlib.sha256).hexdigest()` in `signing.py`.
- Secret resolution in the view:
  `settings.MOBILE_APP_KEYS[request.app_meta["key_id"]]` (key_id is already
  verified; `app_meta` is present because permission passed).
- `legend_state(race_id, key_id)` / `legend_version(race_id, key_id)`: add tag
  aggregates `MAX(CheckpointTag.updated_at)|COUNT(CheckpointTag)` over the
  **draft-excluded** checkpoints of the race, and append `key_id` to the blake2b
  input. The exact filter must mirror `legend_state`'s checkpoint queryset
  predicate (same `race_id`, same draft exclusion):
  `CheckpointTag.objects.filter(point__race_id=race_id).exclude(point__type=CheckpointType.draft.value).aggregate(max_updated=Max("updated_at"), count=Count("id"))`.
  `None` aggregates render `"None"` (stable for a tag-less race).
- Secret subscript is safe: `key_id` was already signature-verified against the
  same `MOBILE_APP_KEYS` map by the permission, so `MOBILE_APP_KEYS[key_id]`
  cannot `KeyError` — use the subscript, **not** `.get()` (a defensive `.get()`
  would mask a real bug).
- Serializer: `LegendTagSerializer` → `{id, tag_hash, check_method}` where
  `tag_hash` is a `SerializerMethodField` reading `self.context["secret"]`;
  `LegendCheckpointSerializer` gains `tags = LegendTagSerializer(many=True)`
  (source `tags`). View queryset adds `.prefetch_related("tags")` and passes
  `context={"secret": secret}`.
- The legend `tags` ordering follows `CheckpointTag.Meta.ordering = ["id"]`
  (prefetch preserves it); no extra ordering needed.

## What Goes Where

- **Implementation Steps** (`[ ]`): model + migration, hashing helper,
  versioning, serializer, view, sync, docs, tests — all in this repo.
- **Post-Completion** (no checkboxes): the iOS/Android client must implement the
  matching `tag_hash` and re-fetch behavior; manual end-to-end scan verification.

## Implementation Steps

### Task 1: Add `updated_at` to `CheckpointTag`

**Files:**
- Modify: `src/website/models/checkpoint.py`
- Create: `src/website/migrations/0080_checkpointtag_updated_at.py`

- [x] add `updated_at = models.DateTimeField(auto_now=True)` to `CheckpointTag`
- [x] generate the migration: `uv run python src/manage.py makemigrations website`
- [x] confirm the file is named `0080_checkpointtag_updated_at` and depends on
      `0079_race_updated_at`
- [x] run `uv run python src/manage.py migrate` against the local DB to confirm
      it applies cleanly
- [x] verify there are **no** `CheckpointTag.save(update_fields=[...])` sites in
      the codebase (the `update_fields` discipline rule); the only production
      write path is `website/views/views_.py:331`
      `CheckpointTag.objects.update_or_create(point=point, tag_id=tag_id)`, which
      moves the fingerprint via create/delete (COUNT), not in-place edit
- [x] run `uv run pytest src/apps/mobile/tests.py` (existing tests still green)
      — must pass before next task

### Task 2: Add `tag_hash` HMAC helper

**Files:**
- Modify: `src/apps/mobile/signing.py`
- Modify: `src/apps/mobile/tests.py`

- [x] add `tag_hash(secret: str, tag_id: str) -> str` returning
      `hmac.new(secret.encode(), tag_id.encode(), hashlib.sha256).hexdigest()`
      with a docstring noting it mirrors the client side
- [x] write test: `tag_hash` is deterministic and equals a hand-computed
      `hmac.new(...).hexdigest()` for a known secret/tag_id
- [x] write test: different secrets produce different hashes for the same tag_id
- [x] run `uv run pytest src/apps/mobile/tests.py` — must pass before next task

### Task 3: Fold tag aggregates + `key_id` into the legend fingerprint

**Files:**
- Modify: `src/apps/mobile/versioning.py`
- Modify: `src/apps/mobile/tests.py`

- [x] change `legend_state(race_id, key_id)` to also aggregate
      `MAX(CheckpointTag.updated_at)|COUNT(CheckpointTag)` over tags whose
      `point` is a non-draft checkpoint of `race_id`, and append `key_id` to the
      blake2b input string
- [x] change `legend_version(race_id, key_id)` to accept and forward `key_id`
- [x] update the module docstrings/comments to reflect tags now in scope and the
      `key_id` dependency (and that `teams_version`/`races_version` are unchanged)
- [x] write test: an in-place `tag.save()` (changing `check_method`) bumps
      `updated_at` and changes `legend_version` (unit-level proof the `auto_now`
      field is folded in — the production path edits via create/delete, but this
      guards against a future in-place save going undetected); adding/removing a
      tag changes it
- [x] write test: a tag on a **draft** checkpoint does NOT change
      `legend_version`
- [x] write test: two different `key_id`s yield different `legend_version` for
      the same data; an empty/tag-less race is stable (no crash, `"None"`)
- [x] run `uv run pytest src/apps/mobile/tests.py` — must pass before next task

### Task 4: Nested tag serializer with hashed `tag_id`

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] add `LegendTagSerializer` with fields `id`, `check_method`, and a
      `tag_hash` `SerializerMethodField` that reads `self.context["secret"]` and
      calls `signing.tag_hash`
- [ ] add `tags = LegendTagSerializer(many=True)` (source `tags`) to
      `LegendCheckpointSerializer`; never expose raw `tag_id`
- [ ] write test (serializer-level): given a secret in context, output is
      `{id, tag_hash, check_method}` with `tag_hash == tag_hash(secret, raw)` and
      no `tag_id` key present
- [ ] run `uv run pytest src/apps/mobile/tests.py` — must pass before next task

### Task 5: Wire the secret + tags into `LegendView`

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] in `LegendView.get`, resolve `key_id = request.app_meta["key_id"]` and
      `secret = settings.MOBILE_APP_KEYS[key_id]` (subscript, not `.get()` — see
      Technical Details; safe because `key_id` is already permission-verified)
- [ ] call `legend_state(race_id, key_id)` (the signature gained `key_id` in
      Task 3) and wrap version in quotes for the ETag (keep the
      `is_legend_visible is None` → `Http404` guard and the `If-None-Match` 304
      short-circuit on every exit path)
- [ ] add `.prefetch_related("tags")` to the checkpoint queryset and pass
      `context={"secret": secret}` to `LegendCheckpointSerializer`
- [ ] keep the hidden-legend branch returning `200` with `checkpoints: []` and
      the ETag set
- [ ] write test: a real signed GET returns checkpoints each with a `tags` array
      of `{id, tag_hash, check_method}`, hash matches `tag_hash(secret, raw)`,
      and no raw `tag_id` appears anywhere in the body
- [ ] write test: editing a tag changes the response ETag; `If-None-Match` with
      the new ETag returns `304` (same key_id)
- [ ] write test: two different `key_id`s get different hashes AND different
      ETags for the same race
- [ ] write test: hidden legend (`is_legend_visible=False`) still returns `200`
      with empty `checkpoints` and an ETag
- [ ] run `uv run pytest src/apps/mobile/tests.py` — must pass before next task

### Task 6: Thread `key_id` through `SyncView`'s `versions.legend`

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] in `SyncView.get`, pass `request.app_meta["key_id"]` to
      `legend_version(race_id, key_id)` so `versions.legend` equals the legend
      ETag for that build
- [ ] write test: `versions.legend` from `/sync/` (bare) equals the legend
      endpoint's ETag (unquoted) for the same `key_id`
- [ ] write test: `versions.legend` differs across two `key_id`s
- [ ] run `uv run pytest src/apps/mobile/tests.py` — must pass before next task

### Task 7: Verify acceptance criteria

- [ ] verify all Overview requirements: tags exposed as hash, no raw `tag_id` on
      the wire, ETag/304 correct per `key_id`, sync manifest consistent, hidden
      legend unchanged
- [ ] verify edge cases: tag-less race, draft-checkpoint tags excluded from
      version, empty aggregates render `"None"`
- [ ] run full suite: `uv run pytest`
- [ ] run `make format && make lint`

### Task 8: [Final] Update documentation

**Files:**
- Modify: `src/apps/mobile/README.md`
- Modify: `CLAUDE.md`

- [ ] README: flip the «теги (`CheckpointTag`) вне области» / «правка тега
      версию легенды не сдвигает» notes; update the «Что обновляется» legend
      bullet, the endpoints-table legend row, and the **Статус** block to say
      tags are now served as `tag_hash` and that `legend_version` depends on
      `key_id` + tag aggregates
- [ ] CLAUDE.md (apps.mobile section): legend endpoint no longer "без tags";
      add `CheckpointTag.updated_at` to the `update_fields` discipline note; note
      legend ETag/`versions.legend` now vary by `key_id`
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention or external systems — informational only.*

**Manual verification:**
- End-to-end: scan a real NFC tag in the iOS/Android app and confirm it matches
  the correct checkpoint using the hashed legend.
- Confirm an app update that rotates `X-App-Key-Id`/secret re-fetches the legend
  (new ETag) rather than serving a `304` with stale hashes.

**Scope note:**
- The legacy `/api/` endpoints still expose **raw** `tag_id` via
  `api/serializers/tag.py:CheckpointTagSerializer2` (`{id, tag_id, check_method}`,
  used by `api/serializers/checkpoint.py`). That is a separate, authenticated
  endpoint and is **intentionally out of scope** — hashing the mobile legend does
  not close raw-UID exposure globally, only on the signed `/app/*` legend.

**External system updates:**
- iOS/Android client: implement the matching `tag_hash` (HMAC-SHA256 of the
  scanned UID string keyed by the build's secret) and compare against legend
  `tag_hash` values; re-fetch legend when `versions.legend` changes.
- Confirm the client hashes the **same canonical UID string form** the server
  stores in `CheckpointTag.tag_id` (case/format must match exactly).
