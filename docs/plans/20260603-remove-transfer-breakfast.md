# Remove the legacy transfer (`race/8/transfer/`) and breakfast registration

## Overview
Remove the **legacy standalone** "bus transfer" (`race/8/transfer/`) and "breakfast"
(`race/<slug>/breakfast/…`) registration features — models, views, forms, admin, URLs,
and templates — and **drop both DB tables** via a `DeleteModel` migration.

This is a **pure deletion**. The features are standalone registration pages backed by
their own models (`Transfer` — verbose_name "Заявка на автобус"; `BreakfastRegistration`),
unrelated to the live registration/payment flow.

**Critical scope boundary** — this is **NOT** the new доп-услуги add-on mechanism.
`apps/race` has a generic `RaceExtra` system where transfer/breakfast exist as
`code="transfer"` / `code="breakfast"` rows. That system, its models, its pricing
helpers, and all its tests are **left completely untouched**. Only the old standalone
pages and their tables are removed. (The prior "remove manual payment stack" plan
explicitly noted "`TransferView` / `Transfer` are bus/passenger transfer — unrelated,
not touched"; this plan is the follow-up that removes exactly that.)

Decisions confirmed in brainstorm:
1. **Legacy only** — the `RaceExtra` add-on mechanism is not touched.
2. **Old URLs just 404** — routes are deleted, no redirects (mirrors the manual-payment
   stack removal, commit `04abc97`).
3. **Tables are dropped** — a new `DeleteModel` migration removes `Transfer` and
   `BreakfastRegistration`.

## Context (from discovery)

**Files/components involved:**
- `src/website/models/models.py` — `Transfer` class (lines 16–43) + `BreakfastRegistration`
  class (lines 46–79, FK to `Race` with unused `related_name="breakfast_registrations"`)
- `src/website/models/__init__.py` — exports `BreakfastRegistration`, `Transfer`
- `src/website/views/views_.py` — `TransferView`, `TransferPaidListView`, `BreakfastView`,
  `BreakfastAdminView`, `BreakfastPaidListView` + helpers `can_manage_transfer`,
  `can_manage_breakfast`; imports of `BreakfastForm`/`TransferForm` (from `website.forms`)
  and `BreakfastRegistration`/`Transfer` (from `website.models`)
- `src/website/views/__init__.py` — re-exports the 5 view classes
- `src/website/forms.py` — `TransferForm`, `BreakfastForm` + constants
  `BUS_REGISTRATION_MAX_PASSENGERS`, `BREAKFAST_MAX_ATTENDEES`; imports
  `BreakfastRegistration, Transfer`
- `src/website/admin.py` — `TransferAdmin`, `BreakfastRegistrationAdmin` + imports
- `src/website/urls.py` — `transfer`/`transfer_paid_list` routes (15–20), three breakfast
  slug routes (80–93), three breakfast int-id redirect routes (40–42, `RaceIdRedirectView`)
- `src/templates/website/` — `transfer.html`, `transfer_paid_list.html`, `breakfast.html`,
  `breakfast_admin.html`, `breakfast_paid_list.html`
- `src/website/migrations/` — historical `0051_transfer`, `0054_breakfastregistration`
  (left untouched); latest is `0072_payment_vtb_payment`

**Related patterns found:**
- Table-dropping is a standard `migrations.DeleteModel`. Latest website migration is
  `0072_payment_vtb_payment`; `apps/race` `0001_initial` already depends on website `0072`,
  so a new website `0073` slots in after it with no cross-app conflict.
- Tests are **pytest-style functions** with `@pytest.mark.django_db`, not `TestCase`.

**Dependencies / things deliberately kept:**
- `apps/race` `RaceExtra` / `TeamExtra` / `PaymentExtra` and `code="transfer"`/
  `code="breakfast"` — the new mechanism. **Untouched.**
- `FREE_MAPS` / `MAP_PRICE` in `forms.py` — unrelated deprecated maps constants. **Kept.**
- `RaceIdRedirectView` itself — still used by other int-id redirects; only its three
  breakfast routes are removed.

**Verified absences (low blast radius):**
- **No tests** target the legacy `Transfer`/`BreakfastRegistration` — every
  `transfer`/`breakfast` hit in `src/website/tests.py` is the new `RaceExtra` (`apps/race`).
- **No nav/menu links** — `base.html`, `base-2.html`, `race_page.html`, and `MenuItem`
  carry no `{% url 'transfer' %}` / `{% url 'breakfast' %}`. The only `{% url %}` refs are
  the five templates linking to each other (all deleted together).
- `related_name="breakfast_registrations"` is never read anywhere.

## Development Approach
- **Testing approach**: Regular. This is a deletion with **no tests to write** — there are
  no tests covering the legacy feature, and the new `RaceExtra` tests are out of scope and
  must keep passing untouched. The "test" deliverable per task is the relevant verification
  command (import check, `makemigrations --check`, grep, full suite).
- Work top-down so the tree never references a removed symbol: views → exports → urls →
  templates → forms → admin → models → migration → verify.
- Run `make format && make lint` before committing (project requirement).

## Testing Strategy
- **Unit tests**: none added or changed — no legacy tests exist (verified). The existing
  suite (incl. all `apps/race` `RaceExtra` tests) must stay green, proving nothing
  depended on the removed symbols.
- **No e2e**: project has no UI e2e harness; pytest is the gate.
- **Migration gate**: `uv run python src/manage.py makemigrations --check --dry-run` must
  report **no changes** after the new migration is added (i.e. model state and migration
  graph agree).
- Full gate: `uv run pytest` must pass before the plan is considered done.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep this plan in sync with actual work

## Solution Overview
Delete dead code in dependency order, then drop the tables. Remove the views first, then
their re-exports, then their URL routes, then the templates they rendered, then the now-
unused forms/constants and admin registrations, then the model classes. Finally add a
`DeleteModel` migration for `Transfer` and `BreakfastRegistration`. Old migrations
`0051`/`0054` stay in the historical graph (never edit applied migrations). Verify with
`grep` that nothing dangles and `makemigrations --check` is clean, then run the suite.

## Technical Details

**Views to delete** (`src/website/views/views_.py`) — by symbol, not line (deletions shift
lines): `can_manage_transfer`, `can_manage_breakfast`, `TransferView`,
`TransferPaidListView`, `BreakfastView`, `BreakfastAdminView`, `BreakfastPaidListView`.
Then prune imports that become unused: drop `BreakfastForm, TransferForm` from the
`from website.forms import (...)` line and `BreakfastRegistration, Transfer` from the
`from website.models import (...)` block. **Keep** everything else those import lines bring
in (e.g. `TeamForm`, `Race`, `Team`, `TeamStartLog`, `TeamMemberRaceLog`, …).

**URL names/routes to remove** (`src/website/urls.py`): `transfer` (`race/8/transfer/`),
`transfer_paid_list` (`race/8/transfer/list/`), `breakfast`, `breakfast_admin`,
`breakfast_paid_list` (the three `race/<slug>/breakfast/…` routes), **and** the three
unnamed breakfast int-id redirect routes (`race/<int:race_id>/breakfast/`, `/admin/`,
`/list/` → `RaceIdRedirectView`) — without these last three, old int-id breakfast URLs
would `301`-redirect into now-dead slug routes. After removal all paths 404.

**Migration** (`src/website/migrations/0073_delete_transfer_breakfast.py`):
```python
from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [("website", "0072_payment_vtb_payment")]
    operations = [
        migrations.DeleteModel(name="Transfer"),
        migrations.DeleteModel(name="BreakfastRegistration"),
    ]
```
Prefer generating it with `uv run python src/manage.py makemigrations website` (after the
model classes are removed) and confirming it produced exactly these two `DeleteModel` ops
with the right dependency; hand-write only if generation names it differently.

## What Goes Where
- **Implementation Steps** (checkboxes): all code/template deletions, the migration, grep
  verification, and the test run — all doable in this repo.
- **Post-Completion** (no checkboxes): deploy note that the routes now 404 and that the
  migration drops two tables (irreversible data loss, authorized).

## Implementation Steps

### Task 1: Delete the transfer + breakfast views

**Files:**
- Modify: `src/website/views/views_.py`

- [x] delete the view classes `TransferView`, `TransferPaidListView`, `BreakfastView`, `BreakfastAdminView`, `BreakfastPaidListView`
- [x] delete the permission helpers `can_manage_transfer` and `can_manage_breakfast`
- [x] remove now-unused imports: `BreakfastForm, TransferForm` from the `from website.forms import (...)` line; `BreakfastRegistration, Transfer` from the `from website.models import (...)` block (keep all other names on those imports)
- [x] sanity: `views_.py` compiles/lints clean (a full `manage.py check` will still fail on the stale `__init__.py` export — fixed in Task 2)
- [x] (no tests — no legacy tests exist; see Testing Strategy)

### Task 2: Prune the views package re-exports

**Files:**
- Modify: `src/website/views/__init__.py`

- [x] remove from the `from .views_ import (...)` list: `BreakfastAdminView`, `BreakfastPaidListView`, `BreakfastView`, `TransferPaidListView`, `TransferView`
- [x] keep all surviving exports (`AddTeam`, `RaceIdRedirectView`, `TeamMemberRaceLogView`, …)

### Task 3: Remove the URL routes

**Files:**
- Modify: `src/website/urls.py`

- [x] delete the `transfer` route (`race/8/transfer/`) and `transfer_paid_list` route (`race/8/transfer/list/`)
- [x] delete the three slug routes: `breakfast` (`race/<slug>/breakfast/`), `breakfast_admin` (`…/breakfast/admin/`), `breakfast_paid_list` (`…/breakfast/list/`)
- [x] delete the three int-id redirect routes: `race/<int:race_id>/breakfast/`, `…/breakfast/admin/`, `…/breakfast/list/` (the `RaceIdRedirectView` lines) — keep `RaceIdRedirectView` itself and its other routes
- [x] `uv run python src/manage.py check` — must pass now (URLConf resolves, no missing view imports)

### Task 4: Delete the templates

**Files:**
- Delete: `src/templates/website/transfer.html`
- Delete: `src/templates/website/transfer_paid_list.html`
- Delete: `src/templates/website/breakfast.html`
- Delete: `src/templates/website/breakfast_admin.html`
- Delete: `src/templates/website/breakfast_paid_list.html`

- [x] delete the five template files listed above
- [x] grep to confirm none are `{% include %}`d / `render()`ed / linked elsewhere: `grep -rn "transfer.html\|transfer_paid_list.html\|breakfast.html\|breakfast_admin.html\|breakfast_paid_list.html" src/` — zero matches (only the deleted files cross-referenced each other)

### Task 5: Remove the forms and constants

**Files:**
- Modify: `src/website/forms.py`

- [x] delete the `TransferForm` and `BreakfastForm` classes
- [x] delete the constants `BUS_REGISTRATION_MAX_PASSENGERS` and `BREAKFAST_MAX_ATTENDEES`
- [x] remove `BreakfastRegistration, Transfer` from the `from website.models import BreakfastRegistration, Team, TeamMemberMove, Transfer` import — **keep `Team` and `TeamMemberMove`**
- [x] **do NOT touch** `FREE_MAPS` / `MAP_PRICE` (unrelated deprecated maps constants — kept)

### Task 6: Remove the admin registrations

**Files:**
- Modify: `src/website/admin.py`

- [x] delete `@admin.register(Transfer) class TransferAdmin` and `@admin.register(BreakfastRegistration) class BreakfastRegistrationAdmin`
- [x] remove `BreakfastRegistration` and `Transfer` from the `from .models import (...)` import (keep all other names)

### Task 7: Remove the models and export

**Files:**
- Modify: `src/website/models/models.py`
- Modify: `src/website/models/__init__.py`

- [x] delete the `Transfer` class (lines ~16–43) and the `BreakfastRegistration` class (lines ~46–79) from `models.py`
- [x] remove `BreakfastRegistration` and `Transfer` from the import list in `models/__init__.py`
- [x] `uv run python src/manage.py check` — passes (no remaining references to the deleted models)

### Task 8: Add the DeleteModel migration (drop tables)

**Files:**
- Create: `src/website/migrations/0073_delete_transfer_breakfast.py`

- [x] generate via `uv run python src/manage.py makemigrations website` and confirm it emits exactly `DeleteModel("Transfer")` + `DeleteModel("BreakfastRegistration")` with `dependencies = [("website", "0072_payment_vtb_payment")]` (hand-edit/rename only if needed; do not edit `0051`/`0054`). Note: Django auto-named it `0073_remove_breakfastregistration_race_delete_transfer_and_more` and split out a separate `RemoveField(breakfastregistration, race)` op; hand-rewrote to `0073_delete_transfer_breakfast.py` with exactly the two `DeleteModel`s — `makemigrations --check` confirms the graph still agrees (DeleteModel subsumes the FK column drop)
- [x] `uv run python src/manage.py makemigrations --check --dry-run` — reports **no changes** (model state and graph agree)
- [x] `uv run python src/manage.py migrate` against the local DB — applies cleanly, both tables dropped

### Task 9: Verify acceptance criteria

- [ ] grep for legacy references across `src/` — `grep -rni "transfer\|breakfast\|завтрак\|трансфер\|автобус" src/ --include=*.py --include=*.html` shows **only** the new `RaceExtra` `code="transfer"`/`"breakfast"` usages in `apps/race` (+ its tests) and the historical migrations `0051`/`0054`; **zero** legacy `Transfer`/`BreakfastRegistration`/view/form/url refs
- [ ] `uv run python src/manage.py check` — no issues
- [ ] `uv run python src/manage.py makemigrations --check --dry-run` — no new migration
- [ ] full suite: `uv run pytest` — all pass (proves the `apps/race` `RaceExtra` tests are unaffected)
- [ ] `make format && make lint` — all checks pass

### Task 10: [Final] Update docs and close out

**Files:**
- Modify: `CLAUDE.md` (only if a documented pattern changed)

- [ ] add a one-line note in `CLAUDE.md` that the legacy standalone transfer/breakfast registration pages + tables were removed (routes now 404); the `RaceExtra` `code="transfer"`/`"breakfast"` add-ons are the supported mechanism
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only*

**Deploy notes:**
- After deploy, the old paths (`/race/8/transfer/`, `/race/8/transfer/list/`,
  `/race/<slug>/breakfast/`, `/race/<slug>/breakfast/admin/`, `/race/<slug>/breakfast/list/`,
  and the `/race/<int>/breakfast/…` redirects) return **404** by design (no redirects).
  Confirm no external bookmark/integration relies on them.
- The `0073` migration **drops two tables** (`website_transfer`,
  `website_breakfastregistration`) — irreversible data loss, explicitly authorized. If any
  historical transfer/breakfast rows are worth archiving, export them **before** applying
  the migration in production.
