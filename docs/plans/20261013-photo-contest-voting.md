# Photo contest: pairwise voting and results

## Overview
- Second step of the site photo contest (step 1: submissions `dcee2a8`, moderators `f99f702`): voting, results and an
  anti-fraud report.
- Voting is **pairwise** (this-or-that): the server picks which two photos a voter sees, so there is no "vote for my
  photo" link and no live counters. Pairs, ranking and the per-voter limit are **per nomination**.
- Phases run in sequence: submissions until `submission_until` → voting until `voting_until` → results shown once a
  moderator sets `is_results_published`.
- During voting photos are visible only inside pairs. After publication the results page is the gallery: photos by
  nomination with place, author, caption and wins/comparisons.
- Any logged-in user votes. Moderators get a report per voter and can void (reversibly) all votes of a user.

## Context (from discovery)
- App `src/apps/photo_contest/`: models `PhotoContest`/`PhotoNomination`/`PhotoEntry`/`PhotoContestModerator`
  (migrations `0001`, `0002`), `views.py` (`_visible_contest`, `_contest_context`, `_login_redirect` → password
  `login?next=`, `_moderated_contest` gate), `permissions.py:can_moderate`, `admin.py`, `services.py`, `tests.py`.
- Templates `src/templates/photo_contest/{contest,list,moderate}.html`; assets `src/static/css/photo_contest.css`,
  `photo_moderate.css`, `src/static/js/photo_contest.js` (contains the `<dialog class="pc-viewer">` photo viewer).
- Prior concept on the unmerged branch `feature/photo-contest` (tied to races, no nominations) — reference only, do
  not merge: plan `git show 2442edf:docs/plans/20260712-photo-contest-pairwise.md`; code in `9cba2d3`
  (`scoring.py`/`pairing.py`), `9747328` (vote page + pair endpoint, `photo_vote.js`), `ab0de7c` (report),
  `bf1c3fe`/`f0fcbf2` (stale outstanding pair handling).
- Client IP: `apps.mobile.permissions._client_ip` (`X-Real-IP` set by nginx, then the last `X-Forwarded-For` entry,
  then `REMOTE_ADDR`; may return `None`). Imported as is — a known cross-app use of a private helper, like
  `apps.mobile` importing `apps.race.permissions`.
- ⚠️ Magic links sign `TimestampSigner().sign(str(EmailVerification.pk))` with the **default salt**
  (`apps/accounts/emails.py:16`). A pair token signed the same way would be a valid magic link for the
  `EmailVerification` row with the same pk (account takeover). The pair token **must** use its own salt.
- `_login_redirect(contest)` always sets `next` to the contest page and `_moderated_contest` always to
  `photo_moderate` — both get an optional `next_url`.
- Home page: `website/views/community.py:HomeView` builds `open_photo_contests` with `submission_until__gte=now`,
  rendered by `_home_photo_contests.html` / `_home_photo_strip.html`; tests in `src/website/test_publications.py`.
  `photo_contest/list.html` puts a contest after its deadline under «Приём закрыт».
- `photo_contest.js` viewer binds to `.photo-contest .pc-thumbs .pc-thumb-link` and shows `data-status` in its meta.
- `PhotoEntry.nomination` is PROTECT: a contest or nomination with entries can't be deleted (existing test
  `test_nomination_and_contest_with_entries_are_protected`).
- No JS in the project posts with CSRF yet — the vote page sends `X-CSRFToken`, the token comes from the JSON config
  island.

## Development Approach
- **testing approach**: Regular (code first, then tests)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - tests are not optional - they are a required part of the checklist
  - write unit tests for new functions/methods
  - write unit tests for modified functions/methods
  - add new test cases for new code paths
  - update existing test cases if behavior changes
  - tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change (`uv run pytest --reuse-db src/apps/photo_contest`)
- run `make format && make lint` before every commit; work in a branch, not on `master`
- maintain backward compatibility (submission flow and moderation keep working unchanged)

## Testing Strategy
- **unit tests**: required for every task, pytest-style functions in `src/apps/photo_contest/tests.py`
- **e2e tests**: the project has no UI e2e stack; the vote page JS is checked by hand (Post-Completion)

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview
- **Issued pair = a `Comparison` row.** GET creates `Comparison(winner=NULL)` and returns a token
  `TimestampSigner(salt=PAIR_TOKEN_SALT).sign(str(pk))`, `PAIR_TOKEN_SALT = "photo_contest.comparison"` (never the
  default salt — see Context). While the newest unanswered row is younger than `PAIR_TTL` and both photos are still
  `approved`, every GET returns the **same** pair — a reload can't skip pairs until a wanted photo shows up.
