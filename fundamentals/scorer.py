"""Pure composite fundamental score. No network, no sqlite."""

VALUATION_RATIO_KEYS = ("pe_ratio", "pb_ratio", "ps_ratio", "p_fcf_ratio", "ev_ebitda")


def percentile_points(p: float | None) -> int | None:
    """Points for a valuation percentile (lower valuation = more points)."""
    if p is None:
        return None
    if p < 20:
        return 6
    if p < 40:
        return 5
    if p < 60:
        return 4
    if p < 80:
        return 2
    return 0


def fundamental_score(
    valuation_percentiles: dict[str, float | None],
    moat_score_val: float | None,
    revenue_cagr_3yr: float | None,
    debt_to_equity: float | None,
    surprises: list[float | None],
) -> dict:
    """Composite 0-100 fundamental score across five legs.

    valuation (0-30): percentile_points over the 5 ratios'
    percentile_vs_history values, rescaled to 30 over the available ones.
    moat (0-25): moat_score x 0.25. growth (0-20): revenue_cagr_3yr bands.
    stability (0-15): debt_to_equity bands. earnings_quality (0-10): count
    of beats among the last 4 EPS surprises (None counts as not-beat).
    """
    points = [
        percentile_points(valuation_percentiles.get(key))
        for key in VALUATION_RATIO_KEYS
    ]
    available = [p for p in points if p is not None]
    if available:
        valuation = round(sum(available) / (6 * len(available)) * 30)
    else:
        valuation = 0

    moat = round(moat_score_val * 0.25) if moat_score_val is not None else 0

    if revenue_cagr_3yr is None or revenue_cagr_3yr <= 0.05:
        growth = 0
    elif revenue_cagr_3yr <= 0.10:
        growth = 10
    elif revenue_cagr_3yr <= 0.15:
        growth = 15
    else:
        growth = 20

    if debt_to_equity is None:
        stability = 0
    elif debt_to_equity < 0.5:
        stability = 15
    elif debt_to_equity < 1.0:
        stability = 10
    else:
        stability = 0

    beats = sum(1 for s in surprises[:4] if s is not None and s > 0)
    earnings_quality = {4: 10, 3: 7, 2: 5, 1: 2}.get(beats, 0)

    return {
        "total": valuation + moat + growth + stability + earnings_quality,
        "valuation": valuation,
        "moat": moat,
        "growth": growth,
        "stability": stability,
        "earnings_quality": earnings_quality,
    }
