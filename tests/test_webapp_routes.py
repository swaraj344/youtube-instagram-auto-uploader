"""Route tests for the config web app (services patched out)."""

import unittest
from unittest.mock import patch

from config import ConfigError
import webapp.app as webapp_app


def client():
    app = webapp_app.create_app()
    app.config["TESTING"] = True
    return app.test_client()


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.client = client()

    def test_entity_list_renders(self):
        with patch.object(webapp_app.services, "list_entities", return_value=[
            {"id": "study-drive", "name": "Study videos", "google_connected": True,
             "has_folder": True, "used_by": ["study-yt"]},
        ]):
            resp = self.client.get("/sources")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Study videos", resp.data)

    def test_unknown_section_404s(self):
        self.assertEqual(self.client.get("/tiktok").status_code, 404)

    def test_new_form_renders_per_section(self):
        with patch.object(webapp_app.services, "list_entities", return_value=[]):
            resp = self.client.get("/youtube/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'name="upload_lead_hours"', resp.data)
        with patch.object(webapp_app.services, "list_entities", return_value=[]):
            resp = self.client.get("/sources/new")
        self.assertIn(b'name="drive_folder_id"', resp.data)
        self.assertNotIn(b'name="slots"', resp.data)

    def test_create_redirects_on_success(self):
        with patch.object(webapp_app.services, "upsert_entity") as upsert:
            resp = self.client.post("/instagram/new",
                                    data={"id": "memes-ig", "name": "M"})
        self.assertEqual(resp.status_code, 302)
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args[0][:2], ("instagram", "memes-ig"))

    def test_create_invalid_returns_400(self):
        with patch.object(webapp_app.services, "upsert_entity",
                          side_effect=ConfigError("bad id")), \
             patch.object(webapp_app.services, "list_entities", return_value=[]):
            resp = self.client.post("/sources/new", data={"id": "Bad Id", "name": "X"})
        self.assertEqual(resp.status_code, 400)

    def test_action_dispatches(self):
        with patch.object(webapp_app.services, "trigger_action") as trig:
            resp = self.client.post("/youtube/study-yt/action",
                                    data={"action": "upload", "upload_slot": "17:30"})
        self.assertEqual(resp.status_code, 302)
        trig.assert_called_once_with("study-yt", "upload", "17:30")

    def test_delete_referenced_source_flashes_error(self):
        with patch.object(webapp_app.services, "delete_entity",
                          side_effect=ConfigError("in use")):
            resp = self.client.post("/sources/study-drive/delete")
        self.assertEqual(resp.status_code, 302)  # redirect back with flash

    def test_toggle_source_404s(self):
        resp = self.client.post("/sources/study-drive/toggle")
        self.assertEqual(resp.status_code, 404)

    def test_migrate_post(self):
        with patch.object(webapp_app.services, "run_migration",
                          return_value="Converted 1 channel(s)") as mig:
            resp = self.client.post("/migrate")
        self.assertEqual(resp.status_code, 302)
        mig.assert_called_once()

    def test_dashboard_overview(self):
        with patch.object(webapp_app.services, "refresh_state", return_value=None), \
             patch.object(webapp_app.services, "list_entities", side_effect=lambda s: {
                 "sources": [{"id": "study-drive", "name": "Study videos",
                              "google_connected": True, "has_folder": True,
                              "used_by": ["study-yt", "casual-ig"]}],
                 "youtube": [{"id": "study-yt", "name": "Study", "source": "study-drive",
                              "enabled": True, "slots": ["17:30"], "next_slot": None,
                              "ready": True, "pending": []}],
                 "instagram": [{"id": "casual-ig", "name": "Casual", "source": "study-drive",
                                "enabled": True, "slots": ["17:30"], "next_slot": None,
                                "ready": True, "last_posted": "2026-08-17 17:30"}],
             }[s]), \
             patch.object(webapp_app.services, "deploy_status",
                          return_value={"config_dirty": False, "secrets_changed": False,
                                        "shared_changed": [], "clean": True}), \
             patch.object(webapp_app.services, "meta_token_days", return_value=40.0), \
             patch.object(webapp_app.services, "migration_available", return_value=False):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Study videos", resp.data)
        self.assertIn(b"casual-ig", resp.data)


if __name__ == "__main__":
    unittest.main()
