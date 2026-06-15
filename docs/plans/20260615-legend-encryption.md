# Legend Encryption (cost/description) for the Mobile App

## Overview

The mobile read-only app (`apps/mobile`) downloads a race's checkpoint legend ahead
of time. Today it serves every checkpoint's `cost`/`description` in cleartext, so a
participant can read the whole legend without visiting any control point (КП).

This feature encrypts the legend of **locked** checkpoints so the app can only decrypt
a КП's `cost`/`description` **after physically scanning that КП's NFC code** — fully
offline. Encryption is selective (per-КП flag), and a single NFC code can unlock a
**configurable subset** of КП (overlaps allowed), via envelope encryption.

**Threat model:** the adversary is the app itself in a participant's hands. A DB leak is
**out of scope** — `cost`/`description` may stay in cleartext in the DB. Therefore there
is **no at-rest encryption and no master key**; we encrypt only what leaves the server.

Prototype of the scheme: `scratch/playground.py`.

## Context (from discovery)

- Files/components involved:
  - `src/website/models/checkpoint.py` — `Checkpoint`, `CheckpointTag` (models live here; migrations in `website`).
  - `src/apps/mobile/views.py` — `LegendView`, `SyncView`.
  - `src/apps/mobile/serializers.py` — `LegendCheckpointSerializer`, `LegendTagSerializer`.
  - `src/apps/mobile/versioning.py` — `legend_state`, `legend_version` (ETag / `versions.legend`).
  - `src/apps/mobile/signing.py` — `tag_hash` (to be dropped from the legend path).
  - `src/apps/mobile/apps.py` — `MobileConfig` (signal registration in `ready()`).
  - `src/website/admin.py` — `CheckpointTagAdmin`, checkpoint admin.
  - `src/apps/mobile/tests.py`, `src/apps/mobile/README.md`, `CLAUDE.md`.
- Patterns found:
  - Conditional GET with a **strong ETag on every exit path**; `If-None-Match` → `304`.
  - `versioning.py` is the single source of truth for both the ETag and the `sync` manifest.
  - `tag_hash` is currently **per-build** (HMAC keyed by the build secret), so the legend
    fingerprint folds in `key_id`. After this change the legend becomes **build-independent**.
  - pytest-style tests (`@pytest.mark.django_db`, `client`/`django_user_model` fixtures).
- Dependencies identified:
  - `cryptography` (AES-256-GCM, HKDF) — installed (48.0.0) but **not a direct dependency**; add it.
  - The `api` app's КП-scanning/scoring path uses `nfc_uid` — **left untouched**.

## Development Approach

- **Testing approach**: Regular (code first, then tests) — matches the repo convention
  (pytest-style functions in `src/<app>/tests.py`).
- Complete each task fully before moving to the next; make small, focused changes.
- **Every task includes new/updated tests** (success + error/edge cases) as separate checklist items.
- **All tests must pass before starting the next task.**
- Run `make format && make lint` before committing (project rule).
- Keep the `api`/scoring `nfc_uid` path and the `teams`/`races` version paths unchanged.

## Testing Strategy

- **Unit tests**: crypto primitives, service layer, signals, management commands, versioning.
- **Integration tests**: `LegendView` end-to-end (scan code → decrypt), `SyncView` manifest, ETag/304.
- No UI e2e (the mobile app binary is external; the server side is covered by the field-set + behavior tests in `apps/mobile/tests.py`).

## Progress Tracking

