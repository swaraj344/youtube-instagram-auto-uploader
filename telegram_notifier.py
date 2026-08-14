"""
Sends pipeline notifications to a Telegram chat via the Bot API.

Deliberately fail-safe: if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not
configured, notify() is a silent no-op, and any Telegram API error is logged
but never raised — a Telegram outage must never break a publish run.
"""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def notify(text: str) -> bool:
    """Send *text* (HTML formatting allowed) to the configured chat.

    Returns True when the message was accepted by Telegram, False when
    Telegram is unconfigured or the send failed.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.debug("Telegram not configured; skipping notification.")
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)
        return False
