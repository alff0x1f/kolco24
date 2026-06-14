# Rename `tag_id` → `nfc_uid` + uppercase normalization (CheckpointTag & Tag)

## Overview
Both `CheckpointTag` (checkpoint NFC tags) and `Tag` (member wristband NFC tags) store the
physical NFC chip UID in a field misleadingly named `tag_id`. The `_id` suffix implies a Django
ForeignKey column, but the value is the raw hex chip UID (e.g. `04A1B2C3`). Rename the field to
`nfc_uid` on both models and normalize stored values to **uppercase** so the value matches what
the scanner/phone emits and removes the current mixed-case inconsistency (old rows lowercase, new
rows uppercase).

Key benefits:
- Clear, honest field name aligned with the project's existing "UID" vocabulary (`TeamFinishLog.tag_uid`).
- Consistent stored casing → reliable equality lookups and stable mobile `tag_hash` matching.

## Context (from discovery)
- **Models** (`website` app, so ONE migration covers both):
  - `src/website/models/checkpoint.py` — `CheckpointTag.tag_id` (line 39), `__str__` (line 53).
  - `src/website/models/tag.py` — `Tag.tag_id` (line 6, **`unique=True`**), `__str__` (line 14).
- **External REST API** (`/api/`) — wire JSON key `tag_id` is renamed to `nfc_uid` (user chose the
  breaking change; no `source=` alias):
  - `src/api/serializers/tag.py` — `TagSerializer` (line 8), `CheckpointTagSerializer` (line 18),
    `CheckpointTagSerializer2` (line 24), `TagTouchSerializer` (line 13).
  - `src/api/views/tag.py` — `CheckpointTagCreateView` (lines 41/45/52, docstring 18),
    `MemberTagTouchView` (lines 74/79), `MemberTagListCreateView` docstring (lines 16-19).
  - `src/api/views/teams.py` — `Tag.objects.filter(tag_id__iexact=...)` (163), `tag.tag_id.upper()` (206).
- **Mobile** (`/app/`) — only ever exposes `tag_hash`, never the raw value:
  - `src/apps/mobile/serializers.py:41` — `signing.tag_hash(secret, tag.tag_id)` → `tag.nfc_uid`.
  - `src/apps/mobile/signing.py:39`, `src/apps/mobile/versioning.py` — internal arg/param named
    `tag_id` (cosmetic rename for clarity; no behavior change).
- **Other internal**:
  - `src/website/views/views_.py:128` — `tag.tag_id` where `tag = log.member_tag` (a `Tag`).
  - `src/website/views/views_.py:325-333` — `PointTagsView` writes `CheckpointTag` via
    `update_or_create(point=..., tag_id=...)`; request body key `tag_id` renamed too.
  - `src/website/admin.py:113` (`CheckpointTagAdmin`), `:215-216` (`TagAdmin`).
- **Tests**: `src/api/tests.py` (member tag create/touch, lines 14/18/28/31/37),
  `src/apps/mobile/tests.py` (many `CheckpointTag.objects.create(tag_id=...)` + `"tag_id" not in data` asserts).
- **Docs**: `CLAUDE.md` (mobile legend / `tag_hash` section), `src/apps/mobile/README.md` (lines 56-57, 353-356).

## Development Approach
- **Testing approach**: Regular (code-first). This is a rename against an existing suite; update
  tests alongside each code change and keep the suite green.
- Complete each task fully before the next; run tests after each task.
- Use **only the Edit tool** for source edits — no `sed`/python scripts (project rule).
- Run `make format && make lint` before finishing.

## Testing Strategy
- **Unit tests**: update existing `api`/`mobile` tests that reference the field/JSON key; add a
  focused test that `save()` uppercases `nfc_uid` for both models.
- No e2e/UI tests in scope (backend + API rename only).

## Progress Tracking
- Mark `[x]` immediately when done; `➕` for new tasks; `⚠️` for blockers.

## Solution Overview
- Rename the model field on both models; uppercase in an overridden `save()` so **every** write path
  (admin, shell, API, migrations going forward) is normalized — serializer/form-only normalization
  is rejected because admin/shell writes would slip through.
- One `website` migration: `RenameField` ×2 + `RunPython` backfill uppercasing existing rows
  (reverse = no-op). Guard the `Tag` backfill against the `unique=True` collision case.
