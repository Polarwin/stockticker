"""Futu OpenD data source: news headlines, company profile, financial statements.

Talks to the local Futu OpenD gateway (FUTU_OPEND_HOST / FUTU_OPEND_PORT,
default 127.0.0.1:11111) via the futu-api SDK. The futu package is
imported lazily (same pattern as FutuBot's futu_news.py) so this module
imports fine without the SDK, and a failed connect disables it for the
rest of the process — callers can use it as an optional first provider
that falls back to yfinance (fundamentals/fetcher.py) or the HTTP news
providers (sentiment.py).

Public functions raise ValueError with a "{ticker}: ..." message on
failure, matching the fetcher.py contract so callers can warn-and-fall-
back per symbol.
"""

import atexit
import os
import sys
import time
from datetime import datetime, timedelta

# A headline is (source, title) — same shape as sentiment.py.
Headline = tuple[str, str]

# Futu rate limit: 10 news searches per 30 seconds. No throttle here —
# on a rate-limit error the caller's provider chain falls through to the
# next source.
NEWS_RESULT_LIMIT = 30

# Watchlist-symbol -> Futu-code overrides for names that differ from the
# default "US.<SYMBOL>" rule (checked by to_futu_code first). NOTE: this
# OpenD build refuses to return quotes for US indices ("US stock indices
# are not supported") even though the codes exist in its security list —
# so ^VIX is mapped to the correct code US..VIX below, but is currently
# always dropped from the snapshot response and served by yfinance. The
# mapping starts working by itself if a future OpenD enables US indices.
SYMBOL_MAP: dict[str, str] = {
    "^VIX": "US..VIX",
}

_ctx = None
_disabled = False


def _close_ctx() -> None:
    """Close the shared context; the SDK's worker threads block exit otherwise."""
    global _ctx
    if _ctx is not None:
        try:
            _ctx.close()
        except Exception:
            pass
        _ctx = None


def _futu():
    """Import the futu SDK lazily so tests/imports work without it."""
    import futu

    return futu


def _quote_ctx():
    """Shared OpenQuoteContext; None (and disabled) after one failure."""
    global _ctx, _disabled
    if _disabled:
        return None
    if _ctx is None:
        try:
            futu = _futu()
            # SDK worker threads are non-daemon by default and would block
            # interpreter exit; daemonize them so scripts can exit without
            # an explicit close (atexit still closes gracefully).
            futu.SysConfig.set_all_thread_daemon(True)
            host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
            port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
            _ctx = futu.OpenQuoteContext(host=host, port=port)
            atexit.register(_close_ctx)
        except Exception:
            _disabled = True
            return None
    return _ctx


def available() -> bool:
    """True when the futu SDK is installed and OpenD is reachable."""
    return _quote_ctx() is not None


def reset_for_tests() -> None:
    """Drop the cached context and re-enable (used by unit tests)."""
    global _ctx, _disabled, _last_statement_call
    _ctx = None
    _disabled = False
    _last_statement_call = 0.0


