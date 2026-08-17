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
        ), patch.object(webapp_app, "render_template", return_value="form"):
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


if __name__ == "__main__":
    unittest.main()
