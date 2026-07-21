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
