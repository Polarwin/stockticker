"""Bullish/bearish confluence table: weighted technical-indicator scores.

Combines five indicators (RSI, Bollinger Bands, MACD, EMA trend, volume vs
its SMA) into one 0-100 score per watchlist symbol. Each indicator votes
bullish, bearish, or neutral on the latest bar and contributes
+-(weight x reliability) to a raw total, which is normalized to
0 (all bearish) .. 100 (all bullish), with 50 = all neutral. Writes a
self-contained dark-themed indicators_table.html.

Notes on signal definitions:
- RSI trend context ("in uptrend/downtrend") uses EMA9 vs EMA21.
- MACD votes by its position relative to the signal line (above = bullish),
  i.e. the state the most recent crossover left behind; a bare "crossed
  today" rule would be neutral on almost every bar.
- Reliability figures are historical win rates (how often the signal pointed
  the right way in backtests), not expected returns.
"""

import html
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf
from ui_styles import REPORT_THEME, nav_html

from db import init_db, resolve_db_path
from indicators import bollinger_bands, ema, macd, rsi, sma
from ticker import load_watchlist

OUTPUT_PATH = Path(__file__).with_name("indicators_table.html")
MIN_BARS = 40  # MACD signal alone needs 26 + 9 bars; leave headroom
HISTORY_PERIOD = "1y"

# (name, weight in points, reliability = historical win rate)
INDICATOR_SPECS = [
    ("RSI (14)", 30, 0.79),
    ("Bollinger Bands (20, 2)", 25, 0.78),
    ("MACD (12, 26, 9)", 20, 0.40),
    ("EMA Trend (9 vs 21)", 15, 0.31),
    ("Volume vs SMA (20)", 10, 0.55),
]
MAX_RAW_SCORE = sum(weight * rel for _, weight, rel in INDICATOR_SPECS)

SCORE_THRESHOLDS = [
    (70, "Strong Bullish"),
    (50, "Moderate Bullish"),
    (30, "Neutral"),
    (10, "Moderate Bearish"),
    (0, "Strong Bearish"),
]


def evaluate_indicators(closes: list[float], volumes: list[float]) -> list[dict]:
    """Score each indicator bullish/bearish/neutral on the latest bar.

    Returns one row per INDICATOR_SPECS entry:
    {name, value, signal, reliability, weight, points}, where
    points = +weight*reliability (bullish), -weight*reliability (bearish),
    or 0 (neutral). Needs at least ~35 closes; indicator values stay "n/a"
    and neutral when their warm-up period is not met.
    """
    ema9 = ema(closes, 9)[-1]
    ema21 = ema(closes, 21)[-1]
    trend = None
    if ema9 is not None and ema21 is not None:
        trend = "up" if ema9 > ema21 else "down"

    rsi_val = rsi(closes)[-1]
    rsi_signal = "neutral"
    if rsi_val is not None:
        if rsi_val < 30:
            rsi_signal = "bullish"
        elif rsi_val > 70:
            rsi_signal = "bearish"
        elif 50 <= rsi_val <= 70 and trend == "up":
            rsi_signal = "bullish"
        elif rsi_val < 50 and trend == "down":
            rsi_signal = "bearish"

    _mid, upper, lower = bollinger_bands(closes)
    bb_signal = "neutral"
    if upper[-1] is not None and lower[-1] is not None:
        if closes[-1] <= lower[-1]:
            bb_signal = "bullish"
        elif closes[-1] >= upper[-1]:
            bb_signal = "bearish"

    m = macd(closes)
    macd_val, signal_val = m["macd"][-1], m["signal"][-1]
    macd_signal = "neutral"
    if macd_val is not None and signal_val is not None:
        macd_signal = "bullish" if macd_val > signal_val else "bearish"

    ema_signal = "neutral"
    if trend == "up":
        ema_signal = "bullish"
    elif trend == "down":
        ema_signal = "bearish"

    vol_sma = sma(volumes, 20)[-1]
    vol_signal = "neutral"
    if vol_sma is not None and vol_sma > 0 and volumes[-1] > vol_sma:
        vol_signal = "bullish" if closes[-1] >= closes[-2] else "bearish"

    values = [
        "n/a" if rsi_val is None else f"{rsi_val:.1f}",
        (
            "n/a"
            if upper[-1] is None
            else f"{closes[-1]:.2f} (band {lower[-1]:.2f}\u2013{upper[-1]:.2f})"
        ),
        (
            "n/a"
            if macd_val is None or signal_val is None
            else f"{macd_val:+.3f} vs signal {signal_val:+.3f}"
        ),
        (
            "n/a"
            if ema9 is None or ema21 is None
            else f"EMA9 {ema9:.2f} vs EMA21 {ema21:.2f}"
        ),
        (
            "n/a"
            if vol_sma is None
            else f"{volumes[-1] / 1e6:.1f}M vs SMA {vol_sma / 1e6:.1f}M"
        ),
    ]
    signals = [rsi_signal, bb_signal, macd_signal, ema_signal, vol_signal]

    rows = []
    for (name, weight, reliability), value, signal in zip(
        INDICATOR_SPECS, values, signals
    ):
        if signal == "bullish":
            points = weight * reliability
        elif signal == "bearish":
            points = -weight * reliability
        else:
            points = 0.0
        rows.append(
            {
                "name": name,
                "value": value,
                "signal": signal,
                "reliability": reliability,
                "weight": weight,
                "points": points,
            }
        )
    return rows


