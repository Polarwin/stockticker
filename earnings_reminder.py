"""Upcoming earnings reminder based on yfinance Ticker.calendar."""

import sys
from datetime import date, datetime

import yfinance as yf

from ticker import load_watchlist

# A match is (ticker, earnings_date, eps_estimate_or_None).
Match = tuple[str, date, float | None]


def get_earnings_info(ticker: str) -> Match | None:
    """Return (ticker, earnings_date, eps_estimate) or None if no date is known.

    Raises ValueError with a per-symbol message on fetch/format failures.
    """
    try:
        calendar = yf.Ticker(ticker).calendar
    except Exception as exc:
        raise ValueError(f"{ticker}: calendar fetch failed ({exc})")

    if calendar is None:
        return None

    # Newer yfinance returns a dict; older versions return a DataFrame.
    if not isinstance(calendar, dict):
        try:
            calendar = calendar.iloc[0].to_dict()
        except (AttributeError, IndexError, TypeError) as exc:
            raise ValueError(f"{ticker}: unexpected calendar format ({exc})")

    dates = calendar.get("Earnings Date")
    if not dates:
        return None

    earnings_date = dates[0] if isinstance(dates, (list, tuple)) else dates
    if isinstance(earnings_date, datetime):
        earnings_date = earnings_date.date()
    if not isinstance(earnings_date, date):
        raise ValueError(f"{ticker}: unexpected earnings date format ({earnings_date!r})")

    eps = calendar.get("Earnings Average")
    if eps is not None:
        try:
            eps = float(eps)
        except (TypeError, ValueError):
            eps = None

    return ticker, earnings_date, eps


def format_match(match: Match, today: date | None = None) -> str:
    """Format one match as 'IBM: earnings on 2026-07-22 (in 1 day), EPS est 2.93'."""
    ticker, earnings_date, eps = match
    if today is None:
        today = date.today()
    delta = (earnings_date - today).days
    day_word = "day" if delta == 1 else "days"
    eps_str = "N/A" if eps is None else f"{eps:g}"
    return (
        f"{ticker}: earnings on {earnings_date.isoformat()} "
        f"(in {delta} {day_word}), EPS est {eps_str}"
    )


def run_earnings_check(days: int, test: bool = False) -> list[Match]:
    """Check the watchlist for earnings within the next `days` days.

    Prints one timestamped console line per match, sorted by date ascending.
    Per-symbol errors print a warning and are skipped, never crash.
    Returns the list of matches; an empty list means no message should be sent.
    """
    tickers = load_watchlist()
    today = date.today()

    matches: list[Match] = []
    for ticker in tickers:
        try:
            info = get_earnings_info(ticker)
        except ValueError as exc:
            print(f"Warning: {exc}", file=sys.stderr)
            continue
        if info is None:
            continue
        _symbol, earnings_date, _eps = info
        if 0 <= (earnings_date - today).days <= days:
            matches.append(info)

    matches.sort(key=lambda m: m[1])

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for match in matches:
        print(f"{timestamp} {format_match(match, today)}")

    return matches
