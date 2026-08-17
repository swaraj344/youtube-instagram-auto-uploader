"""Tests for webapp.services file plumbing (git/gh calls patched out)."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from config import ConfigError
from webapp import services


def seed_config(root, channels=None):
    data = {"channels": channels if channels is not None else [{
        "slug": "study",
        "display_name": "Study Motivation",
        "enabled": True,
        "timezone": "Asia/Kolkata",
        "slots": ["17:30"],
        "upload_lead_hours": 8,
        "content_description": "study shorts",
        "youtube_category_id": "22",
    }]}
    with open(os.path.join(root, "channels.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


FORM = {
    "display_name": "Meme Channel",
    "enabled": "on",
    "timezone": "Asia/Kolkata",
    "slots": "12:00, 18:00",
    "upload_lead_hours": "6",
    "content_description": "memes",
    "youtube_category_id": "23",
    "drive_folder_id": "folder123",
    "ig_business_account_id": "ig456",
}


class ServicesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(services, "ROOT", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        seed_config(self.tmp.name)

    def test_upsert_new_channel_writes_config_and_secrets(self):
        services.upsert_channel("memes", FORM, new=True)
        cfg = json.load(open(os.path.join(self.tmp.name, "channels.json")))
        memes = next(c for c in cfg["channels"] if c["slug"] == "memes")
        self.assertEqual(memes["slots"], ["12:00", "18:00"])
        self.assertEqual(memes["upload_lead_hours"], 6)
        secrets = json.load(
            open(os.path.join(self.tmp.name, "secrets", "channels_secrets.json"))
        )
        self.assertEqual(secrets["memes"]["drive_folder_id"], "folder123")
        self.assertEqual(secrets["memes"]["ig_business_account_id"], "ig456")

    def test_upsert_duplicate_new_slug_rejected(self):
        with self.assertRaises(ConfigError):
            services.upsert_channel("study", FORM, new=True)

    def test_upsert_invalid_slot_rejected_and_config_untouched(self):
        bad = dict(FORM, slots="25:99")
        with self.assertRaises(ConfigError):
            services.upsert_channel("memes", bad, new=True)
        cfg = json.load(open(os.path.join(self.tmp.name, "channels.json")))
        self.assertEqual([c["slug"] for c in cfg["channels"]], ["study"])

    def test_get_channel_merges_secret_fields(self):
        services.upsert_channel("memes", FORM, new=True)
        ch = services.get_channel("memes")
        self.assertEqual(ch["drive_folder_id"], "folder123")
        self.assertFalse(ch["youtube_connected"])
        self.assertIsNone(services.get_channel("nope"))

    def test_toggle_channel_flips_enabled(self):
        services.toggle_channel("study")
        cfg = json.load(open(os.path.join(self.tmp.name, "channels.json")))
        self.assertFalse(cfg["channels"][0]["enabled"])

    def test_delete_channel_removes_config_and_secrets(self):
        services.upsert_channel("memes", FORM, new=True)
        services.delete_channel("memes")
        cfg = json.load(open(os.path.join(self.tmp.name, "channels.json")))
        self.assertEqual([c["slug"] for c in cfg["channels"]], ["study"])
        secrets = json.load(
            open(os.path.join(self.tmp.name, "secrets", "channels_secrets.json"))
        )
        self.assertNotIn("memes", secrets)

    def test_run_legacy_import_targets_first_channel_without_token(self):
        with open(os.path.join(self.tmp.name, "token.json"), "w") as fh:
            json.dump({"refresh_token": "r", "client_id": "c", "client_secret": "s"}, fh)
        with open(os.path.join(self.tmp.name, ".env"), "w") as fh:
            fh.write("DRIVE_FOLDER_ID=legacyfolder\nIG_BUSINESS_ACCOUNT_ID=legacyig\n")
        summary = services.run_legacy_import()
        self.assertIn("study", summary)
        secrets = json.load(
            open(os.path.join(self.tmp.name, "secrets", "channels_secrets.json"))
        )
        self.assertEqual(secrets["study"]["google_token"]["refresh_token"], "r")
        self.assertEqual(secrets["study"]["drive_folder_id"], "legacyfolder")

    def test_trigger_action_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            services.trigger_action("study", "detonate")

    def test_trigger_action_runs_gh_workflow(self):
        with patch.object(services, "_run", return_value=(0, "")) as run:
            services.trigger_action("study", "upload", "17:30")
        args = run.call_args[0][0]
        self.assertEqual(args[:3], ["gh", "workflow", "run"])
        self.assertIn("channel=study", " ".join(args))


if __name__ == "__main__":
    unittest.main()
