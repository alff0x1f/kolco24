# Photo contest results gallery and thumbnails

## Overview
- Feedback on `/photo/<slug>/results/`: put each nomination on its own page, make the grid denser and more like a
  gallery, move captions into the fullscreen viewer.
- The grid loads the 1600px preview for every tile (200–500 KB each, ~35 MB for a 100-photo nomination). Add a 600px
  thumbnail (~40–70 KB) for the grid; the 1600px preview stays for the viewer and the voting page.
- Results become one page per nomination (`/photo/<slug>/results/<id>/`, full ranking). The contest page
  `/photo/<slug>/` shows the top-3 of each nomination after results are published. `/photo/<slug>/results/` without
  an id is removed (only linked from the contest page; results shipped in #296, no outside links). No pre-results
  gallery (decided against).

## Context (from discovery)
- `src/apps/photo_contest/models.py` — `PhotoEntry` (`original` private, `preview` ImageField), `PhotoContest.phase`,
  `is_submission_open`, `is_results_published`.
- `src/apps/photo_contest/images.py` — `PREVIEW_MAX_SIDE = 1600`, `make_preview(file)` (EXIF-transpose, RGB, q85).
- `src/apps/photo_contest/services.py:create_entry` — makes original + preview before the lock, writes files inside
  `transaction.atomic()`, cleans up `targets` on failure.
- `src/apps/photo_contest/signals.py` — `post_delete` deletes `original`/`preview` via `transaction.on_commit`.
- `src/apps/photo_contest/views.py` — `_visible_contest`, `_author_name`, `PhotoResultsView`.
- `src/apps/photo_contest/scoring.py:nomination_standings` — rows `{entry, wins, n, score, place}`, ties share a place.
- `src/templates/photo_contest/results.html`, `src/static/css/photo_contest.css`, `src/static/js/photo_contest.js`
  (viewer reads `data-caption` + `data-status`, navigates inside `.pc-thumbs`; reused unchanged).
- No `management/` package in the app yet.

## Development Approach
- **testing approach**: Regular (code first, then tests)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change: `uv run pytest src/apps/photo_contest/tests.py --reuse-db`
- `make format` + `make lint` before commit

## Testing Strategy
- pytest functions in `src/apps/photo_contest/tests.py` (`@pytest.mark.django_db`, `client`, `django_user_model`),
  reusing the existing helpers/fixtures there (media root overrides, image factories).
- No e2e tests in the project.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix

## Solution Overview
- **Thumbnail**: a third stored file per entry, generated at upload and backfilled from the existing preview by a
  management command. Width/height stored so the justified grid can size tiles before images load.
- **Pages**: `PhotoResultsView` takes a `nomination_id` and shows that nomination's full ranking with tabs to the
  others. The contest page nomination cards get top-3 tiles (after publish) and a «Все N фото →» link (whenever the
  results are visible to the viewer).
- **Grid**: CSS-only justified rows (flex-grow by aspect ratio), captions only in the viewer.

## Technical Details

### Model
```python
thumb = models.ImageField("Миниатюра", max_length=255, blank=True,
                          width_field="thumb_width", height_field="thumb_height")
thumb_width = models.PositiveIntegerField(null=True, blank=True)
thumb_height = models.PositiveIntegerField(null=True, blank=True)
```
- Helper on `PhotoEntry` (`grid_image` property) returning `(url, width, height)`: thumb + `thumb_width/height` if
  set, else `preview.url` + stored `width or 1`/`height or 1` (already EXIF-rotated by `make_original`). **Never
  read `preview.width`** — `preview` has no `width_field`, so it would open the file per tile (and 500 on a missing
  file). The tile only needs the aspect ratio.

### Images
- `THUMB_MAX_SIDE = 600`, `THUMB_QUALITY = 80`.
- `make_preview(file, max_side=None, quality=85)`; `None` means `PREVIEW_MAX_SIDE`, read at call time (tests
  monkeypatch constants). `src.draft(...)` must use `max_side` too.
- On upload the thumb is built **from the preview bytes**, not the original:
  `make_preview(ContentFile(preview), max_side=THUMB_MAX_SIDE, quality=THUMB_QUALITY)` — cheap (no second 50 Мп
  decode) and identical to what the backfill command produces.
- File name: `<entry_dir>/<uuid>_thumb.jpg`, next to `<uuid>.jpg`.

### Nomination results page (`PhotoResultsView`, `results.html`)
- URL `<slug>/results/<int:nomination_id>/`, name `photo_results` (now takes the id). The bare `<slug>/results/` route
  is dropped.
- Access: unchanged — `_visible_contest` + `is_results_published or is_moderator`, else 404. Banner «Итоги не
  опубликованы» for a moderator before publish (as now).
- Nomination: `get_object_or_404(PhotoNomination, pk=nomination_id, contest=contest)`.
- Rows: `nomination_standings` order; `pc-place pc-place-N` badge on places 1–3 **with `wins > 0`** (zero-win entries
  all score 0.0 and tie; without the guard a lightly voted nomination shows everyone as 1st);
  `data-caption="N место · автор — подпись"`, `data-status="Побед: X из Y"`.
- Tabs: all nominations of the contest, current one `aria-current="page"`.
- Tile: `<a href>` = `preview.url` (the viewer loads `link.href`), only `<img src>` = thumb.

### Contest page (`_contest_context`, `contest.html`)
- When `contest.is_results_published`: per nomination `rows = nomination_standings(n)`,
  `nomination.top = [r for r in rows if r["place"] <= 3 and r["wins"] > 0]` (ties at 3rd included),
  `nomination.ranked_count = len(rows)`; tiles in the nomination card with the same badge/caption/status as the
  results page, using the justified grid with a smaller `--row`.
- When results are visible (`is_results_published or is_moderator`): «Все N фото →» link to `photo_results` in each
  card. A moderator before publish gets only the link — no places on the shared page.
- Banner «Голосование завершено. Смотреть итоги» → «Голосование завершено, итоги ниже.» (no link). The moderator-block
  «Итоги» button (`contest.html:206`) is removed — the per-card links replace it.
- Existing «my photos» thumbs on this page stay as they are (1600px preview, current markup).

### Grid CSS
Markup: `<div class="pc-thumbs pc-gallery">` with `<a class="pc-thumb-link">` as **direct children** (no `li`
wrapper — the viewer only needs `.pc-thumb-link` inside `.pc-thumbs`). Selectors must beat the existing
`.photo-contest .pc-thumbs` rules (`photo_contest.css:70` flex gap 12px, `:289` a 3-column grid at ≤600px):
```css
.photo-contest .pc-thumbs.pc-gallery { --row: 220px; display: flex; flex-wrap: wrap; gap: 4px; }
.photo-contest .pc-thumbs.pc-gallery::after { content: ""; flex-grow: 999; }
.photo-contest .pc-gallery .pc-thumb-link {
  flex-grow: calc(var(--w) / var(--h));
  flex-basis: calc(var(--w) / var(--h) * var(--row));
  height: var(--row); position: relative;
}
.photo-contest .pc-gallery img { width: 100%; height: 100%; object-fit: cover; }
@media (max-width: 600px) {
  .photo-contest .pc-thumbs.pc-gallery { --row: 120px; display: flex; }
}
```

## What Goes Where
- **Implementation Steps**: code, migration, command, templates, CSS, tests, CLAUDE.md.
- **Post-Completion**: deploy order, visual check on real photos.

## Implementation Steps

### Task 1: Thumbnail field and image helper

**Files:**
- Modify: `src/apps/photo_contest/models.py`
- Create: `src/apps/photo_contest/migrations/0004_photoentry_thumb.py`
- Modify: `src/apps/photo_contest/images.py`
- Modify: `src/apps/photo_contest/tests.py`

- [x] add `thumb`, `thumb_width`, `thumb_height` to `PhotoEntry`; `makemigrations`
- [x] add `PhotoEntry.grid_image` (thumb, else preview url + stored `width or 1`/`height or 1`)
- [x] add `THUMB_MAX_SIDE`/`THUMB_QUALITY`; give `make_preview` `max_side`/`quality` params
- [x] tests: `make_preview(max_side=600)` fits 600px and keeps aspect; extend `test_preview_is_small_rotated_and_without_exif` for the default
- [x] tests: `grid_image` returns thumb when set, preview + stored dims otherwise, `1`/`1` for zero dims
- [x] run tests - must pass before next task

### Task 2: Generate thumbnail on upload, delete on entry delete

**Files:**
- Modify: `src/apps/photo_contest/services.py`
- Modify: `src/apps/photo_contest/signals.py`
- Modify: `src/apps/photo_contest/tests.py`

- [x] `create_entry`: build thumb from the preview bytes before the lock; save `<name>_thumb.jpg`; add it to `targets` for cleanup
- [x] `delete_entry_files`: include `instance.thumb`
- [x] update existing tests: `test_concurrent_uploads_respect_limit` fake `make_preview` accepts `*args, **kwargs` and waits on the barrier only for the preview call; file counts 2 → 3 there and in `test_rolled_back_delete_keeps_files`; `test_create_entry_lays_files_out_by_contest_and_nomination` checks the thumb next to the preview
- [x] tests: uploaded entry has thumb file with `thumb_width/height` ≤ 600
- [x] tests: add a `"thumb"` case to `test_partial_write_failure_leaves_no_files` (fail on the 2nd `FileSystemStorage._save`); limit reached leaves no thumb; deleting an entry removes the thumb file
- [x] run tests - must pass before next task

### Task 3: `build_photo_thumbs` backfill command

**Files:**
- Create: `src/apps/photo_contest/management/__init__.py`
- Create: `src/apps/photo_contest/management/commands/__init__.py`
- Create: `src/apps/photo_contest/management/commands/build_photo_thumbs.py`
- Modify: `src/apps/photo_contest/tests.py`

- [x] iterate entries with empty `thumb` (all with `--force`), build from `preview` via `make_preview(max_side=THUMB_MAX_SIDE, quality=THUMB_QUALITY)`, save `<preview stem>_thumb.jpg`. Order: build bytes → (with `--force`) delete old thumb file → save new → `save(update_fields=["thumb", "thumb_width", "thumb_height"])`, so a failed rebuild keeps the old thumb
- [x] skip an entry whose preview is missing/undecodable (`OSError` and `ImageRejected`) with a stderr warning, continue; print a count at the end
- [x] tests: only empty entries processed; `--force` rebuilds; broken preview doesn't abort the run
- [x] run tests - must pass before next task

### Task 4: Per-nomination results page

**Files:**
- Modify: `src/apps/photo_contest/views.py`
- Modify: `src/apps/photo_contest/urls.py`
- Modify: `src/templates/photo_contest/results.html`
- Modify: `src/static/css/photo_contest.css`
- Modify: `src/apps/photo_contest/tests.py`

- [x] URL `<slug>/results/<int:nomination_id>/` (`photo_results`); drop the bare `<slug>/results/` route
- [x] `PhotoResultsView` for one nomination (rules above)
- [x] `results.html`: crumbs, tabs, justified grid with top-3 badges, moderator banner, existing `.pc-viewer`; remove the old per-tile caption/author/score body
- [x] justified-row CSS + place badges (desktop/mobile heights)
- [x] update existing results tests to the new URL/markup (keep the no-username check for `data-caption`; the no-`data-status` assertion at `tests.py` ~2147 is reversed — `data-status` is now «Побед: X из Y»)
- [x] tests: foreign nomination 404; 404 before publish for outsiders and for staff/superuser without a moderator row; moderator sees it with the banner
- [x] tests: rows in ranking order, only approved; badges on top-3 only with wins (tie at 3rd → both, zero-win → none)
- [x] tests: `<a href>` is the preview URL, `<img src>` the thumb
- [x] run tests - must pass before next task

### Task 5: Top-3 and links on the contest page

**Files:**
- Modify: `src/apps/photo_contest/views.py`
- Modify: `src/templates/photo_contest/contest.html`
- Modify: `src/static/css/photo_contest.css`
- Modify: `src/apps/photo_contest/tests.py`

- [x] `_contest_context`: `top` + `ranked_count` per nomination when published; flag for showing the results link
- [x] `contest.html`: top-3 tiles + «Все N фото →» in each nomination card; banner text without link; remove the moderator «Итоги» button
- [x] update existing contest-page tests that expect the `photo_results` link in the banner / moderator block
- [x] tests: after publish — top-3 tiles with author in `data-caption` and links to each nomination; before publish — no tiles for anyone, link only for a moderator; during voting — nothing
- [x] run tests - must pass before next task

### Task 6: Verify acceptance criteria
- [x] all requirements from Overview implemented
- [ ] ⚠️ check pages in the browser (left to the user: local contest is still in voting) (desktop + phone width, contest page top-3 + nomination results, viewer navigation)
- [x] full suite: `uv run pytest`
- [x] `make lint`

### Task 7: [Final] Update documentation
- [x] CLAUDE.md photo contest section: 1–2 lines (thumb + backfill command, `results/<id>/` per nomination, top-3 on the contest page)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**Deploy:**
- `manage.py migrate`, then `manage.py build_photo_thumbs` (until it runs, pages fall back to the 1600px preview).

**Manual verification:**
- grid look with real portrait/landscape/panorama photos; last row not stretched
- page weight of a 100-photo nomination in browser devtools
