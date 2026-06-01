# Team Add-ons (доп-услуги): generic per-race extras

## Overview

Add a generic, per-race "add-on" mechanism so teams can purchase optional extras
(transfer now, breakfast likely next race) during **team registration** and **team
edit**, charged together with the race fee in one VTB/SBP payment. The existing
"maps" feature (`map_count`) — a one-off, hardcoded implementation — is **migrated
into** this mechanism and becomes the first add-on (`code="map"`, `price=200`,
`free_per_team=2`).

**Problem it solves:** today every add-on type means new columns on `Team`
(`map_count`/`map_count_paid`), a column on `Payment` (`map`), hardcoded constants
(`FREE_MAPS`/`MAP_PRICE`), and a hardcoded term in the charge formula duplicated in
two views. Adding "transfer", then "breakfast", repeats that surgery each time.

**Key benefit:** an admin enables a new add-on by adding a row on the race edit page
(next to price tiers and categories) — no code change, no migration.

**Integration:** new models live in `apps/race` (its first models + migrations).
Charge formula and payment-creation are extracted into shared helpers used by both
`AddTeam` and `EditTeamView`. Reconciliation in `check_vtb_payments` credits per-payment
snapshots. The team add/edit forms render one stepper per active add-on; the race edit
page gains an "Доп-услуги" section reconciled like categories/price tiers.

## Context (from discovery)

**Files/components involved:**
- `src/apps/race/` — gets its FIRST `models.py` + `migrations/` (AppConfig `label="race_app"`, confirmed in `apps/race/apps.py:6`). Already in `INSTALLED_APPS` (`config/settings.py:82`).
- `src/apps/race/views.py` — `RaceEditView` (`post` at `:539`), reconcilers `_reconcile_categories` (`:392`) and `_reconcile_price_tiers` (`:431`), wired inside `transaction.atomic()` at `:589`.
- `src/apps/race/forms.py` — `RaceForm` (has `categories_json` / `price_tiers_json` hidden fields).
- `src/website/forms.py` — `TeamForm`: `__init__(race_id, ...)` (`:353`), `map_count` field (`:345`), maps cap in `clean()` (`:490`), `clean_map_count()` (`:565`), legacy `save()` writing `team.map_count` (`:597`); constants `FREE_MAPS=2`/`MAP_PRICE=200` (`:26`).
- `src/website/views/views_.py` — `AddTeam` charge formula (`:1647`), `build_team_form_context` (`:1557`), **and the legacy `my_team` view (`:777`) which does `TeamForm(request.POST or None)` (`:778`)** — POST/None lands in the `race_id` slot. This same view renders `my_team/team_predstart/team_start/team_finish` via its `template=` arg.
- `src/website/views/team.py` — `EditTeamView` charge formula (`:193`), ad-hoc "can't reduce paid maps" guard (`:149`); both `TeamForm(...)` calls here (`:46`, `:90`) pass a **real** `race_id` (unaffected).
- `src/website/management/commands/check_vtb_payments.py` — VTB PAID reconciliation (`:69-95`); `team.map_count_paid += payment.map` at `:73`.
- `src/website/models/models.py` — `Team` maps columns (`:335-336`), `Payment` model (`:548`) with `map` field (`:593`), legacy Yandex `Payment.update_team` (`:276`).
- `src/static/js/team-form.js` — live total + stepper (reads `mapCountPaid`/`mapPrice`/`freeMaps`). `src/static/js/team.js` (legacy `my_team` page) reads `#id_map_count` (`:149`, `:153`).
- `src/static/js/race_form.js`, `src/templates/race/race_form.html` — race edit JSON row editors.
- `src/templates/website/add_team.html`, `edit_team.html` — base-2 templates with hidden maps input. `my_team.html:264` renders `{{ team_form.map_count }}` (the only legacy template that does).
- `src/static/css/team-form.css` (scoped `.team-register`), `src/static/css/race_form.css`.
- Latest `website` migration: `0072_payment_vtb_payment` (race_app `0001_initial` FKs reference `website` models → must depend on it).

**Out of scope (verified, no map writes):** `src/api` team creation has zero `map`/extras references; `TeamFormAdmin` is commented out in `my_team` (`views_.py:782-785`). Neither enforces capacity limits today and neither is extended here.

