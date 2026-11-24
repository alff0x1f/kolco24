# apps.race

Loaded on demand when working in this directory. Root rules are in `/CLAUDE.md`.

`apps.race` — race detail page (`/race/<slug>/`) and the unified teams-list page. **Owns the add-on models +
migrations** (`src/apps/race/models.py`, `migrations/` — its first; `0001_initial` declares an explicit
`dependencies = [("website", "0072_payment_vtb_payment")]` because the FKs cross into `website`): `RaceExtra`/
`TeamExtra`/`PaymentExtra` (see the **Team add-ons** note below). All other models still remain in `website`. Entry
points: `src/apps/race/views.py:RacePageView`, `RaceTeamsView`, `RaceEditView`, and `RacePostEditView`. Uses
`label = "race_app"` in `AppConfig` to avoid Django app-registry collision with the `race` model label.
`RacePostEditView` (template `src/templates/race/post_form.html`, assets `src/static/css/post_form.css`,
vendored EasyMDE/Font Awesome CSS and JS, plus `src/static/js/markdown-editor.js`) is one create/edit CBV for
`add_post` (`race/<slug>/post/add/`) and `edit_post` (`race/<slug>/post/<id>/edit/`). It gates on
`is_race_admin`, which allows both `RaceAdmin` ADMIN and MODERATOR rows, looks up the race by slug before auth,
and filters an edit lookup by `race=race` as well as the post id. Its `_load` helper always returns the
`(race, post, response)` triple; callers return a non-`None` response immediately. `RacePageView.build_context`
uses a wider `news_qs` only for that same `is_race_admin` check:
`NewsPost.objects.filter(race=race).select_related("race").order_by("-publication_date", "-pk")`.
This lets a RaceAdmin see posts of an unpublished race, while a bare superuser with no `RaceAdmin` row keeps the
public feed; a far-future scheduled post sorts to the top of the admin slice and can displace a real post from its
ten-row limit.
`RaceTeamsView` (template `src/templates/race/teams.html`, assets `src/static/css/teams.css` +
`src/static/js/teams.js`) backs all three teams URL names (`all_teams`, `teams2`, `my_teams`, wired in
`website/urls.py`); it embeds teams/categories as JSON `<script>` blocks and does search/filter/sort entirely
client-side. `RaceEditView` (template `src/templates/race/race_form.html`, assets `src/static/css/race_form.css` +
`src/static/js/race_form.js`, form `src/apps/race/forms.py:RaceForm`) is one CBV for both creating and editing a race,
backing the `add_race` (`races/new/`) and `edit_race` (`race/<slug>/edit/`) URL names. Create is superuser-only; edit
requires `can_edit_race(user, race)` (in `src/apps/race/permissions.py` — **only** a `RaceAdmin` row with
`role=ADMIN`; being a superuser grants nothing here, so a superuser without such a row gets a 403 on every
`can_edit_race` page — legend, codes, map, app-data, payments, protocol build/freeze). It edits all scalar `Race` fields and inline-manages the race's `Category` rows (incl. `min_people`,
`max_people`, `people_limit`), `RacePriceTier` rows, and `RaceExtra` («Доп-услуги») rows, posted as hidden
`categories_json` + `price_tiers_json` + `extras_json` inputs and reconciled (add/update/delete, `order=index`) inside
one `transaction.atomic()`. Add-on reconciliation (`_reconcile_extras` + `_validate_extra_rows`) uses a **softer**
delete policy than categories: a row still referenced by any `TeamExtra` is force-deactivated (`is_active=False`)
rather than deleted (and `PROTECT` on the FK is the backstop); `code` is validated `^[a-z_]+$`, unique-within-race,
editable on create but read-only once saved. `RacePageView.build_context` exposes `can_edit_race` so `race_page.html`
shows an "Редактировать" button (admins). The "+ Новая гонка" link is not here — it lives
in a superuser-only «Управление» card under «Соревнования» on the home page. That card reuses the race page's
`.card-admin`/`.admin-actions` markup; both classes live in `theme-2.css` so every `base-2.html` page can use them.
The race page's «Администраторы и модераторы» card is shown to `can_edit_race` users (read-only) and to
superusers, who also get an add form (email or numeric user ID + role; adding an already-assigned user changes the
role) and per-row remove buttons. Both POST to `RaceAdminsView` (`race_admins`, `race/<slug>/admins/`,
`action=add|remove`) — superuser-only (anon → `login`, others → 403), PRG back to `#race-administrators`, feedback via
`messages` tagged `race-admins` and rendered only inside that card.
`RaceLegendEditView` (template `src/templates/race/legend_form.html`, assets `src/static/css/legend_form.css` +
`src/static/js/legend_form.js`) is a bulk-edit spreadsheet page for a race's checkpoints, backing the `edit_legend`
(`race/<slug>/legend/edit/`) URL name. Gated on `can_edit_race`. **Must save via `instance.save()`** — never
`QuerySet.update()` — so the legend-crypto `post_save` signals fire (lock toggle creates/destroys `CheckpointSecret`
and rebuilds bundles; a bulk update would bypass signals and leak cleartext). Deletion guard: a КП absent from the
submitted payload is only deleted if it has no `CheckpointTag` rows — those represent physically provisioned NFC
chips that would be silently destroyed; a tag-bearing КП raises `ValueError` and rolls back the whole save.
A read-only «Чипы» column shows each КП's `tag_count` (`annotate(Count("tags"))`; re-read from the DB on an error
re-render, the client's value is dropped) plus a live «размечено X из Y» summary that skips `hidden` КП.
`RaceLegendCodesView` (template `src/templates/race/legend_codes.html`, assets `src/static/css/legend_codes.css` +
`src/static/js/legend_codes.js`) is a read-only table of per-tag NFC codes for field-crew provisioning, backing the
`legend_codes` (`race/<slug>/legend/codes/`) URL name. It is the web twin of `manage.py export_legend_codes --race
<id>` — same `CheckpointTag` queryset ordered by `checkpoint__number` then `id`, same `—` placeholder for tags without
a `code` yet, same `nfc_uid / КП number / code(hex)` columns. The JS "Скопировать CSV" button builds RFC-4180 CSV
from the rendered table and writes it to the clipboard. Gated on `can_edit_race`.
`RaceChecklistView` (`race_checklist`, `race/<slug>/checklist/`, template `src/templates/race/checklist.html` —
**standalone, no base template**, so nothing but the table prints; CSS `src/static/css/checklist.css`) is a printable
A4 list for handing out start packets/maps by hand: one row per paid team (`paid_people > 0`) with start number,
team id, name, category `code`, `paid/ucount`, full member names (non-empty `athlet1…6`, one per line) and an empty
box column whose header comes from `?column=` (default «Отметка»). `?category=<id>` prints one category at a time
(an unknown id falls back to the whole race). Sorted by `start_number` **numerically**
(non-numeric numbers after). Gated by `_load_race_for_admin`.
`RaceExtrasChecklistView` (`race_extras_checklist`, `race/<slug>/extras-checklist/`) is its add-on twin, one
`RaceExtra` per sheet (`?extra=<code>`). Quantity is **`TeamExtra.count_paid` only** — `free_per_team` units are
deliberately not counted. `TeamExtra.objects` bypasses `TeamManager`, so soft-deleted teams are excluded by hand.
`RaceTeamsAdminView`/`RaceTeamsAdminExportView` (`race_teams_admin`, `race/<slug>/teams-admin/`, standalone template
`src/templates/race/teams_admin.html` reusing `checklist.css` + `teams_admin.css`; CSV `race_teams_admin_export`,
`…/teams-admin/export/`, `;` + BOM like the payments export) list a race's teams for organizers: ID, start number,
name, paid seats, category, member transfers (`TeamMemberMove`) and refunds (`PaymentRefund`, zero-amount audit rows
skipped). `src/apps/race/teams_admin.py:team_rows` is the
single source for both. A team is listed if it has paid seats **or** any transfer/refund history (so a team that
lost all seats keeps its row); soft-deleted teams get no row but still show as the other side of a transfer.
Every transfer is listed on its own (date, signed seats, other team) — no grouping, by decision; refunds are one
summary (seats, ₽, count, last date) with every refund in a `<details>`. CSV transfer entries start with the date,
not the sign, so `_csv_safe` doesn't prefix an apostrophe. A refund without a bank date is dated by its journal
row's `created_at` (as on «Платежи») — not `payment.updated_at`, which `record_refund` never touches. `start_number_key` lives in
`teams_admin.py` and is shared with the checklist. `?category=` and the gate work as on the checklist.
`RacePaymentsView`/`RacePaymentsExportView` back the organizer-only «Платежи» page — a read-only payment register
plus totals, for tracking how the money came in. URL names `race_payments` (`race/<slug>/payments/`, template
`src/templates/race/payments.html`, assets `src/static/css/payments.css` + `src/static/js/payments.js`) and
`race_payments_export` (`race/<slug>/payments/export/`, CSV). Both gated by the module-level helper
`_load_race_for_admin` (anon → `login` redirect with `?next=`, non-admin → 403) — the same rule as the map page.
**`src/apps/race/finance.py` is the single source** for both: `payment_rows(race)` returns flat dicts over
`Payment.objects.filter(team__category2__race=race)` (a payment with `team=None` can't be tied to a race and is
skipped; payments of **soft-deleted** teams are deliberately **kept** — `Payment.objects` doesn't go through
`TeamManager`, and that money was really received), `extras_catalog(race)` lists the race's `RaceExtra` rows
(inactive ones included — they may still carry sales), `filter_rows`/`csv_rows` serve the export only. Invariants:
**participation is the residual** `fee_sum = payment_amount + discount_amount − extras_sum`, never
`paid_for × cost_per_person` (with a promo or add-ons `payment_amount` deliberately diverges from that product — see
`create_team_payment`); per-add-on revenue comes from each `PaymentExtra.unit_price` snapshot (`extras_money`), not
from splitting the payment total; **all dates are formatted server-side** (`paid_at`/`paid_sort`/`paid_date` via
`timezone.localtime`) because `_safe_json` is a bare `json.dumps` that can't serialize `datetime`, and day buckets
must use the project timezone rather than the browser's. `paid_at` is `VTBPayment.status_changed_at` for a `done`
payment (fallback `updated_at`), `created_at` otherwise — `Payment.payment_date` is never populated anywhere and is
not read. The page ships **all** rows in a JSON island with an empty `<tbody>` and does filtering, sorting and every
total client-side (like `RaceTeamsView`), so totals always follow the visible filter; refunds (`cancel`) get their
own «Возвращено» tile and are never folded into the income breakdown. **A refund flips the payment `done → cancel`**
(`settlement.py:refund_payment`, the only writer of `cancel`, and only ever from `done`), so the `done` sum is
**already net** — «Осталось» is that sum as-is and must never subtract refunds again, while gross («Поступило») is
reconstructed by adding the `cancel` sum back. The CSV uses `;` **plus a BOM** for Russian
Excel, and every
**text** cell goes through `_csv_safe`, which prefixes an apostrophe to a value starting with `= + - @ \t \r`
(a team name is user-supplied, so `=1+1` would otherwise become a live formula; `csv.writer` protects the file
structure, not the spreadsheet). Numbers are passed through untouched, so a negative amount keeps its sign.
`RaceStartsView`/`RaceStartsDataView` back the organizer-only «Старты» page (`race/<slug>/starts/`, JSON polled every
20 s, gate `_load_race_for_admin`). The **only** source is `Team.start_time` (what the protocol reads); the set is
paid teams (`paid_people > 0`). People are `Team.ucount` (current team size), not `paid_people` — a dropped-out member
keeps the paid seat but won't come; «Финиш» counts the same way. An unset or unformattable time is `null` (not
started). Times are formatted server-side (`_start_clock`, project `TIME_ZONE`) and `server_time_ms` is the page's
"now", so the browser's clock and time zone never matter. All counting and drawing is in `starts.js`.
`RaceFinishesView`/`RaceFinishesDataView`/`RaceFinishesPrintView` back its twin «Финиш» (`race/<slug>/finishes/`,
same gate and team set) plus an A4 sheet for the kitchen (`…/finishes/print/`, standalone, no JS). A team's КВ is
`start_time + Category.control_time`. Unlike «Старты», the server computes states and the hourly arrival timeline in
the pure module `src/apps/race/finish_forecast.py` (model and constants documented there). Invariants: forecast
buckets are merged into timeline rows **by position**, never by `HH:MM` label (labels repeat across midnight);
`arrived` counts every finish, no-КВ categories too; the scale reaches back at most `TIMELINE_BACK` (a mistyped
`finish_time` must not explode it); the print sheet's `?category=` takes that category's rows of the **race-wide**
timeline so paper matches the filtered page; whole people are rounded half-up (`_half_up`) to agree with JS
`Math.round`. The JS poll code is duplicated from `starts.js` on purpose.
`RaceMapView`/`RaceMapPositionsView`/`RaceMapTrackView` (`src/apps/race/views.py`) back the organizer-only «Карта
гонки» page — the read side of `apps.mobile`'s `/app/race/<id>/track/` upload (`TrackPoint` rows were write-only
until this). All three share the same `_load_and_authorize` gate as `RaceLegendEditView` (anon → `login` redirect
with `?next=`, non-admin → 403). URL names: `race_map` (`race/<slug>/map/`, template `src/templates/race/map.html`,
extends `website/base-2.html`, assets `src/static/css/race_map.css` + `src/static/js/race_map.js` — resolves only
the two JSON endpoint URLs into a `#raceMapConfig` island, `trackUrlTemplate` built via `reverse()` with a
`team_id=0` placeholder string-replaced since `reverse()` can't leave a template placeholder); `race_map_positions`
(`race/<slug>/map/positions/`, JSON list, one row per **every** team of the race — a team with no `TrackPoint` rows
gets `lat`/`lon`/`gps_time_ms`/`received_at`/`install_id`/`segment_id` all `null` so the JS sidebar can group it
under «не шлют трек»); `race_map_track` (`race/<slug>/map/track/<int:team_id>/`, JSON `{"segments": [{"install_id",
"segment_id", "points": [[lat, lon], …]}, …], "devices": [{"install_id", "index", "platform", "first_gps_time_ms",
"last_gps_time_ms", "points"}, …]}`, 404 if the team isn't in the race; `devices` is always present, one entry per
`install_id` (an empty `install_id` is its own device), sorted by first fix (tie-break `install_id`), 1-based
`index`, `points` = raw pre-thinning count, `platform` from one `AppInstall` query, `""` when there is no row);
`race_map_gpx` (`race/<slug>/map/track/<int:team_id>/gpx/?include=track|marks|both`, default `both`, other → 400;
`RaceMapGpxView`, same gate, 404 for a team outside the race) — GPX 1.1 download: the **unthinned** track, one
`<trkseg>` per `(install_id, segment_id)` session, and/or the team's located `Mark`s as `<wpt>` named by the КП
`number` (`?` for an unknown `checkpoint_id`); the page shows a «GPX» link on each selected team row, content picked
by the `#rmGpxInclude` select, URL from the `gpxUrlTemplate` config key.
`race_map_gpx_all` (`…/map/gpx/all/`, `RaceMapGpxZipView`) — ZIP of every team's GPX in per-category folders:
each phone's track runs through `track_filter.py` (a port of the apps' `trackLines` — keep in step; divergences: strict `accuracy < 500`, `MAX_SPEED_MPS = 6` for foot races), then is
trimmed to `start_time − 10 min … finish_time + 10 min` (`0` = open); time is `trusted_ms or gps_time_ms`,
unlike `race_map_gpx`.
Positions uses `DISTINCT ON (team_id)` ordered by `-gps_time_ms, -created_at, -id` — the extra tie-breakers make the
picked row deterministic when two phones of one team upload different points with the same `gps_time_ms` (otherwise
marker flicker across polls). Track grouping keys a "session" by the pair `(install_id, segment_id)` (not `segment_id`
alone — two phones of one team must not merge into one line), orders each session's points by
`(gps_time_ms, created_at, id)` (same tie-breaker discipline as positions), thins each session to one point per
`THIN_INTERVAL_MS = 30_000` ms of `gps_time_ms` while always keeping the session's true last point (compared by
full `(lat, lon, gps_time_ms)` identity, not timestamp alone — two fixes can share a `gps_time_ms` with different
coordinates). Each segment carries its own `install_id`/`segment_id` so the frontend maps live-poll appends to the
correct per-session polyline by key instead of assuming array position. The frontend polls positions every 20 s and
fetches a team's track only on click (multi-select, per-team polyline color cycling, live point-append on poll
while a track is selected — skipped when the polled point repeats the last-appended `(session, lat, lon)`,
`>10 min` stale markers greyed). A selected team with 2+ devices gets a checkbox submenu (sibling of the team row)
that hides/shows only that device's polylines — the team marker and the marks layer are unaffected; checkboxes are
keyed by the `devices` array index, never the raw (client-supplied) `install_id`. Device stats are those of track
load, advanced only by the fix the positions poll delivers: the poll returns one row per team (its newest fix across
all phones), so that phone gets `last_gps_time_ms = max(…)` and — only when the row's `gps_time_ms` is strictly
newer (the same row is re-delivered every poll until a new fix lands) — `points += 1`; both happen before, and
independently of, the same-coordinates polyline dedup, so a stationary phone stays fresh; while a second phone that is also
sending but never has the newest fix gets no updates and can still age into grey — the age reads "newest fix this
page has seen", and `points` undercounts batch uploads; deselect + reselect refreshes. A live poll adds a newly seen
non-empty `install_id` as a device (the empty-`install_id` device never gets live points: `sessionKey()` is `null`
for it); hidden state is a flag on the device entry and resets on deselect; keyboard focus on a device checkbox is
restored across the 20 s re-render. Leaflet 1.9.4 is vendored (no CDN) at `src/static/vendor/leaflet/` — off-limits
for edits, served by WhiteNoise like any other static asset — with OSM as the default base tile layer and
OpenTopoMap as a switchable second layer. `TrackPoint` (`apps.mobile`) gained
`Meta.indexes = [models.Index(fields=["race", "team", "-gps_time_ms"], name="mobile_tp_race_team_ts")]` (migration
in `apps.mobile`) to serve both the positions `DISTINCT ON` query and the per-team track scan; the model is still
immutable and still intentionally absent from `apps.mobile`'s `versioning.py` — the index doesn't change that.

