"""Tests for run_pipeline scheduling decisions (pure logic, no network)."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from config import Channel
from run_pipeline import (
    already_queued,
    due_uploads,
    next_slot_occurrence,
    upload_window_open,
)

TZ = ZoneInfo("Asia/Kolkata")


def make_channel(**over):
    base = dict(
        slug="study",
        display_name="Study",
        slots=["17:30", "21:30"],
        upload_lead_hours=8,
        timezone="Asia/Kolkata",
    )
    base.update(over)
    return Channel(**base)


class NextSlotOccurrenceTest(unittest.TestCase):
    def test_slot_later_today(self):
        now = datetime(2026, 8, 16, 10, 0, tzinfo=TZ)
        self.assertEqual(
            next_slot_occurrence("17:30", TZ, now),
            datetime(2026, 8, 16, 17, 30, tzinfo=TZ),
        )

    def test_slot_already_past_rolls_to_tomorrow(self):
        now = datetime(2026, 8, 16, 18, 0, tzinfo=TZ)
        self.assertEqual(
            next_slot_occurrence("17:30", TZ, now),
            datetime(2026, 8, 17, 17, 30, tzinfo=TZ),
        )

    def test_exactly_at_slot_rolls_to_tomorrow(self):
        now = datetime(2026, 8, 16, 17, 30, tzinfo=TZ)
        self.assertEqual(
            next_slot_occurrence("17:30", TZ, now),
            datetime(2026, 8, 17, 17, 30, tzinfo=TZ),
        )


class UploadWindowTest(unittest.TestCase):
    GO_LIVE = datetime(2026, 8, 16, 17, 30, tzinfo=TZ)

    def test_open_exactly_at_lead_boundary(self):
        now = datetime(2026, 8, 16, 9, 30, tzinfo=TZ)
        self.assertTrue(upload_window_open(self.GO_LIVE, now, 8))

    def test_closed_before_lead_boundary(self):
        now = datetime(2026, 8, 16, 9, 29, tzinfo=TZ)
        self.assertFalse(upload_window_open(self.GO_LIVE, now, 8))

    def test_closed_at_go_live(self):
        self.assertFalse(upload_window_open(self.GO_LIVE, self.GO_LIVE, 8))


class AlreadyQueuedTest(unittest.TestCase):
    GO_LIVE = datetime(2026, 8, 16, 17, 30, tzinfo=TZ)

    def test_matching_slot_and_time_is_queued(self):
        queue = [{"slot": "17:30", "go_live_at": self.GO_LIVE.isoformat(), "published": False}]
        self.assertTrue(already_queued(queue, "17:30", self.GO_LIVE))

    def test_published_entries_still_count(self):
        queue = [{"slot": "17:30", "go_live_at": self.GO_LIVE.isoformat(), "published": True}]
        self.assertTrue(already_queued(queue, "17:30", self.GO_LIVE))

    def test_different_day_not_queued(self):
        queue = [{"slot": "17:30", "go_live_at": "2026-08-15T17:30:00+05:30"}]
        self.assertFalse(already_queued(queue, "17:30", self.GO_LIVE))


class DueUploadsTest(unittest.TestCase):
    def test_only_slot_within_lead_window_is_due(self):
        # 10:00 — 17:30 window opened at 09:30; 21:30 window opens at 13:30.
        now = datetime(2026, 8, 16, 10, 0, tzinfo=TZ)
        due = due_uploads(make_channel(), [], now)
        self.assertEqual(due, [("17:30", datetime(2026, 8, 16, 17, 30, tzinfo=TZ))])

    def test_both_slots_due_in_afternoon(self):
        now = datetime(2026, 8, 16, 14, 0, tzinfo=TZ)
        due = due_uploads(make_channel(), [], now)
        self.assertEqual([slot for slot, _ in due], ["17:30", "21:30"])

    def test_queued_slot_is_suppressed(self):
        now = datetime(2026, 8, 16, 14, 0, tzinfo=TZ)
        queue = [{
            "slot": "17:30",
            "go_live_at": datetime(2026, 8, 16, 17, 30, tzinfo=TZ).isoformat(),
            "published": False,
        }]
        due = due_uploads(make_channel(), queue, now)
        self.assertEqual([slot for slot, _ in due], ["21:30"])


if __name__ == "__main__":
    unittest.main()