**Related patterns found:**
- Per-race inline row management via hidden `*_json` POST fields, parsed + validated + reconciled (add/update/delete, `order=index`) inside one `transaction.atomic()` — the model to copy for add-ons.
- `_reconcile_categories` guards in-use rows by **raising `ValueError` and aborting the whole save** (`views.py:422-427`); `_reconcile_price_tiers` hard-deletes with **no** usage guard (`views.py:449`). Add-ons deliberately use a **softer** policy (silently force `is_active=False` for in-use rows instead of aborting) — this is a *new* behavior, not a copy of either existing reconciler. `PROTECT` on the FK is the backstop that turns any accidental hard-delete of an in-use row into an error.
- JSON config island (`teamFormConfig`) feeding vanilla-JS live total — generalize, don't rewrite.
- Race-fee snapshot pattern: `Payment.cost_per_person` + `paid_for` back-calculated in `Payment.update_team` — add-ons mirror it with `PaymentExtra.unit_price`.

**Dependencies identified:**
- Maps cutover touches form + both views + reconciliation + JS + templates together (coupled).
- Migration must run with legacy columns still present (column drop deferred).
- Cross-app FKs (`race_app` ↔ `website`) — accepted; user refactors later.

## Development Approach

- **Testing approach:** Regular (implement, then write tests within each task).
- Complete each task fully before the next; small, focused changes.
- **Every task MUST include new/updated tests** (success + error/edge scenarios). Tests are a required deliverable, listed as separate checklist items.
- **All tests must pass before starting the next task.**
- Run `make format && make lint` before any commit (project rule).
- Maintain backward compatibility: legacy maps columns stay populated-readable until the deferred follow-up migration; in-flight payments created pre-deploy must still reconcile.
- **Update this plan file if scope changes during implementation.**

## Testing Strategy

- **Unit tests:** required for every task. pytest-style functions with `@pytest.mark.django_db` and `client` / `django_user_model` fixtures — NOT Django `TestCase` (project convention). New file `src/apps/race/tests.py` extensions + `src/website/tests.py` extensions.
- **Email tests** (if any touched): override `EMAIL_BACKEND` to locmem — not relevant here.
- **e2e:** project has no Playwright/Cypress suite. Browser behavior of the steppers/live-total is covered by (a) context-island unit assertions and (b) a manual check listed under Post-Completion.
- Run full suite with `uv run pytest` (or `uv run pytest --reuse-db` for iteration).

## Progress Tracking

- Mark completed items `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.
- Keep this plan in sync with actual work.

## Solution Overview

**Architecture:** three new relational models in `apps/race` (Option A — fully relational, no JSON blobs):

- `RaceExtra` — the per-race catalogue (what's purchasable, at what price, how many free).
- `TeamExtra` — per-team desired vs paid counts (generalizes `map_count`/`map_count_paid`).
- `PaymentExtra` — per-payment snapshot of the delta a payment covers (generalizes `Payment.map`); carries a `unit_price` price-snapshot so history/reconciliation are accurate even if the catalogue price later changes.

**Single quantity rule for all add-ons:** `max = ucount − free_per_team`
(maps `free=2` → `ucount−2`; transfer/breakfast `free=0` → `ucount`). No separate cap field.

**Charge formula** (one shared helper, replacing two duplicated copies):
```
total = max(0, (ucount − paid_people) × race.current_price
             + Σ active extras: max(0, count − count_paid) × price)