- **Stale rows are kept**, not deleted: an unanswered row older than `PAIR_TTL` is "abandoned" (shown in the report);
  one with a photo no longer approved is simply passed over. A new pair is issued instead.
- **Answer = one conditional UPDATE**
  `filter(pk=…, user=…, nomination=…, winner__isnull=True, created_at__gte=now − PAIR_TTL).update(winner, answered_at,
  ip, user_agent)`; 0 rows → used, foreign or expired. The TTL is anchored on the row (a re-signed token of an old
  row must not extend it); `unsign(max_age=PAIR_TTL)` is a first cheap check.
- **Per-voter lock.** `issue_pair` and the answer run in `transaction.atomic()` with
  `User.objects.select_for_update().get(pk=user.pk)`, so two tabs/double fetches can't create two outstanding rows
  or answer past the limit.
- **Counted comparison** = answered, non-void, and both `left` and `right` currently `approved` (helper
  `counted_comparisons(nomination)`). Used by standings, pairing shows and agreement. A rejected photo drops out with
  its comparisons; restoring it brings them back.
- **Quota and repeat exclusion count every answered row of the voter, void ones included** — voiding must not hand a
  voter a fresh quota. The progress «12 из 30» shows the same number.
- **Pair selection** (`pairing.select_pair`): `approved` entries of the nomination minus the voter's own; candidates
  ordered by shows (counted comparisons), random tie-break; take the first candidate that still has an opponent not
  yet answered by this voter (either order), opponent random among those. `None` only when no unanswered pair is left.
  Excluding repeats matters: 8 photos give only 28 pairs, limit is 30.
- **No counters.** Shows, answers and wins are aggregates (hundreds of photos, thousands of comparisons). Unanswered
  rows never count as shows, so shows can't be inflated without answering.
- **Ranking** (`scoring.py`): Wilson lower bound of the win share, `z = 1.96`, computed on the fly. Pairs are drawn
  independently of strength, so the win share is a fair metric (no Elo/Bradley-Terry). Places use standard
  competition ranking (1, 1, 3).
- **Voiding**: `Comparison.is_void`, reversible, set for all of a user's comparisons in the contest. Scoring and
  pairing shows filter `is_void=False`.
- **Agreement metric** (report): share of a voter's answers where the chosen photo has the higher final Wilson score;
  pairs with equal scores are skipped. A random clicker ≈ 50%, an honest voter higher, a booster of one photo lower.
  Scores include the voter's own votes (accepted; no leave-one-out).
- **Photo deletion stays PROTECT** (decision): a photo with comparisons can't be deleted. `PhotoDeleteView` catches
  `ProtectedError` and shows a message (reachable if an admin moves `submission_until` past the voting start). Deleting
  the account of an author whose photo was compared is blocked too — accepted; the admin shows Django's protected
  objects page.

## Technical Details

**`PhotoContest`** new fields (migration `0003`):
- `voting_until` — `DateTimeField("Голосование до", null=True, blank=True)`; `null` = the contest has no voting.
- `max_comparisons_per_user` — `PositiveIntegerField("Сравнений на участника в номинации", default=30,
  validators=[MinValueValidator(1)])`.
