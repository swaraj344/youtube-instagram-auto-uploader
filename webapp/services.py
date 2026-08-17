"""
Filesystem / git / gh / Graph-API plumbing for the local config web app.

Everything operates on the repo this app runs from. Entities live in
config.json (committed) + secrets/pipeline_secrets.json (gitignored).
GitHub interactions shell out to `git` and `gh` (already authenticated on
this machine). Nothing here talks to GitHub until deploy().
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values, set_key

from config import CONFIG_FILE, ConfigError, load_config, validate_config
from utils import load_json, save_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECTIONS = ("sources", "youtube", "instagram")
SECRETS_DIR = "secrets"
SECRETS_BASENAME = os.path.join(SECRETS_DIR, "pipeline_secrets.json")
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
    return load_json(_p(CONFIG_FILE), {"sources": [], "youtube": [], "instagram": []})


def write_config(data: dict) -> None:
    validate_config(data)  # raises ConfigError before touching disk
    save_json(_p(CONFIG_FILE), data)


def read_secrets_file() -> dict:
    return load_json(_p(SECRETS_BASENAME), {})


def write_secrets_file(data: dict) -> None:
    os.makedirs(_p(SECRETS_DIR), exist_ok=True)
    save_json(_p(SECRETS_BASENAME), data)


def _env() -> dict:
    return dotenv_values(_p(".env")) if os.path.exists(_p(".env")) else {}


# ---------------------------------------------------------------------------
# Entity CRUD (local only — nothing reaches GitHub until deploy())
# ---------------------------------------------------------------------------

def _find(cfg: dict, section: str, eid: str):
    return next((e for e in cfg.get(section, []) if e["id"] == eid), None)


def get_entity(section: str, eid: str):
    raw = _find(read_config(), section, eid)
    if raw is None:
        return None
    sec = (read_secrets_file().get(section) or {}).get(eid) or {}
    merged = dict(raw)
    if section == "sources":
        merged["drive_folder_id"] = sec.get("drive_folder_id", "") or ""
        merged["google_connected"] = bool(sec.get("google_token"))
    elif section == "youtube":
        merged["youtube_connected"] = bool(sec.get("google_token"))
    else:
        merged["ig_business_account_id"] = sec.get("ig_business_account_id", "") or ""
    return merged


def _entry_from_form(section: str, eid: str, form) -> dict:
    if section == "sources":
        return {"id": eid, "name": (form.get("name") or "").strip()}
    entry = {
        "id": eid,
        "name": (form.get("name") or "").strip(),
        "source": (form.get("source") or "").strip(),
        "enabled": form.get("enabled") == "on",
        "timezone": (form.get("timezone") or "Asia/Kolkata").strip(),
        "slots": [s.strip() for s in (form.get("slots") or "").split(",") if s.strip()],
        "content_description": (form.get("content_description") or "").strip(),
    }
    if section == "youtube":
        try:
            entry["upload_lead_hours"] = int(form.get("upload_lead_hours") or 8)
        except ValueError:
            raise ConfigError("upload_lead_hours must be a whole number")
        entry["category_id"] = (form.get("category_id") or "22").strip()
    return entry


def upsert_entity(section: str, eid: str, form, new: bool) -> None:
    cfg = read_config()
    existing = _find(cfg, section, eid)
    if new and any(_find(cfg, s, eid) for s in SECTIONS):
        raise ConfigError(f"Id '{eid}' already exists")
    if not new and existing is None:
        raise ConfigError(f"No {section} entry '{eid}'")

    entry = _entry_from_form(section, eid, form)
    if existing is not None:
        cfg[section][cfg[section].index(existing)] = entry
    else:
        cfg.setdefault(section, []).append(entry)
    write_config(cfg)  # validates first; secrets untouched on failure

    secrets = read_secrets_file()
    sec = secrets.setdefault(section, {}).setdefault(eid, {})
    if section == "sources":
        sec["drive_folder_id"] = (form.get("drive_folder_id") or "").strip()
    elif section == "instagram":
        sec["ig_business_account_id"] = (form.get("ig_business_account_id") or "").strip()
    write_secrets_file(secrets)


def delete_entity(section: str, eid: str) -> None:
    cfg = read_config()
    if section == "sources":
        used = [d["id"] for s in ("youtube", "instagram") for d in cfg.get(s, [])
                if d.get("source") == eid]
        if used:
            raise ConfigError(f"Source '{eid}' is used by: {', '.join(used)}")
    cfg[section] = [e for e in cfg.get(section, []) if e["id"] != eid]
    save_json(_p(CONFIG_FILE), cfg)  # may leave zero entities; that's valid
    secrets = read_secrets_file()
    if eid in (secrets.get(section) or {}):
        del secrets[section][eid]
        write_secrets_file(secrets)


def toggle_entity(section: str, eid: str) -> None:
    cfg = read_config()
    for e in cfg.get(section, []):
        if e["id"] == eid:
            e["enabled"] = not e.get("enabled", True)
    write_config(cfg)


# ---------------------------------------------------------------------------
# Dashboard / list data
# ---------------------------------------------------------------------------

def refresh_state():
    """Pull latest CI-committed state; return an error string or None."""
    code, out = _run(["git", "pull", "--rebase", "--autostash"])
    return None if code == 0 else out


def list_entities(section: str) -> list:
    from run_pipeline import next_slot_occurrence  # late import avoids cycles

    try:
        cfg = load_config(_p(CONFIG_FILE), read_secrets_file())
    except ConfigError:
        return []
    out = []
    if section == "sources":
        for src in cfg.sources.values():
            used = [d.id for d in list(cfg.youtube) + list(cfg.instagram)
                    if d.source == src.id]
            out.append({
                "id": src.id,
                "name": src.name,
                "google_connected": bool(src.google_token),
                "has_folder": bool(src.drive_folder_id),
                "used_by": used,
                "upload": _upload_jobs.get(src.id),
            })
        return out
    dests = cfg.youtube if section == "youtube" else cfg.instagram
    for d in dests:
        tz = ZoneInfo(d.timezone)
        now = datetime.now(tz)
        row = {
            "id": d.id,
            "name": d.name,
            "source": d.source,
            "enabled": d.enabled,
            "slots": d.slots,
            "next_slot": min(
                (next_slot_occurrence(s, tz, now) for s in d.slots), default=None
            ),
            "ready": d.has_secrets() and cfg.sources[d.source].has_secrets(),
        }
        if section == "youtube":
            queue = load_json(_p(d.queue_file), [])
            row["pending"] = [q for q in queue if not q.get("published")]
        else:
            slot_log = load_json(_p(d.slot_log_file), {})
            posted = sorted(k for k, v in slot_log.items() if v.get("status") == "posted")
            row["last_posted"] = posted[-1] if posted else None
        out.append(row)
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

def connect_google(section: str, eid: str) -> None:
    """OAuth for a source (Drive owner) or a YouTube destination."""
    if section not in ("sources", "youtube"):
        raise ValueError(f"No Google login for section {section!r}")
    from google_auth_oauthlib.flow import InstalledAppFlow

    from auth import SCOPES

    secrets = read_secrets_file()
    sec = (secrets.get(section) or {}).get(eid) or {}
    client_config = sec.get("google_client_secret") or load_json(
        _p("client_secret.json"), None
    )
    if not client_config:
        raise RuntimeError(
            "No client_secret.json in the repo root and no per-entity "
            "client secret configured."
        )
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)
    secrets.setdefault(section, {}).setdefault(eid, {})["google_token"] = json.loads(
        creds.to_json()
    )
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


def trigger_action(target_id: str, action: str, upload_slot: str = "") -> None:
    if action not in ("upload", "publishnow", "postnow"):
        raise ValueError(f"Unknown action {action!r}")
    code, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    fields = ["-f", f"target={target_id}", "-f", f"action={action}"]
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
    _, out = _run(["git", "status", "--porcelain", CONFIG_FILE, "channels.json", "state"])
    config_dirty = bool(out.strip())
    fp = load_json(_p(FINGERPRINT_BASENAME), {})
    blob = json.dumps(read_secrets_file(), sort_keys=True)
    secrets_changed = fp.get("pipeline_secrets") != _fingerprint(blob)
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
    # --stat because a migration deletes/creates many state files; a full
    # diff would be noise.
    _, diff = _run(
        ["git", "diff", "HEAD", "--stat", "--", CONFIG_FILE, "channels.json", "state"]
    )
    return {"status": deploy_status(), "config_diff": diff}


def deploy() -> list:
    """Apply local edits to GitHub. Each part is independent and best-effort."""
    results = []
    status = deploy_status()
    fp = load_json(_p(FINGERPRINT_BASENAME), {})

    if status["config_dirty"]:
        _run(["git", "add", CONFIG_FILE, "channels.json", "state"])
        code, out = _run(["git", "commit", "-m", "Update pipeline config via web UI"])
        if code == 0:
            code, out = _run(["git", "push"])
        results.append("config: pushed" if code == 0 else f"config FAILED: {out}")

    if status["secrets_changed"]:
        blob = json.dumps(read_secrets_file(), sort_keys=True)
        code, out = _run(["gh", "secret", "set", "PIPELINE_SECRETS_JSON"], input=blob)
        if code == 0:
            fp["pipeline_secrets"] = _fingerprint(blob)
            results.append("PIPELINE_SECRETS_JSON: updated")
        else:
            results.append(f"PIPELINE_SECRETS_JSON FAILED: {out}")

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
# Local folder -> Drive upload (background job with progress)
# ---------------------------------------------------------------------------

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")

# source_id -> mutable progress dict, read by the status endpoint. Jobs live
# for the app's lifetime; a restart mid-upload is safe (dedupe skips what
# already made it to Drive).
_upload_jobs: dict = {}


def pick_local_folder():
    """Open the native macOS folder picker; return a path or None if cancelled."""
    code, out = _run([
        "osascript", "-e",
        'POSIX path of (choose folder with prompt "Choose the folder with your videos")',
    ])
    if code != 0:
        return None
    return out.strip() or None


def upload_status(source_id: str):
    return _upload_jobs.get(source_id)


def _drive_for(token_info: dict):
    """Build a Drive client for a source token (patched in tests)."""
    from googleapiclient.discovery import build as gbuild

    from auth import get_credentials

    return gbuild("drive", "v3", credentials=get_credentials(token_info))


def _check_job_free(source_id: str) -> None:
    job = _upload_jobs.get(source_id)
    if job and job.get("status") == "running":
        raise RuntimeError("An upload/import is already running for this source.")


def _check_google_connected(source_id: str) -> None:
    sec = (read_secrets_file().get("sources") or {}).get(source_id) or {}
    if not sec.get("google_token"):
        raise RuntimeError("Connect Google for this source first (it owns the Drive folder).")


def _ensure_dest_folder(drive, source_id: str, state: dict) -> str:
    """Return the source's Drive folder id, creating the folder on first use."""
    secrets = read_secrets_file()
    sec = secrets["sources"][source_id]
    folder_id = (sec.get("drive_folder_id") or "").strip()
    if folder_id:
        return folder_id
    src = next(
        (s for s in read_config().get("sources", []) if s["id"] == source_id), None
    )
    name = (src or {}).get("name", source_id)
    created = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    folder_id = created["id"]
    secrets["sources"][source_id]["drive_folder_id"] = folder_id
    write_secrets_file(secrets)
    state["created_folder"] = True
    return folder_id


