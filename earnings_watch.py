"""Post-earnings watch: 10-minute price + news updates for ~3h after release.

On a watchlist symbol's earnings day (dates come from the DB `earnings`
table, refreshed daily from the same yfinance calendar as the earnings
reminder), detects the actual report release by watching for a new quarter
in the yfinance earnings history, falling back to the SEC EDGAR filing
feed (8-K Item 2.02, or 6-K for foreign issuers) which publishes minutes
after release while yfinance lags by hours. Detection then triggers a
Telegram message every
`earnings_watch_interval_minutes` for `earnings_watch_duration_minutes`.
Each message carries the live price (extended hours included, same source as
the web UI) plus freshly published Yahoo Finance news; items mentioning
guidance/outlook/forecast are tagged with 🔮.

State lives in the DB (`earnings_watch`, `earnings_watch_news` tables) so a
daemon restart neither loses the window nor resends news. In test mode
nothing is written to the database and no messages should be sent (caller's
job).
"""

import sys
from datetime import datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

from db import (
    delete_old_watch_state,
    get_earnings_on,
    get_watch_state,
    init_db,
    mark_news_sent,
    news_already_sent,
    resolve_db_path,
    upsert_watch_state,
)
from earnings_report import fetch_earnings_report, format_report
from ticker import fetch_live_quotes, format_price_line

GUIDANCE_KEYWORDS = ("guidance", "outlook", "forecast")
# Poll window in market-local time; covers both BMO and AMC releases.
# Gates release detection only; active watches run their full duration.
POLL_START = dt_time(6, 0)
POLL_END = dt_time(21, 0)
# How far back to collect news when the release is first detected.
NEWS_LOOKBACK = timedelta(hours=12)


def fetch_latest_quarter(symbol: str) -> str | None:
    """Return the latest reported quarter (ISO date) for a symbol, or None.

    Raises ValueError with a per-symbol message on fetch failure.
    """
    try:
        history = yf.Ticker(symbol).get_earnings_history()
    except Exception as exc:
        raise ValueError(f"{symbol}: earnings history fetch failed ({exc})")
    if history is None or history.empty or "epsActual" not in history.columns:
        return None
    history = history.dropna(subset=["epsActual"]).sort_index()
    if history.empty:
        return None
    quarter = history.index[-1]
    if hasattr(quarter, "date"):
        quarter = quarter.date()
    return quarter.isoformat()


EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
DEFAULT_EDGAR_USER_AGENT = "stockticker/1.0 admin@example.com"

# Symbol -> zero-padded CIK map, cached for the process lifetime.
_cik_map: dict[str, str] | None = None


def _edgar_get(url: str, settings: dict) -> dict:
    """GET an EDGAR JSON endpoint with the required User-Agent header."""
    user_agent = settings.get("edgar_user_agent") or DEFAULT_EDGAR_USER_AGENT
    response = requests.get(url, headers={"User-Agent": user_agent}, timeout=20)
    response.raise_for_status()
    return response.json()


def _cik_for(symbol: str, settings: dict) -> str | None:
    """Resolve a ticker to its zero-padded EDGAR CIK, or None if unknown."""
    global _cik_map
    if _cik_map is None:
        data = _edgar_get(EDGAR_TICKERS_URL, settings)
        _cik_map = {
            entry["ticker"].upper(): str(entry["cik_str"]).zfill(10)
            for entry in data.values()
        }
    return _cik_map.get(symbol.upper())


def edgar_release_filed(symbol: str, filing_date: str, settings: dict) -> bool:
    """True if EDGAR shows an earnings-release filing on `filing_date`.

    US issuers file an 8-K with Item 2.02 (results of operations) on release
    day; foreign private issuers file a 6-K instead. Returns False for
    symbols with no CIK mapping. Raises ValueError with a per-symbol
    message on fetch failure.
    """
    try:
        cik = _cik_for(symbol, settings)
        if cik is None:
            return False
        data = _edgar_get(EDGAR_SUBMISSIONS_URL.format(cik=cik), settings)
    except requests.RequestException as exc:
        raise ValueError(f"{symbol}: EDGAR fetch failed ({exc})")

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    items = recent.get("items", [])
    for form, date, item_list in zip(forms, dates, items):
        if date < filing_date:  # recent filings are newest-first
            break
        if date != filing_date:
            continue
        if form == "8-K" and "2.02" in item_list:
            return True
        if form == "6-K":
            return True
    return False


