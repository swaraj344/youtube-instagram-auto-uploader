# Multi-Channel Pipeline + Config Web App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run N independent niche channels (Drive folder → YouTube channel → IG account each) from one repo via a self-scheduling dispatcher, configured through a local Flask web app.

**Architecture:** Non-secret config lives in committed `channels.json`; per-channel secrets live in one `CHANNELS_SECRETS_JSON` GitHub secret (locally `secrets/channels_secrets.json`, gitignored). A single 15-minute cron runs `run_pipeline.py`, which decides per channel what uploads/publishes are due. The web app edits local config and syncs to GitHub via `git`/`gh` on Deploy.

**Tech Stack:** Python (3.9-compatible), Flask + Jinja (no build step), google-api-python-client, Meta Graph API, GitHub Actions, `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-08-16-multi-channel-design.md`

## Global Constraints

- All commits go to branch `multi-channel`. NEVER push to `main`. (`main`'s crons keep the live pipeline running; GitHub only runs schedules from the default branch.)
- Code must run on Python 3.9 (local venv) AND 3.12 (CI): every new/edited module starts with `from __future__ import annotations`; no runtime-evaluated `X | Y` unions outside annotations.
- Public repo: Drive folder IDs, IG account IDs, and all tokens must never be committed. Secret layer lives only in `secrets/` (gitignored) or GitHub secrets.
- Tests are `unittest` style in `tests/`. Run: `venv/bin/python -m unittest discover -s tests -v` (from repo root). Run the full suite before every commit.
- Slot identifiers are `"HH:MM"` strings everywhere (the old `A`/`B` letters die in this plan).
- Per-channel state lives at `state/<slug>/processed_log.json` and `state/<slug>/publish_queue.json`.
- Commit messages: imperative subject, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- The live channel's slug is `study` (IG only.casual.vibes).

---

### Task 1: Config module + `channels.json` + secrets scaffolding

**Files:**
- Create: `config.py`
- Create: `channels.json`
- Modify: `.gitignore` (add `secrets/`)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (foundation task).
- Produces: `Channel` dataclass (fields: `slug: str`, `display_name: str`, `enabled: bool`, `timezone: str`, `slots: list`, `upload_lead_hours: int`, `content_description: str`, `youtube_category_id: str`, `drive_folder_id`, `ig_business_account_id`, `google_token: dict|None`, `google_client_secret: dict|None`; properties `state_dir`, `queue_file`, `log_file`; method `has_secrets() -> bool`), `load_channels(config_path="channels.json", secrets=None) -> list[Channel]`, `load_secrets() -> dict`, `validate_config(data: dict) -> None`, `ConfigError(ValueError)`, constants `CHANNELS_FILE = "channels.json"`, `SECRETS_FILE = "secrets/channels_secrets.json"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_config.py`:

```python
"""Tests for config.py (validation, loading, secrets merge)."""

import json
import os
import tempfile
import unittest

from config import ConfigError, load_channels, load_secrets, validate_config


def valid_config():
    return {
        "channels": [
            {
                "slug": "study",
                "display_name": "Study Motivation",
                "enabled": True,
                "timezone": "Asia/Kolkata",
                "slots": ["17:30", "21:30"],
                "upload_lead_hours": 8,
                "content_description": "study shorts",
                "youtube_category_id": "22",
            }
        ]
    }


class ValidateConfigTest(unittest.TestCase):
    def test_valid_config_passes(self):
        validate_config(valid_config())  # should not raise

    def test_bad_slug_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["slug"] = "Bad Slug!"
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_duplicate_slug_rejected(self):
        cfg = valid_config()
        cfg["channels"].append(dict(cfg["channels"][0]))
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_bad_slot_time_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["slots"] = ["25:99"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_empty_slots_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["slots"] = []
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_duplicate_slot_times_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["slots"] = ["17:30", "17:30"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_unknown_timezone_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["timezone"] = "Mars/Olympus"
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_missing_display_name_rejected(self):
        cfg = valid_config()
        cfg["channels"][0]["display_name"] = ""
        with self.assertRaises(ConfigError):
            validate_config(cfg)


class LoadChannelsTest(unittest.TestCase):
    def _write(self, data):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(data, tmp)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_merges_secrets_by_slug(self):
        path = self._write(valid_config())
        secrets = {
            "study": {
                "drive_folder_id": "d1",
                "ig_business_account_id": "ig1",
                "google_token": {"refresh_token": "r"},
            }
        }
        (ch,) = load_channels(path, secrets)
        self.assertEqual(ch.drive_folder_id, "d1")
        self.assertEqual(ch.ig_business_account_id, "ig1")
        self.assertTrue(ch.has_secrets())
        self.assertEqual(
            ch.queue_file, os.path.join("state", "study", "publish_queue.json")
        )
        self.assertEqual(
            ch.log_file, os.path.join("state", "study", "processed_log.json")
        )

    def test_missing_secrets_loads_with_has_secrets_false(self):
        path = self._write(valid_config())
        (ch,) = load_channels(path, {})
        self.assertFalse(ch.has_secrets())
        self.assertTrue(ch.enabled)

    def test_missing_file_raises(self):
        with self.assertRaises(ConfigError):
            load_channels("does-not-exist.json", {})

    def test_invalid_json_raises(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write("{nope")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        with self.assertRaises(ConfigError):
            load_channels(tmp.name, {})


class LoadSecretsTest(unittest.TestCase):
    def test_env_var_wins(self):
        os.environ["CHANNELS_SECRETS_JSON"] = '{"study": {"drive_folder_id": "x"}}'
        self.addCleanup(os.environ.pop, "CHANNELS_SECRETS_JSON", None)
        self.assertEqual(load_secrets()["study"]["drive_folder_id"], "x")

    def test_invalid_env_json_raises(self):
        os.environ["CHANNELS_SECRETS_JSON"] = "{nope"
        self.addCleanup(os.environ.pop, "CHANNELS_SECRETS_JSON", None)
        with self.assertRaises(ConfigError):
            load_secrets()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m unittest tests.test_config -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Write `config.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m unittest tests.test_config -v`
Expected: all PASS

- [ ] **Step 5: Create `channels.json` with the live channel**

Read the CURRENT values of `CONTENT_DESCRIPTION`, `SLOT_A_TIME`, `SLOT_B_TIME`, `TIMEZONE`, `YOUTUBE_CATEGORY_ID` from the local `.env` (e.g. `grep -E '^(CONTENT_DESCRIPTION|SLOT_A_TIME|SLOT_B_TIME|TIMEZONE|YOUTUBE_CATEGORY_ID)=' .env`) and substitute them below — these are NOT secrets. If a key is unset, keep the value shown:

```json
{
  "channels": [
    {
      "slug": "study",
      "display_name": "Study Motivation",
      "enabled": true,
      "timezone": "Asia/Kolkata",
      "slots": ["17:30", "21:30"],
      "upload_lead_hours": 8,
      "content_description": "short-form video content",
      "youtube_category_id": "22"
    }
  ]
}
```

Verify it loads: `venv/bin/python -c "from config import load_channels; print(load_channels())"`
Expected: prints one `Channel(slug='study', ...)`.

- [ ] **Step 6: Add the secrets dir to `.gitignore`**

Append to the `# Secrets — NEVER commit these` block in `.gitignore`:

```
# Local channel secrets + deploy fingerprints (managed by the config web app)
secrets/
```

- [ ] **Step 7: Run the full suite, then commit**

Run: `venv/bin/python -m unittest discover -s tests -v` — all pass.

```bash
git add config.py channels.json .gitignore tests/test_config.py
git commit -m "Add channel config module and channels.json

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Migrate state files to `state/study/`

**Files:**
- Move: `processed_log.json` → `state/study/processed_log.json`
- Move: `publish_queue.json` → `state/study/publish_queue.json`

**Interfaces:**
- Consumes: `Channel.state_dir` layout from Task 1 (`state/<slug>/`).
- Produces: the state layout every later task reads/writes. Old scripts (`upload_unlisted.py`, `publish_scheduled.py`) still reference root paths until Tasks 6–7 replace them — that transitional breakage is fine on this branch (nothing runs here; crons run on `main`).

- [ ] **Step 1: Move the files with history**

```bash
mkdir -p state/study
git mv processed_log.json state/study/processed_log.json
git mv publish_queue.json state/study/publish_queue.json
```

- [ ] **Step 2: Verify layout matches config**

Run: `venv/bin/python -c "from config import load_channels; import os; ch = load_channels()[0]; print(os.path.exists(ch.queue_file), os.path.exists(ch.log_file))"`
Expected: `True True`

- [ ] **Step 3: Commit**

```bash
git commit -m "Move pipeline state to state/study/

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `auth.py` takes a token dict instead of a file

**Files:**
- Modify: `auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `Channel.google_token` dicts (token.json-shaped: `client_id`, `client_secret`, `refresh_token`, optional `token`).
- Produces: `get_credentials(token_info: dict) -> Credentials` (refreshes in memory, never writes disk). `SCOPES` and `TOKEN_FILE` stay exported — `oauth_setup.py` still uses both and is intentionally unchanged (headless fallback that writes `token.json`; the web app's legacy import reads it).

- [ ] **Step 1: Write the failing test** — `tests/test_auth.py`:

```python
"""Tests for auth.get_credentials (token-dict based, no disk IO)."""

import unittest

from auth import SCOPES, get_credentials


def token_info():
    return {
        "client_id": "cid.apps.googleusercontent.com",
        "client_secret": "csecret",
        "refresh_token": "rtoken",
        "token": "atoken",
    }


class GetCredentialsTest(unittest.TestCase):
    def test_builds_credentials_from_dict(self):
        creds = get_credentials(token_info())
        self.assertEqual(creds.token, "atoken")
        self.assertEqual(creds.refresh_token, "rtoken")
        # No expiry in the dict -> not expired -> no network refresh attempted.
        self.assertFalse(creds.expired)
        self.assertEqual(sorted(creds.scopes), sorted(SCOPES))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m unittest tests.test_auth -v`
Expected: FAIL — current `get_credentials()` takes no arguments / hits `FileNotFoundError`.

- [ ] **Step 3: Rewrite `auth.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `venv/bin/python -m unittest tests.test_auth -v`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Run: `venv/bin/python -m unittest discover -s tests -v`
Note: `tests/test_list_drive_videos.py` and `tests/test_publish_selection.py` still import the OLD modules, which don't call `get_credentials` at import time — they must still pass. If anything fails, fix before committing.

```bash
git add auth.py tests/test_auth.py
git commit -m "auth: build credentials from token dict, not token.json

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `metadata_generator.py` takes content_description as a parameter

**Files:**
- Modify: `metadata_generator.py`
- Test: `tests/test_metadata_generator.py`

**Interfaces:**
- Consumes: `Channel.content_description` (plain string).
- Produces: `generate_metadata(filename: str, content_description: str = "short-form video content", extra_context: str = "") -> dict` (same return shape: `title`, `description`, `tags`, `ig_caption`); internal `_system_prompt(content_description: str) -> str`. `GROQ_API_KEY` is read at call time, so importing the module no longer requires the env var.

- [ ] **Step 1: Write the failing test** — `tests/test_metadata_generator.py`:

```python
"""Tests for metadata_generator prompt construction (no network)."""

import unittest

from metadata_generator import _system_prompt


class SystemPromptTest(unittest.TestCase):
    def test_prompt_mentions_content_description(self):
        prompt = _system_prompt("cooking tutorials")
        self.assertIn("cooking tutorials", prompt)

    def test_prompt_demands_required_json_keys(self):
        prompt = _system_prompt("anything")
        for key in ("title", "description", "tags", "ig_caption"):
            self.assertIn(key, prompt)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m unittest tests.test_metadata_generator -v`
Expected: FAIL — `ImportError: cannot import name '_system_prompt'` (import may even crash on missing `GROQ_API_KEY`, which this task also fixes).

- [ ] **Step 3: Modify `metadata_generator.py`**

Add `from __future__ import annotations` after the docstring. Delete these module-level lines:

```python
GROQ_API_KEY = os.environ["GROQ_API_KEY"].strip()
CONTENT_DESCRIPTION = os.environ.get("CONTENT_DESCRIPTION", "short-form video content")
SYSTEM_PROMPT = f"""You are a social media editor ..."""
```

Replace with a function (the prompt text is IDENTICAL to the old `SYSTEM_PROMPT`, with the f-string now closing over the parameter):

```python
def _system_prompt(content_description: str) -> str:
    return f"""You are a social media editor writing metadata for {content_description}, \
published on YouTube and Instagram Reels simultaneously. Given a source filename \
(and optional context), output ONLY valid JSON with this exact shape, no markdown \
fences, no preamble:

{{"title": "...", "description": "...", "tags": ["...", "..."], "ig_caption": "..."}}

Rules:
- title: under 70 characters, curiosity-driven but not misleading, for YouTube
- description: 2-4 sentences for YouTube, includes relevant keywords naturally, ends with 3-5 hashtags
- tags: 8-15 relevant single/short-phrase tags, no # symbol
- ig_caption: shorter and punchier than the YouTube description, 1-2 sentences, ends with 5-8 hashtags suited to Instagram Reels discovery
"""
```

Change `_call_groq` to take the system prompt and read the key at call time:

```python
@retry_on_transient(max_attempts=4, base_delay=2.0)
def _call_groq(system_prompt: str, user_prompt: str) -> str:
    """Fire a single Groq request and return the raw content string."""
    api_key = os.environ["GROQ_API_KEY"].strip()
    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }

    response = requests.post(
        GROQ_URL,
        json=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=30,
    )
    if not response.ok:
        logger.error("Groq API error %s: %s", response.status_code, response.text)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()