```

**Key design decisions & rationale:**
- `on_delete=PROTECT` on `RaceExtra` FKs → an add-on teams have purchased can't be deleted; the race edit page **softly deactivates** it (`is_active=False`) instead. This is a *new, softer* policy than `_reconcile_categories` (which raises + aborts) — `PROTECT` is just the backstop against an accidental hard-delete.
- Reconciliation credits add-ons **from the per-payment snapshot** (`+= PaymentExtra.count`). Idempotency lives in the **command**, not in `PaymentExtra`: the outer loop excludes VTB payments already `PAID` and the inner block has the `payment.status == STATUS_DONE` `continue` guard (`check_vtb_payments.py:66`). `PaymentExtra` has no per-row consumed flag by design — `Payment.status` is the idempotency token, so reconciliation must never be driven off `PaymentExtra` alone.
- Race-fee half of payments is **untouched** (`cost_per_person`, `paid_for`, `Payment.update_team` back-calc). Only the maps term is swapped for the generic extras loop.
- **Invariant:** once extras are present, `payment_amount != paid_for × cost_per_person` (the race-fee back-calc in `Payment.update_team` divides `withdraw_amount / cost_per_person`). Extras must therefore **never** flow through the partial Yandex `update_team` path. Legacy Yandex `update_team` never touched maps → stays race-fee-only. Production registration forces `sbp2`/VTB, so extras only flow through the VTB PAID path (which credits `payment.paid_for` directly, not via back-calc) — consistent with maps today. `create_team_payment` asserts/sets `payment_method == "sbp2"`.
- Maps columns/constants are **deprecated but not dropped** in this change; the column-drop migration is a deferred follow-up after prod verification, so a payment created pre-deploy and confirmed post-deploy isn't orphaned.

## Technical Details

**Models** (`src/apps/race/models.py`; references use `race_app` label):
```python
class RaceExtra(models.Model):
    race          = FK("website.Race", related_name="extras", on_delete=CASCADE)
    code          = CharField(max_length=32)      # "map" | "transfer" | "breakfast"
    name          = CharField(max_length=100)     # display, e.g. "Трансфер"
    price         = IntegerField(default=0)        # ₽ per unit
    free_per_team = IntegerField(default=0)
    order         = IntegerField(default=0)
    is_active     = BooleanField(default=True)
    class Meta: unique_together = ("race", "code"); ordering = ["order", "id"]

class TeamExtra(models.Model):
    team       = FK("website.Team", related_name="extras", on_delete=CASCADE)
    race_extra = FK(RaceExtra, related_name="team_extras", on_delete=PROTECT)
    count      = IntegerField(default=0)
    count_paid = IntegerField(default=0)
    class Meta: unique_together = ("team", "race_extra")

class PaymentExtra(models.Model):
    payment    = FK("website.Payment", related_name="extras", on_delete=CASCADE)
    race_extra = FK(RaceExtra, related_name="payment_extras", on_delete=PROTECT)
    count      = IntegerField(default=0)
    unit_price = IntegerField(default=0)           # price snapshot at charge time
```

**Pricing/payment helpers** (`src/apps/race/pricing.py`):
- `compute_team_charge(team, race) -> (total:int, lines:list[ExtraCharge])` — `ExtraCharge` = `(race_extra, count, unit_price)`.
- `create_team_payment(request, team, race) -> HttpResponse | None` — builds `Payment` (+ `cost_per_person`/`paid_for` as today), creates `PaymentExtra` rows for each line, mints the VTB order, returns the redirect; returns `None` when `cost == 0` so the caller redirects to its own success URL. Reads `team.extras` (the `TeamExtra` rows) — caller must upsert those before calling. **Sets `payment_method="sbp2"` and asserts it**, with a comment that `payment_amount` intentionally diverges from `paid_for × cost_per_person` once extras are present (so the partial Yandex back-calc must never run on these).
- `upsert_team_extras(team, cleaned_data, race)` — writes `TeamExtra.count` from `extra_<code>` cleaned fields (get_or_create per active extra).

**Form** (`TeamForm`):
- `__init__`: resolve the race **defensively** — `race_id` may be a `QueryDict`/`None` because the legacy `my_team` view calls `TeamForm(request.POST or None)` (positional `race_id`). If it isn't a valid race id, set `self.extras = []` and add no extra fields (so `my_team` never 500s). Otherwise `self.extras = list(race.extras.filter(is_active=True))` and add `forms.IntegerField("extra_<code>", min_value=0, required=False, initial=0, widget=HiddenInput)` per extra; seed `initial` from the team's `TeamExtra` rows on edit.
- `clean()` (only when `ucount_valid`): per extra, `count ≤ max(0, ucount − free_per_team)` else error; on edit, `count ≥ count_paid` else "Нельзя уменьшить «name»: часть оплачена."
- Remove `map_count` field + `clean_map_count()`. Also drop the `team.map_count = ...` write from the legacy `TeamForm.save()` (`forms.py:597`). Decision on the legacy `my_team` editor: **do not migrate it to extras now** — it's a stale year-2023/2024 superuser path; just keep it crash-free (defensive `__init__`) and remove the now-dead maps block from `my_team.html` + the `#id_map_count` reads in `team.js`. `website/forms.py` may keep a re-export shim if anything external imports the old names.

