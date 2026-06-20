# Checkpoint Color (КП «цвет» для мультигонки)

## Overview
- Add a descriptive **color** label to `Checkpoint` so a multi-discipline race
  (пеший / вело / вода) can visually group its КП. The color is **not** a hex
  value — it's a named token (`red`, `blue`, …) that the mobile app maps to its
  own theme/palette when drawing the map and legend.
- Solves: organizers of multi-stage races need КП grouped by stage/discipline;
  today every КП renders identically.
- Integrates as a single `CharField` on the existing `Checkpoint` model, surfaced
  in the legend-edit grid + Django admin and shipped to the mobile app through
  the existing legend endpoint. **Not secret** — visible for both locked and open
  КП.

## Context (from discovery)
- **Model**: `src/website/models/checkpoint.py` (`Checkpoint`, `CheckpointSecret`,
  `CheckpointTag`). `Checkpoint.updated_at` is already `auto_now`.
- **Enum**: `src/website/models/enums.py` (`CheckpointType(TextChoices)`).
- **Legend edit**: `src/apps/race/views.py` — `RaceLegendEditView` (~885) with
  helpers `_existing_checkpoints` (~905), `_validate_legend_rows` (~577),
  `_reconcile_legend` (~623), `_build_context`/`legend_config` (~924). Module
  constant `_CHECKPOINT_TYPE_VALUES` (574) is the existing validation pattern to
  mirror.
- **Legend grid UI**: `src/templates/race/legend_form.html` (CSS-grid header at
  lines 60–67, JSON islands at 118–120), `src/static/js/legend_form.js`
  (`COLS` array line 60, row builder ~96–114, error `fieldMap` ~221, serializer
  ~338), `src/static/css/legend_form.css` (grid-template-columns).
- **Mobile serializer**: `src/apps/mobile/serializers.py` —
  `LegendCheckpointSerializer.to_representation` (~87) branches on
  `is_legend_locked`; `data` dict built before the branch (line 88).
- **Admin**: `src/website/admin.py` — `CheckpointAdmin` (107) `list_display` +
  `list_filter`, no explicit `fields`/`fieldsets` (so the new field auto-appears
  in the form).
- **Versioning / crypto**: `src/apps/mobile/versioning.py` (`legend_version`) and
  the crypto stack (`signals.py`, `legend_crypto.py`, `CheckpointSecret`) are
  **untouched** — `color` lives on `Checkpoint`, not in `enc_blob`/bundles, and
  `updated_at`/`auto_now` already moves the legend ETag on save.

## Development Approach
- **Testing approach**: Regular (code first, then tests) — small, well-scoped
  change; tests written as the final checklist item of each task.
- Complete each task fully before the next; run tests after each.
- **Every task includes its own tests** (success + error/edge cases).
- **All tests must pass before starting the next task.**
- Run `make format && make lint` before committing.
- Backward compatible: `default=""` means existing КП stay "no color"; no data
  migration.

## Testing Strategy
- **Unit tests** (pytest-style, `@pytest.mark.django_db`, `client`/
  `django_user_model` fixtures — project convention, **not** `TestCase`):
  - `src/apps/mobile/tests.py`: legend endpoint emits `color` for **both** locked
    and open КП; update the existing legend field-set test to include `color`.
  - `src/apps/race/tests.py`: legend-edit saves a valid color; unknown color →
    row error; empty color → `""`.
- **No e2e tests** in this project (no Playwright/Cypress); the legend grid JS is
  covered indirectly via the view tests on the posted `checkpoints_json`.

## Progress Tracking
- Mark `[x]` immediately when done.
- `➕` prefix for newly discovered tasks, `⚠️` for blockers.
- Keep this file in sync with actual work.

## Solution Overview
- One new `TextChoices` enum + one model field + one `AddField` migration.
- Color flows: legend-edit grid `<select>` → `checkpoints_json` →
  `_validate_legend_rows` → `_reconcile_legend` (`instance.save()`, signals
  preserved) → DB → mobile `LegendCheckpointSerializer` → app.
- Admin gets the field for one-off edits.
- ETag/`legend_version` move automatically via `Checkpoint.updated_at` — no
  `versioning.py` change.

