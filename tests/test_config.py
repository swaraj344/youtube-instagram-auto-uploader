"""Tests for config.py (three-entity validation, loading, secrets merge)."""

import json
import os
import tempfile
import unittest

from config import ConfigError, load_config, load_secrets, validate_config


def valid_config():
    return {
        "sources": [{"id": "study-drive", "name": "Study videos"}],
        "youtube": [
            {
                "id": "study-yt",
                "name": "Study Motivation",
                "source": "study-drive",
                "enabled": True,
                "timezone": "Asia/Kolkata",
                "slots": ["17:30", "21:30"],
                "upload_lead_hours": 8,
                "content_description": "study shorts",
                "category_id": "22",
            }
        ],
        "instagram": [
            {
                "id": "casual-ig",
                "name": "only.casual.vibes",
                "source": "study-drive",
                "enabled": True,
                "timezone": "Asia/Kolkata",
                "slots": ["17:30", "21:30"],
                "content_description": "study shorts",
            }
        ],
    }


def valid_secrets():
    return {
        "sources": {
            "study-drive": {
                "drive_folder_id": "folder1",
                "google_token": {"refresh_token": "r"},
            }
        },
        "youtube": {"study-yt": {"google_token": {"refresh_token": "y"}}},
        "instagram": {"casual-ig": {"ig_business_account_id": "ig1"}},
    }


class ValidateConfigTest(unittest.TestCase):
    def test_valid_config_passes(self):
        validate_config(valid_config())  # should not raise

    def test_duplicate_id_across_sections_rejected(self):
        cfg = valid_config()
        cfg["youtube"][0]["id"] = "study-drive"
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_bad_id_rejected(self):
        cfg = valid_config()
        cfg["sources"][0]["id"] = "Bad Id!"
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_unknown_source_ref_rejected(self):
        cfg = valid_config()
        cfg["instagram"][0]["source"] = "nope"
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_empty_slots_rejected(self):
        cfg = valid_config()
        cfg["youtube"][0]["slots"] = []
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_bad_slot_time_rejected(self):
        cfg = valid_config()
        cfg["instagram"][0]["slots"] = ["25:99"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_duplicate_slot_times_rejected(self):
        cfg = valid_config()
        cfg["youtube"][0]["slots"] = ["17:30", "17:30"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_bad_lead_hours_rejected(self):
        cfg = valid_config()
        cfg["youtube"][0]["upload_lead_hours"] = 0
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_unknown_timezone_rejected(self):
        cfg = valid_config()
        cfg["instagram"][0]["timezone"] = "Mars/Olympus"
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_missing_name_rejected(self):
        cfg = valid_config()
        cfg["sources"][0]["name"] = ""
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_missing_sections_default_to_empty(self):
        validate_config({"sources": [{"id": "a", "name": "A"}]})  # no destinations: fine


class LoadConfigTest(unittest.TestCase):
    def _write(self, data):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(data, tmp)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_merges_secrets_by_section_and_id(self):
        cfg = load_config(self._write(valid_config()), valid_secrets())
        src = cfg.sources["study-drive"]
        self.assertEqual(src.drive_folder_id, "folder1")
        self.assertTrue(src.has_secrets())
        (yt,) = cfg.youtube
        self.assertTrue(yt.has_secrets())
        self.assertEqual(yt.kind, "youtube")
        self.assertEqual(yt.queue_file, os.path.join("state", "study-yt", "publish_queue.json"))
        (ig,) = cfg.instagram
        self.assertEqual(ig.ig_business_account_id, "ig1")
        self.assertEqual(ig.kind, "instagram")
        self.assertEqual(ig.slot_log_file, os.path.join("state", "casual-ig", "slot_log.json"))
        self.assertIs(cfg.source_of(yt), src)
        self.assertIs(cfg.source_of(ig), src)

    def test_missing_secrets_load_with_has_secrets_false(self):
        cfg = load_config(self._write(valid_config()), {})
        self.assertFalse(cfg.sources["study-drive"].has_secrets())
        self.assertFalse(cfg.youtube[0].has_secrets())
        self.assertFalse(cfg.instagram[0].has_secrets())

    def test_missing_file_raises(self):
        with self.assertRaises(ConfigError):
            load_config("does-not-exist.json", {})

    def test_invalid_json_raises(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write("{nope")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        with self.assertRaises(ConfigError):
            load_config(tmp.name, {})


class LoadSecretsTest(unittest.TestCase):
    def test_env_var_wins(self):
        os.environ["PIPELINE_SECRETS_JSON"] = '{"sources": {"s": {"drive_folder_id": "x"}}}'
        self.addCleanup(os.environ.pop, "PIPELINE_SECRETS_JSON", None)
        self.assertEqual(load_secrets()["sources"]["s"]["drive_folder_id"], "x")

    def test_invalid_env_json_raises(self):
        os.environ["PIPELINE_SECRETS_JSON"] = "{nope"
        self.addCleanup(os.environ.pop, "PIPELINE_SECRETS_JSON", None)
        with self.assertRaises(ConfigError):
            load_secrets()


if __name__ == "__main__":
    unittest.main()