def fetch_news(symbol: str) -> list[dict]:
    """Fetch recent Yahoo Finance news for a symbol.

    Each item: {id, title, url, source, published (aware datetime or None),
    is_guidance}. Raises ValueError with a per-symbol message on failure.
    """
    try:
        raw = yf.Ticker(symbol).news
    except Exception as exc:
        raise ValueError(f"{symbol}: news fetch failed ({exc})")

    items = []
    for entry in raw or []:
        content = entry.get("content", entry)
        news_id = entry.get("id") or content.get("id")
        title = content.get("title")
        if not news_id or not title:
            continue

        published = None
        pub_date = content.get("pubDate")
        if pub_date:
            try:
                published = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
            except ValueError:
                published = None
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)

        url = (content.get("canonicalUrl") or {}).get("url") or (
            content.get("clickThroughUrl") or {}
        ).get("url")
        source = (content.get("provider") or {}).get("displayName", "")
        text = f"{title} {content.get('summary') or ''}".lower()

        items.append(
            {
                "id": news_id,
                "title": title,
                "url": url,
                "source": source,
                "published": published,
                "is_guidance": any(k in text for k in GUIDANCE_KEYWORDS),
            }
        )
    items.sort(key=lambda i: (i["published"] is None, i["published"] or datetime.min.replace(tzinfo=timezone.utc)))
    return items


def format_watch_message(
    symbol: str,
    quote: dict | None,
    news_items: list[dict],
    elapsed_min: int,
    report: dict | None = None,
) -> str:
    """Format one watch update as a Telegram message.

    On the detection message (report given) the full earnings report body is
    embedded; later ticks carry just the price line.
    """
    lines = [f"⏱️ Post-Earnings Watch — {symbol} (+{elapsed_min} min)"]
    if report is not None:
        lines.append(format_report(report, quote))
    elif quote:
        lines.append(
            format_price_line(
                symbol, quote["price"], quote.get("change_pct"), for_telegram=True
            )
        )
    else:
        lines.append(f"⚠️ {symbol}: price unavailable")
    for item in news_items:
        tag = "🔮" if item["is_guidance"] else "📰"
        source = f" — {item['source']}" if item["source"] else ""
        lines.append(f"{tag} {item['title']}{source}")
        if item["url"]:
            lines.append(item["url"])
    return "\n".join(lines)


def _fresh_news(conn, symbol: str, since: datetime) -> list[dict]:
    """Return news items published after `since` that were not sent yet."""
    try:
        items = fetch_news(symbol)
    except ValueError as exc:
        print(f"Warning: {exc}", file=sys.stderr)
        return []
    return [
        item
        for item in items
        if (item["published"] is None or item["published"] > since)
        and not news_already_sent(conn, symbol, item["id"])
    ]


