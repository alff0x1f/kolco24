# Custom Error Pages (404 / 500 / 403) — «Сбились с маршрута»

## Overview
- Replace Django's bare default error pages with custom, on-brand pages that match the
  modern `base-2.html` design (Rubik font, `theme-2.css`).
- Concept: **«Сбились с маршрута»** — a friendly rogaine/orienteering metaphor
  (lost on the route / this checkpoint isn't on the map). Restrained, themed-but-tasteful —
  not a heavy illustration.
- Scope: all three pages — **404, 500, 403**.
- Benefit: users hitting a missing/forbidden/broken page get a polished, navigable page
  in the site's voice instead of Django's unstyled default, with a clear path back home.

## Context (from discovery)
- **Design system**: `src/templates/website/base-2.html` is the modern stack — dark sticky
  navbar + DB-backed footer baked in, Rubik font, `src/static/css/theme-2.css`.
- **Reusable theme-2 classes**: `.container`, `.page` (do NOT redefine), `.page-head`
  (`h1` + `.sub`), `.card`/`.card-pad`, `.btn` `.btn-primary` `.btn-ghost` `.btn-lg`
  `.btn-block`. Color tokens in `:root`: `--ink #161a1f`, `--muted #6b7785`,
  `--primary #2a5288`, `--primary-2 #4582EC`, `--primary-dk #1d3a63`, `--success #02B875`,
  `--dark #1d242d`, `--paper #eef0f3`, `--paper-2 #fff`, `--radius 14px`.
- **Template discovery**: `settings.TEMPLATES[0].DIRS = [BASE_DIR/templates]` → `src/templates/`.
  Django's default handlers (`page_not_found`, `server_error`, `permission_denied`)
  auto-load `404.html` / `500.html` / `403.html` from the top of that dir. **No handler
  wiring in `config/urls.py` is needed.**
- **Render-context asymmetry (the core constraint)**:
  - `404.html` + `403.html` are rendered **with a `RequestContext`** → `request`, `user`,
    `{% url %}`, context processors all available.
  - `500.html` is rendered by `server_error` with an **empty `Context()`** and **no context
    processors**. `base-2.html`'s navbar reads `{{ user }}`/`request.session` and its footer
    calls the `{% footer_menu %}` template tag which **hits the DB** — unsafe in a 500
    (the DB may be the cause). So `500.html` must NOT extend `base-2.html`.
- **No custom error pages exist today** — `find` shows none; no `handler404` in `config/urls.py`.
- **Assets**: logo at `src/static/images/logo.png` and favicon at `src/static/favicon.ico`
  (both used by navbar). `DEBUG` is env-driven (`settings.py:58`); the `os.getenv` fallback is
  `False`, **but `src/.env` actually sets `DEBUG=True`** for local dev — so plain `runserver`
  shows Django's technical error pages, not these custom ones (see Testing Strategy). Static
  served by WhiteNoise from `STATIC_ROOT`.
- **Tests**: `src/website/tests.py`, pytest-style functions with `@pytest.mark.django_db`
  and `client` / `django_user_model` fixtures (NOT `TestCase`).

## Development Approach
- **Testing approach**: Regular (templates first, then tests) — matches this codebase.
- Complete each task fully before moving to the next; small, focused changes.
- **Every task includes tests** for the behavior it adds.
- **All tests must pass before starting the next task.**
- Run `make format && make lint` before finishing (standing user rule).
- Follow CLAUDE.md `base-2` rules: extend `base-2.html`, page CSS in
  `src/static/css/<page>.css` via `{% block extra_head %}`, scope custom rules under
  `.error-page` (never redefine bare `.page`), write markup in the base-2 idiom.
- Do NOT edit vendored/min assets or `theme.css` (downloaded theme).

## Testing Strategy
- **Unit/integration tests** (pytest-style in `src/website/tests.py`):
  - **404**: `client.get("/this-does-not-exist/")` → assert `status_code == 404` and a unique
    marker from `404.html` (e.g. `сбились с маршрута`) is in the decoded content. This renders
    the custom `404.html` because **pytest-django forces `settings.DEBUG = False` for the test
    session** (`django_debug_mode` ini default; this repo does not set it to `keep`) —
    independent of `src/.env`'s `DEBUG=True`. The test client host `testserver` is auto-allowed.
  - **500 standalone-safety**: `get_template("500.html").render({})` (empty dict, no request,
    no DB) → assert it does **not** raise and contains the 500 marker. This proves the page
    has no context-processor / DB dependency. Needs **no** `@pytest.mark.django_db` — and that
    absence is itself part of what the test guarantees.
  - **403**: `get_template("403.html").render({})` → assert marker present and no error.
    **Must carry `@pytest.mark.django_db`**: 403 extends `base-2.html`, whose footer calls
    `{% footer_menu %}` (a `MenuItem.objects.all()` query), so the render legitimately touches
    the DB (an empty `MenuItem` table is fine). `{{ user }}` / `request.session` render empty
    under the empty context without error.
  - Optional: assert the 404 response template list contains `404.html` (i.e. our custom
    template, not Django's technical default).
- **No e2e framework** in this project — none required.

## Progress Tracking
- Mark completed items `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.
- Keep this plan in sync with actual work.

## Solution Overview
**Option A — split chrome** (chosen):
- `404.html` and `403.html` **extend `website/base-2.html`** → they get the real navbar
  (with login state / menus) and the real footer for free, because they render with a
  `RequestContext`.
- `500.html` is **standalone**: a full `<!doctype html>` document that links `theme-2.css`
  + the shared `error.css`, with a **static** mini-header (logo + brand text linking to `/`,
  no user menu) and **no** DB-backed footer. It references nothing dynamic — links are
  hardcoded `/`, no `{{ user }}`, no `{% url %}`, no `{% footer_menu %}`.
- All three share one stylesheet `src/static/css/error.css`, scoped under `.error-page`,
  so the visuals stay consistent across the three even though the chrome differs.

Rationale: each page is correct for its actual render context; the 500 stays safe even
with the DB down; visual consistency is preserved through shared CSS.

## Technical Details
- **New files**:
  - `src/templates/404.html` — `{% extends "website/base-2.html" %}`, sets `{% block title %}`,
    `{% block extra_head %}` → link `error.css`, `{% block content %}` → `.error-page` hero.
  - `src/templates/403.html` — same shape as 404, different code/copy.
  - `src/templates/500.html` — standalone doc; inline minimal `<head>` (charset, viewport,
    title, Rubik preconnect+link mirroring base-2, `theme-2.css`, `error.css`, favicon).
    **Hardcode all asset URLs** (`/static/css/theme-2.css`, `/static/css/error.css`,
    `/static/favicon.ico`, `/static/images/logo.png`) for maximum standalone safety, instead
    of `{% static %}` — consistent with the page's zero-dependency intent. Static `.nav`
    header (logo + «Кольцо 24» → `/`), `.error-page` hero. No footer or a one-line static
    footer.
  - `src/static/css/error.css` — all rules under `.error-page`: hero layout (centered,
    vertical rhythm), large stylized error code (`.error-code`), inline SVG icon styling
    (`.error-art`, single-color via `currentColor` / `--primary` tokens), CTA spacing,
    `600px` responsive breakpoint matching theme-2 (`@media (max-width: 600px)`, theme-2.css:591).
- **Copy (Russian, friendly rogaine tone)**:
  - **404** — H1 «Похоже, вы сбились с маршрута» / sub «Этого КП нет на карте — страница не
    найдена.» — CTA «На главную» (`btn btn-primary btn-lg`, `href="/"`) + secondary
    `btn-ghost` link (e.g. «Все гонки» or «Назад»).
  - **403** — H1 «Доступ закрыт» / sub «У этого КП стоит судья — сюда вам нельзя.» —
    CTA «На главную».
  - **500** — H1 «Что-то сломалось на дистанции» / sub «Мы уже разбираемся. Попробуйте
    обновить страницу чуть позже.» — CTA «На главную» (`href="/"`).
- **Icon**: one simple inline SVG fitting the theme (compass / map-pin / checkpoint flag),
  using `currentColor` so it tints from the surrounding text/token color. Reuse the same
  SVG (or close variants) across pages for consistency.
- **No `urls.py` / `settings.py` changes** for the feature itself (default handlers find the
  templates by name). Optional temporary preview URLs are dev-only and removed before commit.

## What Goes Where
- **Implementation Steps** (`[ ]`): create the 4 files + tests, optional dev-preview scaffold,
  format/lint, verification.
- **Post-Completion** (no checkboxes): manual visual review with `DEBUG=False`, deploy note.

## Implementation Steps

### Task 1: Shared error stylesheet
**Files:**
- Create: `src/static/css/error.css`

- [x] Create `src/static/css/error.css` with ALL rules scoped under `.error-page`
      (centered hero, vertical rhythm, container max-width reuse).
- [x] Add `.error-code` (large stylized 404/500/403), `.error-art` (inline SVG sizing/color
      via `currentColor` + `--primary` tokens), CTA row spacing.
- [x] Add `@media (max-width: 600px)` breakpoint matching theme-2 (theme-2.css:591) — smaller
      code/heading on mobile.
- [x] Do NOT redefine bare `.page` (theme-2 owns it); verify no class collisions with theme-2.
- [x] Tests: covered indirectly by template render tests in later tasks — note that CSS itself
      has no unit test (asset-only); marker assertions live with the templates.

### Task 2: 404 page (extends base-2)
**Files:**
- Create: `src/templates/404.html`
- Modify: `src/website/tests.py`

- [x] Create `src/templates/404.html` extending `website/base-2.html`; set `{% block title %}`,
      link `error.css` in `{% block extra_head %}`.
- [x] Build `{% block content %}` → `<main class="page error-page">` hero: SVG art, `.error-code`
      404, H1 «Похоже, вы сбились с маршрута», `.sub`, CTA «На главную» (`href="/"`) +
      secondary `btn-ghost`.
- [x] Write test: `client.get("/no-such-page/")` → `status_code == 404` and marker
      «сбились с маршрута» in `resp.content.decode()`.
- [x] Write test (edge): assert `"404.html"` is among the response's used template names
      (custom template, not Django default).
- [x] Run tests — must pass before next task: `uv run pytest src/website/tests.py -k error`.

### Task 3: 403 page (extends base-2)
**Files:**
- Create: `src/templates/403.html`
- Modify: `src/website/tests.py`

- [x] Create `src/templates/403.html` extending `website/base-2.html`; same shape as 404,
      `.error-code` 403, H1 «Доступ закрыт», sub «У этого КП стоит судья — сюда вам нельзя.»,
      CTA «На главную».
- [x] Write test (carry `@pytest.mark.django_db` — 403 inherits base-2's DB-backed footer):
      `get_template("403.html").render({})` does not raise and contains the 403 marker text.
- [x] Run tests — must pass before next task.

### Task 4: 500 page (standalone, DB/context-free)
**Files:**
- Create: `src/templates/500.html`
- Modify: `src/website/tests.py`

- [x] Create standalone `src/templates/500.html` (`<!doctype html>`): minimal `<head>`
      mirroring base-2 (charset, viewport, title, Rubik link). **Hardcode** asset URLs —
      `/static/css/theme-2.css`, `/static/css/error.css`, `/static/favicon.ico` (no
      `{% static %}`, no `{% load %}`).
- [x] Add static `.nav` mini-header: `/static/images/logo.png` (hardcoded) + «Кольцо 24»
      linking to `/`, NO user menu.
- [x] Add `<main class="page error-page">` hero: `.error-code` 500, H1 «Что-то сломалось на
      дистанции», sub «Мы уже разбираемся…», CTA «На главную» (`href="/"`). NO `{{ user }}`,
      NO `{% url %}`, NO `{% footer_menu %}`, NO DB access.
- [x] Write test (standalone-safety): `get_template("500.html").render({})` does NOT raise and
      contains the 500 marker — proves zero context-processor / DB dependency.
- [x] Run tests — must pass before next task.

### Task 5: (Optional, dev-only) Local preview scaffold
**Files:**
- Modify: `src/config/urls.py` (TEMPORARY — remove before commit)

- [x] manual visual-iteration scaffold (skipped — optional, dev-only, not automatable)
- [x] manual visual-iteration scaffold (skipped — optional, dev-only, not automatable)
- [x] No throwaway scaffold added; verified `grep __err` returns no code references (only this plan doc).

### Task 6: Verify acceptance criteria
- [x] All three templates render in the site style; 500 verified DB/context-free.
- [x] Confirm no `handler404/500/403` was added to `config/urls.py` and no dev scaffold remains.
- [x] Run full suite: `uv run pytest`.
- [x] Run `make format && make lint` — clean.

### Task 7: [Final] Docs & cleanup
- [x] Update CLAUDE.md only if a new reusable pattern emerged (likely a one-line note that
      `src/templates/{404,403,500}.html` exist; 500 is standalone by design). Keep minimal.
- [x] Move this plan to `docs/plans/completed/`.

## Post-Completion
*Informational — manual / external, no checkboxes*

**Manual verification**:
- Note: `src/.env` has `DEBUG=True`, so plain `runserver` shows Django's technical error pages,
  NOT these custom ones. To review the real pages, either:
  (a) temporarily set `DEBUG=False` in `src/.env`, run `uv run python src/manage.py collectstatic`
      (so WhiteNoise serves `error.css`/`logo.png` — `runserver`'s auto static-serving is off
      when `DEBUG=False`), then `runserver`; or
  (b) use the Task 5 dev scaffold (renders templates directly via a view, bypassing the DEBUG gate).
- Visually review all three on desktop and a ~375px mobile viewport; confirm navbar/footer
  render on 404/403 and the static header renders on 500.
- Sanity-check the 500 page with the DB stopped (`docker compose stop kolco24_db`) to confirm
  it still renders without error.

**Deploy note**:
- `collectstatic` runs at Docker build time, so `error.css` ships automatically; no extra
  deploy step. No migrations involved.
