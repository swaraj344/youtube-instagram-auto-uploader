"""
One-shot converter: legacy channel model -> decoupled sources/destinations.

Channel slug X becomes source "X-drive" + YouTube "X-yt" + Instagram "X-ig"
(the known live channel "study" keeps the spec-pinned IG id "casual-ig").
State history is copied to BOTH destinations — it genuinely went to both —
except videos still queued-but-unpublished for YouTube, which are removed
from the Instagram copy so IG still posts them at its next slot.

Run at merge time (after merging the decouple branch onto latest main) so
the very latest CI-written state is what gets converted:

    python migration.py
"""

from __future__ import annotations

import json
import os
import shutil

from config import validate_config
from utils import load_json, save_json

_LEGACY_CONFIG = "channels.json"
_LEGACY_SECRETS = os.path.join("secrets", "channels_secrets.json")
_NEW_CONFIG = "config.json"
_NEW_SECRETS = os.path.join("secrets", "pipeline_secrets.json")

# Spec-pinned id for the live channel's IG destination.
_IG_ID_OVERRIDES = {"study": "casual-ig"}


def migration_available(root: str = ".") -> bool:
    return os.path.exists(os.path.join(root, _LEGACY_CONFIG))


def migrate(root: str = ".") -> str:
    legacy_path = os.path.join(root, _LEGACY_CONFIG)
    if not os.path.exists(legacy_path):
        raise RuntimeError("Nothing to migrate — channels.json not found.")

    with open(legacy_path, encoding="utf-8") as fh:
        legacy = json.load(fh)
    old_secrets = load_json(os.path.join(root, _LEGACY_SECRETS), {})

    config = {"sources": [], "youtube": [], "instagram": []}
    secrets = {"sources": {}, "youtube": {}, "instagram": {}}
    converted = []

    for ch in legacy.get("channels", []):
        slug = ch["slug"]
        src_id = f"{slug}-drive"
        yt_id = f"{slug}-yt"
        ig_id = _IG_ID_OVERRIDES.get(slug, f"{slug}-ig")
        sec = old_secrets.get(slug, {})

        config["sources"].append({"id": src_id, "name": f"{ch['display_name']} (Drive)"})
        config["youtube"].append({
            "id": yt_id,
            "name": ch["display_name"],
            "source": src_id,
            "enabled": bool(ch.get("enabled", True)),
            "timezone": ch.get("timezone", "Asia/Kolkata"),
            "slots": list(ch["slots"]),
            "upload_lead_hours": ch.get("upload_lead_hours", 8),
            "content_description": ch.get("content_description", ""),
            "category_id": str(ch.get("youtube_category_id", "22")),
        })
        config["instagram"].append({
            "id": ig_id,
            "name": ch["display_name"],
            "source": src_id,
            "enabled": bool(ch.get("enabled", True)),
            "timezone": ch.get("timezone", "Asia/Kolkata"),
            "slots": list(ch["slots"]),
            "content_description": ch.get("content_description", ""),
        })

        secrets["sources"][src_id] = {
            "drive_folder_id": sec.get("drive_folder_id"),
            "google_token": sec.get("google_token"),
        }
        secrets["youtube"][yt_id] = {
            "google_token": sec.get("google_token"),
            "google_client_secret": sec.get("google_client_secret"),
        }
        secrets["instagram"][ig_id] = {
            "ig_business_account_id": sec.get("ig_business_account_id"),
        }

        # State: copy history to both destinations; move the queue to YouTube.
        old_state = os.path.join(root, "state", slug)
        log = load_json(os.path.join(old_state, "processed_log.json"),
                        {"processed_file_ids": []})
        queue = load_json(os.path.join(old_state, "publish_queue.json"), [])

        inflight = {q.get("drive_file_id") for q in queue if not q.get("published")}
        ig_log = {
            "processed_file_ids": [
                v for v in log["processed_file_ids"] if v not in inflight
            ]
        }

        yt_state = os.path.join(root, "state", yt_id)
        ig_state = os.path.join(root, "state", ig_id)
        os.makedirs(yt_state, exist_ok=True)
        os.makedirs(ig_state, exist_ok=True)
        save_json(os.path.join(yt_state, "processed_log.json"), log)
        save_json(os.path.join(yt_state, "publish_queue.json"), queue)
        save_json(os.path.join(ig_state, "processed_log.json"), ig_log)
        save_json(os.path.join(ig_state, "slot_log.json"), {})
        if os.path.isdir(old_state):
            shutil.rmtree(old_state)

        converted.append(slug)

    validate_config(config)  # never write a config the pipeline can't load
    save_json(os.path.join(root, _NEW_CONFIG), config)
    os.makedirs(os.path.join(root, "secrets"), exist_ok=True)
    save_json(os.path.join(root, _NEW_SECRETS), secrets)
    os.remove(legacy_path)

    return (
        f"Converted {len(converted)} channel(s): {', '.join(converted)}. "
        "Review, then Deploy to push config.json and PIPELINE_SECRETS_JSON."
    )


if __name__ == "__main__":
    print(migrate())