def to_futu_code(symbol: str) -> str | None:
    """Map a watchlist symbol to a Futu code ('US.AAPL'); None if not US equity.

    SYMBOL_MAP overrides are checked first. '^'-prefixed indexes and
    symbols with characters outside US ticker conventions are not handled.
    Class shares use Futu's dot separator ('BRK-B' -> 'US.BRK.B').
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return None
    if symbol in SYMBOL_MAP:
        return SYMBOL_MAP[symbol]
    if symbol.startswith("^"):
        return None
    if not all(ch.isalnum() or ch in ".-" for ch in symbol):
        return None
    return f"US.{symbol.replace('-', '.')}"


def fetch_headlines(symbol: str, hours: int = 24) -> list[Headline]:
    """Recent Futu news for a symbol as (source, title), filtered to `hours`."""
    code = to_futu_code(symbol)
    if code is None:
        raise ValueError(f"{symbol}: not a US symbol Futu can serve")
    ctx = _quote_ctx()
    if ctx is None:
        raise ValueError(f"{symbol}: Futu OpenD unavailable")
    futu = _futu()
    ret, data = ctx.get_search_news(
        code, max_count=NEWS_RESULT_LIMIT, news_sub_type=futu.NewsSubType.NEWS
    )
    if ret != futu.RET_OK:
        raise ValueError(f"{symbol}: Futu news search failed ({data})")

    cutoff = datetime.now() - timedelta(hours=hours)
    items: list[Headline] = []
    for record in data.to_dict("records"):
        title = str(record.get("title") or "").strip()
        if not title:
            continue
        published = str(record.get("publish_time") or "").strip()
        when = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%m/%d"):
            try:
                parsed = datetime.strptime(published, fmt)
                # Futu gives recent items as "M/D" — assume current year.
                when = parsed.replace(year=datetime.now().year) if fmt == "%m/%d" else parsed
                break
            except ValueError:
                continue
        if when is not None and when < cutoff:
            continue
        items.append((str(record.get("source") or ""), title))
    return items


def fetch_price_history(ticker: str, years: int = 10) -> list[tuple[str, float]]:
    """Weekly adjusted closes for `years` back: [(date, close)] oldest first.

    Weekly bars keep the request count low (10 years ≈ 520 bars) while
    giving plenty of depth for historical valuation percentiles. Prices
    are QFQ-adjusted so splits don't distort long windows.
    """
    code = to_futu_code(ticker)
    if code is None:
        raise ValueError(f"{ticker}: not a US symbol Futu can serve")
    ctx = _quote_ctx()
    if ctx is None:
        raise ValueError(f"{ticker}: Futu OpenD unavailable")
    futu = _futu()

    end = datetime.now().date()
    start = end - timedelta(days=years * 365)
    points: list[tuple[str, float]] = []
    page_key = None
    for _ in range(20):  # safety cap; 10y of weekly bars needs 1 page
        try:
            ret, data, page_key = ctx.request_history_kline(
                code,
                start=start.isoformat(),
                end=end.isoformat(),
                ktype=futu.KLType.K_WEEK,
                max_count=1000,
                page_req_key=page_key,
            )
        except Exception as exc:
            raise ValueError(
                f"{ticker}: Futu price history fetch failed ({exc})"
            ) from exc
        if ret != futu.RET_OK:
            raise ValueError(f"{ticker}: Futu price history failed ({data})")
        for record in data.to_dict("records"):
            close = _number(record.get("close"))
            day = str(record.get("time_key") or "")[:10]
            if close is not None and day:
                points.append((day, close))
        if not page_key:
            break
    if not points:
        raise ValueError(f"{ticker}: no Futu price history available")
    points.sort(key=lambda p: p[0])
    return points


def _session_price(row: dict, market_state: str) -> float | None:
    """Latest price for the current session of a snapshot row.

    The Futu snapshot carries pre_price / after_price / overnight_price
    alongside last_price; outside regular hours last_price is the stale
    regular-session close, so pick the field matching the market state
    (falling back to last_price when the session field is empty).
    """
    state = market_state.upper()
    candidates: list[str] = []
    if "PRE_MARKET" in state:
        candidates.append("pre_price")
    elif "AFTER_HOURS" in state:
        candidates.append("after_price")
    elif "NIGHT" in state or "OVERNIGHT" in state:
        candidates.append("overnight_price")
    candidates.append("last_price")
    for field in candidates:
        price = _number(row.get(field))
        if price:
            return price
    return None


def _snapshot_rows(ctx, futu, codes: dict[str, str]) -> list[dict]:
    """Snapshot rows for all codes, per-code fallback when the batch fails.

    One bad code (unsupported security type, delisted symbol) fails the
    whole batched snapshot, so on batch failure retry each code on its own
    and keep the ones that work. Per-code failures are dropped silently —
    the caller's yfinance fallback covers them.
    """
    try:
        ret, data = ctx.get_market_snapshot(list(codes))
        if ret == futu.RET_OK:
            return data.to_dict("records")
        print(f"Warning: Futu batch quotes failed ({data}); retrying per symbol",
              file=sys.stderr)
    except Exception as exc:
        print(f"Warning: Futu batch quotes failed ({exc}); retrying per symbol",
              file=sys.stderr)

    rows: list[dict] = []
    for code in codes:
        try:
            ret, data = ctx.get_market_snapshot([code])
        except Exception:
            continue
        if ret == futu.RET_OK:
            rows.extend(data.to_dict("records"))
    return rows


def fetch_quotes(symbols: list[str]) -> dict:
    """Batch live quotes from Futu snapshots.

    Returns {symbol: {"price", "change_pct", "prev_close"}} matching the
    shape of ticker.fetch_live_quotes; symbols Futu cannot serve (indexes,
    failures) are simply omitted so the caller can fall back to yfinance
    for the rest. One bad code fails a batched snapshot, so a failed batch
    is retried per code (see _snapshot_rows). The price is session-aware:
    pre-/after-hours/overnight price when the market is in that session,
    else the regular last price; change_pct is always measured against the
    previous regular-session close.
    """
    codes = {}
    for symbol in symbols:
        code = to_futu_code(symbol)
        if code is not None:
            codes[code] = symbol
    if not codes:
        return {}
    ctx = _quote_ctx()
    if ctx is None:
        return {}
    futu = _futu()
    rows = _snapshot_rows(ctx, futu, codes)

    # Market state per code drives the session-aware price pick; a failure
    # here just means every symbol uses its regular last price.
    states: dict[str, str] = {}
    try:
        ret, state_data = ctx.get_market_state(list(codes))
        if ret == futu.RET_OK:
            states = {
                str(r.get("code") or ""): str(r.get("market_state") or "")
                for r in state_data.to_dict("records")
            }
    except Exception:
        pass

    quotes = {}
    for row in rows:
        symbol = codes.get(str(row.get("code") or ""))
        if not symbol:
            continue
        price = _session_price(row, states.get(str(row.get("code") or ""), ""))
        prev_close = _number(row.get("prev_close_price"))
        if not price:
            continue
        change_pct = None
        if prev_close:
            change_pct = round((price - prev_close) / prev_close * 100, 2)
        quotes[symbol] = {
            "price": round(price, 2),
            "change_pct": change_pct,
            "prev_close": round(prev_close, 2) if prev_close else None,
        }
    return quotes


def _snapshot(ctx, code: str) -> dict:
    """Single-row market snapshot as a plain dict."""
    futu = _futu()
    ret, data = ctx.get_market_snapshot([code])
    if ret != futu.RET_OK:
        raise ValueError(f"snapshot failed ({data})")
    rows = data.to_dict("records")
    if not rows:
        raise ValueError("empty snapshot")
    return rows[0]


def _basicinfo(ctx, code: str) -> dict:
    """Single-row stock basic info as a plain dict."""
    futu = _futu()
    ret, data = ctx.get_stock_basicinfo(None, code_list=[code])
    if ret != futu.RET_OK:
        raise ValueError(f"basicinfo failed ({data})")
    rows = data.to_dict("records")
    if not rows:
        raise ValueError("empty basicinfo")
    return rows[0]


def _company_profile(ctx, code: str) -> dict[str, str]:
    """Company profile name/value rows as {name: value} (best effort)."""
    futu = _futu()
    try:
        ret, data = ctx.get_company_profile(code)
    except Exception:
        return {}
    if ret != futu.RET_OK:
        return {}
    result: dict[str, str] = {}
    for record in data.to_dict("records"):
        name = str(record.get("name") or "").strip()
        value = record.get("value")
        if name and value is not None:
            result[name] = str(value)
    return result


def _profile_field(profile: dict[str, str], *keywords: str) -> str | None:
    """First profile value whose field name contains any keyword (case-insensitive)."""
    for name, value in profile.items():
        lowered = name.lower()
        if any(keyword in lowered for keyword in keywords):
            return value
    return None


def _number(value) -> float | None:
    """float(value) with junk mapped to None (mirrors fetcher._float_or_none)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN check


