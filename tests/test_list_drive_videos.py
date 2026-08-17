"""Tests for Drive folder listing in uploader.py."""

import unittest

from uploader import list_drive_videos


class _FakeListRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class _FakeFiles:
    """Fake drive.files() that serves results in pages via nextPageToken."""

    def __init__(self, pages):
        self._pages = pages

    def list(self, **kwargs):
        token = kwargs.get("pageToken")
        index = int(token) if token else 0
        page = dict(self._pages[index])
        if index + 1 < len(self._pages):
            page["nextPageToken"] = str(index + 1)
        return _FakeListRequest(page)


class _FakeDrive:
    def __init__(self, pages):
        self._files = _FakeFiles(pages)

    def files(self):
        return self._files


class ListDriveVideosTest(unittest.TestCase):
    def test_collects_videos_across_multiple_pages(self):
        pages = [
            {"files": [
                {"id": "1", "name": "a.mp4", "mimeType": "video/mp4"},
                {"id": "2", "name": "notes.txt", "mimeType": "text/plain"},
            ]},
            {"files": [
                {"id": "3", "name": "b.mp4", "mimeType": "video/mp4"},
            ]},
        ]
        videos = list_drive_videos(_FakeDrive(pages), "test-folder")
        self.assertEqual([v["id"] for v in videos], ["1", "3"])


if __name__ == "__main__":
    unittest.main()
