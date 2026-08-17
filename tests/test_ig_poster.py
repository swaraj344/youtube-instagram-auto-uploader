"""Tests for ig_poster scheduling ledger logic (pure, no network)."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from config import InstagramDest
from ig_poster import due_posts, occurrence_key

TZ = ZoneInfo("Asia/Kolkata")


def make_ig(**over):
    base = dict(
        id="casual-ig", name="Casual", source="study-drive",
        slots=["17:30", "21:30"], timezone="Asia/Kolkata",
    )
    base.update(over)
    return InstagramDest(**base)


class OccurrenceKeyTest(unittest.TestCase):
    def test_format(self):
        occ = datetime(2026, 8, 17, 17, 30, tzinfo=TZ)
        self.assertEqual(occurrence_key(occ), "2026-08-17 17:30")


class DuePostsTest(unittest.TestCase):
    def test_slot_not_reached_yet_is_not_due_today(self):
        # 16:00 — today's slots haven't happened; yesterday's are posted.
        now = datetime(2026, 8, 17, 16, 0, tzinfo=TZ)
        log = {"2026-08-16 17:30": {"status": "posted"},
               "2026-08-16 21:30": {"status": "posted"}}
        self.assertEqual(due_posts(make_ig(), log, now), [])

    def test_passed_slot_is_due(self):
        now = datetime(2026, 8, 17, 18, 0, tzinfo=TZ)
        due = due_posts(make_ig(), {}, now)
        self.assertIn(
            (datetime(2026, 8, 17, 17, 30, tzinfo=TZ), "17:30"), due
        )

    def test_posted_occurrence_skipped(self):
        now = datetime(2026, 8, 17, 18, 0, tzinfo=TZ)
        log = {"2026-08-17 17:30": {"status": "posted"},
               "2026-08-16 21:30": {"status": "posted"}}
        self.assertEqual(due_posts(make_ig(), log, now), [])

    def test_no_video_occurrence_skipped(self):
        now = datetime(2026, 8, 17, 18, 0, tzinfo=TZ)
        log = {"2026-08-17 17:30": {"status": "no_video"},
               "2026-08-16 21:30": {"status": "posted"}}
        self.assertEqual(due_posts(make_ig(), log, now), [])

    def test_failed_occurrence_retries(self):
        now = datetime(2026, 8, 17, 18, 0, tzinfo=TZ)
        log = {"2026-08-17 17:30": {"status": "failed", "failure_notified": True},
               "2026-08-16 21:30": {"status": "posted"}}
        due = due_posts(make_ig(), log, now)
        self.assertEqual(due, [(datetime(2026, 8, 17, 17, 30, tzinfo=TZ), "17:30")])

    def test_occurrence_older_than_24h_lapses(self):
        now = datetime(2026, 8, 18, 18, 0, tzinfo=TZ)
        due = due_posts(make_ig(slots=["17:30"]), {}, now)
        self.assertEqual(due, [(datetime(2026, 8, 18, 17, 30, tzinfo=TZ), "17:30")])

    def test_exactly_at_occurrence_is_due(self):
        now = datetime(2026, 8, 17, 17, 30, tzinfo=TZ)
        due = due_posts(make_ig(slots=["17:30"]), {}, now)
        self.assertEqual(due, [(now, "17:30")])


if __name__ == "__main__":
    unittest.main()
