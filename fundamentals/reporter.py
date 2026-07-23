"""Reporting layer for the fundamentals package.

Orchestrates fetch -> compute -> store per ticker (update_ticker /
update_all), rebuilds result dicts from the DB without network
(load_results), renders the self-contained HTML dashboard, writes the
JSON reports, and builds the earnings-day Telegram alert.

Result dict shape (flat keys, grouped values) — the same shape is
produced by update_ticker and load_results:
    ticker, name, sector, industry, country, market_cap, employees,
    price, ratios, history_percentiles, sector_percentile, moat,
    moat_score, moat_rating, moat_breakdown, dcf, sensitivity,
    fundamental_score, surprises, peer_comparison, valuation_history
"""

import html
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from db import resolve_db_path
from fundamentals import (
    calculator,
    database,
    dcf_valuator,
    earnings_tracker,
    fetcher,
    history,
    moat_scorer,
    peers,
    scorer,
)
from ticker import load_watchlist

PROJECT_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_PATH = PROJECT_DIR / "fundamental_dashboard.html"
REPORTS_DIR = PROJECT_DIR / "reports"
TELEGRAM_LIMIT = 4096

RATIO_KEYS = ("pe_ratio", "pb_ratio", "ps_ratio", "p_fcf_ratio", "ev_ebitda")
RATIO_LABELS = {
    "pe_ratio": "P/E", "forward_pe": "Fwd P/E", "pb_ratio": "P/B",
    "ps_ratio": "P/S", "p_fcf_ratio": "P/FCF", "ev_ebitda": "EV/EBITDA",
}
# Moat breakdown key -> (label, max points) for the mini bars / alert lines.
MOAT_COMPONENTS = {
    "pricing_power": ("Margins", 25),
    "capital_efficiency": ("ROIC", 25),
    "profitability": ("ROE", 20),
    "growth_consistency": ("Growth", 15),
    "financial_strength": ("D-E", 15),
}


def resolve_fundamentals_db_path(settings: dict) -> Path:
    """Resolve settings['fundamentals_db_path'] like db_path (project-relative)."""
    raw = settings.get("fundamentals_db_path")
    return resolve_db_path(raw) if raw else database.DB_PATH


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def update_ticker(
    conn: sqlite3.Connection,
    ticker: str,
    watchlist: list[str] | None = None,
    risk_free_rate: float | None = None,
) -> dict:
    """Fetch, compute, and store all fundamentals data for one ticker.

    Writes profile/financials/earnings/moat/valuation/DCF/history/peer
    rows (the caller commits). Returns the flat result dict. ValueError
    from the fetch layer propagates so batch callers can warn-and-continue.
    """
    if watchlist is None:
        watchlist = []
    if risk_free_rate is None:
        risk_free_rate = fetcher.fetch_risk_free_rate()

    profile = fetcher.fetch_profile(ticker)
    price = fetcher.fetch_price(ticker)
    fin_rows = fetcher.fetch_financials(ticker)
    earnings_rows = fetcher.fetch_earnings(ticker)
    database.upsert_company_profile(conn, profile)
    database.upsert_quarterly_financials(conn, fin_rows)
    database.upsert_earnings_history(conn, earnings_rows)

    moat = moat_scorer.compute_moat_metrics(fin_rows)
    moat_value, moat_rating, moat_breakdown = moat_scorer.moat_score(moat)
    ratios = calculator.compute_valuation_ratios(
        profile, fin_rows, price, eps_cagr_5yr=moat.get("eps_cagr_5yr")
    )
    database.upsert_moat_metrics(conn, {
        "ticker": ticker, **moat,
        "moat_score": moat_value, "moat_rating": moat_rating,
    })
    database.upsert_valuation_ratios(conn, {"ticker": ticker, **ratios})

    dcf = dcf_valuator.compute_dcf(profile, fin_rows, moat, price, risk_free_rate)
    sensitivity = None
    if dcf is not None:
        database.upsert_dcf_valuation(conn, {
            "ticker": ticker, "valuation_date": date.today().isoformat(), **dcf,
        })
        # The grid is a what-if view, kept in the result dict only (not DB).
        fcf_ttm = calculator.ttm(fin_rows, "free_cash_flow")
        shares = profile.get("shares_outstanding") or (
            calculator.latest(fin_rows) or {}
        ).get("shares_outstanding")
        sensitivity = dcf_valuator.sensitivity_grid(
            fcf_ttm, shares, dcf["fcf_growth_rate_5yr"],
            dcf["discount_rate"], dcf["fcf_growth_rate_terminal"],
        )

    history_percentiles = history.update_historical_valuation(
        conn, ticker, ratios, profile.get("sector")
    )
    peer_summary = peers.compute_peer_comparison(
        conn, ticker, profile.get("sector"), profile.get("industry"), watchlist
    )
    peer_tickers = peers.peers_for(
        ticker, profile.get("sector"), profile.get("industry"), watchlist
    )
    sector_percentile = peers.update_sector_percentiles(
        conn, ticker, [ticker, *peer_tickers]
    )

    surprises = [
        r.get("surprise_pct") for r in earnings_rows
        if r.get("eps_actual") is not None
    ][:4]
    fundamental = scorer.fundamental_score(
        history_percentiles, moat_value, moat.get("revenue_cagr_3yr"),
        moat.get("debt_to_equity"), surprises,
    )

    return {
        "ticker": ticker,
        "name": profile.get("name"),
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "country": profile.get("country"),
        "market_cap": profile.get("market_cap"),
        "employees": profile.get("employees"),
        "price": price,
        "ratios": ratios,
        "history_percentiles": history_percentiles,
        "sector_percentile": sector_percentile,
        "moat": moat,
        "moat_score": moat_value,
        "moat_rating": moat_rating,
        "moat_breakdown": moat_breakdown,
        "dcf": dcf,
        "sensitivity": sensitivity,
        "fundamental_score": fundamental,
        "surprises": surprises,
        "peer_comparison": peer_summary,
        "valuation_history": [],
    }


