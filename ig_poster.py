"""
Instagram posting step, called by run_pipeline.py when an IG destination's
slot occurrence has arrived.

Instagram cannot pre-stage content (media_publish goes live immediately), so
there is no lead window: at the slot occurrence we pick the destination's
next unused source video, make its Drive file link-shareable (Instagram
fetches media server-side from a public URL), generate the caption, publish
the Reel, then revoke the link.

A slot_log.json ledger (keyed "YYYY-MM-DD HH:MM") records each occurrence:
  posted   — done (skip)
  no_video — the source had nothing new at that occurrence (skip)
  failed   — retried on every 15-min run while the occurrence is <24h old
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from auth import get_credentials
from config import InstagramDest, Source
from instagram_uploader import get_reel_permalink, publish_reel
from metadata_generator import generate_metadata
from telegram_notifier import notify
from utils import load_json, save_json

logger = logging.getLogger(__name__)

_DONE_STATUSES = ("posted", "no_video")


# ---------------------------------------------------------------------------
# Drive sharing helpers (moved from uploader.py / publisher.py — Instagram is
# now the only consumer of public Drive links)
# ---------------------------------------------------------------------------

def make_shareable(drive, file_id: str) -> str:
    """Grant anyone-with-link viewer access and return a direct-download URL."""
    drive.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def revoke_drive_public_access(drive, file_id: str) -> None:
    """Remove the anyone-with-link permission from a Drive file."""
    try:
        perms = drive.permissions().list(
            fileId=file_id, fields="permissions(id, type)"
        ).execute()
        for perm in perms.get("permissions", []):
            if perm.get("type") == "anyone":
                drive.permissions().delete(
                    fileId=file_id, permissionId=perm["id"]
                ).execute()
                logger.info("  Revoked public Drive access for file %s", file_id)
                return
        logger.debug("  No public permission found for file %s", file_id)
    except Exception as exc:
        # Non-fatal: the Reel is already published.
        logger.warning("  Could not revoke Drive permission for %s: %s", file_id, exc)


# ---------------------------------------------------------------------------
# Ledger logic (pure — unit tested)
# ---------------------------------------------------------------------------

def occurrence_key(occ: datetime) -> str:
    return occ.strftime("%Y-%m-%d %H:%M")


def due_posts(ig: InstagramDest, slot_log: dict, now: datetime) -> list:
    """[(occurrence_datetime, slot), ...] for occurrences that should post now.

    The most recent occurrence of each slot is due when it has passed, is
    less than 24h old, and isn't recorded as posted/no_video. Failed
    occurrences stay due (retry) until they lapse at 24h.
    """
    tz = ZoneInfo(ig.timezone)
    due = []
    for slot in ig.slots:
        hour, minute = map(int, slot.split(":"))
        occ = now.astimezone(tz).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if occ > now:
            occ -= timedelta(days=1)
        if now - occ >= timedelta(hours=24):
            continue
        entry = slot_log.get(occurrence_key(occ)) or {}
        if entry.get("status") in _DONE_STATUSES:
            continue
        due.append((occ, slot))
    return due


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_post(source: Source, ig: InstagramDest, occurrence: datetime, slot: str) -> bool:
    """Post the IG destination's next unused source video for *occurrence*.

    Returns True on success (or recorded no_video), False on a failure that
    will be retried.
    """
    from uploader import list_drive_videos  # shared Drive listing

    key = occurrence_key(occurrence)
    slot_log = load_json(ig.slot_log_file, {})
    log = load_json(ig.log_file, {"processed_file_ids": []})

    creds = get_credentials(source.google_token)
    drive = build("drive", "v3", credentials=creds)

    videos = list_drive_videos(drive, source.drive_folder_id)
    next_video = next(
        (v for v in videos if v["id"] not in log["processed_file_ids"]), None
    )

    if not next_video:
        logger.info("[%s] No new videos in source %s.", ig.id, source.id)
        slot_log[key] = {"status": "no_video"}
        save_json(ig.slot_log_file, slot_log)
        return True

    logger.info("[%s] Posting %s for %s...", ig.id, next_video["name"], key)
    try:
        metadata = generate_metadata(next_video["name"], ig.content_description)
        public_url = make_shareable(drive, next_video["id"])
        media_id = publish_reel(
            ig.ig_business_account_id, public_url, metadata["ig_caption"]
        )
    except Exception as exc:
        logger.error("[%s] Instagram post FAILED: %s", ig.id, exc)
        entry = slot_log.get(key) or {"status": "failed"}
        entry["status"] = "failed"
        if not entry.get("failure_notified"):
            notify(
                f"🔴 <b>[{ig.name}] Instagram post failed</b> ({slot}).\n"
                f"Error: {exc}\n"
                "It will retry on the next scheduled check."
            )
            entry["failure_notified"] = True
        slot_log[key] = entry
        save_json(ig.slot_log_file, slot_log)
        return False

    revoke_drive_public_access(drive, next_video["id"])

    log["processed_file_ids"].append(next_video["id"])
    save_json(ig.log_file, log)
    slot_log[key] = {"status": "posted", "media_id": media_id, "video": next_video["name"]}
    save_json(ig.slot_log_file, slot_log)

    try:
        ig_link = get_reel_permalink(media_id)
    except Exception as exc:
        logger.warning("  Could not fetch Reel permalink: %s", exc)
        ig_link = None
    notify(
        f"🟢 <b>[{ig.name}] Reel live</b> ({slot})\n"
        f"📸 {ig_link or f'published (media {media_id})'}"
    )
    return True
