"""Tests for instagram_uploader argument/env validation (no network)."""

import os
import unittest

from instagram_uploader import publish_reel


class PublishReelValidationTest(unittest.TestCase):
    def test_empty_ig_user_id_raises(self):
        os.environ["META_ACCESS_TOKEN"] = "t"
        self.addCleanup(os.environ.pop, "META_ACCESS_TOKEN", None)
        with self.assertRaises(ValueError):
            publish_reel("", "https://example.com/v.mp4", "caption")

    def test_missing_meta_token_raises(self):
        os.environ.pop("META_ACCESS_TOKEN", None)
        with self.assertRaises(ValueError):
            publish_reel("178414", "https://example.com/v.mp4", "caption")


if __name__ == "__main__":
    unittest.main()