**Reconciliation** (`check_vtb_payments.py`, VTB PAID path): keep race-fee lines; replace `team.map_count_paid += payment.map` with a loop over `payment.extras` doing `get_or_create(TeamExtra)` + `count_paid += pe.count` (+ clamp `count` up to `count_paid`). Inside existing `transaction.atomic()` + `status==done` guard. `sold_out` auto-flip unchanged.

**Race edit config:** `RaceForm` gains hidden `extras_json`; `post` parses + validates + calls new `_reconcile_extras(race, cleaned)` inside the existing atomic block. Validation: `code` non-empty, unique within race, `^[a-z_]+$`; `price ≥ 0`; `free_per_team ≥ 0`. Reconcile: match by `id` (existing) or `code` (new); `order=index`; update `name/price/free_per_team/order/is_active`; delete only rows with **zero** `TeamExtra` usage, otherwise force `is_active=False`.

**JS config island** (`build_team_form_context`): replace `mapCountPaid`/`mapPrice`/`freeMaps` with
`"extras": [{code, name, price, freePerTeam, count, countPaid}, ...]`; keep
`currentPrice`/`paidPeople`/`raceRemaining`/`currentCategoryId`/`isEdit`/`bypassLimits`.
`team-form.js` renders one stepper per extra (loop), each bounded `countPaid … (ucount − freePerTeam)`, recomputes caps on team-size change, and computes the live total mirroring `compute_team_charge`.

## What Goes Where

- **Implementation Steps** (checkboxes): models, migrations (incl. data migration), helpers, form, views, reconciliation, race-edit config, JS/templates, tests, docs.
- **Post-Completion** (no checkboxes): the deferred legacy-column-drop migration, manual VTB/SBP sandbox payment + reconciliation verification, Django admin registration sanity check.

## Implementation Steps

### Task 1: Add-on models + initial migration in `apps/race`

**Files:**
- Create: `src/apps/race/models.py`
- Create: `src/apps/race/migrations/__init__.py`
- Create: `src/apps/race/migrations/0001_initial.py` (via `makemigrations`)
- Modify: `src/apps/race/tests.py`

- [x] create `RaceExtra`, `TeamExtra`, `PaymentExtra` in `src/apps/race/models.py` with the FKs, `unique_together`, `ordering`, and `on_delete` (`CASCADE` to owner, `PROTECT` to `RaceExtra`) per Technical Details
- [x] add `__str__` to each model (readable in admin / shell)
- [x] generate the migration: `uv run python src/manage.py makemigrations race_app` (creates `migrations/__init__.py` if absent — verify the dir exists and is a package)
- [x] ensure `0001_initial` declares an **explicit** `dependencies = [("website", "0072_payment_vtb_payment")]` (the cross-app FKs reference `website.Race/Team/Payment`); cross-app FK migrations frequently need this to apply on a fresh DB
- [x] sanity-check `makemigrations --check` produces no further changes and `migrate` applies cleanly on a **truly empty** DB (not `--reuse-db`), since the whole pytest suite fails at DB setup if this is wrong
- [x] write tests: create each model; `unique_together` violations raise `IntegrityError`; `RaceExtra` default `ordering`; `PROTECT` blocks deleting a `RaceExtra` referenced by a `TeamExtra`/`PaymentExtra`
- [x] run tests — must pass before Task 2

### Task 2: Data migration — maps → extras (backfill, legacy columns retained)

**Files:**
- Create: `src/apps/race/migrations/0002_migrate_maps_to_extras.py`
- Modify: `src/apps/race/tests.py`

