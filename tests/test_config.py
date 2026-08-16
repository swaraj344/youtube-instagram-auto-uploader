"""Tests for config.py (validation, loading, secrets merge)."""

import json
import os
import tempfile
import unittest

from config import ConfigError, load_channels, load_secrets, validate_config


def valid_config():
    return {
        "channels": [
            {
                "slug": "study",
                "display_name": "Study Motivation",
                "enabled": True,
                "timezone": "Asia/Kolkata",
                "slots": ["17:30", "21:30"],
                "upload_lead_hours": 8,
                "content_description": "study shorts",
                "youtube_category_id": "22",
            }
        ]
    }


class ValidateConfigTest(unittest.TestCase):
    def test_valid_config_passes(self):
        validate_config(valid_config())  # should not raise

    def test_bad_slug_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["slug"] = "Bad Slug!"
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_duplicate_slug_rejected(self):
        cfg = valid_config()
        cfg["channels"].append(dict(cfg["channels"][0]))
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_bad_slot_time_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["slots"] = ["25:99"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_empty_slots_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["slots"] = []
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_duplicate_slot_times_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["slots"] = ["17:30", "17:30"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_unknown_timezone_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["timezone"] = "Mars/Olympus"
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_missing_display_name_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["display_name"] = ""
        with self.assertRaises(ConfigError):
            validate_config(cfg)


class LoadChannelsTest(unittest.TestCase):
    def _write(self, data):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(data, tmp)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_merges_secrets_by_slug(self):
        path = self._write(valid_config())
        secrets = {
            "study": {
                "drive_folder_id": "d1",
                "ig_business_account_id": "ig1",
                "google_token": {"refresh_token": "r"},
            }
        }
        (ch,) = load_channels(path, secrets)
        self.assertEqual(ch.drive_folder_id, "d1")
        self.assertEqual(ch.ig_business_account_id, "ig1")
        self.assertTrue(ch.has_secrets())
        self.assertEqual(
            ch.queue_file, os.path.join("state", "study", "publish_queue.json")
        )
        self.assertEqual(
            ch.log_file, os.path.join("state", "study", "processed_log.json")
        )

    def test_missing_secrets_loads_with_has_secrets_false(self):
        path = self._write(valid_config())
        (ch,) = load_channels(path, {})
        self.assertFalse(ch.has_secrets())
        self.assertTrue(ch.enabled)

    def test_missing_file_raises(self):
        with self.assertRaises(ConfigError):
            load_channels("does-not-exist.json", {})

    def test_invalid_json_raises(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write("{nope")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        with self.assertRaises(ConfigError):
            load_channels(tmp.name, {})


class LoadSecretsTest(unittest.TestCase):
    def test_env_var_wins(self):
        os.environ["CHANNELS_SECRETS_JSON"] = '{"study": {"drive_folder_id": "x"}}'
        self.addCleanup(os.environ.pop, "CHANNELS_SECRETS_JSON", None)
        self.assertEqual(load_secrets()["study"]["drive_folder_id"], "x")

    def test_invalid_env_json_raises(self):
        os.environ["CHANNELS_SECRETS_JSON"] = "{nope"
        self.addCleanup(os.environ.pop, "CHANNELS_SECRETS_JSON", None)
        with self.assertRaises(ConfigError):
            load_secrets()


if __name__ == "__main__":
    unittest.main()
