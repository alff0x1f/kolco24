# Race news/article form on its own page + rich editor

## Overview

Today the race page (`/race/<slug>/`) renders the whole news-post creation form inline, for any race
admin or moderator, above the publication feed. The form has 7 fields and a plain `<textarea>` for the
Markdown body; `AddNewsPostView` is POST-only (GET returns 405) and re-renders the entire race page on a
validation error, which is the sole reason it imports `RacePageView` through a deferred import inside the
method to dodge a circular dependency. There is no way to edit a post from the site at all — that is
`/admin/`-only.

This change:

- moves the form onto its own page, backed by one CBV that handles **both** create and edit
  (`RaceEditView` pattern), living in `apps.race` next to the other race sub-pages;
- adds post editing (`race/<slug>/post/<id>/edit/`), reachable from the feed;
- gives the Markdown body the same **EasyMDE** rich editor used by `/page/<slug>/edit/`, with the editor
  init extracted to one shared JS file and both EasyMDE and a FontAwesome subset **vendored** (no CDN,
  per the Leaflet precedent);
- makes drafts and scheduled posts visible **in the race feed to race admins only**, so a draft saved
  from the new page is reachable instead of vanishing.

Benefits: the race page loses ~60 lines of admin-only form markup; the circular-dependency workaround is
deleted; the editor exists once instead of twice; `/page/<slug>/edit/` stops depending on unpkg at runtime.

Note on the icons — the toolbar **is** iconed today, from a CDN nobody configured. `base.html` loads no
FontAwesome and `easymde.min.css` bundles no icon font, but EasyMDE injects its own stylesheet link at
runtime: verified in `easymde.min.js` 2.18.0, it appends
`https://maxcdn.bootstrapcdn.com/font-awesome/latest/css/font-awesome.min.css` unless
`autoDownloadFontAwesome === false`, and its "already loaded" detection matches **only** hrefs containing
`//maxcdn.bootstrapcdn.com/font-awesome/`. So `/page/<slug>/edit/` renders icons as long as that CDN is
reachable, and a **vendored stylesheet is invisible to that check** — without
`autoDownloadFontAwesome: false` in the config, vendoring FontAwesome changes nothing and the page keeps
calling the CDN. Removing that runtime dependency is the actual win here, not adding icons.

## Context (from discovery)

Files/components involved:

- `src/website/views/views_.py:49` — `is_race_admin(user, race)`, any `RaceAdmin` row (ADMIN **or**
  MODERATOR). Stays here; `RacePageView` imports it from here.
- `src/website/views/views_.py:55` — `AddNewsPostView`, to be deleted.
- `src/website/views/__init__.py:3` — **re-exports `AddNewsPostView`**; must be edited in the same task as
  the deletion or Django fails to start and every test errors.
- `src/website/urls.py:80-84` — the `add_post` route.
- `src/apps/race/views.py:121` — `RacePageView.build_context`; `news_qs` at ~line 139, `post_form` at
  ~lines 173-174.
- `src/apps/race/views.py:861` — `RaceEditView`'s docstring says "Auth mirrors ``AddNewsPostView``", a
  dangling reference once that class is gone.
- `src/templates/race/race_page.html:23-86` — the inline form; `.publication-feed__head` just below it;
  the feed partial include at line 94.
- `src/templates/website/_publication_post.html` — shared by **three** include sites: `home.html:29`,
  `publication_list.html:25` (the `/news/`, `/articles/` catalog) and `race_page.html:94`.
- `src/templates/website/edit_page.html` — the editor reference (unpkg EasyMDE + ~35-line inline init);
  renders `{{ form.content }}`, so a widget attr must come from `PageForm` (`src/website/forms.py:352`).
- `src/website/views/views_.py:231` — `edit_page` gates on the Django **group** `"Moderators"`, which is
  unrelated to `RaceAdmin.Role.MODERATOR` used everywhere else in this plan.
- `src/website/forms.py:358` — `NewsPostForm`.
- `src/website/models/news.py:138,152` — `NewsPostQuerySet.visible()` and `NewsPost`.
- `src/static/vendor/leaflet/` — the vendoring precedent, loaded at the **end of the content block** in
  `race/map.html:59`, not from `extra_head`.

**Existing tests this change breaks** (verified — must be updated, not just left to fail):

- `src/website/tests.py:530` `test_add_post_invalid_form_shows_errors` asserts
  `"race/race_page.html" in [t.name for t in response.templates]` and `"post_form" in response.context`.
  Both die with the new view, template and `form` context key. The other three `test_add_post_*` tests at
  lines 215/238/249 test behavior the new view keeps and should **move** to `src/apps/race/tests.py`.
- `src/apps/race/tests.py:483` `test_race_overview_visibility` asserts
  `(post in response.context["news_list"]) is published`, with the comment "The feed uses public
  visibility rules even in a private race preview." The admin branch of the new `news_qs` drops the
  `race__is_published` filter that `visible()` carries, so an admin/moderator previewing an **unpublished**
  race now *does* see its posts. That is the desired behavior (an admin preview should show the admin's own
  posts, and `PublicationDetailView` already serves them — the same test asserts a 200 on the post's detail
  URL at line 496), but it is a second deliberate behavior change and the assertion plus its comment must
  be updated **per role, not wholesale**.
