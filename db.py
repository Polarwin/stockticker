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