_QUOTE_TYPE_MAP = {
    "STOCK": "EQUITY",
    "IDX": "INDEX",
    "ETF": "ETF",
    "BOND": "BOND",
    "WARRANT": "WARRANT",
}


def fetch_profile(ticker: str) -> dict:
    """Company profile from Futu snapshot + basic info (+ company profile).

    Returns the same keys as fundamentals/fetcher.fetch_profile. Fields
    Futu cannot serve (sector, forward_eps, dividend_rate, ...) are None;
    downstream calculations already tolerate None. financial_currency is
    None (unknown), so the ADR FX-conversion step in reporter is skipped
    for Futu-sourced rows.
    """
    code = to_futu_code(ticker)
    if code is None:
        raise ValueError(f"{ticker}: not a US symbol Futu can serve")
    ctx = _quote_ctx()
    if ctx is None:
        raise ValueError(f"{ticker}: Futu OpenD unavailable")

    try:
        snapshot = _snapshot(ctx, code)
        basic = _basicinfo(ctx, code)
    except ValueError as exc:
        raise ValueError(f"{ticker}: Futu profile fetch failed ({exc})") from exc
    company = _company_profile(ctx, code)

    stock_type = str(basic.get("stock_type") or "").upper()
    employees = _profile_field(company, "employees")
    try:
        employees = int(float(employees)) if employees is not None else None
    except (TypeError, ValueError):
        employees = None
    return {
        "ticker": ticker,
        "name": basic.get("name") or snapshot.get("name"),
        "sector": None,
        "industry": _profile_field(company, "industry"),
        "market_cap": _number(snapshot.get("total_market_val")),
        "employees": employees,
        "country": _profile_field(company, "country"),
        "business_summary": _profile_field(
            company, "description", "introduction", "profile"
        ),
        "beta": _number(snapshot.get("beta")),
        "forward_eps": None,
        "dividend_rate": None,
        "shares_outstanding": _number(snapshot.get("issued_shares")),
        "current_price": _number(snapshot.get("last_price")),
        "quote_type": _QUOTE_TYPE_MAP.get(stock_type, stock_type or None),
        "currency": "USD",
        "financial_currency": None,
    }


