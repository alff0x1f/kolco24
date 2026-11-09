# Upload and crop race header images

## Overview
- Add file upload with cropping for «Картинка в шапке» (`header_image`) and «Логотип в шапке» (`header_logo`) on
  `/races/new/` and `/race/<slug>/edit/`.
- Today an organizer must host the image somewhere and paste a URL. After this change they pick a file, crop it, and
  press «Сохранить».
- The URL input stays. No migration, no new endpoint, no `ImageField`: the stored value is still a string, now
  `/media/race_headers/<uuid>.<ext>` for an upload, so every template keeps working.
- For the logo, only the upload is added. No public page renders `header_logo` today, and this plan does not change
  that. The old hint «выводится поверх обложки» is false, so it is replaced.
- Out of scope: deleting a race, or editing these fields in `/admin/`, leaves the old file on disk.

## Context (from discovery)
- `src/website/models/race.py:72-73`: both fields are `CharField(255)`. `Race.clean` accepts a value starting with `/`
  or an http(s) URL.
- `src/apps/race/forms.py`: `RaceForm`, a `ModelForm` with manually rendered fields.
- `src/apps/race/views.py:1166`: `RaceEditView.post`. Validates the form and four hidden JSON payloads, then saves in
  one `transaction.atomic()`. `_reconcile_*` can raise `ValueError`, which rolls back and re-renders.
- `src/templates/race/race_form.html`: form tag at line 11 (no `enctype`), «Шапка страницы» card at lines 139-186.
- `src/static/js/race_form.js:579-598`: live preview over `[data-preview-for]`.
- `src/static/css/race_form.css:202-232`: `.upload` styles.
- `src/apps/photo_contest/images.py`: `probe(file)` checks format, size and pixels and raises `ImageRejected`.
- `/media/` is served by nginx, `client_max_body_size 50m`. Pillow is a dependency.
- Project rule: no external network resources. Vendored assets are pinned and off-limits for edits.
- Race tests live in `src/apps/race/tests.py` and `src/apps/race/test_*.py`, pytest style.

## Development Approach
- **testing approach**: Regular (code first, then tests)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change
- maintain backward compatibility: a plain URL or path must keep working

## Testing Strategy
- **unit tests**: required for every task. Run with `uv run pytest src/apps/race/test_header_images.py --reuse-db`.
  Every test that writes files sets `settings.MEDIA_ROOT` to `tmp_path`.
- **e2e tests**: the project has no browser tests. The crop dialog and touch input are checked by hand (see
  Post-Completion).

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope

## Solution Overview
- The file inputs live in the existing form, which becomes multipart.
- The browser only selects the crop area (vendored Cropper.js 1.6.2). The file and the coordinates go to the server on
  «Сохранить», and Pillow does the crop. The server never trusts the browser for format, size or bounds.
- Images are processed during form validation, so a bad file is a field error. They are written to disk only after
  every check has passed, so an abandoned or failing form leaves nothing behind.
- SVG upload is not accepted, because an SVG served from our own domain can run scripts and race admins are not all
  superusers. An SVG logo can still be set by URL.

## Technical Details

**Crop wire format**: hidden field with `"x,y,width,height"`, integers in pixels of the EXIF-rotated image. Empty means
no crop was sent. Anything else non-empty is a field error.

**Output**

| | Cover | Logo |
|---|---|---|
| Max size | 1920 px wide | 512 px longest side |
| Format | JPEG, quality 85, alpha flattened on white | PNG, keeps transparency |
| No crop sent | centre-cut 4:1 | whole image |

Steps: `probe` → open → `ImageOps.exif_transpose` → (logo only) convert to `RGBA` → clamp crop → crop → downscale
(never upscale) → encode. The `RGBA` step is needed because a CMYK JPEG can't be written as PNG, and a palette PNG would
downscale with jagged edges. A final region below 16 px on either side is a field error. This covers a user crop, the
cover's centre-cut and a whole-image logo. Encoding does not pass EXIF on, so GPS data is removed.

**Where processing runs**: all of it in `RaceForm.clean()`. Declared non-model fields are cleaned after `Meta.fields`, in
declaration order, so a `clean_header_image_file()` could run before the crop field is available and silently produce an
uncropped image. The crop is parsed only when a file is present, so a stale crop without a file is ignored.

**Precedence**: a valid file in the POST beats the text value. `RaceForm.clean` then sets the text field's cleaned value
to `""` and drops its field errors, so an old bad URL can't block the save (`Race.clean` runs later, in `_post_clean`).

**Saving, in `RaceEditView.post`**
1. Read the old `header_image`/`header_logo` values **before** building the form. `is_valid()` mutates the instance.
2. Inside the existing `transaction.atomic()`, before `form.save()`: call `store_upload` for each processed image and
   set the field on `form.instance` to the returned path.
