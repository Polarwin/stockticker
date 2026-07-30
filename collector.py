"""Daily collector that keeps the local price database up to date."""

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from db import (
    get_meta,
    init_db,
    insert_intraday_quotes,
    resolve_db_path,
    set_meta,
    upsert_cached_quotes,
    upsert_earnings,
    upsert_prices,
)
from earnings_reminder import get_earnings_info
from indicators import detect_crossovers
from ticker import fetch_live_quotes, load_watchlist

LAST_UPDATE_KEY = "last_update_date"
# Number of days re-downloaded on each update to cover gaps from downtime.
REFRESH_DAYS = 5


def db_update_due(settings: dict, today: str) -> bool:
    """True when the database has not yet been updated for `today` (YYYY-MM-DD)."""
    conn = init_db(resolve_db_path(settings["db_path"]))
    try:
        last_update = get_meta(conn, LAST_UPDATE_KEY)
    finally:
        conn.close()
    return (last_update or "") < today


def fetch_history_rows(symbol: str, period: str) -> list[tuple]:
    """Fetch daily OHLCV rows for a symbol as (date, open, high, low, close, volume).

    Raises ValueError with a per-symbol message on failure.
    """
    try:
        history = yf.Ticker(symbol).history(period=period)
    except Exception as exc:
        raise ValueError(f"{symbol}: fetch failed ({exc})")

    if history.empty:
        raise ValueError(f"{symbol}: no price data available")

    rows = []
    for index, row in history.iterrows():
        close = row["Close"]
        if pd.isna(close):
            continue
        volume = row["Volume"]
        rows.append(
            (
                index.date().isoformat(),
                None if pd.isna(row["Open"]) else float(row["Open"]),
                None if pd.isna(row["High"]) else float(row["High"]),
                None if pd.isna(row["Low"]) else float(row["Low"]),
                float(close),
                None if pd.isna(volume) else int(volume),
            )
        )
    return rows


def update_database(settings: dict, test: bool = False) -> tuple[int, int]:
    """Update the local price database from yfinance.

    Downloads db_backfill_days of history on the first run, otherwise only
    the last few days to cover downtime gaps. Per-symbol errors warn and
    skip. Console log only; no Telegram message.

    Returns (symbols_updated, rows_total). (0, 0) means already up to date.
    """
    market_tz = ZoneInfo(settings["market_timezone"])
    now = datetime.now(market_tz)
    today = now.date().isoformat()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    db_path = resolve_db_path(settings["db_path"])
    conn = init_db(db_path)
    try:
        last_update = get_meta(conn, LAST_UPDATE_KEY)
        if (last_update or "") >= today:
            print(f"{timestamp} DB already up to date (last update {last_update})")
            return 0, 0

        backfill = last_update is None
        period = f"{settings['db_backfill_days']}d" if backfill else f"{REFRESH_DAYS}d"
        if backfill:
            print(
                f"{timestamp} DB first run: backfilling "
                f"{settings['db_backfill_days']} days of history"
            )

        symbols_updated = 0
        rows_total = 0
        for symbol in load_watchlist():
            try:
                rows = fetch_history_rows(symbol, period)
            except ValueError as exc:
                print(f"Warning: {exc}", file=sys.stderr)
                continue
            rows_total += upsert_prices(conn, symbol, rows)
            symbols_updated += 1

        if symbols_updated > 0:
            set_meta(conn, LAST_UPDATE_KEY, today)
        conn.commit()

        print(
            f"{timestamp} DB updated: {symbols_updated} symbols, "
            f"{rows_total} rows total"
        )

        earnings_updated = update_earnings(settings, test=test, conn=conn)

        return symbols_updated, rows_total
    finally:
        conn.close()


def update_quotes_cache(settings: dict) -> int:
    """Refresh the quotes_cache table with a batch fetch from yfinance.

    The web UI serves /api/quotes from this table, so the background loop
    is the only place live quotes are fetched. Each refresh also appends
    one snapshot per symbol to intraday_quotes, building a 5-minute
    history that covers pre- and post-market (yfinance only serves
    intraday data for the trailing 60 days, so it must be collected
    continuously). Console log only.

    Returns the number of symbols cached.
    """
    market_tz = ZoneInfo(settings["market_timezone"])
    now = datetime.now(market_tz)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    quotes = fetch_live_quotes(load_watchlist())
    if not quotes:
        print(f"{timestamp} Quotes cache: fetch returned nothing, keeping old values")
        return 0

    conn = init_db(resolve_db_path(settings["db_path"]))
    try:
        count = upsert_cached_quotes(conn, quotes, now.isoformat())
        insert_intraday_quotes(conn, quotes, now.isoformat())
        conn.commit()
    finally:
        conn.close()
    print(f"{timestamp} Quotes cache updated: {count} symbols")
    return count


def update_earnings(settings: dict, test: bool = False, conn=None) -> int:
    """Refresh the earnings table from yfinance Ticker.calendar.

    For each watchlist symbol, upserts 'Earnings Date'[0] and
    'Earnings Average' into the earnings table. Per-symbol errors warn and
    skip. Console log only; no Telegram message.

    Returns the number of symbols with an earnings date upserted.
    """
    market_tz = ZoneInfo(settings["market_timezone"])
    now = datetime.now(market_tz)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    updated_at = now.isoformat()

    own_conn = conn is None
    if own_conn:
        conn = init_db(resolve_db_path(settings["db_path"]))
    try:
        upserted = 0
        for symbol in load_watchlist():
            try:
                info = get_earnings_info(symbol)
            except ValueError as exc:
                print(f"Warning: {exc}", file=sys.stderr)
                continue
            if info is None:
                continue
            _symbol, earnings_date, eps = info
            upsert_earnings(
                conn, symbol, earnings_date.isoformat(), eps, updated_at
            )
            upserted += 1
        conn.commit()
        print(f"{timestamp} Earnings table updated: {upserted} symbols")
        return upserted
    finally:
        if own_conn:
            conn.close()


def check_signals(settings: dict, test: bool = False) -> list[tuple[str, str, str, str]]:
    """Check all watchlist symbols for MACD/RSI crossovers on the latest bar.

    Prints one timestamped console line per signal. Returns a list of
    (symbol, indicator, direction, date) tuples; empty means no alert.
    """
    market_tz = ZoneInfo(settings["market_timezone"])
    timestamp = datetime.now(market_tz).strftime("%Y-%m-%d %H:%M:%S")

    signals: list[tuple[str, str, str, str]] = []
    conn = init_db(resolve_db_path(settings["db_path"]))
    try:
        for symbol in load_watchlist():
            rows = conn.execute(
                """
                SELECT date, close FROM daily_prices
                WHERE symbol = ? AND close IS NOT NULL ORDER BY date
                """,
                (symbol,),
            ).fetchall()
            if len(rows) < 2:
                continue
            closes = [r[1] for r in rows]
            for indicator, direction in detect_crossovers(closes):
                signals.append((symbol, indicator, direction, rows[-1][0]))
    finally:
        conn.close()

    for symbol, indicator, direction, date in signals:
        print(f"{timestamp} {symbol}: {indicator} {direction} crossover ({date})")
    if not signals:
        print(f"{timestamp} No MACD/RSI crossovers detected")
    return signals
