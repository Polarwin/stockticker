"""Comprehensive pre-market report: an 8-section daily briefing.

Covers portfolio holdings only, plus ^VIX/ES=F/NQ=F/^TNX for the market
overview. Sections: market overview, earnings calendar, news sentiment,
options flow, candlestick patterns, confluence score, pre-market movers,
and action items. Delivered as a concise Telegram message (<4096 chars)
plus a self-contained dark premarket_report.html served by web.py.

Data sources: Finnhub (news/earnings timing) and Alpha Vantage (news)
when their API keys are set in the environment, with yfinance as the
fallback for news and the source for quotes, options chains, and
earnings dates. The footer of both outputs notes which source was used.
"""

import html
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from dotenv import load_dotenv

from db import (
    get_options_volume_history,
    init_db,
    resolve_db_path,
    upsert_options_volume,
)
from generate_indicators_table import (
    MIN_BARS,
    combine_score,
    evaluate_indicators,
    history_from_db,
    score_label,
)
from options_flow import fetch_options_flow, flow_label, is_unusual, options_bonus
from patterns import detect_patterns
from sentiment import (
    fetch_headlines,
    fetch_market_headlines,
    fetch_yfinance_headlines,
    news_source,
    score_sentiment,
    sentiment_bonus,
    sentiment_label,
)

# Load environment variables from .env if present (same as notify.py).
load_dotenv()

OUTPUT_PATH = Path(__file__).with_name("premarket_report.html")
TELEGRAM_LIMIT = 4096
FINNHUB_URL = "https://finnhub.io/api/v1"

# Futures/indices for the market-overview section.
OVERVIEW_SYMBOLS = ["ES=F", "NQ=F", "^VIX", "^TNX"]

# Confluence bonuses: pattern by tier (signed by direction), plus the
# sentiment/options bonuses from their modules.
PATTERN_BONUS = {1: 20, 2: 15}

# A quote is (symbol, pre-market price, pre-market % change, previous
# close); any of the three values may be None.
Quote = tuple[str, float | None, float | None, float | None]

# A news item is (source, title).
NewsItem = tuple[str, str]


