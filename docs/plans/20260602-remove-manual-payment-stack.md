# Remove the legacy `/payments/` endpoint and manual-payment stack

## Overview
Remove the `/payments/` admin verification endpoint and **all** related manual-payment
code. This stack existed to manually verify bank transfers (Сбербанк / СБП / Card):
a user submitted "I paid, here is my card number", and an admin confirmed/cancelled
each payment by hand on the `/payments/` page.

That whole flow is dead. Today every payment goes through VTB `sbp2` and is confirmed
**automatically** by `check_vtb_payments` (the `_settle_race_payment` method credits
`paid_people`/`paid_sum`, flips `OPEN → SOLD_OUT`, and credits add-ons). The manual
stack is no longer reachable from the live UI.

This is a **pure deletion**:
- **No DB migration.** Tables (`Payment`, `PaymentLog`, `PaymentsYa`,
  `SbpPaymentRecipient`) are all **kept for history** per the explicit requirement.
- Only **unused model methods** are removed (not fields, not tables).
- The live VTB `sbp2` flow and `check_vtb_payments` command are **untouched**.

Scope confirmed in brainstorm = "full sweep" of three layers:
- **Layer A** — admin verification page (`/payments/` + confirm/cancel/up/down)
- **Layer B** — user-facing manual-pay flow (`TeamPayment` + sberbank/sbp templates)
- **Layer C** — older 2024/Yandex API stubs (`NewPaymentView`, `paymentinfo`,
  `get_cost`, `yandex_payment`, `success`) + the `PaymentsYa` methods + orphaned JS

## Context (from discovery)

**Files/components involved:**
- `src/website/views/views_.py` — all the views being deleted
- `src/website/views/__init__.py` — re-export list
- `src/website/urls.py` — routes + an import line
- `src/templates/payment_list.html`, `src/templates/website/{sberbank,sbp,payment_process,success}.html`
- `src/static/js/team.js`, `src/static/js/index_reg.js` — orphaned legacy JS
- `src/website/models/models.py` — `PaymentsYa` methods to remove
- `src/website/tests.py` — `_confirm_payment` helper rewrite

**Related patterns found:**
- Live confirmation lives in
  `src/website/management/commands/check_vtb_payments.py::_settle_race_payment`
  (lines ~72-109). It is the canonical "credit a confirmed payment" path and the
  template the test-helper rewrite must mirror.
- Tests are **pytest-style functions** with `@pytest.mark.django_db`, not `TestCase`.

**Dependencies / things deliberately kept:**
- `PaymentLog` is still read by `Team.has_payment_history` (`models.py:399-406`) → keep model + table.
- `SbpPaymentRecipient` is still the `Payment.recipient` FK target → keep model + table.
- `PaymentsYa` table kept for history (and still referenced by the legacy
  `email.py:send_to_all_teams` refund script, which uses **fields, not methods**).
- `admin.py` registrations for `Payment`, `PaymentsYa`, `SbpPaymentRecipient` kept
  (read-only history viewing).
- `TransferView` / `Transfer` are **bus/passenger transfer** — unrelated, not touched.

## Development Approach
- **Testing approach**: Regular (this is a deletion; the only test work is rewriting
  the `_confirm_payment` helper so the existing sold-out/people-limit tests keep
  passing against the live VTB confirmation logic instead of the deleted Yandex path).
- Work top-down: remove views → exports → urls → templates → JS → model methods →
  test helper → verify. After each task, run the relevant tests.
- **No DB migration is produced or expected.** If a migration appears, something is wrong.
- Run `make format && make lint` before committing (project requirement).

## Testing Strategy
- **Unit tests**: The suite has no tests targeting the deleted views directly (verified).
  The only affected tests are the sold-out/people-limit tests that go through the
  `_confirm_payment` helper. Rewriting the helper (Task 7) keeps those green.
- **No e2e**: project has no UI e2e harness; pytest is the gate.
- Full gate: `uv run pytest` must pass before the plan is considered done.

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- keep this plan in sync with actual work

## Solution Overview
Delete dead code in dependency order so the tree never references a removed symbol:
views first, then their re-exports, then their URL routes, then the templates they
rendered, then the orphaned JS that called the removed API endpoints. Finally strip the
now-unused `PaymentsYa` methods (keeping the table) and repoint the one test helper at
the live confirmation logic. Verify with `grep` that nothing dangles, then run the suite.

## Technical Details

**Views to delete** (`src/website/views/views_.py`), with approximate current line anchors
(verify by symbol, not line number — deletions shift everything):
- `success` (~611), `NewPaymentView` (~621), `ConfirmPaymentView` (~678),
  `CancelPaymentView` (~727), `PaymentUp` (~739), `PaymentDown` (~764),
  `paymentinfo` (~804), `get_cost` (~827), `yandex_payment` (~836),
  `TeamPayment` (~1444), `payment_list` (~1690).

