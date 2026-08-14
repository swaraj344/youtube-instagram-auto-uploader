"""
Telegram command poller. Runs every 5 minutes via .github/workflows/telegram-bot.yml.

Reads pending bot messages, ignores anyone who isn't the configured chat, and:
  /status      -> replies inline with pipeline state (read-only)
  /upload A|B  -> dispatches the main pipeline workflow with that slot
  /publish     -> dispatches a publish check (publishes anything due)
  /publishnow  -> dispatches a forced publish of the next queued video
  anything else -> help text

Updates are ACKNOWLEDGED BEFORE processing (at-most-once): if a run crashes
mid-command the command is dropped, never executed twice. The confirmation
reply doubles as the delivery receipt — no reply means resend.

Exits cleanly when Telegram env vars are unset so the workflow can exist
before the bot account does.
"""

import logging
import os
from datetime import datetime, timezone

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
    "/status — queue and pipeline state\n"
    "/upload A or /upload B — queue next video for a slot\n"
    "/publish — publish anything that is due now\n"
    "/publishnow — publish the next queued video immediately"
)

_VALID_SLOTS = ("A", "B")


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
    if cmd == "publishnow":
        return ("publishnow", {})
    if cmd == "publish":
        return ("publish", {})
    if cmd == "upload":
        if len(parts) >= 2 and parts[1].upper() in _VALID_SLOTS:
            return ("upload", {"slot": parts[1].upper()})
        return ("invalid_upload", {})
    return ("unknown", {})


def handle_message(text: str, deps: dict) -> str:
    """Route one authorized message; return the reply text.

    deps: {"dispatch": fn(inputs: dict), "build_status": fn() -> str}
    """
    parsed = parse_command(text)
    command, args = parsed if parsed else ("unknown", {})

    if command == "status":
        return deps["build_status"]()
    if command == "upload":
        deps["dispatch"]({"slot": args["slot"]})
        return (
            f"⏳ Queuing next video for slot {args['slot']} — "
            "you'll get the preview link when it's uploaded."
        )
    if command == "publish":
        deps["dispatch"]({})
        return "⏳ Running a publish check — anything due goes live now."
    if command == "publishnow":
        deps["dispatch"]({"force_next": "true"})
        return "⏳ Force-publishing the next queued video."
    if command == "invalid_upload":
        return "Usage: /upload A or /upload B"
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
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/actions/workflows/pipeline.yml/dispatches",
        json={"ref": "main", "inputs": inputs},
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
        from upload_unlisted import DRIVE_FOLDER_ID, list_drive_videos
        from utils import load_json

        drive = gbuild("drive", "v3", credentials=get_credentials())
        videos = list_drive_videos(drive, DRIVE_FOLDER_ID)
        processed = set(
            load_json("processed_log.json", {"processed_file_ids": []})["processed_file_ids"]
        )
        remaining = [v for v in videos if v["id"] not in processed]
        lines.append(f"🎬 {len(remaining)} videos left in Drive ({len(videos)} total)")

        queue = load_json("publish_queue.json", [])
        pending = [q for q in queue if not q.get("published")]
        if pending:
            for item in pending:
                lines.append(
                    f"📋 Slot {item['slot']} queued → live at {item['go_live_at']}"
                )
        else:
            lines.append("📋 Nothing queued right now")
    except Exception as exc:
        lines.append(f"⚠️ Could not read queue/Drive state: {exc}")

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

    deps = {"dispatch": dispatch_pipeline, "build_status": build_status}
    for text in authorized_texts(updates, chat_id):
        logger.info("Handling command: %s", text)
        reply = handle_message(text, deps)
        notify(reply)


if __name__ == "__main__":
    main()