def load_holdings(db_path: str) -> list[str]:
    """Return held symbols from the holdings table, sorted alphabetically."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT symbol FROM holdings ORDER BY symbol").fetchall()
    return [row[0] for row in rows]


def select_symbols(holdings: list[str], ticker: str | None) -> list[str]:
    """Holdings to cover: just `ticker` for a deep dive, else all holdings."""
    if ticker:
        return [ticker.strip().upper()]
    return holdings


def get_premarket_quotes(symbols: list[str]) -> list[Quote]:
    """Fetch pre-market price, % change, and previous close per symbol.

    Uses Yahoo's preMarket* fields before the open; falls back to the
    regular-session change when pre-market data is unavailable.
    Per-symbol errors print a warning and yield (symbol, None, None, None).
    """
    tickers = yf.Tickers(" ".join(symbols))
    quotes: list[Quote] = []
    for symbol in symbols:
        try:
            info = tickers.tickers[symbol].info
        except Exception as exc:
            print(f"Warning: {symbol}: quote fetch failed ({exc})", file=sys.stderr)
            quotes.append((symbol, None, None, None))
            continue
        price = info.get("preMarketPrice") or info.get("regularMarketPrice")
        prev = info.get("previousClose") or info.get("regularMarketPreviousClose")
        pct = info.get("preMarketChangePercent")
        if pct is None:
            if price is not None and prev:
                pct = (price - prev) / prev * 100
        quotes.append((symbol, price, pct, prev))
    return quotes


def get_news(symbol: str, max_items: int = 2) -> list[NewsItem]:
    """Return up to `max_items` recent (source, title) news items."""
    return fetch_yfinance_headlines(symbol, max_items=max_items)


# ---------------------------------------------------------------------------
# Section 1: market overview
# ---------------------------------------------------------------------------


def fetch_overview() -> dict:
    """Overnight futures, VIX, 10-year yield, and top market headlines."""
    quotes = {s: (p, c, prev) for s, p, c, prev in get_premarket_quotes(OVERVIEW_SYMBOLS)}
    return {"quotes": quotes, "headlines": fetch_market_headlines(max_items=3)}


# ---------------------------------------------------------------------------
# Section 2: earnings calendar
# ---------------------------------------------------------------------------


def _fetch_finnhub_earnings_timing(days: int = 7) -> dict[str, dict] | None:
    """{symbol: {"date", "hour"}} from Finnhub's earnings calendar.

    Returns None when the key is missing or the request fails (the caller
    then reports timing as "n/a" in the source footer).
    """
    token = os.getenv("FINNHUB_API_KEY")
    if not token:
        return None
    today = datetime.now().date()
    try:
        response = requests.get(
            f"{FINNHUB_URL}/calendar/earnings",
            params={
                "from": today.isoformat(),
                "to": (today + timedelta(days=days)).isoformat(),
                "token": token,
            },
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("earningsCalendar", [])
    except Exception as exc:
        print(f"Warning: Finnhub earnings calendar failed ({exc})", file=sys.stderr)
        return None
    timing: dict[str, dict] = {}
    for item in items:
        symbol = item.get("symbol")
        if symbol and symbol not in timing:
            timing[symbol] = {"date": item.get("date"), "hour": item.get("hour")}
    return timing


def _parse_calendar(cal) -> dict | None:
    """Normalize yfinance .calendar (dict or DataFrame) to plain fields."""
    if cal is None:
        return None
    earnings_date = eps = revenue = None
    try:
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or []
            earnings_date = dates[0] if dates else None
            eps = cal.get("Earnings Average")
            revenue = cal.get("Revenue Average")
        else:  # older yfinance: single-column DataFrame indexed by field
            fields = cal.iloc[:, 0]
            dates = fields.get("Earnings Date")
            if isinstance(dates, (list, tuple)):
                earnings_date = dates[0] if dates else None
            else:
                earnings_date = dates
            eps = fields.get("Earnings Average")
            revenue = fields.get("Revenue Average")
    except Exception:
        return None
    if hasattr(earnings_date, "date") and not isinstance(earnings_date, str):
        earnings_date = earnings_date.date()
    if earnings_date is None:
        return None
    return {"date": earnings_date, "eps_estimate": eps, "revenue_estimate": revenue}


def _surprise_history(ticker: yf.Ticker) -> str | None:
    """'Beat X/4' from the last 4 reported quarters, or None."""
    try:
        dates = ticker.earnings_dates
        if dates is None or dates.empty:
            return None
        past = dates.dropna(subset=["EPS Estimate", "Reported EPS"]).head(4)
        if past.empty:
            return None
        beats = int((past["Reported EPS"] > past["EPS Estimate"]).sum())
        return f"Beat {beats}/{len(past)}"
    except Exception:
        return None


def _earnings_when(day, today) -> str | None:
    """Classify an earnings date as today/tomorrow/this week (or None)."""
    if day is None:
        return None
    if hasattr(day, "date") and not isinstance(day, str):
        day = day.date()
    if isinstance(day, str):
        day = datetime.strptime(day, "%Y-%m-%d").date()
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    week_end = today + timedelta(days=6 - today.weekday())
    if today < day <= week_end:
        return "this week"
    return None


def fetch_earnings(symbols: list[str], today) -> tuple[dict[str, dict], bool]:
    """Earnings calendar entry per symbol: date, estimates, timing, surprises.

    BMO/AMC timing comes from Finnhub when FINNHUB_API_KEY is set, else
    "n/a". Per-symbol errors warn and yield a None entry. Returns
    (entries, timing_available): timing_available is False when the
    Finnhub calendar was unreachable or unkeyed this run.
    """
    timing = _fetch_finnhub_earnings_timing()
    entries: dict[str, dict] = {}
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            cal = _parse_calendar(ticker.calendar)
            surprises = _surprise_history(ticker)
        except Exception as exc:
            print(f"Warning: {symbol}: earnings fetch failed ({exc})", file=sys.stderr)
            entries[symbol] = None
            continue
        if cal is None:
            entries[symbol] = None
            continue
        hour = ((timing or {}).get(symbol) or {}).get("hour") or ""
        entries[symbol] = {
            "date": cal["date"].isoformat(),
            "when": _earnings_when(cal["date"], today),
            "eps_estimate": cal["eps_estimate"],
            "revenue_estimate": cal["revenue_estimate"],
            "timing": {"bmo": "BMO", "amc": "AMC"}.get(hour.lower(), "n/a"),
            "surprises": surprises,
        }
    return entries, timing is not None


# ---------------------------------------------------------------------------
# Section 5/6 helpers: pattern status and confluence score
# ---------------------------------------------------------------------------


def update_pattern_status(patterns: list[dict], pct: float | None) -> None:
    """Upgrade Tentative hits using the pre-market move direction.

    Agrees with the reversal direction -> Confirmed; disagrees -> Expired.
    """
    if pct is None or pct == 0:
        return
    for pattern in patterns:
        agrees = (pattern["direction"] == "bullish") == (pct > 0)
        pattern["status"] = "Confirmed" if agrees else "Expired"


def pattern_bonus(patterns: list[dict]) -> int:
    """Signed bonus from the strongest live pattern (Tier 1: 20, Tier 2: 15).

    Expired patterns (premarket move disagrees) no longer count.
    """
    live = [
        p for p in patterns if p["tier"] in PATTERN_BONUS and p["status"] != "Expired"
    ]
    if not live:
        return 0
    best = min(live, key=lambda p: p["tier"])
    magnitude = PATTERN_BONUS[best["tier"]]
    return magnitude if best["direction"] == "bullish" else -magnitude


def apply_bonuses(
    base_score: float,
    patterns: list[dict],
    sentiment_score: float | None,
    pcr: float | None,
) -> dict:
    """Stack pattern/sentiment/options bonuses on the 0-100 base score.

    Returns {base, pattern, sentiment, options, final, label}; the final
    score is clamped to 0-100 and labeled with the existing 5 labels.
    """
    parts = {
        "pattern": pattern_bonus(patterns),
        "sentiment": sentiment_bonus(sentiment_score),
        "options": options_bonus(pcr),
    }
    final = base_score + sum(parts.values())
    final = round(max(0.0, min(100.0, final)), 1)
    return {"base": base_score, **parts, "final": final, "label": score_label(final)}


# Weights/reliabilities for the extra confluence-panel rows (web.py
# /api/indicators). Reliability for patterns follows the tier; the options
# figure is a heuristic estimate, like the other panel reliabilities.
PATTERN_ROW_WEIGHT = 20
PATTERN_ROW_RELIABILITY = {1: 0.75, 2: 0.50}
OPTIONS_ROW_WEIGHT = 8
OPTIONS_ROW_RELIABILITY = 0.55


def pattern_indicator_row(patterns: list[dict]) -> dict:
    """Confluence-panel row for the strongest live candlestick pattern.

    Neutral ("none in last 3 bars") when nothing fired; Expired patterns
    are ignored. points matches pattern_bonus() so the row agrees with
    the score it feeds.
    """
    live = [p for p in patterns if p["status"] != "Expired"]
    if not live:
        return {
            "name": "Candlestick Pattern",
            "value": "none in last 3 bars",
            "signal": "neutral",
            "reliability": 0.50,
            "weight": PATTERN_ROW_WEIGHT,
            "points": 0.0,
        }
    best = min(live, key=lambda p: p["tier"])
    return {
        "name": "Candlestick Pattern",
        "value": f"{best['name']} ({best['status']})",
        "signal": best["direction"],
        "reliability": PATTERN_ROW_RELIABILITY.get(best["tier"], 0.50),
        "weight": PATTERN_ROW_WEIGHT,
        "points": float(pattern_bonus(patterns)),
    }


def options_indicator_row(flow: dict | None) -> dict:
    """Confluence-panel row for options put/call flow (neutral on no data)."""
    pcr = flow["pcr"] if flow else None
    if pcr is None:
        return {
            "name": "Options Flow (PCR)",
            "value": "n/a",
            "signal": "neutral",
            "reliability": OPTIONS_ROW_RELIABILITY,
            "weight": OPTIONS_ROW_WEIGHT,
            "points": 0.0,
        }
    label = flow_label(pcr)
    return {
        "name": "Options Flow (PCR)",
        "value": f"PCR {pcr:.2f} — {label}",
        "signal": "bullish" if "Bullish" in label else "bearish",
        "reliability": OPTIONS_ROW_RELIABILITY,
        "weight": OPTIONS_ROW_WEIGHT,
        "points": float(options_bonus(pcr)),
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def _bars_from_db(conn, symbol: str) -> dict:
    """OHLCV bars from daily_prices, oldest first, ready for detect_patterns."""
    rows = conn.execute(
        """
        SELECT date, open, high, low, close, volume FROM daily_prices
        WHERE symbol = ? AND close IS NOT NULL ORDER BY date
        """,
        (symbol,),
    ).fetchall()
    return {
        "dates": [r[0] for r in rows],
        "opens": [r[1] if r[1] is not None else r[4] for r in rows],
        "highs": [r[2] if r[2] is not None else r[4] for r in rows],
        "lows": [r[3] if r[3] is not None else r[4] for r in rows],
        "closes": [r[4] for r in rows],
        "volumes": [r[5] if r[5] is not None else 0.0 for r in rows],
    }


def build_report_data(settings: dict, ticker: str | None = None) -> dict:
    """Fetch and score everything the report needs.

    With `ticker` set, runs the same pipeline for that one symbol (deep
    dive), whether or not it is a holding. Options-volume snapshots are
    written to the database as a side effect.
    """
    db_path = resolve_db_path(settings.get("db_path", "stockticker.db"))
    tz = ZoneInfo(settings.get("market_timezone", "America/New_York"))
    now = datetime.now(tz)
    today = now.date()

    symbols = select_symbols(load_holdings(str(db_path)), ticker)
    quotes = {s: (p, c, prev) for s, p, c, prev in get_premarket_quotes(symbols)}
    earnings, timing_available = fetch_earnings(symbols, today)

    conn = init_db(db_path)
    holdings: list[dict] = []
    try:
        for symbol in symbols:
            price, pct, prev_close = quotes.get(symbol, (None, None, None))

            headlines = fetch_headlines(symbol)
            sent_score = score_sentiment(headlines)
            sentiment = {
                "count": len(headlines),
                "score": sent_score,
                "label": sentiment_label(sent_score),
                "headlines": headlines,
            }

            flow = fetch_options_flow(symbol)
            options = None
            if flow is not None:
                upsert_options_volume(
                    conn, symbol, today.isoformat(),
                    flow["call_volume"], flow["put_volume"],
                )
                history = get_options_volume_history(
                    conn, symbol, today.isoformat()
                )
                options = {
                    **flow,
                    "unusual": is_unusual(flow, history),
                    "label": flow_label(flow["pcr"]),
                    "baseline_days": len(history),
                }

            bars = _bars_from_db(conn, symbol)
            patterns = []
            if len(bars["closes"]) >= 3:
                patterns = detect_patterns(
                    bars["opens"], bars["highs"], bars["lows"],
                    bars["closes"], bars["volumes"],
                    dates=bars["dates"],
                )
                update_pattern_status(patterns, pct)

            score = None
            indicator_rows = []
            if len(bars["closes"]) >= MIN_BARS:
                closes, volumes = history_from_db(conn, symbol)
                indicator_rows = evaluate_indicators(closes, volumes)
                base = combine_score(indicator_rows)
                score = apply_bonuses(
                    base, patterns, sent_score,
                    options["pcr"] if options else None,
                )

            holdings.append(
                {
                    "symbol": symbol,
                    "price": price,
                    "pct": pct,
                    "prev_close": prev_close,
                    "earnings": earnings.get(symbol),
                    "sentiment": sentiment,
                    "options": options,
                    "patterns": patterns,
                    "score": score,
                    "indicator_rows": indicator_rows,
                }
            )
        conn.commit()
    finally:
        conn.close()

    data = {
        "generated_at": now.strftime("%a %b %d, %H:%M %Z"),
        "ticker": ticker.strip().upper() if ticker else None,
        "overview": fetch_overview(),
        "holdings": holdings,
        "sources": {
            "news": news_source(),
            "timing": "finnhub" if timing_available else "n/a",
        },
    }
    data["actions"] = build_actions(data)
    return data


def build_actions(data: dict) -> dict:
    """Group holdings into action items from scores, patterns, earnings."""
    bullish, bearish, neutral, earnings_today = [], [], [], []
    for holding in data["holdings"]:
        symbol = holding["symbol"]
        score = holding["score"]["final"] if holding["score"] else None
        confirmed = [p for p in holding["patterns"] if p["status"] == "Confirmed"]
        has_bull = any(p["direction"] == "bullish" for p in confirmed)
        has_bear = any(p["direction"] == "bearish" for p in confirmed)
        if score is not None and score >= 60 and has_bull:
            bullish.append(symbol)
        elif (score is not None and score <= 30) or has_bear:
            bearish.append(symbol)
        else:
            neutral.append(symbol)
        if (holding["earnings"] or {}).get("when") == "today":
            earnings_today.append(symbol)
    return {
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "earnings_today": earnings_today,
    }


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------


def _fmt_pct(pct: float | None) -> str:
    return "n/a" if pct is None else f"{pct:+.1f}%"


def _fmt_volume(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(int(value))


def _pattern_names(patterns: list[dict]) -> str:
    live = [p for p in patterns if p["status"] != "Expired"]
    if not live:
        return "-"
    return ", ".join(
        f"{p['name']}{' ✓' if p['status'] == 'Confirmed' else ''}" for p in live
    )


def _mover_line(holding: dict) -> str:
    """One compact per-holding line: '🟢 AMD 530.00 (-2.7%) 24.1 Strong Bearish'."""
    symbol, price, pct = holding["symbol"], holding["price"], holding["pct"]
    if price is None or pct is None:
        return f"⚪ {symbol}: quote unavailable"
    emoji = "🔴" if pct <= -0.5 else "🟢" if pct >= 0.5 else "⚪"
    line = f"{emoji} {symbol} {price:.2f} ({pct:+.1f}%)"
    if holding["score"]:
        line += f" · {holding['score']['final']:.0f} {holding['score']['label']}"
    patterns = _pattern_names(holding["patterns"])
    if patterns != "-":
        line += f" · {patterns}"
    if holding["options"] and holding["options"]["pcr"] is not None:
        line += f" · PCR {holding['options']['pcr']:.2f}"
    if (holding["earnings"] or {}).get("when") in ("today", "tomorrow"):
        line += " · 📅"
    return line


def _format_overview_lines(data: dict) -> list[str]:
    quotes = data["overview"]["quotes"]
    parts = []
    for symbol in ("ES=F", "NQ=F"):
        price, pct, _prev = quotes.get(symbol, (None, None, None))
        parts.append(f"{symbol} {_fmt_pct(pct)}")
    vix_price, vix_pct, _ = quotes.get("^VIX", (None, None, None))
    if vix_price is not None:
        parts.append(f"VIX {vix_price:.2f} ({_fmt_pct(vix_pct)})")
    tnx_price, tnx_pct, _ = quotes.get("^TNX", (None, None, None))
    if tnx_price is not None:
        parts.append(f"^TNX {tnx_price:.2f} ({_fmt_pct(tnx_pct)})")
    return [" · ".join(parts)] if parts else []


def format_telegram(data: dict) -> str:
    """Build the Telegram message, hard-capped under 4096 chars.

    Portfolio mode: overview, earnings today/tomorrow, top 3 bullish and
    bearish movers, action items. Deep-dive mode (data['ticker']): full
    detail for one symbol — all indicator rows, all patterns, up to 5
    headlines, options chain summary.
    """
    lines = [f"🌅 Pre-Market Report — {data['generated_at']}"]
    lines += _format_overview_lines(data)

    if data["ticker"]:
        lines += _deep_dive_lines(data)
    else:
        lines += _portfolio_lines(data)

    lines.append(
        f"News: {data['sources']['news']} · Earnings timing: {data['sources']['timing']}"
    )
    message = "\n".join(line for line in lines if line)
    return _elide(message, TELEGRAM_LIMIT)


def _portfolio_lines(data: dict) -> list[str]:
    lines: list[str] = []
    earnings_soon = [
        h for h in data["holdings"] if (h["earnings"] or {}).get("when")
    ]
    if earnings_soon:
        bits = []
        for h in earnings_soon:
            e = h["earnings"]
            bit = f"{h['symbol']} {e['when']}"
            if e["timing"] != "n/a":
                bit += f" ({e['timing']})"
            bits.append(bit)
        lines.append("📅 Earnings: " + ", ".join(bits))

    ranked = sorted(
        data["holdings"],
        key=lambda h: (
            h["score"]["final"] if h["score"] else 50.0
        ),
        reverse=True,
    )
    top_bullish = [h for h in ranked if h["score"] and h["score"]["final"] >= 60][:3]
    top_bearish = [
        h for h in reversed(ranked) if h["score"] and h["score"]["final"] <= 30
    ][:3]
    if top_bullish:
        lines.append("📈 Top bullish:")
        lines += [_mover_line(h) for h in top_bullish]
    if top_bearish:
        lines.append("📉 Top bearish:")
        lines += [_mover_line(h) for h in top_bearish]

    actions = data["actions"]
    action_lines = []
    if actions["bullish"]:
        action_lines.append("🟢 " + ", ".join(actions["bullish"]))
    if actions["bearish"]:
        action_lines.append("🔴 " + ", ".join(actions["bearish"]))
    if actions["earnings_today"]:
        action_lines.append("📅 Earnings today: " + ", ".join(actions["earnings_today"]))
    if action_lines:
        lines.append("⚡ Action items:")
        lines += action_lines
    return lines


def _deep_dive_lines(data: dict) -> list[str]:
    holding = data["holdings"][0]
    lines = [_mover_line(holding)]

    score = holding["score"]
    if score:
        lines.append(
            f"Score {score['final']:.1f}/100 {score['label']} "
            f"(base {score['base']:.1f}, pattern {score['pattern']:+d}, "
            f"sentiment {score['sentiment']:+d}, options {score['options']:+d})"
        )
        lines.append("Indicators:")
        for row in holding["indicator_rows"]:
            lines.append(
                f"  {row['name']}: {row['value']} — {row['signal']} "
                f"({row['points']:+.1f})"
            )
    else:
        lines.append(f"Score: n/a (needs {MIN_BARS} bars of history)")

    if holding["patterns"]:
        lines.append("Patterns:")
        for p in holding["patterns"]:
            vol = {True: "vol ✓", False: "vol ✗", None: "vol n/a"}[
                p["volume_confirmed"]
            ]
            lines.append(
                f"  {p['name']} (Tier {p['tier']}, {p['direction']}, "
                f"{p['status']}, {vol}) — {p['date'] or 'n/a'}"
            )
    else:
        lines.append("Patterns: none in the last 3 bars")

    sent = holding["sentiment"]
    lines.append(
        f"Sentiment: {sent['score'] if sent['score'] is not None else 'n/a'} "
        f"{sent['label']} ({sent['count']} headlines)"
    )
    for source, title in sent["headlines"][:5]:
        prefix = f"[{source}] " if source else ""
        lines.append(f"  • {prefix}{title}")

    options = holding["options"]
    if options:
        unusual = {True: "⚠ unusual", False: "normal", None: "collecting baseline"}[
            options["unusual"]
        ]
        lines.append(
            f"Options: calls {_fmt_volume(options['call_volume'])}, "
            f"puts {_fmt_volume(options['put_volume'])}, "
            f"PCR {options['pcr'] if options['pcr'] is not None else 'n/a'} "
            f"{options['label']} ({unusual})"
        )
        lines.append("  Expiries: " + ", ".join(options["expiries"]))
    else:
        lines.append("Options: no chain data")

    earnings = holding["earnings"]
    if earnings:
        eps = earnings["eps_estimate"]
        line = f"Earnings: {earnings['date']} ({earnings['timing']})"
        if eps is not None:
            line += f", EPS est {eps}"
        if earnings["surprises"]:
            line += f", {earnings['surprises']}"
        lines.append(line)
    return lines


def _elide(message: str, limit: int) -> str:
    """Hard-cap the message at `limit` chars, truncating at a line boundary."""
    if len(message) <= limit:
        return message
    suffix = "\n… (truncated; full report at /premarket)"
    cut = message[: limit - len(suffix)].rfind("\n")
    if cut <= 0:
        cut = limit - len(suffix)
    return message[:cut] + suffix


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_CSS = """
  body { background: #0d1117; color: #c9d1d9; margin: 0;
         font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .container { max-width: 1100px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 1.3rem; }
  h2 { font-size: 1.1rem; margin: 0 0 8px; }
  .meta { color: #8b949e; font-size: 0.85rem; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
           padding: 16px; margin: 16px 0; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; }
  th { color: #8b949e; font-weight: 600; }
  .bull { color: #2ca02c; }
  .bear { color: #d62728; }
  .neutral { color: #8b949e; }
  .pill { border: 1px solid currentColor; border-radius: 10px;
           padding: 1px 8px; font-size: 0.8rem; white-space: nowrap; }
  .scorebar { position: relative; height: 12px; border-radius: 6px; min-width: 120px;
               background: linear-gradient(90deg, #d62728 0%, #8b949e 50%, #2ca02c 100%); }
  .scoremark { position: absolute; top: -3px; width: 4px; height: 18px;
                background: #ffffff; border-radius: 2px; transform: translateX(-2px); }
  .legend { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
             padding: 12px 16px; margin: 16px 0; font-size: 0.85rem; }
  ul.compact { margin: 8px 0; padding-left: 20px; }
"""


def _label_class(label: str) -> str:
    if "Bullish" in label:
        return "bull"
    if "Bearish" in label:
        return "bear"
    return "neutral"


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _pct_class(pct: float | None) -> str:
    if pct is None:
        return "neutral"
    if pct >= 0.5:
        return "bull"
    if pct <= -0.5:
        return "bear"
    return "neutral"


def _overview_card(data: dict) -> str:
    rows = []
    names = {"ES=F": "S&P 500 futures", "NQ=F": "Nasdaq futures",
             "^VIX": "VIX", "^TNX": "10Y yield"}
    for symbol in OVERVIEW_SYMBOLS:
        price, pct, prev = data["overview"]["quotes"].get(symbol, (None, None, None))
        rows.append(
            "<tr>"
            f"<td>{_esc(symbol)} <span class=\"meta\">{_esc(names[symbol])}</span></td>"
            f"<td>{'n/a' if price is None else f'{price:.2f}'}</td>"
            f'<td><span class="{_pct_class(pct)}">{_fmt_pct(pct)}</span></td>'
            f"<td>{'n/a' if prev is None else f'{prev:.2f}'}</td>"
            "</tr>"
        )
    headlines = "".join(
        f"<li>{_esc(f'[{source}] ' if source else '')}{_esc(title)}</li>"
        for source, title in data["overview"]["headlines"]
    )
    return (
        '<section class="card"><h2>1. Market Overview</h2>'
        "<table><thead><tr><th>Symbol</th><th>Price</th><th>Overnight</th>"
        "<th>Prev Close</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        f'<ul class="compact">{headlines}</ul></section>'
    )


def _earnings_card(data: dict) -> str:
    rows = []
    for h in data["holdings"]:
        e = h["earnings"]
        if not e or (data["ticker"] is None and e["when"] is None):
            continue
        rev = e["revenue_estimate"]
        rows.append(
            "<tr>"
            f"<td>{_esc(h['symbol'])}</td><td>{_esc(e['date'])}</td>"
            f"<td>{_esc(e['when'] or '-')}</td>"
            f"<td>{_esc(f'{eps:.2f}' if (eps := e['eps_estimate']) is not None else 'n/a')}</td>"
            f"<td>{_esc(_fmt_volume(rev) if rev is not None else 'n/a')}</td>"
            f"<td>{_esc(e['timing'])}</td><td>{_esc(e['surprises'] or 'n/a')}</td>"
            "</tr>"
        )
    body = "".join(rows) or '<tr><td colspan="7" class="neutral">No earnings this week</td></tr>'
    return (
        '<section class="card"><h2>2. Earnings Calendar</h2>'
        "<table><thead><tr><th>Symbol</th><th>Date</th><th>When</th>"
        "<th>EPS Est</th><th>Rev Est</th><th>Timing</th><th>Surprises</th></tr></thead>"
        f"<tbody>{body}</tbody></table></section>"
    )


def _sentiment_card(data: dict) -> str:
    rows = []
    for h in data["holdings"]:
        sent = h["sentiment"]
        top = sent["headlines"][0] if sent["headlines"] else None
        top_text = f"[{top[0]}] {top[1]}" if top and top[0] else (top[1] if top else "-")
        rows.append(
            "<tr>"
            f"<td>{_esc(h['symbol'])}</td><td>{sent['count']}</td>"
            f"<td>{_esc(sent['score'] if sent['score'] is not None else 'n/a')}</td>"
            f'<td><span class="pill {_label_class(sent["label"])}">{_esc(sent["label"])}</span></td>'
            f"<td>{_esc(top_text)}</td>"
            "</tr>"
        )
    return (
        '<section class="card"><h2>3. News Sentiment</h2>'
        "<table><thead><tr><th>Symbol</th><th>Headlines</th><th>Score*</th>"
        "<th>Label</th><th>Top Headline</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _options_card(data: dict) -> str:
    rows = []
    for h in data["holdings"]:
        options = h["options"]
        if not options:
            rows.append(
                f"<tr><td>{_esc(h['symbol'])}</td>"
                '<td colspan="5" class="neutral">no chain data</td></tr>'
            )
            continue
        unusual = {
            True: '<span class="bear">⚠ unusual</span>',
            False: "normal",
            None: f"collecting baseline ({options['baseline_days']}d)",
        }[options["unusual"]]
        rows.append(
            "<tr>"
            f"<td>{_esc(h['symbol'])}</td>"
            f"<td>{_fmt_volume(options['call_volume'])}</td>"
            f"<td>{_fmt_volume(options['put_volume'])}</td>"
            f"<td>{_esc(options['pcr'] if options['pcr'] is not None else 'n/a')}</td>"
            f"<td>{unusual}</td>"
            f'<td><span class="pill {_label_class(options["label"])}">{_esc(options["label"])}</span></td>'
            "</tr>"
        )
    return (
        '<section class="card"><h2>4. Options Flow</h2>'
        "<table><thead><tr><th>Symbol</th><th>Call Vol</th><th>Put Vol</th>"
        "<th>PCR</th><th>Unusual</th><th>Flow*</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _patterns_card(data: dict) -> str:
    rows = []
    for h in data["holdings"]:
        for p in h["patterns"]:
            vol = {True: "✓", False: "✗", None: "n/a"}[p["volume_confirmed"]]
            status_class = "bull" if p["status"] == "Confirmed" else (
                "bear" if p["status"] == "Expired" else "neutral"
            )
            rows.append(
                "<tr>"
                f"<td>{_esc(h['symbol'])}</td><td>{_esc(p['name'])}</td>"
                f"<td>{p['tier']}</td><td>{_esc(p['date'] or 'n/a')}</td>"
                f'<td><span class="{_label_class(p["direction"])}">{_esc(p["direction"])}</span></td>'
                f"<td>{_esc(p['trend_context'])}</td><td>{vol}</td>"
                f"<td>{p['reliability'] * 100:.0f}%*</td>"
                f'<td><span class="{status_class}">{_esc(p["status"])}</span></td>'
                "</tr>"
            )
    body = "".join(rows) or '<tr><td colspan="9" class="neutral">No patterns in the last 3 bars</td></tr>'
    return (
        '<section class="card"><h2>5. Candlestick Patterns</h2>'
        "<table><thead><tr><th>Symbol</th><th>Pattern</th><th>Tier</th><th>Date</th>"
        "<th>Direction</th><th>Trend Context</th><th>Vol</th><th>Reliability</th>"
        "<th>Status</th></tr></thead>"
        f"<tbody>{body}</tbody></table></section>"
    )


def _scores_card(data: dict) -> str:
    rows = []
    for h in data["holdings"]:
        score = h["score"]
        if not score:
            rows.append(
                f"<tr><td>{_esc(h['symbol'])}</td>"
                f'<td colspan="7" class="neutral">n/a (needs {MIN_BARS} bars)</td></tr>'
            )
            continue
        rows.append(
            "<tr>"
            f"<td>{_esc(h['symbol'])}</td><td>{score['base']:.1f}</td>"
            f"<td>{score['pattern']:+d}</td><td>{score['sentiment']:+d}</td>"
            f"<td>{score['options']:+d}</td>"
            f"<td><b>{score['final']:.1f}</b></td>"
            f'<td><div class="scorebar"><div class="scoremark" '
            f'style="left:{score["final"]:.1f}%"></div></div></td>'
            f'<td><span class="pill {_label_class(score["label"])}">{_esc(score["label"])}</span></td>'
            "</tr>"
        )
    return (
        '<section class="card"><h2>6. Confluence Score</h2>'
        "<table><thead><tr><th>Symbol</th><th>Base</th><th>Pattern</th>"
        "<th>Sentiment</th><th>Options</th><th>Final</th><th></th><th>Label</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _movers_card(data: dict) -> str:
    ranked = sorted(
        data["holdings"],
        key=lambda h: (h["pct"] is None, -(h["pct"] or 0.0)),
    )
    rows = []
    for h in ranked:
        earnings = "📅 " + h["earnings"]["when"] if (h["earnings"] or {}).get("when") else "-"
        label = h["score"]["label"] if h["score"] else "n/a"
        rows.append(
            "<tr>"
            f"<td>{_esc(h['symbol'])}</td>"
            f"<td>{'n/a' if h['prev_close'] is None else f'{h['prev_close']:.2f}'}</td>"
            f"<td>{'n/a' if h['price'] is None else f'{h['price']:.2f}'}</td>"
            f'<td><span class="{_pct_class(h["pct"])}">{_fmt_pct(h["pct"])}</span></td>'
            f"<td>{_esc(f'{h["score"]["final"]:.1f}' if h['score'] else 'n/a')}</td>"
            f"<td>{_esc(_pattern_names(h['patterns']))}</td>"
            f"<td>{_esc(earnings)}</td>"
            f'<td><span class="pill {_label_class(label)}">{_esc(label)}</span></td>'
            "</tr>"
        )
    return (
        '<section class="card"><h2>7. Pre-Market Movers</h2>'
        "<table><thead><tr><th>Symbol</th><th>Prev Close</th><th>Pre-Market</th>"
        "<th>Change</th><th>Score</th><th>Pattern</th><th>Earnings</th>"
        "<th>Setup</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _actions_card(data: dict) -> str:
    actions = data["actions"]
    items = []
    if actions["bullish"]:
        items.append(
            f'<li class="bull">🟢 {", ".join(map(_esc, actions["bullish"]))} '
            "— score &ge; 60 + confirmed bullish pattern</li>"
        )
    if actions["bearish"]:
        items.append(
            f'<li class="bear">🔴 {", ".join(map(_esc, actions["bearish"]))} '
            "— score &le; 30 or confirmed bearish pattern</li>"
        )
    if actions["neutral"]:
        items.append(
            f'<li class="neutral">⚪ {", ".join(map(_esc, actions["neutral"]))} — no action</li>'
        )
    if actions["earnings_today"]:
        items.append(
            f"<li>📅 {', '.join(map(_esc, actions['earnings_today']))} — earnings today</li>"
        )
    return (
        '<section class="card"><h2>8. Action Items</h2>'
        f'<ul class="compact">{"".join(items)}</ul></section>'
    )


def _deep_dive_card(data: dict) -> str:
    """Full indicator rows for the single deep-dive symbol."""
    h = data["holdings"][0]
    if not h["indicator_rows"]:
        return ""
    rows = "".join(
        "<tr>"
        f"<td>{_esc(row['name'])}</td><td>{_esc(row['value'])}</td>"
        f"<td>{_esc(row['signal'])}</td><td>{row['reliability'] * 100:.0f}%*</td>"
        f"<td>{row['weight']} pts</td><td>{row['points']:+.1f}</td>"
        "</tr>"
        for row in h["indicator_rows"]
    )
    return (
        f'<section class="card"><h2>{_esc(h["symbol"])} Indicator Detail</h2>'
        "<table><thead><tr><th>Indicator</th><th>Current Value</th><th>Signal</th>"
        "<th>Reliability</th><th>Weight</th><th>Points</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></section>"
    )


def render_html(data: dict) -> str:
    """Render the full report as a self-contained dark HTML page."""
    title = (
        f"Pre-Market Deep Dive — {data['ticker']}"
        if data["ticker"]
        else "Pre-Market Report"
    )
    deep = _deep_dive_card(data) if data["ticker"] else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
<h1>{html.escape(title)}</h1>
<p class="meta">Generated {html.escape(data['generated_at'])} &middot;
holdings only &middot; news: {html.escape(data['sources']['news'])} &middot;
earnings timing: {html.escape(data['sources']['timing'])}</p>

{_overview_card(data)}
{_earnings_card(data)}
{_sentiment_card(data)}
{_options_card(data)}
{_patterns_card(data)}
{_scores_card(data)}
{deep}
{_movers_card(data)}
{_actions_card(data)}

<section class="legend">
<h3>Notes</h3>
<p>Figures marked * are historical win-rate estimates (backtest reliability
for patterns, heuristic strength for sentiment/options flow), not expected
returns &mdash; same convention as the indicators table. Confluence score =
indicator base (0&ndash;100) + pattern bonus (&plusmn;20 Tier&nbsp;1,
&plusmn;15 Tier&nbsp;2) + sentiment (&plusmn;5) + options (&plusmn;8/&plusmn;4),
clamped to 0&ndash;100. Pattern status: Tentative until the pre-market move
confirms (✓) or contradicts (Expired) the reversal direction. Options
"unusual" needs a 5-day volume baseline and flags &gt;2&times; the trailing
average; until then PCR only.</p>
</section>
</div>
</body>
</html>
"""


def build_report(settings: dict, ticker: str | None = None) -> str:
    """Build the report, write premarket_report.html, return the Telegram text."""
    data = build_report_data(settings, ticker=ticker)
    OUTPUT_PATH.write_text(render_html(data))
    print(f"Pre-market report written to {OUTPUT_PATH}")
    return format_telegram(data)
