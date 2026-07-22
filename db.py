"""Local SQLite price database for daily OHLCV history."""

import sqlite3
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def resolve_db_path(db_path: str) -> Path:
    """Resolve db_path, anchoring relative paths at the project directory."""
    path = Path(db_path)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


def init_db(path: Path | str) -> sqlite3.Connection:
    """Open the database, creating tables if needed. Returns the connection."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol TEXT NOT NULL,
            date   TEXT NOT NULL,
            open   REAL,
            high   REAL,
            low    REAL,
            close  REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings (
            symbol        TEXT PRIMARY KEY,
            earnings_date TEXT,
            eps_estimate  REAL,
            updated_at    TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS holdings (
            symbol     TEXT PRIMARY KEY,
            avg_price  REAL,
            quantity   REAL,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sectors (
            symbol     TEXT PRIMARY KEY,
            sector     TEXT,
            industry   TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings_reports (
            symbol     TEXT PRIMARY KEY,
            quarter    TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings_watch (
            symbol           TEXT NOT NULL,
            earnings_date    TEXT NOT NULL,
            baseline_quarter TEXT,
            detected_at      TEXT,
            last_tick_at     TEXT,
            PRIMARY KEY (symbol, earnings_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS earnings_watch_news (
            symbol  TEXT NOT NULL,
            news_id TEXT NOT NULL,
            sent_at TEXT,
            PRIMARY KEY (symbol, news_id)
        )
        """
    )
    # Migration for databases created before the industry column existed.
    sector_cols = [r[1] for r in conn.execute("PRAGMA table_info(sectors)")]
    if "industry" not in sector_cols:
        conn.execute("ALTER TABLE sectors ADD COLUMN industry TEXT")
    conn.commit()
    return conn


def upsert_prices(conn: sqlite3.Connection, symbol: str, rows: list[tuple]) -> int:
    """Insert or replace daily price rows for a symbol. Returns the row count.

    Each row is (date, open, high, low, close, volume).
    """
    conn.executemany(
        """
        INSERT OR REPLACE INTO daily_prices
            (symbol, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [(symbol, *row) for row in rows],
    )
    return len(rows)


def upsert_earnings(
    conn: sqlite3.Connection,
    symbol: str,
    earnings_date: str,
    eps_estimate: float | None,
    updated_at: str,
) -> None:
    """Insert or replace the earnings row for a symbol."""
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings
            (symbol, earnings_date, eps_estimate, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (symbol, earnings_date, eps_estimate, updated_at),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Return a meta value, or None when the key is missing."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set a meta value, replacing any existing one."""
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (key, value),
    )


