"""Tests for the local-folder -> Drive upload job in webapp.services."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from webapp import services


class InlineThread:
    """Runs the thread target synchronously so tests are deterministic."""

    def __init__(self, target=None, args=(), daemon=None):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


class FakeCreateRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response

    def next_chunk(self):
        return None, self._response


class FakeFiles:
    def __init__(self, drive):
        self._drive = drive

    def create(self, body=None, media_body=None, fields=None):
        if media_body is None:
            # folder creation
            self._drive.created_folders.append(body["name"])
            return FakeCreateRequest({"id": "new-folder-id"})
        self._drive.uploaded.append(body["name"])
        return FakeCreateRequest({"id": f"file-{len(self._drive.uploaded)}"})


class FakeDrive:
    def __init__(self):
        self.uploaded = []
        self.created_folders = []

    def files(self):
        return FakeFiles(self)


def seed(root, with_folder_id=True):
    with open(os.path.join(root, "config.json"), "w") as fh:
        json.dump({"sources": [{"id": "study-drive", "name": "Study videos"}],
                   "youtube": [], "instagram": []}, fh)
    os.makedirs(os.path.join(root, "secrets"), exist_ok=True)
    sec = {"google_token": {"refresh_token": "r"}}
    if with_folder_id:
        sec["drive_folder_id"] = "folder1"
    with open(os.path.join(root, "secrets", "pipeline_secrets.json"), "w") as fh:
        json.dump({"sources": {"study-drive": sec}}, fh)


def make_videos(folder, names):
    os.makedirs(folder, exist_ok=True)
    for name in names:
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(b"\x00" * 64)


class StartUploadValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(services, "ROOT", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        services._upload_jobs.clear()
        seed(self.tmp.name)

    def test_rejects_missing_folder(self):
        with self.assertRaises(RuntimeError):
            services.start_upload("study-drive", "/no/such/folder")

    def test_rejects_source_without_google_token(self):
        with open(os.path.join(self.tmp.name, "secrets", "pipeline_secrets.json"), "w") as fh:
            json.dump({"sources": {"study-drive": {"drive_folder_id": "f"}}}, fh)
        videos = os.path.join(self.tmp.name, "vids")
        make_videos(videos, ["a.mp4"])
        with self.assertRaises(RuntimeError):
            services.start_upload("study-drive", videos)

    def test_rejects_folder_without_videos(self):
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        with open(os.path.join(empty, "notes.txt"), "w") as fh:
            fh.write("x")
        with self.assertRaises(RuntimeError):
            services.start_upload("study-drive", empty)

    def test_rejects_second_concurrent_upload(self):
        services._upload_jobs["study-drive"] = {"status": "running"}
        videos = os.path.join(self.tmp.name, "vids")
        make_videos(videos, ["a.mp4"])
        with self.assertRaises(RuntimeError):
            services.start_upload("study-drive", videos)


class UploadJobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(services, "ROOT", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        services._upload_jobs.clear()
        self.drive = FakeDrive()
        for p in (
            patch.object(services.threading, "Thread", InlineThread),
            patch.object(services, "_drive_for", return_value=self.drive),
            patch("uploader.list_drive_videos",
                  return_value=[{"id": "x", "name": "existing.mp4", "mimeType": "video/mp4"}]),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_uploads_new_files_skips_existing_ignores_nonvideo(self):
        seed(self.tmp.name)
        videos = os.path.join(self.tmp.name, "vids")
        make_videos(videos, ["existing.mp4", "new1.mp4", "new2.MOV"])
        with open(os.path.join(videos, "readme.txt"), "w") as fh:
            fh.write("x")
        services.start_upload("study-drive", videos)
        job = services.upload_status("study-drive")
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["total"], 3)
        self.assertEqual(job["done"], 3)
        self.assertEqual(job["uploaded"], 2)
        self.assertEqual(job["skipped"], 1)
        self.assertEqual(job["failed"], 0)
        self.assertEqual(sorted(self.drive.uploaded), ["new1.mp4", "new2.MOV"])

    def test_creates_drive_folder_when_source_has_none(self):
        seed(self.tmp.name, with_folder_id=False)
        videos = os.path.join(self.tmp.name, "vids")
        make_videos(videos, ["a.mp4"])
        services.start_upload("study-drive", videos)
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
