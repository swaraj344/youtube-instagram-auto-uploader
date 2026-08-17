"""
Telegram command poller. Runs every 5 minutes via .github/workflows/telegram-bot.yml.

Reads pending bot messages, ignores anyone who isn't the configured chat, and:
  /status                 -> replies inline with every entity's state (read-only)
  /upload [yt-id] [HH:MM] -> dispatches an upload for that YouTube channel/slot
  /publish                -> dispatches a normal pass (anything due goes out)
  /publishnow [yt-id]     -> flips a channel's next queued video public now
  /postnow [ig-id]        -> posts an IG account's next video now
  anything else           -> help text

An id may be omitted when exactly one destination of that type is enabled.

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
    "/status — sources, YouTube channels, Instagram accounts\n"
    "/upload [yt-id] [HH:MM] — queue next video for a YouTube channel\n"
    "/publish — run a normal pass (anything due goes out)\n"
    "/publishnow [yt-id] — flip a channel's next queued video public now\n"
    "/postnow [ig-id] — post an IG account's next video now"
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
    if cmd in ("publishnow", "postnow"):
        target = parts[1].lower() if len(parts) > 1 else None
        return (cmd, {"target": target})
    if cmd == "upload":
        target = None
        slot = None
        for arg in parts[1:]:
            if _TIME_ARG_RE.match(arg):
                slot = arg
            else:
                target = arg.lower()
        return ("upload", {"target": target, "slot": slot})
    return ("unknown", {})


def resolve_target(requested, ids):
    """Map an optional requested id to a concrete one.

    Returns (id, None) on success or (None, error_reply) when the user
    must specify (unknown id, or several options and none given).
    """
    if requested:
        if requested in ids:
            return requested, None
        return None, (
            f"Unknown target '{requested}'. Options: {', '.join(ids) or 'none configured'}"
        )
    if len(ids) == 1:
        return ids[0], None
    return None, f"Which one? Options: {', '.join(ids) or 'none configured'}"


def handle_message(text: str, deps: dict) -> str:
    """Route one authorized message; return the reply text.

    deps: {"dispatch": fn(inputs: dict), "build_status": fn() -> str,
           "youtube": [enabled yt ids], "instagram": [enabled ig ids]}
    """
    parsed = parse_command(text)
    command, args = parsed if parsed else ("unknown", {})

    if command == "status":
        return deps["build_status"]()
    if command == "publish":
        deps["dispatch"]({})
        return "⏳ Running a pass — anything due goes out now."
    if command == "upload":
        tid, err = resolve_target(args["target"], deps.get("youtube", []))
        if err:
            return err
        deps["dispatch"](
            {"target": tid, "action": "upload", "upload_slot": args["slot"] or ""}
        )
        slot_note = f" for {args['slot']}" if args["slot"] else ""
        return f"⏳ [{tid}] Queuing next video{slot_note}."
    if command == "publishnow":
        tid, err = resolve_target(args["target"], deps.get("youtube", []))
        if err:
            return err
        deps["dispatch"]({"target": tid, "action": "publishnow"})
        return f"⏳ [{tid}] Flipping the next queued video public."
    if command == "postnow":
        tid, err = resolve_target(args["target"], deps.get("instagram", []))
        if err:
            return err
        deps["dispatch"]({"target": tid, "action": "postnow"})
        return f"⏳ [{tid}] Posting the next video to Instagram."
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
        from config import load_config
        from uploader import list_drive_videos
        from utils import load_json

        cfg = load_config()
        for yt in cfg.youtube:
            state = "" if yt.enabled else " — paused"
            lines.append(f"▶️ <b>{yt.name}</b> ({yt.id}){state}")
            source = cfg.sources[yt.source]
            if not (source.has_secrets() and yt.has_secrets()):
                lines.append("  ⚠️ Secrets missing — skipped")
                continue
            try:
                drive = gbuild("drive", "v3", credentials=get_credentials(source.google_token))
                videos = list_drive_videos(drive, source.drive_folder_id)
                processed = set(
                    load_json(yt.log_file, {"processed_file_ids": []})["processed_file_ids"]
                )
                remaining = [v for v in videos if v["id"] not in processed]
                lines.append(f"  🎬 {len(remaining)} videos left in {source.name}")

                pending = [q for q in load_json(yt.queue_file, []) if not q.get("published")]
                for item in pending:
                    lines.append(
                        f"  📋 {item['slot']} queued → public at {item['go_live_at']}"
                    )
                if not pending:
                    lines.append("  📋 Nothing queued")
            except Exception as exc:
                lines.append(f"  ⚠️ Could not read state: {exc}")
        for ig in cfg.instagram:
            state = "" if ig.enabled else " — paused"
            lines.append(f"📸 <b>{ig.name}</b> ({ig.id}){state}")
            if not (cfg.sources[ig.source].has_secrets() and ig.has_secrets()):
                lines.append("  ⚠️ Secrets missing — skipped")
                continue
            slot_log = load_json(ig.slot_log_file, {})
            posted = sorted(k for k, v in slot_log.items() if v.get("status") == "posted")
            lines.append(f"  🟢 Last posted: {posted[-1]}" if posted else "  🕒 No posts recorded yet")
    except Exception as exc:
        lines.append(f"⚠️ Could not load config: {exc}")

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
        from config import load_config

        cfg = load_config()
        yt_ids = [d.id for d in cfg.youtube if d.enabled]
        ig_ids = [d.id for d in cfg.instagram if d.enabled]
    except Exception as exc:
        logger.warning("Could not load config: %s", exc)
        yt_ids, ig_ids = [], []

    deps = {
        "dispatch": dispatch_pipeline,
        "build_status": build_status,
        "youtube": yt_ids,
        "instagram": ig_ids,
    }
    for text in authorized_texts(updates, chat_id):
        logger.info("Handling command: %s", text)
        reply = handle_message(text, deps)
        notify(reply)


if __name__ == "__main__":
    main()
