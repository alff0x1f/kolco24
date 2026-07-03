# Auto-populate Team start/finish time from the mobile /marks/ stream

## Overview

When the Android app uploads a checkpoint take (взятие КП) via `POST /app/race/<id>/marks/`
(`MarkUploadView` in `src/apps/mobile/views.py`), and the take is for a КП whose
`Checkpoint.type` is `start` or `finish`, populate the team's `start_time` /
`finish_time` **if that field is still empty (`0`)**.

Problem it solves: today the boundary times are only set by the dedicated api
endpoints (`TeamStartView` / `TeamFinishView`) or manually. The mobile `/marks/`
stream already carries verified start/finish takes with trustworthy timestamps, so
the team time can be derived from that stream automatically.

Integration: purely additive logic inside the existing `MarkUploadView.post`
transaction — no new endpoint, no model change, no migration. The write is
**once-only** (never overwrites a non-zero value), so it coexists with manual admin
edits and the existing api writes.

## Context (from discovery)

- **Files/components involved:**
  - `src/apps/mobile/views.py` — `MarkUploadView.post` (current post ends ~line 553;
    `Mark`/`MarkPresent` `bulk_create` at ~540–551, inside `transaction.atomic()`).
  - `src/apps/mobile/tests.py` — pytest-style tests; helpers `_make_team_in_race`,
    `_make_cp_with_tag`, `_valid_mark`, `_signed_post`, module `SECRET`,
    autouse `_clear_throttle_cache` fixture.
- **Related patterns found:**
  - `_bids_by_checkpoint(race_id)` / `_is_verified(...)` already do a single
    per-batch tag prefetch and compute `Mark.verified` at ingest.
  - Existing time writes: `src/api/views/teams.py` sets
    `team.start_time` / `team.finish_time` and saves with
    `update_fields=["start_time"/"finish_time", "updated_at"]`.
  - `Team` (`src/website/models/models.py`): `start_time` / `finish_time`
    `BigIntegerField(default=0)`; `updated_at = auto_now` (update_fields discipline).
  - `Checkpoint.type` choices in `src/website/models/enums.py:CheckpointType`
    (`start`, `finish`, `test`, `kp`, `hidden`).
  - `Mark` (`src/apps/mobile/models.py`): `checkpoint_id` plain int, `verified` bool,
    `trusted_ms` (nullable), `wall_ms` (required), FK `team`, FK `race`.
- **Dependencies identified:** none new. Uses `django.db.models.Min` +
  `django.db.models.functions.Coalesce` and the existing `Checkpoint` import already
  present in `views.py`.

## Development Approach

- **Testing approach:** Regular (code first, then tests) — matches the existing
  `src/apps/mobile/tests.py` conventions.
- Single logical change; complete the view logic, then add the full test set before
  considering the task done.
- **Every task includes tests.** All tests must pass before the task is complete.
- Run `uv run pytest src/apps/mobile/tests.py` after the change; run
  `make format && make lint` before committing.
- Maintain backward compatibility: the write is once-only and skips entirely when the
  race has no start/finish КП, so existing behavior is unchanged for races without
  boundary КП or with already-set times.

## Testing Strategy

- **Unit/integration tests:** required, in `src/apps/mobile/tests.py`, pytest-style
  with `@pytest.mark.django_db`. Reuse `_make_team_in_race`, `_make_cp_with_tag`
  (extend it to set `type`), `_valid_mark`, `_signed_post`. The autouse
  `_clear_throttle_cache` fixture already covers throttle isolation.
- **e2e tests:** none — this is a backend-only API change with no UI surface.

## Progress Tracking

- Mark completed items `[x]` immediately.
- `➕` prefix for newly discovered tasks; `⚠️` prefix for blockers.
- Keep this file in sync with actual work.

## Solution Overview

Inside `MarkUploadView.post`, **after** the existing `Mark`/`MarkPresent`
`bulk_create` and **within the same `transaction.atomic()` block** (so the just-upserted
rows are visible to the aggregation):