- `is_results_published` — `BooleanField("Итоги опубликованы", default=False)`.
- `clean()`: `voting_until` must be later than `submission_until`.
- `is_voting_open`: `is_published and voting_until and submission_until < now <= voting_until`.
- `is_voting_finished`: `voting_until and now > voting_until`.
- `phase`: `"submission"` / `"voting"` / `"closed"` / `"no_voting"` (`no_voting`: submissions ended and
  `voting_until` is null — the page says «Приём завершён», never «итоги скоро»; results can't be published).

**`Comparison`** (same migration):
- `nomination` FK → `PhotoNomination` (CASCADE, `related_name="comparisons"`)
- `user` FK → `AUTH_USER_MODEL` (CASCADE, `related_name="photo_comparisons"`)
- `left`, `right` FK → `PhotoEntry` (PROTECT, `related_name="+"`); `winner` FK → `PhotoEntry` (null, PROTECT,
  `related_name="+"`)
- `is_void` (default False), `created_at` (`auto_now_add`), `answered_at` (null), `ip`
  (`GenericIPAddressField`, null), `user_agent` (`CharField(255)`, blank)
- `Meta.indexes = [Index(fields=["nomination", "user"])]`
- PROTECT on photos: see «Photo deletion stays PROTECT» above. Rejecting a photo during voting is a `status` change;
  the photo leaves pairs and the ranking together with its comparisons. Deleting a voter account cascades their rows.
- Not registered in the admin.

Module constants (`pairing.py`): `PAIR_TTL = timedelta(minutes=10)`, `PAIR_TOKEN_SALT = "photo_contest.comparison"`.

**URLs** (`src/apps/photo_contest/urls.py`):

| URL | name | view | access |
|---|---|---|---|
| `<slug>/vote/<int:nomination_id>/` | `photo_vote` | vote page | login (anon → `login?next=`) |
| `<slug>/vote/<int:nomination_id>/pair/` | `photo_pair` | GET pair / POST answer, JSON | login (anon → JSON 403) |
| `<slug>/results/` | `photo_results` | ranking + gallery | public if published (else 404); moderator always |
| `<slug>/results/publish/` | `photo_results_publish` | POST publish/hide | moderator, only after `voting_until` |
| `<slug>/report/` | `photo_report` | anti-fraud report | moderator |
| `<slug>/report/void/` | `photo_report_void` | POST void/unvoid a user | moderator |

**`photo_pair` protocol:**
- GET → `{"token", "left": {"id", "url"}, "right": {"id", "url"}, "answered", "limit"}` or
  `{"done": true, "reason": "limit" | "closed" | "no_pairs", "answered", "limit"}`. `url` is the preview. No caption:
  it may name the author.
- POST JSON `{"token", "winner_id"}` → next pair in the GET format. `400 {"detail": …}` for: malformed body
  (non-JSON, missing keys, non-integer `winner_id`), bad/expired signature, row of another user or another
  nomination, row older than `PAIR_TTL`, `winner_id` not in the pair, a photo of the pair no longer `approved`,
  already answered. Closed voting or exhausted quota on POST → the `done` payload (200), the answer not stored.
- Nomination must belong to the contest (else 404); contest must be published (else 404).

**Report row** (`report.build_report(contest, nomination=None)`): `user` (email), `date_joined`, `answered` (all
answered rows, void included), `abandoned` (unanswered rows older than `PAIR_TTL`), `ip_count` (distinct IPs),
`shared_ip_voters` (other voters seen on any of those IPs; noisy behind mobile carrier NAT — a hint, not proof),
`agreement` (percent, `None` when fewer than `AGREEMENT_MIN_ANSWERS = 10` comparable answers), `top_entry` +
`top_count` (photo chosen most often), `is_void` (has answered rows and all of them are void; `False` with none).
Sorted by agreement ascending, `None` last. Agreement uses the voter's answers whose photos are both approved, void
ones included, so a voided user stays visible.

## What Goes Where
- **Implementation Steps** (`[ ]` checkboxes): models, migration, services, views, templates, JS/CSS, tests, docs.
- **Post-Completion** (no checkboxes): manual check of the vote page on a phone, setting up the real contest.

## Implementation Steps

### Task 1: Contest voting fields and `Comparison` model

**Files:**
- Modify: `src/apps/photo_contest/models.py`
- Create: `src/apps/photo_contest/migrations/0003_voting.py`
- Modify: `src/apps/photo_contest/admin.py`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] add `voting_until`, `max_comparisons_per_user` (`MinValueValidator(1)`), `is_results_published` to
      `PhotoContest` with `clean()` and `is_voting_open` / `is_voting_finished` / `phase`
- [ ] add the `Comparison` model per Technical Details
- [ ] generate migration `0003` (`makemigrations photo_contest`), rename to `0003_voting.py`
- [ ] add the new fields to `PhotoContestAdmin` (`list_display`: `voting_until`, `is_results_published`)
- [ ] write tests: `phase`/`is_voting_open` before, during and after voting, `no_voting` with `voting_until=None`,
      unpublished contest
- [ ] write tests: `clean()` rejects `voting_until <= submission_until`; `max_comparisons_per_user=0` fails
      validation; deleting a voter account cascades their comparisons; deleting a compared photo raises
      `ProtectedError`
