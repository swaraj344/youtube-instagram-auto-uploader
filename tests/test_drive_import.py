"""Tests for the shared-Drive-link -> own Drive import job in webapp.services."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from webapp import services
from tests.test_local_upload import FakeCreateRequest, InlineThread, seed


class FakeFiles:
    def __init__(self, drive):
        self._drive = drive

    def create(self, body=None, media_body=None, fields=None):
        self._drive.created_folders.append(body["name"])
        return FakeCreateRequest({"id": "new-folder-id"})

    def copy(self, fileId=None, body=None, fields=None):
        self._drive.copied.append((fileId, body["name"]))
        if fileId in self._drive.copy_errors:
            raise RuntimeError("copy disabled by owner")
        return FakeCreateRequest({"id": f"copy-{fileId}"})


class FakeDrive:
    def __init__(self):
        self.copied = []
        self.created_folders = []
        self.copy_errors = set()

    def files(self):
        return FakeFiles(self)


class ParseLinkTest(unittest.TestCase):
    def test_folder_url(self):
        self.assertEqual(
            services.parse_drive_folder_link(
                "https://drive.google.com/drive/folders/1YJef4Zu_wPG-9U8?usp=sharing"
            ),
            "1YJef4Zu_wPG-9U8",
        )

    def test_bare_id(self):
        self.assertEqual(
            services.parse_drive_folder_link("1YJef4ZuwPG9U85FgYxM2c4UoLURbqBx"),
            "1YJef4ZuwPG9U85FgYxM2c4UoLURbqBx",
        )

    def test_garbage_returns_none(self):
        self.assertIsNone(services.parse_drive_folder_link("not a link"))
        self.assertIsNone(services.parse_drive_folder_link(""))


class StartImportValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(services, "ROOT", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        services._upload_jobs.clear()
        seed(self.tmp.name)

    def test_rejects_bad_link(self):
        with self.assertRaises(RuntimeError):
            services.start_import("study-drive", "nope")

    def test_rejects_concurrent_job(self):
        services._upload_jobs["study-drive"] = {"status": "running"}
        with self.assertRaises(RuntimeError):
            services.start_import(
                "study-drive", "https://drive.google.com/drive/folders/abcdef123456"
            )

    def test_rejects_source_without_google_token(self):
        with open(os.path.join(self.tmp.name, "secrets", "pipeline_secrets.json"), "w") as fh:
            json.dump({"sources": {"study-drive": {"drive_folder_id": "f"}}}, fh)
        with self.assertRaises(RuntimeError):
            services.start_import(
                "study-drive", "https://drive.google.com/drive/folders/abcdef123456"
            )


class ImportJobTest(unittest.TestCase):
    SHARED = "sharedfolder123"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(services, "ROOT", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        services._upload_jobs.clear()
        self.drive = FakeDrive()

        def fake_list(drive, folder_id):
            if folder_id == self.SHARED:
                return [
                    {"id": "s1", "name": "a.mp4", "mimeType": "video/mp4"},
                    {"id": "s2", "name": "b.mp4", "mimeType": "video/mp4"},
                    {"id": "s3", "name": "existing.mp4", "mimeType": "video/mp4"},
                ]
            return [{"id": "x", "name": "existing.mp4", "mimeType": "video/mp4"}]

        for p in (
            patch.object(services.threading, "Thread", InlineThread),
            patch.object(services, "_drive_for", return_value=self.drive),
            patch("uploader.list_drive_videos", side_effect=fake_list),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _link(self):
        return f"https://drive.google.com/drive/folders/{self.SHARED}"

    def test_copies_new_skips_existing(self):
        seed(self.tmp.name)
        services.start_import("study-drive", self._link())
        job = services.upload_status("study-drive")
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["mode"], "import")
        self.assertEqual(job["total"], 3)
        self.assertEqual(job["uploaded"], 2)
        self.assertEqual(job["skipped"], 1)
        self.assertEqual(job["failed"], 0)
        self.assertEqual([c[0] for c in self.drive.copied], ["s1", "s2"])

    def test_copy_errors_counted_not_fatal(self):
        seed(self.tmp.name)
        self.drive.copy_errors.add("s2")
        services.start_import("study-drive", self._link())
        job = services.upload_status("study-drive")
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["uploaded"], 1)
        self.assertEqual(job["failed"], 1)
        self.assertIn("b.mp4: copy disabled by owner", job["errors"][0])

    def test_creates_dest_folder_when_missing(self):
        seed(self.tmp.name, with_folder_id=False)
        services.start_import("study-drive", self._link())
        job = services.upload_status("study-drive")
        self.assertEqual(job["status"], "done")
        self.assertEqual(self.drive.created_folders, ["Study videos"])
        secrets = json.load(
            open(os.path.join(self.tmp.name, "secrets", "pipeline_secrets.json"))
        )
        self.assertEqual(
            secrets["sources"]["study-drive"]["drive_folder_id"], "new-folder-id"
        )


if __name__ == "__main__":
    unittest.main()
