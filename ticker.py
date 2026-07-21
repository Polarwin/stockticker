"""Price fetching, % change, sorting, and formatting for the stock ticker."""

import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf

WATCHLIST_PATH = Path(__file__).with_name("watchlist.txt")


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


def fetch_price_and_change(ticker: str) -> tuple[float, float | None]:
    """Fetch the latest close and % change vs the previous close.

    Returns (latest_price, pct_change). pct_change is None when the
    prior close is unavailable (e.g. only one trading day returned).

    Raises a ValueError with a per-symbol message on failure.
    """
    try:
        data = yf.Ticker(ticker)
        history = data.history(period="2d")
    except Exception as exc:
        raise ValueError(f"{ticker}: fetch failed ({exc})")

    if history.empty:
        raise ValueError(f"{ticker}: no price data available")

    try:
        latest = float(history["Close"].iloc[-1])
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"{ticker}: unexpected response format ({exc})")

    if len(history) < 2:
        return latest, None

    try:
        previous = float(history["Close"].iloc[-2])
    except (KeyError, IndexError, TypeError) as exc:
        return latest, None

    if previous == 0:
        return latest, None

    pct_change = (latest - previous) / previous * 100
    return latest, pct_change


def format_change(pct_change: float | None) -> str:
    """Format the percent change with sign and two decimals, or N/A."""
    if pct_change is None:
        return "N/A"
    return f"{pct_change:+.2f}%"


def format_price_line(
    ticker: str,
    price: float,
    pct_change: float | None,
    *,
    for_telegram: bool = False,
) -> str:
    """Format one price line for console or Telegram output."""
    change = format_change(pct_change)
    line = f"{ticker}: ${price:.2f} ({change})"

    if for_telegram:
        if pct_change is None:
            line = f"⚠️ {line}"
        elif pct_change > 0:
            line = f"🟢 {line}"
        elif pct_change < 0:
            line = f"🔴 {line}"

    return line


def fetch_all_prices(tickers: list[str]) -> dict[str, tuple[float, float | None] | str]:
    """Fetch prices for all tickers, returning (price, change) or an error per symbol."""
    results: dict[str, tuple[float, float | None] | str] = {}
    for ticker in tickers:
        try:
            results[ticker] = fetch_price_and_change(ticker)
        except ValueError as exc:
            results[ticker] = str(exc)
    return results


def sort_results(
    results: dict[str, tuple[float, float | None] | str],
) -> list[tuple[str, tuple[float, float | None] | str]]:
    """Sort results by % change descending; errors and N/A go at the bottom."""

    def sort_key(item: tuple[str, tuple[float, float | None] | str]) -> tuple[int, float]:
        value = item[1]
        if isinstance(value, tuple):
            _price, change = value
            if change is None:
                return (1, 0.0)
            return (0, -change)
        return (2, 0.0)

    return sorted(results.items(), key=sort_key)


def build_console_lines(
    sorted_results: list[tuple[str, tuple[float, float | None] | str]],
    timestamp: str,
) -> list[str]:
    """Build the timestamped console lines for one round."""
    lines = []
    for ticker, value in sorted_results:
        if isinstance(value, tuple):
            price, change = value
            lines.append(f"{timestamp} {format_price_line(ticker, price, change)}")
        else:
            lines.append(f"{timestamp} {value}")
    return lines


def format_summary(
    sorted_results: list[tuple[str, tuple[float, float | None] | str]],
    timestamp: str,
) -> str:
    """Format a single Telegram message summarizing the round."""
    header = f"📈 Stock Update ({timestamp})"
    lines = [header]
    for ticker, value in sorted_results:
        if isinstance(value, tuple):
            price, change = value
            lines.append(
                format_price_line(ticker, price, change, for_telegram=True)
            )
        else:
            lines.append(f"⚠️ {ticker}: {value}")
    return "\n".join(lines)


def run_ticker_round(test: bool = False) -> tuple[list[str], str | None]:
    """Run one fetch round: print console lines and build a Telegram summary.

    Returns (console_lines, telegram_message). telegram_message is None on
    total failure (no results at all). The caller decides whether to send
    the message (it should not when test=True).
    """
    tickers = load_watchlist()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = fetch_all_prices(tickers)
    sorted_results = sort_results(results)

    console_lines = build_console_lines(sorted_results, timestamp)
    for line in console_lines:
        print(line)

    if not console_lines:
        return console_lines, None

    return console_lines, format_summary(sorted_results, timestamp)