def start_upload(source_id: str, folder: str) -> None:
    """Validate, then upload *folder*'s videos to the source's Drive folder
    in a background thread. Raises RuntimeError on anything the user must fix."""
    _check_job_free(source_id)
    folder = os.path.expanduser((folder or "").strip())
    if not os.path.isdir(folder):
        raise RuntimeError(f"Not a folder: {folder or '(empty)'}")
    _check_google_connected(source_id)
    files = sorted(
        f for f in os.listdir(folder)
        if f.lower().endswith(VIDEO_EXTENSIONS)
        and os.path.isfile(os.path.join(folder, f))
    )
    if not files:
        raise RuntimeError(
            f"No video files ({', '.join(VIDEO_EXTENSIONS)}) found in that folder."
        )

    state = {
        "status": "running", "mode": "upload", "folder": folder, "total": len(files),
        "done": 0, "uploaded": 0, "skipped": 0, "failed": 0,
        "current": "", "current_pct": 0, "error": None,
    }
    _upload_jobs[source_id] = state
    threading.Thread(
        target=_upload_worker, args=(source_id, folder, files, state), daemon=True
    ).start()


def _upload_worker(source_id: str, folder: str, files: list, state: dict) -> None:
    try:
        from googleapiclient.http import MediaFileUpload

        from uploader import list_drive_videos

        sec = read_secrets_file()["sources"][source_id]
        drive = _drive_for(sec["google_token"])
        folder_id = _ensure_dest_folder(drive, source_id, state)

        existing = {f["name"] for f in list_drive_videos(drive, folder_id)}

        for name in files:
            state["current"] = name
            state["current_pct"] = 0
            if name in existing:
                state["skipped"] += 1
                state["done"] += 1
                continue
            path = os.path.join(folder, name)
            try:
                media = MediaFileUpload(
                    path, chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/*"
                )
                request = drive.files().create(
                    body={"name": name, "parents": [folder_id]},
                    media_body=media,
                    fields="id",
                )
                response = None
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        state["current_pct"] = int(status.progress() * 100)
            except Exception as exc:
                state["failed"] += 1
                state.setdefault("errors", []).append(f"{name}: {exc}")
            else:
                state["uploaded"] += 1
            state["done"] += 1

        state["current"] = ""
        state["current_pct"] = 0
        state["status"] = "done"
    except Exception as exc:
        state["status"] = "error"
        state["error"] = str(exc)


# ---------------------------------------------------------------------------
# Shared Drive link -> own Drive import (server-side copy, no local transfer)
# ---------------------------------------------------------------------------

_DRIVE_FOLDER_LINK_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")


def parse_drive_folder_link(link: str):
    """Extract a folder id from a Drive folder URL (or accept a bare id)."""
    link = (link or "").strip()
    m = _DRIVE_FOLDER_LINK_RE.search(link)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", link):
        return link
    return None


def start_import(source_id: str, link: str) -> None:
    """Server-side copy every video from a shared Drive folder into the
    source's own folder, in a background thread."""
    _check_job_free(source_id)
    shared_id = parse_drive_folder_link(link)
    if not shared_id:
        raise RuntimeError(
            "That doesn't look like a Drive folder link "
            "(expected https://drive.google.com/drive/folders/…)."
        )
    _check_google_connected(source_id)

    state = {
        "status": "running", "mode": "import", "link": link, "total": 0,
        "done": 0, "uploaded": 0, "skipped": 0, "failed": 0,
        "current": "listing shared folder…", "current_pct": 0, "error": None,
    }
    _upload_jobs[source_id] = state
    threading.Thread(
        target=_import_worker, args=(source_id, shared_id, state), daemon=True
    ).start()


def _import_worker(source_id: str, shared_id: str, state: dict) -> None:
    try:
        from uploader import list_drive_videos

        sec = read_secrets_file()["sources"][source_id]
        drive = _drive_for(sec["google_token"])
        dest_id = _ensure_dest_folder(drive, source_id, state)

        videos = list_drive_videos(drive, shared_id)
        if not videos:
            state["status"] = "error"
            state["error"] = (
                "No videos found — the link may be wrong, not shared with "
                "your account, or the folder is empty."
            )
            return
        existing = {f["name"] for f in list_drive_videos(drive, dest_id)}
        state["total"] = len(videos)

        for video in videos:
            state["current"] = video["name"]
            if video["name"] in existing:
                state["skipped"] += 1
                state["done"] += 1
                continue
            try:
                drive.files().copy(
                    fileId=video["id"],
                    body={"name": video["name"], "parents": [dest_id]},
                    fields="id",
                ).execute()
            except Exception as exc:
                state["failed"] += 1
                state.setdefault("errors", []).append(f"{video['name']}: {exc}")
            else:
                state["uploaded"] += 1
            state["done"] += 1

        state["current"] = ""
        state["status"] = "done"
    except Exception as exc:
        state["status"] = "error"
        state["error"] = str(exc)


# ---------------------------------------------------------------------------
# Legacy migration (channel model -> sources/destinations)
# ---------------------------------------------------------------------------

def migration_available() -> bool:
    import migration

    return migration.migration_available(ROOT)


def run_migration() -> str:
    import migration

    return migration.migrate(ROOT)
