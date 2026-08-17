"""
Single entry point for the pipeline. CI runs this every 15 minutes; slot
times are pure config (config.json), so changing them never touches
workflow YAML.

Each pass, per enabled destination:
  YouTube:   upload any slot whose lead window has opened (go-live minus
             upload_lead_hours), then flip public anything whose go-live
             time has arrived.
  Instagram: post the next unused source video for any slot occurrence
             that has arrived (tracked in a per-destination ledger).

Manual/targeted runs (Telegram bot, web app, Actions UI):
  python run_pipeline.py --target study-yt --upload-slot 17:30   # queue a slot
  python run_pipeline.py --target study-yt --upload-slot ""      # first slot
  python run_pipeline.py --target study-yt --force-next          # flip public now
  python run_pipeline.py --target casual-ig --post-now           # IG post now
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from config import load_config  # noqa: E402
from telegram_notifier import notify  # noqa: E402
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


def due_uploads(dest, queue: list, now: datetime) -> list:
    """[(slot, go_live_datetime), ...] for every slot whose upload should run now."""
    tz = ZoneInfo(dest.timezone)
    due = []
    for slot in dest.slots:
        go_live = next_slot_occurrence(slot, tz, now)
        if upload_window_open(go_live, now, dest.upload_lead_hours) and not already_queued(
            queue, slot, go_live
        ):
            due.append((slot, go_live))
    return due


# ---------------------------------------------------------------------------
# Missing-secrets warning (once per destination, cleared when fixed)
# ---------------------------------------------------------------------------

def _warn_file(dest) -> str:
    return os.path.join(dest.state_dir, "secrets_warning.json")


def warn_missing_secrets(dest, detail: str) -> None:
    os.makedirs(dest.state_dir, exist_ok=True)
    if not load_json(_warn_file(dest), {}).get("notified"):
        notify(
            f"⚠️ <b>[{dest.name}] Skipped</b> — {detail} "
            "Fix it in the config app and deploy."
        )
        save_json(_warn_file(dest), {"notified": True})


def clear_secrets_warning(dest) -> None:
    if os.path.exists(_warn_file(dest)):
        os.remove(_warn_file(dest))


def _ready(cfg, dest) -> bool:
    """Check dest + its source secrets; warn-once and skip when incomplete."""
    source = cfg.sources[dest.source]
    if not source.has_secrets():
        warn_missing_secrets(
            dest, f"its source '{source.name}' is missing Drive folder or Google login."
        )
        return False
    if not dest.has_secrets():
        kind = "YouTube login" if dest.kind == "youtube" else "Instagram account id"
        warn_missing_secrets(dest, f"its {kind} is missing.")
        return False
    clear_secrets_warning(dest)
    return True


# ---------------------------------------------------------------------------
# Per-destination passes
# ---------------------------------------------------------------------------

def process_youtube(cfg, yt, now: datetime) -> None:
    from publisher import run_publish
    from uploader import run_upload

    os.makedirs(yt.state_dir, exist_ok=True)
    queue = load_json(yt.queue_file, [])
    for slot, go_live in due_uploads(yt, queue, now):
        logger.info("[%s] Upload window open for %s (live %s)", yt.id, slot, go_live)
        run_upload(cfg.sources[yt.source], yt, go_live, slot)
    run_publish(yt, now)


def process_instagram(cfg, ig, now: datetime) -> None:
    from ig_poster import due_posts, run_post

    os.makedirs(ig.state_dir, exist_ok=True)
    slot_log = load_json(ig.slot_log_file, {})
    for occurrence, slot in due_posts(ig, slot_log, now):
        logger.info("[%s] Slot %s occurrence due (%s)", ig.id, slot, occurrence)
        run_post(cfg.sources[ig.source], ig, occurrence, slot)


def manual_upload(cfg, yt, slot_arg: str, now: datetime) -> None:
    from uploader import run_upload

    slot = slot_arg or yt.slots[0]
    if slot not in yt.slots:
        raise SystemExit(f"Unknown slot {slot!r} for {yt.id} (has: {yt.slots})")
    os.makedirs(yt.state_dir, exist_ok=True)
    go_live = next_slot_occurrence(slot, ZoneInfo(yt.timezone), now)
    queue = load_json(yt.queue_file, [])
    if already_queued(queue, slot, go_live):
        notify(f"📭 [{yt.name}] {slot} is already queued for {go_live:%d %b}.")
        return
    run_upload(cfg.sources[yt.source], yt, go_live, slot)


def manual_post(cfg, ig, now: datetime) -> None:
    from ig_poster import run_post

    os.makedirs(ig.state_dir, exist_ok=True)
    # Slot key "manual" never matches a real slot occurrence, so a manual
    # post can never suppress a scheduled one.
    run_post(cfg.sources[ig.source], ig, now.replace(second=0, microsecond=0), "manual")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline dispatcher (sources / YouTube / Instagram)."
    )
    parser.add_argument("--target", default="", help="Restrict to one destination id")
    parser.add_argument(
        "--upload-slot",
        default=None,
        help='YouTube: force an upload for this HH:MM slot ("" = first slot). Requires --target.',
    )
    parser.add_argument(
        "--force-next",
        action="store_true",
        help="YouTube: publish the earliest queued video now. Requires --target.",
    )
    parser.add_argument(
        "--post-now",
        action="store_true",
        help="Instagram: post the next unused source video now. Requires --target.",
    )
    args = parser.parse_args()

    manual = args.upload_slot is not None or args.force_next or args.post_now
    if manual and not args.target:
        parser.error("--upload-slot/--force-next/--post-now require --target")

    cfg = load_config()

    if args.target:
        dest = next(
            (d for d in list(cfg.youtube) + list(cfg.instagram) if d.id == args.target),
            None,
        )
        if dest is None:
            raise SystemExit(f"Unknown target: {args.target}")
        now = datetime.now(ZoneInfo(dest.timezone))
        if not _ready(cfg, dest):
            return
        if args.post_now:
            if dest.kind != "instagram":
                raise SystemExit(f"--post-now targets an Instagram account, not {dest.id}")
            manual_post(cfg, dest, now)
        elif args.upload_slot is not None:
            if dest.kind != "youtube":
                raise SystemExit(f"--upload-slot targets a YouTube channel, not {dest.id}")
            manual_upload(cfg, dest, args.upload_slot.strip(), now)
        elif args.force_next:
            if dest.kind != "youtube":
                raise SystemExit(f"--force-next targets a YouTube channel, not {dest.id}")
            from publisher import run_publish

            run_publish(dest, now, force_next=True)
        else:
            if dest.kind == "youtube":
                process_youtube(cfg, dest, now)
            else:
                process_instagram(cfg, dest, now)
        return

    for yt in cfg.youtube:
        if not yt.enabled:
            continue
        now = datetime.now(ZoneInfo(yt.timezone))
        if _ready(cfg, yt):
            process_youtube(cfg, yt, now)

    for ig in cfg.instagram:
        if not ig.enabled:
            continue
        now = datetime.now(ZoneInfo(ig.timezone))
        if _ready(cfg, ig):
            process_instagram(cfg, ig, now)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        notify(f"🔴 <b>Pipeline run crashed</b>: {exc}")
        raise
