# Remove Race.is_legend_visible & rename checkpoint type draft→hidden

## Overview
`Race.is_legend_visible` is a pre-encryption switch that revealed an entire race's
legend on race day. Now that each checkpoint hides its `cost`/`description` behind
per-КП envelope encryption (`is_legend_locked` + `CheckpointSecret`/bundles), the
race-level flag is redundant: a locked КП is already safe to serve, and an open КП is
intentionally cleartext. This plan removes the flag and unifies both the `api` (online
scoring) and `mobile` (offline) serializers on `is_legend_locked` alone.

The same change clarifies the "hidden" checkpoint concept: the `draft` type (used for
КП that exist but are not placed on the terrain — spares, etc.) is renamed to `hidden`
(«Скрытый»). All types except `hidden` are served; `hidden` КП are excluded from `api`,
`mobile`, and the UI exactly as `draft` was.

**Benefits:** one hiding mechanism (lock the КП, or mark it `hidden`), simpler
serializers, smaller version/ETag fingerprint, clearer type name.

## Context (from discovery)
- **Files/components involved:**
  - `website/models/race.py` (field), `website/models/enums.py` (`CheckpointType`)
  - `api/serializers/checkpoint.py`, `api/views/checkpoint.py`
  - `apps/mobile/serializers.py`, `views.py`, `versioning.py`, `legend_crypto.py`, `signals.py`
  - `apps/race/forms.py`, `templates/race/race_form.html`
  - tests: `apps/mobile/tests.py`, `api/tests.py`, `apps/race/tests.py`
  - docs: `apps/mobile/README.md`, `CLAUDE.md`
- **Related patterns found:**
  - api `CheckpointSerializer` currently branches on `is_legend_visible` OR `type != kp` OR `is_legend_locked`.
  - mobile `legend_state(race_id)` returns `(version, is_legend_visible)` and folds `is_legend_visible` into the legend fingerprint; `LegendView` short-circuits to an empty `200` when hidden.
  - Both apps already `.exclude(type="draft")`. The legend type dropdown (`legend_form.js`) is enum-driven via `config.types`, so the label change auto-propagates — no hardcoded `"draft"`.
- **Dependencies identified:**
  - `update_fields` / `auto_now` `updated_at` discipline for version fingerprints (unchanged here).
  - Legend-crypto signals must keep firing — but this change touches no save paths that bypass them; the data migration's `.update()` is safe because the hidden-exclusion set is identical before/after (no bundle rebuild needed).

