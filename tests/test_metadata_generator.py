"""Tests for metadata_generator prompt construction (no network)."""

import unittest

from metadata_generator import _system_prompt


class SystemPromptTest(unittest.TestCase):
    def test_prompt_mentions_content_description(self):
        prompt = _system_prompt("cooking tutorials")
        self.assertIn("cooking tutorials", prompt)

    def test_prompt_demands_required_json_keys(self):
        prompt = _system_prompt("anything")
        for key in ("title", "description", "tags", "ig_caption"):
            self.assertIn(key, prompt)


if __name__ == "__main__":
    unittest.main()
