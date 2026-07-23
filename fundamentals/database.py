"""SQLite storage layer for fundamentals data.

Mirrors the project idiom from db.py: init_db() returns a connection,
callers commit/close themselves. All upsert helpers take/return plain
dicts keyed by column name; updated_at defaults to today when omitted.
"""

import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "fundamentals.db"

COMPANY_PROFILE_COLUMNS = (
    "ticker", "name", "sector", "industry", "market_cap", "employees",
    "country", "business_summary",
)

QUARTERLY_FINANCIALS_COLUMNS = (
    "ticker", "fiscal_date", "report_type", "revenue", "gross_profit",
    "operating_income", "net_income", "eps", "total_assets",
    "total_liabilities", "shareholders_equity", "total_debt",
    "cash_and_equivalents", "operating_cash_flow", "free_cash_flow",
    "capital_expenditure", "shares_outstanding", "depreciation_amortization",
    "interest_expense", "current_assets", "current_liabilities",
)

VALUATION_RATIOS_COLUMNS = (
    "ticker", "fiscal_date", "pe_ratio", "forward_pe", "pb_ratio", "ps_ratio",
    "p_fcf_ratio", "ev_ebitda", "peg_ratio", "dividend_yield",
)

HISTORICAL_VALUATION_COLUMNS = (
    "ticker", "date", "pe_ratio", "pb_ratio", "ps_ratio", "p_fcf_ratio",
    "ev_ebitda", "sector_median_pe", "sector_median_pb", "sector_median_ps",
    "percentile_vs_sector", "percentile_vs_history",
)

EARNINGS_HISTORY_COLUMNS = (
    "ticker", "fiscal_date", "eps_actual", "eps_estimate", "revenue_actual",
    "revenue_estimate", "surprise_pct", "guidance_eps_low",
    "guidance_eps_high", "guidance_revenue_low", "guidance_revenue_high",
    "call_sentiment",
)

MOAT_METRICS_COLUMNS = (
    "ticker", "fiscal_date", "gross_margin", "operating_margin", "net_margin",
    "roe", "roic", "roa", "gross_margin_5yr_avg", "operating_margin_5yr_avg",
    "revenue_cagr_3yr", "revenue_cagr_5yr", "eps_cagr_3yr", "eps_cagr_5yr",
    "fcf_cagr_3yr", "debt_to_equity", "interest_coverage", "current_ratio",
    "moat_score", "moat_rating",
)

DCF_VALUATION_COLUMNS = (
    "ticker", "valuation_date", "current_price", "fcf_per_share_ttm",
    "fcf_growth_rate_5yr", "fcf_growth_rate_terminal", "discount_rate",
    "projected_fcf_5yr", "terminal_value", "intrinsic_value",
    "intrinsic_value_per_share", "upside_downside_pct", "margin_of_safety",
    "mos_label",
)

