"""
YouTube publish step, called by run_pipeline.py every 15 minutes.

Checks the destination's publish queue for anything whose go_live_at time
has arrived, flips it from unlisted -> public, and prunes old published
entries (keeps the file bounded). Instagram is a fully separate step now
(ig_poster.py).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

from auth import get_credentials
from config import YouTubeDest
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

def run_publish(yt: YouTubeDest, now: datetime, force_next: bool = False) -> None:
    """Flip due queue items from unlisted to public (Instagram is separate now)."""
    queue = load_json(yt.queue_file, [])
    due = select_due(queue, now, force_next=force_next)

    if not due:
        if force_next:
            notify(f"📭 [{yt.name}] Nothing in the queue to force-publish.")
        logger.info("[%s] Nothing due yet.", yt.id)
        return

    youtube = build("youtube", "v3", credentials=get_credentials(yt.google_token))

    for item in due:
        video_id = item["youtube_video_id"]
        logger.info("[%s] Setting %s public (%s)...", yt.id, video_id, item["slot"])
        set_youtube_public(youtube, video_id)
        item["published"] = True
        notify(
            f"🟢 <b>[{yt.name}] Video public</b> ({item['slot']})\n"
            f"▶️ https://youtu.be/{video_id}"
        )

    # Prune old entries and persist
    queue = prune_queue(queue)
    save_json(yt.queue_file, queue)
