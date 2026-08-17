"""
Pipeline configuration: sources, YouTube destinations, Instagram destinations.

Two layers, merged by load_config():

  config.json                        committed, non-secret entity config
  PIPELINE_SECRETS_JSON env var      secrets in CI (GitHub Actions secret), OR
  secrets/pipeline_secrets.json      secrets locally (gitignored)

The secrets blob mirrors config.json's three sections, keyed by id:
  {"sources": {id: {...}}, "youtube": {id: {...}}, "instagram": {id: {...}}}

Sources own the Google login used for every Drive operation (list, download,
share, revoke). YouTube destinations hold their own YouTube login. Instagram
destinations hold only the IG business account id — the Meta token is shared
via META_ACCESS_TOKEN.

An entity with missing secrets still loads (has_secrets() False);
run_pipeline.py skips it with a one-time Telegram warning.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

CONFIG_FILE = "config.json"
SECRETS_FILE = os.path.join("secrets", "pipeline_secrets.json")

_ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    """Raised when config.json or the secrets blob is invalid."""


@dataclass
class Source:
    id: str
    name: str
    drive_folder_id: str | None = None
    google_token: dict | None = None

    def has_secrets(self) -> bool:
        return bool(self.drive_folder_id and self.google_token)


@dataclass
class YouTubeDest:
    id: str
    name: str
    source: str
    enabled: bool = True
    timezone: str = "Asia/Kolkata"
    slots: list = field(default_factory=list)
    upload_lead_hours: int = 8
    content_description: str = "short-form video content"
    category_id: str = "22"
    google_token: dict | None = None
    google_client_secret: dict | None = None

    kind = "youtube"

    @property
    def state_dir(self) -> str:
        return os.path.join("state", self.id)

    @property
    def queue_file(self) -> str:
        return os.path.join(self.state_dir, "publish_queue.json")

    @property
    def log_file(self) -> str:
        return os.path.join(self.state_dir, "processed_log.json")

    def has_secrets(self) -> bool:
        return bool(self.google_token)


@dataclass
class InstagramDest:
    id: str
    name: str
    source: str
    enabled: bool = True
    timezone: str = "Asia/Kolkata"
    slots: list = field(default_factory=list)
    content_description: str = "short-form video content"
    ig_business_account_id: str | None = None

    kind = "instagram"

    @property
    def state_dir(self) -> str:
        return os.path.join("state", self.id)

    @property
    def log_file(self) -> str:
        return os.path.join(self.state_dir, "processed_log.json")

    @property
    def slot_log_file(self) -> str:
        return os.path.join(self.state_dir, "slot_log.json")

    def has_secrets(self) -> bool:
        return bool(self.ig_business_account_id)


@dataclass
class PipelineConfig:
    sources: dict
    youtube: list
    instagram: list

    def source_of(self, dest):
        return self.sources[dest.source]


def _validate_common(raw: dict, section: str, seen: set) -> None:
    eid = raw.get("id", "")
    if not _ID_RE.match(eid):
        raise ConfigError(f"{section}: invalid id {eid!r} (lowercase a-z, 0-9, hyphens)")
    if eid in seen:
        raise ConfigError(f"Duplicate id {eid!r} (ids are unique across ALL sections)")
    seen.add(eid)
    if not raw.get("name"):
        raise ConfigError(f"{eid}: name is required")


def _validate_dest(raw: dict, source_ids: set) -> None:
    eid = raw["id"]
    if raw.get("source") not in source_ids:
        raise ConfigError(f"{eid}: unknown source {raw.get('source')!r}")
    slots = raw.get("slots")
    if not isinstance(slots, list) or not slots:
        raise ConfigError(f"{eid}: slots must be a non-empty list")
    for slot in slots:
        if not isinstance(slot, str) or not _TIME_RE.match(slot):
            raise ConfigError(f"{eid}: invalid slot time {slot!r} (use HH:MM)")
    if len(set(slots)) != len(slots):
        raise ConfigError(f"{eid}: duplicate slot times")
    tz = raw.get("timezone", "Asia/Kolkata")
    try:
        ZoneInfo(tz)
    except Exception:
        raise ConfigError(f"{eid}: unknown timezone {tz!r}")


def validate_config(data: dict) -> None:
    """Raise ConfigError if the config.json structure is invalid."""
    if not isinstance(data, dict):
        raise ConfigError("config.json must be a JSON object")
    for section in ("sources", "youtube", "instagram"):
        if not isinstance(data.get(section, []), list):
            raise ConfigError(f"{section} must be a list")

    seen: set = set()
    for raw in data.get("sources", []):
        _validate_common(raw, "sources", seen)
    source_ids = {s["id"] for s in data.get("sources", [])}

    for raw in data.get("youtube", []):
        _validate_common(raw, "youtube", seen)
        _validate_dest(raw, source_ids)
        lead = raw.get("upload_lead_hours", 8)
        if not isinstance(lead, int) or isinstance(lead, bool) or lead < 1:
            raise ConfigError(f"{raw['id']}: upload_lead_hours must be a positive integer")

    for raw in data.get("instagram", []):
        _validate_common(raw, "instagram", seen)
        _validate_dest(raw, source_ids)


def load_secrets() -> dict:
    """Secrets blob: PIPELINE_SECRETS_JSON env var (CI) or local secrets file."""
    raw = os.environ.get("PIPELINE_SECRETS_JSON", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"PIPELINE_SECRETS_JSON is not valid JSON: {exc}")
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def load_config(config_path: str = CONFIG_FILE, secrets: dict | None = None) -> PipelineConfig:
    """Load, validate, and merge both layers into a PipelineConfig."""
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

    def sec(section, eid):
        return (secrets.get(section) or {}).get(eid) or {}

    sources = {}
    for raw in data.get("sources", []):
        s = sec("sources", raw["id"])
        sources[raw["id"]] = Source(
            id=raw["id"],
            name=raw["name"],
            drive_folder_id=s.get("drive_folder_id"),
            google_token=s.get("google_token"),
        )

    youtube = []
    for raw in data.get("youtube", []):
        s = sec("youtube", raw["id"])
        youtube.append(YouTubeDest(
            id=raw["id"],
            name=raw["name"],
            source=raw["source"],
            enabled=bool(raw.get("enabled", True)),
            timezone=raw.get("timezone", "Asia/Kolkata"),
            slots=list(raw["slots"]),
            upload_lead_hours=raw.get("upload_lead_hours", 8),
            content_description=raw.get("content_description", "short-form video content"),
            category_id=str(raw.get("category_id", "22")),
            google_token=s.get("google_token"),
            google_client_secret=s.get("google_client_secret"),
        ))

    instagram = []
    for raw in data.get("instagram", []):
        s = sec("instagram", raw["id"])
        instagram.append(InstagramDest(
            id=raw["id"],
            name=raw["name"],
            source=raw["source"],
            enabled=bool(raw.get("enabled", True)),
            timezone=raw.get("timezone", "Asia/Kolkata"),
            slots=list(raw["slots"]),
            content_description=raw.get("content_description", "short-form video content"),
            ig_business_account_id=s.get("ig_business_account_id"),
        ))

    return PipelineConfig(sources=sources, youtube=youtube, instagram=instagram)
