# Make race-fee VTB `order_id` collision-free with ULIDs (mirror the donate flow)

## Overview
Race-fee VTB payments mint their `order_id` from the local DB primary key:
`order_id=f"ORDER_{payment.id}"`. `Payment.id` is a per-environment autoincrement, but dev and
prod **share the same VTB credentials**, so VTB's `order_id` namespace is shared. A test payment
created on dev reserves e.g. `ORDER_2918` at VTB; later prod's sequence reaches 2918, tries to
create `ORDER_2918`, and VTB rejects it as a duplicate.

The donate flow never has this problem: it uses a random ULID order key
(`order_id = f"SPUTNIK_{ulid}"`, globally unique regardless of environment) and links the domain
object to the VTB record via a foreign key (`DonateRequest.payment` OneToOne → `VTBPayment`,
`related_name="donate_request"`), reconciling by **following the FK** rather than parsing the id
out of `order_id`.

This change makes race-fee payments mirror that principle:
1. Race `order_id` becomes `ORDER_{ulid}` (random, collision-free across environments).
2. The race `Payment` gains a nullable OneToOne FK → `VTBPayment` (mirroring `DonateRequest.payment`).
3. The reconciliation command follows the FK, with a legacy `ORDER_<int>` parse fallback so
   in-flight pre-deploy payments still reconcile.

No behaviour changes for the payer; the only observable difference is the shape of `order_id`.

## Context (from discovery)
- **Mint sites (2):**
  - `src/website/views/views_.py:1893` (`AddTeam`) — `order_id=f"ORDER_{payment.id}"`
  - `src/website/views/team.py:197` (`EditTeamView`) — `order_id=f"ORDER_{payment.id}"`
- **Parse / reconciliation site (1):** `src/website/management/commands/check_vtb_payments.py:60-83`
  — branches on `SPUTNIK_` prefix for donations (`_process_donation`), else parses
  `int(order_id.split("_")[-1])` to find the `Payment`.
- **ULID generator (donate, the model to copy):** `src/donate/views.py:14-19` (`_CROCKFORD`,
  `_ulid()`), used at `:105` as `donate_order_id = f"SPUTNIK_{_ulid()}"`.
- **Link pattern to mirror:** `src/donate/models.py:8-13` — `DonateRequest.payment =
  OneToOneField(VTBPayment, on_delete=CASCADE, related_name="donate_request")`.
- **Models:** `Payment` at `src/website/models/models.py:519`; `VTBPayment` at
  `src/website/models/vtb.py:7` (`from_vtb_payload` upsert at `:43`). Both live in the `website`
  app and are re-exported via `src/website/models/__init__.py`.
- **Latest migration:** `0070_alter_race_place` → new migration is `0071`.
- **No VTB webhook/callback view exists** — reconciliation is *solely* the polling command
  `check_vtb_payments`. `check_vtb_connection.py` is an unrelated diagnostic command (takes
  `order_id` as a CLI arg) and is out of scope.
- **No existing tests** reference the command, `new_order_id`, or `ORDER_` parsing — test work is
  net-new. `src/donate/tests.py` mocks payloads with literal order_ids, so the generator refactor
  must not (and should not need to) change it.

## Development Approach
- **Testing approach**: Regular (code first, then tests). The change is small and mechanical; tests
  target the two new units (`VTBPayment.new_order_id`, `Command._resolve_race_payment`).
- Complete each task fully before moving to the next; all tests must pass before the next task.
- Run `make format && make lint` before committing (project requirement, per user memory).
- Maintain backward compatibility (legacy `ORDER_<int>` fallback in reconciliation).

## Testing Strategy
- **Unit tests** (pytest-style functions with `@pytest.mark.django_db`, per CLAUDE.md):
  - `VTBPayment.new_order_id(prefix)`: returns `"<prefix>_<26 Crockford chars>"`; two calls differ.
  - `Command._resolve_race_payment`: resolves the `Payment` via the FK when set (new ULID order).
  - `Command._resolve_race_payment`: falls back to the legacy `ORDER_<int>` parse when the FK is
    unset (simulates an in-flight pre-deploy payment); returns `None` for an unparseable id.
- The reconciliation `handle()` is a `while True` polling loop — **do not** test the loop; test the
  extracted helper and the generator directly.
- **e2e tests**: none — project has no UI e2e suite for this area.

## Progress Tracking
- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.
- Update this plan if scope changes during implementation.

## Solution Overview
Centralize order-id minting on the model that owns `order_id` (`VTBPayment.new_order_id`), give the
race `Payment` an explicit FK to its `VTBPayment` (so reconciliation never has to decode the id from
a string), and switch both race mint sites to the ULID form. The reconciliation command resolves the
`Payment` by following the FK first and only falls back to the old int-parse for records created
before this deploy.