def combine_score(rows: list[dict]) -> float:
    """Normalize the summed indicator points to 0-100 (50 = all neutral)."""
    raw = sum(row["points"] for row in rows)
    score = 50 + 50 * raw / MAX_RAW_SCORE
    return round(max(0.0, min(100.0, score)), 1)


def score_label(score: float) -> str:
    """Map a 0-100 score to its signal label."""
    for threshold, label in SCORE_THRESHOLDS:
        if score >= threshold:
            return label
    return SCORE_THRESHOLDS[-1][1]


def fetch_history(symbols: list[str]) -> dict[str, tuple[list[float], list[float]]]:
    """Download one year of daily bars; {symbol: (closes, volumes)}.

    Symbols that fail to download are simply omitted (caller falls back to
    the local database).
    """
    data = yf.download(
        symbols,
        period=HISTORY_PERIOD,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
    )
    histories: dict[str, tuple[list[float], list[float]]] = {}
    for symbol in symbols:
        try:
            frame = data[symbol] if symbol in data.columns.get_level_values(0) else data
            frame = frame.dropna(subset=["Close"])
            closes = [float(c) for c in frame["Close"]]
            volumes = [
                float(v) if v == v else 0.0 for v in frame["Volume"].fillna(0)
            ]
        except Exception as exc:
            print(f"Warning: {symbol}: history fetch failed ({exc})", file=sys.stderr)
            continue
        if closes:
            histories[symbol] = (closes, volumes)
    return histories


def history_from_db(conn, symbol: str) -> tuple[list[float], list[float]]:
    """(closes, volumes) from the local daily_prices table, oldest first."""
    rows = conn.execute(
        """
        SELECT close, volume FROM daily_prices
        WHERE symbol = ? AND close IS NOT NULL ORDER BY date
        """,
        (symbol,),
    ).fetchall()
    closes = [r[0] for r in rows]
    volumes = [r[1] if r[1] is not None else 0.0 for r in rows]
    return closes, volumes


def _reliability_color(reliability: float) -> str:
    if reliability >= 0.70:
        return "#2ca02c"  # green: high win rate
    if reliability < 0.40:
        return "#d62728"  # red: poor win rate
    return "#d4a017"  # yellow: medium / confirmation-only


