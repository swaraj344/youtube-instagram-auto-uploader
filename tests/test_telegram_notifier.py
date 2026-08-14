"""Tests for telegram_notifier.py."""

import os
import unittest
from unittest import mock

os.environ.setdefault("DRIVE_FOLDER_ID", "test-folder")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import telegram_notifier


class NotifyTest(unittest.TestCase):
    def test_no_op_returns_false_when_env_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(telegram_notifier.requests, "post") as post:
                self.assertFalse(telegram_notifier.notify("hello"))
                post.assert_not_called()

    def test_sends_html_message_to_configured_chat(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "42"}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(telegram_notifier.requests, "post") as post:
                post.return_value.raise_for_status.return_value = None
                self.assertTrue(telegram_notifier.notify("<b>hi</b>"))

        (url,), kwargs = post.call_args
        self.assertEqual(url, "https://api.telegram.org/bottok123/sendMessage")
        self.assertEqual(kwargs["json"]["chat_id"], "42")
        self.assertEqual(kwargs["json"]["text"], "<b>hi</b>")
        self.assertEqual(kwargs["json"]["parse_mode"], "HTML")

    def test_swallows_network_errors_and_returns_false(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHAT_ID": "42"}
        with mock.patch.dict(os.environ, env):
            with mock.patch.object(
                telegram_notifier.requests, "post", side_effect=OSError("down")
            ):
                self.assertFalse(telegram_notifier.notify("hello"))


if __name__ == "__main__":
    unittest.main()