3. `store_upload` picks the name `race_headers/<uuid4 hex>.<ext>` first. On `OSError` it removes that name, so a
   truncated file can't stay, and raises `ImageRejected` with a save-failed message. `ImageRejected` is a `ValueError`,
   so the existing `except ValueError` turns it into a form error and no new handler is needed. Only the write is
   covered, so a later failure can't report a successful save as failed.
4. Any exception leaving the block deletes the files already written in this request, then follows the existing
   handling.
5. In the existing `else:` branch (`views.py:1257`), delete each old file whose value changed. `ATOMIC_REQUESTS` is not
   set, so this block is the outermost one and `else:` means committed. `on_commit` is deliberately not used: its hooks
   run inside `Atomic.__exit__`, so a hook that raises would reach step 4 and delete the newly committed files.

**Deleting an old file** (`delete_if_unused`) never raises: errors are logged, and the save still succeeds. It deletes
only if both are true:
- `re.fullmatch(r"/media/race_headers/[0-9a-f]{32}\.(jpg|png)", value)`. This strict match is a security check: the
  value is free text typed by a race admin, so a loose prefix test would let `/media/race_headers/../other` delete any
  media file.
- No `Race` has that value in `header_image` or `header_logo`, checked after the save.

**Failed POST must not lose the existing image.** Picking a file clears the URL input, so such a POST carries
`header_image=""`. Whenever a file was sent but not stored (another field failed, the file was rejected, `_reconcile_*`
raised, the write failed), the view restores the old value before rendering:
`form.data = form.data.copy(); form.data[field] = old_values[field]`. Otherwise the re-render shows an empty field, and
the next «Сохранить» clears the image and deletes its file.

**Lost uploads**: on that same re-render the context lists the field, and the template shows «Файл не сохранён, выберите
его снова». If the file itself was rejected, its error is shown instead.

## What Goes Where
- **Implementation Steps**: code, tests and docs in this repo.
- **Post-Completion**: manual browser checks.

## Implementation Steps

### Task 1: Image processing module

**Files:**
- Create: `src/apps/race/header_images.py`
- Create: `src/apps/race/test_header_images.py`

- [ ] add `parse_crop(raw)`: `None` for empty, four integers otherwise, `ImageRejected` for anything else
- [ ] add `process_cover(file, crop)` and `process_logo(file, crop)` returning `(bytes, ext)`, using `probe` and
      `ImageRejected` from `apps.photo_contest.images`
- [ ] apply EXIF rotation, clamp the crop, reject a too small area, downscale, encode as in Technical Details
- [ ] map Pillow decode errors to `ImageRejected`, never a 500
- [ ] write tests: crop on an EXIF-rotated JPEG, clamping, centre fallback for cover, whole image for logo, downscale,
      no upscale
- [ ] write tests: PNG logo keeps alpha, cover flattens alpha, output has no EXIF, CMYK JPEG logo works, palette PNG
      with transparency downscales as `RGBA`
- [ ] write tests: bad crop string, too small crop, too small image without a crop, GIF and non-image rejected,
      truncated file rejected
- [ ] run tests - must pass before next task

### Task 2: File and crop fields on RaceForm

**Files:**
- Modify: `src/apps/race/forms.py`
- Modify: `src/apps/race/test_header_images.py`

- [ ] add optional `header_image_file`, `header_logo_file` (`FileField`) and `header_image_crop`, `header_logo_crop`
      (hidden `CharField`)
- [ ] process each file in `RaceForm.clean()`, parsing the crop only when a file is present. An `ImageRejected` becomes
      an error on that file field
- [ ] keep the results on the form, for example `form.processed_uploads = {"header_image": (bytes, ext), ...}`
- [ ] in `clean`, apply the precedence rule: a valid file clears the text value and its errors
- [ ] write tests: valid file processed, a crop passed through `RaceForm` changes the output, a stale crop without a
      file is ignored
- [ ] write tests: bad file gives a field error, bad file with a valid URL gives a field error, bad URL plus valid file
      is valid
- [ ] write tests: plain URL still valid and saved, bad URL without a file still rejected, empty value clears
- [ ] run tests - must pass before next task

