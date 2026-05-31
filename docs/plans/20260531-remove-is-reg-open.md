# Remove deprecated `is_reg_open` field from Race model

## Overview
The `Race` model carries two registration-state fields. `is_reg_open` (BooleanField, added in
migration `0044`) is a 2-state flag that predates `reg_status` (CharField with `RegStatus` choices
`upcoming`/`open`/`sold_out`, added later in migration `0048`). `reg_status` fully subsumes the
boolean and is the source of truth used across all views, templates, JS, and tests. `is_reg_open`
is now dead — its only reference outside the model definition and its creation migration is the
admin `list_display`.

This change removes `is_reg_open` entirely: drop the model field, swap the admin column to
`reg_status`, and add a migration to drop the DB column. No logic reads `is_reg_open`, so there is
no behavior change and no data migration.

## Context (from discovery)
- Field definition: `src/website/models/race.py:51` — `is_reg_open = BooleanField("Регистрация открыта", default=False)`
- Created in: `src/website/migrations/0044_is_legend_visible_and_more.py`
- **Only live usage**: `src/website/admin.py:225` — `RaceModelAdmin.list_display` includes `is_reg_open`
- Replacement field: `src/website/models/race.py:39` — `reg_status` (`RegStatus` choices), used in `views_.py`, `views/team.py`, `apps/race/views.py`, `add_team.html`, `edit_team.html`, `team-form.js`, and many tests
- Latest migration: `0068_racepricetier.py` → new migration is `0069`
- Removal-migration style reference: `0063_remove_coupons.py` uses `migrations.RemoveField(model_name=..., name=...)`

## Development Approach
- **Testing approach**: Regular (code first, then verify). This is a field deletion with no new
  logic; the relevant "test" is the existing suite continuing to pass plus a Django system check
  confirming no dangling references.
- Complete each task fully before moving to the next.
- Run `make format && make lint` before committing (project requirement).

## Testing Strategy
- **Unit tests**: no new behavior is introduced, so no new unit tests are written. The guarantee is
  that the full existing suite (`uv run pytest`) passes unchanged and `manage.py makemigrations
  --check` reports no drift after the migration is added.
- **e2e tests**: none — project has no UI e2e suite for this area.

## Progress Tracking
- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.

## Solution Overview
Three coordinated edits keep the model, admin, and DB schema in sync:
1. Remove the field from the model.
2. Replace it in the admin `list_display` with `reg_status` (chosen over dropping the column, to
   keep a registration-status column visible in the changelist).
3. Add a `RemoveField` migration depending on `0068`.

## Technical Details
- Migration file `0069_remove_race_is_reg_open.py`:
  ```python
  from django.db import migrations

  class Migration(migrations.Migration):
      dependencies = [
          ("website", "0068_racepricetier"),
      ]
      operations = [
          migrations.RemoveField(
              model_name="race",
              name="is_reg_open",
          ),
      ]
  ```
- Admin `list_display` becomes: `("id", "name", "code", "slug", "date", "is_active", "reg_status")`.

## What Goes Where
- **Implementation Steps**: all three code/schema edits and verification live in this repo.
- **Post-Completion**: the migration must be applied to staging/production DBs at deploy time
  (outside this repo's scope).

## Implementation Steps

### Task 1: Remove field and fix admin, add migration

**Files:**
- Modify: `src/website/models/race.py`
- Modify: `src/website/admin.py`
- Create: `src/website/migrations/0069_remove_race_is_reg_open.py`

- [x] Delete line `is_reg_open = BooleanField("Регистрация открыта", default=False)` from `src/website/models/race.py`
- [x] In `src/website/admin.py`, `RaceModelAdmin.list_display`: replace `is_reg_open` with `reg_status` → `("id", "name", "code", "slug", "date", "is_active", "reg_status")`
- [x] Create `src/website/migrations/0069_remove_race_is_reg_open.py` with a single `migrations.RemoveField(model_name="race", name="is_reg_open")`, dependency `("website", "0068_racepricetier")`
- [x] Run `uv run python src/manage.py makemigrations --check --dry-run` — must report no changes (confirms model and migration agree)
- [x] Run `uv run python src/manage.py migrate` against the local DB — must apply cleanly

### Task 2: Verify acceptance criteria

- [ ] `grep -rn "is_reg_open" src/` returns no matches except the new `0069` and the original `0044` migration files
- [ ] Run `make format && make lint` — must pass
- [ ] Run full test suite: `uv run pytest` — must pass
- [ ] Manually confirm the Race admin changelist renders with the `reg_status` column (Django system checks pass via the test run / `manage.py check`)

### Task 3: [Final] Wrap up

- [ ] Confirm no CLAUDE.md update needed (no new pattern introduced)
- [ ] Move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — no checkboxes, informational only*

**External system updates**:
- Apply migration `0069_remove_race_is_reg_open` to staging and production databases during deploy.
  The dropped column is non-recoverable; since `is_reg_open` carried no authoritative data
  (`reg_status` is the source of truth), no backfill or pre-deploy data capture is required.