```

Change `generate_metadata`'s signature and the one call site inside it:

```python
def generate_metadata(
    filename: str,
    content_description: str = "short-form video content",
    extra_context: str = "",
) -> dict:
```

and

```python
    raw = _call_groq(_system_prompt(content_description), user_prompt)
```

(docstring: add `content_description: what this channel's videos are about — steers titles/captions.`). Everything else in the file stays as is, including the `__main__` block (update its call to `generate_metadata("ep12_clip_founder_burnout.mp4", "podcast clips")`).

- [ ] **Step 4: Run tests to verify pass**

Run: `venv/bin/python -m unittest tests.test_metadata_generator -v`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

```bash
git add metadata_generator.py tests/test_metadata_generator.py
git commit -m "metadata: parameterize content description, lazy API key read

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `instagram_uploader.py` takes the IG account per call

**Files:**
- Modify: `instagram_uploader.py`
- Test: `tests/test_instagram_uploader.py`

**Interfaces:**
- Consumes: `Channel.ig_business_account_id` (string).
- Produces: `publish_reel(ig_user_id: str, video_public_url: str, caption: str) -> str`, `get_reel_permalink(media_id: str) -> str` (unchanged signature). `META_ACCESS_TOKEN` stays a shared env var, read at call time via internal `_auth_headers() -> dict` (raises `ValueError` when unset).

- [ ] **Step 1: Write the failing test** — `tests/test_instagram_uploader.py`:

```python
"""Tests for instagram_uploader argument/env validation (no network)."""

import os
import unittest

from instagram_uploader import publish_reel


class PublishReelValidationTest(unittest.TestCase):
    def test_empty_ig_user_id_raises(self):
        os.environ["META_ACCESS_TOKEN"] = "t"
        self.addCleanup(os.environ.pop, "META_ACCESS_TOKEN", None)
        with self.assertRaises(ValueError):
            publish_reel("", "https://example.com/v.mp4", "caption")

    def test_missing_meta_token_raises(self):
        os.environ.pop("META_ACCESS_TOKEN", None)
        with self.assertRaises(ValueError):
            publish_reel("178414", "https://example.com/v.mp4", "caption")


if __name__ == "__main__":
    unittest.main()
```

Note: `load_dotenv()` in the module may inject a real `META_ACCESS_TOKEN` from `.env` during local runs — the second test pops it AFTER import, which wins because the token is now read at call time.

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m unittest tests.test_instagram_uploader -v`
Expected: FAIL — `publish_reel()` takes 2 positional args today.

- [ ] **Step 3: Modify `instagram_uploader.py`**

Add `from __future__ import annotations`. Delete the module-level lines:

```python
IG_USER_ID = os.environ.get("IG_BUSINESS_ACCOUNT_ID", "").strip()
ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "").strip()
_AUTH_HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
```

Add instead:

```python
def _auth_headers() -> dict:
    """Bearer auth header — token read at call time so channels share one env."""
    token = os.environ.get("META_ACCESS_TOKEN", "").strip()
    if not token:
        raise ValueError("META_ACCESS_TOKEN must be set")
    return {"Authorization": f"Bearer {token}"}
```

Thread `ig_user_id` through the helpers (every `headers=_AUTH_HEADERS` becomes `headers=_auth_headers()`):

```python
@retry_on_transient(max_attempts=3, base_delay=5.0)
def _create_container(ig_user_id: str, video_public_url: str, caption: str) -> str:
    """Step 1: create the media container and return its ID."""
    resp = requests.post(
        f"{BASE_URL}/{ig_user_id}/media",
        json={
            "media_type": "REELS",
            "video_url": video_public_url,
            "caption": caption,
        },
        headers=_auth_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]
```

`_get_container_status(container_id)` and `get_reel_permalink(media_id)` keep their signatures, only swapping in `headers=_auth_headers()`. `_publish_container` becomes `_publish_container(ig_user_id: str, container_id: str) -> str` posting to `f"{BASE_URL}/{ig_user_id}/media_publish"`.

`publish_reel` becomes:

```python
def publish_reel(ig_user_id: str, video_public_url: str, caption: str) -> str:
```

with the guard at the top replaced by:

```python
    if not ig_user_id:
        raise ValueError("ig_user_id is required (channel's IG business account ID)")
    _auth_headers()  # fail fast if META_ACCESS_TOKEN is missing
```

and the two internal calls updated: `_create_container(ig_user_id, video_public_url, caption)` and `_publish_container(ig_user_id, container_id)`. Body otherwise unchanged (poll loop, errors, logging).

- [ ] **Step 4: Run tests to verify pass**

Run: `venv/bin/python -m unittest tests.test_instagram_uploader -v`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

```bash
git add instagram_uploader.py tests/test_instagram_uploader.py
git commit -m "instagram: take IG account ID per call, lazy token read

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `uploader.py` — channel-parameterized upload step

**Files:**
- Move: `upload_unlisted.py` → `uploader.py` (via `git mv`, then edit)
- Modify: `tests/test_list_drive_videos.py` (import path only)

**Interfaces:**
- Consumes: `Channel` (Task 1), `get_credentials(token_info)` (Task 3), `generate_metadata(filename, content_description)` (Task 4), `notify(text)`, `load_json`/`save_json`.
- Produces: `run_upload(channel: Channel, go_live_at: datetime, slot: str) -> bool` (True when a video was queued; False when the Drive folder has no unprocessed videos), plus module helpers `list_drive_videos(drive, folder_id) -> list[dict]` (unchanged), `make_shareable(drive, file_id) -> str`, `download_video(drive, file_id, filename) -> str`, `upload_unlisted_youtube(youtube, video_path, title, description, tags, category_id) -> str`. Queue entries: `{"youtube_video_id", "drive_file_id", "drive_public_url", "ig_caption", "go_live_at": iso-string, "slot": "HH:MM", "published": False}`.

- [ ] **Step 1: Move the file, update the test import**

```bash
git mv upload_unlisted.py uploader.py
```

In `tests/test_list_drive_videos.py`: change the docstring to `"""Tests for Drive folder listing in uploader.py."""`, delete the two `os.environ.setdefault(...)` lines (no longer needed — nothing reads env at import anymore), and change the import to:

```python
from uploader import list_drive_videos
```

- [ ] **Step 2: Run that test — it fails until uploader.py imports cleanly**

Run: `venv/bin/python -m unittest tests.test_list_drive_videos -v`
Expected: FAIL (uploader.py still reads `os.environ["DRIVE_FOLDER_ID"]` at import).

- [ ] **Step 3: Rewrite `uploader.py` around `run_upload`**

Replace the docstring, config block, scheduling section, and `main()` — keep `list_drive_videos`, `make_shareable`, `download_video` exactly as they are. Final file shape:

```python
"""
Upload step of the pipeline, called by run_pipeline.py when a slot's lead
window opens (go-live minus upload_lead_hours).

Picks the channel's next unprocessed Drive video, generates metadata with
Groq, uploads it to YouTube as UNLISTED, makes the Drive file link-shareable
(needed for Instagram later), and queues it in the channel's publish queue
with the target go-live time.

Does NOT touch Instagram — that happens in publisher.py at the go-live
moment, since Instagram can't be uploaded ahead and held.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

from auth import get_credentials
from config import Channel
from metadata_generator import generate_metadata
from telegram_notifier import notify
from utils import load_json, save_json

logger = logging.getLogger(__name__)

TEMP_DIR = "temp_downloads"


# ---------------------------------------------------------------------------
# Drive helpers  (list_drive_videos / make_shareable / download_video —
# unchanged from upload_unlisted.py, keep verbatim)
# ---------------------------------------------------------------------------
...

# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------

def upload_unlisted_youtube(
    youtube,
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
) -> str:
    """Upload *video_path* as an unlisted video and return the YouTube video ID."""
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": "unlisted",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/*")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("  Upload progress: %d%%", int(status.progress() * 100))

    return response["id"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_upload(channel: Channel, go_live_at: datetime, slot: str) -> bool:
    """Queue the channel's next video for *slot* going live at *go_live_at*.

    Returns True when a video was uploaded and queued, False when the Drive
    folder has no unprocessed videos left.
    """
    creds = get_credentials(channel.google_token)
    drive = build("drive", "v3", credentials=creds)
    youtube = build("youtube", "v3", credentials=creds)

    log = load_json(channel.log_file, {"processed_file_ids": []})
    queue = load_json(channel.queue_file, [])

    videos = list_drive_videos(drive, channel.drive_folder_id)
    next_video = next(
        (v for v in videos if v["id"] not in log["processed_file_ids"]), None
    )

    if not next_video:
        logger.info("[%s] No new videos to process.", channel.slug)
        return False

    logger.info("[%s] Next video: %s", channel.slug, next_video["name"])

    logger.info("[%s] Generating metadata with Groq...", channel.slug)
    metadata = generate_metadata(next_video["name"], channel.content_description)

    logger.info("[%s] Downloading from Drive...", channel.slug)
    local_path = download_video(drive, next_video["id"], next_video["name"])

    # Critical section: keep cleanup in finally so a partial run never leaves
    # a dangling temp file or a video stuck in limbo.
    try:
        logger.info("[%s] Uploading to YouTube as unlisted...", channel.slug)
        video_id = upload_unlisted_youtube(
            youtube, local_path,
            metadata["title"], metadata["description"], metadata["tags"],
            channel.youtube_category_id,
        )
        logger.info("[%s]   YouTube video ID: %s", channel.slug, video_id)

        logger.info("[%s] Making Drive file link-shareable for Instagram...", channel.slug)
        drive_public_url = make_shareable(drive, next_video["id"])

        logger.info("[%s] Queued to go public/live at: %s", channel.slug, go_live_at.isoformat())

        queue.append({
            "youtube_video_id": video_id,
            "drive_file_id": next_video["id"],
            "drive_public_url": drive_public_url,
            "ig_caption": metadata["ig_caption"],
            "go_live_at": go_live_at.isoformat(),
            "slot": slot,
            "published": False,
        })
        save_json(channel.queue_file, queue)

        log["processed_file_ids"].append(next_video["id"])
        save_json(channel.log_file, log)

        notify(
            f"📤 <b>[{channel.display_name}] Queued for {slot}</b>\n"
            f"🎬 {metadata['title']}\n"
            f"👀 Preview (unlisted): https://youtu.be/{video_id}\n"
            f"🕒 Goes live: {go_live_at.strftime('%d %b, %I:%M %p')}"
        )

    finally:
        # Always clean up the temp file, even if an exception was raised above.
        if os.path.exists(local_path):
            os.remove(local_path)
            logger.debug("Removed temp file: %s", local_path)

    return True
```

(The `...` marks the three Drive helpers copied verbatim — the executor keeps them from the moved file; they are not new code. No `main()`, no argparse, no `load_dotenv` — `run_pipeline.py` owns process concerns.)

- [ ] **Step 4: Run tests to verify pass**

Run: `venv/bin/python -m unittest tests.test_list_drive_videos -v`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

`tests/test_publish_selection.py` still imports `publish_scheduled` — must still pass (that module doesn't import `upload_unlisted`).

```bash
git add uploader.py tests/test_list_drive_videos.py
git commit -m "Refactor upload step into channel-parameterized uploader.py

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: `publisher.py` — channel-parameterized publish step

**Files:**
- Move: `publish_scheduled.py` → `publisher.py` (via `git mv`, then edit)
- Modify: `tests/test_publish_selection.py` (import path only)

**Interfaces:**
- Consumes: `Channel`, `get_credentials(token_info)`, `publish_reel(ig_user_id, video_public_url, caption)` + `get_reel_permalink(media_id)` (Task 5), `notify`, `load_json`/`save_json`.
- Produces: `run_publish(channel: Channel, now: datetime, force_next: bool = False) -> None`; helpers `select_due(queue, now, force_next=False) -> list[dict]` (logic unchanged), `prune_queue(queue) -> list[dict]` (unchanged), `set_youtube_public(youtube, video_id)`, `revoke_drive_public_access(drive, file_id)`.

- [ ] **Step 1: Move the file, update the test import**

```bash
git mv publish_scheduled.py publisher.py
```

In `tests/test_publish_selection.py`: docstring → `"""Tests for publisher.select_due (schedule vs force-next selection)."""`, delete both `os.environ.setdefault(...)` lines, and:

```python
from publisher import select_due
```

Also update the two fixture dicts' `slot` values from `"A"`/`"B"` to `"17:30"`/`"21:30"` (slot letters are dead; `select_due` never reads `slot`, so tests stay green either way — update for realism).

- [ ] **Step 2: Run test — fails until publisher.py imports cleanly**

Run: `venv/bin/python -m unittest tests.test_publish_selection -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'publisher'` until the move lands, then import errors while the file still has `from instagram_uploader import ...` at old signatures — proceed).

- [ ] **Step 3: Rewrite `publisher.py` around `run_publish`**

Keep `set_youtube_public`, `revoke_drive_public_access`, `select_due`, `prune_queue` bodies EXACTLY as they are. Replace docstring, module config, and `main()`:

```python
"""
Publish step of the pipeline, called by run_pipeline.py every 15 minutes.