- **The `superuser` role in that test keeps the old expectation.** It is parametrized with **no `RaceAdmin`
  row** (only `admin`/`moderator`/`other_admin` get one at line 471), and `is_race_admin` checks solely for
  such a row — so `is_admin` is `False` for a bare superuser. They reach the page (`RacePageView.get`
  admits `request.user.is_superuser`) but get the **public** feed: no drafts, no scheduled posts, no
  «Новая публикация» button. That asymmetry is intentional and matches the documented `can_edit_race` rule
  that being a superuser grants nothing without a row; only `admin` and `moderator` change behavior here.

Related patterns found:

- `RaceEditView` is one CBV backing two URL names (`add_race`/`edit_race`) — the pattern to follow.
- `apps/race/views.py` has five `_load_and_authorize`-style helpers (lines 865, 1145, 1410, 1459, …) all
  returning a `(race, response)` tuple where a non-`None` response means "return it as-is". The new helper
  must match this shape, not invent a tuple-or-response union.
- `race_form.html` — the sibling *form* page — deliberately does **not** include `_race_header.html`; it
  uses a `.breadcrumb` block instead, precisely because the header needs race-page context.
- CLAUDE.md rules that bind here: new pages extend `base-2.html`; never a bare `.page` class in page CSS;
  never `{{ form.field }}` on a base-2 page (Django emits Bootstrap `form-control`, which fights
  `theme-2.css`) — write fields manually; `src/static/vendor/` is **off-limits for edits**.
- Tests in `src/apps/race/tests.py` are pytest-style functions with `@pytest.mark.django_db`, using a
  local `_make_race(...)` helper and inline `RaceAdmin.objects.create(...)`.

Dependencies identified:

- `quote`, `NewsPostForm`, `NewsPost` and `is_race_admin` are **already imported at module level** in
  `src/apps/race/views.py` (lines 5, 35, 36, 40) — the new view needs no new imports, and the
  `NewsPostForm` import must **not** be removed when the `post_form` block goes.
- In `views_.py`, `HttpResponseNotAllowed`, `HttpResponseForbidden`, `NewsPostForm` and `quote` are used
  **only** by `AddNewsPostView` — flake8 F401 fires on all four after the deletion.
- **`autoDownloadFontAwesome: false` is mandatory in the shared JS config** — see the Overview. Without it
  EasyMDE appends a maxcdn FontAwesome `<link>` at runtime and the vendoring accomplishes nothing.
- Verified so later tasks need not re-check: `easymde.min.css` 2.18.0 contains **zero** `@font-face` and
  **zero** `url()` — there are no CodeMirror or font assets to chase.
- Verified by diffing the real files: EasyMDE 2.18 uses 19 built-in icon classes (`fa-bold`, `fa-italic`,
  `fa-header`, `fa-strikethrough`, `fa-code`, `fa-quote-left`, `fa-list-ul`, `fa-list-ol`, `fa-link`,
  `fa-image`, `fa-table`, `fa-minus`, `fa-eye`, `fa-columns`, `fa-arrows-alt`, `fa-question-circle`,
  `fa-undo`, `fa-repeat`, `fa-eraser`) plus `fa-th` for our custom table button. These are FontAwesome **4**
  names, but FA6 free retains all 20 as aliases in `fontawesome.min.css` — **all 20 confirmed present** —
  and FA6's `.fa` rule resolves the bare `fa` prefix to solid
  (`font-family: var(--fa-style-family, "Font Awesome 6 Free"); font-weight: var(--fa-style, 900)`), so a
  solid-only subset is sufficient and no v4-shim file is needed.
- FA6's `all.min.css` declares **six** `@font-face` families (Free solid + regular, Brands, a legacy FA5
  Free/Brands pair, and a v4-compat family), so it cannot be used with one webfont file without editing
  it — which the vendor rule forbids. Use FA's official modular files instead (see Task 1).
- **Both webfont formats must be committed.** `solid.min.css` (572 B) references
  `../webfonts/fa-solid-900.woff2` **and** `.ttf`. A browser only ever fetches the woff2, but
  `collectstatic` is the binding constraint, not the browser: production runs
  `config.storage.CacheBustingStaticFilesStorage`, a `ManifestStaticFilesStorage` subclass whose docstring
  states it strips only the `sourceMappingURL` pattern and keeps "url()/@import hashing in CSS as is" — so
  every CSS `url()` is resolved and rewritten at collect time and a missing target raises `ValueError`,
  failing the Docker build. Ship the ttf even though nothing requests it.
- For the same reason the collect check must be a **real** `collectstatic` with `DEBUG=False`: the
  `STORAGES` override at `src/config/settings.py:251` is inside `if not DEBUG`, so a default local run uses
  the plain storage, resolves no `url()`, and would pass while production fails. `--dry-run` is not enough.
- Static files are served by WhiteNoise from `STATIC_ROOT`, populated by `collectstatic` at Docker build,
  with `STATICFILES_DIRS` → `src/static/`. Vendored files need no config change.
- `NewsPost.is_published` / `publication_date` are the only state signals; there is no author field.

## Development Approach

- **testing approach**: Regular (code first, then tests) — matches how the rest of this repo is written.
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - tests are not optional - they are a required part of the checklist
  - write unit tests for new functions/methods
  - write unit tests for modified functions/methods
  - add new test cases for new code paths
  - update existing test cases if behavior changes
  - tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change (`uv run pytest --reuse-db` for iteration)