**URL names to remove** (`src/website/urls.py`): `payment-list`, `confirm-payment`,
`cancel-payment`, `payment-up`, `payment-down`, `pay_team`, `new_payment`,
`paymentinfo`, `getcost`, `yandexinform`, and the unnamed `^success/...` re_path
(line ~103). Also fix line ~9:
`from .views import CancelPaymentView, ConfirmPaymentView, RaceIdRedirectView`
→ `from .views import RaceIdRedirectView`.

**Model methods to delete** (`src/website/models/models.py`, class `PaymentsYa`):
`get_cost` (~210, static), `new_payment` (~214), `get_sum` (~268), `update_team` (~276).
Keep the class declaration and every field.

**Test helper** (`src/website/tests.py::_confirm_payment`, ~1882): currently calls
`PaymentsYa.update_team`. Replace its body with logic mirroring
`_settle_race_payment`: create the `Payment`, then
`team.paid_people += paid_for`, `team.paid_sum += amount`, `team.save()`, then the
sold-out check (`race = team.category2.race`; if `race.people_limit` and
`reg_status == RegStatus.OPEN` and `race.people_count() >= race.people_limit` →
set `SOLD_OUT`, save). Remove `PaymentsYa` from the import on line 15.

## What Goes Where
- **Implementation Steps** (checkboxes): all code/template/JS deletions, the model-method
  removal, the test-helper rewrite, grep verification, and the test run — all doable in
  this repo.
- **Post-Completion** (no checkboxes): deploy note about the now-404 routes; optional
  later cleanup of `email.py:send_to_all_teams`.

## Implementation Steps

### Task 1: Delete the manual-payment views

**Files:**
- Modify: `src/website/views/views_.py`

- [ ] delete view functions/classes: `success`, `NewPaymentView`, `ConfirmPaymentView`, `CancelPaymentView`, `PaymentUp`, `PaymentDown`, `paymentinfo`, `get_cost`, `yandex_payment`, `TeamPayment`, `payment_list`
- [ ] remove imports in `views_.py` that became unused (likely `PaymentsYa`, `PaymentLog`, `SbpPaymentRecipient`, `csrf_exempt`, `strftime`/`gmtime`) — verify each is unused elsewhere in the file before removing; leave imports still used by surviving code
- [ ] sanity check: `uv run python src/manage.py check` (file still imports cleanly — `__init__.py`/`urls.py` will still be broken until Tasks 2-3; that is expected here)
- [ ] (no new tests in this task — see Task 7 for the only test change)

### Task 2: Prune the views package re-exports

**Files:**
- Modify: `src/website/views/__init__.py`

- [ ] remove from the `from .views_ import (...)` list: `CancelPaymentView`, `ConfirmPaymentView`, `NewPaymentView`, `PaymentDown`, `PaymentUp`, `TeamPayment`, `get_cost`, `payment_list`, `paymentinfo`, `success`, `yandex_payment`
- [ ] keep all surviving exports (e.g. `AddTeam`, `TransferView`, `RaceIdRedirectView`, …)

### Task 3: Remove the URL routes

**Files:**
- Modify: `src/website/urls.py`

- [ ] delete routes: `payments/` (`payment-list`), `payments/confirm/<int:pk>/` (`confirm-payment`), `payments/cancel/<int:pk>/` (`cancel-payment`), `payments/<int:pk>/up/` (`payment-up`), `payments/<int:pk>/down/` (`payment-down`)
- [ ] delete routes: `team/<int:team_id>/pay/` (`pay_team`), `api/v1/newpayment/` (`new_payment`), `api/v1/paymentinfo/` (`paymentinfo`), `api/v1/getcost/` (`getcost`), `yandexinform/` (`yandexinform`), and the `^success/(?P<teamid>...)/` re_path
- [ ] fix the import line `from .views import CancelPaymentView, ConfirmPaymentView, RaceIdRedirectView` → `from .views import RaceIdRedirectView`
- [ ] `uv run python src/manage.py check` — must pass now (URLConf resolves cleanly)

### Task 4: Delete the manual-payment templates

**Files:**
- Delete: `src/templates/payment_list.html`
- Delete: `src/templates/website/sberbank.html`
- Delete: `src/templates/website/sbp.html`
- Delete: `src/templates/website/payment_process.html`
- Delete: `src/templates/website/success.html`

- [ ] delete the five template files listed above
- [ ] grep to confirm none are `{% include %}`d or `render()`ed elsewhere: `grep -rn "payment_list.html\|sberbank.html\|sbp.html\|payment_process.html\|website/success.html" src/`