Checks the channel's publish queue for anything whose go_live_at time has
arrived, then:

  1. Flips the YouTube video from unlisted -> public
  2. Publishes the same video to Instagram as a Reel (first time it's ever
     touched Instagram -- there's no "hold and schedule" on IG's side)
  3. Revokes the Drive file's public-link permission (no longer needed)
  4. Prunes old published entries from the queue (keeps file size bounded)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

from auth import get_credentials
from config import Channel
from instagram_uploader import get_reel_permalink, publish_reel
from telegram_notifier import notify
from utils import load_json, save_json

logger = logging.getLogger(__name__)

# Prune published entries older than this many days to keep the queue file small
QUEUE_PRUNE_DAYS = int(os.environ.get("QUEUE_PRUNE_DAYS", "30"))
```

then the four kept helpers, then:

```python
def run_publish(channel: Channel, now: datetime, force_next: bool = False) -> None:
    """Publish everything due for *channel* (or force the earliest item)."""
    queue = load_json(channel.queue_file, [])
    due = select_due(queue, now, force_next=force_next)

    if not due:
        if force_next:
            notify(f"📭 [{channel.display_name}] Nothing in the queue to force-publish.")
        logger.info("[%s] Nothing due yet.", channel.slug)
        return

    creds = get_credentials(channel.google_token)
    youtube = build("youtube", "v3", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    for item in due:
        yt_id = item["youtube_video_id"]
        drive_file_id = item.get("drive_file_id")
        logger.info("[%s] Publishing %s video: %s", channel.slug, item["slot"], yt_id)

        logger.info("  Setting YouTube to public...")
        set_youtube_public(youtube, yt_id)

        logger.info("  Posting to Instagram as Reel...")
        try:
            ig_media_id = publish_reel(
                channel.ig_business_account_id,
                item["drive_public_url"],
                item["ig_caption"],
            )
            logger.info("  Instagram media ID: %s", ig_media_id)
        except Exception as exc:
            logger.error(
                "  Instagram publish FAILED (YouTube still went public): %s", exc
            )
            # Notify only on the first failure of this item — it retries on
            # every subsequent run, and a broken token would otherwise spam
            # a message every 15 minutes.
            if not item.get("failure_notified"):
                notify(
                    f"🔴 <b>[{channel.display_name}] Instagram publish failed</b> ({item['slot']}).\n"
                    f"YouTube is already public: https://youtu.be/{yt_id}\n"
                    f"Error: {exc}\n"
                    "It will retry on the next scheduled check."
                )
                item["failure_notified"] = True
            # Continue processing other items; don't mark as published so it
            # is retried on the next run.
            continue

        # Revoke Drive public access now that Instagram has fetched the video
        if drive_file_id:
            revoke_drive_public_access(drive, drive_file_id)

        item["published"] = True

        try:
            ig_link = get_reel_permalink(ig_media_id)
        except Exception as exc:
            logger.warning("  Could not fetch Reel permalink: %s", exc)
            ig_link = None
        notify(
            f"🟢 <b>[{channel.display_name}] Video live</b> ({item['slot']})\n"
            f"▶️ YouTube: https://youtu.be/{yt_id}\n"
            f"📸 Instagram: {ig_link or f'published (media {ig_media_id})'}"
        )

    # Prune old entries and persist
    queue = prune_queue(queue)
    save_json(channel.queue_file, queue)
```

No `main()`, no argparse, no `load_dotenv`, no `TIMEZONE_STR`/`QUEUE_FILE` constants.

- [ ] **Step 4: Run tests to verify pass**

Run: `venv/bin/python -m unittest tests.test_publish_selection -v`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

Note: `telegram_commands.build_status` still imports `upload_unlisted` — it's rewritten in Task 10; its test doesn't exercise `build_status`, so the suite stays green.

```bash
git add publisher.py tests/test_publish_selection.py
git commit -m "Refactor publish step into channel-parameterized publisher.py

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: `run_pipeline.py` — the self-scheduling dispatcher

**Files:**
- Create: `run_pipeline.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `load_channels()`, `Channel`, `run_upload(channel, go_live_at, slot)` (Task 6), `run_publish(channel, now, force_next)` (Task 7), `notify`, `load_json`/`save_json`.
- Produces: CLI `python run_pipeline.py [--channel SLUG] [--upload-slot HH:MM|""] [--force-next]`; pure helpers used by tests and the web app: `next_slot_occurrence(slot: str, tz: ZoneInfo, now: datetime) -> datetime`, `upload_window_open(go_live: datetime, now: datetime, lead_hours: int) -> bool`, `already_queued(queue: list, slot: str, go_live: datetime) -> bool`, `due_uploads(channel: Channel, queue: list, now: datetime) -> list[tuple[str, datetime]]`.

- [ ] **Step 1: Write the failing tests** — `tests/test_scheduler.py`:

```python
"""Tests for run_pipeline scheduling decisions (pure logic, no network)."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from config import Channel
from run_pipeline import (
    already_queued,
    due_uploads,
    next_slot_occurrence,
    upload_window_open,
)

TZ = ZoneInfo("Asia/Kolkata")


def make_channel(**over):
    base = dict(
        slug="study",
        display_name="Study",
        slots=["17:30", "21:30"],
        upload_lead_hours=8,
        timezone="Asia/Kolkata",
    )
    base.update(over)
    return Channel(**base)


class NextSlotOccurrenceTest(unittest.TestCase):
    def test_slot_later_today(self):
        now = datetime(2026, 8, 16, 10, 0, tzinfo=TZ)
        self.assertEqual(
            next_slot_occurrence("17:30", TZ, now),
            datetime(2026, 8, 16, 17, 30, tzinfo=TZ),
        )

    def test_slot_already_past_rolls_to_tomorrow(self):
        now = datetime(2026, 8, 16, 18, 0, tzinfo=TZ)
        self.assertEqual(
            next_slot_occurrence("17:30", TZ, now),
            datetime(2026, 8, 17, 17, 30, tzinfo=TZ),
        )

    def test_exactly_at_slot_rolls_to_tomorrow(self):
        now = datetime(2026, 8, 16, 17, 30, tzinfo=TZ)
        self.assertEqual(
            next_slot_occurrence("17:30", TZ, now),
            datetime(2026, 8, 17, 17, 30, tzinfo=TZ),
        )


class UploadWindowTest(unittest.TestCase):
    GO_LIVE = datetime(2026, 8, 16, 17, 30, tzinfo=TZ)

    def test_open_exactly_at_lead_boundary(self):
        now = datetime(2026, 8, 16, 9, 30, tzinfo=TZ)
        self.assertTrue(upload_window_open(self.GO_LIVE, now, 8))

    def test_closed_before_lead_boundary(self):
        now = datetime(2026, 8, 16, 9, 29, tzinfo=TZ)
        self.assertFalse(upload_window_open(self.GO_LIVE, now, 8))

    def test_closed_at_go_live(self):
        self.assertFalse(upload_window_open(self.GO_LIVE, self.GO_LIVE, 8))


class AlreadyQueuedTest(unittest.TestCase):
    GO_LIVE = datetime(2026, 8, 16, 17, 30, tzinfo=TZ)

    def test_matching_slot_and_time_is_queued(self):
        queue = [{"slot": "17:30", "go_live_at": self.GO_LIVE.isoformat(), "published": False}]
        self.assertTrue(already_queued(queue, "17:30", self.GO_LIVE))

    def test_published_entries_still_count(self):
        queue = [{"slot": "17:30", "go_live_at": self.GO_LIVE.isoformat(), "published": True}]
        self.assertTrue(already_queued(queue, "17:30", self.GO_LIVE))

    def test_different_day_not_queued(self):
        queue = [{"slot": "17:30", "go_live_at": "2026-08-15T17:30:00+05:30"}]
        self.assertFalse(already_queued(queue, "17:30", self.GO_LIVE))


class DueUploadsTest(unittest.TestCase):
    def test_only_slot_within_lead_window_is_due(self):
        # 10:00 — 17:30 window opened at 09:30; 21:30 window opens at 13:30.
        now = datetime(2026, 8, 16, 10, 0, tzinfo=TZ)
        due = due_uploads(make_channel(), [], now)
        self.assertEqual(due, [("17:30", datetime(2026, 8, 16, 17, 30, tzinfo=TZ))])

    def test_both_slots_due_in_afternoon(self):
        now = datetime(2026, 8, 16, 14, 0, tzinfo=TZ)
        due = due_uploads(make_channel(), [], now)
        self.assertEqual([slot for slot, _ in due], ["17:30", "21:30"])

    def test_queued_slot_is_suppressed(self):
        now = datetime(2026, 8, 16, 14, 0, tzinfo=TZ)
        queue = [{
            "slot": "17:30",
            "go_live_at": datetime(2026, 8, 16, 17, 30, tzinfo=TZ).isoformat(),
            "published": False,
        }]
        due = due_uploads(make_channel(), queue, now)
        self.assertEqual([slot for slot, _ in due], ["21:30"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m unittest tests.test_scheduler -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'run_pipeline'`

- [ ] **Step 3: Write `run_pipeline.py`**

```python
"""
Single entry point for the multi-channel pipeline. CI runs this every 15
minutes; slot times are pure config (channels.json), so changing them never
touches workflow YAML.

Per enabled channel, each pass:
  1. Uploads any slot whose lead window has opened (go-live minus
     upload_lead_hours) and isn't already queued for that occurrence.
  2. Publishes any queue item whose go-live time has arrived.

Manual/targeted runs (Telegram bot, web app, Actions UI):
  python run_pipeline.py --channel study --upload-slot 17:30   # queue for a slot
  python run_pipeline.py --channel study --upload-slot ""      # first slot
  python run_pipeline.py --channel study --force-next          # publish next now
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from config import Channel, load_channels  # noqa: E402
from publisher import run_publish  # noqa: E402
from telegram_notifier import notify  # noqa: E402
from uploader import run_upload  # noqa: E402
from utils import load_json, save_json  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scheduling decisions (pure — unit tested)
# ---------------------------------------------------------------------------

def next_slot_occurrence(slot: str, tz: ZoneInfo, now: datetime) -> datetime:
    """Next occurrence of *slot* ("HH:MM") in *tz*: today if still ahead, else tomorrow."""
    hour, minute = map(int, slot.split(":"))
    target = now.astimezone(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def upload_window_open(go_live: datetime, now: datetime, lead_hours: int) -> bool:
    """True while now is inside [go_live - lead_hours, go_live)."""
    return go_live - timedelta(hours=lead_hours) <= now < go_live


def already_queued(queue: list, slot: str, go_live: datetime) -> bool:
    """True if this slot occurrence is already in the queue (published or not)."""
    iso = go_live.isoformat()
    return any(
        item.get("slot") == slot and item.get("go_live_at") == iso for item in queue
    )


def due_uploads(channel: Channel, queue: list, now: datetime) -> list:
    """[(slot, go_live_datetime), ...] for every slot whose upload should run now."""
    tz = ZoneInfo(channel.timezone)
    due = []
    for slot in channel.slots:
        go_live = next_slot_occurrence(slot, tz, now)
        if upload_window_open(go_live, now, channel.upload_lead_hours) and not already_queued(
            queue, slot, go_live
        ):
            due.append((slot, go_live))
    return due


# ---------------------------------------------------------------------------
# Missing-secrets warning (once per channel, cleared when fixed)
# ---------------------------------------------------------------------------

def _warn_file(channel: Channel) -> str:
    return os.path.join(channel.state_dir, "secrets_warning.json")


def warn_missing_secrets(channel: Channel) -> None:
    os.makedirs(channel.state_dir, exist_ok=True)
    if not load_json(_warn_file(channel), {}).get("notified"):
        notify(
            f"⚠️ <b>[{channel.display_name}] Channel skipped</b> — secrets are "
            "missing or incomplete (need Drive folder, IG account, and a "
            "connected YouTube account). Fix it in the config app and deploy."
        )
        save_json(_warn_file(channel), {"notified": True})


def clear_secrets_warning(channel: Channel) -> None:
    if os.path.exists(_warn_file(channel)):
        os.remove(_warn_file(channel))


# ---------------------------------------------------------------------------
# Per-channel pass
# ---------------------------------------------------------------------------

def process_channel(channel: Channel, now: datetime) -> None:
    os.makedirs(channel.state_dir, exist_ok=True)
    queue = load_json(channel.queue_file, [])
    for slot, go_live in due_uploads(channel, queue, now):
        logger.info("[%s] Upload window open for %s (live %s)", channel.slug, slot, go_live)
        run_upload(channel, go_live, slot)
    run_publish(channel, now)


def manual_upload(channel: Channel, slot_arg: str, now: datetime) -> None:
    slot = slot_arg or channel.slots[0]
    if slot not in channel.slots:
        raise SystemExit(f"Unknown slot {slot!r} for {channel.slug} (has: {channel.slots})")
    os.makedirs(channel.state_dir, exist_ok=True)
    go_live = next_slot_occurrence(slot, ZoneInfo(channel.timezone), now)
    queue = load_json(channel.queue_file, [])
    if already_queued(queue, slot, go_live):
        notify(f"📭 [{channel.display_name}] {slot} is already queued for {go_live:%d %b}.")
        return
    run_upload(channel, go_live, slot)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-channel pipeline dispatcher.")
    parser.add_argument("--channel", default="", help="Restrict to one channel slug")
    parser.add_argument(
        "--upload-slot",
        default=None,
        help='Force an upload for this HH:MM slot ("" = channel\'s first slot). Requires --channel.',
    )
    parser.add_argument(
        "--force-next",
        action="store_true",
        help="Publish the channel's earliest queued video immediately. Requires --channel.",
    )
    args = parser.parse_args()

    if (args.upload_slot is not None or args.force_next) and not args.channel:
        parser.error("--upload-slot/--force-next require --channel")

    channels = load_channels()
    if args.channel:
        channels = [c for c in channels if c.slug == args.channel]
        if not channels:
            raise SystemExit(f"Unknown channel: {args.channel}")

    for channel in channels:
        # A targeted --channel run works even on a paused channel (explicit
        # human intent); the scheduled pass skips paused channels.
        if not channel.enabled and not args.channel:
            continue

        now = datetime.now(ZoneInfo(channel.timezone))

        if not channel.has_secrets():
            logger.warning("[%s] Missing secrets — skipping.", channel.slug)
            warn_missing_secrets(channel)
            continue
        clear_secrets_warning(channel)

        if args.upload_slot is not None:
            manual_upload(channel, args.upload_slot.strip(), now)
        elif args.force_next:
            run_publish(channel, now, force_next=True)
        else:
            process_channel(channel, now)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        notify(f"🔴 <b>Pipeline run crashed</b>: {exc}")
        raise
```

- [ ] **Step 4: Run tests to verify pass**

Run: `venv/bin/python -m unittest tests.test_scheduler -v`
Expected: PASS

- [ ] **Step 5: Full suite + commit**

```bash
git add run_pipeline.py tests/test_scheduler.py
git commit -m "Add self-scheduling multi-channel dispatcher run_pipeline.py

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Rewrite GitHub workflows + `.env.example`

**Files:**
- Modify: `.github/workflows/pipeline.yml` (full rewrite below)
- Modify: `.github/workflows/telegram-bot.yml`
- Modify: `.env.example` (full rewrite below)

**Interfaces:**
- Consumes: `run_pipeline.py` CLI (Task 8), `check_token_expiry.py` (unchanged).
- Produces: `workflow_dispatch` inputs `channel` / `action` (`upload` | `publishnow` | blank) / `upload_slot` — the contract Task 10 (Telegram) and Task 12 (web app) dispatch against. Env contract for CI: `CHANNELS_SECRETS_JSON`, `META_ACCESS_TOKEN`, `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. No more Google credential file reconstruction — `auth.py` reads token dicts from the secrets blob (token.json contents carry their own client_id/client_secret, so refresh needs no client_secret.json).

- [ ] **Step 1: Replace `.github/workflows/pipeline.yml` entirely**

```yaml
name: PoddyGo Pipeline

permissions:
  contents: write

concurrency:
  group: poddygo-pipeline
  cancel-in-progress: false

on:
  schedule:
    # Self-scheduling pass every 15 min: run_pipeline.py decides per channel
    # which uploads (lead window open) and publishes (go-live reached) are due.
    - cron: "*/15 * * * *"
    # Daily Meta token expiry check: 9:00 AM IST = 3:30 AM UTC
    - cron: "30 3 * * *"

  # Manual trigger — used by the Actions tab, the Telegram bot, and the web app
  workflow_dispatch:
    inputs:
      channel:
        description: "Channel slug (required for action=upload/publishnow)"
        required: false
        default: ""
      action:
        description: "upload | publishnow | blank = normal pass"
        required: false
        default: ""
      upload_slot:
        description: "HH:MM go-live slot for action=upload (blank = channel's first slot)"
        required: false
        default: ""

env:
  TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
  TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
  CHANNELS_SECRETS_JSON: ${{ secrets.CHANNELS_SECRETS_JSON }}
  META_ACCESS_TOKEN: ${{ secrets.META_ACCESS_TOKEN }}
  GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}

jobs:
  run-pipeline:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Normal pipeline pass
        if: >
          github.event.schedule == '*/15 * * * *' ||
          (github.event_name == 'workflow_dispatch' && github.event.inputs.action == '')
        run: python run_pipeline.py

      - name: Manual upload
        if: github.event_name == 'workflow_dispatch' && github.event.inputs.action == 'upload'
        env:
          CHANNEL: ${{ github.event.inputs.channel }}
          UPLOAD_SLOT: ${{ github.event.inputs.upload_slot }}
        run: python run_pipeline.py --channel "$CHANNEL" --upload-slot "$UPLOAD_SLOT"

      - name: Manual publish-now
        if: github.event_name == 'workflow_dispatch' && github.event.inputs.action == 'publishnow'
        env:
          CHANNEL: ${{ github.event.inputs.channel }}
        run: python run_pipeline.py --channel "$CHANNEL" --force-next

      - name: Check Meta token expiry
        if: github.event.schedule == '30 3 * * *'
        run: python check_token_expiry.py

      # Commit updated state back to repo with retry loop to handle push
      # conflicts (the telegram-bot workflow can commit concurrently).
      - name: Commit updated state back to repo
        run: |
          git config user.name "PoddyGo Bot"
          git config user.email "actions@github.com"
          git add state/
          git diff --staged --quiet && exit 0
          git commit -m "Update pipeline state [skip ci]"
          for i in 1 2 3 4 5; do
            git pull --rebase origin ${{ github.ref_name }} && git push && break
            echo "Push attempt $i failed, retrying in 15s..."
            sleep 15
          done
```

(Inputs are passed via `env:` — never interpolate `github.event.inputs.*` directly into `run:` shell text.)

- [ ] **Step 2: Update `.github/workflows/telegram-bot.yml`**

Delete the whole `Reconstruct Google credential files from secrets` step. Replace the final step's `env` block with:

```yaml
      - name: Poll Telegram for commands
        env:
          GH_DISPATCH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          CHANNELS_SECRETS_JSON: ${{ secrets.CHANNELS_SECRETS_JSON }}
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          META_ACCESS_TOKEN: ${{ secrets.META_ACCESS_TOKEN }}
        run: python telegram_commands.py
```

Everything else in the file stays.

- [ ] **Step 3: Replace `.env.example` entirely**

```bash
# ── Shared secrets ─────────────────────────────────────────────────────────
# Per-channel config lives in channels.json (committed) and
# secrets/channels_secrets.json (gitignored) — manage both with the local
# config web app:  python -m webapp

# Groq API key for generating title/description/tags (free tier: console.groq.com/keys)
GROQ_API_KEY=

# Meta / Instagram Graph API token — ONE token covers every IG account
# linked to your Facebook login (see README)
META_ACCESS_TOKEN=

# Optional: Telegram bot for notifications + remote commands (see README)
# Create a bot with @BotFather; chat ID is your private chat with the bot
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# How many days to keep published entries in each channel's publish queue (default: 30)
QUEUE_PRUNE_DAYS=30
```

- [ ] **Step 4: Sanity-check YAML and commit**

Run: `venv/bin/python -c "import json,sys;
try:
    import yaml
except ImportError:
    sys.exit(0)
[yaml.safe_load(open(p)) for p in ['.github/workflows/pipeline.yml', '.github/workflows/telegram-bot.yml']]
print('yaml ok')"`
(If PyYAML isn't installed the check is skipped — rely on careful review; GitHub validates on push.)

Run full suite: `venv/bin/python -m unittest discover -s tests -v`

```bash
git add .github/workflows/pipeline.yml .github/workflows/telegram-bot.yml .env.example
git commit -m "Workflows: single self-scheduling job, channel-aware dispatch

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Channel-aware Telegram commands

**Files:**
- Modify: `telegram_commands.py`
- Modify: `tests/test_telegram_commands.py` (rewrite parse/handle tests)

**Interfaces:**
- Consumes: `load_channels()` (Task 1), workflow dispatch inputs contract (Task 9), `uploader.list_drive_videos`, `run_pipeline` state layout.
- Produces: commands `/status`, `/upload [slug] [HH:MM]`, `/publish`, `/publishnow [slug]`; `parse_command(text) -> tuple | None` returning `("upload", {"channel": str|None, "slot": str|None})`, `("publishnow", {"channel": str|None})`, `("status"|"publish"|"unknown", {})`; `resolve_channel(requested: str|None, channels: list[str]) -> tuple[str|None, str|None]` (slug, error-reply); `handle_message(text, deps)` where `deps = {"dispatch": fn(inputs), "build_status": fn() -> str, "channels": [enabled slugs]}`.

- [ ] **Step 1: Rewrite the parse/handle tests** — replace `tests/test_telegram_commands.py` content:

```python
"""Tests for telegram_commands.py (parsing, channel resolution, routing)."""

import unittest

from telegram_commands import (
    HELP_TEXT,
    authorized_texts,
    handle_message,
    parse_command,
    resolve_channel,
)


class ParseCommandTest(unittest.TestCase):
    def test_status(self):
        self.assertEqual(parse_command("/status"), ("status", {}))

    def test_upload_with_channel_and_slot(self):
        self.assertEqual(
            parse_command("/upload study 17:30"),
            ("upload", {"channel": "study", "slot": "17:30"}),
        )

    def test_upload_slot_only(self):
        self.assertEqual(
            parse_command("/upload 17:30"),
            ("upload", {"channel": None, "slot": "17:30"}),
        )

    def test_upload_channel_only_case_insensitive(self):
        self.assertEqual(
            parse_command("/UPLOAD Study"),
            ("upload", {"channel": "study", "slot": None}),
        )

    def test_upload_bare(self):
        self.assertEqual(
            parse_command("/upload"), ("upload", {"channel": None, "slot": None})
        )

    def test_publishnow_with_channel(self):
        self.assertEqual(
            parse_command("/publishnow memes"), ("publishnow", {"channel": "memes"})
        )

    def test_publishnow_bare(self):
        self.assertEqual(parse_command("/publishnow"), ("publishnow", {"channel": None}))

    def test_publish_distinct_from_publishnow(self):
        self.assertEqual(parse_command("/publish"), ("publish", {}))

    def test_botname_suffix_stripped(self):
        self.assertEqual(parse_command("/status@my_bot"), ("status", {}))

    def test_non_command_text_returns_none(self):
        self.assertIsNone(parse_command("hello there"))

    def test_unknown_command(self):
        self.assertEqual(parse_command("/frobnicate"), ("unknown", {}))


class ResolveChannelTest(unittest.TestCase):
    def test_explicit_valid_slug(self):
        self.assertEqual(resolve_channel("study", ["study", "memes"]), ("study", None))

    def test_explicit_unknown_slug_errors(self):
        slug, err = resolve_channel("nope", ["study"])
        self.assertIsNone(slug)
        self.assertIn("nope", err)

    def test_omitted_with_single_channel_defaults(self):
        self.assertEqual(resolve_channel(None, ["study"]), ("study", None))

    def test_omitted_with_multiple_channels_errors(self):
        slug, err = resolve_channel(None, ["study", "memes"])
        self.assertIsNone(slug)
        self.assertIn("study", err)
        self.assertIn("memes", err)


class HandleMessageTest(unittest.TestCase):
    def setUp(self):
        self.dispatched = []
        self.deps = {
            "dispatch": self.dispatched.append,
            "build_status": lambda: "STATUS",
            "channels": ["study"],
        }

    def test_status(self):
        self.assertEqual(handle_message("/status", self.deps), "STATUS")

    def test_upload_defaults_to_single_channel(self):
        reply = handle_message("/upload 17:30", self.deps)
        self.assertEqual(
            self.dispatched,
            [{"channel": "study", "action": "upload", "upload_slot": "17:30"}],
        )
        self.assertIn("study", reply)

    def test_upload_without_slot_sends_empty_slot(self):
        handle_message("/upload", self.deps)
        self.assertEqual(
            self.dispatched,
            [{"channel": "study", "action": "upload", "upload_slot": ""}],
        )

    def test_upload_ambiguous_channel_asks_instead_of_dispatching(self):
        self.deps["channels"] = ["study", "memes"]
        reply = handle_message("/upload", self.deps)
        self.assertEqual(self.dispatched, [])
        self.assertIn("study", reply)

    def test_publish_dispatches_plain_pass(self):
        handle_message("/publish", self.deps)
        self.assertEqual(self.dispatched, [{}])

    def test_publishnow_dispatches_action(self):
        handle_message("/publishnow study", self.deps)
        self.assertEqual(
            self.dispatched, [{"channel": "study", "action": "publishnow"}]
        )

    def test_unknown_gets_help(self):
        self.assertEqual(handle_message("/wat", self.deps), HELP_TEXT)
        self.assertEqual(handle_message("hi", self.deps), HELP_TEXT)


class AuthorizedTextsTest(unittest.TestCase):
    def test_filters_by_chat_id(self):
        updates = [
            {"message": {"chat": {"id": 111}, "text": "/status"}},
            {"message": {"chat": {"id": 222}, "text": "/upload"}},
            {"message": {"chat": {"id": 111}}},  # no text
        ]
        self.assertEqual(list(authorized_texts(updates, "111")), ["/status"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m unittest tests.test_telegram_commands -v`
Expected: FAIL — old parse shape, no `resolve_channel`.

- [ ] **Step 3: Rewrite `telegram_commands.py` logic**

Add `from __future__ import annotations` and `import re`. Replace `HELP_TEXT`, `_VALID_SLOTS`, `parse_command`, `handle_message`; add `resolve_channel`. Keep `authorized_texts`, `_api`, `fetch_and_ack_updates`, `dispatch_pipeline`, `main` (with the small edits below).

```python
HELP_TEXT = (
    "Commands:\n"
    "/status — every channel's queue and pipeline state\n"
    "/upload [channel] [HH:MM] — queue next video (channel optional when only one)\n"
    "/publish — publish anything that is due now\n"
    "/publishnow [channel] — publish the next queued video immediately"
)

_TIME_ARG_RE = re.compile(r"^\d{1,2}:\d{2}$")


def parse_command(text: str):
    """Return (command, args) for a bot command, or None for non-command text."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split()
    cmd = parts[0][1:].split("@")[0].lower()

    if cmd == "status":
        return ("status", {})
    if cmd == "publish":
        return ("publish", {})
    if cmd == "publishnow":
        channel = parts[1].lower() if len(parts) > 1 else None
        return ("publishnow", {"channel": channel})
    if cmd == "upload":
        channel = None
        slot = None
        for arg in parts[1:]:
            if _TIME_ARG_RE.match(arg):
                slot = arg
            else:
                channel = arg.lower()
        return ("upload", {"channel": channel, "slot": slot})
    return ("unknown", {})


def resolve_channel(requested, channels):
    """Map an optional requested slug to a concrete one.

    Returns (slug, None) on success or (None, error_reply) when the user
    must specify (unknown slug, or several channels and none given).
    """
    if requested:
        if requested in channels:
            return requested, None
        return None, (
            f"Unknown channel '{requested}'. "
            f"Channels: {', '.join(channels) or 'none configured'}"
        )
    if len(channels) == 1:
        return channels[0], None
    return None, f"Which channel? One of: {', '.join(channels) or 'none configured'}"


def handle_message(text: str, deps: dict) -> str:
    """Route one authorized message; return the reply text.

    deps: {"dispatch": fn(inputs: dict), "build_status": fn() -> str,
           "channels": [enabled channel slugs]}
    """
    parsed = parse_command(text)
    command, args = parsed if parsed else ("unknown", {})
    channels = deps.get("channels", [])

    if command == "status":
        return deps["build_status"]()
    if command == "upload":
        slug, err = resolve_channel(args["channel"], channels)
        if err:
            return err
        deps["dispatch"](
            {"channel": slug, "action": "upload", "upload_slot": args["slot"] or ""}
        )
        slot_note = f" for {args['slot']}" if args["slot"] else ""
        return (
            f"⏳ [{slug}] Queuing next video{slot_note} — "
            "you'll get the preview link when it's uploaded."
        )
    if command == "publish":
        deps["dispatch"]({})
        return "⏳ Running a publish check — anything due goes live now."
    if command == "publishnow":
        slug, err = resolve_channel(args["channel"], channels)
        if err:
            return err
        deps["dispatch"]({"channel": slug, "action": "publishnow"})
        return f"⏳ [{slug}] Force-publishing the next queued video."
    return HELP_TEXT
```

Rewrite `build_status` to loop channels (replaces the old single-channel body):

```python
def build_status() -> str:
    """Assemble the /status reply. Each section is best-effort."""
    lines = ["📊 <b>Pipeline status</b>"]

    try:
        # Lazy imports: these need Google creds + full env, only /status uses them.
        from googleapiclient.discovery import build as gbuild

        from auth import get_credentials
        from config import load_channels
        from uploader import list_drive_videos
        from utils import load_json

        for ch in load_channels():
            if not ch.enabled:
                lines.append(f"⏸ <b>{ch.display_name}</b> — paused")
                continue
            lines.append(f"📺 <b>{ch.display_name}</b> ({ch.slug})")
            if not ch.has_secrets():
                lines.append("  ⚠️ Secrets missing — channel is being skipped")
                continue
            try:
                drive = gbuild("drive", "v3", credentials=get_credentials(ch.google_token))
                videos = list_drive_videos(drive, ch.drive_folder_id)
                processed = set(
                    load_json(ch.log_file, {"processed_file_ids": []})["processed_file_ids"]
                )
                remaining = [v for v in videos if v["id"] not in processed]
                lines.append(f"  🎬 {len(remaining)} videos left ({len(videos)} total)")

                queue = load_json(ch.queue_file, [])
                pending = [q for q in queue if not q.get("published")]
                if pending:
                    for item in pending:
                        lines.append(
                            f"  📋 {item['slot']} queued → live at {item['go_live_at']}"
                        )
                else:
                    lines.append("  📋 Nothing queued right now")
            except Exception as exc:
                lines.append(f"  ⚠️ Could not read state: {exc}")
    except Exception as exc:
        lines.append(f"⚠️ Could not load channel config: {exc}")

    try:
        from check_token_expiry import meta_token_days_left

        days = meta_token_days_left()
        if days is not None:
            lines.append(f"🔑 Meta token: {days:.0f} days left")
    except Exception as exc:
        lines.append(f"⚠️ Could not check Meta token: {exc}")

    return "\n".join(lines)
```

In `main()`, build the enabled-slugs list for deps (best-effort — config errors must not kill the poller):

```python
    try:
        from config import load_channels

        enabled = [c.slug for c in load_channels() if c.enabled]
    except Exception as exc:
        logger.warning("Could not load channels: %s", exc)
        enabled = []

    deps = {
        "dispatch": dispatch_pipeline,
        "build_status": build_status,
        "channels": enabled,
    }
```

`dispatch_pipeline` and module docstring: update the docstring's command list to the new one; `dispatch_pipeline` itself is unchanged (the new `inputs` dicts flow straight through to the Task 9 workflow inputs). Change its hardcoded `"ref": "main"` to `"ref": os.environ.get("GITHUB_REF_NAME", "main")` so branch testing dispatches the branch's workflow.

- [ ] **Step 4: Run tests to verify pass**

Run: `venv/bin/python -m unittest tests.test_telegram_commands -v`
Expected: PASS

- [ ] **Step 5: Also update the Telegram command menu note + full suite + commit**

The bot's `setMyCommands` menu and reply keyboard (Telegram-side config) still show `/upload A|B` — after MERGE they must be re-applied with the new command list. Add that as a checklist line in the plan's Task 14 rollout notes (do not call the Telegram API from CI now).

Run: `venv/bin/python -m unittest discover -s tests -v`

```bash
git add telegram_commands.py tests/test_telegram_commands.py
git commit -m "Telegram: channel-aware commands and multi-channel status

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Web app services layer (`webapp/services.py`)

**Files:**
- Create: `webapp/__init__.py` (empty file)
- Create: `webapp/services.py`
- Create: `requirements-webapp.txt`
- Test: `tests/test_webapp_services.py`

**Interfaces:**
- Consumes: `config.py` (`CHANNELS_FILE`, `SECRETS_FILE`, `ConfigError`, `load_channels`, `validate_config`), `utils.load_json`/`save_json`, `run_pipeline.next_slot_occurrence`, `check_token_expiry.meta_token_days_left`, `auth.SCOPES`, shell `git`/`gh`.
- Produces (all used by Task 12's routes): `refresh_state() -> str|None` (pull error or None), `channels_with_status() -> list[dict]` (keys: `ch: Channel`, `pending`, `recent`, `next_slot`, `has_secrets`), `get_channel(slug) -> dict|None` (form-shaped, includes `youtube_connected: bool`), `upsert_channel(slug, form, new: bool)` (raises `ConfigError`), `toggle_channel(slug)`, `delete_channel(slug)`, `connect_youtube(slug)`, `list_ig_accounts() -> list[dict]` (`{id, username, page}`), `trigger_action(slug, action, upload_slot="")`, `shared_settings() -> dict`, `save_shared_settings(form)`, `deploy_status() -> dict` (`config_dirty`, `secrets_changed`, `shared_changed: list`, `clean`), `deploy_plan() -> dict` (`status`, `config_diff`), `deploy() -> list[str]`, `legacy_import_available() -> bool`, `run_legacy_import() -> str`, `meta_token_days() -> float|None`. Module constant `ROOT` (repo root) — tests monkeypatch it.

- [ ] **Step 1: Create `requirements-webapp.txt` and install**

```
flask>=3.0
```

Run: `venv/bin/pip install -r requirements-webapp.txt`
(Flask stays out of `requirements.txt` so CI never installs it.)

- [ ] **Step 2: Write the failing tests** — `tests/test_webapp_services.py`:

```python
"""Tests for webapp.services file plumbing (git/gh calls patched out)."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from config import ConfigError
from webapp import services


def seed_config(root, channels=None):
    data = {"channels": channels if channels is not None else [{
        "slug": "study",
        "display_name": "Study Motivation",
        "enabled": True,
        "timezone": "Asia/Kolkata",
        "slots": ["17:30"],
        "upload_lead_hours": 8,
        "content_description": "study shorts",
        "youtube_category_id": "22",
    }]}
    with open(os.path.join(root, "channels.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


FORM = {
    "display_name": "Meme Channel",
    "enabled": "on",
    "timezone": "Asia/Kolkata",
    "slots": "12:00, 18:00",
    "upload_lead_hours": "6",
    "content_description": "memes",
    "youtube_category_id": "23",
    "drive_folder_id": "folder123",
    "ig_business_account_id": "ig456",
}


class ServicesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(services, "ROOT", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        seed_config(self.tmp.name)

    def test_upsert_new_channel_writes_config_and_secrets(self):
        services.upsert_channel("memes", FORM, new=True)
        cfg = json.load(open(os.path.join(self.tmp.name, "channels.json")))
        memes = next(c for c in cfg["channels"] if c["slug"] == "memes")
        self.assertEqual(memes["slots"], ["12:00", "18:00"])
        self.assertEqual(memes["upload_lead_hours"], 6)
        secrets = json.load(
            open(os.path.join(self.tmp.name, "secrets", "channels_secrets.json"))
        )
        self.assertEqual(secrets["memes"]["drive_folder_id"], "folder123")
        self.assertEqual(secrets["memes"]["ig_business_account_id"], "ig456")

    def test_upsert_duplicate_new_slug_rejected(self):
        with self.assertRaises(ConfigError):
            services.upsert_channel("study", FORM, new=True)

    def test_upsert_invalid_slot_rejected_and_config_untouched(self):
        bad = dict(FORM, slots="25:99")
        with self.assertRaises(ConfigError):
            services.upsert_channel("memes", bad, new=True)
        cfg = json.load(open(os.path.join(self.tmp.name, "channels.json")))
        self.assertEqual([c["slug"] for c in cfg["channels"]], ["study"])

    def test_get_channel_merges_secret_fields(self):
        services.upsert_channel("memes", FORM, new=True)
        ch = services.get_channel("memes")
        self.assertEqual(ch["drive_folder_id"], "folder123")
        self.assertFalse(ch["youtube_connected"])
        self.assertIsNone(services.get_channel("nope"))

    def test_toggle_channel_flips_enabled(self):
        services.toggle_channel("study")
        cfg = json.load(open(os.path.join(self.tmp.name, "channels.json")))
        self.assertFalse(cfg["channels"][0]["enabled"])

    def test_delete_channel_removes_config_and_secrets(self):
        services.upsert_channel("memes", FORM, new=True)
        services.delete_channel("memes")
        cfg = json.load(open(os.path.join(self.tmp.name, "channels.json")))
        self.assertEqual([c["slug"] for c in cfg["channels"]], ["study"])
        secrets = json.load(
            open(os.path.join(self.tmp.name, "secrets", "channels_secrets.json"))
        )
        self.assertNotIn("memes", secrets)

    def test_run_legacy_import_targets_first_channel_without_token(self):
        with open(os.path.join(self.tmp.name, "token.json"), "w") as fh:
            json.dump({"refresh_token": "r", "client_id": "c", "client_secret": "s"}, fh)
        with open(os.path.join(self.tmp.name, ".env"), "w") as fh:
            fh.write("DRIVE_FOLDER_ID=legacyfolder\nIG_BUSINESS_ACCOUNT_ID=legacyig\n")
        summary = services.run_legacy_import()
        self.assertIn("study", summary)
        secrets = json.load(
            open(os.path.join(self.tmp.name, "secrets", "channels_secrets.json"))
        )
        self.assertEqual(secrets["study"]["google_token"]["refresh_token"], "r")
        self.assertEqual(secrets["study"]["drive_folder_id"], "legacyfolder")

    def test_trigger_action_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            services.trigger_action("study", "detonate")

    def test_trigger_action_runs_gh_workflow(self):
        with patch.object(services, "_run", return_value=(0, "")) as run:
            services.trigger_action("study", "upload", "17:30")
        args = run.call_args[0][0]
        self.assertEqual(args[:3], ["gh", "workflow", "run"])
        self.assertIn("channel=study", " ".join(args))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/python -m unittest tests.test_webapp_services -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webapp'`

- [ ] **Step 4: Create `webapp/__init__.py` (empty) and `webapp/services.py`**

```python
"""
Filesystem / git / gh / Graph-API plumbing for the local config web app.

Everything operates on the repo this app runs from. GitHub interactions
shell out to `git` and `gh` (already authenticated on this machine) rather
than embedding API clients. Nothing here talks to GitHub until deploy().
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values, set_key

from config import CHANNELS_FILE, ConfigError, load_channels, validate_config
from utils import load_json, save_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRETS_DIR = "secrets"
SECRETS_BASENAME = os.path.join(SECRETS_DIR, "channels_secrets.json")
FINGERPRINT_BASENAME = os.path.join(SECRETS_DIR, ".deployed.json")
SHARED_SECRET_KEYS = [
    "META_ACCESS_TOKEN",
    "GROQ_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]
WORKFLOW = "pipeline.yml"
GRAPH_URL = "https://graph.facebook.com/v21.0"


def _p(*parts) -> str:
    """Path under the repo root (ROOT is patched in tests)."""
    return os.path.join(ROOT, *parts)


def _run(args, **kw):
    """Run a command in the repo root; return (returncode, combined output)."""
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, **kw)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# ---------------------------------------------------------------------------
# Local file IO
# ---------------------------------------------------------------------------

def read_config() -> dict:
    return load_json(_p(CHANNELS_FILE), {"channels": []})


def write_config(data: dict) -> None:
    validate_config(data)  # raises ConfigError before touching disk
    save_json(_p(CHANNELS_FILE), data)


def read_secrets_file() -> dict:
    return load_json(_p(SECRETS_BASENAME), {})


def write_secrets_file(data: dict) -> None:
    os.makedirs(_p(SECRETS_DIR), exist_ok=True)
    save_json(_p(SECRETS_BASENAME), data)


def _env() -> dict:
    return dotenv_values(_p(".env")) if os.path.exists(_p(".env")) else {}


# ---------------------------------------------------------------------------
# Channel CRUD (local only — nothing reaches GitHub until deploy())
# ---------------------------------------------------------------------------

def get_channel(slug: str):
    raw = next((c for c in read_config()["channels"] if c["slug"] == slug), None)
    if raw is None:
        return None
    sec = read_secrets_file().get(slug, {})
    merged = dict(raw)
    merged["drive_folder_id"] = sec.get("drive_folder_id", "")
    merged["ig_business_account_id"] = sec.get("ig_business_account_id", "")
    merged["youtube_connected"] = bool(sec.get("google_token"))
    return merged


def upsert_channel(slug: str, form, new: bool) -> None:
    cfg = read_config()
    existing = next((c for c in cfg["channels"] if c["slug"] == slug), None)
    if new and existing is not None:
        raise ConfigError(f"Channel '{slug}' already exists")
    if not new and existing is None:
        raise ConfigError(f"No channel '{slug}'")

    try:
        lead = int(form.get("upload_lead_hours") or 8)
    except ValueError:
        raise ConfigError("upload_lead_hours must be a whole number")

    entry = {
        "slug": slug,
        "display_name": (form.get("display_name") or "").strip(),
        "enabled": form.get("enabled") == "on",
        "timezone": (form.get("timezone") or "Asia/Kolkata").strip(),
        "slots": [s.strip() for s in (form.get("slots") or "").split(",") if s.strip()],
        "upload_lead_hours": lead,
        "content_description": (form.get("content_description") or "").strip(),
        "youtube_category_id": (form.get("youtube_category_id") or "22").strip(),
    }
    if existing is not None:
        cfg["channels"][cfg["channels"].index(existing)] = entry
    else:
        cfg["channels"].append(entry)
    write_config(cfg)  # validates first; secrets untouched on failure

    secrets = read_secrets_file()
    ch_secrets = secrets.setdefault(slug, {})
    ch_secrets["drive_folder_id"] = (form.get("drive_folder_id") or "").strip()
    ch_secrets["ig_business_account_id"] = (
        form.get("ig_business_account_id") or ""
    ).strip()
    write_secrets_file(secrets)


def toggle_channel(slug: str) -> None:
    cfg = read_config()
    for ch in cfg["channels"]:
        if ch["slug"] == slug:
            ch["enabled"] = not ch.get("enabled", True)
    write_config(cfg)


def delete_channel(slug: str) -> None:
    cfg = read_config()
    cfg["channels"] = [c for c in cfg["channels"] if c["slug"] != slug]
    save_json(_p(CHANNELS_FILE), cfg)  # may leave zero channels; that's valid
    secrets = read_secrets_file()
    if slug in secrets:
        del secrets[slug]
        write_secrets_file(secrets)


# ---------------------------------------------------------------------------
# Dashboard data
# ---------------------------------------------------------------------------

def refresh_state():
    """Pull latest CI-committed state; return an error string or None."""
    code, out = _run(["git", "pull", "--rebase", "--autostash"])
    return None if code == 0 else out


def channels_with_status() -> list:
    from run_pipeline import next_slot_occurrence  # late import avoids cycles

    try:
        channels = load_channels(_p(CHANNELS_FILE), read_secrets_file())
    except ConfigError:
        return []
    out = []
    for ch in channels:
        queue = load_json(_p(ch.queue_file), [])
        pending = [q for q in queue if not q.get("published")]
        recent = [q for q in queue if q.get("published")][-3:]
        tz = ZoneInfo(ch.timezone)
        now = datetime.now(tz)
        next_slot = min(
            (next_slot_occurrence(s, tz, now) for s in ch.slots), default=None
        )
        out.append(
            {
                "ch": ch,
                "pending": pending,
                "recent": recent,
                "next_slot": next_slot,
                "has_secrets": ch.has_secrets(),
            }
        )
    return out


def meta_token_days():
    try:
        os.environ.setdefault(
            "META_ACCESS_TOKEN", (_env().get("META_ACCESS_TOKEN") or "").strip()
        )
        from check_token_expiry import meta_token_days_left

        return meta_token_days_left()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Integrations: Google OAuth, IG account list, workflow dispatch
# ---------------------------------------------------------------------------

def connect_youtube(slug: str) -> None:
    """Run the installed-app OAuth flow in the browser; store token locally."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    from auth import SCOPES

    secrets = read_secrets_file()
    client_config = secrets.get(slug, {}).get("google_client_secret") or load_json(
        _p("client_secret.json"), None
    )
    if not client_config:
        raise RuntimeError(
            "No client_secret.json in the repo root and no per-channel "
            "client secret configured for this channel."
        )
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)
    secrets.setdefault(slug, {})["google_token"] = json.loads(creds.to_json())
    write_secrets_file(secrets)


def list_ig_accounts() -> list:
    token = (_env().get("META_ACCESS_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("META_ACCESS_TOKEN is not set in .env")
    resp = requests.get(
        f"{GRAPH_URL}/me/accounts",
        params={"fields": "name,instagram_business_account{id,username}"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    accounts = []
    for page in resp.json().get("data", []):
        ig = page.get("instagram_business_account")
        if ig:
            accounts.append(
                {
                    "id": ig["id"],
                    "username": ig.get("username", "?"),
                    "page": page.get("name", ""),
                }
            )
    return accounts


def trigger_action(slug: str, action: str, upload_slot: str = "") -> None:
    if action not in ("upload", "publishnow"):
        raise ValueError(f"Unknown action {action!r}")
    code, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    fields = ["-f", f"channel={slug}", "-f", f"action={action}"]
    if action == "upload":
        fields += ["-f", f"upload_slot={upload_slot}"]
    code, out = _run(
        ["gh", "workflow", "run", WORKFLOW, "--ref", branch or "main", *fields]
    )
    if code != 0:
        raise RuntimeError(out)


# ---------------------------------------------------------------------------
# Shared settings (.env)
# ---------------------------------------------------------------------------

def shared_settings() -> dict:
    env = _env()
    return {k: env.get(k, "") or "" for k in SHARED_SECRET_KEYS}


def save_shared_settings(form) -> None:
    env_path = _p(".env")
    if not os.path.exists(env_path):
        open(env_path, "a").close()
    for key in SHARED_SECRET_KEYS:
        if key in form:
            set_key(env_path, key, (form.get(key) or "").strip())


# ---------------------------------------------------------------------------
# Deploy: local edits -> git push + GitHub secrets
# ---------------------------------------------------------------------------

def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def deploy_status() -> dict:
    _, out = _run(["git", "status", "--porcelain", CHANNELS_FILE])
    config_dirty = bool(out.strip())
    fp = load_json(_p(FINGERPRINT_BASENAME), {})
    blob = json.dumps(read_secrets_file(), sort_keys=True)
    secrets_changed = fp.get("channels_secrets") != _fingerprint(blob)
    env = _env()
    shared_changed = [
        k
        for k in SHARED_SECRET_KEYS
        if (env.get(k) or "").strip()
        and fp.get(k) != _fingerprint((env.get(k) or "").strip())
    ]
    return {
        "config_dirty": config_dirty,
        "secrets_changed": secrets_changed,
        "shared_changed": shared_changed,
        "clean": not (config_dirty or secrets_changed or shared_changed),
    }


def deploy_plan() -> dict:
    _, diff = _run(["git", "diff", CHANNELS_FILE])
    return {"status": deploy_status(), "config_diff": diff}


def deploy() -> list:
    """Apply local edits to GitHub. Each part is independent and best-effort."""
    results = []
    status = deploy_status()
    fp = load_json(_p(FINGERPRINT_BASENAME), {})

    if status["config_dirty"]:
        _run(["git", "add", CHANNELS_FILE])
        code, out = _run(["git", "commit", "-m", "Update channel config via web UI"])
        if code == 0:
            code, out = _run(["git", "push"])
        results.append(
            "channels.json: pushed" if code == 0 else f"channels.json FAILED: {out}"
        )

    if status["secrets_changed"]:
        blob = json.dumps(read_secrets_file(), sort_keys=True)
        code, out = _run(
            ["gh", "secret", "set", "CHANNELS_SECRETS_JSON"], input=blob
        )
        if code == 0:
            fp["channels_secrets"] = _fingerprint(blob)
            results.append("CHANNELS_SECRETS_JSON: updated")
        else:
            results.append(f"CHANNELS_SECRETS_JSON FAILED: {out}")

    env = _env()
    for key in status["shared_changed"]:
        value = (env.get(key) or "").strip()
        code, out = _run(["gh", "secret", "set", key], input=value)
        if code == 0:
            fp[key] = _fingerprint(value)
            results.append(f"{key}: updated")
        else:
            results.append(f"{key} FAILED: {out}")

    os.makedirs(_p(SECRETS_DIR), exist_ok=True)
    save_json(_p(FINGERPRINT_BASENAME), fp)
    return results or ["Nothing to deploy — everything is in sync."]


# ---------------------------------------------------------------------------
# Legacy import (single-channel .env/token.json era -> secrets blob)
# ---------------------------------------------------------------------------

def _import_target():
    """First configured channel that has no google_token yet, or None."""
    secrets = read_secrets_file()
    for ch in read_config()["channels"]:
        if not secrets.get(ch["slug"], {}).get("google_token"):
            return ch["slug"]
    return None


def legacy_import_available() -> bool:
    return os.path.exists(_p("token.json")) and _import_target() is not None


def run_legacy_import() -> str:
    slug = _import_target()
    if slug is None:
        raise RuntimeError("Every configured channel already has a Google token.")
    if not os.path.exists(_p("token.json")):
        raise RuntimeError("token.json not found in the repo root.")
    with open(_p("token.json"), encoding="utf-8") as fh:
        token = json.load(fh)
    env = _env()
    secrets = read_secrets_file()
    entry = secrets.setdefault(slug, {})
    if not entry.get("drive_folder_id"):
        entry["drive_folder_id"] = (env.get("DRIVE_FOLDER_ID") or "").strip()
    if not entry.get("ig_business_account_id"):
        entry["ig_business_account_id"] = (env.get("IG_BUSINESS_ACCOUNT_ID") or "").strip()
    entry["google_token"] = token
    write_secrets_file(secrets)
    return (
        f"Imported legacy .env/token.json into secrets for channel '{slug}'. "
        "Review, then Deploy to push CHANNELS_SECRETS_JSON to GitHub."
    )
```

- [ ] **Step 5: Run tests to verify pass**

Run: `venv/bin/python -m unittest tests.test_webapp_services -v`
Expected: PASS

- [ ] **Step 6: Full suite + commit**

```bash
git add webapp/__init__.py webapp/services.py requirements-webapp.txt tests/test_webapp_services.py
git commit -m "Web app: services layer for config, secrets, deploy, integrations

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: Web app routes + entry point (`webapp/app.py`, `webapp/__main__.py`)

**Files:**
- Create: `webapp/app.py`
- Create: `webapp/__main__.py`
- Test: `tests/test_webapp_routes.py`

**Interfaces:**
- Consumes: every `webapp.services` function from Task 11 (routes patch them in tests via `patch.object(webapp.app.services, ...)` — `app.py` must access them as `services.fn(...)`, never `from webapp.services import fn`).
- Produces: `create_app() -> Flask` with routes `GET /`, `GET+POST /channels/new`, `GET+POST /channels/<slug>/edit`, `POST /channels/<slug>/toggle`, `POST /channels/<slug>/delete`, `POST /channels/<slug>/connect-youtube`, `GET /api/ig-accounts`, `POST /channels/<slug>/action`, `GET+POST /settings`, `GET /deploy`, `POST /deploy`, `POST /import`. `python -m webapp` serves `127.0.0.1:5001` (5000 collides with macOS AirPlay Receiver). Templates land in Task 13 — until then the test client only exercises JSON/redirect routes.

- [ ] **Step 1: Write the failing tests** — `tests/test_webapp_routes.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m unittest tests.test_webapp_routes -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webapp.app'`

- [ ] **Step 3: Write `webapp/app.py`**

```python
"""Flask app factory + routes for the local config UI (localhost only)."""

from __future__ import annotations

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from config import ConfigError
from webapp import services


def create_app() -> Flask:
    app = Flask(__name__)
    # Only signs the flash-message cookie; the app never leaves 127.0.0.1.
    app.secret_key = "local-config-ui"

    @app.get("/")
    def dashboard():
        pull_error = services.refresh_state()
        return render_template(
            "dashboard.html",
            channels=services.channels_with_status(),
            deploy=services.deploy_status(),
            meta_days=services.meta_token_days(),
            pull_error=pull_error,
            legacy=services.legacy_import_available(),
        )

    @app.get("/channels/new")
    def new_channel():
        return render_template("channel_form.html", ch=None, form={})

    @app.post("/channels/new")
    def create_channel():
        slug = (request.form.get("slug") or "").strip().lower()
        try:
            services.upsert_channel(slug, request.form, new=True)
        except ConfigError as exc:
            flash(str(exc), "error")
            return render_template("channel_form.html", ch=None, form=request.form), 400
        flash(f"Channel '{slug}' saved locally — deploy to apply.", "ok")
        return redirect(url_for("edit_channel", slug=slug))

    @app.get("/channels/<slug>/edit")
    def edit_channel(slug):
        ch = services.get_channel(slug)
        if ch is None:
            flash(f"No channel '{slug}'.", "error")
            return redirect(url_for("dashboard"))
        return render_template("channel_form.html", ch=ch, form={})

    @app.post("/channels/<slug>/edit")
    def update_channel(slug):
        try:
            services.upsert_channel(slug, request.form, new=False)
        except ConfigError as exc:
            flash(str(exc), "error")
            return (
                render_template(
                    "channel_form.html", ch=services.get_channel(slug), form=request.form
                ),
                400,
            )
        flash("Saved locally — deploy to apply.", "ok")
        return redirect(url_for("edit_channel", slug=slug))

    @app.post("/channels/<slug>/toggle")
    def toggle_channel(slug):
        services.toggle_channel(slug)
        return redirect(url_for("dashboard"))

    @app.post("/channels/<slug>/delete")
    def delete_channel(slug):
        services.delete_channel(slug)
        flash(
            f"Channel '{slug}' removed locally — deploy to apply. "
            "Its state/ files were kept on disk.",
            "ok",
        )
        return redirect(url_for("dashboard"))

    @app.post("/channels/<slug>/connect-youtube")
    def connect_youtube(slug):
        try:
            services.connect_youtube(slug)
            flash("YouTube connected — token stored locally. Deploy to apply.", "ok")
        except Exception as exc:
            flash(f"OAuth failed: {exc}", "error")
        return redirect(url_for("edit_channel", slug=slug))

    @app.get("/api/ig-accounts")
    def ig_accounts():
        try:
            return jsonify(services.list_ig_accounts())
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502

    @app.post("/channels/<slug>/action")
    def channel_action(slug):
        try:
            services.trigger_action(
                slug,
                request.form.get("action", ""),
                request.form.get("upload_slot", ""),
            )
            flash(f"Dispatched — watch Telegram for progress.", "ok")
        except Exception as exc:
            flash(f"Dispatch failed: {exc}", "error")
        return redirect(url_for("dashboard"))

    @app.get("/settings")
    def settings():
        return render_template("settings.html", values=services.shared_settings())

    @app.post("/settings")
    def save_settings():
        services.save_shared_settings(request.form)
        flash("Saved locally — deploy to sync changed secrets to GitHub.", "ok")
        return redirect(url_for("settings"))

    @app.get("/deploy")
    def deploy_preview():
        return render_template("deploy.html", plan=services.deploy_plan())

    @app.post("/deploy")
    def deploy():
        for line in services.deploy():
            flash(line, "ok")
        return redirect(url_for("dashboard"))

    @app.post("/import")
    def legacy_import():
        try:
            flash(services.run_legacy_import(), "ok")
        except Exception as exc:
            flash(f"Import failed: {exc}", "error")
        return redirect(url_for("dashboard"))

    return app
```

- [ ] **Step 4: Write `webapp/__main__.py`**

```python
"""Entry point: python -m webapp  →  http://127.0.0.1:5001"""

from __future__ import annotations

import os
import webbrowser

from dotenv import load_dotenv

from webapp.app import create_app
from webapp.services import ROOT


def main() -> None:
    os.chdir(ROOT)  # relative paths (state/, channels.json) resolve to the repo
    load_dotenv()
    app = create_app()
    webbrowser.open("http://127.0.0.1:5001/")
    # 5001, not 5000 — macOS AirPlay Receiver squats on 5000.
    app.run(host="127.0.0.1", port=5001, threaded=True, debug=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify pass**

Run: `venv/bin/python -m unittest tests.test_webapp_routes -v`
Expected: PASS

- [ ] **Step 6: Full suite + commit**

```bash
git add webapp/app.py webapp/__main__.py tests/test_webapp_routes.py
git commit -m "Web app: Flask routes and localhost entry point

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: Web app templates + stylesheet

**Files:**
- Create: `webapp/templates/base.html`
- Create: `webapp/templates/dashboard.html`
- Create: `webapp/templates/channel_form.html`
- Create: `webapp/templates/settings.html`
- Create: `webapp/templates/deploy.html`
- Create: `webapp/static/style.css`
- Test: extend `tests/test_webapp_routes.py`

**Interfaces:**
- Consumes: template context from Task 12's routes (`channels`, `deploy`, `meta_days`, `pull_error`, `legacy`, `ch`, `form`, `values`, `plan`) and Task 11's `channels_with_status()` dict shape.
- Produces: rendered pages; no new Python surface.

> When implementing this task, first load the `frontend-design` skill for visual judgment, but the structure below is the contract — keep every `name=`, route, and context variable exactly as written.

- [ ] **Step 1: Extend the route tests with template-rendering cases** — append to `tests/test_webapp_routes.py`:

```python
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
```

Also remove the `patch.object(webapp_app, "render_template", ...)` shim from `test_create_channel_invalid_returns_400` — with real templates the form re-renders for real; assert `resp.status_code == 400` and `b"bad slug"` NOT required (flash renders after redirect only), keep it simple:

```python
    def test_create_channel_invalid_returns_400(self):
        with patch.object(
            webapp_app.services, "upsert_channel", side_effect=ConfigError("bad slug")
        ):
            resp = self.client.post(
                "/channels/new", data={"slug": "Bad Slug", "display_name": "M"}
            )
        self.assertEqual(resp.status_code, 400)
```

- [ ] **Step 2: Run tests — new ones fail (TemplateNotFound)**

Run: `venv/bin/python -m unittest tests.test_webapp_routes -v`

- [ ] **Step 3: Write the templates**

`webapp/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Uploader Config</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
<header>
  <a class="brand" href="{{ url_for('dashboard') }}">▶ Uploader</a>
  <nav>
    <a href="{{ url_for('dashboard') }}">Dashboard</a>
    <a href="{{ url_for('new_channel') }}">+ Add channel</a>
    <a href="{{ url_for('settings') }}">Settings</a>
    <a class="deploy-link" href="{{ url_for('deploy_preview') }}">Deploy</a>
  </nav>
</header>
{% with messages = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in messages %}<p class="flash {{ cat }}">{{ msg }}</p>{% endfor %}
{% endwith %}
<main>{% block content %}{% endblock %}</main>
</body>
</html>
```

`webapp/templates/dashboard.html`:

```html
{% extends "base.html" %}
{% block content %}
{% if pull_error %}<p class="flash error">git pull failed: {{ pull_error }}</p>{% endif %}
{% if not deploy.clean %}
  <div class="banner">
    Local changes not yet on GitHub
    {%- if deploy.config_dirty %} · channels.json{% endif %}
    {%- if deploy.secrets_changed %} · channel secrets{% endif %}
    {%- for k in deploy.shared_changed %} · {{ k }}{% endfor %} —
    <a href="{{ url_for('deploy_preview') }}">review &amp; deploy</a>
  </div>
{% endif %}
{% if legacy %}
  <div class="banner">
    Legacy single-channel setup detected (.env + token.json).
    <form class="inline" method="post" action="{{ url_for('legacy_import') }}">
      <button type="submit">Import into channel secrets</button>
    </form>
  </div>
{% endif %}

<p class="meta">
  {% if meta_days is not none %}🔑 Meta token: {{ '%.0f' % meta_days }} days left
  {% else %}🔑 Meta token: status unknown{% endif %}
</p>

<div class="cards">
{% for row in channels %}
  {% set ch = row.ch %}
  <section class="card {{ 'paused' if not ch.enabled }}">
    <h2>{{ ch.display_name }} <span class="slug">{{ ch.slug }}</span>
      {% if not ch.enabled %}<span class="badge">paused</span>{% endif %}
      {% if not row.has_secrets %}<span class="badge warn">secrets missing</span>{% endif %}
    </h2>
    <p>Slots: {{ ch.slots|join(' · ') }}
       {% if row.next_slot %} — next live {{ row.next_slot.strftime('%d %b %H:%M') }}{% endif %}</p>

    {% if row.pending %}
      <ul class="queue">
      {% for item in row.pending %}
        <li>📋 {{ item.slot }} → {{ item.go_live_at }}
            <a href="https://youtu.be/{{ item.youtube_video_id }}">preview</a></li>
      {% endfor %}
      </ul>
    {% else %}<p class="dim">Nothing queued.</p>{% endif %}

    {% if row.recent %}
      <ul class="queue dim">
      {% for item in row.recent %}<li>🟢 {{ item.slot }} · went live {{ item.go_live_at }}</li>{% endfor %}
      </ul>
    {% endif %}

    <div class="actions">
      <form class="inline" method="post" action="{{ url_for('channel_action', slug=ch.slug) }}">
        <input type="hidden" name="action" value="upload">
        <select name="upload_slot">
          {% for s in ch.slots %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
        </select>
        <button type="submit">Upload now</button>
      </form>
      <form class="inline" method="post" action="{{ url_for('channel_action', slug=ch.slug) }}">
        <input type="hidden" name="action" value="publishnow">
        <button type="submit">Publish next now</button>
      </form>
      <a class="btn" href="{{ url_for('edit_channel', slug=ch.slug) }}">Edit</a>
      <form class="inline" method="post" action="{{ url_for('toggle_channel', slug=ch.slug) }}">
        <button type="submit">{{ 'Resume' if not ch.enabled else 'Pause' }}</button>
      </form>
      <form class="inline" method="post" action="{{ url_for('delete_channel', slug=ch.slug) }}"
            onsubmit="return confirm('Remove channel {{ ch.slug }}? State files stay on disk.')">
        <button type="submit" class="danger">Delete</button>
      </form>
    </div>
  </section>
{% else %}
  <p>No channels yet — <a href="{{ url_for('new_channel') }}">add one</a>.</p>
{% endfor %}
</div>
{% endblock %}
```

`webapp/templates/channel_form.html`:

```html
{% extends "base.html" %}
{% block content %}
{% set editing = ch is not none %}
<h2>{{ 'Edit ' + ch.display_name if editing else 'New channel' }}</h2>
<form method="post" class="stack"
      action="{{ url_for('update_channel', slug=ch.slug) if editing else url_for('create_channel') }}">
  <label>Slug (permanent ID, lowercase/digits/hyphens)
    <input name="slug" value="{{ form.get('slug', ch.slug if editing else '') }}"
           {{ 'readonly' if editing }} required pattern="[a-z0-9-]+">
  </label>
  <label>Display name
    <input name="display_name" value="{{ form.get('display_name', ch.display_name if editing else '') }}" required>
  </label>
  <label><input type="checkbox" name="enabled"
         {{ 'checked' if form.get('enabled') == 'on' or (not form and (not editing or ch.enabled)) }}> Enabled</label>
  <label>Timezone (IANA)
    <input name="timezone" value="{{ form.get('timezone', ch.timezone if editing else 'Asia/Kolkata') }}">
  </label>
  <label>Go-live slots (comma-separated HH:MM)
    <input name="slots" value="{{ form.get('slots', ch.slots|join(', ') if editing else '17:30, 21:30') }}" required>
  </label>
  <label>Upload lead (hours before go-live)
    <input name="upload_lead_hours" type="number" min="1" max="23"
           value="{{ form.get('upload_lead_hours', ch.upload_lead_hours if editing else 8) }}">
  </label>
  <label>Content description (steers AI titles/captions)
    <input name="content_description"
           value="{{ form.get('content_description', ch.content_description if editing else '') }}">
  </label>
  <label>YouTube category ID
    <input name="youtube_category_id"
           value="{{ form.get('youtube_category_id', ch.youtube_category_id if editing else '22') }}">
  </label>
  <label>Drive folder ID (source videos)
    <input name="drive_folder_id"
           value="{{ form.get('drive_folder_id', ch.drive_folder_id if editing else '') }}">
  </label>
  <label>Instagram business account ID
    <input id="ig-id" name="ig_business_account_id"
           value="{{ form.get('ig_business_account_id', ch.ig_business_account_id if editing else '') }}">
  </label>
  <p><button type="button" onclick="loadIg()">Load my IG accounts</button>
     <span id="ig-list" class="dim"></span></p>
  <p><button type="submit" class="primary">Save locally</button></p>
</form>

{% if editing %}
<form method="post" action="{{ url_for('connect_youtube', slug=ch.slug) }}">
  <p>YouTube: {{ '✅ connected' if ch.youtube_connected else '❌ not connected' }}
    <button type="submit">{{ 'Reconnect' if ch.youtube_connected else 'Connect YouTube' }}</button>
    <span class="dim">(opens a Google sign-in in your browser — pick this channel's account)</span>
  </p>
</form>
{% endif %}

<script>
async function loadIg() {
  const el = document.getElementById('ig-list');
  el.textContent = 'Loading…';
  const resp = await fetch('{{ url_for('ig_accounts') }}');
  const data = await resp.json();
  if (!resp.ok) { el.textContent = data.error; return; }
  el.innerHTML = data.map(a =>
    `<button type="button" onclick="document.getElementById('ig-id').value='${a.id}'">` +
    `@${a.username} (${a.page})</button>`).join(' ');
}
</script>
{% endblock %}
```

`webapp/templates/settings.html`:

```html
{% extends "base.html" %}
{% block content %}
<h2>Shared settings</h2>
<p class="dim">Stored in .env locally; changed values are pushed as GitHub secrets on Deploy.</p>
<form method="post" class="stack">
  {% for key, value in values.items() %}
  <label>{{ key }}
    <input name="{{ key }}" value="{{ value }}" autocomplete="off">
  </label>
  {% endfor %}
  <p><button type="submit" class="primary">Save locally</button></p>
</form>
{% endblock %}
```

`webapp/templates/deploy.html`:

```html
{% extends "base.html" %}
{% block content %}
<h2>Deploy</h2>
{% if plan.status.clean %}
  <p>✅ Everything is in sync with GitHub.</p>
{% else %}
  <ul>
    {% if plan.status.config_dirty %}<li>channels.json will be committed &amp; pushed</li>{% endif %}
    {% if plan.status.secrets_changed %}<li>CHANNELS_SECRETS_JSON secret will be updated</li>{% endif %}
    {% for k in plan.status.shared_changed %}<li>{{ k }} secret will be updated</li>{% endfor %}
  </ul>
  {% if plan.config_diff %}<pre class="diff">{{ plan.config_diff }}</pre>{% endif %}
  <form method="post"><button type="submit" class="primary">Deploy to GitHub</button></form>
{% endif %}
{% endblock %}
```

`webapp/static/style.css`:

```css
:root {
  --bg: #f6f7f9; --card: #fff; --ink: #1a202c; --dim: #718096;
  --accent: #4c6ef5; --warn: #e8590c; --ok: #2b8a3e; --line: #e2e8f0;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
       font: 15px/1.5 -apple-system, "Segoe UI", sans-serif; }
header { display: flex; align-items: center; gap: 1.5rem;
         padding: .75rem 1.5rem; background: var(--card);
         border-bottom: 1px solid var(--line); }
.brand { font-weight: 700; text-decoration: none; color: var(--ink); }
nav { display: flex; gap: 1rem; }
nav a { color: var(--dim); text-decoration: none; }
nav a:hover, nav a.deploy-link { color: var(--accent); }
main { max-width: 60rem; margin: 1.5rem auto; padding: 0 1.5rem; }
.cards { display: grid; gap: 1rem; }
.card { background: var(--card); border: 1px solid var(--line);
        border-radius: 10px; padding: 1rem 1.25rem; }
.card.paused { opacity: .65; }
.card h2 { margin: 0 0 .25rem; font-size: 1.1rem; }
.slug { color: var(--dim); font-weight: 400; font-size: .85rem; }
.badge { font-size: .7rem; padding: .1rem .5rem; border-radius: 99px;
         background: var(--line); vertical-align: middle; }
.badge.warn { background: #ffe8cc; color: var(--warn); }
.queue { list-style: none; padding: 0; margin: .5rem 0; }
.dim { color: var(--dim); }
.actions { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center;
           margin-top: .75rem; border-top: 1px solid var(--line); padding-top: .75rem; }
.inline { display: inline-flex; gap: .35rem; }
button, .btn { border: 1px solid var(--line); background: var(--card);
               border-radius: 6px; padding: .35rem .8rem; cursor: pointer;
               font: inherit; text-decoration: none; color: var(--ink); }
button:hover, .btn:hover { border-color: var(--accent); color: var(--accent); }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.danger:hover { border-color: var(--warn); color: var(--warn); }
.flash { padding: .5rem 1rem; border-radius: 8px; margin: .75rem 1.5rem; }
.flash.ok { background: #ebfbee; color: var(--ok); }
.flash.error { background: #fff0eb; color: var(--warn); }
.banner { background: #fff9db; border: 1px solid #ffe066; border-radius: 8px;
          padding: .6rem 1rem; margin-bottom: 1rem; }
.stack { display: grid; gap: .75rem; max-width: 30rem; }
.stack label { display: grid; gap: .25rem; font-size: .9rem; color: var(--dim); }
.stack input { font: inherit; padding: .45rem .6rem; border: 1px solid var(--line);
               border-radius: 6px; color: var(--ink); }
.diff { background: #0b1021; color: #d8dee9; padding: 1rem; border-radius: 8px;
        overflow-x: auto; font-size: .8rem; }
.meta { color: var(--dim); }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `venv/bin/python -m unittest tests.test_webapp_routes -v`
Expected: all PASS (including the template-render tests).

- [ ] **Step 5: Manual smoke test**

Run: `venv/bin/python -m webapp` — browser opens the dashboard showing the `study` card (with a "secrets missing" badge until the legacy import runs — expected). Click through Add channel, Settings, Deploy preview. Ctrl-C to stop. Fix anything broken before committing.

- [ ] **Step 6: Full suite + commit**

```bash
git add webapp/templates webapp/static tests/test_webapp_routes.py
git commit -m "Web app: dashboard, channel form, settings, deploy pages

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 14: README, final verification, push branch

**Files:**
- Modify: `README.md`
- Delete: nothing (old entry scripts already replaced by `git mv` in Tasks 6–7)

**Interfaces:**
- Consumes: everything above.
- Produces: the branch ready for the user's merge decision.

- [ ] **Step 1: Update `README.md`**

Rework these areas (keep the project's existing tone; don't rewrite untouched sections):
1. Architecture overview: replace the two-script `upload_unlisted.py --slot A/B` + `publish_scheduled.py` story with: `channels.json` (+ `CHANNELS_SECRETS_JSON` secret) → `run_pipeline.py` every 15 min → per-channel `state/<slug>/` files.
2. Add a **Multi-channel configuration** section:

```markdown
## Multi-channel configuration

Each channel is an independent niche: its own Drive folder → its own YouTube
channel → its own Instagram account, with its own go-live slots.

- Non-secret config: `channels.json` (committed).
- Per-channel secrets (Drive folder ID, IG account ID, Google token): the
  `CHANNELS_SECRETS_JSON` GitHub secret — locally `secrets/channels_secrets.json`
  (gitignored).
- Shared secrets (one for all channels): `META_ACCESS_TOKEN`, `GROQ_API_KEY`,
  `TELEGRAM_*`.

Configure visually with the local web app:

    pip install -r requirements-webapp.txt
    python -m webapp        # opens http://127.0.0.1:5001

The app edits everything locally; the **Deploy** button commits
`channels.json` and updates the GitHub secrets (needs `gh` CLI logged in).

Manual CLI equivalents:

    python run_pipeline.py                                # what CI runs every 15 min
    python run_pipeline.py --channel study --upload-slot 17:30
    python run_pipeline.py --channel study --force-next
```

3. Telegram commands section: update to `/status`, `/upload [channel] [HH:MM]`, `/publish`, `/publishnow [channel]`.
4. YouTube quota note: ~6 uploads/day per Google Cloud project ⇒ ~3 channels × 2 slots; channel 4+ needs its own GCP project via the per-channel `google_client_secret` field.

- [ ] **Step 2: Full-suite verification**

Run: `venv/bin/python -m unittest discover -s tests -v` — every test passes.
Run: `venv/bin/python -c "import run_pipeline, uploader, publisher, telegram_commands, config; from webapp.app import create_app; create_app(); print('imports ok')"`
Expected: `imports ok`.

- [ ] **Step 3: Commit and push the branch**

```bash
git add README.md
git commit -m "Document multi-channel setup and config web app

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push -u origin multi-channel
```

Pushing the branch is safe: GitHub runs `schedule:` triggers only from the default branch, so `main`'s pipeline continues untouched.

- [ ] **Step 4: Record the rollout checklist (NOT executed now — needs the user)**

Post this checklist in the final report; merging is the user's call:

1. Locally: `python -m webapp` → run **Import** (builds `secrets/channels_secrets.json` from `.env`/`token.json`) → **Deploy** (creates the `CHANNELS_SECRETS_JSON` secret; `channels.json` is already committed).
2. Optional pre-merge test: `gh workflow run pipeline.yml --ref multi-channel -f channel=study -f action=publishnow` on a queued item, or just a plain dispatch to watch a no-op pass.
3. Merge `multi-channel` → `main`; watch one 15-min cycle and one full slot cycle.
4. After a clean slot cycle: delete now-unused secrets `DRIVE_FOLDER_ID`, `IG_BUSINESS_ACCOUNT_ID`, `GOOGLE_TOKEN_JSON`, `GOOGLE_CLIENT_SECRET_JSON` (`gh secret delete …`).
5. Re-apply the Telegram bot's `setMyCommands` menu + reply keyboard with the new command list (`/upload [channel] [HH:MM]` etc.) via the Bot API.

---

## Plan Self-Review (completed)

- **Spec coverage:** config model → Tasks 1–2; pipeline refactor → Tasks 3–8; workflow changes → Task 9; Telegram → Task 10; web app (dashboard, channel form + OAuth + IG picker, shared settings, manual actions, deploy bar, legacy import) → Tasks 11–13; error handling → Tasks 8/11/12 (skip-and-warn, ConfigError surfacing, best-effort deploy); testing → every task; migration/rollout → Task 14. Per-channel `google_client_secret` (quota scaling) is carried in the schema (Task 1) and honored by `connect_youtube` (Task 11).
- **Placeholder scan:** the only elision is Task 6's `...` marking three helpers explicitly kept verbatim from the moved file — not new code.
- **Type consistency:** `run_upload(channel, go_live_at, slot)`, `run_publish(channel, now, force_next)`, `get_credentials(token_info)`, `publish_reel(ig_user_id, url, caption)`, `generate_metadata(filename, content_description, extra_context)`, dispatch inputs `{channel, action, upload_slot}` — checked against every consumer task.




