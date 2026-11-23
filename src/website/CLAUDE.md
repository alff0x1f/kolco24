# website

Loaded on demand when working in this directory. Root rules are in `/CLAUDE.md`.

Payments, refunds, VTB polling (`check_vtb_payments`), pricing, add-ons, promo codes, people limits and seat reservation are documented in `src/apps/race/CLAUDE.md` — read it before touching them.

**Home page owned-teams panel**: the home page (`website/views/community.py:HomeView`, template
`src/templates/website/home.html`) shows a «Личный кабинет» section listing the signed-in user's teams across **all**
published races with `date_end >= today`, grouped by race. `HomeView.get` builds one base queryset `unfinished_races`
(`is_published=True`, `date_end__gte=today`, `order_by("date", "pk")` — named for the criterion, since a race running
right now counts; `RaceListView`'s separate `future_races` context key means the stricter `date > today`) —
`upcoming_races` (right column) is its first three non-hidden
(`[:3]`, the spotlight `featured_race` deliberately **included** — the sidebar duplicates the banner) while the panel gets
`unfinished_races` **whole**, since a team's race can be past the slice. `owned_teams_by_race(user, races)` makes **one** query
(`select_related("category2", "category2__race")`) and groups in Python; an anonymous user gets `[]` with **zero**
queries. Context key `owned_team_groups`, rendered by `src/templates/website/_home_owned_teams.html` (included without
`only`), styled by the `.my-teams` block in `src/static/css/community.css` (no new breakpoints — rules added inside the
existing 720/600 px media queries). `can_change` is computed once per race, carried on the group dict, and mirrored into each team dict (the template and
tests read it there) via `apps.race.permissions.is_team_editing_open`. The module-local `_team_display_name(team, user)` takes the owner
**explicitly** (unlike `apps.race.views._team_display_name`, which reads `team.owner` and would cost +1 query per
unnamed team). This panel **deliberately duplicates** the race-page panel (`apps/race/views.py:_owned_teams`,
`src/templates/race/_owned_teams.html`, `.owned-*` in `race.css`) — there is intentionally **no shared partial or shared
CSS**: the two pages use different design vocabularies (`community.css`/`community-button` vs `race.css`/`.btn`), so
~25 lines of Python and ~40 of markup are duplicated on purpose. Ordering is race `date` → race `id` → category
`order` → `start_number` → team `id`; `Team.start_number` is a **`CharField`**, so that leg sorts lexicographically
(`"10"` before `"9"`) — **deliberate**, matching the race-page panel, and it must not be "fixed" to a numeric sort in
one panel only. There is **deliberately no empty state**: a signed-in user with no teams in any future race renders
nothing at all (the template is wrapped in `{% if owned_team_groups %}`). Home-page tests live in
`src/website/test_publications.py`, not `src/website/tests.py`; the test helper `_unfinished_races()` there calls the
view's own `community.unfinished_races(today)` so the two definitions can't drift.

**Team add/edit pages** (`add_team.html`, `edit_team.html`) are two standalone base-2 templates — the shared form body
is intentionally duplicated (no partial); only behavior/styling is shared via `src/static/css/team-form.css` (scoped
under `.team-register`) and `src/static/js/team-form.js`. The JS reads no inline template vars: both views emit a JSON
config island `<script type="application/json" id="teamFormConfig">` (`currentPrice`, `paidPeople`, `isEdit`,
`raceRemaining`, `currentCategoryId`, `bypassLimits`, and
`extras: [{code, name, price, freePerTeam, count, countPaid}, …]`) and the JS renders one stepper per add-on (hidden
`extra_<code>` inputs, each bounded `countPaid … max(ucount − freePerTeam, countPaid)`) and computes the live доплата-aware total
mirroring the backend `compute_team_charge` (
`max(0, (ucount − paidPeople) × currentPrice + Σ active extras max(0, count − countPaid) × price)`). `team-form.js` and
`apps/race/pricing.py` carry cross-reference comments pointing at each other (the client/server-mirror rule).
`raceRemaining` is `null` for unlimited races; `currentCategoryId` is `null` on add. Category `<option>` elements carry
`data-remaining` (free slots, empty string = unlimited) and `data-current="1"` (team's own category) — `team-form.js`
uses these to disable full categories in the dropdown and cap the segmented size control; the team's own category is
never disabled. The segmented team-size control reads allowed counts from each category `<option>`'s `data-counts`
attribute (no hardcoded `switch`). `AddTeam` (`views_.py`) and `EditTeamView` (`team.py`) share context-building
helpers (`build_category_options` / `build_team_form_context` in `views_.py`); `EditTeamView` renders `edit_team.html`
and adds edit-only sections (payment history, member transfer, delete). Server-side guards (in `TeamForm.clean`) enforce
`ucount ∈ [category.min_people, category.max_people]` and, per active extra, `count ≤ max(0, ucount − free_per_team, count_paid)`
plus the edit-only `count ≥ count_paid` ("can't reduce a partly-paid add-on"). `ucount` may drop **below** `paid_people` (a
member dropped out): the overpaid seat stays on the team — no auto-refund, still counted in the race/category limits
(occupancy is `paid_people`), and paid add-on units stay allowed above the shrunken cap — the client controls only cap values in
the browser. `TeamForm.__init__` resolves the race **defensively** (a non-id/`None` `race_id` must yield
`self.extras = []` and add no fields rather than 500).

**Add-team double submit**: `add_team.html` carries a hidden one-time `submit_token` (uuid4 hex, minted on GET, kept on
an error re-render) stored in `Team.submit_token` with a partial `UniqueConstraint(owner, submit_token)` where the token
is non-empty (migration `website/0094`). `AddTeam.post` looks the token up **before** form validation (the first post's
team already holds its seats, so re-validating could fail capacity on itself) and resumes instead of creating by
redirecting to `team_checkout` (`team/<id>/checkout/`, `TeamCheckoutView`, owner or superuser); a parallel post that
loses the insert catches `IntegrityError` and resumes the same way. A missing/malformed token keeps the old
create-per-POST behaviour. `TeamCheckoutView` tells three states apart: **in flight** (`pricing.checkout_in_flight` — a
`draft` with no `vtb_payment` younger than `CHECKOUT_IN_FLIGHT_TTL = 90 s`, longer than VTB timeouts + gunicorn timeout)
→ `checkout_pending.html`, a meta-refresh page polling itself every 3 s; an open VTB order → its pay URL
(`pricing.open_pay_redirect`); otherwise (VTB failed → draft deleted, or a stranded draft older than the TTL) →
`edit_team`. In-flight is checked **first** so an older open order for a different amount is never offered. Backstop:
`create_team_payment` locks the `Team` row (`select_for_update`) and raises `CheckoutInFlight` while a checkout is in
flight — without it the edit form would mint a second payable order for the same seats (a double settle would credit
`paid_people` twice); `EditTeamView.post` checks it before saving and turns the exception into a redirect to
`team_checkout`. This precedes the promo `open_checkout` rule, so an in-flight promo checkout now yields
`CheckoutInFlight`, not `PromoUnavailable`.
`team-form.js` also locks the submit buttons after the first `submit` (unlocked on a bfcache `pageshow`, or after `SUBMIT_LOCK_MS = 40 s` so a request stopped in the browser doesn't leave
the page stuck). Related
load fixes: `VTBClient` caches the OAuth token per process (class-level, under a lock) instead of per instance, and
`src/gunicorn.conf.py` (auto-loaded from `/app`) sets workers/threads/timeout via `GUNICORN_*` env vars.
