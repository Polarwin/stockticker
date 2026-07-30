"""Pure valuation-ratio calculations. No network, no sqlite.

Rows are quarterly_financials dicts (see fundamentals.database). Unless
noted, functions sort by fiscal_date themselves, so input order is free.
"""


def _sorted(rows: list[dict]) -> list[dict]:
    """Rows sorted newest fiscal_date first (None dates sort last)."""
    return sorted(rows, key=lambda r: r.get("fiscal_date") or "", reverse=True)


def dedupe_quarters(rows: list[dict]) -> list[dict]:
    """One row per (report_type, fiscal year-month), newest date winning.

    Sources disagree on fiscal period-end dates by a few days (Futu
    2026-03-27 vs yfinance 2026-03-31 for the same quarter), so rows from
    mixed sources would otherwise be summed twice by ttm(). Rows without
    a fiscal_date are kept as-is.
    """
    seen: set[tuple[str, str]] = set()
    result = []
    for row in _sorted(rows):
        day = row.get("fiscal_date") or ""
        key = (str(row.get("report_type") or ""), day[:7])
        if day and key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def ttm(rows: list[dict], key: str) -> float | None:
    """Trailing-twelve-month sum of `key`.

    Sums the newest 4 quarterly ('10-Q') values that have `key`; when only
    1-3 such quarters exist, whatever exists is summed (documented choice:
    a partial sum beats no number for young coverage). When no quarterly
    row has the key, falls back to the newest annual ('10-K') value.
    Duplicate quarter-ends from mixed sources (see dedupe_quarters) are
    counted once.
    """
    quarterly = [r for r in dedupe_quarters(rows)
                 if r.get("report_type") == "10-Q" and r.get(key) is not None]
    if quarterly:
        return sum(r[key] for r in quarterly[:4])
    annual = [r for r in dedupe_quarters(rows)
              if r.get("report_type") == "10-K" and r.get(key) is not None]
    if annual:
        return annual[0][key]
    return None


def latest(rows: list[dict]) -> dict | None:
    """Newest row by fiscal_date, or None for an empty list."""
    ordered = _sorted(rows)
    return ordered[0] if ordered else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """numerator / denominator, None unless both exist and denominator > 0."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def compute_valuation_ratios(
    profile: dict,
    fin_rows: list[dict],
    price: float | None,
    eps_cagr_5yr: float | None = None,
) -> dict:
    """Compute valuation ratios; keys match valuation_ratios columns.

    market_cap comes from the profile, else price x shares from the latest
    financial row; when neither is available all market-cap-derived ratios
    are None. Missing debt/cash are treated as 0 in the EV bridge.
    """
    row = latest(fin_rows) or {}
    shares = profile.get("shares_outstanding") or row.get("shares_outstanding")

    market_cap = profile.get("market_cap")
    if market_cap is None and price is not None and shares:
        market_cap = price * shares

    net_income = ttm(fin_rows, "net_income")
    revenue = ttm(fin_rows, "revenue")
    fcf = ttm(fin_rows, "free_cash_flow")
    operating_income = ttm(fin_rows, "operating_income")
    depreciation = ttm(fin_rows, "depreciation_amortization")

    pe_ratio = _ratio(market_cap, net_income)

    forward_eps = profile.get("forward_eps")
    forward_pe = None
    if market_cap is not None and forward_eps and forward_eps > 0 and shares:
        forward_pe = market_cap / (forward_eps * shares)

    # Balance-sheet fields come from the newest row that actually has a
    # value: the latest quarter's balance data sometimes lags the income
    # statement (empty balance report), which would blank P/B and skew EV.
    def latest_with(key):
        return next(
            (r.get(key) for r in _sorted(fin_rows) if r.get(key) is not None),
            None,
        )

    pb_ratio = _ratio(market_cap, latest_with("shareholders_equity"))
    ps_ratio = _ratio(market_cap, revenue)
    p_fcf_ratio = _ratio(market_cap, fcf)

    ebitda = None
    if operating_income is not None:
        ebitda = operating_income + (depreciation or 0.0)
    ev = None
    if market_cap is not None:
        ev = market_cap + (latest_with("total_debt") or 0.0) - (
            latest_with("cash_and_equivalents") or 0.0)
    ev_ebitda = _ratio(ev, ebitda)

    peg_ratio = None
    if pe_ratio is not None and eps_cagr_5yr and eps_cagr_5yr > 0:
        peg_ratio = pe_ratio / (eps_cagr_5yr * 100)

    dividend_rate = profile.get("dividend_rate")
    dividend_yield = None
    if dividend_rate is not None:
        if market_cap and shares:
            dividend_yield = dividend_rate * shares / market_cap
        elif price:
            dividend_yield = dividend_rate / price

    return {
        "pe_ratio": pe_ratio,
        "forward_pe": forward_pe,
        "pb_ratio": pb_ratio,
        "ps_ratio": ps_ratio,
        "p_fcf_ratio": p_fcf_ratio,
        "ev_ebitda": ev_ebitda,
        "peg_ratio": peg_ratio,
        "dividend_yield": dividend_yield,
        "fiscal_date": row.get("fiscal_date"),
    }
