# Multi-Channel Support + Local Config Web App — Design

**Date:** 2026-08-16
**Status:** Approved
**Branch:** `multi-channel` (GitHub only runs cron schedules from the default
branch, so the live pipeline on `main` is untouched until merge)

## Goal

Run N independent niche channels from one repo — each channel is its own
Drive folder → its own YouTube channel → its own Instagram account, with its
own slot times and content description. Add a local web app to configure
channels visually instead of hand-editing secrets and env vars.

Channels never share videos (no cross-posting). Designed for 2–3 channels
now; the config schema supports per-channel Google Cloud projects so channel
4+ (which exceeds the ~6 uploads/day YouTube quota of one project) needs no
refactor.

## 1. Config model

The repo is public, so configuration splits into a committed non-secret layer
and a single secret blob.

### `channels.json` (committed)

```json
{
  "channels": [
    {
      "slug": "study",
      "display_name": "Study Motivation",
      "enabled": true,
      "timezone": "Asia/Kolkata",
      "slots": ["17:30", "21:30"],
      "upload_lead_hours": 8,
      "content_description": "study motivation shorts",
      "youtube_category_id": "22"
    }
  ]
}
```

- `slug`: lowercase `[a-z0-9-]+`, unique, immutable once created (it keys
  state paths and the secrets blob).
- `slots`: list of `HH:MM` go-live times in `timezone`. Replaces the fixed
  A/B slot model — a channel may have 1, 2, or more slots.
- `upload_lead_hours`: how long before go-live the unlisted YouTube upload
  runs (default 8).

### `CHANNELS_SECRETS_JSON` (one GitHub Actions secret)

JSON object keyed by slug:

```json
{
  "study": {
    "drive_folder_id": "…",
    "ig_business_account_id": "…",
    "google_token": { "…token.json contents…": "" },
    "google_client_secret": null
  }
}
```

- `google_client_secret` is optional; `null` means "use the shared
  `GOOGLE_CLIENT_SECRET_JSON` secret". Set per-channel when a channel lives
  in a different GCP project (quota scaling).
