"""
Telegram command poller. Runs every 5 minutes via .github/workflows/telegram-bot.yml.

Reads pending bot messages, ignores anyone who isn't the configured chat, and:
  /status                  -> replies inline with every channel's state (read-only)
  /upload [channel] [HH:MM]-> dispatches an upload for that channel/slot
  /publish                 -> dispatches a publish check (publishes anything due)
  /publishnow [channel]    -> dispatches a forced publish of the next queued video
  anything else            -> help text

The channel slug may be omitted when exactly one channel is enabled.

Updates are ACKNOWLEDGED BEFORE processing (at-most-once): if a run crashes
mid-command the command is dropped, never executed twice. The confirmation
reply doubles as the delivery receipt — no reply means resend.

Exits cleanly when Telegram env vars are unset so the workflow can exist
before the bot account does.
"""

from __future__ import annotations

import logging
import os
import re

import requests
from dotenv import load_dotenv

from telegram_notifier import notify

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Commands:\n"
    "/status — every channel's queue and pipeline state\n"
    "/upload [channel] [HH:MM] — queue next video (channel optional when only one)\n"
    "/publish — publish anything that is due now\n"
    "/publishnow [channel] — publish the next queued video immediately"
)

_TIME_ARG_RE = re.compile(r"^\d{1,2}:\d{2}$")


# ---------------------------------------------------------------------------
# Pure logic (unit-tested)
# ---------------------------------------------------------------------------