1. Build `type_by_cp` = `{checkpoint_id: type}` for the race's `start`/`finish` КП
   (one query). If empty → the race has no boundary КП → skip everything.
2. Cheap guard: from the deduped batch, collect referenced `checkpoint_id`s once and
   compute `start_touched` / `finish_touched`. Skip a boundary the batch didn't touch
   (keeps the common "plain КП takes" upload at ~one extra small query, no aggregation).
3. Load the `Team` (the view currently only does `.exists()`); switch to fetching it so
   we can read/write the boundary fields.
4. For each boundary that is touched **and** whose team field is currently `0`:
   compute the **earliest** timestamp across **all stored verified marks** for that
   team + boundary КП set:
   `Mark.objects.filter(team_id, race_id, verified=True, checkpoint_id__in=<ids>)
   .aggregate(t=Min(Coalesce("trusted_ms", "wall_ms")))["t"]`.
   If `t` is not `None`, set the field.
5. If any field changed, `team.save(update_fields=[changed...] + ["updated_at"])`.

Key design decisions (settled in brainstorm):

- **Time source:** prefer `trusted_ms`, fall back to `wall_ms` (via `Coalesce`).
- **Gate:** `verified=True` only — proof of physical NFC scan. Consequence: start/finish
  КП must be provisioned with `CheckpointTag`s (same requirement as locked-КП scanning).
- **Selection:** earliest verified mark wins; computed over batch + history after the
  upsert → deterministic regardless of upload/batch order.
- **Write-once:** only write when the field is `0`; never overwrite (protects manual
  admin edits and `TeamStartView`/`TeamFinishView`).