- Mark completed items `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.
- Keep this plan in sync with actual work.

## Solution Overview

Envelope encryption (per `scratch/playground.py`):

1. Each **locked** КП is sealed with its own random `content_key` (AES-256-GCM over
   `{cost, description}`, `aad = str(cp_id)`). `id`/`number`/`type` stay public.
2. Each NFC tag carries a random high-entropy `code` (`os.urandom(16)`) written to its
   user memory. The server stores a per-tag **bundle**: AES-GCM of `{cp_id: content_key}`
   over the tag's `unlocks` set, wrap key `= HKDF(code)`, `aad = bid` where
   `bid = sha256(code)[:16]`.
3. App flow (offline): read `code` → `bid = sha256(code)[:16]` → find bundle by `bid` →
   `HKDF(code)` decrypts the bundle → `{cp_id: content_key}` → decrypt each locked КП's `enc`.

The envelope (content-key indirection) is what enables "code → subset of КП" with
overlaps (one КП reachable from several codes; one code opening several КП) without
re-encrypting a КП per code.

Ciphertext + bundles are **precomputed and stored** in the DB so ETags stay stable.
`content_key` and `code` are stored raw (DB is trusted). Triggers keep stored blobs
consistent via **signals**, with a **management command** for backfill/repair (bulk ops
bypass signals).

## Technical Details

### Data model (in `src/website/models/checkpoint.py`; migrations in `website`)

- **`CheckpointSecret`** (new, `OneToOneField → Checkpoint`, `related_name="secret"`):
  - `content_key = models.BinaryField()` — 32 raw bytes, generated once.
  - `enc_blob = models.JSONField()` — `{"iv": b64, "ct": b64}` (AES-GCM of `{cost, description}`).
  - `updated_at = models.DateTimeField(auto_now=True)`.
  - Exists **only** for locked КП.
- **`Checkpoint`** += `is_legend_locked = models.BooleanField(default=False)`.
- **`CheckpointTag`** += :
  - `code = models.BinaryField(null=True, blank=True)` — raw random code (16 B).
  - `bid = models.CharField(max_length=16, blank=True, default="")` — **16 hex chars**: `hashlib.sha256(code).hexdigest()[:16]` (matches `scratch/playground.py:94`). The mobile client MUST compute it identically (Post-Completion). Not `sha256(code)[:16]` raw bytes (that is 16 B → 32 hex → would overflow `max_length=16` and never match).
  - `bundle_blob = models.JSONField(null=True, blank=True)` — `{"iv": b64, "ct": b64}`.
  - `unlocks = models.ManyToManyField("website.Checkpoint", related_name="unlocked_by", blank=True)`.
    M2M has no DB default; **runtime default** in `build_bundle`: empty `unlocks` ⇒ treat as `[point]`.

### Crypto primitives (`src/apps/mobile/crypto.py`, new — ported from playground)

- `seal(key: bytes, plaintext: bytes, aad: bytes) -> dict` → `{"iv": b64, "ct": b64}` (AES-256-GCM, 12-B IV).
- `unseal(key: bytes, enc: dict, aad: bytes) -> bytes` — **not** named `open` (would shadow the builtin and trip `flake8`).
- `derive_wrap_key(code: bytes) -> bytes` — HKDF-SHA256, length 32, `info=b"kp-wrap-v1"` (high-entropy code → HKDF is enough, no Argon2).
- No `wrap_at_rest`/master key. `enc_blob`/`bundle_blob` store only `{"iv", "ct"}` — the playground's `{"v","alg"}` envelope tags are intentionally dropped (single fixed format; YAGNI for this threat model).

### Service layer (`src/apps/mobile/legend_crypto.py`, new)

- `seal_checkpoint(cp)`: if `not cp.is_legend_locked` → delete `CheckpointSecret` if present and return;
  else `get_or_create` secret, generate `content_key=os.urandom(32)` when missing, re-encrypt `enc_blob`
  from current `{cost, description}` (`aad=str(cp.id)`), save. `content_key` is **not** regenerated on a
  description edit.
- `ensure_code(tag)`: set `tag.code=os.urandom(16)` **only when missing** (regenerating would break already-written tags).
- `build_bundle(tag)`: `ensure_code`; resolve the unlock set (`tag.unlocks` or `[tag.point]` if empty);
  collect `content_key`s of the **locked** КП in that set (skip open КП — they have no secret);
  `bid=hashlib.sha256(code).hexdigest()[:16]`; `bundle_blob=seal(derive_wrap_key(code), json({cp_id: b64(content_key)}), aad=bid.encode())`;
  save `code`/`bid`/`bundle_blob`.
- **`update_fields`/`updated_at` invariant (CLAUDE.md)**: the service writes via `save(update_fields=[...])`
  (needed for the recursion guard below), and **every such list MUST include `"updated_at"`**. `CheckpointTag`
  saves use `update_fields=["code", "bid", "bundle_blob", "updated_at"]`; `CheckpointSecret` saves include
  `"updated_at"`. Omitting it leaves the legend fingerprint (which folds `MAX(CheckpointTag.updated_at)` /
  `MAX(CheckpointSecret.updated_at)`, Task 6) stale → app gets `304` and never re-fetches the rebuilt legend.

### Signals (`src/apps/mobile/signals.py`, new; registered in `MobileConfig.ready()`)

- `post_save(Checkpoint)`: `seal_checkpoint(cp)`; on an `is_legend_locked` toggle also rebuild the bundles of
  **`cp.tags.all()` ∪ `cp.unlocked_by.all()`** (a `content_key` appeared/disappeared). `cp.tags.all()` is
  required because a tag with an **empty** `unlocks` M2M unlocks its own КП via the `[point]` runtime default
  and is therefore **not** in `cp.unlocked_by` — without this its bundle would miss the new `content_key`.
  Cleartext-only edits (`cost`/`description`) re-seal `enc_blob` only — bundles untouched (same `content_key`).
- `post_save(CheckpointTag)` and `m2m_changed(CheckpointTag.unlocks)` (on `post_add`/`post_remove`/`post_clear`): `build_bundle(tag)`.
- **Recursion guard (concrete mechanism):** the `post_save(CheckpointTag)` receiver early-returns when
  `update_fields` is exactly the service's sentinel set `{"code", "bid", "bundle_blob", "updated_at"}` — so the
  `build_bundle` write does not re-trigger itself, while a genuine field edit (no/other `update_fields`) does.
  The `post_save(Checkpoint)` path saves a **different** sender (`CheckpointSecret`), so no self-loop there.
  The `m2m_changed` path is additionally fenced by a thread-local re-entrancy flag set around `build_bundle`
  (in case it ever touches `unlocks`). Cover both with the Task 4 "no infinite recursion" test.

### Management commands (`src/apps/mobile/management/commands/`)

- `rebuild_legend_crypto [--race <id>] [--regenerate-codes]`: backfill after migration + repair
  (re-seal all locked КП, rebuild all bundles). `--regenerate-codes` forces new `code`s (re-provisioning).
- `export_legend_codes --race <id>`: print `nfc_uid / КП number / code(hex)` for writing codes into tags.

### Serving (`src/apps/mobile/serializers.py`, `views.py`)

Response shape:

```json
{
  "race": 42,
  "checkpoints": [
    {"id": 153, "number": 1, "type": "kp", "enc": {"iv": "...", "ct": "..."}},
    {"id": 154, "number": 2, "type": "kp", "cost": 4, "description": "Дерево у дороги"}
  ],
  "bundles": [ {"bid": "a1b2...", "iv": "...", "ct": "...", "check_method": "offline"} ]
}
```

- `LegendCheckpointSerializer.to_representation` branches: locked → `{id, number, type, enc}` (from prefetched
  `secret.enc_blob`); open → `{id, number, type, cost, description}`.
- New `BundleSerializer`: `bid, iv, ct, check_method` from `CheckpointTag` (flattens `bundle_blob`'s `iv`/`ct`).
- Remove `LegendTagSerializer`/`tag_hash` and the `secret`/`context` threading — for **locked** КП the app
  matches scan → КП by `bid` (decrypting the bundle yields the `cp_id`s).
- **Open-КП recognition is out of scope for the legend.** Open КП have no secret and no bundle, so a scan on
  an open КП is not resolved via this endpoint anymore (`tag_hash` is gone). The legend's job is unlocking
  encrypted descriptions; the scoring/scan-recording flow lives in the `api` app and matches by `nfc_uid`
  (unchanged). Note this behavior change explicitly in the docs (Task 9).
- Hidden legend (`is_legend_visible=False`) → `{race, checkpoints: [], bundles: []}`, ETag still set.
- Bundles queryset: `CheckpointTag.objects.filter(point__race_id=race_id)` excluding draft-point tags.

### Versioning (`src/apps/mobile/versioning.py`) — build-independent legend

- Drop `key_id` from `legend_state`/`legend_version` (legend no longer per-build).
- `LegendView`/`SyncView` stop resolving `secret`/`key_id` for the legend (teams/races unchanged).
- New legend fingerprint (blake2b, draft-excluded queryset) folds three aggregates:
  1. `MAX(Checkpoint.updated_at)|COUNT` (incl. lock toggle — flag bump hits `auto_now`).
  2. `MAX(CheckpointSecret.updated_at)|COUNT` (re-seal / enc appear / disappear).
  3. `MAX(CheckpointTag.updated_at)|COUNT` (code/unlocks/bundle/check_method).
- Strong ETag on every exit path, `If-None-Match` → `304` (behavior unchanged).

## What Goes Where

- **Implementation Steps** (checkboxes): dependency, models + migration, crypto, service, signals, commands,
  serializers/view, versioning, admin, tests, docs.
- **Post-Completion** (no checkboxes): writing `code`s into physical NFC tags (external tooling),
  re-provisioning already-deployed tags, coordinating the mobile-app client to read the written code and
  implement the offline decrypt.

## Implementation Steps

### Task 1: Add `cryptography` dependency + crypto primitives

**Files:**
- Modify: `pyproject.toml`
- Create: `src/apps/mobile/crypto.py`
- Modify: `src/apps/mobile/tests.py`

- [x] add `cryptography` to `[project].dependencies` in `pyproject.toml`; run `uv lock`
- [x] create `crypto.py` with `seal`, `unseal`, `derive_wrap_key` (ported from `scratch/playground.py`, no master key; **not** named `open` — shadows the builtin)
- [x] write tests: `seal`/`unseal` roundtrip (with correct `aad`)
- [x] write tests: wrong key / wrong `aad` / tampered `ct` → raises (GCM auth failure)
- [x] write tests: `derive_wrap_key` deterministic for same code, differs across codes
- [x] run tests — must pass before next task

### Task 2: Data model + migration

**Files:**
- Modify: `src/website/models/checkpoint.py`
- Modify: `src/website/models/__init__.py` (add `CheckpointSecret` to the re-exports next to `Checkpoint`/`CheckpointTag`)
- Create: `src/website/migrations/00XX_legend_encryption.py` (via `makemigrations`)
- Modify: `src/apps/mobile/tests.py`

- [x] add `Checkpoint.is_legend_locked` (Boolean, default `False`)
- [x] add `CheckpointTag.code` (BinaryField, nullable), `bid` (CharField `max_length=16` = `sha256(code).hexdigest()[:16]`), `bundle_blob` (JSONField, nullable), `unlocks` (M2M → `website.Checkpoint`, `related_name="unlocked_by"`, blank)
- [x] add `CheckpointSecret` model (O2O → `Checkpoint`, `related_name="secret"`, `content_key` BinaryField, `enc_blob` JSONField, `updated_at` auto_now)
- [x] run `makemigrations` (no data migration — default `False`, backfill via command in Task 5)
- [x] write tests: model creation, `CheckpointSecret` O2O reverse `checkpoint.secret`, `tag.unlocks` M2M add/clear
- [x] run tests + `migrate` on the test DB — must pass before next task

### Task 3: Service layer (`legend_crypto.py`)

**Files:**
- Create: `src/apps/mobile/legend_crypto.py`
- Modify: `src/apps/mobile/tests.py`

- [x] implement `seal_checkpoint(cp)` (delete secret when unlocked; create/re-seal `enc_blob`, keep `content_key`)
- [x] implement `ensure_code(tag)` (generate `code` only when missing)
- [x] implement `build_bundle(tag)` (resolve unlock set with `[point]` fallback; skip open КП; set `bid`/`bundle_blob`)
- [x] write tests: locked → secret created, `enc_blob` decrypts to `{cost, description}`; unlocked → secret deleted
- [x] write tests: re-`seal_checkpoint` after description edit keeps the same `content_key`
- [x] write tests: `build_bundle` → bundle decrypts to `{cp_id: content_key}`; open КП skipped; one КП in two tags' bundles (overlap); `ensure_code` does not change an existing code
- [x] run tests — must pass before next task

### Task 4: Signals + registration

**Files:**
- Create: `src/apps/mobile/signals.py`
- Modify: `src/apps/mobile/apps.py` (`MobileConfig.ready()` imports signals)
- Modify: `src/apps/mobile/tests.py`

- [ ] `post_save(Checkpoint)` → `seal_checkpoint`; on lock toggle also rebuild bundles of `cp.tags.all()` **∪** `cp.unlocked_by.all()` (the `∪ cp.tags` covers implicit-`[point]` tags with empty `unlocks`)
- [ ] `post_save(CheckpointTag)` + `m2m_changed(unlocks)` on `post_add`/`post_remove`/`post_clear` → `build_bundle`
- [ ] add recursion guard: `post_save(CheckpointTag)` early-returns when `update_fields == {"code","bid","bundle_blob","updated_at"}`; thread-local flag fences the `m2m_changed` path
- [ ] write tests: locking a КП via `save()` creates its secret; toggling lock rebuilds dependent bundles **including an implicit-`[point]` tag**
- [ ] write tests: editing a tag's `unlocks` rebuilds its bundle; no infinite recursion (tag save + m2m change)
- [ ] write tests: a bundle rebuild **moves the legend ETag** (guards the `update_fields` must-include-`updated_at` rule)
- [ ] run tests — must pass before next task

### Task 5: Management commands (backfill + export)

**Files:**
- Create: `src/apps/mobile/management/__init__.py`, `src/apps/mobile/management/commands/__init__.py`
- Create: `src/apps/mobile/management/commands/rebuild_legend_crypto.py`
- Create: `src/apps/mobile/management/commands/export_legend_codes.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] `rebuild_legend_crypto [--race] [--regenerate-codes]` — re-seal all locked КП + rebuild all bundles
- [ ] `export_legend_codes --race <id>` — print `nfc_uid / КП number / code(hex)`
- [ ] write tests: `rebuild_legend_crypto` backfills secrets+bundles for a race with locked КП
- [ ] write tests: `--regenerate-codes` changes codes; without it codes are preserved
- [ ] write tests: `export_legend_codes` output lists every tag's code
- [ ] run tests — must pass before next task