- [ ] run tests - must pass before task 2

### Task 2: Scoring service

**Files:**
- Create: `src/apps/photo_contest/scoring.py`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] `counted_comparisons(nomination)`: answered, `is_void=False`, `left` and `right` both `approved`
- [ ] `wilson_lower_bound(wins, n, z=1.96)`, `0.0` when `n == 0`
- [ ] `nomination_standings(nomination) -> [{"entry", "wins", "n", "score", "place"}]`: `approved` entries only (also
      those with no comparisons), over `counted_comparisons`, sorted by score desc then `n` desc then pk; places by
      standard competition ranking (1, 1, 3) on equal score
- [ ] write tests: Wilson edge cases (`n=0`, `wins=n`, same share with smaller `n` → lower score)
- [ ] write tests: standings ignore void and unanswered rows, pending/rejected entries, comparisons against a
      now-rejected photo (and count them again after restore), other nominations; ties share a place (1, 1, 3)
- [ ] run tests - must pass before task 3

### Task 3: Pairing service

**Files:**
- Create: `src/apps/photo_contest/pairing.py`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] `PAIR_TTL`, `PAIR_TOKEN_SALT`; `answered_count(nomination, user)` — all answered rows of the voter, void included
- [ ] `select_pair(nomination, user) -> (left, right) | None` per Solution Overview (walk candidates by shows, first one
      with an unanswered opponent; left/right order randomized)
- [ ] `outstanding_pair(nomination, user)`: newest unanswered row younger than `PAIR_TTL` with both photos `approved`,
      else `None` (stale rows stay in the DB)
- [ ] `issue_pair(nomination, user) -> Comparison | None`: inside `transaction.atomic()` with
      `User.objects.select_for_update().get(pk=user.pk)` — outstanding row or a new one from `select_pair`
- [ ] write tests: never the voter's own photo; no repeat of an answered pair (either order), void answers included;
      least-shown photo comes first; least-shown photo already paired with every candidate but other pairs left → a
      pair is still returned; `None` with fewer than 2 candidates or all pairs answered
- [ ] write tests: `issue_pair` reuses a fresh outstanding row without new rows; issues a new one after `PAIR_TTL` or
      when a photo of the outstanding pair was rejected, keeping the old row; unanswered rows don't count as shows
- [ ] write tests: concurrent `issue_pair` calls (threads, like `test_concurrent_uploads_respect_limit`) leave one
      outstanding row
- [ ] run tests - must pass before task 4

### Task 4: Pair endpoint (`photo_pair`)

**Files:**
- Modify: `src/apps/photo_contest/views.py`
- Modify: `src/apps/photo_contest/urls.py`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] `PhotoPairView` GET: published contest + nomination of the contest (404), anon → 403 JSON, `done/closed` when
      voting is not open, `done/limit` at quota, `issue_pair` → pair payload with
      `TimestampSigner(salt=PAIR_TOKEN_SALT)` token, or `done/no_pairs`
- [ ] `PhotoPairView` POST: parse JSON body (malformed → 400), `unsign(token, max_age=PAIR_TTL)`, then in
      `transaction.atomic()` with the per-voter lock: voting open + quota (else `done`), the 400 checks from Technical
      Details, conditional UPDATE (row-anchored TTL) with `ip=_client_ip(request)` and `user_agent[:255]`; respond with
      the next pair (same code path as GET)
- [ ] write tests: happy path (GET → POST → `answered` grows, winner stored with ip/user agent); repeated GET returns
      the same pair and creates no rows
- [ ] write tests: double POST → 400; expired token → 400; token re-signed for a row older than `PAIR_TTL` → 400;
      another user's token → 400; `winner_id` not in pair → 400; token from another nomination → 400; photo rejected
      after issue → 400 and next GET gives a new pair; malformed bodies (non-JSON, missing keys, string `winner_id`)
      → 400
- [ ] write tests: a pair token is rejected by `magic_link` and a magic-link token is rejected by `photo_pair`
- [ ] write tests: quota → `done/limit`; voided voter still at `done/limit`; concurrent POSTs on two open rows never
      exceed the limit; outside the voting window → `done/closed`; anon → 403; nomination of another contest → 404;
      POST with `enforce_csrf_checks=True` passes with `X-CSRFToken` and fails without it