## Payments, refunds, pricing (shared with `website`)

**Payments**: models exist for four providers — VTB (OAuth), Yandex Wallet, Sberbank (phone transfer), SBP — each in
`website/models/` (`VTBPayment`, `PaymentsYa`, etc.). Credentials come from env vars — see `deploy/kolco24.env.example`.
The live flow is VTB `sbp2`, confirmed automatically by the `check_vtb_payments` polling command, whose
`_settle_race_payment`/`_credit_extras` are now thin wrappers over `apps/race/settlement.py`
(`settle_payment`/`credit_extras` — shared with the zero-charge promo path). The legacy manual-verification stack (the admin `/payments/` confirm/cancel page, the
user-facing sberbank/sbp "I paid, here is my card" templates, and the older Yandex API stubs `new_payment`/
`paymentinfo`/`getcost`/`yandexinform`/`success`) was **removed** — those routes now 404. The `Payment`/`PaymentLog`/
`PaymentsYa`/`SbpPaymentRecipient` tables and admin registrations are kept for history.

**Refunds** (VTB order status `REFUNDED` / `PARTIALLY_REFUNDED`) are a **journal, not a field**: every refund the bank
reports is one **`PaymentRefund`** row (`website/models/models.py`, migration `website/0095`) hanging off its `Payment`
(`related_name="refunds"`). Full and partial refunds are the same mechanism — the difference is only whether a row dries
up the payment's remaining amount.

