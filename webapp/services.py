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