## Technical Details
- `CheckpointColor(TextChoices)`: `none=""` (default, "Без цвета"), `red`, `blue`,
  `green`, `yellow`, `orange`, `purple`.
- `Checkpoint.color = CharField(max_length=20, choices=..., default="", blank=True)`.
- Mobile legend shape becomes: locked → `{id, number, type, color, enc}`;
  open → `{id, number, type, color, cost, description}`.

## What Goes Where
- **Implementation Steps** (`[ ]`): model/enum/migration, view validation+reconcile,
  grid template/JS/CSS, serializer, admin, tests.
- **`api.CheckpointSerializer` intentionally NOT changed** — online scoring
  (`/api/`) needs no color; this is a deliberate scope decision, not an oversight.
- **Post-Completion** (no checkboxes): deploy `migrate`; coordinate the color→theme
  mapping with the mobile app team; manual smoke of the legend grid in a browser.

## Implementation Steps

### Task 1: Add `CheckpointColor` enum + `Checkpoint.color` field + migration

**Files:**
- Modify: `src/website/models/enums.py`
- Modify: `src/website/models/checkpoint.py`
- Create: `src/website/migrations/000X_checkpoint_color.py` (via `makemigrations`)
- Modify: `src/website/tests.py` (model lives in `website`, so the model-level test
  goes here per the `src/<app>/tests.py` convention)

- [x] add `CheckpointColor(TextChoices)` to `enums.py` with values
      `none=""`, `red`, `blue`, `green`, `yellow`, `orange`, `purple` (Russian labels).
- [x] import `CheckpointColor` in `checkpoint.py`; add
      `color = models.CharField("Цвет КП", max_length=20, choices=CheckpointColor.choices, default="", blank=True)`
      on `Checkpoint`, placed next to `type`.
- [x] run `uv run python src/manage.py makemigrations website` — verify it produces a
      single clean `AddField` (no data migration, no other model churn).
- [x] write a model test: a new `Checkpoint` defaults to `color == ""`; assigning a
      valid value persists; the migration applies cleanly (`migrate`).
- [x] run tests — must pass before next task.

### Task 2: Legend-edit backend — validate + reconcile + config

**Files:**
- Modify: `src/apps/race/views.py`
- Modify: `src/apps/race/tests.py`

- [x] add module constant `_CHECKPOINT_COLOR_VALUES = {value for value, _ in CheckpointColor.choices}`
      next to `_CHECKPOINT_TYPE_VALUES` (~574); import `CheckpointColor`.
- [x] `_existing_checkpoints` (~909): add `"color": cp.color` to each row dict.
- [x] `_validate_legend_rows` (~577): `color = str(row.get("color") or "").strip()`;
      if `color not in _CHECKPOINT_COLOR_VALUES` → `row_errors["color"] = "Неизвестный цвет."`;
      add `"color": color` to the cleaned dict (empty stays `""`).
- [x] `_reconcile_legend` (~623): set `instance.color = row["color"]` before
      `instance.save()` (keep `save()` — never `QuerySet.update()`; signals must fire).
- [x] `_build_context` `legend_config` (~933): add
      `"colors": [{"value": v, "label": l} for v, l in CheckpointColor.choices]`.
- [x] write tests: POST `checkpoints_json` with a valid color persists it on the КП;
      unknown color → row error + re-render (no save); empty/missing color → `""`;
      existing-КП edit preserves color round-trip.
- [x] run tests — must pass before next task.

### Task 3: Legend grid UI — header, `<select>` cell, serialize, CSS

**Files:**
- Modify: `src/templates/race/legend_form.html`
- Modify: `src/static/js/legend_form.js`
- Modify: `src/static/css/legend_form.css`

- [x] template (`legend_form.html`): add a `Цвет` header `<span class="lg-h lg-h-color">`
      in the `.lg-headrow` (between `Тип` and `Стоимость`).
- [x] JS: read `var COLORS = Array.isArray(config.colors) ? config.colors : [...]`;
      add `"color"` to the `COLS` array (insert after `"type"`); render a
      `<select class="lg-cell lg-color" data-col="N">` from `COLORS` in the row
      builder; set its value from `c.color` (default `""`). **Re-index every
      subsequent `data-col`** (cost/desc/lock) so the indices stay contiguous.