## Development Approach
- **Testing approach**: Regular (code first, then tests) — this is a mechanical removal/rename over a well-tested surface; tests are adjusted alongside each task.
- Complete each task fully before moving to the next; small, focused changes.
- **Every task includes new/updated tests.** Tests must pass before starting the next task.
- Run `uv run pytest --reuse-db` after each task; `make format && make lint` before finishing.
- Backward compatibility is **not** required for the API shape here (the flag's removal is the intended behavior change); the migration is forward-only.

## Testing Strategy
- **Unit/integration tests** (pytest-style, `@pytest.mark.django_db`): required for every task.
- **No UI e2e harness** in this project — UI change (removing the race-form toggle) is covered by the existing `apps/race` form/view tests.
- Key behavioral assertions to (re)write:
  - api: locked КП → `cost=0`/`description=""`; non-locked КП (any non-hidden type) → cleartext, regardless of any race flag.
  - mobile: legend endpoint **always** serves checkpoints/tags for a published race (no empty-when-hidden branch); `hidden` КП excluded; ETag stable.
  - rename: `hidden` КП excluded everywhere `draft` was; legend version moves on `kp <-> hidden` flip but not on a `hidden`-КП edit.

## Progress Tracking
- Mark completed items `[x]` immediately.
- `➕` prefix for newly discovered tasks; `⚠️` for blockers.
- Keep this file in sync if scope shifts.

## Solution Overview
Two workstreams, sequenced so the schema/enum changes land first:

1. **Remove `is_legend_visible`** — drop the model field (RemoveField only, no data
   step), collapse the api serializer to `is_legend_locked`-only, drop the mobile
   race-level gate and the `|{visible}` fingerprint fold, remove the form field + UI.
2. **Rename `draft` → `hidden`** — change the enum value+label, migrate existing rows
   with `.update()`, and replace every `"draft"` literal with `CheckpointType.hidden.value`.

Both are forward-only migrations. The api behavior change (open КП always cleartext) is
intended; existing `is_legend_visible=False` races must lock their КП or mark them
`hidden` going forward (no auto-lock migration).

## Technical Details
- **api serializer (new logic):** `cost = 0 if cp.is_legend_locked else cp.cost`;
  `description = "" if cp.is_legend_locked else cp.description`. No `type`/`is_legend_visible`
  branch. Drop the now-unused `CheckpointType` import in the serializer.
- **api view:** remove `get_serializer_context` (its only job was injecting the flag).
  Keep the `.exclude(type=hidden)` filter (renamed value).
- **mobile `versioning.py`:** delete `legend_state`; keep a single `legend_version(race_id)`
  that aggregates `Checkpoint`/`CheckpointSecret`/`CheckpointTag` over the hidden-excluded
  queryset (no race row read, no `|{visible}`). Update callers + docstrings.
- **mobile `views.py` `LegendView`:** remove the `is_legend_visible` short-circuit;
  always serialize; use `legend_version`. Rename the `.exclude(type=...)` to the new value.
- **Migration (workstream 1):** `RemoveField(Race, "is_legend_visible")`.
- **Migration (workstream 2):** `AlterField(Checkpoint.type, choices=...)` + a `RunPython`
  data step: forward `Checkpoint.objects.filter(type="draft").update(type="hidden")`,
  reverse the inverse. Put both operations in one migration (Alter then RunPython) or two
  ordered migrations — `.update()` is safe (identical exclusion set, no bundle rebuild).

## What Goes Where
- **Implementation Steps** (`[ ]`): all code, migrations, tests, and doc updates below.
- **Post-Completion** (no checkboxes): deploy-time grep verification reminder + the
  operational note that pre-existing hidden-via-flag races need their КП locked/marked.

## Implementation Steps

### Task 1: Drop `is_legend_visible` field + migration

**Files:**
- Modify: `src/website/models/race.py`
- Create: `src/website/migrations/00XX_remove_race_is_legend_visible.py`

- [x] Delete the `is_legend_visible = BooleanField(...)` field (`race.py:59`).
- [x] Generate the migration: `uv run python src/manage.py makemigrations website` (verify it's a single `RemoveField`, no data step). → `0084_remove_race_is_legend_visible.py`
- [x] Run `uv run python src/manage.py migrate` against the local DB to confirm it applies.
- [x] (Tests for behavior land in Tasks 2–3, which consume this field's removal; no standalone test for the bare field drop.)
- [x] Run `uv run pytest --reuse-db -q` — model import succeeds (test collection passes; field removed from `RaceForm.Meta.fields` to unblock app import — remaining Task 4 template/test work deferred).

### Task 2: Collapse api serializer to `is_legend_locked` only

**Files:**
- Modify: `src/api/serializers/checkpoint.py`
- Modify: `src/api/views/checkpoint.py`

- [x] Rewrite `get_cost`/`get_description` to branch solely on `cp.is_legend_locked` (locked → `0`/`""`, else cleartext).
- [x] Remove the `is_legend_visible` context lookups and the now-unused `CheckpointType` import in the serializer.
- [x] Remove `get_serializer_context` from `CheckpointView` (its only job was injecting the flag). Keep the draft/hidden `.exclude(...)` (value updated in Task 6).
- [x] Update/rewrite api lock tests (`api/tests.py` ~177–219) to assert lock-only semantics: open КП cleartext, locked КП zeroed — with no race flag involved.
- [x] **Add the headline-behavior regression test:** an **unlocked** default `type=kp` КП now serves cleartext `cost`/`description` (this case previously returned `0`/`""` when `is_legend_visible=False`). This is the single biggest behavior change and must have its own assertion.
- [x] Run `uv run pytest --reuse-db src/api/tests.py -q` — must pass.

### Task 3: Drop mobile race-level gate + fingerprint fold

**Files:**
- Modify: `src/apps/mobile/serializers.py`
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/versioning.py`

- [x] Remove `"is_legend_visible"` from the race serializer fields (`serializers.py:27`).
- [x] In `versioning.py`: **inline** the aggregate body into `legend_version(race_id)` and **delete** `legend_state` (currently `legend_version` is a thin wrapper over `legend_state` at ~177). `legend_version` aggregates `Checkpoint`/`CheckpointSecret`/`CheckpointTag` over the hidden-excluded queryset — drop the `Race.values_list("is_legend_visible")` read and the `|{visible}` fold. Update docstrings to remove all `is_legend_visible` references.
- [x] In `views.py` `LegendView`: remove the `is_legend_visible is None`/short-circuit block (`~155–166`, always serialize), and call `legend_version` instead of `legend_state`.
- [x] **Prune now-unused imports** (`make lint`/flake8 F401 will fail otherwise): drop `legend_state` from the `from .versioning import ...` line (`views.py:33`) and `Http404` (`views.py:13`) once its only use — the deleted `is_legend_visible is None` guard — is gone.
- [x] Note: the deleted `is_legend_visible is None` check also doubled as a benign TOCTOU guard (race deleted between `get_object_or_404` and the version read). Dropping it is intentional — the window is tiny and the result harmless (empty legend instead of 404).
- [x] Update/rewrite affected mobile tests: remove the now-obsolete hidden-legend-empty tests, the `test_legend_version_changes_when_is_legend_visible_toggled`, and drop `"is_legend_visible"` from the race field-set test. Added `test_legend_always_served_for_published_race` asserting the legend is served for a published race regardless of any prior flag.
- [x] Run `uv run pytest --reuse-db src/apps/mobile/tests.py -q` — failures confirmed limited to the leftover `is_legend_visible=` kwargs (TypeError; fixed in Task 5).

### Task 4: Remove the race-form toggle (form + template)

**Files:**
- Modify: `src/apps/race/forms.py`
- Modify: `src/templates/race/race_form.html`
- Modify: `src/apps/race/tests.py`

- [x] Remove `"is_legend_visible"` from `RaceForm.Meta.fields` (`forms.py:61`). (already removed in Task 1)
- [x] Remove the toggle `<label class="switch">…name="is_legend_visible"…</label>` block from `race_form.html` (~334).
- [x] Drop the `"is_legend_visible": False` entry from the race-edit test payload (`apps/race/tests.py:667`).
- [x] Add/adjust an `apps/race` test asserting the form saves without the field and the edit page renders (no leftover field reference).
- [x] Run `uv run pytest --reuse-db src/apps/race/tests.py -q` — must pass.

### Task 5: Sweep remaining `is_legend_visible=` test kwargs

**Files:**
- Modify: `src/apps/mobile/tests.py`
- Modify: `src/api/tests.py`

- [x] Remove the `is_legend_visible=...` kwarg from every remaining `Race.objects.create(...)` (~40 call sites across the two files). All kwargs removed from `apps/mobile/tests.py`; `api/tests.py` had none left (only a docstring note at :194).
- [x] Grep to confirm zero residual `is_legend_visible` references anywhere: `grep -rn is_legend_visible src` — remaining hits are intentional: migrations (`0044/0045/0084`, historical), `apps/race/tests.py` assertions (verify field absent), `api/tests.py:194` docstring (documents the behavior change), and README (Task 8).
- [x] Run `uv run pytest --reuse-db -q` — full suite green for workstream 1. → 565 passed.

### Task 6: Rename CheckpointType draft→hidden (enum + migration)

**Files:**
- Modify: `src/website/models/enums.py`
- Create: `src/website/migrations/00XX_rename_checkpoint_draft_to_hidden.py`

- [x] Change `draft = "draft", "Черновик"` → `hidden = "hidden", "Скрытый"` in `CheckpointType`.
- [x] Create the migration: `AlterField(Checkpoint.type, choices=...)` + a `RunPython` step — forward `Checkpoint.objects.filter(type="draft").update(type="hidden")`, reverse the inverse. → `0085_rename_checkpoint_draft_to_hidden.py`.
- [x] Apply locally (`migrate`) and confirm any seed/dev `draft` rows become `hidden`.
- [x] Confirm the legend-edit type dropdown shows «Скрытый» (enum-driven via `CheckpointType.choices` → `config.types` in `apps/race/views.py:932`; `legend_form.js:45` reads `config.types` — no template/JS edit needed).
- [x] Run `uv run pytest --reuse-db -q` — failures confirmed limited to leftover `CheckpointType.draft` references (AttributeError; fixed in Tasks 7–8).

### Task 7: Replace `"draft"` literals with `CheckpointType.hidden.value`

**Files:**
- Modify: `src/api/views/checkpoint.py`
- Modify: `src/apps/mobile/views.py`
- Modify: `src/apps/mobile/versioning.py`
- Modify: `src/apps/mobile/legend_crypto.py`
- Modify: `src/apps/mobile/signals.py`

> ⚠️ **`"draft"` is overloaded — only checkpoint-`type` literals get renamed.** Leave
> `Payment.STATUS_DRAFT = "draft"` (`website/models/models.py:376,378,421`), the
> seat-reservation `status="draft"` (`apps/race/pricing.py:99`, `website/models/race.py`),
> migration `0028_add_payment_balance.py`, and the payment/reservation tests
> (`website/tests.py` `_draft_payment`, `test_gate_draft_teams...`) **untouched** — those are
> a different concept. Only rename literals compared against `Checkpoint.type`.

- [x] api view exclusion (`checkpoint.py:18`) → `CheckpointType.hidden.value`.
- [x] mobile `views.py` exclusions (~173, 183) → hidden value.
- [x] mobile `versioning.py` exclusions (~130, 135, 140) → hidden value; update the "non-draft"/"draft" wording in docstrings.
- [x] `legend_crypto.py:110` `.exclude(type="draft")` → hidden value (added `CheckpointType` import); updated comment at ~99.
- [x] `signals.py:107` type-flip guard `(old == "draft") != (new == "draft")` → compare against `CheckpointType.hidden.value` (added import); updated comments at ~97–98.
- [x] Update the draft-handling mobile tests: renamed `type="draft"` → `type="hidden"` and the related test function names/strings (`test_legend_excludes_hidden_checkpoints`, `*_kp_flips_to_hidden`, `*_hidden_flips_to_kp`, `*_hidden_checkpoint_edited`, `*_tag_on_hidden_checkpoint`, `*_cross_race_and_hidden_unlocks`, `*_hidden_to_kp_on_locked_cp`, etc.).
- [x] Grep for residual **checkpoint-type** literals: `grep -rnE 'type=.?["'\'']draft["'\'']|== ["'\'']draft["'\'']' src` returns only `0085_rename_checkpoint_draft_to_hidden.py` data-migration steps (forward filter + reverse), which must reference the historical literal by design — same exemption as the other historical migrations. No live code hits.
- [x] Run `uv run pytest --reuse-db -q` — full suite green (565 passed). `make format && make lint` clean.

### Task 8: Update documentation

**Files:**
- Modify: `src/apps/mobile/README.md`
- Modify: `CLAUDE.md`

- [x] `apps/mobile/README.md`: remove the `is_legend_visible` gate/fingerprint references (~181, 448, 451) and the empty-when-hidden behavior; describe the always-served legend and the `hidden` type. The surrounding prose is Russian — also rename «draft-исключённого» → «hidden-исключённого» wording.
- [x] `CLAUDE.md`: update the `api` `CheckpointSerializer` note (now lock-only, no `is_legend_visible`), the mobile legend/versioning invariants (no race flag, `legend_version` only, no `|{visible}` fold, legend always served), and rename `draft` → `hidden` («Скрытый») in the type wording.
- [x] Verify no doc still references `is_legend_visible` or the `draft` type: `grep -rn "is_legend_visible\|draft" CLAUDE.md src/apps/mobile/README.md` — `is_legend_visible` gone; remaining `draft` hits are the payment/reservation `status="draft"` concept (out of scope per Task 7).

### Task 9: Verify acceptance criteria
- [x] `grep -rn is_legend_visible src docs CLAUDE.md` → only documented exemptions remain: historical migrations (`0044/0045/0084/0085`), the `api/tests.py:194` docstring documenting the behavior change, the `apps/race/tests.py` assertions verifying field absence, and the plan docs themselves. CLAUDE.md is clean; no live code references.
- [x] `grep -rnE 'type=.?["'\'']draft["'\'']|== ["'\'']draft["'\'']' src` → only the `0085` data-migration steps (forward/reverse, historical literal by design). No live code hits; `Payment.STATUS_DRAFT` / reservation `status="draft"` deliberately remain and are out of scope.
- [x] api: locked КП zeroed, non-locked any-non-hidden type cleartext (no flag); mobile: legend always served for published races, `hidden` excluded, ETag stable; legend version moves on `kp↔hidden` flip, not on a `hidden`-КП edit. — all covered by the passing test suite.
- [x] `make format` then `make lint` — both clean.
- [x] `uv run pytest` — full suite passes (565 passed).

### Task 10: [Final] Finalize
- [x] Confirm both migrations are present, ordered, and reversible. → `0084_remove_race_is_legend_visible.py` (RemoveField, auto-reversible) and `0085_rename_checkpoint_draft_to_hidden.py` (AlterField + RunPython with explicit `hidden_to_draft` reverse); 0085 depends on 0084; `makemigrations --check` reports no pending changes.
- [x] Move this plan to `docs/plans/completed/`.

## Post-Completion
*Items requiring manual intervention or external systems — informational only.*

**Operational / deploy:**
- **Behavior change on deploy:** any existing race that relied on `is_legend_visible=False` to hide its legend will serve its non-locked КП as cleartext immediately. Before deploy, lock those races' КП (`is_legend_locked=True`, via the admin/legend-edit page so the crypto signals fire) or set the relevant КП to `type=hidden`.
- **Mobile clients:** the legend ETag / `versions.legend` change once on deploy (fingerprint no longer folds the removed flag) → clients refetch the legend once. Expected, harmless.
- **Data migration:** existing `type="draft"` checkpoints become `type="hidden"` automatically (forward-only `.update()`); no manual step.

**Manual verification (optional):**
- Hit `/app/race/<id>/legend/` for a race with a mix of locked/open/hidden КП and confirm: open КП cleartext, locked КП `enc`-only, hidden КП absent, tags present for open+locked.