def update_all(
    conn: sqlite3.Connection, tickers: list[str], test: bool = False
) -> list[dict]:
    """update_ticker over a batch; per-symbol failures warn and are skipped.

    The risk-free rate is fetched once for the whole batch. Commits after
    each successful ticker so one failure costs nothing already stored.
    """
    risk_free_rate = fetcher.fetch_risk_free_rate()
    results = []
    for ticker in tickers:
        try:
            result = update_ticker(
                conn, ticker, watchlist=tickers, risk_free_rate=risk_free_rate
            )
        except ValueError as exc:
            print(f"Warning: {exc}", file=sys.stderr)
            continue
        conn.commit()
        results.append(result)
        score = result["fundamental_score"]["total"]
        print(
            f"{ticker}: fundamental {score}/100, moat {result['moat_score']}"
            f" ({result['moat_rating']})"
        )
    return results


def load_results(conn: sqlite3.Connection, tickers: list[str]) -> list[dict]:
    """Rebuild result dicts purely from stored DB rows (no network).

    Tickers without a stored profile are skipped. history_percentiles are
    re-derived from the stored snapshots (latest ratios vs last 20 rows);
    the sensitivity grid is recomputed from the stored DCF assumptions.
    valuation_history holds all snapshots, date-ascending, for the chart.
    """
    results = []
    for ticker in tickers:
        profile = database.get_company_profile(conn, ticker)
        if profile is None:
            continue
        fin_rows = database.get_quarterly_financials(conn, ticker)
        ratios = database.get_latest_valuation_ratios(conn, ticker) or {}
        moat = database.get_latest_moat_metrics(conn, ticker) or {}
        dcf = database.get_latest_dcf_valuation(conn, ticker)
        earnings_rows = database.get_earnings_history(conn, ticker, limit=8)
        history_rows = database.get_historical_valuation(conn, ticker)
        peer_rows = database.get_peer_comparison(conn, ticker)

        history_percentiles = {
            key: history.percentile(
                ratios.get(key), [row.get(key) for row in history_rows[:20]]
            )
            for key in RATIO_KEYS
        }
        sector_percentile = history_rows[0].get("percentile_vs_sector") if history_rows else None

        moat_value = moat.get("moat_score")
        moat_rating = moat.get("moat_rating")
        _score, _rating, moat_breakdown = moat_scorer.moat_score(moat)

        sensitivity = None
        if dcf is not None:
            fcf_ttm = calculator.ttm(fin_rows, "free_cash_flow")
            shares = profile.get("shares_outstanding") or (
                calculator.latest(fin_rows) or {}
            ).get("shares_outstanding")
            sensitivity = dcf_valuator.sensitivity_grid(
                fcf_ttm, shares, dcf.get("fcf_growth_rate_5yr"),
                dcf.get("discount_rate"),
                dcf.get("fcf_growth_rate_terminal") or 0.025,
            )

        peer_summary = {}
        for row in peer_rows:
            metric = row["metric"]
            if metric not in peer_summary:
                peer_summary[metric] = {
                    "ticker": row.get("ticker_value"),
                    "median": row.get("sector_median"),
                    "premium_discount_pct": row.get("premium_discount_pct"),
                }

        surprises = [
            r.get("surprise_pct") for r in earnings_rows
            if r.get("eps_actual") is not None
        ][:4]
        fundamental = scorer.fundamental_score(
            history_percentiles, moat_value, moat.get("revenue_cagr_3yr"),
            moat.get("debt_to_equity"), surprises,
        )

        results.append({
            "ticker": ticker,
            "name": profile.get("name"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "country": profile.get("country"),
            "market_cap": profile.get("market_cap"),
            "employees": profile.get("employees"),
            # Price is only persisted on the DCF row.
            "price": dcf.get("current_price") if dcf else None,
            "ratios": ratios,
            "history_percentiles": history_percentiles,
            "sector_percentile": sector_percentile,
            "moat": moat,
            "moat_score": moat_value,
            "moat_rating": moat_rating,
            "moat_breakdown": moat_breakdown,
            "dcf": dcf,
            "sensitivity": sensitivity,
            "fundamental_score": fundamental,
            "surprises": surprises,
            "peer_comparison": peer_summary,
            "valuation_history": list(reversed(history_rows)),
        })
    return results


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

_CSS = """
  body { background: #0d1117; color: #c9d1d9; margin: 0;
         font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .container { max-width: 1100px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 1.3rem; }
  h2 { font-size: 1.1rem; margin: 0 0 8px; }
  h3 { font-size: 0.95rem; margin: 12px 0 6px; color: #8b949e; }
  .meta { color: #8b949e; font-size: 0.85rem; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
           padding: 16px; margin: 16px 0; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; }
  th { color: #8b949e; font-weight: 600; }
  th.sortable { cursor: pointer; user-select: none; }
  th.sortable:hover { color: #c9d1d9; }
  .bull { color: #2ca02c; }
  .bear { color: #d62728; }
  .neutral { color: #8b949e; }
  .pill { border: 1px solid currentColor; border-radius: 10px;
           padding: 1px 8px; font-size: 0.8rem; white-space: nowrap; }
  .scorebar { position: relative; height: 12px; border-radius: 6px; min-width: 120px;
               background: linear-gradient(90deg, #d62728 0%, #8b949e 50%, #2ca02c 100%); }
  .scoremark { position: absolute; top: -3px; width: 4px; height: 18px;
                background: #ffffff; border-radius: 2px; transform: translateX(-2px); }
  .pbar { position: relative; height: 10px; border-radius: 5px; min-width: 100px;
           background: #21262d; overflow: hidden; }
  .pfill { position: absolute; top: 0; left: 0; height: 100%; border-radius: 5px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 24px; }
  @media (max-width: 800px) { .grid2 { grid-template-columns: 1fr; } }
  .kv { display: flex; justify-content: space-between; padding: 3px 0;
         border-bottom: 1px solid #21262d; font-size: 0.9rem; }
  .kv .k { color: #8b949e; }
  .dot { display: inline-block; width: 12px; height: 12px; border-radius: 50%;
          margin-right: 4px; cursor: default; }
  .big { font-size: 1.6rem; font-weight: 700; }
  details { margin: 10px 0; }
  summary { cursor: pointer; color: #8b949e; font-size: 0.9rem; }
  td.base { background: rgba(44, 160, 44, 0.15); font-weight: 600; }
  .dcfbox { background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
             padding: 10px 14px; }
"""


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _fmt(value, digits: int = 1) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


def _fmt_signed_pct(value, digits: int = 1) -> str:
    return "N/A" if value is None else f"{value:+.{digits}f}%"


def _fmt_money(value) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1e12:
        return f"${value / 1e12:.2f}T"
    if abs(value) >= 1e9:
        return f"${value / 1e9:.2f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"${value:,.0f}"


def _pctile_color(pct: float | None) -> str:
    """Low valuation percentile = cheap = green; high = expensive = red."""
    if pct is None:
        return "#8b949e"
    if pct < 40:
        return "#2ca02c"
    if pct <= 60:
        return "#8b949e"
    return "#d62728"


def _score_color(score: float | None) -> str:
    if score is None:
        return "#8b949e"
    if score >= 70:
        return "#2ca02c"
    if score >= 40:
        return "#8b949e"
    return "#d62728"


def _pctile_bar(pct: float | None, label: str) -> str:
    """One labeled 0-100 percentile bar (gray when no data)."""
    width = 0 if pct is None else max(0.0, min(100.0, pct))
    text = "N/A" if pct is None else f"{pct:.0f}"
    return (
        f'<div class="kv"><span class="k">{_esc(label)}</span>'
        f'<span style="display:flex;align-items:center;gap:8px">'
        f'<span class="pbar"><span class="pfill" style="width:{width:.0f}%;'
        f'background:{_pctile_color(pct)}"></span></span>{text}</span></div>'
    )


def _overview_section(results: list[dict]) -> str:
    def avg(key):
        values = [r[key] for r in results if r.get(key) is not None]
        return sum(values) / len(values) if values else None

    fund_scores = [
        r["fundamental_score"]["total"] for r in results
        if r.get("fundamental_score")
    ]
    avg_fund = sum(fund_scores) / len(fund_scores) if fund_scores else None
    avg_moat = avg("moat_score")
    upsides = [
        r["dcf"]["upside_downside_pct"] for r in results
        if r.get("dcf") and r["dcf"].get("upside_downside_pct") is not None
    ]
    avg_upside = sum(upsides) / len(upsides) if upsides else None

    sectors: dict[str, int] = {}
    for r in results:
        sectors[r.get("sector") or "Unknown"] = sectors.get(
            r.get("sector") or "Unknown", 0
        ) + 1
    sector_bits = ", ".join(
        f"{_esc(name)} ({count})"
        for name, count in sorted(sectors.items(), key=lambda kv: -kv[1])
    ) or "N/A"

    return (
        '<section class="card"><h2>Portfolio Overview</h2>'
        '<div class="grid2">'
        f'<div class="kv"><span class="k">Tickers covered</span>'
        f'<span>{len(results)}</span></div>'
        f'<div class="kv"><span class="k">Avg fundamental score</span>'
        f'<span>{_fmt(avg_fund)}</span></div>'
        f'<div class="kv"><span class="k">Avg moat score</span>'
        f'<span>{_fmt(avg_moat)}</span></div>'
        f'<div class="kv"><span class="k">Avg DCF upside</span>'
        f'<span>{_fmt_signed_pct(avg_upside)}</span></div>'
        "</div>"
        f'<p class="meta">Sectors: {sector_bits}</p></section>'
    )


def _surprise_dots(surprises: list[float | None]) -> str:
    if not surprises:
        return '<span class="neutral">N/A</span>'
    dots = []
    for surprise in surprises[:4]:
        beat = surprise is not None and surprise > 0
        color = "#2ca02c" if beat else "#d62728"
        tooltip = "N/A" if surprise is None else f"{surprise:+.1f}%"
        dots.append(
            f'<span class="dot" style="background:{color}" '
            f'title="{_esc(tooltip)}"></span>'
        )
    return "".join(dots)


def _pe_chart(history_rows: list[dict]) -> str:
    """Inline SVG: own P/E over time + sector median + 25-75% band + current dot."""
    points = [
        (row["date"], row.get("pe_ratio"), row.get("sector_median_pe"))
        for row in history_rows if row.get("pe_ratio") is not None
    ]
    if len(points) < 2:
        return '<p class="neutral">N/A — builds up as daily snapshots accumulate</p>'

    width, height, pad = 600, 200, 34
    pes = [p[1] for p in points]
    sector_values = [p[2] for p in points if p[2] is not None]
    lo = min(pes + sector_values)
    hi = max(pes + sector_values)
    if hi == lo:
        hi = lo + 1.0
    margin = (hi - lo) * 0.08
    lo, hi = lo - margin, hi + margin

    def x(index: int) -> float:
        return pad + index * (width - 2 * pad) / (len(points) - 1)

    def y(value: float) -> float:
        return height - pad - (value - lo) / (hi - lo) * (height - 2 * pad)

    ordered = sorted(pes)
    q25 = ordered[int(0.25 * (len(ordered) - 1))]
    q75 = ordered[int(0.75 * (len(ordered) - 1))]
    band = (
        f'<rect x="{pad}" y="{y(q75):.1f}" width="{width - 2 * pad}" '
        f'height="{(y(q25) - y(q75)):.1f}" fill="rgba(139,148,158,0.15)"/>'
    )
    pe_line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_d, v, _s) in enumerate(points))
    sector_line = " ".join(
        f"{x(i):.1f},{y(s):.1f}"
        for i, (_d, _v, s) in enumerate(points) if s is not None
    )
    sector_svg = (
        f'<polyline points="{sector_line}" fill="none" stroke="#d29922" '
        f'stroke-width="1.5" stroke-dasharray="5,4"/>' if sector_line else ""
    )
    last_x, last_y = x(len(points) - 1), y(points[-1][1])
    dot = f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" fill="#2ca02c"/>'

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px" role="img">'
        f"{band}"
        f'<polyline points="{pe_line}" fill="none" stroke="#58a6ff" '
        f'stroke-width="2"/>'
        f"{sector_svg}{dot}"
        f'<text x="{pad}" y="{y(hi) + 12:.1f}" fill="#8b949e" '
        f'font-size="10">{hi:.1f}</text>'
        f'<text x="{pad}" y="{y(lo):.1f}" fill="#8b949e" '
        f'font-size="10">{lo:.1f}</text>'
        f'<text x="{pad}" y="{height - 6}" fill="#8b949e" '
        f'font-size="10">{_esc(points[0][0])}</text>'
        f'<text x="{width - pad}" y="{height - 6}" fill="#8b949e" '
        f'font-size="10" text-anchor="end">{_esc(points[-1][0])}</text>'
        "</svg>"
        '<p class="meta">Blue: own P/E &middot; dashed gold: sector median '
        "&middot; gray band: own 25-75% range</p>"
    )