### Task 6: Serving + build-independent versioning (one task — done together so each test stays green)

> Merged: the `key_id` drop spans `versioning.py`, `LegendView`, **and** `SyncView`. Splitting it leaves an
> intermediate state where the `LegendView` and `SyncView` legend ETags disagree (one with `key_id=""`, one
> with the real key_id), violating the single-source contract. Do all of it in one task.

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/views.py` (`LegendView` body **and** `LegendView`/`SyncView` versioning call sites)
- Modify: `src/apps/mobile/versioning.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] `LegendCheckpointSerializer.to_representation`: locked → `{id, number, type, enc}`; open → `{id, number, type, cost, description}`
- [ ] add `BundleSerializer` (`bid, iv, ct, check_method`); remove `LegendTagSerializer`/`tag_hash` usage
- [ ] `versioning.py`: remove `key_id` from `legend_state`/`legend_version`; fold in `CheckpointSecret` `MAX(updated_at)|COUNT`
- [ ] `LegendView.get`: prefetch `secret`; build `bundles` from draft-excluded `CheckpointTag`; drop `secret`/`key_id` resolution; call `legend_state` without `key_id`; keep hidden-legend `{checkpoints: [], bundles: []}` + ETag
- [ ] `SyncView`: call `legend_version` without `key_id` (teams/races paths unchanged)
- [ ] write tests: locked КП → `enc`, no `cost`/`description`; open КП → `cost`/`description`, no `enc`; mixed response
- [ ] write tests: end-to-end — read `code`, compute `bid`, `HKDF(code)` decrypt bundle → decrypt КП `enc` → original `cost`/`description`
- [ ] write tests: ETag moves on cost/description edit, lock toggle, `unlocks`/`code` change; hidden legend → empty + ETag; update field-set tests
- [ ] write tests: two different `X-App-Key-Id` builds get the **same** legend ETag; `If-None-Match` → `304`; `versions.legend` matches the legend ETag for any build
- [ ] run tests — must pass before next task