Handled by `check_vtb_payments`, but **only on demand** — a refund appears on an order that is already `PAID`, and the
endless poll loop excludes `PAID` (and `REFUNDED`/`PARTIALLY_REFUNDED`) rows forever. Run
`manage.py check_vtb_payments --order-id ORDER_<ulid> [--order-id …]` after making a refund: it checks those orders once,
whatever their local status, and exits. `_refund_entries(payload)` walks `transactions.refunds[]` and hands **each
confirmed entry** (`RECONCILED`/`REFUNDED`/`COMPLETED`; an unconfirmed one is skipped entirely — it may still fall
through, and a row is already a money movement) to `apps/race/settlement.py:record_refund` on its own. The **order's
status label decides nothing**.

**Idempotency is the bank's own key**: `PaymentRefund.vtb_refund_id` (`refundId`) is `unique`, and `get_or_create` on it
is the single arbiter — re-polling an order, a different order of `refunds[]`, or two commands racing all change
nothing; a known row only has its `status` refreshed. **Invariant**: total refunds (summed over the rows) can never
exceed `payment_amount`,
and money moves only from a `done` payment. A row beyond that is still **recorded with `amount = 0`** (audit trace) but
debits nothing — needed literally, because a real `refundId` arriving for an order rolled back before this table existed
would otherwise debit the team a second time, and an order paid and refunded between two polls (its `Payment` still
`draft`) would take back what was never credited.