### Task 3: Store uploads and clean up old files

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/header_images.py`
- Modify: `src/apps/race/test_header_images.py`

- [ ] bind `request.FILES` in `RaceEditView.post` and read the old field values before building the form
- [ ] add `store_upload(data, ext)` and `delete_if_unused(value)` to `header_images.py`, with the strict path match
- [ ] write files inside the existing `transaction.atomic()`, before `form.save()`
- [ ] on any exception leaving the block, delete the files of this request
- [ ] delete changed old values in the existing `else:` branch, not in `on_commit`
- [ ] restore the old text value in `form.data` whenever a file was sent but not stored
- [ ] pass the lost-upload field list to `_build_context` on a re-render
- [ ] write tests: upload on create and on edit stores the `/media/race_headers/...` path and the file exists
- [ ] write tests: another field failing writes no file and reports the lost upload (use the `client` fixture, the
      `_edit_post` helper gives no `.context`), `_reconcile_*` `ValueError` removes the new file and keeps the old value
- [ ] write tests: edit with a file and a bad slug shows the old value on re-render, and re-POSTing that value keeps the
      image and its file. Same for a rejected file
- [ ] write tests: old file deleted on replace and on clear, kept if another race uses it, external URL untouched, a
      `/media/race_headers/../x` value deletes nothing
- [ ] write tests: old-file delete raising still gives a 302 and the new file exists
- [ ] write tests: disk write `OSError` gives a form error and no save, the second of two writes failing removes the
      first file and leaves no partial file, race moderator gets 403 and nothing is stored
- [ ] run tests - must pass before next task

### Task 4: Vendor Cropper.js

**Files:**
- Create: `src/static/vendor/cropperjs/cropper.min.js`
- Create: `src/static/vendor/cropperjs/cropper.min.css`
- Create: `src/static/vendor/cropperjs/LICENSE`

- [ ] download the three files of `cropperjs@1.6.2` from the npm package, unmodified
- [ ] confirm the version banner in both files says 1.6.2
- [ ] confirm neither file loads a resource from the network (the banner's project URL is fine)
- [ ] no code changed, so no new tests. Run `collectstatic --noinput` with `DEBUG=False` and a temporary `STATIC_ROOT`,
      because a dry run skips the production storage post-processing

### Task 5: Upload and crop UI

**Files:**
- Modify: `src/templates/race/race_form.html`
- Modify: `src/static/js/race_form.js`
- Modify: `src/static/css/race_form.css`
- Modify: `src/apps/race/test_header_images.py`

- [ ] template: add `enctype="multipart/form-data"`, file inputs (`accept="image/jpeg,image/png,image/webp"`), hidden
      crop inputs, «Загрузить файл» buttons, new hints (including the false logo hint at `race_form.html:181`), file
      field errors, the lost-upload warning
- [ ] template: add one `<dialog>` with the crop area, «Применить», «Отмена» and the line about the phone header showing
      only the central part. Load the vendored CSS and JS on this page only
- [ ] template: every new button inside the form, including «Загрузить файл» and «Изменить кадр», is `type="button"`
- [ ] JS: preview box and button open the picker. Check size (20 MB, mirrors `probe`) and that the browser can decode
      the file before opening the dialog. On failure show a message under the field and drop the file
- [ ] JS: start Cropper after `showModal`, with `checkOrientation: false`, `viewMode: 1` (keeps the crop box on the
      image, so server clamping can't break 4:1), `aspectRatio: 4` for cover and free for logo
- [ ] JS: call `cropper.destroy()` and revoke the object URL on every close. Cropper 1.x ignores a second init on the
      shared `<img>` and would show the old image
- [ ] JS: «Применить» writes `getData(true)` to the hidden field, shows the cropped canvas as preview, the file name and
      «Изменить кадр». «Отмена» drops the file on a first pick and keeps the crop after «Изменить кадр»
- [ ] JS: route the dialog `cancel` event (Escape) to the «Отмена» logic, or the file would stay with no crop
- [ ] JS: last action wins. The URL input is cleared, with the placeholder «будет загружен файл», only on «Применить»,
      so a cancelled or failed pick leaves the current image alone. Typing in the URL input drops the file and crop
- [ ] CSS: styles scoped under `.race-form-page`, usable at phone width
- [ ] write tests: the page has the multipart `enctype`, both file inputs and crop fields, the vendored assets
- [ ] write tests: the warning renders after a failed POST with a valid file, and not on a plain GET
- [ ] run tests - must pass before next task

### Task 6: Verify acceptance criteria
- [ ] verify all requirements from Overview are implemented
- [ ] verify a race with an external `header_image` still saves unchanged
- [ ] verify no new external URL was added to pages
- [ ] run full test suite: `uv run pytest`
- [ ] run `make format` and `make lint`

### Task 7: [Final] Update documentation
- [ ] add one short line to the vendored assets note in `CLAUDE.md`: Cropper.js pinned to 1.6.2, off-limits for edits
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems - no checkboxes, informational only*

**Manual verification**
- Run these with `DEBUG=True` or on the nginx stack. The `/media/` route in `website/urls.py` is active only in debug.
- Desktop browser: pick, crop, change the frame, type a URL after picking, save, then replace the image.
- Pick then «Отмена», and pick then Escape: the current image and URL stay as they were.
- Crop the cover, then pick a file for the logo: the dialog shows the new file, not the cover.
- Phone: open the picker, crop by touch, save.
- A portrait phone photo with EXIF rotation ends up cropped exactly where it was selected.
- Force a slug error after picking a file: the warning appears, the old image is still shown.
- The new cover looks right on the race page and on the home page spotlight.
