"""
Shared Google OAuth credentials helper.

get_credentials() builds Credentials from a token-info dict (the contents of
a token.json) taken from the channel's secret layer. It refreshes expired
access tokens IN MEMORY only — in CI the refreshed access token lives for the
run; the durable refresh token in the secrets blob never changes.

TOKEN_FILE and SCOPES remain exported for oauth_setup.py (headless fallback
that still writes a local token.json for the web app's legacy import).
"""

from __future__ import annotations

import os

from google.oauth2.credentials import Credentials

TOKEN_FILE = os.environ.get("TOKEN_FILE", "token.json")

# All Google API scopes needed by the pipeline.
# Change here propagates everywhere automatically.
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def get_credentials(token_info: dict) -> Credentials:
    """
    Build Credentials from *token_info* (token.json contents), refreshing
    in memory if the access token has expired.

    Raises:
        ValueError: if token_info is missing required fields.
        google.auth.exceptions.RefreshError: if the refresh token is invalid.
    """
    if not token_info:
        raise ValueError("No Google token configured for this channel")

    creds = Credentials.from_authorized_user_info(token_info, SCOPES)

    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request  # lazy import

        creds.refresh(Request())

    return creds