def _sensitivity_table(sensitivity: dict | None) -> str:
    if not sensitivity:
        return '<p class="neutral">N/A — no computable DCF</p>'
    growths = sensitivity["growth_rates"]
    discounts = sensitivity["discount_rates"]
    values = sensitivity["values"]
    header = "".join(
        f"<th>{discount * 100:.1f}%</th>" for discount in discounts
    )
    rows = []
    for g_index, growth in enumerate(growths):
        cells = []
        for d_index in range(len(discounts)):
            value = values[g_index][d_index]
            base = ' class="base"' if g_index == 2 and d_index == 2 else ""
            if value is None:
                cells.append(f"<td{base}>—</td>")
            else:
                cells.append(
                    f'<td{base} title="{value:.2f}">{value:.0f}</td>'
                )
        rows.append(f"<tr><th>{growth * 100:.1f}%</th>{''.join(cells)}</tr>")
    return (
        "<table><thead>"
        f'<tr><th>growth \\ discount</th>{header}</tr>'
        f"</thead><tbody>{''.join(rows)}</tbody></table>"
        '<p class="meta">Intrinsic value per share; highlighted cell = base '
        "assumptions. Hover a cell for the exact value.</p>"
    )


def _stock_card(result: dict) -> str:
    ticker = result["ticker"]
    ratios = result.get("ratios") or {}
    percentiles = result.get("history_percentiles") or {}
    dcf = result.get("dcf")
    fundamental = result.get("fundamental_score") or {}
    fund_total = fundamental.get("total")
    moat_score = result.get("moat_score")
    breakdown = result.get("moat_breakdown") or {}

    ratio_rows = "".join(
        f'<div class="kv"><span class="k">{_esc(RATIO_LABELS[key])}</span>'
        f"<span>{_fmt(ratios.get(key), 1)}</span></div>"
        for key in ("pe_ratio", "forward_pe", "pb_ratio", "ps_ratio",
                    "p_fcf_ratio", "ev_ebitda")
    )
    percentile_bars = "".join(
        _pctile_bar(percentiles.get(key), RATIO_LABELS[key])
        for key in RATIO_KEYS
    )
    percentile_bars += _pctile_bar(
        result.get("sector_percentile"), "P/E vs sector"
    )

    moat_bars = "".join(
        _pctile_bar(
            None if breakdown.get(key) is None
            else breakdown[key] / max_points * 100,
            f"{label} ({'N/A' if breakdown.get(key) is None else breakdown[key]}/{max_points})",
        )
        for key, (label, max_points) in MOAT_COMPONENTS.items()
    )

    if dcf:
        dcf_rows = (
            f'<div class="kv"><span class="k">Intrinsic value/share</span>'
            f"<span>{_fmt(dcf.get('intrinsic_value_per_share'), 2)}</span></div>"
            f'<div class="kv"><span class="k">Current price</span>'
            f"<span>{_fmt(dcf.get('current_price'), 2)}</span></div>"
            f'<div class="kv"><span class="k">Upside</span>'
            f"<span>{_fmt_signed_pct(dcf.get('upside_downside_pct'))}</span></div>"
            f'<div class="kv"><span class="k">Margin of safety</span>'
            f"<span>{_esc(dcf.get('mos_label') or 'N/A')}</span></div>"
        )
    else:
        dcf_rows = '<p class="neutral">N/A — no computable DCF</p>'

    score_bar = (
        f'<div class="scorebar"><div class="scoremark" '
        f'style="left:{max(0.0, min(100.0, fund_total)):.0f}%"></div></div>'
        if fund_total is not None else '<span class="neutral">N/A</span>'
    )

    return (
        f'<section class="card"><h2>{_esc(ticker)}'
        f' <span class="meta">{_esc(result.get("name"))} &middot; '
        f'{_esc(result.get("sector") or "Unknown sector")} &middot; '
        f'price {_fmt(result.get("price"), 2)}</span></h2>'
        '<div class="grid2"><div>'
        "<h3>Valuation Ratios</h3>" + ratio_rows +
        "<h3>Historical Percentiles (vs own history)</h3>" + percentile_bars +
        "</div><div>"
        f"<h3>Moat Score: {_fmt(moat_score, 0)}/100 "
        f'<span class="pill" style="color:{_score_color(moat_score)}">'
        f'{_esc(result.get("moat_rating") or "N/A")}</span></h3>' + moat_bars +
        '<h3>DCF Valuation</h3><div class="dcfbox">' + dcf_rows + "</div>"
        "</div></div>"
        f'<h3>Fundamental Score: <span class="big" '
        f'style="color:{_score_color(fund_total)}">'
        f'{fund_total if fund_total is not None else "N/A"}</span>/100</h3>'
        + score_bar +
        f'<h3>Last 4 earnings surprises</h3>{_surprise_dots(result.get("surprises") or [])}'
        f'<div class="kv"><span class="k">Guidance</span>'
        f'<span class="neutral">N/A</span></div>'
        f"<details><summary>Historical valuation chart (P/E)</summary>"
        f"{_pe_chart(result.get('valuation_history') or [])}</details>"
        f"<details><summary>DCF sensitivity (5x5)</summary>"
        f"{_sensitivity_table(result.get('sensitivity'))}</details>"
        "</section>"
    )


