"""Route tests for the config web app (services patched out; no templates needed)."""

import unittest
from unittest.mock import patch

from config import ConfigError
import webapp.app as webapp_app


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.app = webapp_app.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_ig_accounts_returns_json(self):
        with patch.object(
            webapp_app.services,
            "list_ig_accounts",
            return_value=[{"id": "1", "username": "u", "page": "P"}],
        ):
            resp = self.client.get("/api/ig-accounts")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()[0]["username"], "u")

    def test_ig_accounts_error_becomes_502(self):
        with patch.object(
            webapp_app.services, "list_ig_accounts", side_effect=RuntimeError("no token")
        ):
            resp = self.client.get("/api/ig-accounts")
        self.assertEqual(resp.status_code, 502)
        self.assertIn("no token", resp.get_json()["error"])

    def test_create_channel_redirects_on_success(self):
        with patch.object(webapp_app.services, "upsert_channel") as upsert:
            resp = self.client.post(
                "/channels/new", data={"slug": "memes", "display_name": "M"}
            )
        self.assertEqual(resp.status_code, 302)
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args[0][0], "memes")

    def test_create_channel_invalid_returns_400(self):
        with patch.object(
            webapp_app.services, "upsert_channel", side_effect=ConfigError("bad slug")
        ):
            resp = self.client.post(
                "/channels/new", data={"slug": "Bad Slug", "display_name": "M"}
            )
        self.assertEqual(resp.status_code, 400)

    def test_channel_action_dispatches(self):
        with patch.object(webapp_app.services, "trigger_action") as trig:
            resp = self.client.post(
                "/channels/study/action",
                data={"action": "upload", "upload_slot": "17:30"},
            )
        self.assertEqual(resp.status_code, 302)
        trig.assert_called_once_with("study", "upload", "17:30")

    def test_deploy_post_runs_deploy(self):
        with patch.object(
            webapp_app.services, "deploy", return_value=["channels.json: pushed"]
        ) as dep:
            resp = self.client.post("/deploy")
        self.assertEqual(resp.status_code, 302)
        dep.assert_called_once()

    def test_import_post_flashes_summary(self):
        with patch.object(
            webapp_app.services, "run_legacy_import", return_value="Imported"
        ) as imp:
            resp = self.client.post("/import")
        self.assertEqual(resp.status_code, 302)
        imp.assert_called_once()


class TemplateRenderTest(unittest.TestCase):
    def setUp(self):
        self.app = webapp_app.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _fake_status(self):
        from config import Channel

        ch = Channel(
            slug="study",
            display_name="Study Motivation",
            slots=["17:30", "21:30"],
            drive_folder_id="d",
            ig_business_account_id="ig",
            google_token={"refresh_token": "r"},
        )
        return [
            {
                "ch": ch,
                "pending": [
                    {"slot": "17:30", "go_live_at": "2026-08-16T17:30:00+05:30",
                     "youtube_video_id": "abc123", "published": False}
                ],
                "recent": [],
                "next_slot": None,
                "has_secrets": True,
            }
        ]

    def test_dashboard_renders_channel_card(self):
        with patch.object(webapp_app.services, "refresh_state", return_value=None), \
             patch.object(webapp_app.services, "channels_with_status",
                          return_value=self._fake_status()), \
             patch.object(webapp_app.services, "deploy_status",
                          return_value={"config_dirty": False, "secrets_changed": False,
                                        "shared_changed": [], "clean": True}), \
             patch.object(webapp_app.services, "meta_token_days", return_value=42.0), \
             patch.object(webapp_app.services, "legacy_import_available",
                          return_value=False):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Study Motivation", resp.data)
        self.assertIn(b"17:30", resp.data)

    def test_new_channel_form_renders(self):
        resp = self.client.get("/channels/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'name="slug"', resp.data)
        self.assertIn(b'name="slots"', resp.data)

    def test_settings_renders_shared_keys(self):
        with patch.object(webapp_app.services, "shared_settings",
                          return_value={"META_ACCESS_TOKEN": "x", "GROQ_API_KEY": "",
                                        "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            resp = self.client.get("/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"META_ACCESS_TOKEN", resp.data)

    def test_deploy_preview_renders_diff(self):
        with patch.object(webapp_app.services, "deploy_plan",
                          return_value={"status": {"config_dirty": True,
                                                   "secrets_changed": True,
                                                   "shared_changed": ["GROQ_API_KEY"],
                                                   "clean": False},
                                        "config_diff": "+ memes"}):
            resp = self.client.get("/deploy")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"+ memes", resp.data)


if __name__ == "__main__":
    unittest.main()
