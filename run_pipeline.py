"""
Single entry point for the multi-channel pipeline. CI runs this every 15
minutes; slot times are pure config (channels.json), so changing them never
touches workflow YAML.

Per enabled channel, each pass:
  1. Uploads any slot whose lead window has opened (go-live minus
     upload_lead_hours) and isn't already queued for that occurrence.
  2. Publishes any queue item whose go-live time has arrived.

Manual/targeted runs (Telegram bot, web app, Actions UI):
  python run_pipeline.py --channel study --upload-slot 17:30   # queue for a slot
  python run_pipeline.py --channel study --upload-slot ""      # first slot
  python run_pipeline.py --channel study --force-next          # publish next now
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from config import Channel, load_channels  # noqa: E402
from publisher import run_publish  # noqa: E402
from telegram_notifier import notify  # noqa: E402
from uploader import run_upload  # noqa: E402
from utils import load_json, save_json  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scheduling decisions (pure — unit tested)
# ---------------------------------------------------------------------------

def next_slot_occurrence(slot: str, tz: ZoneInfo, now: datetime) -> datetime:
    """Next occurrence of *slot* ("HH:MM") in *tz*: today if still ahead, else tomorrow."""
    hour, minute = map(int, slot.split(":"))
    target = now.astimezone(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def upload_window_open(go_live: datetime, now: datetime, lead_hours: int) -> bool:
    """True while now is inside [go_live - lead_hours, go_live)."""
    return go_live - timedelta(hours=lead_hours) <= now < go_live


def already_queued(queue: list, slot: str, go_live: datetime) -> bool:
    """True if this slot occurrence is already in the queue (published or not)."""
    iso = go_live.isoformat()
    return any(
        item.get("slot") == slot and item.get("go_live_at") == iso for item in queue
    )


def due_uploads(channel: Channel, queue: list, now: datetime) -> list:
    """[(slot, go_live_datetime), ...] for every slot whose upload should run now."""
    tz = ZoneInfo(channel.timezone)
    due = []
    for slot in channel.slots:
        go_live = next_slot_occurrence(slot, tz, now)
        if upload_window_open(go_live, now, channel.upload_lead_hours) and not already_queued(
            queue, slot, go_live
        ):
            due.append((slot, go_live))
    return due


# ---------------------------------------------------------------------------
# Missing-secrets warning (once per channel, cleared when fixed)
# ---------------------------------------------------------------------------

def _warn_file(channel: Channel) -> str:
    return os.path.join(channel.state_dir, "secrets_warning.json")


def warn_missing_secrets(channel: Channel) -> None:
    os.makedirs(channel.state_dir, exist_ok=True)
    if not load_json(_warn_file(channel), {}).get("notified"):
        notify(
            f"⚠️ <b>[{channel.display_name}] Channel skipped</b> — secrets are "
            "missing or incomplete (need Drive folder, IG account, and a "
            "connected YouTube account). Fix it in the config app and deploy."
        )
        save_json(_warn_file(channel), {"notified": True})


def clear_secrets_warning(channel: Channel) -> None:
    if os.path.exists(_warn_file(channel)):
        os.remove(_warn_file(channel))


# ---------------------------------------------------------------------------
# Per-channel pass
# ---------------------------------------------------------------------------

def process_channel(channel: Channel, now: datetime) -> None:
    os.makedirs(channel.state_dir, exist_ok=True)
    queue = load_json(channel.queue_file, [])
    for slot, go_live in due_uploads(channel, queue, now):
        logger.info("[%s] Upload window open for %s (live %s)", channel.slug, slot, go_live)
        run_upload(channel, go_live, slot)
    run_publish(channel, now)


def manual_upload(channel: Channel, slot_arg: str, now: datetime) -> None:
    slot = slot_arg or channel.slots[0]
    if slot not in channel.slots:
        raise SystemExit(f"Unknown slot {slot!r} for {channel.slug} (has: {channel.slots})")
    os.makedirs(channel.state_dir, exist_ok=True)
    go_live = next_slot_occurrence(slot, ZoneInfo(channel.timezone), now)
    queue = load_json(channel.queue_file, [])
    if already_queued(queue, slot, go_live):
        notify(f"📭 [{channel.display_name}] {slot} is already queued for {go_live:%d %b}.")
        return
    run_upload(channel, go_live, slot)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-channel pipeline dispatcher.")
    parser.add_argument("--channel", default="", help="Restrict to one channel slug")
    parser.add_argument(
        "--upload-slot",
        default=None,
        help='Force an upload for this HH:MM slot ("" = channel\'s first slot). Requires --channel.',
    )
    parser.add_argument(
        "--force-next",
        action="store_true",
        help="Publish the channel's earliest queued video immediately. Requires --channel.",
    )
    args = parser.parse_args()

    if (args.upload_slot is not None or args.force_next) and not args.channel:
        parser.error("--upload-slot/--force-next require --channel")

    channels = load_channels()
    if args.channel:
        channels = [c for c in channels if c.slug == args.channel]
        if not channels:
            raise SystemExit(f"Unknown channel: {args.channel}")

    for channel in channels:
        # A targeted --channel run works even on a paused channel (explicit
        # human intent); the scheduled pass skips paused channels.
        if not channel.enabled and not args.channel:
            continue

        now = datetime.now(ZoneInfo(channel.timezone))

        if not channel.has_secrets():
            logger.warning("[%s] Missing secrets — skipping.", channel.slug)
            warn_missing_secrets(channel)
            continue
        clear_secrets_warning(channel)

        if args.upload_slot is not None:
            manual_upload(channel, args.upload_slot.strip(), now)
        elif args.force_next:
            run_publish(channel, now, force_next=True)
        else:
            process_channel(channel, now)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        notify(f"🔴 <b>Pipeline run crashed</b>: {exc}")
        raise