# Futu financial-statement display names -> stockticker row schema keys.
# Matching is case-insensitive substring; the LONGEST matching candidate
# wins, so "Total Current Assets" maps to current_assets (not total_assets)
# and "Cost of Revenue" matches nothing. Names below were verified against
# OpenD 10.9 (lang=en) for US equities; extend if a build returns others.
# _debt_long/_debt_short are summed into total_debt after the merge.
FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "revenue": ("total operating revenue", "total revenue"),
    "gross_profit": ("gross profit",),
    "operating_income": ("operating profit", "operating income"),
    "net_income": ("net income", "net profit"),
    "eps": ("diluted eps", "diluted earnings per share"),
    "total_assets": ("total assets",),
    "total_liabilities": ("total liabilities",),
    "shareholders_equity": ("total equity", "stockholders' equity"),
    "total_debt": ("total debt",),
    "_debt_long": ("long term debt and capital lease obligation", "long term debt"),
    "_debt_short": (
        "short-term debt and capital lease obligation",
        "short term debt",
        "current debt",
    ),
    "cash_and_equivalents": (
        "cash and cash equivalents & short-term investments",
        "cash and cash equivalents",
    ),
    "operating_cash_flow": (
        "operating cash flow",
        "net cash flow from continuing operations",
    ),
    "capital_expenditure": (
        "capital expenditure",
        "capex",
        "net ppe purchase and sale",
    ),
    "depreciation_amortization": (
        "depreciation & depletion & amortization",
        "depreciation and amortization",
        "depreciation & amortization",
    ),
    "interest_expense": ("interest expense",),
    "current_assets": ("total current assets",),
    "current_liabilities": ("total current liabilities",),
    "free_cash_flow": ("free cash flow",),
    "shares_outstanding": (
        "diluted weighted average shares",
        "weighted average shares",
    ),
}

FINANCIAL_TYPE_ANNUAL = 7  # Qot_Common.F10Type: 1-4 single quarters, 7 annual
_STATEMENT_TYPES = (1, 2, 3)  # 1=income, 2=balance, 3=cashflow
_MAX_PAGES = 6  # 6 pages x 50 reports covers decades of history
# Futu limits get_financials_statements to 30 calls per 30 seconds;
# without spacing, a watchlist-wide batch blows the budget and every
# later ticker silently falls back to yfinance.
_STATEMENT_MIN_INTERVAL = 1.1
_last_statement_call = 0.0


def _throttle_statement_call() -> None:
    global _last_statement_call
    wait = _STATEMENT_MIN_INTERVAL - (time.monotonic() - _last_statement_call)
    if wait > 0:
        time.sleep(wait)
    _last_statement_call = time.monotonic()

# Schema keys every returned row carries (None when Futu has no value).
_ROW_KEYS = tuple(FIELD_CANDIDATES)