- Propagate the rename through serializers, views, admin, mobile internals, tests, docs.

## Technical Details
- `save()` override (both models): `self.nfc_uid = (self.nfc_uid or "").upper()` before `super().save()`.
  Only `.upper()` — no separator stripping, no hex validation (minimal scope).
- Migration backfill: **the RunPython function gets the historical model via `apps.get_model(...)`,
  which has NO `save()` override** — so it must uppercase explicitly, not lean on the override.
  For each row: `row.nfc_uid = (row.nfc_uid or "").upper()` then save. `CheckpointTag` must save
  `update_fields=["nfc_uid", "updated_at"]` (so the legend fingerprint moves; `auto_now` is a field
  attribute and IS preserved in historical state). `Tag` has no `updated_at` → save
  `update_fields=["nfc_uid"]`.
- For `Tag` (unique), pre-check whether any two rows collide case-insensitively; if so, **abort with a
  clear error** (do not silently merge/drop) so a human resolves the duplicate first.

## Consequences (not blockers)
- Backfilling `CheckpointTag` to uppercase changes **every** mobile legend `tag_hash` and bumps the
  legend ETag / `versions.legend` — this is by design (`versioning.py` folds `MAX(updated_at)|COUNT`;
  `save()` touches `auto_now updated_at`). It likely *fixes* a latent mismatch where a phone that
  uppercases a scanned UID never matched lowercase-stored hashes.
- **Breaking API change**: `/api/` responses/requests now use `nfc_uid` instead of `tag_id`.
  Consuming clients must update (see Post-Completion).

## What Goes Where
- **Implementation Steps**: model, migration, serializers, views, admin, mobile, tests, docs — all in-repo.
- **Post-Completion**: updating the external mobile/API clients that read/write `tag_id`; deploy-order note.

## Implementation Steps

### Task 1: Rename field + add normalization on both models

**Files:**
- Modify: `src/website/models/checkpoint.py`
- Modify: `src/website/models/tag.py`

- [x] `checkpoint.py`: rename `tag_id` → `nfc_uid` (keep `verbose_name="UID тега"`), update `__str__`
- [x] `checkpoint.py`: add `save()` that uppercases `self.nfc_uid` before `super().save()`
- [x] `tag.py`: rename `tag_id` → `nfc_uid` (keep `unique=True`, `verbose_name="UID тега"`), update `__str__`
- [x] `tag.py`: add `save()` that uppercases `self.nfc_uid` before `super().save()`
- [x] add pytest-style tests (`@pytest.mark.django_db`, per CLAUDE.md) in `src/website/tests.py`
      asserting `save()` uppercases `nfc_uid` for both `CheckpointTag` and `Tag`
- [x] run tests for the touched models — deferred: the rename breaks the admin `list_display`
      system check (Task 5) which gates all `manage.py`/pytest runs, and the test DB needs the
      Task 2 migration. Models parse-check OK; full validation lands in Task 2/5/6.

### Task 2: Migration — rename columns + backfill uppercase

**Files:**
- Create: `src/website/migrations/00XX_rename_tag_id_to_nfc_uid.py` (next number after latest)