### Task 5: Delete orphaned legacy JS (verify first)

**Files:**
- Delete: `src/static/js/team.js`
- Delete: `src/static/js/index_reg.js`

- [ ] verify unreferenced: `grep -rn "team\.js\|index_reg\.js" src/templates/ src/static/` returns no template/asset load of these files
- [ ] confirm these are hand-written app JS, **not** vendored `*.min.js` (memory: vendored assets are off-limits; these two are not vendored)
- [ ] delete `src/static/js/team.js` and `src/static/js/index_reg.js`

### Task 6: Remove unused `PaymentsYa` methods (keep table)

**Files:**
- Modify: `src/website/models/models.py`

- [ ] delete `PaymentsYa.get_cost` (static), `PaymentsYa.new_payment`, `PaymentsYa.get_sum`, `PaymentsYa.update_team`
- [ ] keep the `PaymentsYa` class declaration and **all fields** unchanged (table preserved for history)
- [ ] do **not** touch `PaymentLog`, `SbpPaymentRecipient`, or `Payment` (models, fields, `STATUS_CHOICES` all kept for history)
- [ ] confirm no migration is generated: `uv run python src/manage.py makemigrations --check --dry-run` reports **no changes** for `website`

### Task 7: Rewrite the `_confirm_payment` test helper

**Files:**
- Modify: `src/website/tests.py`

- [ ] rewrite `_confirm_payment` to mirror `check_vtb_payments._settle_race_payment`: after creating the `Payment`, do `team.paid_people += paid_for`, `team.paid_sum += amount`, `team.save()`, then the `OPEN → SOLD_OUT` check (`race = team.category2.race`; if `race.people_limit` and `race.reg_status == RegStatus.OPEN` and `race.people_count() >= race.people_limit` → set `SOLD_OUT`, `race.save(update_fields=["reg_status"])`)
- [ ] remove `PaymentsYa` from the import on line ~15 (`from website.models.models import PaymentsYa, Team` → `from website.models.models import Team`); ensure `RegStatus` is imported where the helper lives
- [ ] run the dependent tests: `uv run pytest src/website/tests.py -k "sold_out or cap or people_limit"` — all must pass unchanged

### Task 8: Verify acceptance criteria

- [ ] grep for every removed symbol/URL-name/template path across `src/` — zero dangling refs in Python, templates (`{% url %}`), or JS: `grep -rn "payment-list\|confirm-payment\|cancel-payment\|payment-up\|payment-down\|pay_team\|'new_payment'\|paymentinfo\|getcost\|yandexinform\|payment_list\|ConfirmPaymentView\|CancelPaymentView\|PaymentUp\|PaymentDown\|TeamPayment\|NewPaymentView" src/`
- [ ] grep that no live code calls the removed `PaymentsYa` methods: `grep -rn "\.update_team(\|PaymentsYa.get_cost\|\.new_payment(\|\.get_sum(" src/`
- [ ] `uv run python src/manage.py check` passes
- [ ] `uv run python src/manage.py makemigrations --check --dry-run` shows **no** new migration
- [ ] full suite: `uv run pytest`
- [ ] `make format && make lint`

### Task 9: [Final] Update docs and close out

**Files:**
- Modify: `CLAUDE.md` (only if a documented pattern changed)

- [ ] update `CLAUDE.md` if any documented behavior changed (likely minimal — the live VTB flow is unchanged; consider a one-line note that the manual sberbank/sbp verification flow was removed)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion
*Items requiring manual intervention or external systems — informational only*

**Deploy notes:**
- After deploy, the old paths (`/payments/`, `/team/<id>/pay/`, `/api/v1/newpayment/`,
  `/api/v1/paymentinfo/`, `/api/v1/getcost/`, `/yandexinform/`, `/success/<id>/`) return
  **404** by design (no redirects). Confirm no external integration or bookmark relies on
  them — in particular the Yandex notification webhook (`/yandexinform/`) is decommissioned.
- `collectstatic` runs at Docker build; deleting the two JS files just means they stop
  being collected. No action needed beyond a normal build.

**Optional later cleanup (out of scope here):**
- `src/website/email.py:send_to_all_teams` is a legacy one-off refund script that reads
  `PaymentsYa` rows (fields only — it does not break from this change). Can be removed in
  a separate pass if desired.
- The deprecated `Payment.map` / `Team.map_count*` columns and `STATUS_DRAFT_WITH_INFO`/
  `STATUS_CANCEL` constants remain (history). A future migration could drop truly-dead
  columns once a deploy cycle confirms nothing in-flight needs them.
