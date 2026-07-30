"""Per-stock Technical & Fundamental report page.

Combines, for one ticker: analyst consensus & price targets (analyst.py),
technical indicators/patterns/confluence score (premarket_report's
deep-dive pipeline), fundamentals from the local DB (valuation ratios,
percentiles vs own history, moat, DCF), and news headlines. Rendered as
a self-contained HTML page in the shared ui_styles theme; every section
degrades to "n/a" when its data is missing (indexes/ETFs have no
fundamentals or analyst coverage).
"""

import html

import analyst
import premarket_report
import sentiment
from fundamentals import database, reporter
from ticker import fetch_live_quotes
from ui_styles import REPORT_THEME, nav_html


def build_stock_data(settings: dict, ticker: str) -> dict:
    """Gather every section's data for one ticker."""
    ticker = ticker.strip().upper()
    data = {"ticker": ticker}

    technicals = premarket_report.build_report_data(settings, ticker)
    holding = technicals["holdings"][0] if technicals["holdings"] else {}
    data["holding"] = holding
    data["generated_at"] = technicals["generated_at"]

    data["quote"] = fetch_live_quotes([ticker]).get(ticker)

    fundamentals = None
    conn = database.init_db(reporter.resolve_fundamentals_db_path(settings))
    try:
        results = reporter.load_results(conn, [ticker])
        if results:
            fundamentals = results[0]
    finally:
        conn.close()
    data["fundamentals"] = fundamentals

    data["analyst"] = analyst.fetch_analyst_data(ticker)
    data["news_source"] = sentiment.news_source()
    return data


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "n/a"


def _pct(value) -> str:
    return _fmt(value, 1, "%")


def _big(value) -> str:
    """Compact large money figures: 1.94T / 416.2B / 12.3M."""
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(number) >= threshold:
            return f"{number / threshold:,.2f}{suffix}"
    return f"{number:,.0f}"


def _signal_class(signal: str | None) -> str:
    return {"bullish": "up", "bearish": "down"}.get(signal or "", "flat")


def _card(title: str, body: str) -> str:
    return f'<section class="card"><h2>{_esc(title)}</h2>{body}</section>'


def _kv(rows: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<div class="kv-row"><span class="k">{_esc(k)}</span>'
        f'<span class="v">{v}</span></div>'
        for k, v in rows
    )
    return f'<div class="kv">{items}</div>'


def _header(data: dict) -> str:
    ticker = data["ticker"]
    holding = data.get("holding") or {}
    fundamentals = data.get("fundamentals") or {}
    quote = data.get("quote") or {}

    price = quote.get("price") if quote else holding.get("price")
    pct = quote.get("change_pct") if quote else holding.get("pct")
    name = fundamentals.get("name") or ticker
    sector_bits = " · ".join(
        bit for bit in (fundamentals.get("sector"),
                        fundamentals.get("industry")) if bit
    )
    pct_class = "up" if (pct or 0) >= 0 else "down"
    pct_text = "n/a" if pct is None else f"{pct:+.2f}%"
    return (
        f"<h1>{_esc(ticker)}"
        f'<span class="meta"> — {_esc(name)}</span></h1>'
        f'<p class="meta">{_esc(sector_bits)}'
        f'{" · " if sector_bits else ""}'
        f'Market cap {_big(fundamentals.get("market_cap"))} · '
        f'{_esc(data.get("generated_at", ""))}</p>'
        f'<p style="font-size:1.6rem;margin:8px 0">'
        f'<strong>{_fmt(price)}</strong> '
        f'<span class="{pct_class}">{pct_text}</span></p>'
    )