def get_holdings(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return all holdings as {symbol: {"avg_price": float, "quantity": float}}."""
    rows = conn.execute(
        "SELECT symbol, avg_price, quantity FROM holdings"
    ).fetchall()
    return {s: {"avg_price": a, "quantity": q} for s, a, q in rows}


def upsert_holding(
    conn: sqlite3.Connection,
    symbol: str,
    avg_price: float,
    quantity: float,
    updated_at: str,
) -> None:
    """Insert or replace the holding for a symbol."""
    conn.execute(
        """
        INSERT OR REPLACE INTO holdings (symbol, avg_price, quantity, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (symbol, avg_price, quantity, updated_at),
    )


def delete_holding(conn: sqlite3.Connection, symbol: str) -> None:
    """Remove the holding for a symbol (no error when absent)."""
    conn.execute("DELETE FROM holdings WHERE symbol = ?", (symbol,))


def get_latest_quotes(conn: sqlite3.Connection, symbols: list[str]) -> dict:
    """Latest close + % change vs previous close from daily_prices.

    Returns {symbol: {"price": float, "change_pct": float|None}}; symbols
    without stored closes are omitted.
    """
    quotes = {}
    for symbol in symbols:
        rows = conn.execute(
            """
            SELECT close FROM daily_prices
            WHERE symbol = ? AND close IS NOT NULL ORDER BY date DESC LIMIT 2
            """,
            (symbol,),
        ).fetchall()
        if not rows:
            continue
        price = rows[0][0]
        change_pct = None
        if len(rows) == 2 and rows[1][0]:
            change_pct = round((price - rows[1][0]) / rows[1][0] * 100, 2)
        quotes[symbol] = {"price": round(price, 2), "change_pct": change_pct}
    return quotes


def get_reported_quarter(conn: sqlite3.Connection, symbol: str) -> str | None:
    """Return the last notified earnings-report quarter (ISO date), or None."""
    row = conn.execute(
        "SELECT quarter FROM earnings_reports WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row[0] if row else None


def upsert_reported_quarter(
    conn: sqlite3.Connection, symbol: str, quarter: str, updated_at: str
) -> None:
    """Insert or replace the last notified earnings-report quarter."""
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_reports (symbol, quarter, updated_at)
        VALUES (?, ?, ?)
        """,
        (symbol, quarter, updated_at),
    )


def get_sectors(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return the cached sector/industry per symbol.

    {symbol: {"sector": str, "industry": str|None}}. industry is None for
    rows cached before the industry column existed (needs a refetch) and ""
    when yfinance reported no industry.
    """
    rows = conn.execute("SELECT symbol, sector, industry FROM sectors").fetchall()
    return {s: {"sector": sector, "industry": industry} for s, sector, industry in rows}


def upsert_sector(
    conn: sqlite3.Connection,
    symbol: str,
    sector: str,
    industry: str,
    updated_at: str,
) -> None:
    """Insert or replace the sector/industry for a symbol."""
    conn.execute(
        """
        INSERT OR REPLACE INTO sectors (symbol, sector, industry, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (symbol, sector, industry, updated_at),
    )


def get_earnings_on(conn: sqlite3.Connection, day_iso: str) -> dict:
    """Return {symbol: eps_estimate} for symbols whose earnings date is day_iso."""
    rows = conn.execute(
        "SELECT symbol, eps_estimate FROM earnings WHERE earnings_date = ?",
        (day_iso,),
    ).fetchall()
    return {symbol: eps for symbol, eps in rows}


def get_watch_state(
    conn: sqlite3.Connection, symbol: str, earnings_date: str
) -> dict | None:
    """Return the earnings-watch row as a dict, or None when absent."""
    row = conn.execute(
        """
        SELECT baseline_quarter, detected_at, last_tick_at
        FROM earnings_watch WHERE symbol = ? AND earnings_date = ?
        """,
        (symbol, earnings_date),
    ).fetchone()
    if row is None:
        return None
    return {
        "baseline_quarter": row[0],
        "detected_at": row[1],
        "last_tick_at": row[2],
    }


def upsert_watch_state(
    conn: sqlite3.Connection,
    symbol: str,
    earnings_date: str,
    baseline_quarter: str | None,
    detected_at: str | None,
    last_tick_at: str | None,
) -> None:
    """Insert or replace the earnings-watch row for a symbol/date."""
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_watch
            (symbol, earnings_date, baseline_quarter, detected_at, last_tick_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (symbol, earnings_date, baseline_quarter, detected_at, last_tick_at),
    )


def delete_old_watch_state(conn: sqlite3.Connection, before_date: str) -> None:
    """Remove earnings-watch rows for earnings dates before `before_date`."""
    conn.execute(
        "DELETE FROM earnings_watch WHERE earnings_date < ?", (before_date,)
    )


def news_already_sent(conn: sqlite3.Connection, symbol: str, news_id: str) -> bool:
    """True when this news item was already sent for the symbol."""
    row = conn.execute(
        "SELECT 1 FROM earnings_watch_news WHERE symbol = ? AND news_id = ?",
        (symbol, news_id),
    ).fetchone()
    return row is not None


def mark_news_sent(
    conn: sqlite3.Connection, symbol: str, news_id: str, sent_at: str
) -> None:
    """Record that a news item was sent for the symbol."""
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_watch_news (symbol, news_id, sent_at)
        VALUES (?, ?, ?)
        """,
        (symbol, news_id, sent_at),
    )
