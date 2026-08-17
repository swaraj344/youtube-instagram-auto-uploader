"""
Publish step of the pipeline, called by run_pipeline.py every 15 minutes.

Checks the channel's publish queue for anything whose go_live_at time has
arrived, then:

  1. Flips the YouTube video from unlisted -> public
  2. Publishes the same video to Instagram as a Reel (first time it's ever
     touched Instagram -- there's no "hold and schedule" on IG's side)
  3. Revokes the Drive file's public-link permission (no longer needed)
  4. Prunes old published entries from the queue (keeps file size bounded)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

from auth import get_credentials
from config import Channel
from instagram_uploader import get_reel_permalink, publish_reel
from telegram_notifier import notify
from utils import load_json, save_json

logger = logging.getLogger(__name__)

# Prune published entries older than this many days to keep the queue file small
QUEUE_PRUNE_DAYS = int(os.environ.get("QUEUE_PRUNE_DAYS", "30"))


# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------

def set_youtube_public(youtube, video_id: str) -> None:
    """Flip *video_id* from unlisted to public."""
    youtube.videos().update(
        part="status",
        body={"id": video_id, "status": {"privacyStatus": "public"}},
    ).execute()


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------

def revoke_drive_public_access(drive, file_id: str) -> None:
    """Remove the anyone-with-link permission from a Drive file.

    The file was made public in uploader.py so Instagram could fetch it.
    Once the Reel is published we no longer need that permission.
    """
    try:
        # List permissions to find the 'anyone' permission ID
        perms = drive.permissions().list(fileId=file_id, fields="permissions(id, type)").execute()
        for perm in perms.get("permissions", []):
            if perm.get("type") == "anyone":
                drive.permissions().delete(fileId=file_id, permissionId=perm["id"]).execute()
                logger.info("  Revoked public Drive access for file %s", file_id)
                return
        logger.debug("  No public permission found for file %s (already revoked?)", file_id)
    except Exception as exc:
        # Non-fatal: log and continue. The video is already published.
        logger.warning("  Could not revoke Drive permission for %s: %s", file_id, exc)


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------

def select_due(queue: list[dict], now: datetime, force_next: bool = False) -> list[dict]:
    """Pick the queue items to publish on this run.

    Normal mode: every unpublished item whose go_live_at has passed.
    force_next: the single earliest unpublished item, schedule ignored
    (backs the Telegram /publishnow command).
    """
    unpublished = [item for item in queue if not item.get("published")]
    if force_next:
        unpublished.sort(key=lambda item: datetime.fromisoformat(item["go_live_at"]))
        return unpublished[:1]
    return [
        item for item in unpublished
        if datetime.fromisoformat(item["go_live_at"]) <= now
    ]


def prune_queue(queue: list[dict]) -> list[dict]:
    """Remove published entries older than QUEUE_PRUNE_DAYS to keep file bounded."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=QUEUE_PRUNE_DAYS)
    before = len(queue)
    queue = [
        item for item in queue
        if not item.get("published")
        or datetime.fromisoformat(item["go_live_at"]) >= cutoff
    ]
    pruned = before - len(queue)
    if pruned:
        logger.info("Pruned %d old published entries from queue.", pruned)
    return queue


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_publish(channel: Channel, now: datetime, force_next: bool = False) -> None:
    """Publish everything due for *channel* (or force the earliest item)."""
    queue = load_json(channel.queue_file, [])
    due = select_due(queue, now, force_next=force_next)

    if not due:
        if force_next:
            notify(f"📭 [{channel.display_name}] Nothing in the queue to force-publish.")
        logger.info("[%s] Nothing due yet.", channel.slug)
        return

    creds = get_credentials(channel.google_token)
    youtube = build("youtube", "v3", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    for item in due:
        yt_id = item["youtube_video_id"]
        drive_file_id = item.get("drive_file_id")
        logger.info("[%s] Publishing %s video: %s", channel.slug, item["slot"], yt_id)

        logger.info("  Setting YouTube to public...")
        set_youtube_public(youtube, yt_id)

        logger.info("  Posting to Instagram as Reel...")
        try:
            ig_media_id = publish_reel(
                channel.ig_business_account_id,
                item["drive_public_url"],
                item["ig_caption"],
            )
            logger.info("  Instagram media ID: %s", ig_media_id)
        except Exception as exc:
            logger.error(
                "  Instagram publish FAILED (YouTube still went public): %s", exc
            )
            # Notify only on the first failure of this item — it retries on
            # every subsequent run, and a broken token would otherwise spam
            # a message every 15 minutes.
            if not item.get("failure_notified"):
                notify(
                    f"🔴 <b>[{channel.display_name}] Instagram publish failed</b> ({item['slot']}).\n"
                    f"YouTube is already public: https://youtu.be/{yt_id}\n"
                    f"Error: {exc}\n"
                    "It will retry on the next scheduled check."
                )
                item["failure_notified"] = True
            # Continue processing other items; don't mark as published so it
            # is retried on the next run.
            continue

        # Revoke Drive public access now that Instagram has fetched the video
        if drive_file_id:
            revoke_drive_public_access(drive, drive_file_id)

        item["published"] = True

        try:
            ig_link = get_reel_permalink(ig_media_id)
        except Exception as exc:
            logger.warning("  Could not fetch Reel permalink: %s", exc)
            ig_link = None
        notify(
            f"🟢 <b>[{channel.display_name}] Video live</b> ({item['slot']})\n"
            f"▶️ YouTube: https://youtu.be/{yt_id}\n"
            f"📸 Instagram: {ig_link or f'published (media {ig_media_id})'}"
        )

    # Prune old entries and persist
    queue = prune_queue(queue)
    save_json(channel.queue_file, queue)
