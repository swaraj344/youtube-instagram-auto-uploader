# Decoupled Sources / YouTube / Instagram — Design

**Date:** 2026-08-17
**Status:** Approved
**Branch:** `decouple` (GitHub runs cron schedules only from the default
branch, so the live pipeline on `main` is untouched until merge)
**Supersedes:** the "channel" model from
`2026-08-16-multi-channel-design.md` (a channel bundled one Drive folder +
one YouTube channel + one IG account with shared slots)

## Goal

Manage three entity types separately, linked by reference:

1. **Sources** — Drive folders of videos, each with its own Google login.
2. **YouTube channels** — each linked to one source, with its own slots,
   lead time, metadata description, and YouTube login.
3. **Instagram accounts** — each linked to one source, with its own slots
   and metadata description (Meta token stays shared).

One source may feed any number of YouTube channels and IG accounts. Every
linked destination works through the source folder independently at its own
pace, keeping its own "already used" list — the same video may appear on
different destinations on different days. YouTube and Instagram are fully
independent: IG posts straight from Drive at its own slot times; YouTube
keeps the unlisted-then-public flow.

## 1. Config model

### `config.json` (committed, non-secret; replaces `channels.json`)

```json
{
  "sources": [
    { "id": "study-drive", "name": "Study videos" }
  ],
  "youtube": [
    {
      "id": "study-yt",
      "name": "Study Motivation",
      "source": "study-drive",
      "enabled": true,
      "timezone": "Asia/Kolkata",
      "slots": ["17:30", "21:30"],
      "upload_lead_hours": 8,
      "content_description": "study motivation short videos targeting a student audience",
      "category_id": "22"
    }
  ],
  "instagram": [
    {
      "id": "casual-ig",
      "name": "only.casual.vibes",
      "source": "study-drive",
      "enabled": true,
      "timezone": "Asia/Kolkata",
      "slots": ["17:30", "21:30"],
      "content_description": "study motivation short videos targeting a student audience"
    }
  ]
}
```

Validation rules:
- `id`: lowercase `[a-z0-9-]{1,40}`, **unique across all three lists**
  (ids key state directories and the secrets blob).
- Every destination's `source` must name an existing source id.
- `slots`: non-empty list of unique `HH:MM` strings.
- `upload_lead_hours` (YouTube only): positive integer, default 8.
- `timezone`: valid IANA name. IG entries have no lead field — Instagram
  cannot pre-stage content.

### `PIPELINE_SECRETS_JSON` (one NEW GitHub secret)

New name on purpose: the live `main` keeps reading `CHANNELS_SECRETS_JSON`
until merge, so deploying the new blob early breaks nothing. Locally the
web app maintains `secrets/pipeline_secrets.json` (gitignored).

```json
{
  "sources": {
    "study-drive": { "drive_folder_id": "…", "google_token": { "…": "" } }
  },
  "youtube": {
    "study-yt": { "google_token": { "…": "" }, "google_client_secret": null }
  },
  "instagram": {
    "casual-ig": { "ig_business_account_id": "…" }
  }
}
```

- **Sources own a Google login.** Listing, downloading, making a file
  link-shareable, and revoking that link all use the source's token — an
  IG account has no Google credential, and a YouTube channel on a
  different Google account cannot read someone else's folder.
- YouTube tokens are used only for the YouTube API. For the current setup
  the source and YouTube tokens are the same account's token, stored twice.
- Shared secrets unchanged: `META_ACCESS_TOKEN` (covers all IG accounts),
  `GROQ_API_KEY`, `TELEGRAM_*`.

## 2. Code structure

- `config.py` — rewritten: `Source`, `YouTubeDest`, `InstagramDest`
  dataclasses; `load_config()` returns a `PipelineConfig` holding all
  three lists with secrets merged; validation as above. Missing secrets
  load with `has_secrets()` False (runtime skip + one-time warning, as
  today).
- `uploader.py` — YouTube upload step: `run_upload(source, yt, go_live_at,
  slot)`; Drive reads via `source` token, YouTube upload via `yt` token.
  No Drive link-sharing here anymore.
- `publisher.py` — YouTube publish step: `run_publish(yt, now,
  force_next)`; flips due queue items public. No Instagram, no Drive
  revoke (drops both).
- `ig_poster.py` (new) — Instagram step: `run_post(source, ig, occurrence,
  slot)`; picks the IG destination's next unused source video → share link
  (source token) → Groq caption → `publish_reel` → revoke link → record.
  `due_posts(ig, slot_log, now)` returns unposted slot occurrences from the
  last 24 h (older ones lapse rather than pile up).
- `run_pipeline.py` — dispatcher loops enabled YouTube destinations
  (upload-due + publish-due, as today) and enabled IG destinations
  (post-due). CLI: `--target <id>` with `--upload-slot HH:MM|""` /
  `--force-next` (YouTube) / `--post-now` (Instagram).
- Unchanged: `instagram_uploader.py` (already takes `ig_user_id` per
  call), `metadata_generator.py`, `auth.py`, `telegram_notifier.py`,
  `check_token_expiry.py`, `utils.py`.

## 3. State

Per destination under `state/<id>/` (sources have no state):