- maintain backward compatibility: the `add_post` URL **name and path do not change**, so every
  `reverse()`/`{% url %}` caller keeps working.
- **three deliberate behavior changes**, all to be stated in tests: GET on `add_post` no longer returns
  405; a `RaceAdmin` (admin or moderator — **not** a bare superuser) previewing an unpublished race now
  sees its posts in the feed; an anon user can
  now distinguish an existing race slug (302 to login) from a bogus one (404), because the new helper looks
  the race up before checking auth.
- work on a branch — never commit to master. Run `make format && make lint` before every commit.
- no Python/sed scripts for source edits; edit files directly.

## Testing Strategy

- **unit tests**: required for every task (see Development Approach above). New ones go in
  `src/apps/race/tests.py`, pytest-style functions with `@pytest.mark.django_db` and the `client` /
  `django_user_model` fixtures — **not** `TestCase` subclasses.
- **existing tests**: two must be updated and three moved — see the Context section. Treat those edits as
  part of the task that breaks them, never as follow-up.
- **e2e tests**: this project has **no** UI-based e2e suite (no Playwright/Cypress) — nothing to add.
  The JS added here (`markdown-editor.js`) is verified by rendered-template assertions (the asset is
  linked, the textarea carries the hook attribute) plus the manual checks under Post-Completion.
- **image handling gets a real test, not just a manual check**: `NewsPostForm(data=..., instance=post)`
  covers both the preserve and the `image-clear=on` branch without a real upload.
- Full suite command: `uv run pytest`.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview

One CBV, `RacePostEditView` in `src/apps/race/views.py`, backs both URL names. A shared
`_load(request, race_slug, post_id=None)` does race lookup, auth, authorization and post lookup; `get`
renders the form, `post` validates and either redirects to the post's detail page or re-renders its own
template. Because it never touches `RacePageView.build_context`, the deferred import and the circular
dependency it worked around both disappear, and `AddNewsPostView` is deleted from `website`.

Key design decisions and rationale:

- **View lives in `apps.race`, not `website`.** Every other race sub-page (edit, legend, map, payments) is
  there, and moving it is what actually removes the deferred-import wart CLAUDE.md documents. `NewsPost`
  and `NewsPostForm` stay in `website` — only the view moves.
- **One CBV for create and edit.** Mirrors `RaceEditView`. The alternative (two views) duplicates the
  auth block for no gain.
- **Gate is `is_race_admin`, not `can_edit_race`.** `can_edit_race` requires `role=ADMIN`; news posting is
  exactly what the `MODERATOR` role exists for and moderators can post today. Tightening it would silently
  take the ability away. Any admin/moderator may edit **any** post of that race — `NewsPost` has no author
  field, and a per-author rule was explicitly rejected (it would need a new column + migration).
- **`race=race` in the post lookup**, not `pk` alone — that is what stops a moderator of race A editing
  race B's post through a guessed id.
- **No `_race_header.html` on the form page.** That partial needs `reg_open`, `reg_upcoming`,
  `race_remaining`, `race_full`, `race_team_count`; its only two other includers each rebuild those keys in
  their own view. Following `race_form.html` — the sibling form page — and using a `.breadcrumb` back to
  the race avoids duplicating six context keys for decoration.
- **Vendored assets over CDN.** Follows the Leaflet precedent: pinned version, works behind a flaky CDN,
  no third-party runtime dependency. FontAwesome comes in as its official modular files so nothing has to
  be hand-edited (the vendor dir is off-limits for edits).
- **Editor init shared, markup duplicated.** This repo duplicates markup on purpose (the two team-form
  templates, the two owned-teams panels) while sharing *behavior* via JS/CSS. One
  `src/static/js/markdown-editor.js` keyed off a `data-markdown-editor` attribute — not a hardcoded
  `#id_content` — so both pages opt in without agreeing on a field id, and it no-ops where the attribute
  is absent.
- **Drafts shown in the feed to admins** rather than on a separate admin list page: a handful of rows does
  not justify a new view + template + tab, and one place to see everything is less to learn. The public
  feed is unchanged for everyone else.
- **Two pure-Python properties on `NewsPost`** (`is_draft`, `is_scheduled`) because a Django template
  cannot compare `publication_date` to "now". Properties need no migration.

Out of scope (deliberately): posts with `race=NULL` (site-wide news) stay `/admin/`-only and `edit_post`
404s on them; no separate admin post-list page; no per-author edit restriction; the anon redirect keeps
going to password `login` (not the passwordless `account_start`) — consistent with every other admin page,
since only team registration uses `account_start`.

## Technical Details

**URLs** (`src/website/urls.py`) — replace the current `add_post` entry with two, both pointing at the
imported `RacePostEditView`:

```python
path("race/<slug:race_slug>/post/add/", RacePostEditView.as_view(), name="add_post"),
path("race/<slug:race_slug>/post/<int:post_id>/edit/", RacePostEditView.as_view(), name="edit_post"),
```

`RacePostEditView` is imported alongside the other `apps.race.views` names already imported there
(`RaceEditView`, `RaceLegendEditView`, …), not via the `views.` prefix.

**View flow** (`src/apps/race/views.py`) — the helper returns a `(race, post, response)` triple to match
the five existing `_load_and_authorize` helpers in this module, where a non-`None` response is returned
as-is:

```
_load(request, race_slug, post_id=None) -> (race, post, response)
  race = get_object_or_404(Race, slug=race_slug)
  not request.user.is_authenticated -> (None, None, HttpResponseRedirect(reverse("login") + "?next=" + quote(request.path, safe="/:@")))
  not is_race_admin(request.user, race) -> (race, None, HttpResponseForbidden())
  post = get_object_or_404(NewsPost, pk=post_id, race=race) if post_id else None
  -> (race, post, None)

get  -> render(template_name, {race, post, form: NewsPostForm(instance=post), is_edit: post is not None})
post -> form = NewsPostForm(request.POST, request.FILES, instance=post)
        valid   -> obj = form.save(commit=False); obj.race = race; obj.save(); redirect(obj.get_absolute_url())
        invalid -> render same template with the bound form (no build_context call)
```

`obj.race = race` is only needed on create but is harmless on edit (the post is already in this race by
the lookup). The race lookup happening before the auth check is a small, accepted behavior change: an anon
user gets 302 for a real slug and 404 for a bogus one.

**Template** `src/templates/race/post_form.html`: extends `base-2.html`, `{% load static tz %}`, a
`.breadcrumb` back to the race (following `race_form.html`, **not** including `_race_header.html`), then a
`.post-form` wrapper (scoped — never a bare `.page`, `theme-2.css` owns that) holding one
`<form method="post" enctype="multipart/form-data">`. Fields written manually, moved from
`race_page.html:28-82` nearly verbatim: `title`, `summary`, `content`, `image`, `kind`,
`publication_date`, `is_published`, each as
`<input class="input{% if form.X.errors %} has-error{% endif %}">` with
`{{ form.X.errors|join:", " }}` beneath, and the same `{% get_current_timezone %}` hint under the date.
The content textarea carries `data-markdown-editor`. On edit the image row renders Django's
`ClearableFileInput` (`{{ form.image }}`, the one deliberate exception to the manual-fields rule) so
leaving the field empty does not wipe an existing image. Title/heading switch on `is_edit`: «Новая
публикация» vs «Редактировать: {{ post.title }}». Buttons: «Сохранить» plus «Отмена» to
`{% url 'race' race.slug %}`.

**Assets:**

- `src/static/vendor/easymde/easymde.min.css`, `easymde.min.js` — EasyMDE 2.18.0.
- `src/static/vendor/fontawesome/css/fontawesome.min.css` (the icon definitions) +
  `css/solid.min.css` (~570 B, the solid `@font-face` only) + `webfonts/fa-solid-900.woff2`. FA's official
  modular split, so **no vendored file is edited** and only one webfont ships. `all.min.css` is explicitly
  **not** used: it declares six `@font-face` families and would 404 on five missing webfonts.
- JS is loaded at the **end of the content block** (the Leaflet precedent in `race/map.html:59`), or with
  `defer` — never as a synchronous `<head>` script, since `easymde.min.js` is ~350 KB.
- `src/static/js/markdown-editor.js` — the extracted init, self-starting:
  `document.addEventListener("DOMContentLoaded", ...)` → `querySelectorAll("[data-markdown-editor]")` →
  per element `new EasyMDE({element, minHeight: "300px", spellChecker: false, autosave: {enabled: false},
  toolbar: [...incl. the custom drawTable button...], renderingConfig: {singleLineBreaks: false}})`, plus
  the existing on-`submit` `el.value = mde.value()` sync. Guards `typeof EasyMDE === "undefined"` and
  returns, so a missing asset degrades to a plain textarea instead of throwing.
- `src/static/css/post_form.css` — scoped under `.post-form`: field rows following `race_form.css`
  conventions, plus a short override block aligning the EasyMDE toolbar/box with `theme-2.css`
  (border-radius, border color, font-family).

**Race page** (`src/apps/race/views.py` `build_context`): delete the `post_form` block — but **keep** the
module-level `NewsPostForm` import, which `RacePostEditView` now uses. Compute
`is_admin = user is not None and is_race_admin(user, race)`; admins get
`NewsPost.objects.filter(race=race).select_related("race").order_by("-publication_date", "-pk")`,
everyone else keeps `NewsPost.objects.visible().filter(race=race)`. The admin branch repeats the ordering
on purpose rather than reusing `visible()` — `visible()` *is* the public filter, so there is nothing to
reuse but the `order_by`. `news_count` and the `[:10]` slice are unchanged. Add
`context["can_manage_posts"] = is_admin` (named for both the add and the edit affordance it gates).

Two consequences to accept and note in code comments: admins now see posts of an **unpublished** race
(the `race__is_published` filter is gone from their branch), and because the ordering is
`-publication_date`, a far-future scheduled post sorts to the **top** of an admin's `[:10]` slice and can
push a real news item out of the visible ten.

**Feed partial** (`src/templates/website/_publication_post.html`) has three include sites, so the
additions sit behind flags passed explicitly at the race-page site only:
`{% include "website/_publication_post.html" with publication=news hide_race=True can_edit=can_manage_posts edit_url=... %}`.
Inside: a `{% if can_edit %}` «Редактировать» link in the footer, and — under the same guard — a badge:
«Черновик» when `publication.is_draft`, «Запланирована на {{ publication.publication_date|date:"j E Y" }}»
when `publication.is_scheduled` (a raw datetime would leak through without the filter; the partial already
uses `|date:"j E Y"` for its timestamp). `home.html` and `publication_list.html` pass neither flag and
must render byte-identically to today.