Key design decisions (all confirmed up front):
- **OneToOne FK on `Payment`** (not a salted order_id, not an untyped id column on `VTBPayment`):
  it's the exact mirror of `DonateRequest.payment` and gives a real, queryable relation.
- **`on_delete=SET_NULL`** (vs. `CASCADE` used by `DonateRequest`): a race `Payment` is an
  authoritative bookkeeping record that predates and outlives its VTB record — deleting a VTB row
  must not delete the `Payment`.
- **Generator as a `VTBPayment` static method** taking a `prefix`: one source of truth shared by
  donate (`"SPUTNIK"`) and race (`"ORDER"`).
- **Keep the legacy parse as a fallback**, not a hard cutover, so payments created before deploy
  and paid after still reconcile automatically.

## Technical Details

### Model field (`src/website/models/models.py`, `Payment`)
```python
vtb_payment = models.OneToOneField(
    "VTBPayment",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="race_payment",
)
```
String ref `"VTBPayment"` resolves within the same `website` app.

### Generator (`src/website/models/vtb.py`)
Module level:
```python
import os
import time
...
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
```
On `VTBPayment`:
```python
@staticmethod
def new_order_id(prefix: str) -> str:
    val = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    ulid = "".join(_CROCKFORD[(val >> (5 * i)) & 0x1F] for i in range(25, -1, -1))
    return f"{prefix}_{ulid}"
```

### Reconciliation helper (`check_vtb_payments.py`)
```python
def _resolve_race_payment(self, vtb_payment):
    # New ORDER_<ulid> payments link via the explicit FK.
    try:
        return vtb_payment.race_payment
    except Payment.DoesNotExist:
        pass
    # Legacy fallback: order_id == "ORDER_<payment_id>".
    try:
        payment_id = int(vtb_payment.order_id.split("_")[-1])
    except (ValueError, AttributeError):
        return None
    return Payment.objects.filter(pk=payment_id).first()
```
The reverse OneToOne raises `RelatedObjectDoesNotExist`, a subclass of `Payment.DoesNotExist`, so
the `except` catches the "no FK set" case. The `SPUTNIK_` donation branch is unchanged.

### Migration
`0072_payment_vtb_payment.py`, `AddField` for `Payment.vtb_payment`, dependency
`("website", "0071_category_people_limit_race_people_limit")`. Nullable → no data backfill.

## What Goes Where
- **Implementation Steps**: model field + migration, generator method, donate refactor, the two race
  mint-site edits, the reconciliation helper, and all unit tests — all in this repo.
- **Post-Completion**: apply migration `0071` to staging/production at deploy; the dev↔prod VTB
  collision is resolved going forward (no retroactive cleanup of already-reserved VTB order ids is
  required — they simply age out).

## Implementation Steps

### Task 1: Add `VTBPayment.new_order_id` and route donate through it

**Files:**
- Modify: `src/website/models/vtb.py`
- Modify: `src/donate/views.py`
- Modify: `src/donate/tests.py` (only if a new generator test is added here)

- [x] In `src/website/models/vtb.py`: add `import os` and `import time`, the module-level
      `_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"`, and the `new_order_id(prefix)` static method
      on `VTBPayment` (see Technical Details).
- [x] In `src/donate/views.py`: replace `donate_order_id = f"SPUTNIK_{_ulid()}"` (line 105) with
      `donate_order_id = VTBPayment.new_order_id("SPUTNIK")` (`VTBPayment` already imported, line 12).
- [x] Delete the now-unused `_CROCKFORD` and `_ulid()` (lines 14-19) and the unused `import os` /
      `import time` from `src/donate/views.py`; confirm via grep that nothing else in that file uses
      them.
- [x] Write a test for `VTBPayment.new_order_id`: asserts `"PREFIX_"` prefix + 26-char Crockford
      suffix (all chars ∈ `_CROCKFORD`), and that two calls return different values.
- [x] Run the new test + `src/donate/tests.py` — must pass (donate payload mocks use literal
      order_ids, so they should be unaffected).

### Task 2: Add the `Payment.vtb_payment` FK and migration

**Files:**
- Modify: `src/website/models/models.py`
- Create: `src/website/migrations/0071_payment_vtb_payment.py`

- [x] Add the `vtb_payment` OneToOneField to `Payment` (`src/website/models/models.py:519`, see
      Technical Details): `on_delete=SET_NULL`, `null=True`, `blank=True`,
      `related_name="race_payment"`.
- [x] Generate the migration: `uv run python src/manage.py makemigrations website` → produced
      `0072_payment_vtb_payment.py` (not `0071` — the intervening `0071_category_people_limit_race_people_limit`
      merged after this plan was written) with dependency `("website", "0071_category_people_limit_race_people_limit")`.
