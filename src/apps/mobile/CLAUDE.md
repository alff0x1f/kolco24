# apps.mobile

Loaded on demand when working in this directory. Root rules are in `/CLAUDE.md`.

`apps.mobile` — **signed mobile-app endpoints** for iOS/Android (`label = "mobile"`, mounted at `/app/*`
via `config/urls.py`). The **read** path is self-contained: touches neither `/api/` nor `donate`/`website` — but the
**track-upload and marks-upload writes** (`TrackPoint`/`Mark`+`MarkPresent`, see the **Track upload** and **Marks
upload** invariants below) are the documented exceptions, holding
cross-app FKs into `website.Team`/`website.Race` (like `apps.race`). The reads are accountless — the
app authenticates **itself** (per-build HMAC); a thin **write layer** adds a per-person bearer token on top (login/
logout, legend-tag create, judge scans, member-tag bind — see the **Per-person write layer** invariant below). The
track, marks and photo POSTs are **build-HMAC-only** like the reads (NOT part of the per-person write layer); each
POST's gate is stated in its own invariant bullet below (**Track upload** / **Marks upload** / **Photo upload** /
**Judge scans upload** / **Member-tag bind**). Full
design (background-sync model,
two-server lease/handoff, secret-rotation runbook, 403 reason codes) lives in `src/apps/mobile/README.md`; exact
response fields are pinned by the serializers and the field-set tests in `tests.py`. Invariants to preserve:
  - **Auth**: per-build shared secret selected by `X-App-Key-Id` from `MOBILE_APP_KEYS` (JSON key-id → secret map env,
    parsed once at startup, **fail-closed** — a missing/malformed map means every `/app/*` request 403s; a
    non-empty-but-bad value also logs a settings-import `WARNING`). HMAC-SHA256 over the canonical
    `method + "\n" + full_path + "\n" + ts + "\n" + sha256_hex(body)` (`signing.py`); replay window ±
    `MOBILE_APP_TS_WINDOW` = 300 s — a hardcoded constant in `settings.py`, not an env var; no nonce. **Body signing
    works for POST too** (the canonical already folds `sha256_hex(body)`): the `RawPostDataException` caveat does not
    bite because Django buffers `request.body`, so `SignedAppPermission` reading it before DRF parses `request.data` is
    safe (pinned by `test_signed_permission_reads_body_before_drf_parse`).
  - **Neutral failures, no writes in the permission**: every verification failure returns the same
    `403 {"detail": "Forbidden"}` — no hint which check failed. `SignedAppPermission` does no DB writes; the log +
    aggregated `AppAuthFailure` row happen in `AppAPIView.permission_denied()`, and `AppInstall` stats are recorded
    best-effort in `initial()` after permissions pass (a stats-write failure never breaks a response; a denied request
    never writes `AppInstall`).
  - **Per-person write layer** (login + legend-tag create + judge scans + member-tag bind, on top of the build HMAC):
    a **layered permission stack** —
    build HMAC → person identity → per-race authorization. `MobileToken` (`models.py`) is a revocable **opaque** bearer
    (not JWT — instant revoke via `revoked_at` is the priority for an admin credential): only `sha256(raw)` is stored
    in `token_hash` (unique-indexed), the raw `secrets.token_urlsafe(32)` is returned **once** at login (a high-entropy
    token needs no salt/slow hash); `is_active = revoked_at is None and expires_at > now`; TTL `MOBILE_TOKEN_TTL =
    timedelta(days=30)` in `settings.py`. **Not** ETag-fingerprinted → no `updated_at`. Helpers in `tokens.py`
    (`generate_token`/`hash_token`/`resolve_token` — `resolve_token` best-effort stamps `last_used_at`). Permissions
    (`permissions.py`): `IsMobileUser` resolves `Authorization: Bearer <token>` → `request.mobile_user` +
    `request.mobile_token` (identity only, authorizes nothing; stack **after** `SignedAppPermission`);
    `CanEditRaceLegend` reads `view.kwargs["race_id"]`, loads `Race` (missing → 404), returns
    `apps.race.permissions.can_edit_race` (same `RaceAdmin(role=ADMIN)`-only rule as the web `RaceLegendEditView`);
    it reads `request.mobile_user` defensively (`getattr(..., None)` → `False`/403, not 500) and must stack **after**
    `IsMobileUser`. **Actionable-vs-neutral error split**: the anonymous build layer stays a neutral
    `403 {"detail":"Forbidden"}` (no brute-force hint), but the authenticated layers are actionable — an invalid/
    expired/revoked token raises `MobileTokenInvalid` (an `APIException` with `status_code = 401`; needed because with
    `authentication_classes = []` a raised `NotAuthenticated` renders as 403), and a non-admin user gets an actionable
    403. Endpoints (all POST, `views.py`): `POST /app/login/` (gated by `SignedAppPermission` **only** — even the
    password endpoint is build-only; authenticates email+password via `EmailBackend`, mints a `MobileToken`, returns
    `{token, expires_at, admin_race_ids}`; login stays **open to every user** (participants will use it too) —
    `admin_race_ids` = sorted ids of the user's `RaceAdmin(role=ADMIN)` races (`[]` for a plain user, moderator or
    bare superuser), a **UI hint only** so the app can hide the admin actions; every write re-checks
    `can_edit_race`; failures are enumeration-safe — wrong password and unknown email both 401 with
    the **same** message, malformed body 400); `POST /app/logout/` (`SignedAppPermission + IsMobileUser`; flips `revoked_at` on the
    **presented** token only, other tokens of the user stay valid); `POST /app/race/<id>/tags/`
    (`SignedAppPermission + IsMobileUser + CanEditRaceLegend`; the online-only NFC-chip→КП provisioning endpoint). Tag
    create takes body `{checkpoint_id, nfc_uid}` where **`checkpoint_id` is the checkpoint `id`** (`number` is not unique
    per race, hence id) — the single wire vocabulary across `/app/`: a checkpoint's stable id is always `checkpoint_id`,
    its display number always `number`, no key overloaded. Resolves a **non-`hidden`** КП by id within the race (404),
    normalizes `nfc_uid` (`.strip().upper()`),
    then: same `nfc_uid` on the **same** КП → idempotent 200; on a **different** КП → 409 (no auto-rebind); else
    `CheckpointTag(checkpoint, nfc_uid, created_by=request.mobile_user)` created in `transaction.atomic()` via
    `instance.save()` (**fires the legend-crypto signals** — server stays the single crypto source; catches
    `IntegrityError` from the **global** `CheckpointTag.nfc_uid` unique constraint (migration `website/0089`, which
    replaced the old `unique_together("point","nfc_uid")` — a UID is now globally unique; `0089` aborts with a
    `RuntimeError` if any duplicate UIDs exist, requiring manual resolution before the migration can proceed) → re-query: same КП ⇒ idempotent 200, different КП ⇒ 409).
    The mobile `TagCreateSerializer.nfc_uid` caps `max_length=255` (mirrors the column) so
    an oversized UID is a 400, not a DB-insert 500. Response
    `{bid, checkpoint_id: cp.id, number: cp.number, nfc_uid, code: code.hex()}` (201, or 200 idempotent; both ids under
    honest keys); a row with `bid==""`/`code is None`
    (created bypassing signals) is repaired via `build_bundle` before responding (no `None.hex()` 500) — the repair
    runs under `select_for_update()` and re-checks the row inside the lock so two concurrent repairs can't mint
    different codes (the first response could otherwise be written to the chip before the second overwrites it).
    Token resolution (`tokens.py:resolve_token`) also rejects a token whose owner is `is_active=False`, so disabling a
    compromised admin cuts off provisioning instantly without waiting out the 30-day TTL.
    `CheckpointTag.created_by` (`FK(AUTH_USER_MODEL, null, SET_NULL, related_name="provisioned_tags")`, migration
    `website/0088`) is **not** in any version fingerprint, but creating a tag still moves `versions.legend`/the legend
    ETag via `CheckpointTag.updated_at`. The same stack also gates `POST /app/race/<id>/judge_scans/` and
    `POST /app/race/<id>/member_tags/bind/` (see **Judge scans upload** / **Member-tag bind** below).
    **Throttling** (first use here): IP-scoped
    `ClientIPScopedRateThrottle` (`throttling.py`) — a thin `ScopedRateThrottle` subclass that overrides only
    `get_ident` to key on the un-spoofable `_client_ip` (last `X-Forwarded-For` entry, the one nginx appends), so a
    client can't rotate a forged XFF prefix for a fresh bucket (DRF's stock `get_ident` trusts the *first* entry
    unless `NUM_PROXIES` is set). It still reads no `request.data`/`request.body`, so the body-read ordering hazard
    stays closed. `throttle_scope` is set **per view** (`mobile-login` 5/min on login, `mobile-write` 60/min on tag
    create); rates in `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` (no global `DEFAULT_THROTTLE_CLASSES`); needs the new
    `CACHES` (`LocMemCache`) added for it. **Test isolation**: the
    `autouse` `_clear_throttle_cache` fixture in `src/apps/mobile/tests.py` calls `cache.clear()` before/after every
    test to prevent throttle counts leaking across tests (all test requests share the same client IP); any new test
    module that exercises throttled mobile endpoints must replicate this fixture.
  - **Endpoints** (reads all GET, listed here; each POST is described in its own invariant bullet — the gate of each
    is stated there; see `urls.py`): `/app/races/` (published, not `is_hidden_in_app`), `/app/race/<id>/teams/` (teams **plus the
    embedded category catalogue** — deliberately no separate categories endpoint; inactive categories included so
    every `category2` id resolves), `/app/race/<id>/legend/` (checkpoints **plus a per-tag `tags` array** — `bid → checkpoint_id`
    identity for **every** tag (open + locked), plus `iv`/`ct` for the offline legend unlock on locked КП only — see
    the **Legend encryption** invariant below; the legend is **always served** for a published race (no race-level
    visibility gate), with `type="hidden"` КП excluded), `/app/race/<id>/member_tags/` (the participant-bracelet pool —
    `{number, nfc_uid}` per `Tag` for offline scan resolution; the pool is the race's own K24 v2 bracelets
    (`Tag.race = race`, unwindowed) plus the **global** v1 pool (`race IS NULL`, one physical set reused across
    races), a race row shadowing a global row with the same `nfc_uid`; the global part is a **data-anchored** window
    `last_seen_at IS NULL OR last_seen_at >= MAX(last_seen_at) − 30d` over global rows (not wall-clock `now()`, so an idle race is
    stable; a never-scanned pool returns everything; never-scanned rows — e.g. a bracelet just created by
    `member_tags/bind/` — are always served, which is load-bearing for the bind endpoint's "create moves the ETag")), `/app/race/<id>/sync/`
    (pure version manifest — no data, no
    ETag; lease/handoff stubbed: `data_source` from
    `MOBILE_DATA_SOURCE` env, default `"cloud"`, `lease_expires_at` always `null`).
  - **Conditional GET**: races/teams/legend/member_tags set a strong `ETag` on **every** exit path; a matching
    `If-None-Match`
    short-circuits to `304` with no serialization. `versioning.py` is the **single source of truth** for both the
    ETags and the `sync` manifest: each fingerprint is a `blake2b(digest_size=8)` hex over `MAX(updated_at)|COUNT`
    aggregates computed over the exact queryset the view serves (`None` aggregates render as `"None"`, so an empty
    race is stable) — **except `member_tags_version()`**, which folds in the sorted active ID list (see **Member
    tags** below), and **except `legend_version()`**, which additionally prefixes a `_LEGEND_SCHEMA_VERSION` integer
    so a response-shape change (fields added or removed) forces a cache bust at deploy time even when no DB row
    changed; bump `_LEGEND_SCHEMA_VERSION` whenever the legend response shape changes (`LegendCheckpointSerializer` or the
    per-tag `TagSerializer` fields) (current value: 3, bumped to 2 when `color` was added, to 3 when the `tags[]` identity
    key was renamed `point` → `checkpoint_id`). `races_version()` likewise prefixes `_RACES_SCHEMA_VERSION` — bump it whenever
    `RaceListSerializer` fields change. Deliberately **no `versions.categories`** — category edits must move `versions.teams`;
    `races_version()` is global and deliberately absent from the per-race `sync` manifest (the races list is the app's
    entry point, probed via its own conditional GET).
  - **Member tags** (`active_member_tags()` + `member_tags_version()` in `versioning.py`; `MemberTagSerializer` in
    `serializers.py` — distinct name from the legend per-`CheckpointTag` `TagSerializer`): the fingerprint hashes the
    **actual served field values** — `blake2b` over the canonical JSON encoding of `[(id, number, nfc_uid), …]`
    ordered by `id` over the same data-anchored window `active_member_tags()` serves (single-source contract;
    JSON encoding avoids any ambiguity from special characters in `nfc_uid`). Hashing field values (not just
    `MAX(updated_at)|COUNT|IDs`) ensures a same-COUNT identity swap (one chip ages past the 30-day floor while a
    bind brings another in) and a same-COUNT field edit under concurrent provisioning (where one write's timestamp
    falls below the existing `MAX(updated_at)`) both change the fingerprint and avoid a stale 304.
    `Tag` gained an `auto_now` `updated_at` field **so the version moves on provisioning edits (add / renumber /
    remove) but not on `last_seen_at` stamps**: the bind re-stamp (`_resolve_existing` in `views.py`) keeps
    `save(update_fields=["last_seen_at"])` and **intentionally omits `"updated_at"`** — a deliberate carve-out from
    the `update_fields` discipline. `member_tags_version(race_id=…)` is per race
    (same pool as `active_member_tags(race_id)`; without `race_id` both mean the global pool only) and is included
    in the per-race `sync` manifest. The **legend** fingerprint additionally folds in two more
    `MAX(updated_at)|COUNT` aggregates over the same hidden-excluded checkpoints — `CheckpointSecret` (re-seal / enc
    appear/disappear on a lock toggle) and `CheckpointTag` (code/unlocks/bundle/check_method) — so the legend ETag /
    `versions.legend` move on any lock toggle, re-seal, or tag edit. The legend fingerprint is `legend_version(race_id)`
    alone — there is no race-level visibility flag folded in. The legend is **build-independent**: the
    ciphertext/bundles are precomputed and stored in the DB (not keyed by the per-build secret), so `legend_version`
    takes **no** `key_id` and two builds share the legend ETag (an `X-App-Key-Id` rotation no longer
    re-fetches the legend).
  - **Legend encryption** (`src/apps/mobile/crypto.py`, `legend_crypto.py`, `signals.py`; models in
    `src/website/models/checkpoint.py`): a **locked** checkpoint (`Checkpoint.is_legend_locked=True`) hides its
    `cost`/`description` behind envelope encryption so the app can only decrypt them **after physically scanning that
    КП's NFC code** — fully offline. Threat model: the adversary is the app in a participant's hands; a DB leak is out
    of scope, so there is **no at-rest encryption and no master key** — only what leaves the server is encrypted.
    Scheme (AES-256-GCM + HKDF via the `cryptography` package — a direct `pyproject.toml` dependency since this
    feature; `crypto.py:seal`/`unseal`/`derive_wrap_key`; `unseal` is **not** named `open` to avoid
    shadowing the builtin/tripping flake8): each locked КП is sealed with its own random 32-B `content_key` into
    `CheckpointSecret` (`O2O → Checkpoint`, `related_name="secret"`; `enc_blob={"iv","ct"}` over `{cost, description}`,
    `aad=str(cp.id)`); each `CheckpointTag` (its FK to `Checkpoint` is `CheckpointTag.checkpoint`, `related_name="tags"`,
    renamed from `point` in migration `website/0090`) carries a random 16-B `code` (written into the tag's NFC user memory
    out-of-band) and a per-tag `bundle_blob` = AES-GCM of `{cp_id: content_key}` over the tag's `unlocks` set, wrap
    key `HKDF(code)`, `aad=bid` where **`bid = sha256(code).hexdigest()[:16]`** (16 hex chars — the mobile client must
    compute it identically). `unlocks` is an M2M (`related_name="unlocked_by"`); an **empty** `unlocks` means the tag
    unlocks its own `checkpoint` (runtime `[tag.checkpoint]` default in `build_bundle`). The envelope (content-key indirection) is
    what lets one code open a configurable subset of КП with overlaps without re-encrypting a КП per code. Ciphertext
    + bundles are **precomputed and stored** so ETags stay stable. The service layer (`legend_crypto.py`:
    `seal_checkpoint`/`ensure_code`/`build_bundle`) is driven by **signals** (`post_save(Checkpoint)` re-seals + on a
    lock toggle rebuilds bundles of `cp.tags.all() ∪ cp.unlocked_by.all()`; `post_save(CheckpointTag)` +
    `m2m_changed(unlocks)` rebuild the tag's bundle) with a **recursion guard** (`post_save(CheckpointTag)`
    early-returns when `update_fields == {"code","bid","bundle_blob","updated_at"}`, the service's sentinel set; a
    thread-local flag fences the `m2m_changed` path). Bulk ops bypass signals — backfill/repair via
    `manage.py rebuild_legend_crypto [--race <id>] [--regenerate-codes]`; dump codes for tag provisioning via
    `manage.py export_legend_codes --race <id>`. Admin lock/unlock + rebuild-bundle bulk actions **must iterate and
    `save()`/call the service** (never `queryset.update()`, which skips the signals → no secret → cleartext leak). The
    serializer (`LegendCheckpointSerializer`) branches: locked → `{id, number, type, color, enc}`, open →
    `{id, number, type, color, cost, description}` — **both branches carry `color`** (`Checkpoint.color`, a named
    `CheckpointColor` token `red`/`blue`/`green`/`yellow`/`orange`/`purple`/`""`, **not secret**, the mobile app maps
    it to its own palette; it lives on `Checkpoint` (not in `enc_blob`/bundles) so the legend ETag /
    `versions.legend` move on a color edit via `Checkpoint.updated_at` `auto_now` — the reconcile must stay a plain
    `instance.save()` not `update_fields` omitting `"updated_at"`; adding `color` also bumped `_LEGEND_SCHEMA_VERSION`
    to 2 in `versioning.py` (schema-change cache bust — see **Conditional GET** above)). `TagSerializer` emits **one entry per `CheckpointTag`**:
    `{bid, checkpoint_id, check_method}` for **every** tag (identity, open + locked) plus `iv`/`ct` from
    `bundle_blob` (`None` for open tags — identity-only, not decryptable). The legend view's tag queryset no longer
    excludes `bundle_blob=None` (so open-КП tags are emitted) and adds `.exclude(bid="")` (drops un-built rows created
    bypassing signals); response key is `tags` (was `bundles`).
    **`tag_hash` is gone** — offline КП identity is now `bid → checkpoint_id` for every tag (open + locked). Per the **`update_fields` discipline** above, every service
    `save(update_fields=[...])` on `CheckpointTag`/`CheckpointSecret` includes `"updated_at"`.
  - **`update_fields` discipline**: the fingerprints rely on `auto_now` `updated_at` fields on `Team`/`Athlet`/
    `Checkpoint`/`CheckpointTag`/`CheckpointSecret`/`Category`/`Race`/`Tag`. Any `save(update_fields=[...])` on these models **must** include `"updated_at"`,
    otherwise the version/ETag goes stale (e.g. the auto `OPEN → SOLD_OUT` `reg_status` flips in
    `check_vtb_payments.py` and `website/models/models.py` include it). **Exception**: the bind re-stamp deliberately
    omits `"updated_at"` from `save(update_fields=["last_seen_at"])` — see the **Member tags** invariant above.
  - **`nfc_uid` normalized invariant**: `Tag.nfc_uid` and `CheckpointTag.nfc_uid` are auto-normalized (stripped and uppercased) by their
    `save()` overrides. `save()` with or without `update_fields` still runs the override (Django calls the Python method regardless).
    The one case that bypasses the override entirely is `QuerySet.update(nfc_uid=...)` — that generates raw SQL without calling `save()`,
    so callers **must normalize the value themselves** (`.strip().upper()`) before passing it to `update()`. Lookups against `nfc_uid`
    should use the plain exact-match (`nfc_uid=value.strip().upper()`) rather than `__iexact`, since stored values are always stripped
    and uppercase.
  - **Track upload** (`POST /app/race/<id>/track/`, name `track` — the **third POST**, but **build-HMAC-only**: gated
    by `AppAPIView`'s default `[SignedAppPermission]`, **NOT** the per-person write layer — track recording runs on
    participants' phones, no bearer token; throttle `mobile-write`, 60/min). GPS-track ingestion for the Android app:
    receive a batch, store idempotently, ack the accepted ids — the organizer-facing map that reads tracks back is a
    separate later task, **out of scope** here. `TrackPoint` (`models.py`) is **write-only / immutable**: one row per
    GPS fix, **PK = the client-generated UUID** (`id`) so the idempotency key *is* the PK and upsert collapses to
    `bulk_create(objs, ignore_conflicts=True)` (a re-sent point hits a PK conflict and is silently skipped — no
    secondary index, no `update_or_create`). It is the documented exception to `apps.mobile`'s "self-contained" read
    path — it holds **cross-app FKs** into `website.Team`/`website.Race` (like `apps.race`). **No `updated_at`,
    deliberately not in `versioning.py`** (immutable rows never touch the fingerprint/`sync` machinery). `install_id`
    (from `request.app_meta["install_id"]`, the `X-Install-Id` header set by `SignedAppPermission` before the view
    runs) is stored for device attribution (a team recording from two phones is separable: `segment_id` distinguishes
    recording sessions, `install_id` groups a phone's sessions). **`accepted` = every submitted id** (with
    `ignore_conflicts=True` a point is either newly inserted or already present — both are success for the client's
    retry loop). **Validation is all-or-nothing (400 on a bad batch)**, not partial-accept: `TrackPointSerializer`/
    `TrackUploadSerializer` (`serializers.py`) bound `lat ∈ [-90,90]`, `lon ∈ [-180,180]`, `min_value=0` on the
    physically-non-negative magnitudes (`accuracy`/`gps_time_ms`/`elapsed_at`/`trusted_ms`/`vertical_accuracy`/
    `boot_count`), `altitude` **unbounded** (may be below sea level), `id`/`segment_id` `min_length=1`, `points`
    `max_length=500` (the app's batch cap). Flow (`views.py:TrackUploadView`): resolve a published `Race` (404) →
    validate body (400) → check the team is in the race (`category2__race_id=race_id`, else 404) →
    `bulk_create(ignore_conflicts=True)` → 200 `{"accepted": [all submitted ids]}`. Same trust boundary as the reads:
    a genuine build can post a track for any `team_id` in the race; `team_id`/`install_id` are spoofable, accepted
    as-is.
  - **Marks upload** (`POST /app/race/<id>/marks/`, name `marks` — a **fourth POST**, also **build-HMAC-only** like
    `/track/`: gated by `AppAPIView`'s default `[SignedAppPermission]`, NOT the per-person write layer — takes run on
    participants' phones, no bearer token; throttle `mobile-write`, 60/min). Checkpoint-take (взятие КП) ingestion for
    the Android app: receive a batch, store, compute a per-mark `verified`, ack the accepted ids — the read-side
    scoring/dedup/reattribution feature is a separate future task, **out of scope** here. **Two normalized models**
    (`models.py`, cross-app FKs into `website.Team`/`website.Race` like `apps.race`): `Mark` is one row per take report,
    **PK = the client UUID** (`id`, the idempotency key); `MarkPresent` is one row per present member, FK back
    (`related_name="present"`) with `unique_together("mark","number_in_team")` so child inserts are conflict-safe on
    re-send (normalized over a JSON column so the roster is queryable for the future scoring task; member identity is the
    physical chip `nfc_uid`/`code`, not the client `number`; `{nfc_uid:null, number:0}` is the "no snapshot"
    sentinel). `Mark` has `checkpoint_id` as a **plain int, not an FK** (an unknown КП is still accepted — data is never
    lost), `cp_code`/`cp_nfc_uid` `blank=True` (blank for a future `photo` mark), 7 flat nullable `loc_*` columns (the
    whole `location` may be null), and a server-computed `verified`. **No `updated_at`, deliberately out of
    `versioning.py`** (nothing reads a version off `Mark`) — but unlike `TrackPoint` the row is **NOT immutable**.
    **One deliberate divergence from `/track/`: enrichment-upsert, not strict-immutable.** The client deliberately
    **re-sends the same `id`** when a late GPS fix (`attachLocation`) or a new roster member (`addMember`) lands after
    the DTO was serialized but before the row was marked uploaded — the first POST carries `location=null`/a partial
    `present[]`, a later POST carries the enriched payload. A pre-filter-and-skip design would **permanently lose** the
    late data, so a repeat `id` must **merge**. Enrichment is monotonic (location null→value, `present` only grows,
    `complete` false→true; take-moment times and `cp_*` are stable), so a blind **last-write-wins upsert** of the
    scalars is correct and simplest: `Mark.objects.bulk_create(objs, update_conflicts=True, unique_fields=["id"],
    update_fields=MARK_UPDATE_FIELDS)` (Postgres `ON CONFLICT (id) DO UPDATE`, `MARK_UPDATE_FIELDS` = every scalar
    **except** `id` and `created_at` so the original insert time is preserved), then
    `MarkPresent.objects.bulk_create(present_objs, ignore_conflicts=True)` for **all** marks (additive — the
    `unique_together` skips already-stored slots, inserts the late roster members). `accepted` echoes **all** submitted
    ids. **In-batch `id` de-dup (the one `update_conflicts` gotcha)**: a single `INSERT … ON CONFLICT (id) DO UPDATE`
    with the same `id` twice raises Postgres `CardinalityViolation` → 500. The real client never sends in-batch dup ids,
    but a malformed body must not 500, so the view **de-dups `marks` by `id` (keeping the last occurrence)** before
    building the upsert objects; `accepted` still echoes the originally-submitted ids (`MarkPresent` is unaffected — it
    uses `ignore_conflicts`, which tolerates in-batch dups). **`verified` computed at ingest** with a single per-batch
    prefetch (`_bids_by_checkpoint`, no per-mark query): true iff `sha256(bytes.fromhex(cp_code)).hexdigest()[:16]`
    matches a `CheckpointTag.bid` of the claimed `checkpoint_id` (`cp_code` is the **hex** of the tag's 16 raw bytes) —
    proof the КП was physically scanned. Unknown КП / mismatch / bad-hex / blank `cp_code` → `verified=False`, **row
    still stored**; `.exclude(bid="")` drops un-built tags (mirrors the legend view). **Same trust boundary as the
    reads / `/track/`**: build-HMAC only, `team_id`/`source_install_id` spoofable and accepted as-is — but
    `source_install_id` is read from the **signed body** (the contract's provenance key), the `X-Install-Id` header
    stays for `AppInstall` stats. Serializers (`MarkUploadSerializer`/`MarkSerializer`/`PresentMemberSerializer`/
    `MarkLocationSerializer`) reuse `FiniteFloatField` and mirror `TrackUploadSerializer` bounds (`lat`/`lon`
    `[-90,90]`/`[-180,180]`, `altitude` unbounded, `min_value=0` on non-negative magnitudes, `marks` `max_length=500`,
    all-or-nothing 400); **400-not-500 caps**: 32-bit `IntegerField` columns (`checkpoint_id`/`expected_count`/`number`/
    `number_in_team`/**`boot_count`**) carry `max_value=2147483647`, BigInt fields (`trusted_ms`/`wall_ms`/`elapsed_at`/
    `loc_gps_time_ms`/`loc_elapsed_at`) the wider `2**63-1` — don't cross the two (a 2⁶³ value in `boot_count` still
    500s). `location` is `required=False` (load-bearing: the client uses kotlinx `encodeDefaults=false`, so a fix-less
    take **omits** the key rather than sending `location:null`); `method` is `ChoiceField(["nfc","photo"])` (any other
    value 400). Flow (`views.py:MarkUploadView`): resolve published `Race` (404) → validate (400) → team-in-race check
    (`category2__race_id`, else 404) → build `bids_by_cp` + compute `verified` → de-dup batch + flatten `location` to
    `loc_*` (`location is None` → all `loc_*=None`) + build `MarkPresent` objs → `transaction.atomic()` (parent upsert
    before child insert) → 200 `{"accepted": [all submitted ids]}`. Empty `marks` → early ack `[]` (tag-bid query
    skipped). **Boundary-time side effect**: still inside that same `transaction.atomic()`, after the `Mark`/
    `MarkPresent` upserts, a verified (`verified=True`, `method="nfc"`) mark for a `start`/`finish`-typed КП
    auto-populates `Team.start_time`/`finish_time` — write-once (only when the field is still `0`; **first-write-
    wins**, not global-earliest-wins: the first upload that finds the field `0` sets it and every later upload is a
    no-op for that field, even one carrying a genuinely earlier verified mark — see the second assertion of
    `test_mark_upload_earliest_verified_start_mark_wins` in `apps/mobile/tests.py`; manual values are never
    overwritten either), and within that first write, earliest-wins (`Min(Coalesce(NullIf("trusted_ms", 0),"wall_ms"))` — a zero `trusted_ms` counts as absent,
    **excluding non-positive results** — an epoch-`0` mark would otherwise permanently pin the aggregate at `0`,
    which is also the unset sentinel, and starve out every later genuinely-timed mark — over all stored verified
    NFC marks for that team+boundary at that moment, not just the current batch), saved with
    `update_fields=[...,"updated_at"]`. A `method="photo"` take never sets it. Races with no `start`/`finish` КП, or
    КП not provisioned with `CheckpointTag`s, simply never auto-populate (no error). Fetches the `Team` row with
    `select_for_update()` so a write can never regress an already-set field to a *later* value; it does **not**
    make two truly-concurrent uploads for the same team resolve to the global earliest across both (each computes
    its aggregate only over what its own not-yet-committed transaction can see, so whichever commits first wins and
    the loser is skipped by the write-once guard even if it held a genuinely earlier mark) — accepted as
    first-write-wins for both the sequential and the concurrent case, since the stored value is always a real
    verified scan, never fabricated or clobbered to something later. **Accepted risk — same trust boundary as the
    rest of `/marks/`, now feeding a scored field**: `team_id` is still spoofable (build-HMAC-only, no per-person
    auth), and `Team.start_time`/`finish_time` are read by `apps/race/results.py` when building the protocol
    snapshot — so a client that legitimately learns a shared start/finish station's code can post a fabricated
    verified NFC mark for another team's `team_id` and, being first, permanently set that team's boundary time
    (write-once). No additional gating is added here; an organizer who suspects a bogus auto-populated value must
    currently clear it by hand (edit the field in `/admin/` — a subsequent `/marks/` upload that still references
    the same boundary КП will recompute and re-set it from the still-present historical `Mark` rows, so a durable
    fix also requires removing/editing those `Mark` rows, which have no admin UI today).
  - **Photo upload** (`POST /app/race/<id>/mark/<mark_id>/photo/<frame_id>`, name `mark_photo` — a **fifth POST**, also
    **build-HMAC-only** like `/track/`/`/marks/`: gated by `AppAPIView`'s default `[SignedAppPermission]`, NOT the
    per-person write layer; throttle scope `mobile-photo`, `120/min`). Stores one raw JPEG frame for a
    `method="photo"` `Mark` — the client posts one request per frame; the organizer-facing read/render side is a
    separate future task, **out of scope** here. **Binary body, not JSON**: the view reads `request.body` directly
    and never touches `request.data` (DRF's default parsers don't handle `image/jpeg`; touching `.data` 415s) —
    `SignedAppPermission` already reads `request.body` before the view (Django buffers it), so HMAC-over-raw-bytes
    works unchanged. `MarkPhoto` (`models.py`, child FK `mark → Mark` CASCADE `related_name="photos"`) is stored at
    a deterministic, unguessable path `mark_photos/<mark_id>/<frame_id>.jpg` (two client UUIDs) via a `FileField`
    on the persisted `/app/media` volume; **no `race` field** — `mark.race_id` carries it, and the URL `race_id` is
    validated against the mark by the `Mark.objects.get(pk=mark_id, race_id=race_id)` lookup (404 on mismatch).
    **No `updated_at`, deliberately absent from `versioning.py`** — same immutable-model precedent as `TrackPoint`.
    **Idempotent by `(mark, frame_id)`**: `unique_together` plus an `exists()` pre-check short-circuits a re-send to
    `200`. **Row-first write ordering, single transaction**: the `MarkPhoto` row (with an empty `image`) is inserted
    *and* the storage write happens inside the same `transaction.atomic()` block — the DB's `unique_together` is the
    single arbiter of which of two truly concurrent requests owns a given `(mark, frame_id)`; only the insert's
    winner ever touches storage (a losing `IntegrityError` returns `200` without writing anything), so two racing
    requests can never both write the shared canonical path and have one silently overwrite the other's bytes.
    Keeping the storage write inside the same atomic block means a storage failure rolls the row insert back too —
    a retry never finds a committed row with an empty `image` (which would otherwise make the `exists()`
    idempotency pre-check permanently ack a frame that was never actually stored). `image` is
    `FileField(max_length=255, ...)` (not the Django default 100) since the deterministic path can reach 145 chars
    (`mark_photos/` + a 64-char `mark_id` + `/` + a 64-char `frame_id` + `.jpg`) — both ids are capped at 64 by
    `SAFE_ID_RE`. **Orphan-file guard**: because only the row's owner ever reaches the
    storage step, a canonical file left by a crashed prior attempt (write started, then crashed before completing)
    can be safely reused — the view `storage.exists()`/`storage.delete()`s any stale file at the canonical path
    first so a re-post reuses the deterministic name instead of getting a storage-suffixed `_XXXX` copy. **`mark_id`/
    `frame_id` charset guard**: both are interpolated into a filesystem path (`mark_photos/<mark_id>/<frame_id>.jpg`),
    so the view validates each against `^[A-Za-z0-9_-]{1,64}$` and 400s before the `Mark` lookup or touching storage
    — `mark_id` needs this too because `Mark.id` is a client-chosen string with no charset restriction at `/marks/`
    ingestion (e.g. a literal `".."` would otherwise reach Django's `upload_to` path-building unvalidated); the URL's
    `<str:frame_id>`/`<str:mark_id>` converters (`[^/]+`) already 404 an empty or slash-containing segment at the
    routing layer before the view runs, so the view guard only fires for a single bad-charset segment (e.g. `a.jpg`
    or a literal `..`). Flow: `Race` 404 (published only) → `mark_id`/`frame_id` charset 400 → `Mark` 404
    (`pk=mark_id, race_id=race_id`) → empty-body 400 → oversized 413 (`PHOTO_MAX_BYTES = 10 MB`) → idempotent 200 →
    row insert + storage write in one transaction (200 on `IntegrityError`) → 201. **`DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024`**
    (`settings.py`) had to be raised above
    the Django default 2.5 MB — it's enforced the moment `request.body` is first read, which happens inside
    `SignedAppPermission` **before** the view, so an under-cap JPEG over the old 2.5 MB default would have hit an
    opaque `RequestDataTooBig` 400 before the explicit 413 ever ran; this is a **global** relaxation (any non-
    multipart POST may now buffer up to 12 MB), accepted because nginx already gates all bodies at `client_max_body_
    size 50m`. **URL has no trailing slash** (diverging from every other `/app/` route) because the signed canonical
    string is the request's `full_path` and must match the client byte-for-byte. Not admin-registered, matching the
    `Mark`/`TrackPoint`/`MarkPresent` convention (only the stats models `AppInstall`/`AppAuthFailure` are registered).
    A frame arriving before its parent `Mark` 404s — contract-safe, since the client only drains a mark's frames
    after that mark's own upload is acknowledged, and treats a photo `404` as transient (self-heals on the next sync).
  - **Judge scans upload** (`POST /app/race/<id>/judge_scans/`, name `judge_scans` — a **sixth POST**; unlike
    `/track/`/`/marks/` it is on the **per-person write layer**, gated by the same stack as tag-create
    `[SignedAppPermission, IsMobileUser, CanEditRaceLegend]` — a judge station is an admin credential, so the caller
    must present a `MobileToken` bearer owned by a race admin (`RaceAdmin(role=ADMIN)` only — `can_edit_race` grants a bare superuser nothing, so
    one without such a row is 403); a missing/invalid
    token → `401`, a non-admin → actionable `403`, a bad build sig → neutral `403 {"detail":"Forbidden"}`.
    `CanEditRaceLegend` reads `view.kwargs["race_id"]` (loads `Race`, missing → 404, **without** the `is_published`
    filter — an unpublished race the admin owns clears the permission, then the view's `get_object_or_404(...,
    is_published=True)` 404s). Throttle scope `mobile-write`, 60/min). Ingests a batch of judge start/finish bracelet
    scans — a judge station scans **all teams of the race at once**, so the endpoint is **race-scoped with no
    `team_id`** (the one structural omission vs. `/track/`: no team-in-race check). `JudgeScan` (`models.py`) is
    immutable/write-only like `TrackPoint`: **PK = the client UUID `id`** (the idempotency key), FK
    `race → website.Race` CASCADE `related_name="judge_scans"`, **no `updated_at`, deliberately out of
    `versioning.py`** (an immutable row never touches the fingerprint/`sync` machinery), not admin-registered.
    Idempotency is `JudgeScan.objects.bulk_create(objs, ignore_conflicts=True)` — a re-sent `id` is silently
    skipped, no enrichment. **`source_install_id` is read from the signed body**, not the `X-Install-Id` header
    (same pattern as `/marks/`'s `source_install_id`, not `/track/`'s header-sourced `install_id`). **All-or-nothing
    400** (`serializer.is_valid(raise_exception=True)`): one malformed scan row 400s the whole batch, matching
    `/track/`/`/marks/` — not the contract prose's partial-accept wording. `nfc_uid` is **normalized on store**
    (`.strip().upper()`) in the view before building objs (`bulk_create` bypasses `save()` overrides) so it matches
    the normalized member `Tag` pool format for post-facto participant resolution — a **deliberate divergence from
    `Mark.cp_nfc_uid`/`MarkPresent.nfc_uid`, which are stored raw**. `event_type` is `ChoiceField(["start","finish"])`.
    Flow (`views.py:JudgeScanUploadView`): resolve published `Race` (404) → validate body via
    `JudgeScanUploadSerializer` (400) → no team check → read `source_install_id` from validated data → normalize
    each scan's `nfc_uid` → `bulk_create(ignore_conflicts=True)` → 200 `{"accepted": [all submitted ids]}`. Empty
    `scans` → ack `[]` (an empty `bulk_create` is a no-op). Read-side scoring, per-`participant_number` peak dedup,
    and admin reattribution/rendering of judge scans are **out of scope** (a future task).
  - **Member-tag bind** (`POST /app/race/<id>/member_tags/bind/`, name `member_tag_bind`, `MemberTagBindView`) —
    on the **per-person write layer** with the tag-create stack `[SignedAppPermission, IsMobileUser,
    CanEditRaceLegend]` (`RaceAdmin(role=ADMIN)` only — a bare superuser is 403, same as tag-create), throttle
    `mobile-write` (the one IP bucket shared with `/tags/`/`/track/`/`/marks/`/`/judge_scans/`). Binds a bracelet
    `nfc_uid → number` and returns its secret `code` (hex) to write into the bracelet; the full status table
    (201/200/404/409) lives in `src/apps/mobile/README.md`. Invariants: **no auto-rebind** — a known UID on a different
    number is 409 and mints nothing; a `404` means either an unpublished race or an unknown UID with
    `number: null` (clients tell them apart by `detail`). `Tag.code` (`BinaryField`, 16 random bytes, `website/0097`)
    is minted on insert, or lazily for a legacy row under `select_for_update()` with a re-check; an issued code never
    changes, is served by **no GET**, and is **not in `member_tags_version`** (creating a `Tag` moves the ETag, minting
    a code doesn't). The code belongs to the `Tag` **row**, not the UID (an `/admin/` `nfc_uid` edit carries it over).
    A bind with `number` sets `last_seen_at = MAX(last_seen_at)` of the global pool (`_bind_seen_at`; `now` if never
    scanned), so re-binding an aged-out v1 chip brings it back into the served window without moving the floor
    (stamping "now" could drop every other chip of an idle pool). **Accepted risk**: the v1 pool is global, so
    an admin of any published race can read any v1 bracelet's code (`number: null`). The v1 path (body without
    `code`) only ever sees global rows (`race IS NULL`).
  - **K24 v2 bracelets** (offline issuing; format, statuses and test vector in `src/apps/mobile/README.md`,
    «Браслеты v2»): the judge's phone writes `race_id u16 ‖ number u16 ‖ HMAC-SHA256(K_race, 0x22 ‖ uid ‖ race_id ‖
    number)[:12]` (the leading type byte is domain separation — keep it in any new chip type signed with the race key) (`crypto.member_code`) and later uploads the bind as `POST bind/` with `code` → `Tag(race, nfc_uid,
    number)` (201/200, a valid code for a new number **rebinds**, bad code 400, no 409). `Tag.race` FK
    (`website/0101`; uniqueness `(race, nfc_uid)` + partial `unique(nfc_uid) WHERE race IS NULL`). The race key is
    its own row `mobile.RaceChipKey` (O2O, `mobile/0014`, 32 B) — **never a `Race` column**: a stale full
    `Race.save()` from the edit form would write it back to NULL and the next request would mint a different key. It
    is served by `GET /app/race/<id>/chip_key/` (bind's stack, `no-store`). **Only the cloud mints a key**
    (`chip_key.ensure_chip_key`, `get_or_create`; or `manage.py ensure_chip_keys`); with
    `MOBILE_DATA_SOURCE=local` the endpoint 404s and the command refuses — never a fresh key, the LAN server gets it
    from the DB copy. `MarkPresent.code_ok` (`mobile/0014`) is recomputed at `/marks/` ingest over the **stored**
    present rows of the batch's marks, after the insert (`chip_key.recheck_present_codes` → `check_member_code`; a
    re-sent mark keeps its old rows but may have changed race): 24-hex code → HMAC recompute, 32-hex → global
    `Tag.code` (one query per batch, `v1_codes_for`), nothing to check → `None`; never rejects, shown as «⚠» on the
    app-data team page. `apps/race/app_data._tag_pool(race)` uses the same race-wins pool.
