# Mobile App: Races List Endpoint

## Overview
- Add a signed mobile-app endpoint `GET /app/races/` (URL name `mobile:races`) that
  returns the list of published competitions for the iOS/Android app.
- Lets the app render a competitions list and then drill into the existing
  `mobile:legend` endpoint (which is keyed by `race_id`).
- Built entirely on the existing signed-endpoint infrastructure in `apps.mobile`
  (`AppAPIView` + `SignedAppPermission`) — no new auth, no model, no migration.

## Context (from discovery)
- Files/components involved:
  - `src/apps/mobile/urls.py` — URL routing (`app_name = "mobile"`, `mobile:legend` today).
  - `src/apps/mobile/serializers.py` — DRF serializers (`LegendCheckpointSerializer`).
  - `src/apps/mobile/views.py` — `AppAPIView` base (signature gate + install stats) and `LegendView`.
  - `src/apps/mobile/tests.py` — pytest-style tests, signed-request helpers.
  - `CLAUDE.md` — `apps.mobile` architecture paragraph.
- Related patterns found:
  - `AppAPIView` sets `authentication_classes = []`, `permission_classes = [SignedAppPermission]`,
    and records install stats in `initial()` (best-effort, never breaks the response).
  - `LegendView` returns a **wrapped** object `{"race": id, "checkpoints": [...]}`.
  - `Race` (`website/models/race.py`) has `Meta.ordering = ["-date"]` and an
    `is_published` BooleanField; `RegStatus` choices are `upcoming/open/sold_out`.
  - Tests use `_signed_headers(method, path, secret)` to build valid signed headers,
    and assert neutral `403 {"detail": "Forbidden"}` for auth failures.
- Dependencies identified: none new — reuses `SignedAppPermission`, `signing.py`, DRF.

## Development Approach
- **Testing approach**: Regular (code first, then tests) — confirmed.
- Single logical unit (one endpoint); implement view + serializer + URL together, then tests.
- **Every code change ships with tests** — success + exclusion + auth-failure cases.
- **All tests must pass before completion**: `uv run pytest src/apps/mobile/tests.py`.
- Run `make format && make lint` before commit (project convention).
- No backward-compat concerns — additive endpoint.

## Testing Strategy
- **Unit/request-level tests** (pytest, `@pytest.mark.django_db`): mirror the existing
  legend request-level tests using `client` + `_signed_headers`.
- **No e2e tests** — project has no UI e2e suite; the mobile client is external.

## Progress Tracking
- Mark completed items `[x]` immediately.
- `➕` prefix for newly discovered tasks; `⚠️` for blockers.

## Solution Overview
- Add `RaceListSerializer` exposing the agreed core fields (no images).
- Add `RaceListView(AppAPIView)` whose `get` returns published races, wrapped as
  `{"races": [...]}` (consistent with `LegendView`).
- Wire `path("races/", ...)` into `apps/mobile/urls.py`.
- Signature verification + install-stat recording are inherited from `AppAPIView`;
  the canonical string for this no-param GET is `GET\n/app/races/\n<ts>\n<sha256("")>`,
  already supported by `signing.py` / `SignedAppPermission` unchanged.

## Technical Details
- **Serializer** `RaceListSerializer(ModelSerializer)` on `website.models.race.Race`,
  `fields = ["id", "name", "slug", "date", "date_end", "place", "reg_status", "is_legend_visible"]`.
- **View** `RaceListView(AppAPIView)`:
  ```python
  def get(self, request):
      qs = Race.objects.filter(is_published=True)  # Meta.ordering = ["-date"]
      return Response({"races": RaceListSerializer(qs, many=True).data})
  ```
- **URL**: `path("races/", RaceListView.as_view(), name="races")` → `/app/races/`.
- Response shape: `{"races": [ {8 fields}, ... ]}`. No pagination (small list, YAGNI).
- **No query string**: the signature covers `full_path` (query string included), so the
  client must call `/app/races/` with no query params, or the signature breaks. Tests
  sign and request the exact same path (as the legend tests do).

## What Goes Where
- **Implementation Steps** (checkboxes): serializer, view, URL, tests, docs — all in-repo.
- **Post-Completion** (no checkboxes): mobile client must add the `/app/races/` call;
  `MOBILE_APP_SECRET` must be set in the deploy env (already required by `mobile:legend`).

## Implementation Steps

### Task 1: Add races-list endpoint (serializer + view + URL)

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/urls.py`

- [x] add `RaceListSerializer(ModelSerializer)` in `serializers.py` importing
      `website.models.race.Race`, with the 8 core fields (no images)
- [x] add `RaceListView(AppAPIView)` in `views.py` with a `get` returning
      `Response({"races": RaceListSerializer(Race.objects.filter(is_published=True), many=True).data})`
- [x] add `path("races/", RaceListView.as_view(), name="races")` to `urls.py`
      and update the `from .views import ...` import
- [x] write request-level tests in `src/apps/mobile/tests.py` (success):
      valid signed `GET /app/races/` → 200, returns only `is_published=True` races
      in `-date` order, each item has exactly the 8 fields, response is `{"races": [...]}`
- [x] write tests for exclusion + auth-failure cases:
      unpublished race excluded; no-headers/bad-signature → neutral `403 {"detail": "Forbidden"}`;
      empty response (`{"races": []}`) when no published races exist
- [x] run tests — must pass before next task: `uv run pytest src/apps/mobile/tests.py`

### Task 2: Verify acceptance criteria
- [x] verify `GET /app/races/` returns only published races, `-date` order, wrapped shape, 8 fields
- [x] verify auth failures return neutral `403 {"detail": "Forbidden"}` (no leak)
- [x] run `make format && make lint`
- [x] run full mobile suite: `uv run pytest src/apps/mobile/tests.py`

### Task 3: [Final] Update documentation
- [x] update the `apps.mobile` paragraph in `CLAUDE.md` to document the second
      endpoint `GET /app/races/` (`mobile:races`): published-only, `-date` order,
      8 core fields, wrapped `{"races": [...]}`, no pagination
- [x] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only*

**External system updates:**
- Mobile client (iOS/Android): add the signed `GET /app/races/` call and parse `{"races": [...]}`.
- Deploy env: `MOBILE_APP_SECRET` must be set (already required by the existing `mobile:legend` endpoint).
