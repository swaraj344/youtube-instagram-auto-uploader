"""Tests for check_token_expiry.py day math and warning threshold."""

import os
import unittest

os.environ.setdefault("DRIVE_FOLDER_ID", "test-folder")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from check_token_expiry import days_left, should_warn

DAY = 86400


class DaysLeftTest(unittest.TestCase):
    def test_three_days_out(self):
        self.assertAlmostEqual(days_left(10 * DAY, 7 * DAY), 3.0)

    def test_never_expiring_token_returns_none(self):
        self.assertIsNone(days_left(0, 7 * DAY))
        self.assertIsNone(days_left(None, 7 * DAY))

    def test_already_expired_is_negative(self):
        self.assertLess(days_left(5 * DAY, 7 * DAY), 0)


class ShouldWarnTest(unittest.TestCase):
    def test_warns_at_and_below_seven_days(self):
        self.assertTrue(should_warn(7.0))
        self.assertTrue(should_warn(1.5))
        self.assertTrue(should_warn(-2.0))

    def test_quiet_above_seven_days_and_for_no_expiry(self):
        self.assertFalse(should_warn(7.1))
        self.assertFalse(should_warn(45.0))
        self.assertFalse(should_warn(None))


if __name__ == "__main__":
    unittest.main()