_PEER_SORT_JS = """
document.querySelectorAll("th.sortable").forEach(function (th) {
  th.addEventListener("click", function () {
    var tbody = th.closest("table").tBodies[0];
    var idx = th.cellIndex;
    var asc = th.dataset.asc !== "1";
    th.dataset.asc = asc ? "1" : "0";
    Array.from(tbody.rows).sort(function (a, b) {
      var x = parseFloat(a.cells[idx].dataset.v);
      var y = parseFloat(b.cells[idx].dataset.v);
      if (isNaN(x)) return 1;
      if (isNaN(y)) return -1;
      return asc ? x - y : y - x;
    }).forEach(function (row) { tbody.appendChild(row); });
  });
});
"""


def _peer_table(results: list[dict]) -> str:
    def cell(value, text):
        if value is None:
            return '<td data-v="">N/A</td>'
        return f'<td data-v="{value}">{_esc(text)}</td>'

    rows = []
    for r in results:
        if not r.get("peer_comparison"):
            continue
        ratios = r.get("ratios") or {}
        dcf = r.get("dcf") or {}
        cells = [f'<td data-v="{_esc(r["ticker"])}"><b>{_esc(r["ticker"])}</b></td>']
        for key in ("pe_ratio", "pb_ratio", "ps_ratio", "ev_ebitda"):
            value = ratios.get(key)
            premium = (r["peer_comparison"].get(key) or {}).get(
                "premium_discount_pct"
            )
            text = _fmt(value, 1)
            if value is not None and premium is not None:
                text += f" ({premium:+.0f}%)"
            cells.append(cell(value, text))
        moat_score = r.get("moat_score")
        cells.append(cell(moat_score, _fmt(moat_score, 0)))
        fund = (r.get("fundamental_score") or {}).get("total")
        cells.append(cell(fund, str(fund) if fund is not None else "N/A"))
        upside = dcf.get("upside_downside_pct")
        cells.append(cell(upside, _fmt_signed_pct(upside)))
        rows.append(f"<tr>{''.join(cells)}</tr>")

    body = "".join(rows) or (
        '<tr><td colspan="8" class="neutral">No peer comparison data yet — '
        "run --update-fundamentals for several sector peers</td></tr>"
    )
    return (
        '<section class="card"><h2>Peer Comparison</h2>'
        "<table><thead><tr>"
        '<th class="sortable">Ticker</th><th class="sortable">P/E</th>'
        '<th class="sortable">P/B</th><th class="sortable">P/S</th>'
        '<th class="sortable">EV/EBITDA</th><th class="sortable">Moat</th>'
        '<th class="sortable">Fundamental Score</th>'
        '<th class="sortable">DCF Upside</th>'
        f"</tr></thead><tbody>{body}</tbody></table>"
        '<p class="meta">Ratio cells: value (premium/discount vs peer median). '
        "Click a header to sort.</p></section>"
    )


