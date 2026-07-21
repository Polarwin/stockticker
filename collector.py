"""Daily collector that keeps the local price database up to date."""

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from db import get_meta, init_db, resolve_db_path, set_meta, upsert_earnings, upsert_prices
from earnings_reminder import get_earnings_info
from ticker import load_watchlist

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
