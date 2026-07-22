"""Post-earnings report notification: key financials plus the latest price.

Detects newly released earnings reports for watchlist symbols by tracking
the latest reported quarter per symbol in the earnings_reports DB table
(first sighting of a symbol seeds the table without notifying). For each
new quarter, builds a Telegram message with reported EPS vs estimate,
revenue, net income, and the latest live price (same source as the web UI).
"""

import math
import sys
from datetime import datetime

import yfinance as yf

from db import (
    get_reported_quarter,
    init_db,
    resolve_db_path,
    upsert_reported_quarter,
)
from ticker import fetch_live_quotes, format_change, load_watchlist


def _float_or_none(value) -> float | None:
    """Convert a pandas/numpy scalar to float, mapping NaN to None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def fetch_earnings_report(symbol: str) -> dict | None:
    """Fetch the most recent earnings report for a symbol.

    Returns {symbol, quarter (date), eps_actual, eps_estimate, surprise_pct,
    revenue, net_income}, or None when no reported quarter is available.
    Raises ValueError with a per-symbol message on fetch failure.
    """
    try:
        ticker = yf.Ticker(symbol)
        history = ticker.get_earnings_history()
    except Exception as exc:
        raise ValueError(f"{symbol}: earnings history fetch failed ({exc})")
    if history is None or history.empty or "epsActual" not in history.columns:
        return None

    history = history.dropna(subset=["epsActual"]).sort_index()
    if history.empty:
        return None
    latest = history.iloc[-1]
    quarter = history.index[-1]
    if hasattr(quarter, "date"):
        quarter = quarter.date()

    report = {
        "symbol": symbol,
        "quarter": quarter,
        "eps_actual": _float_or_none(latest["epsActual"]),
        "eps_estimate": _float_or_none(latest.get("epsEstimate")),
        # yfinance reports the surprise as a fraction (0.049 = +4.9%).
        "surprise_pct": (
            surprise * 100
            if (surprise := _float_or_none(latest.get("surprisePercent"))) is not None
            else None
        ),
        "revenue": None,
        "net_income": None,
    }

    try:
        stmt = ticker.quarterly_income_stmt
    except Exception:
        return report
    if stmt is None or stmt.empty:
        return report

    # Prefer the income-statement column matching the report quarter,
    # otherwise fall back to the most recent quarter available.
    col = next(
        (c for c in stmt.columns if getattr(c, "date", lambda: c)() == quarter),
        stmt.columns[0],
    )

    def value(name: str) -> float | None:
        try:
            return _float_or_none(stmt.loc[name, col])
        except KeyError:
            return None

    report["revenue"] = value("Total Revenue")
    report["net_income"] = value("Net Income")
    return report


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1e9:
        return f"${value / 1e9:.2f}B"
    return f"${value / 1e6:.1f}M"


def format_report(report: dict, quote: dict | None) -> str:
    """Format one earnings report as a Telegram message."""
    lines = [
        f"📑 Earnings Report — {report['symbol']} "
        f"(quarter ended {report['quarter'].isoformat()})"
    ]
    eps = f"EPS: {report['eps_actual']:g}"
    if report["eps_estimate"] is not None:
        eps += f" vs est {report['eps_estimate']:g}"
    if report["surprise_pct"] is not None:
        eps += f" ({report['surprise_pct']:+.1f}% surprise)"
    lines.append(eps)
    lines.append(f"Revenue: {_fmt_money(report['revenue'])}")
    lines.append(f"Net income: {_fmt_money(report['net_income'])}")
    if quote:
        lines.append(
            f"Price: ${quote['price']:.2f} ({format_change(quote.get('change_pct'))})"
        )
    return "\n".join(lines)


def check_new_reports(settings: dict, test: bool = False) -> list[str]:
    """Check the watchlist for newly released earnings reports.

    Compares each symbol's latest reported quarter against the stored one.
    Symbols seen for the first time are seeded without notifying; symbols
    with a newer quarter produce one Telegram message each. Prints
    timestamped console lines throughout. In test mode nothing is written
    to the database and no messages should be sent (caller's job).
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_path = resolve_db_path(settings["db_path"])

    conn = init_db(db_path)
    try:
        new_reports: dict[str, dict] = {}
        for symbol in load_watchlist():
            try:
                report = fetch_earnings_report(symbol)
            except ValueError as exc:
                print(f"Warning: {exc}", file=sys.stderr)
                continue
            if report is None:
                continue
            quarter = report["quarter"].isoformat()
            stored = get_reported_quarter(conn, symbol)
            if stored is None:
                print(
                    f"{timestamp} {symbol}: seeding earnings report baseline "
                    f"({quarter})"
                )
                if not test:
                    upsert_reported_quarter(
                        conn, symbol, quarter, datetime.now().isoformat()
                    )
                continue
            if quarter <= stored:
                continue
            new_reports[symbol] = report
            if not test:
                upsert_reported_quarter(
                    conn, symbol, quarter, datetime.now().isoformat()
                )
        if not test:
            conn.commit()

        if not new_reports:
            print(f"{timestamp} No new earnings reports")
            return []

        quotes = fetch_live_quotes(list(new_reports))
        messages = []
        for symbol, report in new_reports.items():
            print(f"{timestamp} {symbol}: new earnings report ({report['quarter']})")
            messages.append(format_report(report, quotes.get(symbol)))
        return messages
    finally:
        conn.close()
