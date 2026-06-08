# Mobile App Backend (`apps.mobile`): Signed Endpoints, Sync & Versioning

> Consolidated design for the `apps.mobile` Django app — the signed backend for the
> iOS/Android client. Merges four delivered plans:
> signed endpoints + install stats, the races list, teams + sync, and legend
> ETag/versioning. **All work is implemented** (see `git log` on
> `feat/mobile-app-signed-endpoints`); the per-task implementation checklists are
> omitted — this file is the reference design, not a worklist.

## Overview

A self-contained Django app `apps.mobile` (`label = "mobile"`, mounted at `/app/*`)
serving the mobile client **without user registration**. The app authenticates
*itself* with a shared secret baked into the binary plus a per-request
**HMAC-SHA256 signature**, so the data isn't reachable by plain `curl`. We accept
the secret is extractable from the binary — this deters casual scraping, not a
determined reverse-engineer (by design).

Four endpoints, all `AppAPIView` subclasses (signature gate + best-effort install
stats), reusing one signing/permission stack:

| Endpoint | Name | Returns | Conditional GET |
|---|---|---|---|
| `GET /app/race/<race_id>/legend/` | `mobile:legend` | race checkpoint legend | ETag / 304 |
| `GET /app/races/` | `mobile:races` | published-races list | — |
| `GET /app/race/<race_id>/teams/` | `mobile:teams` | full team list + members | ETag / 304 |
| `GET /app/race/<race_id>/sync/` | `mobile:sync` | pure manifest (versions + lease) | — |

Self-contained: existing `/api/` (timing/scanning) and `BearerTokenPermission`
(export) are **not** touched. Footprint into the rest of the project is additive
only: the new app, 1 line in `config/urls.py`, a few settings + reads, 1 line in
`kolco24.env.example`, and two additive model fields (`Athlet.updated_at`,
`Checkpoint.updated_at`).

## Context (from discovery)

- Django 4.2, source under `src/`, `manage.py` at `src/manage.py`. DRF in use, no
  global permissions (`REST_FRAMEWORK` only sets the JSON renderer).
- Feature apps live under `src/apps/<name>/` with a unique `AppConfig` label
  (`apps.race` → `label="race_app"`, `apps.accounts` → `label="accounts"`).
- Secrets read via `os.getenv(...)` (python-dotenv loads `src/.env`).
- `config/urls.py` mounts apps via `include(...)`.
- Legend source: `website.Checkpoint` (`src/website/models/checkpoint.py`) — fields
  `number`, `cost`, `type`, `description`, FK `race`; `CheckpointType.draft`
  excluded from the legend.
- Race list source: `website.models.race.Race` — `Meta.ordering = ["-date"]`,
  `is_published` BooleanField, `is_legend_visible` flag.
- Team source: `website.Team` (`category2` FK → `Category` → `race`; `TeamManager`
  excludes `is_deleted`) with member composition in `Athlet`
  (`team.athlet_set`; fields `name`/`birth`/`number_in_team`). Legacy `athlet1..6`
  on `Team` are deprecated and excluded.
- Existing `BearerTokenPermission` (static Bearer header, in
  `src/api/views/contributors.py`) is reference only — **not reused** (different
  purpose).
- Tests are pytest-style with `@pytest.mark.django_db` and `client` /
  `django_user_model` fixtures (not Django `TestCase`).
  `DJANGO_SETTINGS_MODULE=config.settings` from `pyproject.toml`.

## Solution Overview

### Authentication & stats (shared spine)

- **Approach:** a DRF `permission_class` (`SignedAppPermission`) does the crypto
  check — pure, it only stashes parsed metadata on `request`. A base `AppAPIView`
  records the `AppInstall` stat in `initial()` *after* permissions pass. This keeps
  `has_permission` free of heavy side effects while guaranteeing stats are written
  only for verified requests.
- **Fail closed:** an empty/missing `MOBILE_APP_SECRET` → every request 403, so a
  misconfigured deploy never leaks data.
