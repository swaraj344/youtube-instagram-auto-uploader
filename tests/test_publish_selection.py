"""Tests for publish_scheduled.select_due (schedule vs force-next selection)."""

import os
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("DRIVE_FOLDER_ID", "test-folder")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from publish_scheduled import select_due

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 8, 15, 18, 0, tzinfo=TZ)

PAST = {"slot": "A", "go_live_at": "2026-08-15T17:30:00+05:30", "published": False}
FUTURE = {"slot": "B", "go_live_at": "2026-08-15T21:30:00+05:30", "published": False}
DONE = {"slot": "A", "go_live_at": "2026-08-14T17:30:00+05:30", "published": True}


class SelectDueTest(unittest.TestCase):
    def test_normal_mode_returns_only_unpublished_past_items(self):
        self.assertEqual(select_due([DONE, PAST, FUTURE], NOW), [PAST])

    def test_normal_mode_empty_when_nothing_due(self):
        self.assertEqual(select_due([DONE, FUTURE], NOW), [])

    def test_force_next_returns_earliest_unpublished_even_if_future(self):
        self.assertEqual(select_due([DONE, FUTURE], NOW, force_next=True), [FUTURE])

    def test_force_next_prefers_earliest_by_go_live_time(self):
        self.assertEqual(
            select_due([FUTURE, PAST, DONE], NOW, force_next=True), [PAST]
        )

    def test_force_next_empty_when_all_published(self):
        self.assertEqual(select_due([DONE], NOW, force_next=True), [])


if __name__ == "__main__":
    unittest.main()