def parse_command(text: str):
    """Return (command, args) for a bot command, or None for non-command text."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split()
    cmd = parts[0][1:].split("@")[0].lower()

    if cmd == "status":
        return ("status", {})
    if cmd == "publish":
        return ("publish", {})
    if cmd == "publishnow":
        channel = parts[1].lower() if len(parts) > 1 else None
        return ("publishnow", {"channel": channel})
    if cmd == "upload":
        channel = None
        slot = None
        for arg in parts[1:]:
            if _TIME_ARG_RE.match(arg):
                slot = arg
            else:
                channel = arg.lower()
        return ("upload", {"channel": channel, "slot": slot})
    return ("unknown", {})


def resolve_channel(requested, channels):
    """Map an optional requested slug to a concrete one.

    Returns (slug, None) on success or (None, error_reply) when the user
    must specify (unknown slug, or several channels and none given).
    """
    if requested:
        if requested in channels:
            return requested, None
        return None, (
            f"Unknown channel '{requested}'. "
            f"Channels: {', '.join(channels) or 'none configured'}"
        )
    if len(channels) == 1:
        return channels[0], None
    return None, f"Which channel? One of: {', '.join(channels) or 'none configured'}"


def handle_message(text: str, deps: dict) -> str:
    """Route one authorized message; return the reply text.

    deps: {"dispatch": fn(inputs: dict), "build_status": fn() -> str,
           "channels": [enabled channel slugs]}
    """
    parsed = parse_command(text)
    command, args = parsed if parsed else ("unknown", {})
    channels = deps.get("channels", [])

    if command == "status":
        return deps["build_status"]()
    if command == "upload":
        slug, err = resolve_channel(args["channel"], channels)
        if err:
            return err
        deps["dispatch"](
            {"channel": slug, "action": "upload", "upload_slot": args["slot"] or ""}
        )
        slot_note = f" for {args['slot']}" if args["slot"] else ""
        return (
            f"⏳ [{slug}] Queuing next video{slot_note} — "
            "you'll get the preview link when it's uploaded."
        )
    if command == "publish":
        deps["dispatch"]({})
        return "⏳ Running a publish check — anything due goes live now."
    if command == "publishnow":
        slug, err = resolve_channel(args["channel"], channels)
        if err:
            return err
        deps["dispatch"]({"channel": slug, "action": "publishnow"})
        return f"⏳ [{slug}] Force-publishing the next queued video."
    return HELP_TEXT


def authorized_texts(updates: list, chat_id: str):
    """Yield text messages that come from the configured chat only."""
    for update in updates:
        message = update.get("message") or {}
        if str(message.get("chat", {}).get("id")) == str(chat_id) and message.get("text"):
            yield message["text"]


# ---------------------------------------------------------------------------
# Telegram + GitHub plumbing
# ---------------------------------------------------------------------------

def _api(token: str, method: str, **params):
    resp = requests.get(
        f"https://api.telegram.org/bot{token}/{method}", params=params, timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("result", [])


def fetch_and_ack_updates(token: str) -> list:
    """Fetch pending updates and mark them consumed before returning."""
    updates = _api(token, "getUpdates", timeout=0)
    if updates:
        last_id = max(u["update_id"] for u in updates)
        _api(token, "getUpdates", offset=last_id + 1, limit=1, timeout=0)
    return updates


def dispatch_pipeline(inputs: dict) -> None:
    """Trigger the main pipeline workflow, same as the Actions 'Run workflow' button."""
    gh_token = os.environ["GH_DISPATCH_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/actions/workflows/pipeline.yml/dispatches",
        json={"ref": ref, "inputs": inputs},
        headers={
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=15,
    )
    resp.raise_for_status()


def build_status() -> str:
    """Assemble the /status reply. Each section is best-effort."""
    lines = ["📊 <b>Pipeline status</b>"]

    try:
        # Lazy imports: these need Google creds + full env, only /status uses them.
        from googleapiclient.discovery import build as gbuild

        from auth import get_credentials
        from config import load_channels
        from uploader import list_drive_videos
        from utils import load_json

        for ch in load_channels():
            if not ch.enabled:
                lines.append(f"⏸ <b>{ch.display_name}</b> — paused")
                continue
            lines.append(f"📺 <b>{ch.display_name}</b> ({ch.slug})")
            if not ch.has_secrets():
                lines.append("  ⚠️ Secrets missing — channel is being skipped")
                continue
            try:
                drive = gbuild("drive", "v3", credentials=get_credentials(ch.google_token))
                videos = list_drive_videos(drive, ch.drive_folder_id)
                processed = set(
                    load_json(ch.log_file, {"processed_file_ids": []})["processed_file_ids"]
                )
                remaining = [v for v in videos if v["id"] not in processed]
                lines.append(f"  🎬 {len(remaining)} videos left ({len(videos)} total)")

                queue = load_json(ch.queue_file, [])
                pending = [q for q in queue if not q.get("published")]
                if pending:
                    for item in pending:
                        lines.append(
                            f"  📋 {item['slot']} queued → live at {item['go_live_at']}"
                        )
                else:
                    lines.append("  📋 Nothing queued right now")
            except Exception as exc:
                lines.append(f"  ⚠️ Could not read state: {exc}")
    except Exception as exc:
        lines.append(f"⚠️ Could not load channel config: {exc}")

    try:
        from check_token_expiry import meta_token_days_left

        days = meta_token_days_left()
        if days is not None:
            lines.append(f"🔑 Meta token: {days:.0f} days left")
    except Exception as exc:
        lines.append(f"⚠️ Could not check Meta token: {exc}")

    return "\n".join(lines)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.info("Telegram not configured; nothing to do.")
        return

    updates = fetch_and_ack_updates(token)
    if not updates:
        logger.info("No pending messages.")
        return

    try:
        from config import load_channels

        enabled = [c.slug for c in load_channels() if c.enabled]
    except Exception as exc:
        logger.warning("Could not load channels: %s", exc)
        enabled = []

    deps = {
        "dispatch": dispatch_pipeline,
        "build_status": build_status,
        "channels": enabled,
    }
    for text in authorized_texts(updates, chat_id):
        logger.info("Handling command: %s", text)
        reply = handle_message(text, deps)
        notify(reply)


if __name__ == "__main__":
    main()