- YouTube: `processed_log.json` (source video ids used) +
  `publish_queue.json` (unlisted uploads waiting to flip public; entries
  keep `youtube_video_id`, `drive_file_id`, `go_live_at`, `slot`,
  `published` — no more `drive_public_url` or `ig_caption`).
- Instagram: `processed_log.json` (source video ids posted) +
  `slot_log.json` — ledger keyed `"YYYY-MM-DD HH:MM"` →
  `{ "status": "posted" | "failed", "media_id": …, "failure_notified": … }`.
  A slot occurrence is due when it has passed, is less than 24 h old, and
  has no `posted` entry; failures retry every 15-min run with one Telegram
  alert.
- `secrets_warning.json` skip-marker per destination, as today.

## 4. Workflows and Telegram

- `pipeline.yml`: same two crons; `workflow_dispatch` inputs become
  `target` (id), `action` (`upload` | `publishnow` | `postnow` | blank =
  normal pass), `upload_slot`. Env gains `PIPELINE_SECRETS_JSON`, drops
  `CHANNELS_SECRETS_JSON`.
- `telegram-bot.yml`: env updated the same way.
- Telegram commands: `/status` (all sources + destinations),
  `/upload <yt-id> [HH:MM]`, `/publishnow <yt-id>`, `/postnow <ig-id>`,
  `/publish` (normal pass). Ids may be omitted when exactly one enabled
  destination of that type exists.

## 5. Web UI — three management screens

Nav becomes: Dashboard · Sources · YouTube · Instagram · Settings · Deploy.

- **Sources** (`/sources`): list (name, masked folder id, Google
  connected?, which destinations link to it); add/edit form (name, id,
  Drive folder ID, **Connect Google** button); delete blocked while any
  destination references the source.
- **YouTube** (`/youtube`): list with queue/next-slot summary; add/edit
  form (name, id, source dropdown, slots, lead hours, content description,
  category, **Connect YouTube** button); pause/resume, delete; actions
  "Upload now" (slot picker) and "Publish next now".
- **Instagram** (`/instagram`): list with last-post/next-slot summary;
  add/edit form (name, id, source dropdown, slots, content description,
  IG account picker fed by the Graph API); pause/resume, delete; action
  "Post next now".
- **Dashboard**: read-only overview of all three sections + Meta token
  health + deploy banner.
- **Settings** and the **Deploy** bar work as today; Deploy now pushes
  `config.json` + `PIPELINE_SECRETS_JSON`.
- A one-shot **"Convert legacy channel setup"** banner runs the migration
  below when `channels.json` still exists.

## 6. Migration (auto-convert, one-shot)

Converts the live single-channel setup in one step, on the branch:

1. `channels.json` + old secrets → `config.json` + new secrets file:
   source `study-drive` (folder id + google token), YouTube `study-yt`
   (token, slots 17:30/21:30, lead 8, description, category), Instagram
   `casual-ig` (IG account id, same slots, same description).
2. State: `state/study/processed_log.json` is copied to BOTH
   `state/study-yt/processed_log.json` and
   `state/casual-ig/processed_log.json` (that history genuinely went to
   both destinations). `state/study/publish_queue.json` moves to
   `state/study-yt/publish_queue.json`. `state/casual-ig/slot_log.json`
   starts empty.
3. **In-flight fix:** for every queue entry still unpublished at migration
   time, its `drive_file_id` is REMOVED from the IG copy of the processed
   log — under the old model Instagram would have posted it at go-live, so
   the new IG destination must still pick it up at its next slot instead
   of skipping it. (Its leftover public Drive link from the old flow gets
   revoked when the IG post completes.)
4. `channels.json` and `state/study/` are deleted from the repo in the
   migration commit.

## 7. Error handling

- Config validation errors: web app blocks save/Deploy inline; CI crashes
  with the existing crash notification.
- Destination with missing secrets: skipped with a one-time Telegram
  warning (existing mechanism). A source with missing secrets skips every
  destination linked to it, naming the source in the warning.
- IG post failure: retried each 15-min run while the occurrence is < 24 h
  old; one Telegram alert per occurrence.
- Deploy remains per-item best-effort with retry (as built).

## 8. Testing

Unit tests (all pure logic, no network): config validation/merge for the
three entity types; YouTube due-upload scheduling (unchanged logic,
re-pointed); IG `due_posts` (occurrence passed / 24-h lapse / posted /
failed-retries); migration converter (config mapping, state copies,
in-flight fix); web routes for the three screens; Telegram parsing with
target ids. Existing test files are updated to the new interfaces.

## 9. Rollout

1. Build on `decouple`; `main` keeps running the channel model.
2. Web app: run **Convert legacy channel setup** → **Deploy** (creates
   `PIPELINE_SECRETS_JSON`; old secret untouched).
3. Merge → watch one 15-min cycle and one slot cycle per destination.
4. Cleanup: delete `CHANNELS_SECRETS_JSON`, update the Telegram bot
   command menu (`/upload <yt-id> [HH:MM]`, `/publishnow <yt-id>`,
   `/postnow <ig-id>`).

## Out of scope

- Round-robin / consume-once distribution (each destination mirrors the
  full source by design).
- Pairing an IG post to a YouTube go-live moment.
- Multiple Meta apps/tokens; hosting the web app anywhere but localhost.
