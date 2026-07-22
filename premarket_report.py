"""Pre-market portfolio report: holdings quotes plus news on big movers."""

import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import yfinance as yf

# A quote is (symbol, pre-market price or None, pre-market % change or None).
Quote = tuple[str, float | None, float | None]

# A news item is (source, title).
NewsItem = tuple[str, str]

# Symbols that never get a news dig: indices and broad ETFs.
NEWS_EXCLUDE = {"SPY", "QQQ"}


def load_holdings(db_path: str) -> list[str]:
    """Return held symbols from the holdings table, sorted alphabetically."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT symbol FROM holdings ORDER BY symbol").fetchall()
    return [row[0] for row in rows]


def get_premarket_quotes(symbols: list[str]) -> list[Quote]:
    """Fetch pre-market price and % change for each symbol.

    Uses Yahoo's preMarket* fields before the open; falls back to the
    regular-session change when pre-market data is unavailable.
    Per-symbol errors print a warning and yield (symbol, None, None).
    """
    tickers = yf.Tickers(" ".join(symbols))
    quotes: list[Quote] = []
    for symbol in symbols:
        try:
            info = tickers.tickers[symbol].info
        except Exception as exc:
            print(f"Warning: {symbol}: quote fetch failed ({exc})", file=sys.stderr)
            quotes.append((symbol, None, None))
            continue
        price = info.get("preMarketPrice") or info.get("regularMarketPrice")
        pct = info.get("preMarketChangePercent")
        if pct is None:
            prev = info.get("previousClose") or info.get("regularMarketPreviousClose")
            if price is not None and prev:
                pct = (price - prev) / prev * 100
        quotes.append((symbol, price, pct))
    return quotes


def get_news(symbol: str, max_items: int = 2) -> list[NewsItem]:
    """Return up to `max_items` recent (source, title) news items."""
    items: list[NewsItem] = []
    for raw in yf.Ticker(symbol).news or []:
        content = raw.get("content", raw)
        title = content.get("title")
        if not title:
            continue
        provider = content.get("provider")
        if isinstance(provider, dict):
            source = provider.get("displayName", "")
        else:
            source = content.get("publisher", "")
        items.append((source, title))
        if len(items) >= max_items:
            break
    return items


def _format_quote_line(symbol: str, price: float | None, pct: float | None) -> str:
    """Format one holding as '🔴 AMD 530.00 (-2.7%)'."""
    if price is None or pct is None:
        return f"⚪ {symbol}: quote unavailable"
    if pct <= -0.5:
        emoji = "🔴"
    elif pct >= 0.5:
        emoji = "🟢"
    else:
        emoji = "⚪"
    return f"{emoji} {symbol} {price:.2f} ({pct:+.1f}%)"


def build_report(settings: dict) -> str:
    """Build the Telegram-ready pre-market report message.

    Covers every holding; adds a news dig only for holdings whose
    pre-market move exceeds premarket_move_threshold_pct.
    """
    db_path = settings.get("db_path", "stockticker.db")
    threshold = float(settings.get("premarket_move_threshold_pct", 2.0))
    tz = ZoneInfo(settings.get("market_timezone", "America/New_York"))
    now = datetime.now(tz)

    holdings = load_holdings(db_path)
    symbols = holdings if "^VIX" in holdings else holdings + ["^VIX"]
    quotes = dict((s, (p, c)) for s, p, c in get_premarket_quotes(symbols))

    vix_price, vix_pct = quotes.pop("^VIX", (None, None))
    vix_str = (
        "unavailable"
        if vix_price is None or vix_pct is None
        else f"{vix_price:.2f} ({vix_pct:+.1f}%)"
    )

    header = (
        f"🌅 Pre-Market Portfolio Report — {now.strftime('%a %b %d, %H:%M %Z')}\n"
        f"VIX {vix_str}"
    )

    ranked = sorted(
        ((s, p, c) for s, (p, c) in quotes.items()),
        key=lambda q: (q[2] is None, q[2] or 0.0),
    )
    lines = [_format_quote_line(s, p, c) for s, p, c in ranked]

    news_lines: list[str] = []
    for symbol, _price, pct in ranked:
        if pct is None or abs(pct) < threshold:
            continue
        if symbol in NEWS_EXCLUDE or symbol.startswith("^"):
            continue
        try:
            news = get_news(symbol)
        except Exception as exc:
            print(f"Warning: {symbol}: news fetch failed ({exc})", file=sys.stderr)
            continue
        for source, title in news:
            prefix = f"[{source}] " if source else ""
            news_lines.append(f"• {symbol}: {prefix}{title}")

    parts = [header, *lines]
    if news_lines:
        parts.append("📰 Movers in the news:\n" + "\n".join(news_lines))
    return "\n".join(parts)
