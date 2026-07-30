"""Historical valuation snapshots and percentile ranks (sqlite-backed)."""

import sqlite3
from datetime import date

from fundamentals import calculator, database

RATIO_KEYS = ("pe_ratio", "pb_ratio", "ps_ratio", "p_fcf_ratio", "ev_ebitda")

# Percentile window: backfilled weekly snapshots make multi-year windows
# meaningful (260 ≈ 5 years of weekly points); daily-only accumulation
# simply ranks against whatever exists.
PERCENTILE_WINDOW = 260

# update_ticker backfills from Futu price history when fewer snapshots
# than this are stored (a full backfill writes ~10 years of weekly rows).
BACKFILL_THRESHOLD = 60


def percentile(value: float | None, series: list[float | None]) -> float | None:
    """Percentile rank of `value` within `series`, on a 0-100 scale.

    Computed as count(strictly below) / n x 100 — ties count as below-not,
    so an all-equal series yields 0 (documented simplification). None
    entries in the series are ignored; None value or empty series -> None.
    """
    if value is None:
        return None
    clean = [v for v in series if v is not None]
    if not clean:
        return None
    below = sum(1 for v in clean if v < value)
    return below / len(clean) * 100


def update_historical_valuation(
    conn: sqlite3.Connection,
    ticker: str,
    ratios: dict,
    sector: str | None = None,
) -> dict:
    """Store today's valuation snapshot and return history percentiles.

    For each of the 5 ratios the current value is ranked against the
    ticker's own last PERCENTILE_WINDOW stored snapshots (daily runs plus
    any Futu-backfilled weekly history); the returned dict maps ratio ->
    percentile (None when there is no history or no current value). The
    PE-based percentile is stored in the percentile_vs_history column
    (single-column schema; per-ratio values are the return value).
    percentile_vs_sector is left None — peers.update_sector_percentiles
    fills it. Caller commits.
    """
    history = database.get_historical_valuation(
        conn, ticker, limit=PERCENTILE_WINDOW
    )

    percentiles = {}
    for key in RATIO_KEYS:
        percentiles[key] = percentile(
            ratios.get(key), [row.get(key) for row in history]
        )

    snapshot = {
        "ticker": ticker,
        "date": date.today().isoformat(),
        "pe_ratio": ratios.get("pe_ratio"),
        "pb_ratio": ratios.get("pb_ratio"),
        "ps_ratio": ratios.get("ps_ratio"),
        "p_fcf_ratio": ratios.get("p_fcf_ratio"),
        "ev_ebitda": ratios.get("ev_ebitda"),
        "sector_median_pe": None,
        "sector_median_pb": None,
        "sector_median_ps": None,
        "percentile_vs_sector": None,
        "percentile_vs_history": percentiles["pe_ratio"],
    }
    database.upsert_historical_valuation(conn, snapshot)
    return percentiles


def backfill_valuation_history(
    conn: sqlite3.Connection,
    ticker: str,
    profile: dict,
    fin_rows: list[dict],
    price_points: list[tuple[str, float]],
) -> int:
    """Store one valuation snapshot per (date, close) point, as-of that date.

    For each price point the ratios are computed with the same
    compute_valuation_ratios call as the live path, but with fin_rows
    filtered to fiscal dates on/before the point's date and the market cap
    derived from the point's close (current share count — dilution drift
    over long windows is a documented approximation). Returns the number
    of snapshots written; the caller commits.
    """
    shares = profile.get("shares_outstanding")
    if not shares:
        return 0
    count = 0
    for day, close in price_points:
        rows_asof = [
            r for r in fin_rows
            if r.get("fiscal_date") and r["fiscal_date"] <= day
        ]
        if not rows_asof:
            continue
        ratios = calculator.compute_valuation_ratios(
            {"ticker": ticker, "shares_outstanding": shares}, rows_asof, close
        )
        database.upsert_historical_valuation(conn, {
            "ticker": ticker,
            "date": day,
            "pe_ratio": ratios.get("pe_ratio"),
            "pb_ratio": ratios.get("pb_ratio"),
            "ps_ratio": ratios.get("ps_ratio"),
            "p_fcf_ratio": ratios.get("p_fcf_ratio"),
            "ev_ebitda": ratios.get("ev_ebitda"),
            "sector_median_pe": None,
            "sector_median_pb": None,
            "sector_median_ps": None,
            "percentile_vs_sector": None,
            "percentile_vs_history": None,
        })
        count += 1
    return count