def _map_item(display_name: str) -> str | None:
    """Map a Futu statement display name to a schema key, or None.

    Longest matching candidate wins so specific names beat generic ones
    ("total current assets" over "total assets").
    """
    name = display_name.strip().lower()
    if not name:
        return None
    best_key = None
    best_len = 0
    for key, candidates in FIELD_CANDIDATES.items():
        for candidate in candidates:
            if candidate in name and len(candidate) > best_len:
                best_key, best_len = key, len(candidate)
    return best_key


def _fetch_statement_reports(ctx, code: str, statement_type: int) -> list[dict]:
    """All reports for one statement type, following pagination."""
    futu = _futu()
    reports: list[dict] = []
    next_key = None
    for _ in range(_MAX_PAGES):
        _throttle_statement_call()
        ret, result = ctx.get_financials_statements(
            code,
            statement_type=statement_type,
            financial_type=10,  # single quarters + annual
            next_key=next_key,
            num=50,
        )
        if ret != futu.RET_OK:
            raise ValueError(f"statement {statement_type} query failed ({result})")
        reports.extend(result.get("report_list", []))
        next_key = result.get("next_key")
        if not next_key or next_key == "-1":
            break
    return reports


def fetch_financials(ticker: str) -> list[dict]:
    """Financial rows from Futu statements, same schema as fetcher.fetch_financials.

    Annual ('10-K') rows first, then quarterly ('10-Q'), each block newest
    first — matching the ordering fetcher.fetch_financials documents, so
    the upsert in reporter keeps TTM sums intact. Free cash flow falls
    back to OCF +/- CapEx (sign-aware) when no explicit field exists.
    """
    code = to_futu_code(ticker)
    if code is None:
        raise ValueError(f"{ticker}: not a US symbol Futu can serve")
    ctx = _quote_ctx()
    if ctx is None:
        raise ValueError(f"{ticker}: Futu OpenD unavailable")

    try:
        merged: dict[tuple[str, int], dict] = {}
        for statement_type in _STATEMENT_TYPES:
            for report in _fetch_statement_reports(ctx, code, statement_type):
                date_str = str(report.get("date_time_str") or "")[:10]
                if not date_str:
                    continue
                ftype = int(report.get("financial_type") or 0)
                row = merged.setdefault(
                    (date_str, ftype),
                    {
                        "ticker": ticker,
                        "fiscal_date": date_str,
                        "report_type": (
                            "10-K" if ftype == FINANCIAL_TYPE_ANNUAL else "10-Q"
                        ),
                    },
                )
                for item in report.get("item_list", []):
                    if "data" not in item:
                        continue
                    key = _map_item(str(item.get("display_name") or ""))
                    # First write wins: multiple Futu line items can map to
                    # the same key (e.g. Total Equity vs Stockholders'
                    # Equity); the statement's own field order decides.
                    if key is not None and key not in row:
                        row[key] = _number(item["data"])
    except ValueError as exc:
        raise ValueError(f"{ticker}: Futu financials fetch failed ({exc})") from exc

    if not merged:
        raise ValueError(f"{ticker}: no Futu financial statements available")

    rows = list(merged.values())
    for row in rows:
        debt_long = row.pop("_debt_long", None)
        debt_short = row.pop("_debt_short", None)
        for key in _ROW_KEYS:
            if not key.startswith("_"):
                row.setdefault(key, None)
        if row.get("total_debt") is None:
            parts = [p for p in (debt_long, debt_short) if p is not None]
            row["total_debt"] = sum(parts) if parts else None
        if row.get("free_cash_flow") is None:
            ocf = row.get("operating_cash_flow")
            capex = row.get("capital_expenditure")
            if ocf is not None and capex is not None:
                # CapEx sign varies by source; FCF = OCF - |CapEx|.
                row["free_cash_flow"] = ocf - abs(capex)
                row["capital_expenditure"] = -abs(capex)
    annual = sorted(
        (r for r in rows if r["report_type"] == "10-K"),
        key=lambda r: r["fiscal_date"], reverse=True,
    )
    quarterly = sorted(
        (r for r in rows if r["report_type"] == "10-Q"),
        key=lambda r: r["fiscal_date"], reverse=True,
    )
    return annual + quarterly
