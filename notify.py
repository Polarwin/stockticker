"""Telegram notification helper shared by the ticker and earnings modules."""

import os
import sys

import requests
from dotenv import load_dotenv

# Load environment variables from .env if present.
load_dotenv()


def send_telegram(message: str) -> None:
    """Send a message to the configured Telegram chat.

    Prints a warning and returns if Telegram credentials are missing.
    Prints/raises a clear error if the API request fails.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print(
            "Warning: TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set; "
            "skipping Telegram notification.",
            file=sys.stderr,
        )
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        # The exception message embeds the request URL, which contains the
        # bot token; mask it before writing to the journal.
        safe = str(exc).replace(token, "***")
        print(f"Error: failed to send Telegram message: {safe}", file=sys.stderr)
        raise SystemExit(1)
