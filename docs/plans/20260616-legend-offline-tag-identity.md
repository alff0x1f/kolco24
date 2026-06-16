# Legend offline tag identity (`bid → point`)

## Overview
Restore the mobile app's ability to identify **which checkpoint (КП) was scanned** while
fully **offline**, for *every* checkpoint — locked **and** open.

The legend-encryption work removed `tag_hash` (a hash of `nfc_uid`) from the
`/app/race/<id>/legend/` response. Before, the offline app could do
`scan → nfc_uid → hash → match tag_hash → cp_id` for any КП. Now the only
tag→КП link in the legend is **indirect and locked-only**: the per-tag `bundles`
(`code → bid → bundle → {cp_id: content_key}`). And a bundle is a **key-distribution**
mechanism, not an **identity** one — when a tag's `unlocks` set has several КП, the
bundle hands out keys for all of them, so it cannot say *which* КП the participant is
physically standing at. Open КП have **no** bundle, so there is no offline scan→cp_id
mapping for them at all.

The race scenario is **offline**: the phone must resolve a scan to a `cp_id` by itself,
without the server, for all checkpoints.

**Solution (brainstorm Variant A):** keep a single on-the-wire identifier — `code` — and
expose, for **every** tag, its own `cp_id` (`point`) next to `bid`. This splits the two
concerns cleanly:
- **identity** = `bid → point` (1:1, always present, including open КП);
- **unlock** = `bid → bundle` (`iv`/`ct`, locked КП only).

`unlocks` stays purely "which legends this code reveals". The raw `code`/`nfc_uid` never
travel on the wire (README invariant preserved).

## Context (from discovery)
Files/components involved:
- `src/apps/mobile/serializers.py` — `BundleSerializer` (becomes `TagSerializer`).
- `src/apps/mobile/views.py` — `LegendView.get` (response field `bundles` → `tags`,
  drop `.exclude(bundle_blob=None)`, hidden-legend empty `tags`).
- `src/apps/mobile/management/commands/export_legend_codes.py` — already dumps **all**
  race tags (filter is `point__race_id` only); confirm with a test, no code change expected.
- `src/apps/mobile/versioning.py` — **no change**; `legend_state` already aggregates
  `CheckpointTag` over all non-draft-КП tags (no `bundle_blob` filter).
- `src/apps/mobile/tests.py` — field-set + end-to-end tests reference `data["bundles"]`.
- `src/apps/mobile/README.md`, root `CLAUDE.md` — docs.

Related patterns found:
- `LegendCheckpointSerializer` branches locked vs open (`{id,number,type,enc}` vs
  `{...,cost,description}`) — `tags` mirrors this split for `iv`/`ct`.
- `CheckpointTag` already has `point_id`, and `code`/`bid` are computed for every tag in
  `legend_crypto.build_bundle` (open tags just get `bundle_blob=None`).
- Tests are pytest-style functions with `@pytest.mark.django_db`.

Dependencies identified:
- **No migration** — `point_id`, `code`, `bid` already exist on `CheckpointTag`.
- Out of scope: the offline visit buffer + an upload/scoring endpoint (today
  `api.CheckpointTagCreateView` is disabled). This change only provides the `bid → point`
  map for offline `cp_id` recognition.

## Development Approach
- **testing approach**: Regular (code first, then tests) — matches the existing
  pytest-function suite in `src/apps/mobile/tests.py`.
- complete each task fully before moving to the next; small, focused changes.
- **every task includes new/updated tests** (success + edge cases).
- **all tests must pass before starting the next task.**
- run `uv run pytest src/apps/mobile/tests.py` after each change; `uv run pytest` before
  finishing.
- `make format && make lint` before committing (project rule).
- maintain the response invariants: hidden legend → empty payload + ETag on every exit
  path; raw `code`/`nfc_uid` never serialized.

## Testing Strategy
- **unit tests**: required for every task. Serializer-level (open tag → `{bid, point,
  check_method}` no `iv/ct`; locked tag → also `iv/ct`) and view-level (`tags` array
  shape, hidden → empty `tags`, end-to-end offline decrypt still works via `tags`).
- **e2e tests**: project has no UI e2e harness for this server endpoint — N/A.
- Versioning regression: confirm `legend_version` already moves when an **open** КП tag is
  added (likely covered by `test_legend_version_changes_when_tag_added_and_removed`;
  add an open-КП-specific assertion if not).

## Progress Tracking
- mark completed items `[x]` immediately when done.
- add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.
- keep this file in sync with actual work.

## Solution Overview
Single response array `tags`, one entry per physical `CheckpointTag`:

```json
{
  "race": 42,
  "checkpoints": [
    {"id": 153, "number": 1, "type": "kp", "enc": {"iv": "...", "ct": "..."}},
    {"id": 154, "number": 2, "type": "kp", "cost": 4, "description": "Дерево у дороги"}
  ],
  "tags": [
    {"bid": "a1b2...", "point": 153, "check_method": "offline", "iv": "...", "ct": "..."},
    {"bid": "c3d4...", "point": 154, "check_method": "offline"}
  ]
}
```

Rules:
- `point` (= `point_id`) and `bid` and `check_method` — **always** present.
- `iv`/`ct` — present **only** when the tag has a `bundle_blob` (locked-КП unlock).
- merged `bundles` → `tags` (one array; `bid` is not duplicated across two arrays).

Design decisions / rationale:
- **Identity via `code`, not `nfc_uid`** — one identifier end to end; raw `nfc_uid` stays
  off the wire. `code` is written into **all** tags' NFC user memory (operator choice),
  so `bid = sha256(code)[:16]` resolves for open and locked КП alike.
- **No migration** — identity data already in DB; we stop hiding it
  (drop `.exclude(bundle_blob=None)`).
- **Versioning untouched** — `legend_state`'s `CheckpointTag` aggregate already spans all
  non-draft-КП tags, so adding open tags to the body needs no fingerprint change.

## Technical Details
- `TagSerializer` (rename of `BundleSerializer`):
  - `bid = CharField()`
  - `point = IntegerField(source="point_id")`
  - `check_method = CharField()`
  - `iv` / `ct` = `SerializerMethodField` returning `(tag.bundle_blob or {}).get(...)`
    (→ `None` for open tags). Keep `None` rather than omitting the key (simpler, stable
    shape; the app treats absent/`None` `iv`/`ct` as "identity-only, not decryptable").
- `LegendView.get`:
  - hidden branch: `{"race": race_id, "checkpoints": [], "tags": []}` (ETag unchanged).
  - tag queryset: drop `.exclude(bundle_blob=None)`; **add `.exclude(bid="")`**; keep
    `.filter(point__race_id=race_id).exclude(point__type=draft).order_by("id")`.
  - response key `bundles` → `tags`; serializer `BundleSerializer` → `TagSerializer`.
- **Un-built tag edge case:** `CheckpointTag` defaults are `code=None`, `bid=""` (model
  defaults). Every tag is normally run through `build_bundle` by the `post_save` signal, so
  `bid` is populated — but a row created bypassing signals (legacy/import/incomplete repair)
  would otherwise serialize as a useless `{"bid": "", ...}` entry the client cannot match.
  `.exclude(bid="")` keeps such junk off the wire (cheap, no false negatives — a real tag
  always has a non-empty `bid`).
- `bid` collision space = 64 bits over tens of tags/race → negligible.

## What Goes Where
- **Implementation Steps** (`[ ]`): serializer, view, command-confirmation test, suite
  test updates, docs.
- **Post-Completion** (no checkboxes): the offline visit buffer + upload/scoring endpoint
  (separate task); field provisioning of `code` into every physical tag; mobile-app client
  changes consuming `tags`/`point`.

## Implementation Steps

### Task 1: `TagSerializer` — per-tag identity + optional unlock

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] rename `BundleSerializer` → `TagSerializer`; update its docstring to describe
      identity (`bid → point`, always) vs unlock (`iv`/`ct`, locked only).
- [ ] add `point = serializers.IntegerField(source="point_id")`.
- [ ] keep `bid`, `check_method`; keep `iv`/`ct` as `SerializerMethodField` returning
      `None` when `bundle_blob` is falsy.
- [ ] write serializer test: **open** tag (`bundle_blob=None`) → `{bid, point,
      check_method}` with `iv is None`, `ct is None`.
- [ ] write serializer test: **locked** tag → same fields **plus** non-null `iv`/`ct`.
- [ ] run `uv run pytest src/apps/mobile/tests.py` — must pass before Task 2.

### Task 2: `LegendView` — emit `tags` for all tags; hidden → empty `tags`

**Files:**
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] import `TagSerializer` (was `BundleSerializer`).
- [ ] visible branch: drop `.exclude(bundle_blob=None)`, **add `.exclude(bid="")`** to the
      tag queryset; rename the local `bundle_qs` → `tag_qs`; response key `"bundles"` →
      `"tags"` using `TagSerializer`.
- [ ] hidden branch: change `"bundles": []` → `"tags": []` (ETag handling unchanged).
- [ ] re-grep `grep -n 'data\["bundles"\]' src/apps/mobile/tests.py` (suite shifts; don't
      trust line numbers) and update **every** site `data["bundles"]` → `data["tags"]`
      (today ≈ lines 480, 643, 670, 856 plus the end-to-end / build-independent /
      hidden-with-tags tests). Note: `test_legend_excludes_draft_checkpoints` (≈ 670)
      creates a tag on a **draft** КП — `.exclude(point__type=draft)` still drops it, so
      `data["tags"] == []` stays correct there.