- [ ] run tests - must pass before task 5

### Task 5: Vote page (`photo_vote`)

**Files:**
- Create: `src/templates/photo_contest/vote.html`
- Create: `src/static/js/photo_vote.js`
- Create: `src/static/css/photo_vote.css`
- Modify: `src/apps/photo_contest/views.py` (incl. `_login_redirect(contest, next_url=None)`)
- Modify: `src/apps/photo_contest/urls.py`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] `_login_redirect` gets an optional `next_url` (default: the contest page, current behaviour)
- [ ] `PhotoVoteView`: published contest + nomination (404), anon → `_login_redirect(contest, next_url=<vote page>)`;
      context: nomination, other nominations of the contest (links on the done screen), JSON config island
      (`pairUrl`, `contestUrl`, `csrfToken`)
- [ ] `vote.html` on `base-2.html`: two photo cards side by side (stacked on narrow screens), a «Выбрать» button under
      each, progress «12 из 30», done screens per `reason` with links to other nominations, error box with «Повторить»
- [ ] `photo_vote.js`: fetch pair, POST answer with `X-CSRFToken`, lock buttons until the response, POST 400 → re-GET,
      network/5xx → error box; keys ←/→ vote left/right (not while the large view is open)
- [ ] large view: tap on a photo opens a `<dialog>` with the two photos (arrows/swipe between them, «Это лучше»
      votes for the shown one); reuse the `pc-viewer` markup/CSS from `contest.html` if it separates cleanly, else a
      small own dialog in `photo_vote.js`
- [ ] `photo_vote.css` scoped under `.photo-vote`
- [ ] write tests: anon → `login?next=/photo/<slug>/vote/<id>/`; logged-in 200 with config island; foreign or
      missing nomination → 404; unpublished contest → 404; existing upload/delete anon redirects unchanged
- [ ] run tests - must pass before task 6

### Task 6: Contest page by phase, protected photo delete

**Files:**
- Modify: `src/apps/photo_contest/views.py` (`_contest_context`, `PhotoDeleteView`)
- Modify: `src/templates/photo_contest/contest.html`
- Modify: `src/static/css/photo_contest.css`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] `_contest_context`: `phase`; per nomination `approved_count` (one aggregate query) and, for a logged-in user,
      `answered` (one aggregate query); `can_vote = approved_count >= 2`
- [ ] template: upload form only in `submission`; in `voting` — «Голосование до …», per nomination «Голосовать» +
      «12 из 30» (anon gets the button too, it leads to login via the vote page); `closed` — «Голосование завершено,
      итоги скоро»; `no_voting` — «Приём завершён»; published results — link «Итоги»; «Мои фото» stay in every phase
- [ ] `PhotoDeleteView`: catch `ProtectedError` → error message «Фото уже участвует в голосовании, удалить нельзя»
- [ ] write tests: each phase renders the right block; «Голосовать» hidden for a nomination with < 2 approved photos;
      progress counts the user's answers in that nomination; delete of a compared photo shows the message, no 500
- [ ] write tests: `django_assert_max_num_queries` for the contest page with several nominations and answers
- [ ] run tests - must pass before task 7

### Task 7: Home page and contest list during voting

**Files:**
- Modify: `src/website/views/community.py` (`HomeView`)
- Modify: `src/templates/website/_home_photo_contests.html`, `src/templates/website/_home_photo_strip.html`
- Modify: `src/templates/photo_contest/list.html`, `src/apps/photo_contest/views.py` (`PhotoContestListView`)
- Modify: `src/website/test_publications.py`, `src/apps/photo_contest/tests.py`

- [ ] home query: published contests with `submission_until >= now` **or** `voting_until >= now`
- [ ] home partials: «Голосование до …» variant when the contest is in `voting`
- [ ] `list.html`: contests in `voting` grouped with open ones (or their own group) with a «Голосование до …» badge
- [ ] write tests: a contest in voting shows on the home page and in the list with the voting badge; a contest past
      `voting_until` (or with `voting_until=None` past submissions) doesn't show on the home page
- [ ] run tests - must pass before task 8

### Task 8: Results page and publishing