- [x] Run `uv run python src/manage.py makemigrations --check --dry-run` — reports "No changes detected".
- [x] Run `uv run python src/manage.py migrate` against the local DB — applied cleanly.
- [x] (No new unit test for the field itself; it's covered indirectly by Task 4's FK-resolution
      test.)

### Task 3: Switch both race mint sites to ULID order ids + set the FK

**Files:**
- Modify: `src/website/views/views_.py`
- Modify: `src/website/views/team.py`

- [ ] `src/website/views/views_.py` (`AddTeam`, ~line 1892-1898): change
      `order_id=f"ORDER_{payment.id}"` → `order_id=VTBPayment.new_order_id("ORDER")`; after
      `vtb_payment = VTBPayment.from_vtb_payload(payload)` add
      `payment.vtb_payment = vtb_payment` and `payment.save(update_fields=["vtb_payment"])`. Leave
      `order_name=f"...({payment.id})"` untouched (human-readable, not used for matching).
- [ ] `src/website/views/team.py` (`EditTeamView`, ~line 196-202): same two edits.
- [ ] Confirm `VTBPayment` is imported in both modules (already used for `from_vtb_payload`).
- [ ] Add/extend a view test (or a focused unit assertion) confirming a created race `Payment` has
      `vtb_payment` set and the minted `order_id` starts with `"ORDER_"` and is **not** `ORDER_<int>`
      — mock `VTBClient.create_order`/`_ensure_token` so no network call is made (follow the mocking
      style in `src/donate/tests.py`).
- [ ] Run the new test(s) — must pass.

### Task 4: Reconcile via the FK with a legacy fallback

**Files:**
- Modify: `src/website/management/commands/check_vtb_payments.py`
- Modify: `src/website/tests.py`

- [ ] Add `_resolve_race_payment(self, vtb_payment)` to the command (see Technical Details).
- [ ] Replace the inline parse block (lines 64-69: the `# order_id has format ORDER_<payment_id>`
      comment + `int(order_id.split("_")[-1])` + `Payment.objects.filter(pk=...).first()`) with
      `payment = self._resolve_race_payment(vtb_payment)`; keep the existing
      `if not payment or payment.status == Payment.STATUS_DONE: continue` guard and the team-update
      logic that follows. The `SPUTNIK_` donation branch stays unchanged.
- [ ] Write a test: `Payment` linked to a PAID `VTBPayment` via the FK (ULID order_id) →
      `_resolve_race_payment` returns that `Payment`.
- [ ] Write a test: legacy `VTBPayment(order_id="ORDER_<id>")` with **no** FK set →
      `_resolve_race_payment` falls back to the int parse and returns the matching `Payment`;
      an unparseable order_id (e.g. a stray non-numeric tail) returns `None`.
- [ ] Run the new tests — must pass.

### Task 5: Verify acceptance criteria

- [ ] `grep -rn "ORDER_{payment.id}\|f\"ORDER_{" src/` returns no matches (both mint sites converted).
- [ ] `grep -rn "_ulid\|_CROCKFORD" src/donate/` returns no matches (helper fully moved).
- [ ] `grep -rn "order_id.split" src/` shows the only remaining int-parse is inside
      `_resolve_race_payment`'s fallback.
- [ ] Run `make format && make lint` — must pass.
- [ ] Run full test suite: `uv run pytest` — must pass.
- [ ] `uv run python src/manage.py makemigrations --check --dry-run` — no drift.

### Task 6: [Final] Documentation & wrap-up

**Files:**
- Modify: `CLAUDE.md` (Payments section)

- [ ] Add a short note to the **Payments** section of `CLAUDE.md`: race-fee and donation VTB
      `order_id`s are random ULIDs (`ORDER_<ulid>` / `SPUTNIK_<ulid>`) minted by
      `VTBPayment.new_order_id(prefix)`; reconciliation follows the `Payment.vtb_payment` /
      `DonateRequest.payment` FK (the `ORDER_<int>` int-parse remains only as a legacy fallback).
- [ ] Move this plan to `docs/plans/completed/`.

## Post-Completion
*Items requiring manual intervention or external systems — no checkboxes, informational only*

**External system updates**:
- Apply migration `0071_payment_vtb_payment` to staging and production databases at deploy time.
- After deploy, any race payments created *before* the deploy (old `ORDER_<int>` order ids, no FK)
  reconcile through the legacy fallback in `_resolve_race_payment`. Once those have all settled
  (PAID/EXPIRED), the fallback branch can be removed in a future cleanup — optional, not required.
- No retroactive cleanup of VTB-side reserved order ids is needed; the dev↔prod collision is
  prevented going forward by ULID uniqueness.