- [x] JS `setCell` (~161): this is a `COLS`-index `if/else` dispatch using
      **hardcoded** `controlByCol(row, N)` literals (`0..4`). Add a `color` branch
      (normalize a pasted value against the colors value/label set, default `""`,
      mirroring `normalizeType`) **and bump the hardcoded literals** for
      cost/desc/lock to their new indices — both the `data-col` attributes (above)
      and these literals must match, or paste-fill writes to the wrong cells.
- [x] JS error `fieldMap` (~221): add `color: ".lg-color"`; JS row serializer
      (~338): add `color: row.querySelector(".lg-color").value`. Confirm the
      `has-error` highlight applies to a `<select>` (`.lg-color`) the same as inputs;
      add a CSS rule if `has-error` is input-scoped. (`.lg-color` is a `.lg-cell`, so
      `.lg-cell.has-error` already covers it — no new rule.)
- [x] JS TSV-paste path: account for the new column when mapping pasted cells
      (color is a `<select>`, so a pasted free-text color should match by value or be
      ignored/left default — keep paste robust, don't crash on extra/short rows).
      (`setCell` color branch routes paste through `normalizeColor` → unknown → `""`.)
- [x] CSS (`legend_form.css`): add the new column to the grid `grid-template-columns`
      for both `.lg-headrow` and `.lg-row`, plus any `.lg-h-color`/`.lg-color` width.
- [x] manual check noted in Post-Completion (no JS unit harness); add/extend a view
      test asserting the rendered `legend_config` JSON island contains `colors`.
- [x] run tests — must pass before next task.

### Task 4: Mobile legend serializer emits `color`

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/tests.py`

- [x] `LegendCheckpointSerializer.to_representation` (~88): build
      `data = {"id": cp.id, "number": cp.number, "type": cp.type, "color": cp.color}`
      **before** the `is_legend_locked` branch, so both branches carry `color`.
- [x] update the docstring: locked → `{id, number, type, color, enc}`;
      open → `+ cost, description`.
- [x] tests: legend endpoint returns `color` for an **open** КП and a **locked** КП;
      update the existing legend field-set/contract test to include `color`.
- [x] confirm (test or assertion) that a `color` change moves the legend ETag /
      `versions.legend` via `updated_at` — no `versioning.py` change required. This
      holds because `_reconcile_legend` does a **plain** `instance.save()` (no
      `update_fields`), so `auto_now` bumps `updated_at`; keep the reconcile a full
      save (don't switch it to `update_fields` omitting `"updated_at"`).
- [x] run tests — must pass before next task.

### Task 5: Django admin exposes `color`

**Files:**
- Modify: `src/website/admin.py`

- [x] `CheckpointAdmin`: add `"color"` to `list_display` and `list_filter`.
- [x] verify the change form shows `color` (no explicit `fields`/`fieldsets`, so it
      auto-appears) — confirmed: no `fields`/`fieldsets` on `CheckpointAdmin`, so the
      field renders automatically.
- [x] no new test strictly required (admin config); covered by Task 1/4.
- [x] run tests — must pass before next task.

### Task 6: Verify acceptance criteria
- [x] verify Overview requirements: enum present, field defaults `""`, locked & open
      mobile legend both emit `color`, grid edits round-trip, admin shows it.
- [x] verify edge cases: empty color, unknown color rejected, existing КП unaffected.
- [x] run full suite: `uv run pytest`.
- [x] run `make format && make lint`.

### Task 7: [Final] Docs + housekeeping
- [x] update `CLAUDE.md` — note `Checkpoint.color` in the legend/serializer
      invariants (locked & open both carry `color`; not secret; ETag via `updated_at`).
- [x] move this plan to `docs/plans/completed/`.

## Post-Completion
*Manual / external — no checkboxes.*

**Manual verification:**
- Browser smoke of `race/<slug>/legend/edit/`: color `<select>` renders per row,
  saves, reloads with the stored value; TSV paste from Excel still works with the
  added column.
- Hit `/app/race/<id>/legend/` for a race with a locked + open КП and confirm
  `color` is present in both, and that toggling a КP color changes the `ETag`.

**External system updates:**
- Run `migrate` on deploy.
- Coordinate with the mobile app team: define the `color` token → theme-palette
  mapping (`red`/`blue`/`green`/`yellow`/`orange`/`purple`/`""`) on the client.