def _earnings_section(earnings_calendar: list[dict] | None) -> str:
    if not earnings_calendar:
        return ""
    rows = "".join(
        f"<tr><td>{_esc(e['ticker'])}</td><td>{_esc(e['date'])}</td>"
        f"<td>{_esc('N/A' if e.get('eps_estimate') is None else f'{e['eps_estimate']:g}')}</td></tr>"
        for e in earnings_calendar
    )
    return (
        '<section class="card"><h2>Earnings Calendar (next 30 days)</h2>'
        "<table><thead><tr><th>Ticker</th><th>Date</th>"
        "<th>EPS Est</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></section>"
    )


def render_dashboard(
    results: list[dict], earnings_calendar: list[dict] | None = None
) -> str:
    """Render the full fundamentals dashboard as self-contained dark HTML."""
    generated = datetime.now().strftime("%a %b %d, %H:%M")
    cards = "".join(_stock_card(r) for r in results) or (
        '<section class="card"><p class="neutral">No fundamentals data yet — '
        "run --update-fundamentals first.</p></section>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fundamental Dashboard</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
<h1>Fundamental Dashboard</h1>
<p class="meta">Generated {html.escape(generated)} &middot; yfinance data &middot;
DCF: 5yr FCF projection, terminal growth 2.5%, WACC = risk-free + beta x 5.5%</p>

{_overview_section(results)}
{cards}
{_peer_table(results)}
{_earnings_section(earnings_calendar)}
</div>
<script>{_PEER_SORT_JS}</script>
</body>
</html>
"""


def generate_dashboard(
    settings: dict, test: bool = False, output_path: Path | str | None = None
) -> Path:
    """Render the dashboard from stored data and write it (unless testing).

    Writes BOTH reports/fundamental_dashboard.html and the project-root
    fundamental_dashboard.html; returns the root path. The earnings
    calendar fetch is best-effort (None on any failure).
    """
    db_path = resolve_fundamentals_db_path(settings)
    conn = database.init_db(db_path)
    try:
        tickers = load_watchlist()
        results = load_results(conn, tickers)
    finally:
        conn.close()

    calendar = None
    try:
        calendar = earnings_tracker.next_earnings(tickers, days=30)
    except Exception as exc:
        print(f"Warning: earnings calendar fetch failed ({exc})", file=sys.stderr)

    page = render_dashboard(results, calendar)
    root_path = Path(output_path) if output_path else DASHBOARD_PATH
    if not test:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "fundamental_dashboard.html").write_text(page)
        root_path.write_text(page)
        print(f"Fundamental dashboard written to {root_path} and {REPORTS_DIR}")
    return root_path


# ---------------------------------------------------------------------------
# JSON reports
# ---------------------------------------------------------------------------


def _jsonable(value):
    """Recursively keep only plain JSON types (dict/list/str/int/float/bool/None)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def write_json_reports(results: list[dict], reports_dir: Path | str) -> tuple[Path, Path]:
    """Write fundamental_report_<date>.json and dcf_report_<date>.json.

    Returns (fundamental_path, dcf_path). The DCF report maps ticker ->
    DCF fields + sensitivity grid; tickers without a DCF are omitted.
    """
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    fundamental_path = reports_dir / f"fundamental_report_{today}.json"
    fundamental_path.write_text(
        json.dumps([_jsonable(r) for r in results], indent=2)
    )

    dcf_data = {
        r["ticker"]: {**_jsonable(r["dcf"]),
                      "sensitivity": _jsonable(r.get("sensitivity"))}
        for r in results if r.get("dcf")
    }
    dcf_path = reports_dir / f"dcf_report_{today}.json"
    dcf_path.write_text(json.dumps(dcf_data, indent=2))
    return fundamental_path, dcf_path


# ---------------------------------------------------------------------------
# Telegram alert
# ---------------------------------------------------------------------------


def _elide(message: str, limit: int = TELEGRAM_LIMIT) -> str:
    """Hard-cap the message at `limit` chars, truncating at a line boundary."""
    if len(message) <= limit:
        return message
    suffix = "\n… (truncated)"
    cut = message[: limit - len(suffix)].rfind("\n")
    if cut <= 0:
        cut = limit - len(suffix)
    return message[:cut] + suffix


def _percentile_wording(pct: float | None) -> str:
    """'cheap'/'fair'/'expensive' from a vs-own-history percentile."""
    if pct is None:
        return "n/a"
    if pct < 40:
        return "cheap"
    if pct <= 60:
        return "fair"
    return "expensive"


def _moat_emoji(rating: str | None) -> str:
    return {
        "Wide Moat": "🟢", "Narrow Moat": "🟡",
        "Weak Moat": "🟠", "No Moat": "🔴",
    }.get(rating or "", "⚪")


def _mos_emoji(label: str | None) -> str:
    return {
        "Strong Buy": "🟢", "Buy": "🟢", "Fair Value": "🟡",
        "Slightly Overvalued": "🟠", "Overvalued": "🔴",
    }.get(label or "", "⚪")


def _score_emoji(score: float | None) -> str:
    if score is None:
        return "⚪"
    if score >= 70:
        return "🟢"
    if score >= 40:
        return "🟡"
    return "🔴"


def _action_line(fund_total: float | None, upside: float | None) -> str:
    """One-line rule-based summary for the alert footer."""
    if fund_total is not None and fund_total >= 70 and upside is not None and upside > 15:
        return "Strong fundamentals, DCF shows upside. Watch for technical entry."
    if upside is not None and upside < 0:
        return "Fundamentals solid but DCF shows limited upside."
    if fund_total is not None and fund_total < 40:
        return "Weak fundamental score; avoid adding here."
    return "Mixed picture; size any position conservatively."


def build_telegram_alert(result: dict, timing: str = "") -> str:
    """Plain-text earnings-day fundamental alert (Telegram, <=4096 chars)."""
    ticker = result["ticker"]
    ratios = result.get("ratios") or {}
    percentiles = result.get("history_percentiles") or {}
    peer = (result.get("peer_comparison") or {}).get("pe_ratio") or {}
    moat = result.get("moat") or {}
    breakdown = result.get("moat_breakdown") or {}
    dcf = result.get("dcf") or {}
    fundamental = result.get("fundamental_score") or {}
    fund_total = fundamental.get("total")

    header = f"📊 Fundamental Alert — {ticker} Earnings Today"
    if timing:
        header += f" ({timing})"
    lines = [header, "Valuation:"]

    pe_pct = percentiles.get("pe_ratio")
    pe_line = f"• P/E: {_fmt(ratios.get('pe_ratio'))}"
    if pe_pct is not None:
        pe_line += f" (5yr percentile: {pe_pct:.0f}% → {_percentile_wording(pe_pct)})"
    lines.append(pe_line)
    lines.append(f"• Forward P/E: {_fmt(ratios.get('forward_pe'))}")
    lines.append(f"• P/FCF: {_fmt(ratios.get('p_fcf_ratio'))}")
    lines.append(f"• EV/EBITDA: {_fmt(ratios.get('ev_ebitda'))}")

    lines.append(f"vs Sector ({result.get('sector') or 'n/a'}):")
    premium = peer.get("premium_discount_pct")
    if premium is None:
        lines.append("• P/E: N/A vs sector median")
    else:
        direction = "cheaper" if premium < 0 else "pricier"
        lines.append(f"• P/E: {abs(premium):.0f}% {direction} than median")

    moat_score = result.get("moat_score")
    moat_rating = result.get("moat_rating")
    lines.append(
        f"Moat Score: {_fmt(moat_score, 0)}/100 {_moat_emoji(moat_rating)} "
        f"({moat_rating or 'N/A'})"
    )
    metric_keys = {
        "pricing_power": ("Gross Margin", moat.get("gross_margin"), "{:.0%}"),
        "capital_efficiency": ("ROIC", moat.get("roic"), "{:.0%}"),
        "profitability": ("ROE", moat.get("roe"), "{:.0%}"),
        "growth_consistency": ("Rev CAGR 5yr", moat.get("revenue_cagr_5yr"), "{:.0%}"),
        "financial_strength": ("Debt/Equity", moat.get("debt_to_equity"), "{:.2f}"),
    }
    for key, (label, value, fmt) in metric_keys.items():
        points, max_points = breakdown.get(key), MOAT_COMPONENTS[key][1]
        mark = "⚠️" if points is None or points < 0.6 * max_points else "✅"
        rendered = "N/A" if value is None else fmt.format(value)
        lines.append(f"• {label}: {rendered} {mark}")

    lines.append("DCF Valuation:")
    if dcf:
        lines.append(
            f"• Intrinsic Value: ${_fmt(dcf.get('intrinsic_value_per_share'), 0)}/share"
        )
        lines.append(f"• Current Price: ${_fmt(dcf.get('current_price'), 0)}")
        lines.append(f"• Upside: {_fmt_signed_pct(dcf.get('upside_downside_pct'))}")
        label = dcf.get("mos_label")
        lines.append(f"• Margin of Safety: {label or 'N/A'} {_mos_emoji(label)}")
    else:
        lines.append("• N/A — no computable DCF (negative or missing FCF)")

    lines.append(f"Fundamental Score: {fund_total if fund_total is not None else 'N/A'}"
                 f"/100 {_score_emoji(fund_total)}")

    surprises = result.get("surprises") or []
    if surprises:
        beats = sum(1 for s in surprises[:4] if s is not None and s > 0)
        rendered = ", ".join(
            "n/a" if s is None else f"{s:+.1f}%" for s in surprises[:4]
        )
        lines.append(
            f"Last 4 Earnings: {rendered} {'✅' if beats >= 3 else '⚠️'}"
        )
    else:
        lines.append("Last 4 Earnings: N/A")

    lines.append(f"Action: {_action_line(fund_total, dcf.get('upside_downside_pct'))}")
    return _elide("\n".join(lines))


def run_earnings_check(settings: dict, test: bool = False) -> list[str]:
    """Alert on watchlist tickers reporting today: update + Telegram per ticker.

    In test mode alerts are printed instead of sent. Returns the messages.
    """
    from notify import send_telegram  # lazy: avoid requests import in pure paths

    tickers = load_watchlist()
    todays = earnings_tracker.tickers_with_earnings_today(tickers)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not todays:
        print(f"{timestamp} No watchlist earnings today")
        return []

    conn = database.init_db(resolve_fundamentals_db_path(settings))
    messages = []
    try:
        risk_free_rate = fetcher.fetch_risk_free_rate()
        for ticker in todays:
            try:
                result = update_ticker(
                    conn, ticker, watchlist=tickers,
                    risk_free_rate=risk_free_rate,
                )
            except ValueError as exc:
                print(f"Warning: {exc}", file=sys.stderr)
                continue
            conn.commit()
            message = build_telegram_alert(result)
            messages.append(message)
            print(f"{timestamp} {ticker}: fundamental alert prepared")
            if test:
                print(message)
            else:
                send_telegram(message)
    finally:
        conn.close()
    return messages


# ---------------------------------------------------------------------------
# Premarket integration
# ---------------------------------------------------------------------------


def fundamental_one_liners(
    conn: sqlite3.Connection, tickers: list[str] | None = None
) -> dict[str, str]:
    """Compact per-ticker lines for the premarket report.

    {ticker: 'AAPL — Fund: 78 🟢 | Moat: 82 Wide | DCF: +17.9%'}; tickers=None
    covers every ticker with a stored profile. Silently empty when the
    tables or scores are missing.
    """
    try:
        if tickers is None:
            rows = conn.execute("SELECT ticker FROM company_profiles").fetchall()
            tickers = [row[0] for row in rows]
        liners = {}
        for result in load_results(conn, list(tickers)):
            if result.get("moat_score") is None:
                continue
            fund_total = (result.get("fundamental_score") or {}).get("total")
            rating = (result.get("moat_rating") or "N/A").split()[0]
            dcf = result.get("dcf") or {}
            upside = dcf.get("upside_downside_pct")
            liners[result["ticker"]] = (
                f"{result['ticker']} — "
                f"Fund: {fund_total if fund_total is not None else 'N/A'} "
                f"{_score_emoji(fund_total)} | Moat: {result['moat_score']:.0f} "
                f"{rating} | DCF: {_fmt_signed_pct(upside)}"
            )
        return liners
    except sqlite3.OperationalError:
        return {}