### Task 7: Admin

**Files:**
- Modify: `src/website/admin.py`
- Modify: `src/apps/mobile/tests.py`

- [ ] `Checkpoint` admin: `is_legend_locked` in list/edit + bulk actions «Запереть/Открыть легенду»
  — the bulk action MUST **iterate and `save()`** each object (or call `seal_checkpoint` directly), **never** `queryset.update()` (that skips the Task 4 signals → no `CheckpointSecret` created → serializer falls back to the open branch and leaks `cost`/`description` in cleartext)
- [ ] `CheckpointTag` admin: `unlocks` via `filter_horizontal`, `bid` read-only, actions «Перегенерировать код» / «Пересобрать бандл» (`code` displayed hex-encoded if shown)
- [ ] write tests: lock/unlock admin action creates/deletes secrets (asserting the bulk path actually seals, not just flips the flag); rebuild-bundle action repopulates `bundle_blob`
- [ ] run tests — must pass before next task

### Task 8: Verify acceptance criteria

- [ ] verify selective encryption: locked КП hidden until scan, open КП always visible
- [ ] verify "code → subset" with overlap works end-to-end
- [ ] run full suite: `uv run pytest`
- [ ] run `make format && make lint`
- [ ] verify the `api`/scoring `nfc_uid` path and `teams`/`races` versions are unaffected

### Task 9: Documentation

- [ ] update `src/apps/mobile/README.md` (legend now encrypted/build-independent; `bid`+bundles replace `tag_hash`; codes/provisioning)
- [ ] update the `CLAUDE.md` mobile invariant ("legend ETag per-build / folds key_id / tags as tag_hash" → new model; keep `nfc_uid` normalized invariant + api path)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention or external systems — informational only.*

**External system updates:**
- **Write `code`s into physical NFC tags' user memory** before the race (separate tooling — out of this plan).
  Use `export_legend_codes` for the values. Already-deployed tags (UID-only) must be re-provisioned.
- **Mobile-app client** must be updated to read the written `code` from tag memory, compute
  `bid = sha256(code).hexdigest()[:16]` (16 hex chars — must match the server), locate the bundle, and run the
  offline decrypt (`HKDF` → bundle → `content_key` → КП `enc`).

**Manual verification:**
- On a staging race: lock a subset of КП, configure `unlocks` with an overlap, fetch `/app/race/<id>/legend/`,
  confirm locked КП expose only `enc` and that a correct `code` decrypts exactly its configured subset.
