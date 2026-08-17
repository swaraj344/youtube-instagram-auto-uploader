"""Tests for telegram_commands.py (parsing, target resolution, routing)."""

import unittest

from telegram_commands import (
    HELP_TEXT,
    authorized_texts,
    handle_message,
    parse_command,
    resolve_target,
)


class ParseCommandTest(unittest.TestCase):
    def test_status(self):
        self.assertEqual(parse_command("/status"), ("status", {}))

    def test_upload_with_target_and_slot(self):
        self.assertEqual(
            parse_command("/upload study-yt 17:30"),
            ("upload", {"target": "study-yt", "slot": "17:30"}),
        )

    def test_upload_slot_only(self):
        self.assertEqual(
            parse_command("/upload 17:30"),
            ("upload", {"target": None, "slot": "17:30"}),
        )

    def test_upload_bare(self):
        self.assertEqual(
            parse_command("/upload"), ("upload", {"target": None, "slot": None})
        )

    def test_publishnow(self):
        self.assertEqual(
            parse_command("/publishnow study-yt"), ("publishnow", {"target": "study-yt"})
        )
        self.assertEqual(parse_command("/publishnow"), ("publishnow", {"target": None}))

    def test_postnow(self):
        self.assertEqual(
            parse_command("/postnow casual-ig"), ("postnow", {"target": "casual-ig"})
        )
        self.assertEqual(parse_command("/postnow"), ("postnow", {"target": None}))

    def test_publish_distinct(self):
        self.assertEqual(parse_command("/publish"), ("publish", {}))

    def test_botname_suffix_stripped(self):
        self.assertEqual(parse_command("/status@my_bot"), ("status", {}))

    def test_non_command_none(self):
        self.assertIsNone(parse_command("hello there"))

    def test_unknown(self):
        self.assertEqual(parse_command("/frobnicate"), ("unknown", {}))


class ResolveTargetTest(unittest.TestCase):
    def test_explicit_valid(self):
        self.assertEqual(resolve_target("a", ["a", "b"]), ("a", None))

    def test_explicit_unknown_errors(self):
        tid, err = resolve_target("nope", ["a"])
        self.assertIsNone(tid)
        self.assertIn("nope", err)

    def test_omitted_single_defaults(self):
        self.assertEqual(resolve_target(None, ["a"]), ("a", None))

    def test_omitted_multiple_errors(self):
        tid, err = resolve_target(None, ["a", "b"])
        self.assertIsNone(tid)
        self.assertIn("a", err)
        self.assertIn("b", err)


class HandleMessageTest(unittest.TestCase):
    def setUp(self):
        self.dispatched = []
        self.deps = {
            "dispatch": self.dispatched.append,
            "build_status": lambda: "STATUS",
            "youtube": ["study-yt"],
            "instagram": ["casual-ig"],
        }

    def test_status(self):
        self.assertEqual(handle_message("/status", self.deps), "STATUS")

    def test_upload_defaults_to_single_yt(self):
        reply = handle_message("/upload 17:30", self.deps)
        self.assertEqual(
            self.dispatched,
            [{"target": "study-yt", "action": "upload", "upload_slot": "17:30"}],
        )
        self.assertIn("study-yt", reply)

    def test_postnow_defaults_to_single_ig(self):
        reply = handle_message("/postnow", self.deps)
        self.assertEqual(
            self.dispatched, [{"target": "casual-ig", "action": "postnow"}]
        )
        self.assertIn("casual-ig", reply)

    def test_publishnow_ambiguous_asks(self):
        self.deps["youtube"] = ["a-yt", "b-yt"]
        reply = handle_message("/publishnow", self.deps)
        self.assertEqual(self.dispatched, [])
        self.assertIn("a-yt", reply)

    def test_publish_plain_pass(self):
        handle_message("/publish", self.deps)
        self.assertEqual(self.dispatched, [{}])

    def test_unknown_gets_help(self):
        self.assertEqual(handle_message("/wat", self.deps), HELP_TEXT)


class AuthorizedTextsTest(unittest.TestCase):
    def test_filters_by_chat_id(self):
        updates = [
            {"message": {"chat": {"id": 111}, "text": "/status"}},
            {"message": {"chat": {"id": 222}, "text": "/upload"}},
            {"message": {"chat": {"id": 111}}},
        ]
        self.assertEqual(list(authorized_texts(updates, "111")), ["/status"])


if __name__ == "__main__":
    unittest.main()
