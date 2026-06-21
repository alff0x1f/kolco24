# Rename `CheckpointTag.point` → `checkpoint` + unify mobile API `checkpoint_id` wire vocabulary

## Overview

The `CheckpointTag` FK to `Checkpoint` is named `point` (shorthand for "control
point" / КП). DRF serializes a `point` FK as the related PK, so on the wire
`point` means **checkpoint id** in the tag-create request and in the legend
`tags[]`, but the tag-create **response** deliberately overloads the same key
`point` with `cp.number` (the human-readable number). One JSON key, two meanings.
The legacy `api` tag-create endpoint uses the *inverted* convention (request
`number`, response `point` = id), so the two implementations contradict each
other.

This change renames the model field `point` → `checkpoint` (Django-idiomatic: FK
named after its model) and establishes a single wire vocabulary across the whole
`/app/` API: a checkpoint's stable id is always `checkpoint_id`, its display
number is always `number`. No key is ever overloaded.

**Benefits:** removes the id-vs-number ambiguity that the handoff doc had to warn
about twice; aligns the model field with the `Checkpoint` model and the `unlocks`
M2M (which also targets `Checkpoint`); fixes the legacy api resolve-by-`number`
footgun (`number` is not unique per race) by resolving by id.

The contract has **not** shipped to mobile devs yet (`docs/mobile-admin-auth-and-tags.md`
was written but not committed), so changing it now is cheap.

## Context (from discovery)

- **Model:** `src/website/models/checkpoint.py:67` — `CheckpointTag.point =
  ForeignKey("website.Checkpoint", related_name="tags")`. Also `unlocks` M2M
  (`related_name="unlocked_by"`) — **not touched** (separate relation).
- **Latest migration:** `src/website/migrations/0089_checkpointtag_nfc_uid_unique.py`
  → new migration is `0090`. The old `unique_together("point","nfc_uid")` was
  already removed in `0089`, so a `RenameField` has no constraint referencing the
  old name to trip over.
- **~65 references** to `.point` / `point=` / `point_id` / `point__` across 16
  files (excluding migrations): `src/website/admin.py`, `src/website/tests.py`,
  `src/website/models/checkpoint.py`, `src/website/models/models.py`,
  `src/website/views/views_.py`, `src/api/views/tag.py`,
  `src/apps/mobile/signals.py`, `src/apps/mobile/legend_crypto.py`,
  `src/apps/mobile/serializers.py`, `src/apps/mobile/versioning.py`,
  `src/apps/mobile/views.py`, `src/apps/mobile/tests.py`,
  `src/apps/mobile/management/commands/export_legend_codes.py`,
  `src/apps/mobile/management/commands/rebuild_legend_crypto.py`,
  `src/apps/race/views.py`, `src/apps/race/tests.py`.
- **Wire definitions:**
  - `src/apps/mobile/serializers.py` — `TagCreateSerializer.point` (request),
    `TagSerializer.point = IntegerField(source="point_id")` (legend identity).
  - `src/apps/mobile/views.py` — `TagCreateView._tag_response` builds
    `{bid, point: tag.point.number, ...}`; `TagCreateView.post` reads
    `validated_data["point"]`.
  - `src/api/serializers/tag.py` — `CheckpointTagSerializer.number` (request).
  - `src/api/views/tag.py` — `CheckpointTagCreateView` resolves by `number`
    (`get_control_point`), responds `{point: tag.point.id, ...}`. Superuser-only,
    flagged "выключено, тк эндпоинт пока не используется".
- **Schema-version cache bust:** `src/apps/mobile/versioning.py` —
  `_LEGEND_SCHEMA_VERSION = 2` is folded into `legend_version()`; changing the
  legend `tags[]` shape requires a bump to 3.

## Development Approach

- **Testing approach:** Regular (rename refactor — update affected tests inside
  the same task as the code they cover).
- Complete each task fully before the next; run the suite after each.
- The model rename is atomic by nature: Task 1 must update **every** Python
  reference (source *and* tests) in one pass, or the project won't import. Wire
  key changes (JSON keys) are split into later, independently-green tasks.
- **Every task updates its tests before moving on; all tests must pass before the
  next task.**
- Use **Edit only** for source changes — no `python3`/`sed`/scripts (user rule:
  scripts are blind to surrounding context).
- `make format && make lint` before any commit; `uv run pytest` for the full run.
- Do **not** commit without an explicit request.

## Testing Strategy

- **Unit tests:** pytest-style functions with `@pytest.mark.django_db` (project
  convention — not `TestCase` subclasses). Update ORM attribute references
  (`.point` → `.checkpoint`) and JSON-key assertions (`point` → `checkpoint_id`,
  add `number` to the tag-create response).
- The mobile throttle tests rely on the autouse `_clear_throttle_cache` fixture —
  do not remove it.
- **No e2e suite** in this project (Django + DRF + pytest only).
- `uv run pytest --reuse-db` for fast iteration; full `uv run pytest` at the end.

## Progress Tracking

- Mark completed items `[x]` immediately.
- New tasks: `➕` prefix. Blockers: `⚠️` prefix.
- Keep this file in sync if scope shifts.

## Solution Overview

1. **Model:** `RenameField` migration `point` → `checkpoint`; `related_name="tags"`
   unchanged (`Checkpoint.tags` stays). All ORM/attribute references updated.
2. **Single wire vocabulary** across `/app/`:
   - tag-create request `{checkpoint_id, nfc_uid}`
   - tag-create response `{bid, checkpoint_id, number, nfc_uid, code}` (both ids,
     each under an honest key)
   - legend `tags[]` `checkpoint_id` (was `point`), `_LEGEND_SCHEMA_VERSION` 2→3
3. **Legacy api** aligned to the same vocabulary and to resolve-by-id.
4. **Docs** updated (handoff doc + CLAUDE.md).

## Technical Details

- `migrations.RenameField("checkpointtag", "point", "checkpoint")` — column
  rename only, no data migration. Depends on `0089`.
- DRF: `TagSerializer.checkpoint_id = IntegerField(source="checkpoint_id")` — after
  the model rename the attribute is `checkpoint_id`; `source` can be dropped if the
  field name matches (`checkpoint_id` field reads `tag.checkpoint_id` natively).
- `TagCreateView` reads `validated_data["checkpoint_id"]` and responds
  `{"bid", "checkpoint_id": cp.id, "number": cp.number, "nfc_uid", "code"}`.
- Legacy `CheckpointTagCreateView`: resolve
  `Checkpoint.objects.exclude(type=hidden).get(id=checkpoint_id, race_id=race_id)`
  (404 on miss), respond with the same key set as mobile for consistency.

## What Goes Where

- **Implementation Steps** (checkboxes): model rename + migration, all code
  references, wire contracts (mobile tag-create, mobile legend, legacy api),
  tests, docs.
- **Post-Completion** (no checkboxes): mobile-client coordination (they consume
  the new keys), deploy-time legend cache bust verification, `makemigrations
  --check` in CI.

## Implementation Steps

### Task 1: Rename model field + migration + all internal ORM references

> **Atomicity note (the ORM-vs-wire split).** Renaming the field is atomic at the
> ORM layer: **every** `source="point_id"`, `point__…` lookup, `filter(point=…)`,
> and `tag.point`/`tag.point_id` accessor breaks the instant the field moves, so
> they **all** move in this task — including the ones inside `views.py` and
> `serializers.py`. What is **deferred** to Tasks 2/3 is only the **JSON key
> name** the client sees: keep the response dict key `point` and the
> `TagSerializer` *field name* `point` here (change only its `source=`), and keep
> `TagCreateSerializer.point` / `validated_data["point"]` here (rename to
> `checkpoint_id` in Task 2). After this task the wire shape is byte-for-byte
> unchanged; only the internal attribute moved.

**Files:**
- Modify: `src/website/models/checkpoint.py`
- Create: `src/website/migrations/0090_rename_checkpointtag_point_checkpoint.py`
- Modify: `src/website/admin.py`, `src/website/views/views_.py`
- Modify: `src/apps/mobile/signals.py`, `src/apps/mobile/legend_crypto.py`,
  `src/apps/mobile/versioning.py`, `src/apps/mobile/views.py`,
  `src/apps/mobile/serializers.py`,
  `src/apps/mobile/management/commands/export_legend_codes.py`,
  `src/apps/mobile/management/commands/rebuild_legend_crypto.py`
- Modify: `src/apps/race/views.py`
- Modify: `src/website/tests.py`, `src/apps/race/tests.py`
- Verify-only (no `CheckpointTag.point` refs — do **not** edit):
  `src/website/models/models.py` (its `point.point` at ~line 277 is a `TakenKP`
  loop variable, unrelated to this FK)

- [x] rename `CheckpointTag.point` → `checkpoint` in `checkpoint.py` (keep
      `verbose_name="КП"`, `on_delete=CASCADE`, `related_name="tags"`); leave
      `unlocks`/`unlocked_by` untouched
- [x] create migration `0090` with `migrations.RenameField("checkpointtag",
      "point", "checkpoint")`; verify `uv run python src/manage.py makemigrations
      --check` reports no further changes
- [x] update every ORM/attribute reference `point__` / `point=` / `tag.point` /
      `tag.point_id` → `checkpoint*` across the source files above (signals
      `cp.tags`/`unlocked_by` related_names are unchanged; only the forward
      accessor moves) — Edit only, file by file
- [x] `serializers.py`: `TagSerializer` — change **only** `source="point_id"` →
      `source="checkpoint_id"`; **keep the field name `point`** (the wire key
      moves in Task 3). Leave `TagCreateSerializer.point` as-is (moves in Task 2)
- [x] `views.py`: `LegendView` queryset `point__race_id`/`point__type` →
      `checkpoint__…`; `TagCreateView` `tag.point_id`/`filter(point=cp)`/
      `CheckpointTag(point=cp,…)` → `checkpoint*`; **keep** the response dict key
      `"point": tag.checkpoint.number` and `validated_data["point"]` (move in Task 2)
- [x] update `.point` ORM/attribute references in `src/website/tests.py` and
      `src/apps/race/tests.py`; legend `tags[].point` / tag-create response
      `point` **JSON-key assertions stay as-is** (keys unchanged in this task)
- [x] run `uv run pytest` — suite green before Task 2 (wire shape unchanged;
      only the internal attribute moved)

### Task 2: Mobile tag-create wire contract (`checkpoint_id` + `number`)

**Files:**
- Modify: `src/apps/mobile/serializers.py` (`TagCreateSerializer`)
- Modify: `src/apps/mobile/views.py` (`TagCreateView`)
- Modify: `src/apps/mobile/tests.py`

- [x] `TagCreateSerializer`: rename request field `point` → `checkpoint_id`
      (`IntegerField`); update docstring (it documents "point is the checkpoint id")
- [x] `TagCreateView.post`: read `validated_data["checkpoint_id"]`; keep
      resolve-by-id within race + `exclude(type=hidden)` logic
- [x] `TagCreateView._tag_response`: emit `{"bid", "checkpoint_id": tag.checkpoint_id,
      "number": tag.checkpoint.number, "nfc_uid", "code"}` (both ids, honest keys);
      update the method docstring
- [x] update tag-create tests: request body uses `checkpoint_id`; assert response
      carries `checkpoint_id` (= id) **and** `number` (= number); cover 201 / 200
      idempotent / 409 / 404 / 400 paths
- [x] run `uv run pytest src/apps/mobile/tests.py` — must pass before Task 3

### Task 3: Mobile legend wire contract + schema-version bump

**Files:**
- Modify: `src/apps/mobile/serializers.py` (`TagSerializer`)
- Modify: `src/apps/mobile/versioning.py` (`_LEGEND_SCHEMA_VERSION`)
- Modify: `src/apps/mobile/tests.py`

- [x] `TagSerializer`: rename legend identity key `point` → `checkpoint_id`
      (reads `tag.checkpoint_id`); update docstring (the `bid → point` identity
      wording becomes `bid → checkpoint_id`)
- [x] bump `_LEGEND_SCHEMA_VERSION` 2 → 3 in `versioning.py` (forces legend
      ETag / `versions.legend` cache bust on the response-shape change); update
      the inline comment noting the bump reason
- [x] update legend tests: assert `tags[]` entries carry `checkpoint_id`;
      assert the legend ETag/version changed vs the pre-bump fingerprint if such
      a test exists (none asserts a hardcoded fingerprint — all are relative
      before/after comparisons, unaffected by the bump)
- [x] run `uv run pytest src/apps/mobile/tests.py` — must pass before Task 4

### Task 4: Legacy api tag-create — align vocabulary + resolve by id

**Files:**
- Modify: `src/api/serializers/tag.py` (`CheckpointTagSerializer`)
- Modify: `src/api/views/tag.py` (`CheckpointTagCreateView`)
- Modify: api tag tests (in `src/api/` test module)

- [x] `CheckpointTagSerializer`: replace `number` field with `checkpoint_id`
      (`IntegerField`)
- [x] grep `get_control_point` to confirm `CheckpointTagCreateView` is its only
      caller before changing its resolution semantics
- [x] `CheckpointTagCreateView`: resolve `Checkpoint` by **id** within race +
      `exclude(type=hidden)` (replace `get_control_point(race_id, number)` by-number
      lookup → by-id, 404 on miss); respond `{checkpoint_id: tag.checkpoint_id,
      number: tag.checkpoint.number, nfc_uid, ...}` matching the mobile key set
      (was `{point: tag.point.id, ...}`); keep the `IntegrityError` → 409/200
      re-query handling
- [x] update api tag tests: request uses `checkpoint_id`; response key set
      matches; cover the cross-КП 409 and idempotent 200 paths
- [x] run `uv run pytest src/api/` — must pass before Task 5

### Task 5: Documentation

**Files:**
- Modify: `docs/mobile-admin-auth-and-tags.md`
- Modify: `CLAUDE.md`

- [ ] `docs/mobile-admin-auth-and-tags.md` §4 «Привязка тега»: request
      `{checkpoint_id, nfc_uid}`, response `{bid, checkpoint_id, number, nfc_uid,
      code}`; update the request/response tables and the curl example
- [ ] remove **both** ⚠️ id-vs-number warnings (§4 and §8) — no longer applicable;
      update §8 «Частые ошибки» (drop the "`point` перепутан" bullet)
- [ ] update legend description if it references the `tags[]` `point` key →
      `checkpoint_id`
- [ ] `CLAUDE.md`: update the `apps.mobile` per-person-write-layer and legend
      sections that state «`point` is the checkpoint `id`, not `number`» to the
      new `checkpoint_id` vocabulary; note the model field is now
      `CheckpointTag.checkpoint`
- [ ] (no automated tests for docs — verify links/anchors render)

### Task 6: Verify acceptance criteria

- [ ] grep confirms no stale `CheckpointTag` FK refs remain: search `point__` /
      `point=` / `\.point\b` / `point_id`, then exclude the known-unrelated
      survivors (`point_number`, `point.point` `TakenKP` loop var, `points_`,
      `endpoint`, `control_point`). Cross-check positively by grepping
      `checkpoint__` / `\.checkpoint\b` to confirm the refs actually moved (proving
      presence beats proving absence)
- [ ] no JSON key `point` remains in any `/app/` or `/api/` tag/legend response
- [ ] `uv run python src/manage.py makemigrations --check` is clean
- [ ] run full suite: `uv run pytest`
- [ ] `make format && make lint` clean

### Task 7: Finalize documentation

- [ ] confirm `CLAUDE.md` reflects the rename + new wire vocabulary
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention or external systems — informational only.*

**Mobile client coordination:**
- The iOS/Android client must switch request/response keys to `checkpoint_id`
  (+ read `number` from the tag-create response) and the legend `tags[]` parser
  to `checkpoint_id`. The HMAC signing contract is unchanged — only JSON keys move.
- The `_LEGEND_SCHEMA_VERSION` bump invalidates cached legend ETags on first
  fetch after deploy; clients will re-download the legend once (expected).

**Deploy / CI:**
- Verify `makemigrations --check` runs in CI so the `0090` rename is enforced.
- The `RenameField` is a pure column rename (no data migration) — safe to apply
  online; no downtime expected.
