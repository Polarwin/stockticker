"""Stock price watcher with optional Telegram alerts."""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yfinance as yf
from dotenv import load_dotenv

WATCHLIST_PATH = Path(__file__).with_name("watchlist.txt")
DEFAULT_INTERVAL_SECONDS = 600  # 10 minutes

# Load environment variables from .env if present.
load_dotenv()


def load_watchlist(path: Path = WATCHLIST_PATH) -> list[str]:
    """Load tickers from the watchlist file, one per line."""
    if not path.exists():
        print(f"Watchlist file not found: {path}", file=sys.stderr)
        raise SystemExit(1)

    tickers = []
    for line in path.read_text().splitlines():
        ticker = line.strip().upper()
        if ticker and not ticker.startswith("#"):
            tickers.append(ticker)

    if not tickers:
        print(f"No tickers found in watchlist: {path}", file=sys.stderr)
        raise SystemExit(1)

    return tickers


def fetch_latest_price(ticker: str) -> float:
    """Fetch the latest closing price for a given ticker.

    Raises a ValueError with a per-symbol message on failure.
    """
    try:
        data = yf.Ticker(ticker)
        history = data.history(period="1d")
    except Exception as exc:
        raise ValueError(f"{ticker}: fetch failed ({exc})")

    if history.empty:
        raise ValueError(f"{ticker}: no price data available")

    try:
        return float(history["Close"].iloc[-1])
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"{ticker}: unexpected response format ({exc})")


def fetch_all_prices(tickers: list[str]) -> dict[str, float | str]:
    """Fetch prices for all tickers, returning price or error message per symbol."""
    results: dict[str, float | str] = {}
    for ticker in tickers:
        try:
            results[ticker] = fetch_latest_price(ticker)
        except ValueError as exc:
            results[ticker] = str(exc)
    return results


def print_prices(results: dict[str, float | str], timestamp: str) -> None:
    """Print timestamped prices/errors for each ticker."""
    for ticker, value in results.items():
        if isinstance(value, float):
            print(f"{timestamp} {ticker}: ${value:.2f}")
        else:
            print(f"{timestamp} {value}")


def format_summary(results: dict[str, float | str], timestamp: str) -> str:
    """Format a single Telegram message summarizing the round."""
    header = f"📈 Stock Update ({timestamp})"
    lines = [header]
    for ticker, value in results.items():
        if isinstance(value, float):
            lines.append(f"{ticker}: ${value:.2f}")
        else:
            lines.append(f"{ticker}: {value}")
    return "\n".join(lines)


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
        print(f"Error: failed to send Telegram message: {exc}", file=sys.stderr)
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch prices for a watchlist.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit instead of looping every 10 minutes.",
    )
    args = parser.parse_args()

    tickers = load_watchlist()

    while True:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        results = fetch_all_prices(tickers)
        print_prices(results, timestamp)

        summary = format_summary(results, timestamp)
        send_telegram(summary)

        if args.once:
            break
        time.sleep(DEFAULT_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
