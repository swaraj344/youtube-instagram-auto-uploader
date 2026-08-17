"""Tests for auth.get_credentials (token-dict based, no disk IO)."""

import unittest

from auth import SCOPES, get_credentials


def token_info():
    return {
        "client_id": "cid.apps.googleusercontent.com",
        "client_secret": "csecret",
        "refresh_token": "rtoken",
        "token": "atoken",
        # Far-future expiry keeps the token "fresh" so no network refresh
        # happens in this unit test (this google-auth version treats a
        # missing expiry as expired).
        "expiry": "2099-01-01T00:00:00Z",
    }


class GetCredentialsTest(unittest.TestCase):
    def test_builds_credentials_from_dict(self):
        creds = get_credentials(token_info())
        self.assertEqual(creds.token, "atoken")
        self.assertEqual(creds.refresh_token, "rtoken")
        # No expiry in the dict -> not expired -> no network refresh attempted.
        self.assertFalse(creds.expired)
        self.assertEqual(sorted(creds.scopes), sorted(SCOPES))

    def test_empty_token_info_raises(self):
        with self.assertRaises(ValueError):
            get_credentials({})


if __name__ == "__main__":
    unittest.main()
