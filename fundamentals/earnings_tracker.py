"""Earnings-date tracking via yfinance calendars.

Robustness pattern mirrors earnings_reminder.py: the calendar may be a
dict (newer yfinance) or a DataFrame (older), and per-symbol failures
print a warning to stderr and are skipped, never crash the batch.
"""

import sys
from datetime import date, datetime, timedelta

import yfinance as yf

from fundamentals import fetcher


def _equity_only(watchlist: list[str]) -> list[str]:
    """Drop known non-equity symbols ('^' indexes, cached ETFs) silently."""
    cached = fetcher.load_non_equity()
    return [
        t for t in watchlist
        if not fetcher.is_non_equity_symbol(t) and t not in cached
    ]


def _as_date(value) -> date | None:
    """Normalize a datetime/date-like value to a date, None if not possible."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _earnings_entries(ticker: str) -> list[tuple[date, float | None]]:
    """(earnings_date, eps_estimate) pairs from yfinance for a ticker.

    Tries Ticker.calendar first (dict or DataFrame format), then falls
    back to get_earnings_dates(). Raises ValueError on fetch failures.
    """
    try:
        t = yf.Ticker(ticker)
        calendar = t.calendar
    except Exception as exc:
        raise ValueError(f"{ticker}: calendar fetch failed ({exc})")

    entries: list[tuple[date, float | None]] = []
    if calendar is not None:
        if not isinstance(calendar, dict):
            try:
                calendar = calendar.iloc[0].to_dict()
            except (AttributeError, IndexError, TypeError) as exc:
                raise ValueError(f"{ticker}: unexpected calendar format ({exc})")
        dates = calendar.get("Earnings Date") or []
        if not isinstance(dates, (list, tuple)):
            dates = [dates]
        eps = calendar.get("Earnings Average")
        try:
            eps = float(eps) if eps is not None else None
        except (TypeError, ValueError):
            eps = None
        for value in dates:
            day = _as_date(value)
            if day is not None:
                entries.append((day, eps))

    if entries:
        return entries

    # Fallback: the earnings-dates table (index = report datetimes).
    try:
        df = t.get_earnings_dates(limit=4)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    for index, record in df.iterrows():
        day = _as_date(index)
        if day is None:
            continue
        try:
            eps = float(record.get("EPS Estimate"))
        except (TypeError, ValueError):
            eps = None
        entries.append((day, eps))
    return entries


def tickers_with_earnings_today(
    watchlist: list[str], today: date | None = None
) -> list[str]:
    """Watchlist tickers reporting on `today` (local date; default: today).

    Matches when any known earnings date equals today. Per-symbol failures
    print 'Warning: ...' to stderr and are skipped.
    """
    if today is None:
        today = date.today()
    matches = []
    for ticker in _equity_only(watchlist):
        try:
            entries = _earnings_entries(ticker)
        except ValueError as exc:
            print(f"Warning: {exc}", file=sys.stderr)
            continue
        if any(day == today for day, _eps in entries):
            matches.append(ticker)
    return matches


def next_earnings(watchlist: list[str], days: int = 30) -> list[dict]:
    """Upcoming earnings within `days` days, sorted by date ascending.

    Returns [{"ticker", "date" (ISO), "eps_estimate"}], one entry per
    ticker (its nearest upcoming date). Same warn-and-continue handling.
    """
    today = date.today()
    horizon = today + timedelta(days=days)
    upcoming = []
    for ticker in _equity_only(watchlist):
        try:
            entries = _earnings_entries(ticker)
        except ValueError as exc:
            print(f"Warning: {exc}", file=sys.stderr)
            continue
        future = [(day, eps) for day, eps in entries if today <= day <= horizon]
        if not future:
            continue
        day, eps = min(future, key=lambda e: e[0])
        upcoming.append({"ticker": ticker, "date": day.isoformat(),
                         "eps_estimate": eps})
    upcoming.sort(key=lambda e: e["date"])
    return upcoming
