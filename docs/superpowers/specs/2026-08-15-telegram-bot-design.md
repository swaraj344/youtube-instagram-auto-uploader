# Telegram Bot Integration — Design

Date: 2026-08-15 · Status: approved by user

## Goal

Telegram notifications for pipeline events (publish success with links, failures,
video queued, Meta token expiry warnings) and remote control of the pipeline via
bot commands (`/upload A|B`, `/publish`, `/publishnow`, `/status`), with no new
hosting infrastructure.

## Architecture (approved: "lightweight poller + dispatch")

A new GitHub Actions workflow polls Telegram every 5 minutes. It answers
`/status` inline (read-only) and executes action commands by dispatching the
existing "PoddyGo Pipeline" workflow via `workflow_dispatch` — the same path as
the Actions-tab "Run workflow" button. All state mutation (queue, processed log,
git commits) stays in the main pipeline with its existing concurrency lock.

Rejected alternatives: poller executing uploads inline (duplicates state-commit
logic, risks racing the main pipeline's pushes); persistent bot process (needs a
server, contradicts the project's no-infrastructure design).

## Components

### telegram_notifier.py (new)
- `notify(text: str) -> bool` — sends HTML-formatted message via Bot API
  `sendMessage` to `TELEGRAM_CHAT_ID`.
- Missing env vars → silent no-op returning False (pipeline must run fine
  without Telegram configured). API errors → logged warning, never raised.

### telegram_commands.py (new, poller entry point)
- Fetches pending updates via `getUpdates`, then immediately acknowledges them
  (offset = last update id + 1) BEFORE processing: at-most-once semantics. A
  crashed run drops commands rather than double-executing them; the user resends
  when no confirmation reply arrives.
- Ignores every message not from `TELEGRAM_CHAT_ID` (no reply to strangers).
- Commands:
  - `/status` — inline reply: videos remaining in Drive, queued items with
    go-live times, Meta token days remaining.
  - `/upload A` / `/upload B` — dispatch pipeline with `slot` input; reply "on it".
  - `/publish` — dispatch pipeline with no inputs (runs publish check); reply.
  - `/publishnow` — dispatch pipeline with `force_next=true`; reply.
  - Anything else → help text reply.
- Workflow dispatch via GitHub REST API using the run's `GITHUB_TOKEN`
  (`permissions: actions: write`), repo from `GITHUB_REPOSITORY`.
- Exits 0 immediately when Telegram secrets are unset (workflow can merge
  before the bot account exists).

### check_token_expiry.py (new)
- Daily: `debug_token` on the Meta token (self-authorized), compute days to
  `expires_at`; if ≤ 7, `notify()` a warning. Max one message/day by cron design
  (no state file needed).

### publish_scheduled.py (changes)
- Extract `select_due(queue, now, force_next=False)` — normal mode returns all
  unpublished items whose `go_live_at` has passed; `force_next` returns the
  single earliest unpublished item regardless of time.
- New CLI flag `--force-next` implementing `/publishnow`.
- On successful publish: fetch Reel permalink (new
  `get_reel_permalink(media_id)` in instagram_uploader.py), then
  notify "🟢 Live" with YouTube + Instagram links.
- On Instagram/YouTube failure: notify with error summary (existing behavior of
  continuing/retrying unchanged).

### upload_unlisted.py (changes)
- After queueing: notify "📤 Queued for <slot time>" with AI title and unlisted
  YouTube preview link.

### Workflows
- `.github/workflows/telegram-bot.yml` (new): cron `*/5 * * * *` +
  `workflow_dispatch`; `permissions: contents: read, actions: write`; own
  concurrency group; checkout → Python 3.12 → pip install → reconstruct Google
  creds (for /status) → run poller. ~30 s/run, free on public repo.
- `.github/workflows/pipeline.yml` (changes): new `force_next` dispatch input +
  step running `publish_scheduled.py --force-next`; publish-check step condition
  updated to exclude force_next dispatches; new cron `30 3 * * *` (09:00 IST)
  + step running `check_token_expiry.py`; `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
  env added to script steps.

## Security
- Bot responds only to `TELEGRAM_CHAT_ID`; all other senders silently ignored.
- Two new secrets (GitHub + .env): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Testing
- unittest with faked network (pattern of tests/test_list_drive_videos.py):
  command parsing, chat-ID filtering, routing to fake deps, `select_due`
  time/force logic, notifier no-op + error swallowing, expiry-day math.

## User setup (only manual part)
1. @BotFather → /newbot → copy bot token, paste to Claude.
2. Send any message to the new bot (registers chat).
3. Claude extracts chat ID via getUpdates, sets secrets, verifies live.
