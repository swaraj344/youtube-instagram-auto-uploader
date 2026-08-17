"""
Upload step of the pipeline, called by run_pipeline.py when a slot's lead
window opens (go-live minus upload_lead_hours).

Picks the channel's next unprocessed Drive video, generates metadata with
Groq, uploads it to YouTube as UNLISTED, makes the Drive file link-shareable
(needed for Instagram later), and queues it in the channel's publish queue
with the target go-live time.

Does NOT touch Instagram — that happens in publisher.py at the go-live
moment, since Instagram can't be uploaded ahead and held.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

from auth import get_credentials
from config import Channel
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


def make_shareable(drive, file_id: str) -> str:
    """Grant anyone-with-link viewer access and return a direct-download URL."""
    drive.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()
    return f"https://drive.google.com/uc?export=download&id={file_id}"


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

def run_upload(channel: Channel, go_live_at: datetime, slot: str) -> bool:
    """Queue the channel's next video for *slot* going live at *go_live_at*.

    Returns True when a video was uploaded and queued, False when the Drive
    folder has no unprocessed videos left.
    """
    creds = get_credentials(channel.google_token)
    drive = build("drive", "v3", credentials=creds)
    youtube = build("youtube", "v3", credentials=creds)

    log = load_json(channel.log_file, {"processed_file_ids": []})
    queue = load_json(channel.queue_file, [])

    videos = list_drive_videos(drive, channel.drive_folder_id)
    next_video = next(
        (v for v in videos if v["id"] not in log["processed_file_ids"]), None
    )

    if not next_video:
        logger.info("[%s] No new videos to process.", channel.slug)
        return False

    logger.info("[%s] Next video: %s", channel.slug, next_video["name"])

    logger.info("[%s] Generating metadata with Groq...", channel.slug)
    metadata = generate_metadata(next_video["name"], channel.content_description)

    logger.info("[%s] Downloading from Drive...", channel.slug)
    local_path = download_video(drive, next_video["id"], next_video["name"])

    # Critical section: keep cleanup in finally so a partial run never leaves
    # a dangling temp file or a video stuck in limbo.
    try:
        logger.info("[%s] Uploading to YouTube as unlisted...", channel.slug)
        video_id = upload_unlisted_youtube(
            youtube, local_path,
            metadata["title"], metadata["description"], metadata["tags"],
            channel.youtube_category_id,
        )
        logger.info("[%s]   YouTube video ID: %s", channel.slug, video_id)

        logger.info("[%s] Making Drive file link-shareable for Instagram...", channel.slug)
        drive_public_url = make_shareable(drive, next_video["id"])

        logger.info("[%s] Queued to go public/live at: %s", channel.slug, go_live_at.isoformat())

        queue.append({
            "youtube_video_id": video_id,
            "drive_file_id": next_video["id"],
            "drive_public_url": drive_public_url,
            "ig_caption": metadata["ig_caption"],
            "go_live_at": go_live_at.isoformat(),
            "slot": slot,
            "published": False,
        })
        save_json(channel.queue_file, queue)

        log["processed_file_ids"].append(next_video["id"])
        save_json(channel.log_file, log)

        notify(
            f"📤 <b>[{channel.display_name}] Queued for {slot}</b>\n"
            f"🎬 {metadata['title']}\n"
            f"👀 Preview (unlisted): https://youtu.be/{video_id}\n"
            f"🕒 Goes live: {go_live_at.strftime('%d %b, %I:%M %p')}"
        )

    finally:
        # Always clean up the temp file, even if an exception was raised above.
        if os.path.exists(local_path):
            os.remove(local_path)
            logger.debug("Removed temp file: %s", local_path)

    return True
