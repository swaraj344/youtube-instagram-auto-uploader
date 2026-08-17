"""
YouTube upload step, called by run_pipeline.py when a slot's lead window
opens (go-live minus upload_lead_hours).

Picks the destination's next unused video from its SOURCE's Drive folder,
generates metadata with Groq, uploads it to YouTube as UNLISTED, and queues
it in the destination's publish queue with the target go-live time.

Drive reads use the source's Google login (it owns the folder); the YouTube
upload uses the destination's login. Instagram is a fully separate step now
(ig_poster.py) — nothing here shares Drive links.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

from auth import get_credentials
from config import Source, YouTubeDest
from metadata_generator import generate_metadata
from telegram_notifier import notify
from utils import load_json, save_json

logger = logging.getLogger(__name__)

TEMP_DIR = "temp_downloads"


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------

def list_drive_videos(drive, folder_id: str) -> list[dict]:
    query = f"'{folder_id}' in parents and trashed = false"
    files: list[dict] = []
    page_token = None
    while True:
        results = (
            drive.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType)",
                orderBy="name",
                pageSize=1000,
                pageToken=page_token,
            )
            .execute()
        )
        files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return [f for f in files if f["mimeType"].startswith("video/")]


def download_video(drive, file_id: str, filename: str) -> str:
    """Download *file_id* from Drive into TEMP_DIR and return the local path."""
    os.makedirs(TEMP_DIR, exist_ok=True)
    local_path = os.path.join(TEMP_DIR, filename)
    request = drive.files().get_media(fileId=file_id)
    with open(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                logger.info("  Download progress: %d%%", int(status.progress() * 100))
    return local_path


# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------

def upload_unlisted_youtube(
    youtube,
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
) -> str:
    """Upload *video_path* as an unlisted video and return the YouTube video ID."""
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": "unlisted",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/*")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("  Upload progress: %d%%", int(status.progress() * 100))

    return response["id"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_upload(source: Source, yt: YouTubeDest, go_live_at: datetime, slot: str) -> bool:
    """Queue the YouTube destination's next source video for *slot*.

    Drive reads use the SOURCE's Google login (it owns the folder); the
    YouTube upload uses the DESTINATION's login. Returns True when a video
    was uploaded and queued, False when the source has no unused videos.
    """
    drive = build("drive", "v3", credentials=get_credentials(source.google_token))
    youtube = build("youtube", "v3", credentials=get_credentials(yt.google_token))

    log = load_json(yt.log_file, {"processed_file_ids": []})
    queue = load_json(yt.queue_file, [])

    videos = list_drive_videos(drive, source.drive_folder_id)
    next_video = next(
        (v for v in videos if v["id"] not in log["processed_file_ids"]), None
    )

    if not next_video:
        logger.info("[%s] No new videos in source %s.", yt.id, source.id)
        return False

    logger.info("[%s] Next video: %s", yt.id, next_video["name"])

    logger.info("[%s] Generating metadata with Groq...", yt.id)
    metadata = generate_metadata(next_video["name"], yt.content_description)

    logger.info("[%s] Downloading from Drive...", yt.id)
    local_path = download_video(drive, next_video["id"], next_video["name"])

    # Critical section: keep cleanup in finally so a partial run never leaves
    # a dangling temp file or a video stuck in limbo.
    try:
        logger.info("[%s] Uploading to YouTube as unlisted...", yt.id)
        video_id = upload_unlisted_youtube(
            youtube, local_path,
            metadata["title"], metadata["description"], metadata["tags"],
            yt.category_id,
        )
        logger.info("[%s]   YouTube video ID: %s", yt.id, video_id)
        logger.info("[%s] Queued to go public at: %s", yt.id, go_live_at.isoformat())

        queue.append({
            "youtube_video_id": video_id,
            "drive_file_id": next_video["id"],
            "go_live_at": go_live_at.isoformat(),
            "slot": slot,
            "published": False,
        })
        save_json(yt.queue_file, queue)

        log["processed_file_ids"].append(next_video["id"])
        save_json(yt.log_file, log)

        notify(
            f"📤 <b>[{yt.name}] Queued for {slot}</b>\n"
            f"🎬 {metadata['title']}\n"
            f"👀 Preview (unlisted): https://youtu.be/{video_id}\n"
            f"🕒 Goes public: {go_live_at.strftime('%d %b, %I:%M %p')}"
        )

    finally:
        # Always clean up the temp file, even if an exception was raised above.
        if os.path.exists(local_path):
            os.remove(local_path)
            logger.debug("Removed temp file: %s", local_path)

    return True