**Model** (`src/website/models/news.py`): two properties on `NewsPost`, no migration —
`is_draft` → `not self.is_published`; `is_scheduled` → `self.is_published and self.publication_date > timezone.now()`.

## What Goes Where

- **Implementation Steps** (`[ ]` checkboxes): code, templates, assets, tests, docs inside this repo.
- **Post-Completion** (no checkboxes): browser verification of the editor rendering and the real upload
  path, which no test in this repo can cover.

## Implementation Steps

### Task 1: Vendor EasyMDE and a FontAwesome solid subset

**Files:**
- Create: `src/static/vendor/easymde/easymde.min.css`
- Create: `src/static/vendor/easymde/easymde.min.js`
- Create: `src/static/vendor/fontawesome/css/fontawesome.min.css`
- Create: `src/static/vendor/fontawesome/css/solid.min.css`
- Create: `src/static/vendor/fontawesome/webfonts/fa-solid-900.woff2`
- Create: `src/static/vendor/fontawesome/webfonts/fa-solid-900.ttf`

- [ ] download EasyMDE 2.18.0 `easymde.min.css` + `easymde.min.js` into `src/static/vendor/easymde/`
      (pinned version, mirroring the flat `src/static/vendor/leaflet/` layout)
- [ ] pick **one exact FontAwesome 6 version** and take every file from that same release (6.5.2 is the
      version whose icon coverage was verified against EasyMDE's 20 classes); record the version in a
      comment or the CLAUDE.md note so a later partial upgrade cannot mix releases
- [ ] download the FontAwesome **modular** files: `fontawesome.min.css` and `solid.min.css` into
      `src/static/vendor/fontawesome/css/`, and **both** `fa-solid-900.woff2` **and** `fa-solid-900.ttf`
      into `.../webfonts/` — **not** `all.min.css`, which declares six `@font-face` families and would need
      editing (forbidden: the vendor dir is off-limits for edits)
- [ ] grep both CSS files for every `url(...)` and confirm each target is committed — under
      `ManifestStaticFilesStorage` an unresolvable reference is a hard `collectstatic` failure, so a
      missing `.ttf` breaks the Docker build even though no browser requests it
- [ ] verify with a **real** production-storage collect, not `--dry-run`:
      `DEBUG=False uv run python src/manage.py collectstatic --noinput` into a throwaway `STATIC_ROOT`
      (the `STORAGES` override lives inside `if not DEBUG`, so a default local run would pass regardless),
      then confirm the hashed files exist and the FA CSS in `staticfiles/` points at the hashed webfont
- [ ] no tests in this task (assets only) — the linked-asset assertions live in Tasks 3 and 5
- [ ] run tests to confirm nothing regressed: `uv run pytest --reuse-db`

### Task 2: Extract the shared Markdown editor init

**Files:**
- Create: `src/static/js/markdown-editor.js`

- [ ] create `src/static/js/markdown-editor.js` with a `DOMContentLoaded` handler iterating
      `document.querySelectorAll("[data-markdown-editor]")`
- [ ] guard `typeof EasyMDE === "undefined"` and return early, so a missing asset leaves a usable plain textarea
- [ ] port the config from `src/templates/website/edit_page.html`: `minHeight: "300px"`,
      `spellChecker: false`, `autosave: {enabled: false}`, the toolbar list including the custom
      `drawTable` button (`className: "fa fa-th mde-icon-table"`), `renderingConfig: {singleLineBreaks: false}`
- [ ] **add `autoDownloadFontAwesome: false`** — the one deliberate addition to the ported config. EasyMDE
      otherwise injects a `maxcdn.bootstrapcdn.com` FontAwesome `<link>` at runtime, and its
      already-loaded check only recognizes that maxcdn href, so it cannot see our vendored CSS
- [ ] port the on-`submit` sync `el.value = mde.value()`, guarding `el.form` being absent
- [ ] add a cross-reference comment naming the two templates that load this file (the repo convention used
      by `team-form.js` ↔ `apps/race/pricing.py`)
- [ ] no tests in this task (the file has no consumer yet) — Tasks 3 and 5 assert it is linked
- [ ] run tests: `uv run pytest --reuse-db`

### Task 3: Migrate `/page/<slug>/edit/` onto the vendored assets

**Files:**
- Modify: `src/templates/website/edit_page.html`
- Modify: `src/website/forms.py`
- Modify: `src/website/tests.py`

- [ ] replace the two unpkg `<link>`/`<script>` tags with `{% static 'vendor/easymde/easymde.min.css' %}`
      and `{% static 'vendor/easymde/easymde.min.js' %}` (the JS `defer`red or at the end of the content
      block, not a synchronous head script)
- [ ] add `{% static 'vendor/fontawesome/css/fontawesome.min.css' %}` and `.../solid.min.css` — replacing
      the FontAwesome that EasyMDE has been pulling from maxcdn at runtime, now suppressed by
      `autoDownloadFontAwesome: false` from Task 2
- [ ] delete the ~35-line inline `<script>` and load `{% static 'js/markdown-editor.js' %}` with `defer`
- [ ] add `data-markdown-editor` to `PageForm.Meta.widgets["content"]` attrs in `src/website/forms.py:352`
      (the template renders `{{ form.content }}`, so the attribute cannot go in the template)
- [ ] write a test that a user in the Django group `"Moderators"` (created in the test — this is the group
      gate at `views_.py:238`, unrelated to `RaceAdmin.Role.MODERATOR`) GETting `/page/<slug>/edit/` gets
      200, the HTML links the vendored easymde/fontawesome/markdown-editor assets, the textarea carries
      `data-markdown-editor`, and **neither `unpkg.com` nor `maxcdn` appears** anywhere in the response
- [ ] write tests for the error/edge cases: a logged-in non-group user still gets 404; an anon user is
      redirected by `@login_required` (not 404)
- [ ] run tests - must pass before next task

### Task 4: Add `is_draft` / `is_scheduled` to `NewsPost`

**Files:**
- Modify: `src/website/models/news.py`
- Modify: `src/website/tests.py`

- [ ] add `is_draft` property returning `not self.is_published`
- [ ] add `is_scheduled` property returning `self.is_published and self.publication_date > timezone.now()`
      (`timezone` is already imported in this module)
- [ ] confirm no migration is generated: `uv run python src/manage.py makemigrations --check --dry-run`
- [ ] write tests for the success cases: a published past-dated post is neither draft nor scheduled; an
      `is_published=False` post is a draft; a published future-dated post is scheduled
- [ ] write tests for the edge cases: a draft with a future date reports `is_draft` and **not**
      `is_scheduled`; a post dated exactly `now` is not scheduled
- [ ] run tests - must pass before next task

### Task 5: Add `RacePostEditView`, its URLs, template and CSS

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/website/urls.py`
- Create: `src/templates/race/post_form.html`
- Create: `src/static/css/post_form.css`
- Modify: `src/apps/race/tests.py`
- Modify: `src/website/tests.py`

- [ ] add `RacePostEditView` to `src/apps/race/views.py` with `template_name = "race/post_form.html"` and
      `_load(request, race_slug, post_id=None)` returning a `(race, post, response)` triple (matching the
      module's five existing `_load_and_authorize` helpers): race by slug (404) → anon →
      `HttpResponseRedirect(reverse("login") + "?next=" + quote(request.path, safe="/:@"))` →
      `is_race_admin` else `HttpResponseForbidden()` → `get_object_or_404(NewsPost, pk=post_id, race=race)`
      when `post_id` (the `race=race` filter blocks cross-race id guessing)
- [ ] implement `get` (render `NewsPostForm(instance=post)` with `race`, `post`, `is_edit`) and `post`
      (valid → `obj.race = race`, save, redirect `obj.get_absolute_url()`; invalid → re-render the bound
      form, **no** `build_context` call and **no** deferred import)
- [ ] confirm no new imports are needed — `quote`, `NewsPost`, `NewsPostForm` and `is_race_admin` are
      already imported at module level (lines 5, 35, 36, 40)
- [ ] wire both URLs in `src/website/urls.py` in this task (the tests below need them): keep `add_post`'s
      name and path, add `edit_post` at `race/<slug:race_slug>/post/<int:post_id>/edit/`, importing
      `RacePostEditView` alongside the other `apps.race.views` names
- [ ] create `src/templates/race/post_form.html`: extends `base-2.html`, `{% load static tz %}`, a
      `.breadcrumb` back to the race (**not** `_race_header.html`, whose context this view does not build),
      `.post-form` scoped wrapper, all 7 fields written manually per the base-2 rule,
      `data-markdown-editor` on the content textarea, the `{% get_current_timezone %}` date hint,
      `{{ form.image }}` (`ClearableFileInput`) for the image row, `is_edit`-switched title/heading,
      «Сохранить» + «Отмена»
- [ ] load `css/post_form.css` and the vendored FontAwesome + EasyMDE CSS from `{% block extra_head %}`,
      and `easymde.min.js` + `js/markdown-editor.js` deferred / at the end of the content block
- [ ] create `src/static/css/post_form.css` scoped under `.post-form` (no bare `.page`), with the
      EasyMDE-to-`theme-2.css` override block
- [ ] write tests for the success cases: GET add returns 200 for an ADMIN and for a MODERATOR, and the HTML
      carries `data-markdown-editor` + the vendored assets; POST valid creates the post with the right
      `race` and redirects to its detail URL; POST valid for a **draft** followed with `follow=True`
      returns 200 (`PublicationDetailView` has a `RaceAdmin`-gated preview branch, so the author is not
      bounced to 404); GET edit returns 200 and prefills; POST edit updates in place without creating a
      second row
- [ ] write tests for the error/edge cases: anon GET and POST redirect to `login` with `?next=`; a
      logged-in non-admin gets 403; POST invalid (blank title) re-renders with errors and creates nothing;
      GET **and** POST edit for a post of another race 404s; edit of a `race=NULL` post 404s; GET on
      `add_post` no longer returns 405
- [ ] write form-level tests for the image branch (no real upload needed):
      `NewsPostForm(data=..., instance=post_with_image)` with the file field absent keeps the stored image,
      and the same with `image-clear=on` clears it
- [ ] delete `test_add_post_invalid_form_shows_errors` (`src/website/tests.py:530`) **in this task** — it
      asserts `race/race_page.html` and `post_form`, and re-pointing `add_post` above breaks it
      immediately; this task cannot go green with it in place. Its case is covered by the invalid-POST test
      written here
- [ ] run tests - must pass before next task

### Task 6: Delete `AddNewsPostView` and migrate its tests

**Files:**
- Modify: `src/website/views/views_.py`
- Modify: `src/website/views/__init__.py`
- Modify: `src/apps/race/views.py`
- Modify: `src/website/tests.py`
- Modify: `src/apps/race/tests.py`

- [ ] delete `AddNewsPostView` from `src/website/views/views_.py`, keeping `is_race_admin` (imported by
      `RacePageView`)
- [ ] remove `AddNewsPostView` from the re-export list in `src/website/views/__init__.py:3` — without this
      Django fails to start and the whole suite errors
- [ ] drop the imports left unused in `views_.py` by the deletion: `HttpResponseNotAllowed`,
      `HttpResponseForbidden`, `NewsPostForm`, `quote` (verified used only by that class; flake8 F401
      otherwise)
- [ ] reword the dangling `RaceEditView` docstring at `src/apps/race/views.py:861` ("Auth mirrors
      ``AddNewsPostView``") to reference `RacePostEditView`
- [ ] move `test_add_post_by_race_admin`, `test_add_post_unauthorized` and `test_add_post_non_admin_user`
      (`src/website/tests.py:215/238/249`) to `src/apps/race/tests.py`, or delete them where Task 5 already
      covers the same case — no coverage may be lost either way. These three assert only status codes and
      DB state (no template or context keys), so they pass unchanged after Task 5 and can safely move here
- [ ] grep for remaining references to `AddNewsPostView` and for the deferred
      `from apps.race.views import RacePageView` inside `views_.py`, confirming both are gone
- [ ] write a test that `reverse("add_post", args=[slug])` still resolves to the documented path and now
      resolves to `RacePostEditView`, and that `reverse("edit_post", args=[slug, pk])` resolves
- [ ] run tests - must pass before next task

### Task 7: Show drafts to admins and add the feed entry point

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/templates/race/race_page.html`
- Modify: `src/static/css/race.css`
- Modify: `src/apps/race/tests.py`

- [ ] in `build_context`, delete the `post_form` block but **keep** the module-level `NewsPostForm` import
      (`RacePostEditView` in the same module now uses it), and compute
      `is_admin = user is not None and is_race_admin(user, race)`
- [ ] branch `news_qs`: admins get
      `NewsPost.objects.filter(race=race).select_related("race").order_by("-publication_date", "-pk")`,
      others keep `NewsPost.objects.visible().filter(race=race)`; leave `news_count` and the `[:10]` slice
      as they are
- [ ] add code comments covering the three non-obvious points: why the ordering is repeated instead of
      reusing `visible()`; that admins now also see posts of an unpublished race; that a far-future
      scheduled post sorts to the top of the admin slice and can push a real item out of the ten
- [ ] set `context["can_manage_posts"] = is_admin`
- [ ] delete the whole `{% if post_form %}` block (lines ~23-86) from `src/templates/race/race_page.html`
- [ ] add the «+ Новая публикация» link to `.publication-feed__head` under `{% if can_manage_posts %}`,
      next to «Всего: N», pointing at `{% url 'add_post' race.slug %}`
- [ ] remove the now-unreachable `.post-add-card` rules from `src/static/css/race.css:342-351` and add the
      feed-head button styling if existing classes do not cover it
- [ ] update `test_race_overview_visibility` (`src/apps/race/tests.py:483`) and its "The feed uses public
      visibility rules even in a private race preview" comment **per role**: for `admin` and `moderator`
      the post is now in `news_list` regardless of `published`; for `superuser` the old
      `is published` expectation **stands unchanged** (no `RaceAdmin` row → `is_race_admin` is `False` →
      public feed), and the comment should say so explicitly so nobody "fixes" it later
- [ ] write tests for the success cases: a draft and a future-dated post appear in `news_list` for an ADMIN
      and for a MODERATOR; the admin-rendered page contains the «Новая публикация» link
- [ ] write tests for the error/edge cases: anon and a logged-in non-admin see neither the draft nor the
      link; **a bare superuser (no `RaceAdmin` row) also sees neither** — they get the public feed on an
      unpublished race they can otherwise view; `news_count` for a non-admin still counts only visible
      posts; the page no longer renders the old inline form (assert its form action is absent)
- [ ] run tests - must pass before next task

### Task 8: Add the edit link and state badge to the feed partial

**Files:**
- Modify: `src/templates/website/_publication_post.html`
- Modify: `src/templates/race/race_page.html`
- Modify: `src/static/css/community.css`
- Modify: `src/apps/race/tests.py`
- Modify: `src/website/test_publications.py`

- [ ] add a `{% if can_edit %}` «Редактировать» link to the partial's footer, using the `edit_url` passed in
- [ ] add the state badge under the same `{% if can_edit %}` guard: «Черновик» when
      `publication.is_draft`, «Запланирована на {{ publication.publication_date|date:"j E Y" }}» when
      `publication.is_scheduled` (the `|date:` filter is required — a raw datetime would render otherwise)
- [ ] pass the flags at the race-page include site (`race_page.html:94`):
      `{% include "website/_publication_post.html" with publication=news hide_race=True can_edit=can_manage_posts edit_url=... %}`
      with `edit_url` built from `{% url 'edit_post' race.slug news.pk %}`
- [ ] leave the other two include sites (`home.html:29`, `publication_list.html:25`) untouched — the flags
      default falsy, so `/`, `/news/` and `/articles/` render unchanged
- [ ] style the badge and link in `src/static/css/community.css` (the publication-feed block) without
      adding new breakpoints
- [ ] write tests for the success cases: an admin sees the edit link with the right URL on each card, a
      «Черновик» badge on a draft and a «Запланирована» badge on a future-dated post
- [ ] write tests for the error/edge cases: anon sees no link and no badge on the race page; the **home**
      feed (`src/website/test_publications.py`) and the `/news/` catalog page render with neither link nor
      badge
- [ ] run tests - must pass before next task

### Task 9: Verify acceptance criteria

- [ ] verify all requirements from Overview are implemented: standalone create page, edit page, rich
      editor on both it and `/page/<slug>/edit/`, vendored assets with no `unpkg` reference left in the
      repo, admins see drafts in the feed, `AddNewsPostView` and the deferred import gone
- [ ] verify edge cases are handled: cross-race post id 404s, `race=NULL` post 404s, anon redirect carries
      `?next=`, non-admin 403, MODERATOR allowed everywhere ADMIN is, empty image field on edit does not
      wipe the stored image, GET `add_post` no longer 405s
- [ ] verify no vendored file under `src/static/vendor/` was edited (the CLAUDE.md rule): the committed
      easymde/fontawesome files must be byte-identical to their upstream downloads
- [ ] **verify zero external requests from either editor page**: load both with the network tab open (or
      grep the rendered HTML) and confirm no `maxcdn.bootstrapcdn.com` or `unpkg.com` request — the
      `autoDownloadFontAwesome: false` flag is the only thing preventing the runtime CDN injection, and a
      typo in it fails silently with icons that still work
- [ ] confirm `add_post` kept its name and path: grep templates and Python for `add_post` and check each
      call site still works
- [ ] run full test suite: `uv run pytest`
- [ ] no e2e suite in this project — nothing to run (see Testing Strategy)
- [ ] run `make format && make lint` and fix anything they report
- [ ] verify test coverage: every new view branch, both new model properties, the image preserve/clear
      branches, and the two updated legacy tests are covered

### Task 10: [Final] Update documentation

- [ ] update `CLAUDE.md`: the `apps.race` entry gains `RacePostEditView` (template, assets, URL names, the
      `is_race_admin` gate incl. MODERATOR, the `race=race` lookup rule, the `(race, post, response)`
      helper shape); the note about `RacePageView.build_context` being called by
      `website.views.views_.AddNewsPostView` via a deferred import must be **removed** (both the caller and
      the workaround are gone)
- [ ] document in `CLAUDE.md`: the admin-only wider `news_qs` and its two consequences (posts of an
      unpublished race become visible to a `RaceAdmin` — **not** to a bare superuser, who has no row and so
      keeps the public feed; scheduled posts sort to the top of the admin slice); the
      new vendored `easymde`/`fontawesome` assets as off-limits for edits (like Leaflet), including why
      `all.min.css` is deliberately not used; the shared `markdown-editor.js` and its
      `data-markdown-editor` hook, loaded by both `post_form.html` and `edit_page.html`
- [ ] document the `autoDownloadFontAwesome: false` trap in `CLAUDE.md` — that it is load-bearing, why
      (EasyMDE's already-loaded check only recognizes a maxcdn href, so it cannot see the vendored CSS),
      and that removing it silently restores the CDN call while leaving the icons working
- [ ] README.md needs no change (it does not document page-level routes) — confirm and note it
- [ ] move this plan to `docs/plans/completed/` (`mkdir -p docs/plans/completed` if needed)

## Post-Completion

*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification:**

- Open `race/<slug>/post/add/` in a browser as a race admin: the EasyMDE toolbar renders with **real
  icons** (the FontAwesome subset is the thing most likely to be wrong), bold/heading/table/preview all
  work, and the typed Markdown survives submit.
- Same check on `/page/<slug>/edit/` after the migration. Its toolbar already had icons (via the injected
  maxcdn FontAwesome 4), so the goal here is that it looks **unchanged** while making no external request.
  FA4 → FA6 glyphs differ slightly in shape, so a small visual shift is expected and fine; an icon missing
  entirely means an alias did not resolve.
- With the network tab filtered to CSS/fonts, confirm no `maxcdn.bootstrapcdn.com` request on either
  editor page — this is the check that the `autoDownloadFontAwesome: false` flag actually took effect.
- Edit an existing post that has an image, leave the file field untouched, save: the image must still be
  there. Then tick the clear checkbox and save: it must be gone. Unit tests cover the form logic, but not
  the real upload/storage path.
- Check the browser console and network tab for 404s on `fa-solid-900.woff2` — a missing webfont shows up
  as silently blank icons, not an error.
- Save a draft, return to the race page, confirm it appears with the «Черновик» badge and that logging out
  hides it.
- Confirm the page looks right at phone width (the race pages are used on phones during events).

**External system updates:**

- The mobile app does not consume any of these endpoints (`/app/*` is separate) — no client change needed.
- `collectstatic` runs at Docker build time, so the vendored assets ship automatically; no deploy config
  change. Worth a glance at the built image size after the EasyMDE + FontAwesome addition.
