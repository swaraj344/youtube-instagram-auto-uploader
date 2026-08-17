"""Tests for webapp.services entity plumbing (git/gh calls patched out)."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from config import ConfigError
from webapp import services


def seed(root):
    with open(os.path.join(root, "config.json"), "w") as fh:
        json.dump({
            "sources": [{"id": "study-drive", "name": "Study videos"}],
            "youtube": [{"id": "study-yt", "name": "Study", "source": "study-drive",
                         "enabled": True, "timezone": "Asia/Kolkata",
                         "slots": ["17:30"], "upload_lead_hours": 8,
                         "content_description": "x", "category_id": "22"}],
            "instagram": [{"id": "casual-ig", "name": "Casual", "source": "study-drive",
                           "enabled": True, "timezone": "Asia/Kolkata",
                           "slots": ["17:30"], "content_description": "x"}],
        }, fh)


class ServicesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(services, "ROOT", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        seed(self.tmp.name)

    def _secrets(self):
        path = os.path.join(self.tmp.name, "secrets", "pipeline_secrets.json")
        return json.load(open(path)) if os.path.exists(path) else {}

    def test_upsert_new_source_writes_config_and_secret(self):
        services.upsert_entity("sources", "memes-drive", {
            "name": "Meme videos", "drive_folder_id": "f42",
        }, new=True)
        cfg = json.load(open(os.path.join(self.tmp.name, "config.json")))
        self.assertIn("memes-drive", [s["id"] for s in cfg["sources"]])
        self.assertEqual(self._secrets()["sources"]["memes-drive"]["drive_folder_id"], "f42")

    def test_upsert_new_youtube_dest(self):
        services.upsert_entity("youtube", "memes-yt", {
            "name": "Memes", "source": "study-drive", "enabled": "on",
            "timezone": "Asia/Kolkata", "slots": "12:00, 18:00",
            "upload_lead_hours": "6", "content_description": "memes",
            "category_id": "23",
        }, new=True)
        cfg = json.load(open(os.path.join(self.tmp.name, "config.json")))
        yt = next(y for y in cfg["youtube"] if y["id"] == "memes-yt")
        self.assertEqual(yt["slots"], ["12:00", "18:00"])
        self.assertEqual(yt["upload_lead_hours"], 6)

    def test_upsert_new_instagram_dest(self):
        services.upsert_entity("instagram", "memes-ig", {
            "name": "Memes IG", "source": "study-drive", "enabled": "on",
            "timezone": "Asia/Kolkata", "slots": "13:00",
            "content_description": "memes", "ig_business_account_id": "ig99",
        }, new=True)
        cfg = json.load(open(os.path.join(self.tmp.name, "config.json")))
        self.assertIn("memes-ig", [i["id"] for i in cfg["instagram"]])
        self.assertEqual(self._secrets()["instagram"]["memes-ig"]["ig_business_account_id"], "ig99")

    def test_upsert_duplicate_id_rejected(self):
        with self.assertRaises(ConfigError):
            services.upsert_entity("sources", "study-yt", {"name": "X"}, new=True)

    def test_upsert_invalid_slots_rejected_config_untouched(self):
        with self.assertRaises(ConfigError):
            services.upsert_entity("youtube", "bad-yt", {
                "name": "Bad", "source": "study-drive", "slots": "25:99",
            }, new=True)
        cfg = json.load(open(os.path.join(self.tmp.name, "config.json")))
        self.assertNotIn("bad-yt", [y["id"] for y in cfg["youtube"]])

    def test_delete_referenced_source_refused(self):
        with self.assertRaises(ConfigError):
            services.delete_entity("sources", "study-drive")

    def test_delete_dest_then_source_ok(self):
        services.delete_entity("youtube", "study-yt")
        services.delete_entity("instagram", "casual-ig")
        services.delete_entity("sources", "study-drive")
        cfg = json.load(open(os.path.join(self.tmp.name, "config.json")))
        self.assertEqual(cfg["sources"], [])

    def test_toggle_dest(self):
        services.toggle_entity("youtube", "study-yt")
        cfg = json.load(open(os.path.join(self.tmp.name, "config.json")))
        self.assertFalse(cfg["youtube"][0]["enabled"])

    def test_get_entity_shapes(self):
        self.assertEqual(services.get_entity("sources", "study-drive")["drive_folder_id"], "")
        self.assertFalse(services.get_entity("youtube", "study-yt")["youtube_connected"])
        self.assertIsNone(services.get_entity("youtube", "nope"))

    def test_list_entities_source_used_by(self):
        (src,) = services.list_entities("sources")
        self.assertEqual(sorted(src["used_by"]), ["casual-ig", "study-yt"])

    def test_trigger_action_validates(self):
        with self.assertRaises(ValueError):
            services.trigger_action("study-yt", "detonate")
        with patch.object(services, "_run", return_value=(0, "")) as run:
            services.trigger_action("casual-ig", "postnow")
        self.assertIn("target=casual-ig", " ".join(run.call_args[0][0]))

    def test_run_migration_wraps_migrate(self):
        with open(os.path.join(self.tmp.name, "channels.json"), "w") as fh:
            json.dump({"channels": []}, fh)
        self.assertTrue(services.migration_available())
        summary = services.run_migration()
        self.assertIn("Converted", summary)


if __name__ == "__main__":
    unittest.main()
