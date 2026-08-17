# YouTube + Instagram Auto-Uploader

![License](https://img.shields.io/github/license/Abhinavkumar-07/youtube-instagram-auto-uploader)
![Last Commit](https://img.shields.io/github/last-commit/Abhinavkumar-07/youtube-instagram-auto-uploader)
![Stars](https://img.shields.io/github/stars/Abhinavkumar-07/youtube-instagram-auto-uploader?style=social)

Automatically publishes video clips from a Google Drive folder to YouTube and
Instagram Reels on a schedule, twice a day, **completely free** — using Groq
for AI-generated metadata and GitHub Actions for cloud automation. No paid
tools, no server to maintain, no laptop that needs to stay on.

---

Videos sit in a Google Drive folder. This pipeline picks them one at a
time, generates title/description/tags/caption with Groq, and publishes to
**YouTube (unlisted → public)** and **Instagram Reels** at the same moment,
on the slots you configure. It supports **multiple sources and destinations,
decoupled** — Drive folders (sources) are managed on their own, and any
number of YouTube channels and Instagram accounts can each link to a source
with their own slot times — all managed visually with a local web app
(`python -m webapp`). Works for any video content: podcast clips, gaming
highlights, tutorials, vlogs.

## Quick start

1. **Fork this repo**
2. Get free API access: [Groq](https://console.groq.com/keys) (metadata) +
   [Google Cloud](https://console.cloud.google.com/) (Drive/YouTube) +
   [Meta Developers](https://developers.facebook.com/) (Instagram)
3. Add your credentials as **GitHub Secrets** (Settings → Secrets and
   variables → Actions) — see [Configure](#5-configure) below for the full list
4. Enable GitHub Actions on your fork (Actions tab → "I understand, enable")
5. Drop videos into your Drive folder — the pipeline picks them up automatically

Full setup walkthrough below if you want the details on each step.

## How the pipeline runs

YouTube's `publishAt` scheduling only works if the video is `private` at
upload time — it doesn't support "unlisted + auto-publish later." Instagram
has no scheduling in its API at all — calling publish makes it go live
immediately. So publishing happens in two steps, both driven by one
self-scheduling dispatcher:

| Piece | Runs | Does |
|---|---|---|
| `run_pipeline.py` | Every 15 min via GitHub Actions | Per enabled destination, decides which of the steps below are due and runs them |
| upload step (`uploader.py`) | When a YouTube slot's lead window opens (go-live minus `upload_lead_hours`, default 8h) | Picks the channel's next source video, generates metadata, uploads to YouTube as **unlisted** |
| publish step (`publisher.py`) | At the slot time | Flips the YouTube video from unlisted to **public** |
| IG step (`ig_poster.py`) | At each IG slot time | Posts the account's next source video as a **Reel** straight from Drive |

Slot times live in `config.json`, so changing them never touches workflow
YAML.

## Sources & destinations

Three entity types, managed separately in the web app and linked by id:

- **Sources** — Drive folders of videos. A source owns the Google login of
  the account that owns the folder.
- **YouTube channels** — each linked to one source; own slots, upload lead,
  content description, category, and YouTube login. Flow per slot: upload
  unlisted at (go-live − lead) → flip public at go-live.
- **Instagram accounts** — each linked to one source; own slots and content
  description (one shared `META_ACCESS_TOKEN` covers every account). Flow
  per slot: at the slot time, post the account's next unused source video
  as a Reel straight from Drive.

Every destination works through its source independently at its own pace
with its own history — the same video may appear on different destinations
on different days.

- Non-secret config: `config.json` (committed).
- Per-entity secrets: the `PIPELINE_SECRETS_JSON` GitHub secret — locally
  `secrets/pipeline_secrets.json` (gitignored).
- Shared secrets: `META_ACCESS_TOKEN`, `GROQ_API_KEY`, `TELEGRAM_*`.

Configure visually with the local web app:

```bash
pip install -r requirements-webapp.txt
python -m webapp        # opens http://127.0.0.1:5001
```

The app has a management screen per entity type (Sources / YouTube /
Instagram), runs the Google OAuth flows, picks IG accounts live from the
Graph API, and triggers manual uploads/publishes/posts. The **Deploy**
button commits `config.json` and updates the GitHub secrets (needs the
`gh` CLI logged in).

Manual CLI equivalents:

```bash
python run_pipeline.py                                 # what CI runs every 15 min
python run_pipeline.py --target study-yt --upload-slot 17:30
python run_pipeline.py --target study-yt --force-next
python run_pipeline.py --target casual-ig --post-now
```

## One-time setup

### 1. Google Cloud project (Drive + YouTube)

1. https://console.cloud.google.com/ → new project.
2. Enable **Google Drive API** and **YouTube Data API v3**.
3. OAuth consent screen: External, add yourself as a test user.
4. Create OAuth Client ID (Desktop app), download JSON as `client_secret.json` in this folder.

### 2. Meta / Instagram setup

Instagram's API requires a Business or Creator account linked to a Facebook Page.

1. Create a Facebook account if you don't have one: https://www.facebook.com/
2. Create a Facebook Page (any name/category is fine — it just needs to exist): https://www.facebook.com/pages/create
3. On Instagram: Settings → Account type → switch to **Professional account** →
   choose **Creator** or **Business** → link it to the Page you just made.
4. Go to https://developers.facebook.com/ → create an app (type: **Business**).
5. In the app, add the **Instagram Graph API** product.
6. Use the **Graph API Explorer** (developers.facebook.com/tools/explorer) to:
   - Select your app, generate a **User Access Token** with `instagram_basic`,
     `instagram_content_publish`, and `pages_show_list` permissions.
   - Exchange it for a **long-lived token** (60 days) — the Explorer has a button
     for this, or use the `/oauth/access_token` endpoint with `grant_type=fb_exchange_token`.
   - Find your **Instagram Business Account ID**: call `GET /me/accounts` to get your
     Page ID, then `GET /{page-id}?fields=instagram_business_account`.
7. Put both values in `.env` as `META_ACCESS_TOKEN` and `IG_BUSINESS_ACCOUNT_ID`.

Note: long-lived tokens expire after 60 days — you'll need to refresh it
periodically (a reminder on your calendar is easiest; a token-refresh script
can be added later if this becomes a hassle).

### 3. Python environment

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Authenticate Google (one-time, opens a browser)

```bash
python oauth_setup.py
```

### 5. Configure

Copy `.env.example` to `.env` and fill in the shared secrets
(`GROQ_API_KEY`, `META_ACCESS_TOKEN`, optional `TELEGRAM_*`). Then run the
config web app and set up a source (Drive folder + Google login) and your
destinations (YouTube channels, Instagram accounts):

```bash
pip install -r requirements-webapp.txt
python -m webapp
```

(Coming from the old channel-based setup? The dashboard offers a one-click
**Convert** that builds sources/destinations from your existing
`channels.json` and secrets.)

### 6. Run

One command does everything that's due right now (uploads whose lead window
has opened, publishes whose slot time has arrived):

```bash
python run_pipeline.py
```

Keep it running on a schedule (GitHub Actions does this for you — next
section) so queued videos actually go live.

## Automating: runs in the cloud via GitHub Actions (no laptop needed)

This pipeline runs on GitHub's servers on a schedule — your computer can be off.
Here's what's set up and why:

**1. Code lives in a private GitHub repo**, pushed with `git push`. `.gitignore`
keeps `.env`, `client_secret.json`, and `token.json` out of the repo — those are
secrets and should never be committed.

**2. Secrets are stored in GitHub → Settings → Secrets and variables → Actions**,
encrypted (the web app's **Deploy** button manages these for you):
- `PIPELINE_SECRETS_JSON` — per-entity secrets blob (sources' Drive folders
  + Google tokens, YouTube tokens, IG account ids)
- `GROQ_API_KEY`
- `META_ACCESS_TOKEN`
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (optional)

**3. `.github/workflows/pipeline.yml`** is the automation itself. On each scheduled
run, it: checks out the repo → installs dependencies → runs
`python run_pipeline.py`, which loops every enabled destination and does
whatever is due → commits the updated `state/<id>/` files back to the repo so
the next run remembers what's already been done.

**4. Two schedules** (cron times are UTC):
- Pipeline pass: `*/15 * * * *` (every 15 min — uploads AND publishes)
- Meta token expiry check: `30 3 * * *` (9:00 AM IST, Telegram warning in the final week)

**5. Manual testing**: GitHub repo → Actions tab → "PoddyGo Pipeline" → "Run workflow"
button — triggers an on-demand run without waiting for the schedule.

## Telegram bot (optional): notifications + remote control

With two extra secrets the pipeline talks to you on Telegram and takes commands:

**Notifications** — video queued (with unlisted preview link), video live (with
YouTube + Instagram links), failures, and a daily warning during the Meta
token's final week.

**Commands** (checked every 5 min by `.github/workflows/telegram-bot.yml`):

| Command | Does |
|---|---|
| `/status` | Every source and destination: videos left, queued items, last IG post; plus Meta token days remaining |
| `/upload [yt-id] [HH:MM]` | Queue the YouTube channel's next video for a slot right now (id optional when only one is enabled) |
| `/publish` | Run a normal pass — anything due goes out (all destinations) |
| `/publishnow [yt-id]` | Flip the channel's next queued video public immediately, ignoring schedule |
| `/postnow [ig-id]` | Post the IG account's next video immediately |

Setup: message @BotFather → `/newbot` → copy the token. Send your new bot any
message, then fetch your chat ID from `https://api.telegram.org/bot<TOKEN>/getUpdates`
(the `message.chat.id` field). Put both in `.env` and in two GitHub secrets:
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The bot ignores everyone except
that chat ID. Without these secrets everything runs exactly as before.

### Two expiry dates to track

- **Google refresh token expires in 7 days** unless the OAuth app is moved out of
  "Testing" mode. Fix: Google Cloud Console → your project → **Google Auth Platform
  → Audience** → **Publish app**. Do this once, early — it removes the 7-day limit.
- **Meta access token expires in ~60 days** (mid-September 2026, if generated in
  July 2026). When it expires, Instagram posting silently fails. Fix: redo the
  `fb_exchange_token` exchange (see Meta setup section above) and update the
  `META_ACCESS_TOKEN` secret on GitHub with the new token.

## Notes

- YouTube free quota: ~6 uploads/day **per Google Cloud project** (10,000
  units, ~1,600/upload) — that's roughly 3 YouTube channels × 2 slots.
  Channel 4+ needs its own GCP project: set that channel's
  `google_client_secret` in the secrets blob (the schema already supports
  it) and connect YouTube through that project. Instagram posting uses no
  YouTube quota.
- One Meta app + one `META_ACCESS_TOKEN` covers every IG account linked to
  your Facebook login — only the per-channel IG account ID differs.
- Instagram fetches the video from a Drive shareable link server-side — the
  IG step creates that link just before posting and revokes it right after.
- `state/<id>/` holds each destination's history: YouTube destinations keep
  `processed_log.json` + `publish_queue.json`; Instagram destinations keep
  `processed_log.json` + `slot_log.json` (per-slot post ledger).
- If Instagram publish fails but YouTube succeeded, the script logs it and continues —
  check the console output and retry manually if needed.