**Seats** (`PaymentRefund.people`, stored, not derived — so nothing has to guess later what earlier rows took):
- a **closing** row (one that brings the total up to `payment_amount`) takes `paid_for − seats already refunded`. Its
  amount need not divide by the fee: with a promo a 700 ₽ payment covers two people at a 500 ₽ fee, and a divisibility
  rule would free zero seats.
- an ordinary **partial** row takes seats only when its amount is a whole number of `cost_per_person` (±0.001). The
  payload has **no line breakdown**, so any other amount (an add-on, a hand-made sum) moves **money only** and the
  command warns on stderr for an organizer to fix the seats by hand.

A **closing** row also flips `Payment.status` `done → cancel` and calls `debit_extras` — `TeamExtra.count_paid` is
**never** touched by a non-closing refund (guessing which add-on came back would eat units the team still has).

**There is deliberately no `refunded_amount` on `Payment`**: how much came back is read off the journal itself, with one
`aggregate(Sum("amount"), Sum("people"))` that `record_refund` needs for the seats anyway — a cached copy would buy no
query and add a second source of truth to keep in step. The admin list column annotates the same sum
(`PaymentAdmin.get_queryset`) rather than reading a field. Data migration `website/0096` backfills a synthetic
`LEGACY_<payment_id>` row for every pre-existing `cancel` payment (no date, no bank id — `refunded_at` falls back to
`updated_at`): without it they would vanish from the page's «Возвращено», and a real `refundId` arriving later on the
same order would find the payment's amount untouched and debit the team a second time. It is a **separate migration**
from the schema one on purpose — a backfill that fails on data then leaves the table in place, and only the data needs
fixing.

