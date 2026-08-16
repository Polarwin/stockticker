"""Pure DCF valuation. No network, no sqlite.

Two-stage model: explicit 5-year FCF-per-share projection, then a Gordon
growth terminal value, all discounted at a CAPM-flavored rate. When the
latest balance sheet carries debt/cash, an equity bridge is applied:
enterprise value minus net debt (total debt - cash), per share.
"""

from fundamentals.calculator import latest, ttm

TERMINAL_GROWTH = 0.025
EQUITY_RISK_PREMIUM = 0.055
GROWTH_CAP = 0.25
GROWTH_FLOOR = -0.10
DEFAULT_GROWTH = 0.05
DISCOUNT_MAX = 0.20


def mos_label(upside_downside_pct: float) -> str:
    """Margin-of-safety label for an upside/downside percentage."""
    if upside_downside_pct > 30:
        return "Strong Buy"
    if upside_downside_pct > 15:
        return "Buy"
    if upside_downside_pct > 0:
        return "Fair Value"
    if upside_downside_pct > -15:
        return "Slightly Overvalued"
    return "Overvalued"


def net_debt_from_rows(fin_rows: list[dict]) -> float | None:
    """Net debt (total debt minus cash) from the newest quarterly row.

    None when the latest row carries neither total_debt nor
    cash_and_equivalents — the equity bridge is skipped rather than
    assumed zero.
    """
    row = latest(fin_rows) or {}
    total_debt = row.get("total_debt")
    cash = row.get("cash_and_equivalents")
    if total_debt is None and cash is None:
        return None
    return (total_debt or 0.0) - (cash or 0.0)


def dcf_value(
    fcf_ttm: float | None,
    shares: float | None,
    growth_5yr: float | None,
    discount_rate: float | None,
    terminal_growth: float = TERMINAL_GROWTH,
    net_debt: float | None = None,
) -> dict | None:
    """Intrinsic value per share from TTM free cash flow.

    None when fcf_ttm/shares are missing or <= 0, when growth or discount
    inputs are missing, or when discount_rate <= terminal_growth (the
    Gordon denominator would not be positive). When net_debt (aggregate,
    same currency as fcf_ttm) is given, it is subtracted per share as an
    equity bridge.
    """
    if (fcf_ttm is None or fcf_ttm <= 0 or shares is None or shares <= 0
            or growth_5yr is None or discount_rate is None
            or discount_rate <= terminal_growth):
        return None

    fcf_per_share = fcf_ttm / shares
    projected = [fcf_per_share * (1 + growth_5yr) ** year for year in range(1, 6)]
    terminal_value = (
        projected[-1] * (1 + terminal_growth) / (discount_rate - terminal_growth)
    )
    intrinsic = sum(
        fcf / (1 + discount_rate) ** year
        for year, fcf in enumerate(projected, start=1)
    )
    intrinsic += terminal_value / (1 + discount_rate) ** 5

    enterprise_per_share = intrinsic
    if net_debt is not None:
        intrinsic -= net_debt / shares

    return {
        "fcf_per_share_ttm": fcf_per_share,
        "projected_fcf_5yr": sum(projected),
        "terminal_value": terminal_value,
        "enterprise_value_per_share": enterprise_per_share,
        "intrinsic_value_per_share": intrinsic,
    }


def _growth_rate(revenue_cagr_5yr: float | None) -> float:
    """5yr FCF growth assumption from the revenue CAGR.

    Defaults to DEFAULT_GROWTH when unknown; scaled by 0.8, capped at
    GROWTH_CAP, and negative rates are kept but floored at GROWTH_FLOOR.
    """
    if revenue_cagr_5yr is None:
        return DEFAULT_GROWTH
    return max(min(revenue_cagr_5yr * 0.8, GROWTH_CAP), GROWTH_FLOOR)


def _discount_rate(
    risk_free_rate: float, beta: float | None, terminal_growth: float
) -> float:
    """CAPM discount rate clamped to (terminal_growth + 0.01, DISCOUNT_MAX]."""
    rate = risk_free_rate + (beta if beta is not None else 1.0) * EQUITY_RISK_PREMIUM
    return max(min(rate, DISCOUNT_MAX), terminal_growth + 0.01)


def compute_dcf(
    profile: dict,
    fin_rows: list[dict],
    moat_metrics: dict,
    price: float | None,
    risk_free_rate: float,
    terminal_growth: float = TERMINAL_GROWTH,
) -> dict | None:
    """Full DCF valuation; keys match dcf_valuation columns.

    None when the underlying dcf_value is not computable (no positive FCF
    or share count). upside/margin-of-safety fields are None when the
    current price is missing or <= 0.
    """
    fcf_ttm = ttm(fin_rows, "free_cash_flow")
    row = latest(fin_rows) or {}
    shares = profile.get("shares_outstanding") or row.get("shares_outstanding")

    growth = _growth_rate(moat_metrics.get("revenue_cagr_5yr"))
    discount = _discount_rate(risk_free_rate, profile.get("beta"), terminal_growth)
    net_debt = net_debt_from_rows(fin_rows)

    result = dcf_value(fcf_ttm, shares, growth, discount, terminal_growth,
                       net_debt=net_debt)
    if result is None:
        return None

    intrinsic_per_share = result["intrinsic_value_per_share"]
    upside = None
    if price is not None and price > 0:
        upside = (intrinsic_per_share - price) / price * 100

    result.update({
        "current_price": price,
        "fcf_growth_rate_5yr": growth,
        "fcf_growth_rate_terminal": terminal_growth,
        "discount_rate": discount,
        "net_debt": net_debt,
        "intrinsic_value": intrinsic_per_share * shares,
        "upside_downside_pct": upside,
        "margin_of_safety": upside,
        "mos_label": mos_label(upside) if upside is not None else None,
    })
    return result


def sensitivity_grid(
    fcf_ttm: float | None,
    shares: float | None,
    base_growth: float | None,
    base_discount: float | None,
    terminal_growth: float = TERMINAL_GROWTH,
    net_debt: float | None = None,
) -> dict | None:
    """5x5 grid of intrinsic values around the base assumptions.

    Growth offsets ±0.04 in 0.02 steps; discount offsets ±0.02 in 0.01
    steps. Cells where the discount rate <= terminal_growth (or the value
    is otherwise not computable) are None. Returns None when any base
    input is None. net_debt applies the same per-share equity bridge as
    dcf_value so the grid stays comparable to the headline value.
    """
    if (fcf_ttm is None or shares is None or shares <= 0
            or base_growth is None or base_discount is None):
        return None

    growth_rates = [base_growth + offset for offset in (-0.04, -0.02, 0.0, 0.02, 0.04)]
    discount_rates = [base_discount + offset
                      for offset in (-0.02, -0.01, 0.0, 0.01, 0.02)]
    values = []
    for growth in growth_rates:
        row = []
        for discount in discount_rates:
            result = dcf_value(fcf_ttm, shares, growth, discount,
                               terminal_growth, net_debt=net_debt)
            row.append(
                result["intrinsic_value_per_share"] if result else None
            )
        values.append(row)

    return {
        "growth_rates": growth_rates,
        "discount_rates": discount_rates,
        "values": values,
    }
