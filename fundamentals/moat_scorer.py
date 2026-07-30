"""Pure moat (competitive advantage) metrics and scoring. No network, no sqlite.

CAGRs and multi-year averages use annual ('10-K') rows; current
profitability/return metrics use TTM flows over the latest balance sheet.
"""

from fundamentals.calculator import dedupe_quarters, latest, ttm

# Assumed corporate tax rate for the NOPAT approximation in ROIC.
TAX_RATE = 0.21


def cagr(end: float | None, start: float | None, years: int) -> float | None:
    """Compound annual growth rate; None when inputs are missing or <= 0."""
    if end is None or start is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def _annual_rows(fin_rows: list[dict], key: str) -> list[dict]:
    """Annual rows having `key`, newest first."""
    rows = [r for r in fin_rows
            if r.get("report_type") == "10-K" and r.get(key) is not None]
    return sorted(rows, key=lambda r: r.get("fiscal_date") or "", reverse=True)


def _horizon_cagr(fin_rows: list[dict], key: str, years: int) -> float | None:
    """CAGR for `key` over the gap closest to `years`, minimum 2 year-gaps.

    With N annual rows available the gap used is min(years, N-1) — e.g. a
    5yr request with only 3 annual rows computes over 2 gaps. Fewer than
    3 annual rows (2 gaps) yields None.
    """
    rows = _annual_rows(fin_rows, key)
    gaps = len(rows) - 1
    if gaps < 2:
        return None
    gap = min(years, gaps)
    return cagr(rows[0].get(key), rows[gap].get(key), gap)


def _margin_avg(fin_rows: list[dict], numerator: str, years: int = 5) -> float | None:
    """Average annual numerator/revenue margin over up to `years` annual rows."""
    rows = _annual_rows(fin_rows, "revenue")[:years]
    margins = []
    for row in rows:
        value = row.get(numerator)
        revenue = row.get("revenue")
        if value is not None and revenue:
            margins.append(value / revenue)
    if not margins:
        return None
    return sum(margins) / len(margins)


def _divide(numerator: float | None, denominator: float | None) -> float | None:
    """Plain division, None when either side is missing or denominator is 0."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def compute_moat_metrics(fin_rows: list[dict]) -> dict:
    """Compute moat metrics; keys match moat_metrics columns (minus score cache).

    Flow metrics (margins, returns, coverage) are TTM-based; CAGRs and the
    5yr margin averages come from annual rows. Missing debt/cash are
    treated as 0 in invested capital; a None interest_coverage means "no
    measurable debt burden" and should be treated as such downstream.
    """
    fin_rows = dedupe_quarters(fin_rows)
    row = latest(fin_rows) or {}
    revenue = ttm(fin_rows, "revenue")
    gross_profit = ttm(fin_rows, "gross_profit")
    operating_income = ttm(fin_rows, "operating_income")
    net_income = ttm(fin_rows, "net_income")
    interest_expense = ttm(fin_rows, "interest_expense")

    equity = row.get("shareholders_equity")
    debt = row.get("total_debt")
    cash = row.get("cash_and_equivalents")

    roic = None
    if operating_income is not None and equity is not None:
        invested_capital = equity + (debt or 0.0) - (cash or 0.0)
        if invested_capital > 0:
            nopat = operating_income * (1 - TAX_RATE)
            roic = nopat / invested_capital

    debt_to_equity = None
    if debt is not None and equity:
        debt_to_equity = debt / equity

    return {
        "fiscal_date": row.get("fiscal_date"),
        "gross_margin": _divide(gross_profit, revenue),
        "operating_margin": _divide(operating_income, revenue),
        "net_margin": _divide(net_income, revenue),
        "roe": _divide(net_income, equity),
        "roic": roic,
        "roa": _divide(net_income, row.get("total_assets")),
        "gross_margin_5yr_avg": _margin_avg(fin_rows, "gross_profit"),
        "operating_margin_5yr_avg": _margin_avg(fin_rows, "operating_income"),
        "revenue_cagr_3yr": _horizon_cagr(fin_rows, "revenue", 3),
        "revenue_cagr_5yr": _horizon_cagr(fin_rows, "revenue", 5),
        "eps_cagr_3yr": _horizon_cagr(fin_rows, "eps", 3),
        "eps_cagr_5yr": _horizon_cagr(fin_rows, "eps", 5),
        "fcf_cagr_3yr": _horizon_cagr(fin_rows, "free_cash_flow", 3),
        "debt_to_equity": debt_to_equity,
        "interest_coverage": _divide(operating_income, interest_expense),
        "current_ratio": _divide(row.get("current_assets"),
                                 row.get("current_liabilities")),
    }


def _band(value: float, bands: list[tuple[float, int]], default: int) -> int:
    """First points whose threshold `value` exceeds, else default."""
    for threshold, points in bands:
        if value > threshold:
            return points
    return default


def moat_score(metrics: dict) -> tuple[int, str, dict]:
    """Score a moat_metrics dict: (score 0-100, rating, breakdown).

    Components: Pricing Power (gross_margin, max 25), Capital Efficiency
    (roic, 25), Profitability (roe, 20), Growth Consistency
    (revenue_cagr_5yr, 15), Financial Strength (debt_to_equity, 15).
    When a component's input is None it is excluded and the earned points
    are rescaled: score = round(earned / max_possible_of_available * 100).
    Negative debt_to_equity (negative equity) scores 0, not the top band.
    """
    components = {}  # name -> (points or None, max_points)

    gross_margin = metrics.get("gross_margin")
    components["pricing_power"] = (
        None if gross_margin is None else _band(
            gross_margin, [(0.50, 25), (0.40, 20), (0.30, 15), (0.20, 10)], 5),
        25,
    )

    roic = metrics.get("roic")
    components["capital_efficiency"] = (
        None if roic is None else _band(
            roic, [(0.20, 25), (0.15, 20), (0.10, 15), (0.05, 10)], 0),
        25,
    )

    roe = metrics.get("roe")
    components["profitability"] = (
        None if roe is None else _band(
            roe, [(0.20, 20), (0.15, 15), (0.10, 10), (0.05, 5)], 0),
        20,
    )

    revenue_cagr = metrics.get("revenue_cagr_5yr")
    components["growth_consistency"] = (
        None if revenue_cagr is None else _band(
            revenue_cagr, [(0.15, 15), (0.10, 12), (0.05, 8), (0.0, 4)], 0),
        15,
    )

    debt_to_equity = metrics.get("debt_to_equity")
    if debt_to_equity is None:
        strength = None
    elif debt_to_equity < 0:  # negative equity
        strength = 0
    else:
        for threshold, points in ((0.3, 15), (0.6, 12), (1.0, 8), (1.5, 4)):
            if debt_to_equity < threshold:
                strength = points
                break
        else:
            strength = 0
    components["financial_strength"] = (strength, 15)

    earned = sum(p for p, _m in components.values() if p is not None)
    possible = sum(m for p, m in components.values() if p is not None)
    score = round(earned / possible * 100) if possible else 0

    if score >= 80:
        rating = "Wide Moat"
    elif score >= 60:
        rating = "Narrow Moat"
    elif score >= 40:
        rating = "Weak Moat"
    else:
        rating = "No Moat"

    breakdown = {name: points for name, (points, _m) in components.items()}
    return score, rating, breakdown