- [x] create a data migration with `dependencies = [("race_app", "0001_initial"), ("website", "0072_payment_vtb_payment")]`, using `apps.get_model` for `Race`/`Team`/`Payment`/`RaceExtra`/`TeamExtra`/`PaymentExtra` (no live-code imports)
- [x] forward: for every `Race` having ≥1 non-deleted team with `map_count>0` OR `map_count_paid>0`, `get_or_create(race=race, code="map", defaults={"name": "Доп. карты", "price": 200, "free_per_team": 2, "order": 0, "is_active": True})` — key the lookup on `(race, code)` only, everything else under `defaults=`, so a pre-existing `code="map"` row never triggers an `IntegrityError` against `unique_together` (hardcode 200/2 in the migration)
- [x] forward: for each such team, create `TeamExtra(team, race_extra=<race's map extra>, count=map_count, count_paid=map_count_paid)`; resolve the team's race via `team.category2.race` — if `category2 is None` (or its race is missing), skip + `print`/log the team id (don't crash)
- [x] forward: for each `Payment` with `map>0`, create `PaymentExtra(payment, race_extra=<map extra for payment.team's race>, count=payment.map, unit_price=200)`; **guard the nullable `Payment.team` AND `team.category2`** — if either is `None` (orphaned/legacy payment), skip + log the payment id rather than `AttributeError`
- [x] reverse: delete `PaymentExtra`/`TeamExtra`/`RaceExtra` rows with `code="map"` (data recoverable from still-present legacy columns)
- [x] write tests: seed a race + team with `map_count`/`map_count_paid` and a `Payment.map>0`, run the migration logic, assert the expected `RaceExtra`/`TeamExtra`/`PaymentExtra` rows; assert a team with `map_count>0` but no `category2` is skipped without raising; assert a `Payment.map>0` with `team=None` is skipped without raising; assert a race that already has a `code="map"` row doesn't raise (defaults-only); assert reverse removes the `code="map"` rows
- [x] run tests — must pass before Task 3

### Task 3: Pricing + payment-creation helpers (`apps/race/pricing.py`)

**Files:**
- Create: `src/apps/race/pricing.py`
- Modify: `src/apps/race/tests.py`

- [x] implement `compute_team_charge(team, race)` returning `(max(0, int(total)), lines)`: race-fee term `(ucount − paid_people) × race.current_price` plus, for each `race.extras.filter(is_active=True)`, `delta = max(0, count − count_paid)` at the extra's current `price`; emit an `ExtraCharge(race_extra, count=delta, unit_price=price)` per nonzero delta
- [x] implement `upsert_team_extras(team, cleaned_data, race)` — get_or_create a `TeamExtra` per active extra and set `count` from `cleaned_data["extra_<code>"]`
- [x] implement `create_team_payment(request, team, race)` — `cost, lines = compute_team_charge(...)`; if `cost == 0` return `None`; else create `Payment(... cost_per_person=race.current_price, paid_for=ucount − paid_people, payment_amount=cost, payment_with_discount=cost, status="draft", payment_method="sbp2")`, create a `PaymentExtra` per line, mint the VTB order (`VTBClient`, `VTBPayment.new_order_id("ORDER")`, `VTBPreparedPayment`) exactly as the current views do, return the redirect `HttpResponse`
- [x] do NOT write `Payment.map` from the helper (left default 0; dropped later)
- [x] write tests for `compute_team_charge`: no extras (fee only); one extra; multiple extras summed; `count == count_paid` → no add-on charge; partial delta (count 3, paid 1 → charge 2×price); `max(0)` floor when fully paid; `max = ucount − free` boundary respected by the value the caller passes (helper trusts validated counts)
- [x] write tests for `upsert_team_extras` (creates then updates the same row) and `create_team_payment` cost==0 → `None`
- [x] run tests — must pass before Task 4

### Task 4: Cutover — generic `TeamForm`, view wiring, reconciliation, JS/templates, maps-test rewrite

**Files:**
- Modify: `src/website/forms.py` (`TeamForm.__init__`, `clean`, remove `clean_map_count`, drop `map_count` write in `save`)
- Modify: `src/website/views/views_.py` (`AddTeam`, `build_team_form_context`, **`my_team` legacy path**)
- Modify: `src/website/views/team.py` (`EditTeamView`)
- Modify: `src/website/management/commands/check_vtb_payments.py`
- Modify: `src/static/js/team-form.js`, **`src/static/js/team.js`** (drop `#id_map_count` reads)
- Modify: `src/templates/website/add_team.html`, `src/templates/website/edit_team.html`, **`src/templates/website/my_team.html`** (remove the dead maps block `:264-268`)
- Modify: `src/website/tests.py`