- **Neutral errors:** any verification failure returns a single
  `403 {"detail": "Forbidden"}` — no hint *which* check failed (missing headers,
  bad/expired ts, bad sig). A `race_id` 404 is only reachable *after* a valid
  signature.
- **Replay mitigation:** signed unix timestamp with a ±300s freshness window. No
  nonce (deliberately out of scope). Body signing is GET-only for now.

### Versioning (one fingerprint, two consumers)

`apps/mobile/versioning.py` is the **single source of truth** for resource
versions. Each helper returns a bare `blake2b(digest_size=8)` hex over a `|`-joined
aggregate string. A view wraps the bare value in quotes for a strong `ETag`;
`SyncView` emits the same bare value in `versions.<resource>`. So an ETag and a
manifest probe can never disagree. `None` aggregates render as the literal
`"None"` → empty races stay stable, not a crash.

- `teams_version(race_id)` over
  `MAX(Team.updated_at)|MAX(Athlet.updated_at)|COUNT(Athlet)|COUNT(Team)`.
  `Athlet.updated_at` was added (migration `0076`) **specifically** so a member
  rename moves `MAX(Athlet.updated_at)` — `MAX(Team.updated_at)` alone would miss
  it.
- `legend_version(race_id)` over
  `MAX(Checkpoint.updated_at)|COUNT|is_legend_visible`, aggregated over the
  **draft-excluded** queryset the view serves. `Checkpoint.updated_at` was added
  (migration `0077`) for the same reason as `Athlet.updated_at`. `is_legend_visible`
  is folded in so a hide/show moves the version; it's re-queried by `race_id` (not
  taken from the view's `Race`) to keep the bare-`race_id` single-source contract.

### Sync (the cheap probe)

`SyncView` is a **pure manifest** — two aggregate reads, no serialization, no
ETag/304 of its own. It lets the client probe once and re-fetch only changed
resources. The lease/handoff is **stubbed**: `data_source` comes from
`MOBILE_DATA_SOURCE` (default `"cloud"`), `lease_expires_at` is always `null`; the
real per-race local-server lease is deferred.

## Technical Details

### Request headers (from app)

| Header | Content |
|---|---|
| `X-Install-Id` | UUID generated by the app on first launch, stored locally |
| `X-App-Platform` | `ios` / `android` |
| `X-App-Version` | e.g. `1.4.0` |
| `X-App-Ts` | unix time (seconds) at request build |
| `X-App-Sig` | hex of HMAC-SHA256 |

### Canonical string (what's signed)

```
method.upper() + "\n" + full_path + "\n" + ts + "\n" + sha256_hex(body)
```