- [x] confirm latest `website` migration number (`ls src/website/migrations/`) and set `dependencies`
- [x] `migrations.RenameField` for `CheckpointTag` `tag_id`→`nfc_uid`
- [x] `migrations.RenameField` for `Tag` `tag_id`→`nfc_uid`
- [x] `RunPython` forward: get historical models via `apps.get_model`; uppercase explicitly (do NOT
      rely on the `save()` override — it doesn't exist in historical state)
- [x] `RunPython` forward: save `CheckpointTag` rows with `update_fields=["nfc_uid", "updated_at"]`;
      save `Tag` rows with `update_fields=["nfc_uid"]`
- [x] `RunPython` forward: pre-check `Tag` for case-insensitive duplicates; raise a clear error if any (don't merge)
- [x] `RunPython` reverse: no-op (`migrations.RunPython.noop`)
- [x] run `uv run python src/manage.py makemigrations --check` / apply on a scratch DB to verify it runs
      (also folded in two `AlterField`s for the `verbose_name` "ID тега"→"UID тега" change from Task 1)
- [x] verify no other app references the old field name in migrations state

### Task 3: API serializers + views (wire key → `nfc_uid`)

**Files:**
- Modify: `src/api/serializers/tag.py`
- Modify: `src/api/views/tag.py`
- Modify: `src/api/views/teams.py`

- [x] `serializers/tag.py`: `TagSerializer.fields`, `CheckpointTagSerializer.tag_id`,
      `CheckpointTagSerializer2.fields`, `TagTouchSerializer.tag_id` → `nfc_uid`
- [x] `views/tag.py`: `CheckpointTagCreateView` (validated_data key, `create(nfc_uid=...)`, response key, docstring)
- [x] `views/tag.py`: `MemberTagTouchView` (validated_data key, `Tag.objects.get(nfc_uid=...)`, error msg),
      `MemberTagListCreateView` docstring example
- [x] `views/teams.py`: `Tag.objects.filter(nfc_uid__iexact=...)`, `tag.nfc_uid.upper()`
- [x] update `src/api/tests.py` member-tag create/touch tests (request + response key `nfc_uid`).
      NOTE: this file is `APITestCase`/`TestCase` style — edit in-place, do NOT rewrite to pytest style (out of scope)
- [x] run `api` tests - must pass before next task

### Task 4: Mobile internals (no wire change — `tag_hash` only)

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/signing.py`
- Modify: `src/apps/mobile/versioning.py`

- [x] `serializers.py`: `signing.tag_hash(secret, tag.nfc_uid)`; update docstring mentions of `tag_id`
- [x] `signing.py`: rename param `tag_id` → `nfc_uid` (cosmetic; update docstring)
- [x] `versioning.py`: rename internal references/docstrings mentioning `tag_id` (no behavior change)
      — none present (uses `updated_at`/`id` aggregates only), no edit needed
- [x] update `src/apps/mobile/tests.py`: `CheckpointTag.objects.create(nfc_uid=...)` everywhere;
      keep `"tag_id" not in data` asserts and add `"nfc_uid" not in data` where relevant
- [x] run `mobile` tests (legend hashing, ETag/version) - must pass before next task

### Task 5: Remaining internal callers (website views + admin)

**Files:**
- Modify: `src/website/views/views_.py`
- Modify: `src/website/admin.py`

- [ ] `views_.py:128`: `tag.nfc_uid` (member_tag is a `Tag`)
- [ ] `views_.py` `PointTagsView`: `update_or_create(point=..., nfc_uid=...)` and request body key
      `data.get("nfc_uid")` + messages
- [ ] `admin.py`: `CheckpointTagAdmin.list_display` `tag_id`→`nfc_uid`; `TagAdmin.list_display` +
      `search_fields` `tag_id`→`nfc_uid`
- [ ] grep for any remaining `\btag_id\b` referencing these two models; fix stragglers
- [ ] run full suite - must pass before next task

### Task 6: Verify acceptance criteria
- [ ] `grep -rn "\btag_id\b" src/ | grep -v migrations` shows only unrelated hits (`member_tag_id` PK
      field, `member_tag__` lookups). **Do NOT edit historical migration files** (`0029`/`0030`/`0040`
      legitimately reference the old `tag_id` and must stay frozen)
- [ ] `make format && make lint` clean
- [ ] `uv run pytest` green (full suite)
- [ ] manually confirm migration applied + backfill uppercased rows on a scratch/dev DB

### Task 7: [Final] Update documentation
- [ ] `CLAUDE.md`: update mobile legend / `tag_hash` / `update_fields` mentions of `tag_id` → `nfc_uid`
- [ ] `src/apps/mobile/README.md`: lines ~56-57, ~353-356 `tag_id` → `nfc_uid`
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only*

**External system updates**:
- The mobile/desktop clients consuming `/api/` must switch the JSON key `tag_id` → `nfc_uid`
  (member-tag create/touch, checkpoint tags list, point-tag upload). Coordinate the deploy so the
  app release lands together with this change, or version the endpoint if simultaneous cutover isn't possible.
- Mobile legend `tag_hash` values change after the uppercase backfill; clients relying on cached
  hashes will re-fetch via the bumped ETag / `versions.legend` (expected).

**Manual verification**:
- On dev DB after migrate: confirm `Tag` had no case-insensitive duplicates (migration would have aborted),
  and that `CheckpointTag`/`Tag` rows are all uppercase.
