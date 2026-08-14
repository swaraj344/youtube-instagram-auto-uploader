"""Tests for telegram_commands.py (parsing, filtering, routing)."""

import os
import unittest

os.environ.setdefault("DRIVE_FOLDER_ID", "test-folder")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from telegram_commands import authorized_texts, handle_message, parse_command


class ParseCommandTest(unittest.TestCase):
    def test_status(self):
        self.assertEqual(parse_command("/status"), ("status", {}))

    def test_upload_with_slot_case_insensitive(self):
        self.assertEqual(parse_command("/upload a"), ("upload", {"slot": "A"}))
        self.assertEqual(parse_command("/UPLOAD B"), ("upload", {"slot": "B"}))

    def test_upload_missing_or_bad_slot_is_invalid(self):
        self.assertEqual(parse_command("/upload"), ("invalid_upload", {}))
        self.assertEqual(parse_command("/upload C"), ("invalid_upload", {}))

    def test_publish_and_publishnow_are_distinct(self):
        self.assertEqual(parse_command("/publish"), ("publish", {}))
        self.assertEqual(parse_command("/publishnow"), ("publishnow", {}))

    def test_botname_suffix_stripped(self):
        self.assertEqual(parse_command("/status@my_bot"), ("status", {}))

    def test_non_command_text_returns_none(self):
        self.assertIsNone(parse_command("hello there"))

    def test_unknown_command(self):
        self.assertEqual(parse_command("/frobnicate"), ("unknown", {}))


class HandleMessageTest(unittest.TestCase):
    def setUp(self):
        self.dispatched = []
        self.deps = {
            "dispatch": lambda inputs: self.dispatched.append(inputs),
            "build_status": lambda: "STATUS TEXT",
        }

    def test_status_returns_status_text_without_dispatch(self):
        reply = handle_message("/status", self.deps)
        self.assertEqual(reply, "STATUS TEXT")
        self.assertEqual(self.dispatched, [])

    def test_upload_dispatches_slot(self):
        handle_message("/upload B", self.deps)
        self.assertEqual(self.dispatched, [{"slot": "B"}])

    def test_publish_dispatches_no_inputs(self):
        handle_message("/publish", self.deps)
        self.assertEqual(self.dispatched, [{}])

    def test_publishnow_dispatches_force_next(self):
        handle_message("/publishnow", self.deps)
        self.assertEqual(self.dispatched, [{"force_next": "true"}])

    def test_unknown_gets_help_without_dispatch(self):
        reply = handle_message("/frobnicate", self.deps)
        self.assertIn("/upload", reply)
        self.assertEqual(self.dispatched, [])


class AuthorizedTextsTest(unittest.TestCase):
    def test_filters_to_configured_chat_and_text_messages(self):
        updates = [
            {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/status"}},
            {"update_id": 2, "message": {"chat": {"id": 999}, "text": "/publish"}},
            {"update_id": 3, "message": {"chat": {"id": 42}, "photo": []}},
            {"update_id": 4},
            {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/upload A"}},
        ]
        self.assertEqual(
            list(authorized_texts(updates, "42")), ["/status", "/upload A"]
        )


if __name__ == "__main__":
    unittest.main()
