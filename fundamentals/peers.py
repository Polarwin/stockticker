"""Peer comparison against curated sector peer lists (sqlite-backed).

No fetching happens here — peers without rows in the local database are
simply skipped, so results improve as more tickers get processed.
"""

import sqlite3
import statistics
from datetime import date

from fundamentals import database
from fundamentals.history import percentile

SECTOR_PEERS: dict[str, list[str]] = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL"],
    # Industry-level list, matched on industry name rather than GICS sector.
    "Semiconductors": ["NVDA", "AVGO", "TSM", "AMD", "INTC", "QCOM", "MU"],
    "Financial Services": ["JPM", "BAC", "WFC", "GS", "MS"],
    "Healthcare": ["UNH", "JNJ", "LLY", "PFE", "ABBV"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "MCD", "NKE"],
    "Consumer Defensive": ["PG", "KO", "PEP", "WMT", "COST"],
    "Industrials": ["CAT", "BA", "HON", "GE", "UPS"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "T"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP"],
    "Real Estate": ["PLD", "AMT", "EQIX", "SPG", "O"],
    "Basic Materials": ["LIN", "SHW", "FCX", "NEM", "DOW"],
}

# Metrics compared: first five from valuation_ratios, rest from moat_metrics.
VALUATION_METRICS = ("pe_ratio", "pb_ratio", "ps_ratio", "ev_ebitda", "p_fcf_ratio")
MOAT_METRICS = ("roe", "roic", "gross_margin")
COMPARISON_METRICS = VALUATION_METRICS + MOAT_METRICS


def peers_for(
    ticker: str,
    sector: str | None,
    industry: str | None,
    watchlist: list[str] | None,
) -> list[str]:
    """Peer tickers for a ticker (excluding itself).

    Semiconductor industries use the industry-level list; otherwise the
    sector list. When the ticker is not in the curated list, the sector
    list is unioned with `watchlist` (assumed pre-filtered to the same
    sector by the caller — sectors are not verified here), capped at 8.
    """
    if industry and industry.startswith("Semiconductors"):
        base = list(SECTOR_PEERS["Semiconductors"])
    else:
        base = list(SECTOR_PEERS.get(sector or "", []))
    if ticker in base:
        return [t for t in base if t != ticker]
    combined = base + [t for t in (watchlist or []) if t != ticker and t not in base]
    return combined[:8]


def _latest_metrics(conn: sqlite3.Connection, ticker: str) -> dict | None:
    """Latest comparison metrics for a ticker, or None when it has no data."""
    ratios = database.get_latest_valuation_ratios(conn, ticker)
    moat = database.get_latest_moat_metrics(conn, ticker)
    if ratios is None and moat is None:
        return None
    metrics = {}
    for key in VALUATION_METRICS:
        metrics[key] = ratios.get(key) if ratios else None
    for key in MOAT_METRICS:
        metrics[key] = moat.get(key) if moat else None
    return metrics


def compute_peer_comparison(
    conn: sqlite3.Connection,
    ticker: str,
    sector: str | None,
    industry: str | None,
    watchlist: list[str] | None,
) -> dict:
    """Compare the ticker's latest metrics against stored peers.

    Peers without DB rows are skipped (no fetching). The sector median is
    the median of available peer values plus the ticker's own value.
    Rows are upserted per (peer, metric); the caller commits. Returns
    {metric: {"ticker": v, "median": m, "premium_discount_pct": p}} for
    metrics where the ticker has a value.
    """
    own = _latest_metrics(conn, ticker)
    if own is None:
        return {}

    peer_metrics = {}
    for peer in peers_for(ticker, sector, industry, watchlist):
        values = _latest_metrics(conn, peer)
        if values is not None:
            peer_metrics[peer] = values

    today = date.today().isoformat()
    summary = {}
    rows = []
    for metric in COMPARISON_METRICS:
        own_value = own.get(metric)
        if own_value is None:
            continue
        peer_values = {
            peer: values[metric]
            for peer, values in peer_metrics.items()
            if values.get(metric) is not None
        }
        median = statistics.median([*peer_values.values(), own_value])
        premium = (own_value - median) / median * 100 if median else None
        for peer, peer_value in peer_values.items():
            rows.append({
                "ticker": ticker,
                "peer_ticker": peer,
                "metric": metric,
                "ticker_value": own_value,
                "peer_value": peer_value,
                "sector_median": median,
                "premium_discount_pct": premium,
                "updated_at": today,
            })
        summary[metric] = {
            "ticker": own_value,
            "median": median,
            "premium_discount_pct": premium,
        }
    database.upsert_peer_comparison(conn, rows)
    return summary


def sector_medians(conn: sqlite3.Connection, sector_tickers: list[str]) -> dict:
    """Median PE/PB/PS across the latest valuation_ratios of the given tickers."""
    medians = {}
    for key in ("pe_ratio", "pb_ratio", "ps_ratio"):
        values = []
        for ticker in sector_tickers:
            row = database.get_latest_valuation_ratios(conn, ticker)
            if row and row.get(key) is not None:
                values.append(row[key])
        medians[key] = statistics.median(values) if values else None
    return medians


def update_sector_percentiles(
    conn: sqlite3.Connection, ticker: str, sector_tickers: list[str]
) -> float | None:
    """Fill sector medians + percentile_vs_sector on the latest snapshot.

    The percentile ranks the ticker's current PE among the sector peers'
    current PE values. Returns the PE percentile (None when the ticker or
    sector has no PE data, or no snapshot row exists yet). Caller commits.
    """
    medians = sector_medians(conn, sector_tickers)

    own = database.get_latest_valuation_ratios(conn, ticker)
    peer_pes = []
    for t in sector_tickers:
        if t == ticker:
            continue
        row = database.get_latest_valuation_ratios(conn, t)
        if row and row.get("pe_ratio") is not None:
            peer_pes.append(row["pe_ratio"])
    pct = percentile(own.get("pe_ratio") if own else None, peer_pes)

    snapshot = database.get_historical_valuation(conn, ticker, limit=1)
    if not snapshot:
        return None
    conn.execute(
        """
        UPDATE historical_valuation
        SET sector_median_pe = ?, sector_median_pb = ?, sector_median_ps = ?,
            percentile_vs_sector = ?, updated_at = ?
        WHERE ticker = ? AND date = ?
        """,
        (
            medians["pe_ratio"], medians["pb_ratio"], medians["ps_ratio"],
            pct, date.today().isoformat(), ticker, snapshot[0]["date"],
        ),
    )
    return pct