def _parse_stored(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def run_watch_tick(settings: dict, test: bool = False) -> list[str]:
    """Advance every active post-earnings watch by one step.

    Called once per daemon loop iteration; self-limits to earnings days.
    The poll window gates release *detection* only — a watch that already
    detected runs its full duration even past POLL_END. Returns the
    Telegram messages to send (empty when there is nothing to report).
    Prints timestamped console lines throughout. In test mode nothing is
    written to the database.
    """
    if not settings.get("earnings_watch_enabled", True):
        return []

    market_tz = ZoneInfo(settings["market_timezone"])
    now = datetime.now(market_tz)
    in_poll_window = POLL_START <= now.time() <= POLL_END
    today = now.date().isoformat()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    interval = timedelta(minutes=settings["earnings_watch_interval_minutes"])
    duration = timedelta(minutes=settings["earnings_watch_duration_minutes"])

    conn = init_db(resolve_db_path(settings["db_path"]))
    try:
        symbols = get_earnings_on(conn, today)
        if not symbols:
            return []
        if not test:
            delete_old_watch_state(conn, today)

        messages: list[str] = []
        for symbol in symbols:
            state = get_watch_state(conn, symbol, today)

            if state is None:
                # First sighting on the earnings day: seed the baseline
                # quarter without notifying (detection needs something to
                # compare against).
                try:
                    baseline = fetch_latest_quarter(symbol)
                except ValueError as exc:
                    print(f"Warning: {exc}", file=sys.stderr)
                    continue
                print(
                    f"{timestamp} {symbol}: seeding earnings watch baseline "
                    f"({baseline})"
                )
                if not test:
                    upsert_watch_state(conn, symbol, today, baseline, None, None)
                continue

            detected_at = _parse_stored(state["detected_at"], market_tz)

            if detected_at is None:
                if not in_poll_window:
                    continue
                try:
                    current = fetch_latest_quarter(symbol)
                except ValueError as exc:
                    print(f"Warning: {exc}", file=sys.stderr)
                    current = None
                if state["baseline_quarter"] is None and current is not None:
                    # No usable baseline (seeding found nothing): reseed.
                    if not test:
                        upsert_watch_state(conn, symbol, today, current, None, None)

                detected: str | None = None
                if (
                    current is not None
                    and state["baseline_quarter"] is not None
                    and current > state["baseline_quarter"]
                ):
                    # A newer quarter appeared: the report is out.
                    detected = current
                if detected is None:
                    # yfinance lags real releases by hours; fall back to
                    # EDGAR, where the 8-K/6-K appears minutes after release.
                    try:
                        if edgar_release_filed(symbol, today, settings):
                            detected = f"8-K/6-K filed {today}"
                    except ValueError as exc:
                        print(f"Warning: {exc}", file=sys.stderr)
                if detected is None:
                    continue

                print(f"{timestamp} {symbol}: earnings release detected ({detected})")
                report = None
                if detected == current:
                    # Only a yfinance-based detection means its report data
                    # is fresh; on EDGAR detection it still shows the old
                    # quarter, so skip the stale report body.
                    try:
                        report = fetch_earnings_report(symbol)
                    except ValueError as exc:
                        print(f"Warning: {exc}", file=sys.stderr)
                quote = fetch_live_quotes([symbol]).get(symbol)
                news = _fresh_news(conn, symbol, now - NEWS_LOOKBACK)
                messages.append(format_watch_message(symbol, quote, news, 0, report))
                if not test:
                    sent_at = now.isoformat()
                    for item in news:
                        mark_news_sent(conn, symbol, item["id"], sent_at)
                    upsert_watch_state(
                        conn,
                        symbol,
                        today,
                        state["baseline_quarter"],
                        now.isoformat(),
                        now.isoformat(),
                    )
                continue

            elapsed = now - detected_at
            if elapsed > duration:
                continue
            last_tick = _parse_stored(state["last_tick_at"], market_tz)
            if last_tick is not None and now - last_tick < interval:
                continue

            elapsed_min = int(elapsed.total_seconds() // 60)
            print(f"{timestamp} {symbol}: watch update (+{elapsed_min} min)")
            quote = fetch_live_quotes([symbol]).get(symbol)
            news = _fresh_news(conn, symbol, last_tick or now - NEWS_LOOKBACK)
            messages.append(format_watch_message(symbol, quote, news, elapsed_min))
            if not test:
                sent_at = now.isoformat()
                for item in news:
                    mark_news_sent(conn, symbol, item["id"], sent_at)
                upsert_watch_state(
                    conn,
                    symbol,
                    today,
                    state["baseline_quarter"],
                    state["detected_at"],
                    now.isoformat(),
                )

        if not test:
            conn.commit()
        return messages
    finally:
        conn.close()