PEER_COMPARISON_COLUMNS = (
    "ticker", "peer_ticker", "metric", "ticker_value", "peer_value",
    "sector_median", "premium_discount_pct",
)

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS company_profiles (
        ticker           TEXT PRIMARY KEY,
        name             TEXT,
        sector           TEXT,
        industry         TEXT,
        market_cap       REAL,
        employees        INTEGER,
        country          TEXT,
        business_summary TEXT,
        updated_at       DATE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quarterly_financials (
        ticker                   TEXT NOT NULL,
        fiscal_date              TEXT NOT NULL,
        report_type              TEXT,
        revenue                  REAL,
        gross_profit             REAL,
        operating_income         REAL,
        net_income               REAL,
        eps                      REAL,
        total_assets             REAL,
        total_liabilities        REAL,
        shareholders_equity      REAL,
        total_debt               REAL,
        cash_and_equivalents     REAL,
        operating_cash_flow      REAL,
        free_cash_flow           REAL,
        capital_expenditure      REAL,
        shares_outstanding       REAL,
        depreciation_amortization REAL,
        interest_expense         REAL,
        current_assets           REAL,
        current_liabilities      REAL,
        updated_at               DATE,
        PRIMARY KEY (ticker, fiscal_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS valuation_ratios (
        ticker         TEXT NOT NULL,
        fiscal_date    TEXT NOT NULL,
        pe_ratio       REAL,
        forward_pe     REAL,
        pb_ratio       REAL,
        ps_ratio       REAL,
        p_fcf_ratio    REAL,
        ev_ebitda      REAL,
        peg_ratio      REAL,
        dividend_yield REAL,
        updated_at     DATE,
        PRIMARY KEY (ticker, fiscal_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_valuation (
        ticker                 TEXT NOT NULL,
        date                   TEXT NOT NULL,
        pe_ratio               REAL,
        pb_ratio               REAL,
        ps_ratio               REAL,
        p_fcf_ratio            REAL,
        ev_ebitda              REAL,
        sector_median_pe       REAL,
        sector_median_pb       REAL,
        sector_median_ps       REAL,
        percentile_vs_sector   REAL,
        percentile_vs_history  REAL,
        updated_at             DATE,
        PRIMARY KEY (ticker, date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS earnings_history (
        ticker                 TEXT NOT NULL,
        fiscal_date            TEXT NOT NULL,
        eps_actual             REAL,
        eps_estimate           REAL,
        revenue_actual         REAL,
        revenue_estimate       REAL,
        surprise_pct           REAL,
        guidance_eps_low       REAL,
        guidance_eps_high      REAL,
        guidance_revenue_low   REAL,
        guidance_revenue_high  REAL,
        call_sentiment         TEXT,
        updated_at             DATE,
        PRIMARY KEY (ticker, fiscal_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS moat_metrics (
        ticker                  TEXT NOT NULL,
        fiscal_date             TEXT NOT NULL,
        gross_margin            REAL,
        operating_margin        REAL,
        net_margin              REAL,
        roe                     REAL,
        roic                    REAL,
        roa                     REAL,
        gross_margin_5yr_avg    REAL,
        operating_margin_5yr_avg REAL,
        revenue_cagr_3yr        REAL,
        revenue_cagr_5yr        REAL,
        eps_cagr_3yr            REAL,
        eps_cagr_5yr            REAL,
        fcf_cagr_3yr            REAL,
        debt_to_equity          REAL,
        interest_coverage       REAL,
        current_ratio           REAL,
        moat_score              REAL,
        moat_rating             TEXT,
        updated_at              DATE,
        PRIMARY KEY (ticker, fiscal_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dcf_valuation (
        ticker                    TEXT NOT NULL,
        valuation_date            TEXT NOT NULL,
        current_price             REAL,
        fcf_per_share_ttm         REAL,
        fcf_growth_rate_5yr       REAL,
        fcf_growth_rate_terminal  REAL,
        discount_rate             REAL,
        projected_fcf_5yr         REAL,
        terminal_value            REAL,
        intrinsic_value           REAL,
        intrinsic_value_per_share REAL,
        upside_downside_pct       REAL,
        margin_of_safety          REAL,
        mos_label                 TEXT,
        updated_at                DATE,
        PRIMARY KEY (ticker, valuation_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS peer_comparison (
        ticker               TEXT NOT NULL,
        peer_ticker          TEXT NOT NULL,
        metric               TEXT NOT NULL,
        ticker_value         REAL,
        peer_value           REAL,
        sector_median        REAL,
        premium_discount_pct REAL,
        updated_at           DATE,
        PRIMARY KEY (ticker, peer_ticker, metric)
    )
    """,
)

TABLES = (
    "company_profiles", "quarterly_financials", "valuation_ratios",
    "historical_valuation", "earnings_history", "moat_metrics",
    "dcf_valuation", "peer_comparison",
)


def init_db(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the fundamentals database, creating tables if needed.

    Defaults to DB_PATH; pass ":memory:" for tests. Parent directories are
    created as needed. Callers commit/close the connection themselves.
    """
    if path is None:
        path = DB_PATH
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.commit()
    return conn


def _upsert(conn: sqlite3.Connection, table: str, columns: tuple, row: dict) -> None:
    """INSERT OR REPLACE one dict row; updated_at defaults to today."""
    values = [row.get(c) for c in columns]
    values.append(row.get("updated_at") or date.today().isoformat())
    cols = ", ".join((*columns, "updated_at"))
    marks = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})", values
    )


def _get(conn: sqlite3.Connection, sql: str, params: tuple) -> list[dict]:
    """Run a query and return rows as plain dicts."""
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def upsert_company_profile(conn: sqlite3.Connection, profile: dict) -> None:
    """Insert or replace a company profile dict (keys = column names)."""
    _upsert(conn, "company_profiles", COMPANY_PROFILE_COLUMNS, profile)


def get_company_profile(conn: sqlite3.Connection, ticker: str) -> dict | None:
    """Return the profile for a ticker, or None when absent."""
    rows = _get(
        conn, "SELECT * FROM company_profiles WHERE ticker = ?", (ticker,)
    )
    return rows[0] if rows else None


def upsert_quarterly_financials(
    conn: sqlite3.Connection, rows: list[dict]
) -> int:
    """Insert or replace financial rows (annual + quarterly). Returns count."""
    for row in rows:
        _upsert(conn, "quarterly_financials", QUARTERLY_FINANCIALS_COLUMNS, row)
    return len(rows)


def get_quarterly_financials(
    conn: sqlite3.Connection, ticker: str, report_type: str | None = None
) -> list[dict]:
    """Financial rows for a ticker, newest fiscal_date first.

    report_type filters to '10-Q' or '10-K' when given.
    """
    if report_type is None:
        return _get(
            conn,
            "SELECT * FROM quarterly_financials WHERE ticker = ? "
            "ORDER BY fiscal_date DESC",
            (ticker,),
        )
    return _get(
        conn,
        "SELECT * FROM quarterly_financials WHERE ticker = ? AND report_type = ? "
        "ORDER BY fiscal_date DESC",
        (ticker, report_type),
    )


def upsert_valuation_ratios(conn: sqlite3.Connection, ratios: dict) -> None:
    """Insert or replace one valuation_ratios row (needs ticker/fiscal_date)."""
    _upsert(conn, "valuation_ratios", VALUATION_RATIOS_COLUMNS, ratios)


def get_valuation_ratios(
    conn: sqlite3.Connection, ticker: str, limit: int | None = None
) -> list[dict]:
    """Valuation ratio rows for a ticker, newest fiscal_date first."""
    sql = (
        "SELECT * FROM valuation_ratios WHERE ticker = ? "
        "ORDER BY fiscal_date DESC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return _get(conn, sql, (ticker,))


def get_latest_valuation_ratios(
    conn: sqlite3.Connection, ticker: str
) -> dict | None:
    """Newest valuation_ratios row for a ticker, or None."""
    rows = get_valuation_ratios(conn, ticker, limit=1)
    return rows[0] if rows else None


def upsert_historical_valuation(conn: sqlite3.Connection, snapshot: dict) -> None:
    """Insert or replace one historical_valuation daily snapshot."""
    _upsert(conn, "historical_valuation", HISTORICAL_VALUATION_COLUMNS, snapshot)


def get_historical_valuation(
    conn: sqlite3.Connection, ticker: str, limit: int | None = None
) -> list[dict]:
    """Historical valuation snapshots for a ticker, newest date first."""
    sql = (
        "SELECT * FROM historical_valuation WHERE ticker = ? ORDER BY date DESC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return _get(conn, sql, (ticker,))


def upsert_earnings_history(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert or replace earnings_history rows. Returns count."""
    for row in rows:
        _upsert(conn, "earnings_history", EARNINGS_HISTORY_COLUMNS, row)
    return len(rows)


def get_earnings_history(
    conn: sqlite3.Connection, ticker: str, limit: int | None = None
) -> list[dict]:
    """Earnings history rows for a ticker, newest fiscal_date first."""
    sql = (
        "SELECT * FROM earnings_history WHERE ticker = ? "
        "ORDER BY fiscal_date DESC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return _get(conn, sql, (ticker,))


def upsert_moat_metrics(conn: sqlite3.Connection, metrics: dict) -> None:
    """Insert or replace one moat_metrics row (needs ticker/fiscal_date)."""
    _upsert(conn, "moat_metrics", MOAT_METRICS_COLUMNS, metrics)


def get_moat_metrics(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    """Moat metric rows for a ticker, newest fiscal_date first."""
    return _get(
        conn,
        "SELECT * FROM moat_metrics WHERE ticker = ? ORDER BY fiscal_date DESC",
        (ticker,),
    )


def get_latest_moat_metrics(
    conn: sqlite3.Connection, ticker: str
) -> dict | None:
    """Newest moat_metrics row for a ticker, or None."""
    rows = _get(
        conn,
        "SELECT * FROM moat_metrics WHERE ticker = ? "
        "ORDER BY fiscal_date DESC LIMIT 1",
        (ticker,),
    )
    return rows[0] if rows else None


def upsert_dcf_valuation(conn: sqlite3.Connection, valuation: dict) -> None:
    """Insert or replace one dcf_valuation row (needs ticker/valuation_date)."""
    _upsert(conn, "dcf_valuation", DCF_VALUATION_COLUMNS, valuation)


def get_dcf_valuation(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    """DCF valuation rows for a ticker, newest valuation_date first."""
    return _get(
        conn,
        "SELECT * FROM dcf_valuation WHERE ticker = ? "
        "ORDER BY valuation_date DESC",
        (ticker,),
    )


def get_latest_dcf_valuation(
    conn: sqlite3.Connection, ticker: str
) -> dict | None:
    """Newest dcf_valuation row for a ticker, or None."""
    rows = _get(
        conn,
        "SELECT * FROM dcf_valuation WHERE ticker = ? "
        "ORDER BY valuation_date DESC LIMIT 1",
        (ticker,),
    )
    return rows[0] if rows else None


def upsert_peer_comparison(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert or replace peer_comparison rows. Returns count."""
    for row in rows:
        _upsert(conn, "peer_comparison", PEER_COMPARISON_COLUMNS, row)
    return len(rows)


def get_peer_comparison(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    """Peer comparison rows for a ticker, ordered by metric/peer."""
    return _get(
        conn,
        "SELECT * FROM peer_comparison WHERE ticker = ? "
        "ORDER BY metric, peer_ticker",
        (ticker,),
    )
