"""Tests for the legacy channel -> sources/destinations migration."""

import json
import os
import tempfile
import unittest

from migration import migrate, migration_available


def seed_legacy(root):
    with open(os.path.join(root, "channels.json"), "w") as fh:
        json.dump({"channels": [{
            "slug": "study",
            "display_name": "Study Motivation",
            "enabled": True,
            "timezone": "Asia/Kolkata",
            "slots": ["17:30", "21:30"],
            "upload_lead_hours": 8,
            "content_description": "study shorts",
            "youtube_category_id": "22",
        }]}, fh)
    os.makedirs(os.path.join(root, "secrets"), exist_ok=True)
    with open(os.path.join(root, "secrets", "channels_secrets.json"), "w") as fh:
        json.dump({"study": {
            "drive_folder_id": "folder1",
            "ig_business_account_id": "ig1",
            "google_token": {"refresh_token": "r"},
            "google_client_secret": None,
        }}, fh)
    state = os.path.join(root, "state", "study")
    os.makedirs(state, exist_ok=True)
    with open(os.path.join(state, "processed_log.json"), "w") as fh:
        json.dump({"processed_file_ids": ["v1", "v2", "v3"]}, fh)
    with open(os.path.join(state, "publish_queue.json"), "w") as fh:
        json.dump([
            {"youtube_video_id": "yA", "drive_file_id": "v2",
             "go_live_at": "2026-08-17T17:30:00+05:30", "slot": "17:30", "published": True},
            {"youtube_video_id": "yB", "drive_file_id": "v3",
             "go_live_at": "2026-08-17T21:30:00+05:30", "slot": "21:30", "published": False},
        ], fh)


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        seed_legacy(self.root)

    def _load(self, *parts):
        with open(os.path.join(self.root, *parts)) as fh:
            return json.load(fh)

    def test_available_before_not_after(self):
        self.assertTrue(migration_available(self.root))
        migrate(self.root)
        self.assertFalse(migration_available(self.root))

    def test_config_mapping(self):
        migrate(self.root)
        cfg = self._load("config.json")
        self.assertEqual(cfg["sources"], [{"id": "study-drive", "name": "Study Motivation (Drive)"}])
        (yt,) = cfg["youtube"]
        self.assertEqual(yt["id"], "study-yt")
        self.assertEqual(yt["source"], "study-drive")
        self.assertEqual(yt["slots"], ["17:30", "21:30"])
        self.assertEqual(yt["category_id"], "22")
        (ig,) = cfg["instagram"]
        self.assertEqual(ig["id"], "casual-ig")
        self.assertEqual(ig["source"], "study-drive")
        self.assertNotIn("upload_lead_hours", ig)

    def test_secrets_mapping(self):
        migrate(self.root)
        sec = self._load("secrets", "pipeline_secrets.json")
        self.assertEqual(sec["sources"]["study-drive"]["drive_folder_id"], "folder1")
        self.assertEqual(sec["sources"]["study-drive"]["google_token"]["refresh_token"], "r")
        self.assertEqual(sec["youtube"]["study-yt"]["google_token"]["refresh_token"], "r")
        self.assertEqual(sec["instagram"]["casual-ig"]["ig_business_account_id"], "ig1")

    def test_state_copied_to_both_with_inflight_fix(self):
        migrate(self.root)
        yt_log = self._load("state", "study-yt", "processed_log.json")
        self.assertEqual(yt_log["processed_file_ids"], ["v1", "v2", "v3"])
        # v3 is queued-but-unpublished -> removed from the IG copy so IG still posts it
        ig_log = self._load("state", "casual-ig", "processed_log.json")
        self.assertEqual(ig_log["processed_file_ids"], ["v1", "v2"])
        queue = self._load("state", "study-yt", "publish_queue.json")
        self.assertEqual([q["youtube_video_id"] for q in queue], ["yA", "yB"])
        # Ledger is seeded: the most recent occurrence of each slot is marked
        # posted (the legacy pipeline covered them), so the first run after
        # migration doesn't fire extra IG posts.
        slot_log = self._load("state", "casual-ig", "slot_log.json")
        self.assertEqual(len(slot_log), 2)
        self.assertTrue(all(v["status"] == "posted" for v in slot_log.values()))

    def test_legacy_files_removed(self):
        migrate(self.root)
        self.assertFalse(os.path.exists(os.path.join(self.root, "channels.json")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "state", "study")))

    def test_nothing_to_migrate_raises(self):
        migrate(self.root)
        with self.assertRaises(RuntimeError):
            migrate(self.root)


if __name__ == "__main__":
    unittest.main()