- Drive folder IDs and IG account IDs stay out of the public repo (they are
  GitHub secrets today; that doesn't regress).

Shared secrets are unchanged: `META_ACCESS_TOKEN` (one Meta app covers all
IG accounts), `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`GOOGLE_CLIENT_SECRET_JSON`.

### Local layout (web app's source of truth)

- `channels.json` — same file, edited locally, committed on Deploy.
- `secrets/channels_secrets.json` — gitignored; synced to the GitHub secret
  via `gh secret set CHANNELS_SECRETS_JSON` on Deploy.

## 2. Pipeline refactor

### `config.py` (new)

Loads both layers and exposes `Channel` dataclasses (config + secrets
merged). Secrets come from the `CHANNELS_SECRETS_JSON` env var in CI or
`secrets/channels_secrets.json` locally. Validates on load: slug format and
uniqueness, `HH:MM` slot format, unknown-slug secrets, enabled channels with
missing secrets.

### `run_pipeline.py` (new — the only CI entry point)

Runs every 15 minutes. For each enabled channel:

1. **Upload check.** For each slot, compute the next go-live occurrence. If
   `now >= go_live − upload_lead_hours` and `now < go_live` and that
   slot+date isn't already queued (tracked in state), run the upload step
   (pick next unprocessed Drive video → Groq metadata → unlisted YouTube
   upload → make Drive file shareable → append to that channel's queue).
2. **Publish check.** Publish every queue item whose `go_live_at` has
   arrived: YouTube unlisted → public, publish IG Reel, revoke Drive link,
   mark published, prune old entries.

CLI flags for targeted/manual runs: `--channel <slug>`, `--upload-slot
<HH:MM>`, `--force-next` (publish the channel's earliest queued item now).

A channel whose secrets are missing or whose auth fails is skipped with a
one-time Telegram warning (`failure_notified`-style flag in state); other
channels keep running.

### Refactored modules

`auth.py`, `instagram_uploader.py`, `metadata_generator.py`, and the upload
/ publish logic (currently `upload_unlisted.py` / `publish_scheduled.py`)
stop reading module-level env vars and take the `Channel` as a parameter.
The two step scripts become library modules called by `run_pipeline.py`
(files may be renamed `uploader.py` / `publisher.py`).

### State

Per-channel state under `state/<slug>/`:

- `state/<slug>/processed_log.json`
- `state/<slug>/publish_queue.json`

Queue entries gain nothing new except living under their channel; the
slot letter field is replaced by the slot time string. Existing root-level
`processed_log.json` / `publish_queue.json` migrate to `state/study/` in
this branch (git `mv`, plus the workflow's restore step updated).

### Telegram

- Notifications prefixed with the channel display name.
- Commands become channel-aware: `/status` shows all channels;
  `/upload <slug> [HH:MM]`, `/publishnow <slug>`. When exactly one channel
  is enabled the slug may be omitted.

## 3. Workflow changes

`pipeline.yml` shrinks to a single job:

- cron `*/15 * * * *` → `python run_pipeline.py`
- cron daily 03:30 UTC → `python check_token_expiry.py` (unchanged)
- `workflow_dispatch` inputs: `channel` (slug), `action`
  (`upload` / `publishnow`), `upload_slot` (optional `HH:MM`) — used by the
  Telegram bot and the web app.
- Env: `CHANNELS_SECRETS_JSON` + shared secrets. The per-channel
  `DRIVE_FOLDER_ID` / `IG_BUSINESS_ACCOUNT_ID` / `GOOGLE_TOKEN_JSON` secret
  wiring disappears after migration.
- Single job → single `state/` commit at the end (retry loop kept for the
  telegram-bot workflow overlap).

`telegram-bot.yml`: updated env + channel-aware command dispatch; structure
unchanged.

## 4. Web config app (`webapp/`)

Flask, server-rendered Jinja templates + vanilla JS, no build step. Binds
`127.0.0.1` only; no auth layer. Run with `python -m webapp`.

Pages:

- **Dashboard** — one card per channel: enabled/paused, next slot times,
  queue contents (unpublished items with go-live times), recent publishes,
  and token health (Meta token expiry, Google token validity). Refreshes
  repo state (`git pull --rebase`) before rendering so CI-committed state is
  current.
- **Channel form** (add/edit) — all `channels.json` fields; **Connect
  YouTube** button runs the InstalledAppFlow OAuth in the browser and stores
  the resulting token in the local secrets file; **IG account picker**
  populated live from the Graph API (`me/accounts` → pages → IG business
  accounts) using the shared Meta token; pause/enable toggle; delete (with
  confirmation; state files are left on disk).
- **Shared settings** — Meta token (with expiry check), Groq key, Telegram
  credentials, shared Google client secret.
- **Manual actions** — per-channel "Upload now" / "Publish next now"
  buttons → `gh workflow run pipeline.yml -f channel=… -f action=…`.
- **Deploy bar** — local edits are staged until **Deploy**, which shows a
  diff (channels.json changes + which slugs' secrets changed), then commits
  and pushes `channels.json` and runs `gh secret set CHANNELS_SECRETS_JSON`.
- **First-run import** — detects legacy `.env` + `token.json` + root state
  files and builds the `study` channel entry and secrets blob from them, so
  the current channel onboards with zero re-auth.

The web app shells out to `git` and `gh` (both already authenticated on this
machine) rather than embedding GitHub API clients.

## 5. Error handling

- Config validation errors: web app blocks Deploy with inline messages;
  `config.py` raises at load in CI (crash → existing crash notification).
- Missing/invalid channel secrets in CI: skip channel, notify once.
- IG publish failure: unchanged per-item retry + one-time notification.
- Deploy failures (push rejected, `gh` error): surfaced in the web UI with
  the raw error; nothing is half-applied locally (config commit and secret
  update are retried independently).

## 6. Testing

- Unit tests: slot-due scheduler (lead window edges, timezone, already-
  queued suppression), config loading/validation, legacy-state import,
  queue selection/pruning (existing tests updated to new paths/interfaces).
- Web app: route tests with Flask's test client (form validation, import
  flow); OAuth/Graph/gh interactions mocked.
- Manual verification on the branch via `workflow_dispatch` before merge.

## 7. Migration / rollout

1. All work on `multi-channel`; `main`'s crons keep running throughout.
2. Before merge: run the web app import → Deploy (creates
   `CHANNELS_SECRETS_JSON`, commits `channels.json` on the branch).
3. Merge to `main`; watch one 15-minute cycle and one full slot cycle.
4. After a clean slot cycle: delete the now-unused per-channel secrets
   (`DRIVE_FOLDER_ID`, `IG_BUSINESS_ACCOUNT_ID`, `GOOGLE_TOKEN_JSON`).

## Out of scope

- Cross-posting one video to multiple destinations.
- Hosting the web app anywhere but localhost.
- Multiple Meta apps/tokens (one Meta app covers all IG accounts).
- Auto-registering extra GCP projects (schema supports per-channel client
  secrets; creating projects stays manual).
