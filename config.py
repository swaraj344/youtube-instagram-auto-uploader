"""
Channel configuration for the multi-channel pipeline.

Two layers, merged into Channel objects by load_channels():

  channels.json                      committed, non-secret per-channel config
  CHANNELS_SECRETS_JSON env var      secrets in CI (GitHub Actions secret), OR
  secrets/channels_secrets.json      secrets locally (gitignored), keyed by slug

An enabled channel with missing secrets still loads (has_secrets() is False);
run_pipeline.py skips it with a one-time Telegram warning rather than
crashing the whole run.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

CHANNELS_FILE = "channels.json"
SECRETS_FILE = os.path.join("secrets", "channels_secrets.json")

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    """Raised when channels.json or the secrets blob is invalid."""


@dataclass
class Channel:
    slug: str
    display_name: str
    enabled: bool = True
    timezone: str = "Asia/Kolkata"
    slots: list = field(default_factory=list)
    upload_lead_hours: int = 8
    content_description: str = "short-form video content"
    youtube_category_id: str = "22"
    # Secret layer (None until secrets are configured for this slug)
    drive_folder_id: str | None = None
    ig_business_account_id: str | None = None
    google_token: dict | None = None
    google_client_secret: dict | None = None

    @property
    def state_dir(self) -> str:
        return os.path.join("state", self.slug)

    @property
    def queue_file(self) -> str:
        return os.path.join(self.state_dir, "publish_queue.json")

    @property
    def log_file(self) -> str:
        return os.path.join(self.state_dir, "processed_log.json")

    def has_secrets(self) -> bool:
        return bool(
            self.drive_folder_id and self.ig_business_account_id and self.google_token
        )


def validate_config(data: dict) -> None:
    """Raise ConfigError if the channels.json structure is invalid."""
    if not isinstance(data, dict) or not isinstance(data.get("channels"), list):
        raise ConfigError('channels.json must be {"channels": [...]}')
    seen = set()
    for raw in data["channels"]:
        slug = raw.get("slug", "")
        if not _SLUG_RE.match(slug):
            raise ConfigError(
                f"Invalid slug {slug!r} (lowercase a-z, 0-9, hyphens only)"
            )
        if slug in seen:
            raise ConfigError(f"Duplicate slug {slug!r}")
        seen.add(slug)
        if not raw.get("display_name"):
            raise ConfigError(f"{slug}: display_name is required")
        slots = raw.get("slots")
        if not isinstance(slots, list) or not slots:
            raise ConfigError(f"{slug}: slots must be a non-empty list")
        for slot in slots:
            if not isinstance(slot, str) or not _TIME_RE.match(slot):
                raise ConfigError(f"{slug}: invalid slot time {slot!r} (use HH:MM)")
        if len(set(slots)) != len(slots):
            raise ConfigError(f"{slug}: duplicate slot times")
        lead = raw.get("upload_lead_hours", 8)
        if not isinstance(lead, int) or isinstance(lead, bool) or lead < 1:
            raise ConfigError(f"{slug}: upload_lead_hours must be a positive integer")
        tz = raw.get("timezone", "Asia/Kolkata")
        try:
            ZoneInfo(tz)
        except Exception:
            raise ConfigError(f"{slug}: unknown timezone {tz!r}")


def load_secrets() -> dict:
    """Secrets blob: CHANNELS_SECRETS_JSON env var (CI) or local secrets file."""
    raw = os.environ.get("CHANNELS_SECRETS_JSON", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"CHANNELS_SECRETS_JSON is not valid JSON: {exc}")
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def load_channels(
    config_path: str = CHANNELS_FILE, secrets: dict | None = None
) -> list[Channel]:
    """Load, validate, and merge both config layers."""
    if not os.path.exists(config_path):
        raise ConfigError(f"{config_path} not found")
    with open(config_path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{config_path} is not valid JSON: {exc}")
    validate_config(data)
    if secrets is None:
        secrets = load_secrets()

    channels = []
    for raw in data["channels"]:
        sec = secrets.get(raw["slug"]) or {}
        channels.append(
            Channel(
                slug=raw["slug"],
                display_name=raw["display_name"],
                enabled=bool(raw.get("enabled", True)),
                timezone=raw.get("timezone", "Asia/Kolkata"),
                slots=list(raw["slots"]),
                upload_lead_hours=raw.get("upload_lead_hours", 8),
                content_description=raw.get(
                    "content_description", "short-form video content"
                ),
                youtube_category_id=str(raw.get("youtube_category_id", "22")),
                drive_folder_id=sec.get("drive_folder_id"),
                ig_business_account_id=sec.get("ig_business_account_id"),
                google_token=sec.get("google_token"),
                google_client_secret=sec.get("google_client_secret"),
            )
        )
    return channels