- [x] `TeamForm.__init__`: **defensively resolve the race** (guard non-int `race_id` — `my_team` passes `request.POST or None` positionally; on a bad id set `self.extras = []` and add no fields, so `my_team` never 500s); otherwise build `self.extras` from active `RaceExtra`, add hidden `extra_<code>` IntegerField per extra, seed `initial` from the team's `TeamExtra` rows on edit
- [x] `TeamForm.clean()`: replace the maps block with the per-extra cap check (`count ≤ ucount − free_per_team`) and the edit-only `count ≥ count_paid` guard; remove the `map_count` field and `clean_map_count()`; drop the `team.map_count = ...` write in the legacy `TeamForm.save()` (`:597`) (keep a `forms.py` re-export shim only if an external importer needs it)
- [x] `build_team_form_context`: emit the `extras` list (code/name/price/freePerTeam/count/countPaid); drop `mapCountPaid`/`mapPrice`/`freeMaps` (and the `map_price`/`free_maps`/`map_count_paid` template context keys)
- [x] `AddTeam.post` + `EditTeamView.post`: after `form.is_valid()`, call `upsert_team_extras(...)`, then `create_team_payment(...)`; if it returns `None` (cost 0) redirect to the existing success URL; remove the inline maps formula, the `Payment.map=` arg, and the ad-hoc "can't reduce paid maps" guard in `EditTeamView` (now in `clean()`)
- [x] legacy `my_team`: confirmed crash-free under the new `__init__` (defensive guard) and remove the now-dead maps block from `my_team.html` + `#id_map_count` reads in `team.js`. **Not** migrated to extras (stale year-2023/2024 superuser path) — documented decision
- [x] **`FREE_MAPS`/`MAP_PRICE`**: their only importers (`views_.py:35-36`, `team.py:9`) are rewritten in this task → remove those imports here; leave the constant *definitions* in `forms.py` as dead code until the deferred follow-up migration (one consistent story: usages gone now, defs dropped with the columns)
- [x] `check_vtb_payments` VTB PAID path: replace `team.map_count_paid += payment.map` with the loop over `payment.extras` (`get_or_create` `TeamExtra`, `count_paid += pe.count`, clamp `count` up); keep race-fee lines, `transaction.atomic()`, the `status==done` guard, and the `sold_out` flip
- [x] `team-form.js` + `add_team.html`/`edit_team.html`: render one stepper per extra from the `extras` config (hidden `extra_<code>` inputs), bound `countPaid … (ucount − freePerTeam)`, recompute on team-size change; live total mirrors `compute_team_charge`; remove the hardcoded maps row; styling stays in `team-form.css` (`.team-register`); base-2 rule — manual inputs, no `form-control`; add a cross-reference comment in both `team-form.js` and `pricing.py` pointing at each other (the CLAUDE.md client/server-mirror rule)
- [x] rewrite existing maps tests in `src/website/tests.py` to the new field name (`extra_map` instead of `map_count`) and the new context keys — keep behavior covered, don't delete
- [x] write tests: create team with `extra_transfer=2` → one `Payment` + `PaymentExtra(count=2, unit_price=price)`, amount = fee + 2×price; edit that adds transfer later charges/snapshots only the delta; `cost==0` path → redirect, no `Payment`; over-cap rejected; edit drop-below-paid rejected; superuser `bypass_limits` still bypasses capacity gates but extras caps still apply
- [x] write test: **`my_team` POST regression** — posting to `my_team` (which calls `TeamForm(request.POST or None)`) does not 500 under the new `__init__`
- [x] write test: **reconciliation idempotency must exercise the command guard** — run the command's PAID-handling block twice for the same payment and assert the second pass short-circuits on `status==done` (credit applied exactly once); also assert get-or-create when no `TeamExtra` exists yet
- [x] write test: **client/server formula mirror** — the config island carries exactly the fields the JS needs (`extras[]` with `price`/`freePerTeam`/`count`/`countPaid`, plus `currentPrice`/`paidPeople`), and a server-side `compute_team_charge` case matches the documented total for a representative team
- [x] run full suite — must pass before Task 5

### Task 5: Race edit page — "Доп-услуги" configuration

**Files:**
- Modify: `src/apps/race/forms.py` (`RaceForm`)
- Modify: `src/apps/race/views.py` (`post`, new `_reconcile_extras`, `_validate_extra_rows`, context)
- Modify: `src/static/js/race_form.js`
- Modify: `src/templates/race/race_form.html`
- Modify: `src/static/css/race_form.css`
- Modify: `src/apps/race/tests.py`

