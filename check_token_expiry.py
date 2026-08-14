"""
Daily Meta token expiry check (runs from pipeline.yml, 09:00 IST cron).

Long-lived Meta tokens die after ~60 days and Instagram posting then fails
silently. This asks the Graph API when the current token expires and sends a
Telegram warning during the final week — at most one message per day because
the cron only fires once a day.
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv

from telegram_notifier import notify

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

WARN_AT_DAYS = 7


def days_left(expires_at: "int | None", now_ts: float) -> "float | None":
    """Days until the epoch timestamp *expires_at*; None if the token never expires."""
    if not expires_at:
        return None
    return (expires_at - now_ts) / 86400


def should_warn(days: "float | None") -> bool:
    return days is not None and days <= WARN_AT_DAYS


def meta_token_days_left() -> "float | None":
    """Ask the Graph API how long the configured META_ACCESS_TOKEN has left."""
    token = os.environ.get("META_ACCESS_TOKEN", "").strip()
    if not token:
        return None
    resp = requests.get(
        "https://graph.facebook.com/v21.0/debug_token",
        params={"input_token": token, "access_token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return days_left(resp.json()["data"].get("expires_at"), time.time())


def main() -> None:
    days = meta_token_days_left()
    if should_warn(days):
        logger.warning("Meta token expires in %.1f days — sending Telegram warning.", days)
        notify(
            f"🔑 <b>Meta token expires in {max(days, 0):.0f} day(s)!</b>\n"
            "Instagram posting stops when it does. To refresh: generate a new "
            "token in Graph API Explorer, then have Claude exchange it and "
            "update the META_ACCESS_TOKEN GitHub secret and .env."
        )
    else:
        logger.info(
            "Meta token OK (%s days left).",
            "∞" if days is None else f"{days:.1f}",
        )


if __name__ == "__main__":
    main()