`Race.reg_status` is deliberately **not** reopened — same no-auto-reopen rule as the `OPEN → SOLD_OUT` flip. Donations
(`SPUTNIK_*`) have no `Payment`, so they keep the old all-or-nothing path: a full refund flips `MemberDonation.is_paid`
back to `False`, a partial one is refused (a donation is paid or not). Note a `cancel` payment also frees its промокод
slot immediately (`open_draft_q` counts only drafts). On the «Платежи» page a refund is **its own row** (`finance.py:
_refund_rows`, `status="refund"`, label «Возврат») with a negative `amount`, negative `paid_for` and the **bank's own
date**, so it lands in its own day bucket; zero-amount audit rows are skipped. Every total on the page is therefore a
plain sum over the same three buckets, and **all three panels must agree**: the tiles («Поступило» over `done` +
`cancel` payments, «Возвращено» the refund rows, «Осталось» the difference), the breakdown, and the daily chart, which
buckets **every** money row (`status !== "unpaid"`) so a refund day is never missing and a net-negative day draws its
bar from `Math.abs`. In the breakdown the per-line figures (Участие, each add-on, Скидка) stay **gross over
`done` + `cancel`** — a refund cannot be split across them, the bank never says which line came back — and the refund is
subtracted as its own «Возвращено» line, so «Итого» equals the «Осталось» tile. The lines reconcile to the gross total
by construction (`fee_sum = amount + discount − extras_sum`), which is why they must all use the same bucket. A
read-only `PaymentRefundInline` shows the journal on the payment in `/admin/`.

**When the money arrived** (`VTBPayment.status_changed_at`): written **once**, by `check_vtb_payments._store_status` on
the **first** transition to `PAID`, from the confirmed entry of `transactions.payments[]` (`_payment_moment`; falls back
to the order's own `status.changedAt` when there is none). Two traps make this shape non-negotiable: the **order's**
`status.changedAt` equals its `createdAt` in VTB's payloads and never tracks the payment at all (it used to be the only
writer, via `from_vtb_payload` at order creation — so the field meant "order created" while `apps/race/finance.py:
_paid_moment` reads it as the payment date), and the **payment transaction's** `status.changedAt` is *moved by a later
refund* (in the sample order a refund rewrote it two days forward). Re-polling a `PAID` order therefore must never
rewrite the field, or a refunded payment's income would jump to the refund's day.