**Files:**
- Create: `src/templates/photo_contest/results.html`
- Modify: `src/apps/photo_contest/views.py` (incl. `_moderated_contest(request, slug, next_url=None)`)
- Modify: `src/apps/photo_contest/urls.py`
- Modify: `src/templates/photo_contest/contest.html` (moderator sidebar)
- Modify: `src/static/css/photo_contest.css`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] `_moderated_contest` gets an optional `next_url` (default: `photo_moderate`, current behaviour)
- [ ] `PhotoResultsView`: contest via `_visible_contest`; non-moderator and not published → 404; per nomination
      `nomination_standings`; moderator sees a «Не опубликовано» badge
- [ ] `results.html`: per nomination — place, preview, author name, caption, wins/n; no score column. Previews open the
      existing viewer: `.photo-contest` wrapper, `.pc-thumbs`/`.pc-thumb-link` markup, `<dialog class="pc-viewer">`,
      no `data-status` (the viewer must not show «Принято»)
- [ ] `PhotoResultsPublishView` (POST, `_moderated_contest` gate, `action=publish|hide`): publish only when
      `is_voting_finished`, else error message; PRG back to the contest page with `messages`
- [ ] moderator sidebar in `contest.html`: links «Итоги», «Отчёт по голосам», publish/hide button
- [ ] write tests: results 404 for anon and plain users before publication, 200 after; moderator always 200; places
      follow wins; pending/rejected photos absent; no status text in the markup
- [ ] write tests: publish by non-moderator → 403, anon → login redirect; publish before `voting_until` refused; publish
      of a `no_voting` contest refused; hide works; CSRF enforced
- [ ] run tests - must pass before task 9

### Task 9: Anti-fraud report and voiding

**Files:**
- Create: `src/apps/photo_contest/report.py`
- Create: `src/templates/photo_contest/report.html`
- Modify: `src/apps/photo_contest/views.py`
- Modify: `src/apps/photo_contest/urls.py`
- Modify: `src/static/css/photo_moderate.css`
- Modify: `src/apps/photo_contest/tests.py`

- [ ] `report.build_report(contest, nomination=None)` per Technical Details; Wilson scores from `nomination_standings`
      per nomination, a bounded number of queries (no per-voter queries)
- [ ] `PhotoReportView` (GET, `_moderated_contest(…, next_url=<report>)`): nomination filter `?nomination=<id>`
      (unknown → whole contest), table of rows
- [ ] `PhotoReportVoidView` (POST `user_id`, `action=void|unvoid`): update `is_void` on all of the user's comparisons
      in the contest; PRG back to the report keeping the filter, `messages`
- [ ] write tests: access (anon → `login?next=<report>`, plain user → 403, moderator 200, bare superuser → 403)
- [ ] write tests: agreement on a small fixture (always picks the stronger photo → 100%, the weaker → 0%, equal-score
      pairs skipped), `None` below 10 answers; `shared_ip_voters`; `abandoned` counts only rows older than
      `PAIR_TTL`; `is_void` false for a user with no answers; sort order
- [ ] write tests: void removes the user's votes from standings, unvoid restores them; the voided user keeps the
      quota used; a user's comparisons in another contest untouched
- [ ] write tests: `django_assert_max_num_queries` for the report with several voters and nominations
- [ ] run tests - must pass before task 10

### Task 10: Verify acceptance criteria
- [ ] verify all requirements from Overview are implemented
- [ ] verify edge cases: nomination with 0–1 approved photos, voter whose own photos leave < 2 candidates, all pairs
      answered before the limit, voting closed while the vote page is open, contest with `voting_until=None`, admin
      moving `submission_until` after voting started (delete shows a message)
- [ ] run full test suite: `uv run pytest`
- [ ] `make format && make lint` — clean

### Task 11: [Final] Update documentation
- [ ] add a short note to the `apps.photo_contest` paragraph in CLAUDE.md: phases, `Comparison` as the issued pair
      with a one-time token signed with **its own salt** (never the default — it would double as a magic link),
      per-nomination pairs/limit, quota counts void answers, counted comparison = both photos approved, results
      visibility (keep it minimal)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**Manual verification:**
- vote page on a phone: card layout for portrait/landscape photos, large view with swipe, «Выбрать» and «Это лучше»,
  done screens, behaviour on a slow network
- dev contest: 2 nominations, 6–10 photos each, moderate, vote from 2–3 accounts, look at the report, void one user,
  publish results

**External system updates:**
- agree with organizers on `voting_until` and `max_comparisons_per_user` for the real contest
- announce voting to participants with a link to the contest page