def _signal_cell(signal: str) -> str:
    if signal == "bullish":
        return '<span class="bull">\u25b2 Bullish</span>'
    if signal == "bearish":
        return '<span class="bear">\u25bc Bearish</span>'
    return '<span class="neutral">\u25cf Neutral</span>'


def _label_class(label: str) -> str:
    if "Bullish" in label:
        return "bull"
    if "Bearish" in label:
        return "bear"
    return "neutral"


def build_table_html(results: list[dict], generated_at: str) -> str:
    """Render the per-symbol indicator rows as a self-contained HTML page.

    results: [{symbol, score, label, rows}], rows as from
    evaluate_indicators(). Pure HTML/CSS: no external scripts or stylesheets.
    """
    cards = []
    for result in results:
        rows_html = []
        for row in result["rows"]:
            rel_pct = row["reliability"] * 100
            points = f"{row['points']:+.1f}" if row["points"] else "0"
            rows_html.append(
                "<tr>"
                f"<td>{html.escape(row['name'])}</td>"
                f"<td>{html.escape(row['value'])}</td>"
                f"<td>{_signal_cell(row['signal'])}</td>"
                f'<td><div class="relbar"><div class="relfill" '
                f'style="width:{rel_pct:.0f}%;background:{_reliability_color(row["reliability"])}">'
                f"</div></div> {rel_pct:.0f}%</td>"
                f"<td>{row['weight']} pts</td>"
                f"<td>{points}</td>"
                "</tr>"
            )
        score = result["score"]
        label = result["label"]
        cards.append(
            '<section class="card">'
            f"<h2>{html.escape(result['symbol'])} "
            f'<span class="pill {_label_class(label)}">{label}</span></h2>'
            '<div class="scorebar">'
            f'<div class="scoremark" style="left:{score:.1f}%"></div>'
            "</div>"
            f'<div class="scorenum">Score {score:.1f} / 100</div>'
            "<table>"
            "<thead><tr><th>Indicator</th><th>Current Value</th><th>Signal</th>"
            "<th>Reliability</th><th>Weight</th><th>Points</th></tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody>"
            "</table>"
            "</section>"
        )

    summary_rows = "".join(
        "<tr>"
        f"<td>{html.escape(r['symbol'])}</td>"
        f"<td>{r['score']:.1f}</td>"
        f'<td><span class="pill {_label_class(r["label"])}">{r["label"]}</span></td>'
        "</tr>"
        for r in sorted(results, key=lambda r: r["score"], reverse=True)
    )

    legend_items = "".join(
        f'<li><span class="pill {_label_class(label)}">{label}</span> '
        f"&ge; {threshold}" if threshold else f'<li><span class="pill {_label_class(label)}">{label}</span> &lt; 10'
        for threshold, label in SCORE_THRESHOLDS
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Technical Indicators &mdash; Bullish/Bearish Confluence</title>
<style>
  body {{ background: #0d1117; color: #c9d1d9; margin: 0;
         font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
  .container {{ max-width: 960px; margin: 0 auto; padding: 16px; }}
  h1 {{ font-size: 1.3rem; }}
  h2 {{ font-size: 1.1rem; margin: 0 0 8px; }}
  .meta {{ color: #8b949e; font-size: 0.85rem; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
           padding: 16px; margin: 16px 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; }}
  th {{ color: #8b949e; font-weight: 600; }}
  .bull {{ color: #2ca02c; }}
  .bear {{ color: #d62728; }}
  .neutral {{ color: #8b949e; }}
  .pill {{ border: 1px solid currentColor; border-radius: 10px;
           padding: 1px 8px; font-size: 0.8rem; }}
  .relbar {{ display: inline-block; width: 80px; height: 8px; vertical-align: middle;
             background: #21262d; border-radius: 4px; overflow: hidden; }}
  .relfill {{ height: 100%; }}
  .scorebar {{ position: relative; height: 12px; border-radius: 6px;
               background: linear-gradient(90deg, #d62728 0%, #8b949e 50%, #2ca02c 100%); }}
  .scoremark {{ position: absolute; top: -3px; width: 4px; height: 18px;
                background: #ffffff; border-radius: 2px; transform: translateX(-2px); }}
  .scorenum {{ color: #8b949e; font-size: 0.85rem; margin: 6px 0 12px; }}
  .legend, .guide {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                     padding: 12px 16px; margin: 16px 0; font-size: 0.85rem; }}
  .legend ul {{ list-style: none; padding: 0; margin: 8px 0 0; }}
  .legend li {{ margin: 4px 0; }}
  .guide h3, .legend h3 {{ margin: 0 0 4px; font-size: 0.95rem; }}
  @media (max-width: 640px) {{
    .card {{ overflow-x: auto; }}
    table {{ min-width: 560px; }}
  }}
  {REPORT_THEME}
</style>
</head>
<body>
{nav_html("dashboard")}
<div class="container">
<h1>Technical Indicators &mdash; Bullish/Bearish Confluence</h1>
<p class="meta">Generated {html.escape(generated_at)} &middot; daily bars, latest close</p>

<section class="card">
<h2>Summary</h2>
<table>
<thead><tr><th>Symbol</th><th>Score (0&ndash;100)</th><th>Signal</th></tr></thead>
<tbody>{summary_rows}</tbody>
</table>
</section>

{''.join(cards)}

<section class="legend">
<h3>Signal thresholds</h3>
<ul>{legend_items}</ul>
<p class="meta">Score = 50 + 50 &times; (&Sigma; &plusmn;weight&times;reliability) / {MAX_RAW_SCORE:.1f}.
50 means perfectly balanced; every indicator voting the same way reaches 0 or 100.</p>
</section>

<section class="guide">
<h3>How to read the reliability figures</h3>
<p>Reliability is the signal's historical <b>win rate</b> &mdash; how often it
pointed in the right direction in backtests &mdash; <b>not</b> the return you
should expect. A 79% win rate means the signal was right about 4 out of 5
times, but says nothing about how much was won or lost each time; a
high-win-rate signal with small average gains can still underperform a
low-win-rate signal with large gains. Low-reliability indicators (MACD, EMA
trend) carry less weight and should only confirm, never drive a decision on
their own.</p>
</section>
</div>
</body>
</html>
"""


def generate_indicators_table(
    settings: dict,
    test: bool = False,
    output_path: Path = OUTPUT_PATH,
) -> Path | None:
    """Generate indicators_table.html for all watchlist symbols.

    Daily bars come from a batched yfinance download, falling back to the
    local database per symbol. Console-only output. Returns the output path,
    or None when no symbol has enough history.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    symbols = load_watchlist()

    histories = fetch_history(symbols)
    missing = [
        s for s in symbols if len(histories.get(s, ([], []))[0]) < MIN_BARS
    ]
    if missing:
        conn = init_db(resolve_db_path(settings["db_path"]))
        try:
            for symbol in missing:
                closes, volumes = history_from_db(conn, symbol)
                if len(closes) >= MIN_BARS:
                    histories[symbol] = (closes, volumes)
        finally:
            conn.close()

    results = []
    for symbol in symbols:
        closes, volumes = histories.get(symbol, ([], []))
        if len(closes) < MIN_BARS:
            print(
                f"{timestamp} {symbol}: only {len(closes)} bars "
                f"(need {MIN_BARS}), skipped"
            )
            continue
        rows = evaluate_indicators(closes, volumes)
        score = combine_score(rows)
        label = score_label(score)
        results.append({"symbol": symbol, "score": score, "label": label, "rows": rows})
        print(f"{timestamp} {symbol}: score {score:.1f} ({label})")

    if not results:
        print(f"{timestamp} No symbols with enough history; table not generated")
        return None

    output_path.write_text(build_table_html(results, timestamp))
    print(f"{timestamp} Indicators table written to {output_path}")
    return output_path