**VTB `order_id`s** (race-fee and donations) are random ULIDs — `ORDER_<ulid>` for race fees, `SPUTNIK_<ulid>` for
donations — minted by the single generator `VTBPayment.new_order_id(prefix)` (`website/models/vtb.py`). They are
globally unique across environments, which matters because dev and prod share VTB credentials (and thus the VTB
`order_id` namespace). Reconciliation (the polling command `check_vtb_payments`) follows an explicit FK to find the
domain object — `Payment.vtb_payment` (OneToOne, `on_delete=SET_NULL`, `related_name="race_payment"`) for race fees,
`DonateRequest.payment` for donations — never by parsing the id. The legacy `ORDER_<int>` int-parse survives only as a
fallback in `_resolve_race_payment` for pre-deploy in-flight payments and can be dropped once those settle.

**Team pricing & sizes** (`website/models/race.py`): `RacePriceTier` (FK `race`, `related_name="price_tiers"`; `price`,
`active_until` inclusive `DateField`, `order`) holds the price ladder. `Race.current_price` is the single source of
truth for the charged per-person amount — it returns the earliest tier with `active_until >= today`, the last tier when
all are past, or falls back to `Race.cost` when the race has no tiers. `Race.price_tier_ladder()` returns
`[{"tier", "status"}]` (`past`/`active`/`future`) for display. When charging, set `cost_now = race.current_price` for
BOTH the people-count multiplier AND the stored `Payment.cost_per_person` — `Team.update_team` back-calculates
`paid_for = withdraw_amount / cost_per_person`, so the two must stay identical. `Team.additional_charge` is deprecated —
do not add it to the formula. Allowed team sizes come from `Category.min_people`/`max_people` (defaults 2/6), not a
hardcoded JS `switch`.

**Team add-ons / доп-услуги** (`src/apps/race/models.py`, `src/apps/race/pricing.py`): a generic per-race "extra"
mechanism — teams buy optional units (maps, transfer, breakfast, …) during add/edit, charged together with the race fee
in one VTB/SBP payment. Three relational models: `RaceExtra` (per-race catalogue: `code`/`name`/`price`/`free_per_team`/
`order`/`is_active`, `unique_together("race","code")`), `TeamExtra` (per-team desired-vs-paid: `count`/`count_paid`, FK
to `RaceExtra` `on_delete=PROTECT`), `PaymentExtra` (per-payment snapshot of the delta a payment covers: `count` +
`unit_price` price-snapshot, generalizes the old `Payment.map`). **Single quantity rule for every add-on:**
`max = ucount − free_per_team` (maps `free=2`; transfer/breakfast `free=0`). Three shared helpers replace the two
duplicated maps formulas: `compute_team_charge(team, race) -> (total, lines)` (race-fee term plus
`Σ active extras max(0, count − count_paid) × price`), `upsert_team_extras(team, cleaned_data, race)` (writes
`TeamExtra.count` from the form's `extra_<code>` fields), and `create_team_payment(request, team, race)` (builds the
`Payment` + `PaymentExtra` rows, mints the VTB order, returns the redirect — or `None` when cost is 0 so the caller
redirects to its own success URL; sets/asserts `payment_method="sbp2"` because once extras are present `payment_amount`
deliberately diverges from `paid_for × cost_per_person`, so these must never flow through the partial Yandex
`update_team` back-calc). Both `AddTeam` and `EditTeamView` call `upsert_team_extras` then `create_team_payment`. *
*Reconciliation** (`check_vtb_payments`, VTB PAID path) credits add-ons from the per-payment snapshot — loops
`payment.extras`, `get_or_create(TeamExtra)`, `count_paid += pe.count` (clamping `count` up) — inside the existing
`transaction.atomic()`; idempotency lives in the command (the `status == done` guard + the outer already-`PAID`
exclusion), **not** in `PaymentExtra` (it has no consumed flag — `Payment.status` is the idempotency token). An admin
enables a new add-on by adding a row on the race edit page (no code change, no migration). The **legacy standalone**
transfer (`race/8/transfer/`) and breakfast (`race/<slug>/breakfast/…`) registration pages + their `Transfer`/
`BreakfastRegistration` models/tables were **removed** (migration `0073_delete_transfer_breakfast`; routes now 404, no
redirects) — the `RaceExtra` `code="transfer"`/`"breakfast"` add-ons above are the supported mechanism.

**Maps are now the `code="map"` extra** (`price=200`, `free_per_team=2`), backfilled by data migration
`apps/race/migrations/0002_migrate_maps_to_extras`. The legacy `Team.map_count`/`Team.map_count_paid`, `Payment.map`,
are **deprecated but still present** — their usages are
gone but the column definitions are pending removal in a deferred follow-up migration (kept through at least
one deploy cycle so a payment created pre-deploy and confirmed post-deploy still reconciles).