- [x] `RaceForm`: add hidden `extras_json` field
- [x] `views.py`: add `_validate_extra_rows` (code non-empty + unique-within-race + `^[a-z_]+$`; `price ≥ 0`; `free_per_team ≥ 0`) and `_reconcile_extras(race, cleaned)` mirroring `_reconcile_price_tiers` (match by id/code, `order=index`, update scalar fields); delete only rows with zero `TeamExtra` usage, otherwise force `is_active=False`
- [x] wire parse → validate → `_reconcile_extras` into `post` inside the existing `transaction.atomic()` (alongside categories/price tiers), with the same per-row error echo on failure
- [x] add `extras` to `_build_context`/`build_context` and serialize existing rows for the editor (incl. each row's `has_teams`/usage flag so JS shows deactivate-not-delete)
- [x] `race_form.js` + `race_form.html` + `race_form.css`: add an "Доп-услуги" row editor (name/code/price/free_per_team/active toggle) serialized to `extras_json`; `code` editable on create, read-only once saved; "remove" maps to deactivate for in-use rows
- [x] write tests: `extras_json` add/update/deactivate reconciliation; in-use extra (`PROTECT`) is deactivated not deleted; duplicate code rejected; blank/invalid code rejected; `price`/`free_per_team` negative rejected; unused row hard-deleted
- [x] run tests — must pass before Task 6

### Task 6: Verify acceptance criteria

- [x] verify all Overview requirements: transfer purchasable on add + edit; maps migrated and behave as a `code="map"` extra; new add-on configurable on race edit with no code change (the edge cases — `cost==0`, over-cap, drop-below-paid, idempotency, category-less skip, deactivate-not-delete — are each covered by Task 2/4/5 tests; this task is the final gate, not a re-list) — confirmed: `RaceExtra/TeamExtra/PaymentExtra` models, `compute_team_charge`/`create_team_payment` helpers, `extras_json`/`_reconcile_extras` race-edit config all present; full suite green
- [x] run full test suite: `uv run pytest` — 314 passed
- [x] run `make format && make lint` and fix any findings — no Makefile present; ran the documented underlying tools (ruff --fix, black, isort, then ruff/black/isort --check + flake8): all pass, no changes needed
- [x] manual smoke: `uv run python src/manage.py migrate` on a copy with existing maps data, confirm backfilled rows look right — manual (skipped: not automatable; backfill logic covered by Task 2 migration tests)

### Task 7: Documentation + finalize

**Files:**
- Modify: `CLAUDE.md`
- Modify: `src/apps/race/__init__.py` (if a default_app_config note is needed — likely not)

- [ ] update `CLAUDE.md`: `apps.race` now owns models/migrations (note the cross-app FKs to `website`); document the add-ons system (RaceExtra/TeamExtra/PaymentExtra, `max = ucount − free`, the shared `compute_team_charge`/`create_team_payment` helpers, snapshot-based reconciliation, deprecated-but-present maps columns)
- [ ] note in `CLAUDE.md` that maps are now the `code="map"` extra and the legacy `map_count`/`map_count_paid`/`Payment.map`/`FREE_MAPS`/`MAP_PRICE` are pending removal (deferred migration)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention or external systems — no checkboxes, informational only.*

**Deferred follow-up migration (separate change, after prod verification):**
- Drop `Team.map_count`, `Team.map_count_paid`, `Payment.map`, and the `FREE_MAPS`/`MAP_PRICE` constants once production confirms no in-flight pre-deploy payments remain unreconciled and the backfill looks correct. Keep them through at least one deploy cycle so a payment created before the cutover and confirmed after it still credits correctly.

**Manual verification:**
- Run a real VTB/SBP sandbox payment for a team with a transfer add-on; confirm the `Payment` + `PaymentExtra` rows, then run `check_vtb_payments` and confirm `TeamExtra.count_paid` is credited exactly once (re-run to confirm idempotency).
- Browser check of the add/edit steppers: caps update on team-size change, live total matches the server charge, paid units can't be reduced.
- Race edit page: add a "breakfast" add-on end-to-end with no code change; confirm it appears on the team form.

**Admin/ops:**
- Decide whether to register `RaceExtra`/`TeamExtra`/`PaymentExtra` in Django admin (and `collectstatic` for the changed JS/CSS at Docker build time — already part of the build).
