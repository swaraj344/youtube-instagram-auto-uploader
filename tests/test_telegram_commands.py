"""Tests for telegram_commands.py (parsing, channel resolution, routing)."""

import unittest

from telegram_commands import (
    HELP_TEXT,
    authorized_texts,
    handle_message,
    parse_command,
    resolve_channel,
)


class ParseCommandTest(unittest.TestCase):
    def test_status(self):
        self.assertEqual(parse_command("/status"), ("status", {}))

    def test_upload_with_channel_and_slot(self):
        self.assertEqual(
            parse_command("/upload study 17:30"),
            ("upload", {"channel": "study", "slot": "17:30"}),
        )

    def test_upload_slot_only(self):
        self.assertEqual(
            parse_command("/upload 17:30"),
            ("upload", {"channel": None, "slot": "17:30"}),
        )

    def test_upload_channel_only_case_insensitive(self):
        self.assertEqual(
            parse_command("/UPLOAD Study"),
            ("upload", {"channel": "study", "slot": None}),
        )

    def test_upload_bare(self):
        self.assertEqual(
            parse_command("/upload"), ("upload", {"channel": None, "slot": None})
        )

    def test_publishnow_with_channel(self):
        self.assertEqual(
            parse_command("/publishnow memes"), ("publishnow", {"channel": "memes"})
        )

    def test_publishnow_bare(self):
        self.assertEqual(parse_command("/publishnow"), ("publishnow", {"channel": None}))

    def test_publish_distinct_from_publishnow(self):
        self.assertEqual(parse_command("/publish"), ("publish", {}))

    def test_botname_suffix_stripped(self):
        self.assertEqual(parse_command("/status@my_bot"), ("status", {}))

    def test_non_command_text_returns_none(self):
        self.assertIsNone(parse_command("hello there"))

    def test_unknown_command(self):
        self.assertEqual(parse_command("/frobnicate"), ("unknown", {}))


class ResolveChannelTest(unittest.TestCase):
    def test_explicit_valid_slug(self):
        self.assertEqual(resolve_channel("study", ["study", "memes"]), ("study", None))

    def test_explicit_unknown_slug_errors(self):
        slug, err = resolve_channel("nope", ["study"])
        self.assertIsNone(slug)
        self.assertIn("nope", err)

    def test_omitted_with_single_channel_defaults(self):
        self.assertEqual(resolve_channel(None, ["study"]), ("study", None))

    def test_omitted_with_multiple_channels_errors(self):
        slug, err = resolve_channel(None, ["study", "memes"])
        self.assertIsNone(slug)
        self.assertIn("study", err)
        self.assertIn("memes", err)


class HandleMessageTest(unittest.TestCase):
    def setUp(self):
        self.dispatched = []
        self.deps = {
            "dispatch": self.dispatched.append,
            "build_status": lambda: "STATUS",
            "channels": ["study"],
        }

    def test_status(self):
        self.assertEqual(handle_message("/status", self.deps), "STATUS")

    def test_upload_defaults_to_single_channel(self):
        reply = handle_message("/upload 17:30", self.deps)
        self.assertEqual(
            self.dispatched,
            [{"channel": "study", "action": "upload", "upload_slot": "17:30"}],
        )
        self.assertIn("study", reply)

    def test_upload_without_slot_sends_empty_slot(self):
        handle_message("/upload", self.deps)
        self.assertEqual(
            self.dispatched,
            [{"channel": "study", "action": "upload", "upload_slot": ""}],
        )

    def test_upload_ambiguous_channel_asks_instead_of_dispatching(self):
        self.deps["channels"] = ["study", "memes"]
        reply = handle_message("/upload", self.deps)
        self.assertEqual(self.dispatched, [])
        self.assertIn("study", reply)

    def test_publish_dispatches_plain_pass(self):
        handle_message("/publish", self.deps)
        self.assertEqual(self.dispatched, [{}])

    def test_publishnow_dispatches_action(self):
        handle_message("/publishnow study", self.deps)
        self.assertEqual(
            self.dispatched, [{"channel": "study", "action": "publishnow"}]
        )

    def test_unknown_gets_help(self):
        self.assertEqual(handle_message("/wat", self.deps), HELP_TEXT)
        self.assertEqual(handle_message("hi", self.deps), HELP_TEXT)


class AuthorizedTextsTest(unittest.TestCase):
    def test_filters_by_chat_id(self):
        updates = [
            {"message": {"chat": {"id": 111}, "text": "/status"}},
            {"message": {"chat": {"id": 222}, "text": "/upload"}},
            {"message": {"chat": {"id": 111}}},  # no text
        ]
        self.assertEqual(list(authorized_texts(updates, "111")), ["/status"])


if __name__ == "__main__":
    unittest.main()