**Промокоды** (`src/apps/race/models.py:RacePromo`, `src/apps/race/promo.py`): a per-race discount code on the
**participation fee only** — add-ons are always charged at full price. `RacePromo` (`code` normalized to upper case in
`save()`, `discount_type` `percent`/`fixed`, `value`, `max_uses` where `0 = unlimited`, `is_active`, `comment`, `order`,
`unique_together("race","code")`) carries **no usage counter**: `discount_for(fee)` returns an `int` clamped to `0 … fee`
(a percent floored — the JS mirror in `team-form.js` floors too, so the fee is `int()`-rounded *before* the discount).
`Payment` gained `promo` (FK, `PROTECT`) + `discount_amount` (snapshot); `payment_amount` is the money actually charged
(already net of the discount), and the legacy `payment_with_discount` keeps mirroring it. Usage is **derived from
`Payment` rows** (variant A — no denormalized counter, no `PromoUse` table): one team = one use, so the use count is the
number of **distinct teams** occupying the code — those with a `done` payment or an *open* `draft` (`promo.py:
open_draft_q`): one whose VTB order the bank has not reported `EXPIRED` (`expire_at` alone is **not** trusted — an order
paid just before it stays `CREATED` until `check_vtb_payments` catches up, and the poller settles a `PAID` order
unconditionally, so releasing early would let the slot be redeemed twice), or one with no VTB order yet younger than
`RESERVATION_TTL` (checkout in flight; older is stranded — nothing can settle it). So, **unlike** `Race.reserved_people`'s
20-min TTL, an abandoned order holds the quota until VTB expires it; a cancelled payment frees it immediately,
`STATUS_DRAFT_WITH_INFO` is deliberately not counted. `resolve_promo`
checks `not_found → inactive → already_used → limit_reached`; `occupied_team_ids` **must** keep its
`.exclude(team__isnull=True)` and `check_available` its `team.pk is None` branch — on the add flow `TeamForm.team` is an
unsaved `Team()` and Django would silently compile `filter(team=<unsaved>)` into `team_id IS NULL`.
`compute_team_charge(team, race, promo=None)` returns `(total, lines, discount)`; `create_team_payment(…, promo=None)`
re-checks the quota under `select_for_update()` on the promo row (so the last slot can't go to two teams at once) and
raises `PromoUnavailable`, which `AddTeam`/`EditTeamView` turn into a form error. A team holds **at most one payable
order per promo** (`promo.py:open_checkout`, same `open_draft_q` rule): an open order for the identical charge (amount,
`paid_for`, discount, `PaymentExtra` lines) is reused — redirect to its pay URL, no new order — and any other open order
raises `PromoUnavailable`; without this, two payable discounted orders would each settle independently. `AddTeam` also
deletes the team it
just created so a retry doesn't duplicate it. **Zero-charge path**: when a promo covers the whole fee but there are
seats/add-ons to credit, the payment is created as a **`draft`** and settled right there via
`apps/race/settlement.py:settle_payment` — creating it `done` would trip that function's own idempotency guard and
credit nothing; `create_team_payment` still returns `None` so the caller redirects to its own success URL. Settlement
itself moved out of `check_vtb_payments` into `apps/race/settlement.py` (`settle_payment`/`credit_extras`, logic
unchanged) so the command and the zero-charge path share one implementation. The code is entered on the team form
(hidden `promo_code`, validated in `TeamForm.clean_promo_code`) and checked live by `GET race/<slug>/promo/check/`
(`promo_check`, `apps/race/views.py:PromoCheckView`) — **a GET on purpose**: the check writes nothing and no JS in this
project posts, so there is no CSRF-token helper to reuse; it answers the discount *rule* (`type`/`value`), never a total,
and the JS recomputes as the team size changes. Codes are managed on the race edit page (fourth block, hidden
`promos_json` + `_validate_promo_rows`/`_reconcile_promos`, same `transaction.atomic()` as categories/tiers/extras):
`code` matches `^[A-Z0-9_-]{2,32}$`, is unique within the race and **read-only once saved**; a code referenced by any
`Payment` is deactivated instead of deleted (`PROTECT` is the backstop), exactly like add-ons. Accepted behaviours: a
team that paid with a code pays full price on a top-up (`already_used`); a deleted team keeps holding its quota;
editing `Payment.status` in `/admin/` immediately changes the code's availability (the price of the derived counter);
`paid_people`/`paid_for`/`PaymentExtra` are untouched by discounts, so results and protocols are unaffected.

**People limits** (`website/models/race.py`): two `IntegerField`s — `Race.people_limit` (cap across the whole race) and
`Category.people_limit` (cap within a category), both `default=0` where `0 = unlimited` (distinct from per-team
`Category.max_people`). Occupancy = **paid + reserved**: `Category.people_count()` / `Race.people_count()` sum
`paid_people` over non-deleted teams (`TeamManager` already excludes `is_deleted`); `Category.reserved_people()` /
`Race.reserved_people()` add *live reservations* (see **Seat reservation** below); `remaining_people()` returns `None`
when unlimited. `Category.remaining_people(exclude_team=...)` self-excludes the team being edited. The capacity **gate
lives in `TeamForm.clean()`** (reused by both `AddTeam` and `EditTeamView`, which pass `team=` +
`bypass_limits=request.user.is_superuser`; superusers skip the gate): the race-check blocks only *growth* (
`needed = new_ucount − team.paid_people`), the category-check blocks entering a full category or growing inside a full
one (a pure 2→2 move into a category with room is allowed). Auto `sold_out`: `update_team()` flips `Race.reg_status`
`OPEN → SOLD_OUT` once the race cap is reached at payment confirmation — **no auto-reopening** when slots free up (
Option B). Limits are configured on the race edit page (`RaceForm.people_limit` + per-row `Category.people_limit`).

**Seat reservation** (`website/models/race.py`): a team that *started* paying must hold its seats until the payment
confirms, otherwise a payment confirmed 5–10 min later could push the race past its cap (the old paid-basis caveat). A
*live reservation* is any `Payment` with `status="draft"` whose `created_at` is within `RESERVATION_TTL` (a module-level
`timedelta`, currently **20 min**) — `create_team_payment` already mints exactly such a draft when the user is
redirected to VTB, so no new "start reservation" code path exists. `Race.reserved_people(exclude_team=…)` /
`Category.reserved_people(exclude_team=…)` sum, **per distinct team** (a re-submit's extra drafts don't multiply the
reservation), `max(0, ucount − paid_people)` — the seats that payment will fill (full `ucount` on add, the delta on a
top-up). `remaining_people()` = `people_limit − people_count() − reserved_people()`, self-excluding the edited team's
own paid **and** reserved seats. Nothing else changed: the `TeamForm.clean()` gate, the displayed `raceRemaining`/
`data-remaining` caps, and `bypass_limits` all tighten automatically because they already route through
`remaining_people()`. `reg_status` SOLD_OUT still flips on **paid** fill only (no reservation-driven open/close churn).
Accepted caveat: **fail-safe TTL** — the 20-min window is our estimate of the VTB order's life, not the real
expiry; if a draft outlives or dies before it, the seat is held at most ~20 min (never longer, never overbooks past the
window).


## Protocol snapshot

**Protocol snapshot (results)** (`src/apps/race/models.py`, `src/apps/race/results.py`): the results page
(`/results/`, URL name `category_results`) reads a **denormalized snapshot**, never the live `Team`/`TakenKP`/
`Checkpoint` models — so a mid-race team edit or KP re-scan can't change already-published results. Two models:
`Protocol` (`status` `draft`/`final`, constants `Protocol.DRAFT`/`Protocol.FINAL`; `frozen_at`, `created_by`,
`Meta.ordering = ["-created_at"]`, FK `race → website.Race` CASCADE `related_name="protocols"`) and `ProtocolRow`
(one denormalized row per team per protocol, `related_name="rows"`, indexed on `(protocol, category_id)`).
`build_protocol(race, user)` (service, `results.py`) recomputes from live data into the current `draft` in place
(deletes+rebuilds its rows) or creates a new `draft` if the latest protocol is already `final`; it locks
`Race.objects.select_for_update()` (not the `Protocol` queryset, which can be empty on the first build) so
concurrent builds can't create two drafts. `freeze_protocol(race)` flips the latest `draft` to `final` (no-op /
returns `None` if there is none). Penalty is computed from `Category.control_time`/`overtime_penalty` (both
default `0` = no penalty) instead of the old hardcoded category-id-based control times — **fill in those
category fields before the first real `freeze`**, or the frozen protocol locks in zero penalties. Visibility
(`ProtocolView`): `can_edit_race(user, race)` users see the latest protocol of any status; everyone else sees only
the latest `final` (or a "not published yet" message). URLs (`website/urls.py`): `category_results` now serves
the snapshot (the old live-computed results page is removed); build/freeze are
POST-only at `.../results/build/` / `.../results/freeze/` (names `protocol_build`/`protocol_freeze`), gated by
`can_edit_race`. A verified NFC take with an incomplete roster counts only if a judge sets `Mark.force_counted` (button on the
app-data team page, `race_app_data_mark_count`, `can_edit_race`; the field is deliberately not in `MARK_UPDATE_FIELDS`, but a re-upload that changes the take's team/КП/`method`/`cp_code` drops it);
otherwise its КП shows as «неполные» (`ProtocolRow.nfc_incomplete_checkpoints`) to editors only.
A judge withdraws a team («снять с зачёта») on the app-data team page (`race_app_data_team_dnf`): it is stored on
`Team.dnf` + `dnf_reason`/`dnf_by`/`dnf_at` and takes effect on the next rebuild — the row (listed even without a start time) gets `place=0`, sorts last in
its category and shows «сн.» + the reason (`ProtocolRow.withdrawn` = `dnf and not place`, so older rows keep their place).
