import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import yfinance as yf

WATCHLIST_PATH = Path(__file__).with_name("watchlist.txt")
DEFAULT_INTERVAL_SECONDS = 600  # 10 minutes


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


def print_prices(tickers: list[str]) -> None:
    """Print timestamped prices for each ticker, handling errors per symbol."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for ticker in tickers:
        try:
            price = fetch_latest_price(ticker)
            print(f"{timestamp} {ticker}: ${price:.2f}")
        except ValueError as exc:
            print(f"{timestamp} {exc}")


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
        print_prices(tickers)
        if args.once:
            break
        time.sleep(DEFAULT_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
