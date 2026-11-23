# apps.photo_contest

Loaded on demand when working in this directory. Root rules are in `/CLAUDE.md`.

`apps.photo_contest` — site photo contest, **not tied to races** (no FK into `website`; only `AUTH_USER_MODEL`),
mounted at `/photo/`. Step 1 is submissions only; voting/gallery/results are future work. `PhotoContest` (slug,
`submission_until`, `is_published`, `max_photos_per_user` — the limit is **per nomination**) → `PhotoNomination` →
`PhotoEntry` (`nomination` FK `PROTECT`, no contest FK; status `pending`/`approved`/`rejected`, moderated in
`/admin/` via bulk actions). Any logged-in user uploads (anon → `login?next=`); rejected entries free their
slot; the quota check runs under `select_for_update()` on the nomination (`services.py:create_entry`). Image rules
live in `images.py` (constants read at call time so tests monkeypatch them): accept ≤ 20 MB JPEG/PNG/WebP (HEIC →
format error), ≤ 50 Мп; the original is stored byte-for-byte when ≤ 15 MB, else re-encoded JPEG at full resolution
(quality steps, EXIF kept); a 1600 px EXIF-free JPEG preview is always made. `probe` reads only the header, so decode
errors in `make_original`/`make_preview` are also mapped to `ImageRejected` (field error, never a 500). **Originals
are private**: `storage.py:PrivateStorage` under `MEDIA_PRIVATE_ROOT` (`/app/media_private`, its own volume, never
mounted into nginx, which serves all of `/app/media`); it reads the setting on every access (Django builds a field's
storage once at import, so a captured path would ignore test overrides) and its `url()` raises — originals are served
only by `photo_original` to users with `photo_contest.view_photoentry` (404 otherwise). Files are laid out as
`photo_contest/<contest_id>/<nomination_id>/<uuid>.<ext>` in both roots (one folder per contest for backups; ids,
never slugs), which is why `nomination` is read-only in the admin. Files are deleted by a `post_delete` signal via
`transaction.on_commit` (a rolled-back delete keeps them). `.dockerignore` keeps `src/media_private/` (and other
local data/secrets) out of the image. **Moderators** are `PhotoContestModerator` rows (contest+user, no roles),
assigned only by a superuser in the «Администраторы конкурса» block on the contest page (`photo_contest_moderators`,
user looked up by `apps/accounts/users.py:find_user`, shared with `RaceAdminsView`). `permissions.py:can_moderate`
needs a row — a bare superuser is not a moderator, as with races. A moderator sees the unpublished contest, opens
originals of its photos and works on `photo_moderate` (`<slug>/moderate/`); restoring a rejected photo does not
re-check the per-nomination limit. The home page shows open contests via `open_photo_contests`
(`_home_photo_contests.html`).
**Voting** (`pairing.py`/`scoring.py`/`report.py`): phases `submission_until` → `voting_until` → manual
`is_results_published`; pairwise, per nomination. A `Comparison` row is the issued pair; its token is
`TimestampSigner(salt=PAIR_TOKEN_SALT)` — **never the default salt**, which magic links use over
`EmailVerification.pk`, so a pair token would log in as someone else. The answer is one conditional UPDATE
(`winner IS NULL`, row younger than `PAIR_TTL`) under a per-voter `select_for_update`. Quota and repeat
exclusion count void answers too (voiding must not refill the quota); the ranking (Wilson lower bound) counts
only non-void answers whose two photos are both approved now. A compared photo is `PROTECT`ed.
**Results**: one page per nomination (`photo_results`, `<slug>/results/<id>/`, justified grid); the contest page
shows each nomination's top-3 once published. Medals need `wins > 0` (zero-win entries tie). Grid tiles use a 600px
`thumb` made from the preview (`manage.py build_photo_thumbs [--force]` backfills); the viewer opens the preview.