- `full_path` = `request.get_full_path()` (path **with** query string, so `?param=`
  can't be tampered — clients must call the bare path).
- `sha256_hex(body)` = hex SHA-256 of the raw body (empty for GET → hash of `b""`).
  Signing the body *hash* (not the body) keeps the canonical small.

### `signing.py`

- `build_canonical(method, full_path, ts, body: bytes) -> str`
- `sign(secret, canonical) -> str` → `hmac.new(secret.encode(), canonical.encode(), sha256).hexdigest()`
- `verify(secret, canonical, provided_sig) -> bool` → `hmac.compare_digest(...)` (constant-time)

### `permissions.py` — `SignedAppPermission.has_permission`

1. `secret = getattr(settings, "MOBILE_APP_SECRET", "") or ""` → empty → `False` (fail closed).
2. read `X-App-Sig`, `X-App-Ts`, `X-Install-Id`; any missing → `False`.
3. `ts` not int → `False`.
4. `abs(time.time() - ts) > settings.MOBILE_APP_TS_WINDOW` → `False`.
5. `verify(...)` over `build_canonical(method, get_full_path(), ts, body)` false → `False`.
6. success → stash `request.app_meta = {install_id[:64], platform[:16], app_version[:32], ip}` → `True`.

- `message = "Forbidden"` so DRF returns the neutral `{"detail": "Forbidden"}` body.
- `_client_ip(request)`: first `X-Forwarded-For` entry, fall back to `REMOTE_ADDR`.

### `views.py`

- **`AppAPIView(APIView)`**: `authentication_classes = []`,
  `permission_classes = [SignedAppPermission]`. Overrides `initial()` to call
  `super().initial()` (runs `check_permissions`) **then** `_record_install()`.
  `_record_install` reads `request.app_meta`, does
  `AppInstall.objects.update_or_create(install_id, defaults={...})` then a separate
  `F("request_count") + 1` increment — wrapped in `try/except` +
  `logger.exception` so a stats-write failure never breaks the response.
- **`LegendView.get(request, race_id)`**: `get_object_or_404(Race)`;
  `quoted = f'"{legend_version(race_id)}"'`. If
  `request.headers.get("If-None-Match") == quoted` → `HttpResponseNotModified()`
  with `ETag` set, no serialization. Else serialize
  `Checkpoint.objects.filter(race=race).order_by("number", "id")` (the `id`
  tie-breaker keeps output deterministic), set `ETag` on the response. When
  `race.is_legend_visible is False` return `{"race": id, "checkpoints": []}`
  (200, empty list — not 403) with the ETag still set, so a later un-hide is
  detected.
- **`RaceListView.get(request)`**:
  `Response({"races": RaceListSerializer(Race.objects.filter(is_published=True), many=True).data})`
  (`Meta.ordering = ["-date"]`). No pagination, no query string (YAGNI).
- **`TeamsView.get(request, race_id)`**: `get_object_or_404(Race)`;
  `quoted = f'"{teams_version(race_id)}"'`. **304 path:** `If-None-Match` match →
  `HttpResponseNotModified()` + ETag, early return before touching the queryset.
  **200 path:** serialize
  `Team.objects.filter(category2__race=race).order_by("id")` with
  `prefetch_related(Prefetch("athlet_set", queryset=Athlet.objects.order_by("number_in_team", "id")))`,
  set ETag, return `{"race": id, "teams": [...]}`. Teams with `category2=None` are
  out of scope (a race owns teams via `category2.race`) — deliberately excluded
  from both the fingerprint and the list.
- **`SyncView.get(request, race_id)`**: `get_object_or_404(Race)`; returns
  `{"race": id, "data_source": settings.MOBILE_DATA_SOURCE, "lease_expires_at": None,
  "versions": {"teams": teams_version(race_id), "legend": legend_version(race_id)}}`.
  No If-None-Match/304.

### `serializers.py`

- **`LegendCheckpointSerializer`** on `Checkpoint`,
  `fields = ["number", "cost", "type", "description"]` (never exposes `id`/`year`).
- **`RaceListSerializer`** on `Race`, exactly the 8 core fields
  `["id", "name", "slug", "date", "date_end", "place", "reg_status", "is_legend_visible"]`
  (no images).
- **`TeamSerializer`** on `Team`: `id`, `teamname`,
  `category2` (`IntegerField(source="category2_id")`), `ucount`, `paid_people`,
  `start_time`, `finish_time`, and nested `members` (`name`, `birth`,
  `number_in_team`). `members` iterates the **prefetched** relation directly —
  no `.order_by(...)` in the serializer (ordering comes from the view's
  `Prefetch`, so the prefetch isn't defeated / no N+1).

### `versioning.py`

```python
# teams_version
Team.objects.filter(category2__race_id=race_id).aggregate(Max("updated_at"), Count("id"))
Athlet.objects.filter(team__category2__race_id=race_id).aggregate(Max("updated_at"), Count("id"))
# legend_version
Checkpoint.objects.filter(race_id=race_id)
    .exclude(type=CheckpointType.draft.value)
    .aggregate(max_updated=Max("updated_at"), count=Count("id"))
Race.objects.filter(pk=race_id).values_list("is_legend_visible", flat=True).first()
```

Each builds a `|`-joined raw string (`None` → literal `"None"`) and returns
`hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()`. The `is_legend_visible`
re-query is **deliberate** — keep the bare-`race_id` signature; don't "optimize" it
into accepting the view's `Race` (it would break the single-source-of-truth
contract).

### Model `AppInstall`

One row per app-generated `install_id`, read-only admin (view stats only):

- `install_id` CharField(64, unique, db_index)
- `platform` CharField(16, blank), `app_version` CharField(32, blank)
- `first_seen` (auto_now_add), `last_seen` (auto_now)
- `last_ip` GenericIPAddressField(null, blank)
- `request_count` PositiveIntegerField(default=0)

### Settings & URLs

- `src/config/settings.py`: `MOBILE_APP_SECRET = os.getenv("MOBILE_APP_SECRET", "")`,
  `MOBILE_APP_TS_WINDOW = 300`, `MOBILE_DATA_SOURCE = os.getenv("MOBILE_DATA_SOURCE", "cloud")`;
  `"apps.mobile"` in `INSTALLED_APPS`.
- `src/config/urls.py`: `path("app/", include("apps.mobile.urls"))`.
- `apps/mobile/urls.py`: `app_name = "mobile"` with routes
  `legend` / `races` / `teams` / `sync`.
- `deploy/kolco24.env.example`: `MOBILE_APP_SECRET=` line.

### Migrations

- `website` `0076_athlet_updated_at` — `Athlet.updated_at = DateTimeField(auto_now=True)`.
- `website` `0077_checkpoint_updated_at` — `Checkpoint.updated_at = DateTimeField(auto_now=True)`.

Both `auto_now`: existing rows get the migration timestamp on first save — one
harmless version bump after deploy, no data backfill.

## Accepted limitations (from plan review)

- **Body signing is GET-only.** `build_canonical` hashes the body generically, but
  every endpoint is GET (empty body). DRF's `request.body` can raise
  `RawPostDataException` once a stream is parsed — revisit raw-body reading in the
  permission before adding any body-bearing POST.
- **Stats are best-effort.** `update_or_create` + a separate `F()` increment are
  two non-transactional queries; under concurrent requests from the same
  `install_id` an insert race or lost increment is possible, and any error is
  swallowed. Exact, race-free counting is out of scope — the response must never
  fail because of stats.
- **No nonce.** Replay mitigation is the ±300s ts window only.
- **`CheckpointTag` out of scope.** The legend never exposes tags, so a tag edit
  deliberately does *not* move `legend_version`.
- **Lease stubbed.** `data_source` from `MOBILE_DATA_SOURCE` (cloud default),
  `lease_expires_at` always `null`; the real per-race local-server lease/handoff is
  deferred.

## Post-Completion

*Items requiring manual intervention or external systems — informational only.*

**Manual verification**

- After deploy, confirm a plain `curl /app/race/<id>/legend/` returns 403 and a
  correctly-signed request returns 200.
- Spot-check the admin `AppInstall` list shows installs and increasing
  `request_count`.

**External / deploy**

- Generate a strong `MOBILE_APP_SECRET` and set it in `deploy/kolco24.env` (and any
  env where the app runs) — identical to the value compiled into the iOS/Android
  clients.
- Hand the mobile devs the client-side signing recipe (header names, canonical
  string, ts window) so clients produce a matching `X-App-Sig`.
- Set `MOBILE_DATA_SOURCE` only where a local-race server should later advertise
  `local`; cloud needs no action.

**Mobile-client follow-ups (independent, server is backward compatible)**

- Render the competitions list from `GET /app/races/`, drill into `mobile:legend`/
  `mobile:teams` by `race_id`.
- Implement the conditional-GET loop: store the ETag from `/legend/` and `/teams/`,
  probe `/sync/` (one cheap signed request), re-fetch only resources whose
  `versions.*` changed, sending `If-None-Match` to hit the 304 path.

**Deferred follow-ups (out of scope)**

- The real per-race lease/handoff (`data_source: "local"` + computed
  `lease_expires_at`) described in the README's "Два сервера и хэндофф".