def _analyst_section(data: dict) -> str:
    analyst_data = data.get("analyst") or {}
    consensus = analyst_data.get("consensus")
    trend = analyst_data.get("trend") or []
    grades = analyst_data.get("grades") or []
    quote = data.get("quote") or {}
    price = quote.get("price") if quote else None

    if consensus is None and not trend and not grades:
        return _card("Analyst Consensus", '<p class="meta">n/a</p>')

    parts = []
    if consensus:
        upside = None
        if price and consensus.get("mean_target"):
            upside = (consensus["mean_target"] - price) / price * 100
        parts.append(_kv([
            ("Rating", _esc(consensus.get("rating_label") or "n/a")),
            ("Analysts", _fmt(consensus.get("total"), 0)),
            ("Mean target", _fmt(consensus.get("mean_target"))),
            ("High / Low", f"{_fmt(consensus.get('high_target'))} / "
                           f"{_fmt(consensus.get('low_target'))}"),
            ("Upside to mean", _pct(upside)),
            ("Buy / Hold / Sell",
             f"{_pct(consensus.get('buy_pct'))} / "
             f"{_pct(consensus.get('hold_pct'))} / "
             f"{_pct(consensus.get('sell_pct'))}"),
        ]))
    if trend:
        rows = "".join(
            "<tr>"
            f"<td>{_esc(r.get('period'))}</td>"
            f"<td>{_fmt(r.get('strong_buy'), 0)}</td>"
            f"<td>{_fmt(r.get('buy'), 0)}</td>"
            f"<td>{_fmt(r.get('hold'), 0)}</td>"
            f"<td>{_fmt(r.get('sell'), 0)}</td>"
            f"<td>{_fmt(r.get('strong_sell'), 0)}</td>"
            "</tr>"
            for r in trend
        )
        parts.append(
            "<h3>Recommendation trend</h3><table><thead><tr><th>Month</th>"
            "<th>Strong Buy</th><th>Buy</th><th>Hold</th><th>Sell</th>"
            f"<th>Strong Sell</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    if grades:
        rows = "".join(
            "<tr>"
            f"<td>{_esc(g.get('date'))}</td>"
            f"<td>{_esc(g.get('firm'))}</td>"
            f"<td>{_esc(g.get('action'))}</td>"
            f"<td>{_esc(g.get('from_grade'))} → {_esc(g.get('to_grade'))}</td>"
            f"<td>{_fmt(g.get('prior_target'), 0)} → "
            f"{_fmt(g.get('target'), 0)}</td>"
            "</tr>"
            for g in grades[:12]
        )
        parts.append(
            "<h3>Recent grade changes</h3><table><thead><tr><th>Date</th>"
            "<th>Firm</th><th>Action</th><th>Grade</th>"
            f"<th>Target</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    return _card("Analyst Consensus", "".join(parts))


def _technical_section(data: dict) -> str:
    holding = data.get("holding") or {}
    rows_data = holding.get("indicator_rows") or []
    score = holding.get("score")
    patterns = holding.get("patterns") or []
    options = holding.get("options")

    parts = []
    if score:
        parts.append(_kv([
            ("Confluence score", f"{score['final']}/100 — {_esc(score['label'])}"),
            ("Base / Patterns / Sentiment / Options",
             f"{score['base']:.0f} / {score['pattern']:+d} / "
             f"{score['sentiment']:+d} / {score['options']:+d}"),
        ]))
    if rows_data:
        rows = "".join(
            "<tr>"
            f"<td>{_esc(r.get('name'))}</td>"
            f"<td>{_esc(r.get('value'))}</td>"
            f'<td><span class="{_signal_class(r.get("signal"))}">'
            f"{_esc(r.get('signal'))}</span></td>"
            f"<td>{_fmt(r.get('points'), 1)}</td>"
            "</tr>"
            for r in rows_data
        )
        parts.append(
            "<table><thead><tr><th>Indicator</th><th>Value</th>"
            f"<th>Signal</th><th>Points</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    live = [p for p in patterns if p.get("status") != "Expired"]
    if live:
        items = "".join(
            f"<li>{_esc(p['name'])} ({_esc(p['direction'])}, "
            f"{_esc(p['status'])})</li>"
            for p in live
        )
        parts.append(f"<h3>Candlestick patterns</h3><ul>{items}</ul>")
    if options:
        unusual = " — unusual volume" if options.get("unusual") else ""
        parts.append(f'<p class="meta">Options put/call '
                     f'{_fmt(options.get("pcr"))} '
                     f'({_esc(options.get("label"))}){unusual}</p>')
    if not parts:
        parts.append('<p class="meta">n/a</p>')
    return _card("Technical Analysis", "".join(parts))


def _fundamentals_section(data: dict) -> str:
    fundamentals = data.get("fundamentals")
    if not fundamentals:
        return _card("Fundamentals", '<p class="meta">n/a</p>')
    ratios = fundamentals.get("ratios") or {}
    percentiles = fundamentals.get("history_percentiles") or {}
    dcf = fundamentals.get("dcf")

    ratio_rows = []
    for key, label in (("pe_ratio", "P/E"), ("forward_pe", "Fwd P/E"),
                       ("pb_ratio", "P/B"), ("ps_ratio", "P/S"),
                       ("p_fcf_ratio", "P/FCF"), ("ev_ebitda", "EV/EBITDA")):
        pct = percentiles.get(key)
        pct_text = "n/a" if pct is None else f"{pct:.0f}th pct"
        ratio_rows.append(
            f"<tr><td>{label}</td><td>{_fmt(ratios.get(key))}</td>"
            f"<td>{pct_text}</td></tr>"
        )
    parts = [
        "<table><thead><tr><th>Ratio</th><th>Value</th>"
        f"<th>vs own history</th></tr></thead>"
        f"<tbody>{''.join(ratio_rows)}</tbody></table>",
        _kv([
            ("Fundamental score",
             f"{(fundamentals.get('fundamental_score') or {}).get('total', 'n/a')}/100"),
            ("Moat", f"{_fmt(fundamentals.get('moat_score'), 0)} — "
                     f"{_esc(fundamentals.get('moat_rating'))}"),
            ("Sector percentile", _pct(fundamentals.get("sector_percentile"))),
        ]),
    ]
    if dcf:
        upside = dcf.get("upside_downside_pct")
        upside_class = "up" if (upside or 0) >= 0 else "down"
        parts.append(
            f'<p>DCF fair value <strong>{_fmt(dcf.get("intrinsic_value_per_share"))}</strong>'
            f' → <span class="{upside_class}">{_pct(upside)}</span> '
            f'({_esc(dcf.get("mos_label"))})</p>'
        )
    return _card("Fundamentals", "".join(parts))


def _news_section(data: dict) -> str:
    holding = data.get("holding") or {}
    headlines = (holding.get("sentiment") or {}).get("headlines") or []
    label = (holding.get("sentiment") or {}).get("label") or "n/a"
    if not headlines:
        return _card("News & Sentiment", '<p class="meta">n/a</p>')
    items = "".join(
        f"<li>{_esc(f'[{source}] ' if source else '')}{_esc(title)}</li>"
        for source, title in headlines[:10]
    )
    return _card(
        "News & Sentiment",
        f'<p class="meta">Sentiment: {_esc(label)} · source: '
        f'{_esc(data.get("news_source"))}</p><ul>{items}</ul>',
    )


def render_stock_page(data: dict) -> str:
    """Self-contained HTML page for one ticker's report."""
    sections = "".join([
        _analyst_section(data),
        _technical_section(data),
        _fundamentals_section(data),
        _news_section(data),
    ])
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(data["ticker"])} — Technical &amp; Fundamental</title>
<style>{REPORT_THEME}</style>
</head><body>
{nav_html("dashboard")}
<main class="container">
{_header(data)}
{sections}
</main>
</body></html>"""