- **Placement:** inline in the view (not a signal — `Mark` is `bulk_created`, signals
  don't fire; codebase style is explicit-logic-in-views).
- **Scope:** `Team.start_time` / `finish_time` only. No `TeamMemberRaceLog`, no
  `distance_time` (YAGNI).
- **Concurrency:** no `select_for_update` — racing batches for the same team both read
  `0` and write the *same* deterministic earliest value; harmless last-write-wins.

## Technical Details

- New imports in `src/apps/mobile/views.py`: add `Min` to the existing
  `from django.db.models import ...` line, and add
  `from django.db.models.functions import Coalesce`. `Checkpoint` **and**
  `CheckpointType` are already imported (`from website.models.enums import CheckpointType`
  at line 26) — do **not** re-import them (a duplicate import fails `make lint`).
- Use `.value` on the enum members in the `type__in` filter
  (`[CheckpointType.start.value, CheckpointType.finish.value]`) to match the file's
  convention (e.g. `views.py:290,763` use `CheckpointType.hidden.value`). Comparisons of
  the `values_list` `type` results against `CheckpointType.start`/`.finish` still work
  (TextChoices members are `str` subclasses).
- The `type_by_cp` query and the boundary aggregation must run **after** the
  `bulk_create` calls, still inside `with transaction.atomic():`.
- The batch's referenced checkpoint ids come from the already-computed `deduped` dict
  (`{m["checkpoint_id"] for m in deduped.values()}`) — no extra pass over `marks`.
- Preserve the existing early-return for the empty-`marks` case (nothing to do).
- Keep the response contract unchanged: `200 {"accepted": [all submitted ids]}`.

## What Goes Where

- **Implementation Steps** (checkboxes): view logic + tests in this repo.
- **Post-Completion** (no checkboxes): manual data note about provisioning start/finish
  КП with tags, and deploy.

## Implementation Steps

### Task 1: Auto-populate boundary times in MarkUploadView

**Files:**
- Modify: `src/apps/mobile/views.py`

- [ ] Add imports: `Min` (extend the existing `django.db.models` import) and `Coalesce`
      (`django.db.models.functions`). `Checkpoint` and `CheckpointType` are already
      imported — do not re-import.
- [ ] In `MarkUploadView.post`, after the `Mark`/`MarkPresent` `bulk_create` and inside
      the same `transaction.atomic()`, add a helper block: build
      `type_by_cp = dict(Checkpoint.objects.filter(race_id=race_id,
      type__in=[CheckpointType.start, CheckpointType.finish]).values_list("id","type"))`;
      return/skip if empty.
- [ ] Compute `batch_cp_ids = {m["checkpoint_id"] for m in deduped.values()}`;
      derive `start_ids` / `finish_ids` from `type_by_cp`; compute
      `start_touched` / `finish_touched` against `batch_cp_ids`.
- [ ] Keep the early `Team.objects.filter(...).exists()` check (it drives the empty-marks
      404 / early-return path). Add a `Team` fetch in the boundary block, then for each
      touched boundary with the team field `== 0`, aggregate
      `Min(Coalesce("trusted_ms","wall_ms"))` over verified marks for that boundary's
      cp ids and set the field; save once with
      `update_fields=[changed...] + ["updated_at"]` only if something changed.
- [ ] Verify the empty-`marks` early return and the `200 {"accepted": ...}` contract are
      unchanged.

### Task 2: Tests for boundary-time auto-population

**Files:**
- Modify: `src/apps/mobile/tests.py`

- [ ] Extend/add a helper to create a `start`/`finish`-typed КП with a `CheckpointTag`
      (like `_make_cp_with_tag` but passing `type=CheckpointType.start`/`finish`), so
      uploaded marks can be `verified=True`.
- [ ] Test: a verified `start` mark sets `Team.start_time` from `trusted_ms`; and a
      variant with `trusted_ms=None` falls back to `wall_ms`.
- [ ] Test: an **unverified** start mark (bad/blank `cp_code`, or КП with no tag) does
      **not** set `start_time` (stays `0`).
- [ ] Test: an existing **non-zero** `start_time` is preserved (no overwrite) when a
      new verified start mark arrives.
- [ ] Test: **earliest** wins — multiple verified start marks (across the batch and/or a
      second upload) leave `start_time` at the minimum `Coalesce(trusted_ms, wall_ms)`.
- [ ] Test: a batch with no start/finish marks leaves `start_time` / `finish_time`
      untouched; and a race with no start/finish КП is a no-op.
- [ ] Test: `finish_time` is set independently of `start_time` (verified finish mark
      sets finish only).
- [ ] Test: `Team.updated_at` advances when a boundary time is written (guards the
      `update_fields=[..., "updated_at"]` discipline).
- [ ] Test: team-scoping — a verified start mark for a *different* team in the same race
      does not populate this team's `start_time`.
- [ ] Run `uv run pytest src/apps/mobile/tests.py` — must pass.

### Task 3: Verify acceptance criteria

- [ ] Verify each brainstorm decision is honored (time source, verified gate,
      earliest, write-once, team-only scope, inline placement).
- [ ] Run full suite: `uv run pytest src/apps/mobile/tests.py` (and a broader
      `uv run pytest --reuse-db` if quick).
- [ ] Run `make format && make lint`.

### Task 4: Finalize

- [ ] Update `CLAUDE.md` **Marks upload** invariant to note the boundary-time
      side effect (verified start/finish marks populate `Team.start_time`/`finish_time`
      once, earliest-wins, `trusted_ms`→`wall_ms`).
- [ ] Move this plan to `docs/plans/completed/`.

## Post-Completion

*Informational — no checkboxes.*

**Manual/operational:**
- Operational note: for boundary times to populate, the race's `start`/`finish` КП must
  be provisioned with `CheckpointTag`s (so scans verify). Races without provisioned
  boundary КП will simply never auto-populate — no error, times stay `0`.
- Deploy: no migration; ship with the normal Docker build/push flow. Branch first
  (never commit to master); `make format && make lint` before committing.