- [ ] add view test: response `tags` includes an **open**-КП tag entry with `point` and
      **no** decryptable `iv`/`ct` (`iv is None`).
- [ ] add view test: a tag with no `code`/`bid` (`bid=""`, created bypassing signals) is
      **excluded** from `tags`.
- [ ] add view test: hidden legend → `data["tags"] == []`.
- [ ] adapt `test_legend_end_to_end_scan_code_decrypts_locked_checkpoint` to locate the
      entry in `tags` by `bid`, assert its `point` equals the locked КП id, then run the
      existing HKDF → bundle → content_key → `enc` decrypt path.
- [ ] run `uv run pytest src/apps/mobile/tests.py` — must pass before Task 3.

### Task 3: Confirm `export_legend_codes` dumps every tag (incl. open КП)

**Files:**
- Modify: `src/apps/mobile/tests.py`
- (Modify only if needed: `src/apps/mobile/management/commands/export_legend_codes.py`)

- [ ] verify the command's queryset filters by `point__race_id` only (no locked/
      `bundle_blob` filter) — expected already true; change only if a filter is present.
- [ ] write a test invoking the command (e.g. via `call_command` capturing stdout) on a
      race with one **open** and one **locked** КP tag: assert **both** `nfc_uid`s and
      their `code` hex appear in the output.
- [ ] run `uv run pytest src/apps/mobile/tests.py` — must pass before Task 4.

### Task 4: Versioning regression — open-КП tag moves `legend_version`

**Files:**
- Modify: `src/apps/mobile/tests.py`

- [ ] confirm/add a `legend_version` test: adding a tag to an **open** КП changes the
      fingerprint (guards that `legend_state`'s `CheckpointTag` aggregate already spans
      open-КП tags, so the new `tags` body can never go stale). No `versioning.py` change.
- [ ] run `uv run pytest src/apps/mobile/tests.py` — must pass before Task 5.

### Task 5: Verify acceptance criteria
- [ ] response carries `tags` (not `bundles`); each entry has `bid`, `point`,
      `check_method`; locked entries additionally carry `iv`/`ct`; open entries do not.
- [ ] hidden legend → empty `tags`; ETag still set on every exit path; 304 path unchanged.
- [ ] offline end-to-end: `bid → point` resolves cp_id; locked decrypt still works.
- [ ] raw `code`/`nfc_uid` never appear in any response body.
- [ ] run full suite: `uv run pytest`.
- [ ] `make format && make lint`.

### Task 6: [Final] Update documentation
- [ ] `src/apps/mobile/README.md`: re-grep `grep -n bundles src/apps/mobile/README.md` and
      update **all** sites, not just «Что отдаётся» (today ≈ lines 58-59, 86, 127-128, 139
      JSON example, 416 endpoints table, 429 deep-dive `mobile:legend` note): `bundles` →
      `tags`, add `point`, explain the identity (`bid → point`, always) vs unlock (`iv`/`ct`,
      locked only) split — especially the «Шифрование легенды» intro (≈ 58-59) that
      currently calls `bundles` the only tag→КП link; note `code` is written into **all**
      tags for offline КП recognition.
- [ ] root `CLAUDE.md` apps.mobile section: `BundleSerializer` → `TagSerializer`; response
      field `tags` with `point`; `tag_hash` stays removed but offline identity is now
      `bid → point` for every tag (open + locked); legend tag queryset no longer excludes
      `bundle_blob=None`.
- [ ] move this plan to `docs/plans/completed/`.

## Post-Completion
*Items requiring manual intervention or external systems — informational only.*

**External system updates:**
- **Mobile app client**: consume `tags` (not `bundles`); on scan, compute
  `bid = sha256(code)[:16]`, look up `tags[].bid` → `point` (cp_id) for identity; if the
  entry has `iv`/`ct`, run the existing offline unlock to reveal `cost`/`description`.
- **Field provisioning**: write each tag's `code` (from `export_legend_codes`) into the
  NFC user memory of **every** physical tag, including open-КП tags (a blank tag exposes
  only `nfc_uid`, which this scheme intentionally does not use for identity).

**Separate follow-up task (NOT this plan):**
- Offline **visit recording**: buffer `(cp_id, timestamp)` on the phone and sync when a
  server is reachable; add/enable an upload/scoring endpoint in `api`
  (`CheckpointTagCreateView` is currently disabled) with dedup + scoring. This change only
  supplies the `bid → point` identity map; it records nothing.
