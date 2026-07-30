"""Analyst consensus, price targets, and grade history for one ticker.

Futu OpenD is the primary source (get_research_analyst_consensus +
get_research_rating_summary); yfinance is the fallback
(recommendations_summary / analyst_price_targets / upgrades_downgrades).
Results are normalized to one shape and cached in
data/analyst_cache.json (6h TTL) so report-page views don't hammer the
providers. Missing pieces are None/[] — renderers must tolerate that.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import futu_source

CACHE_PATH = Path(__file__).resolve().parent / "data" / "analyst_cache.json"
CACHE_TTL_HOURS = 6

# Qot_Common.ResearchRatingType: only Sell(1)/Hold(3)/Buy(4) are returned.
_FUTU_RATING_LABELS = {1: "Sell", 3: "Hold", 4: "Buy"}


def _empty() -> dict:
    return {"consensus": None, "trend": [], "grades": []}


# ---------------------------------------------------------------------------
# Futu
# ---------------------------------------------------------------------------


def _futu_analyst_data(ticker: str) -> dict:
    """Analyst data from Futu research endpoints. Raises on failure."""
    code = futu_source.to_futu_code(ticker)
    if code is None:
        raise ValueError(f"{ticker}: not a US symbol Futu can serve")
    ctx = futu_source._quote_ctx()
    if ctx is None:
        raise ValueError(f"{ticker}: Futu OpenD unavailable")
    futu = futu_source._futu()

    data = _empty()
    ret, consensus = ctx.get_research_analyst_consensus(code)
    if ret != futu.RET_OK:
        raise ValueError(f"{ticker}: Futu consensus failed ({consensus})")
    data["consensus"] = {
        "mean_target": consensus.get("average"),
        "high_target": consensus.get("highest"),
        "low_target": consensus.get("lowest"),
        "total": consensus.get("total"),
        "buy_pct": consensus.get("buy"),
        "hold_pct": consensus.get("hold"),
        "sell_pct": consensus.get("sell"),
        "rating_label": _FUTU_RATING_LABELS.get(consensus.get("rating")),
        "date": consensus.get("update_time_str"),
    }

    ret, summary = ctx.get_research_rating_summary(code)
    if ret == futu.RET_OK and isinstance(summary, dict):
        grades = []
        for institution in summary.get("inst_rating_summary_list", []):
            info = institution.get("institution_info", {})
            firm = info.get("institution_en_name") or info.get(
                "institution_name"
            )
            for item in institution.get("rating_item_list", []):
                grades.append({
                    "date": item.get("recommendation_date_str"),
                    "firm": firm,
                    "action": None,
                    "to_grade": _FUTU_RATING_LABELS.get(item.get("rating")),
                    "from_grade": _FUTU_RATING_LABELS.get(
                        item.get("last_rating")
                    ),
                    "target": item.get("target_price"),
                    "prior_target": None,
                })
        grades.sort(key=lambda g: g.get("date") or "", reverse=True)
        data["grades"] = grades[:20]
    return data


# ---------------------------------------------------------------------------
# yfinance fallback
# ---------------------------------------------------------------------------


def _yf_analyst_data(ticker: str) -> dict:
    """Analyst data from yfinance. Raises on failure."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    data = _empty()

    targets = t.analyst_price_targets
    if isinstance(targets, dict) and targets:
        data["consensus"] = {
            "mean_target": targets.get("mean"),
            "high_target": targets.get("high"),
            "low_target": targets.get("low"),
            "total": targets.get("numberOfAnalysts"),
            "buy_pct": None,
            "hold_pct": None,
            "sell_pct": None,
            "rating_label": None,
            "date": None,
        }

    trend = t.recommendations_summary
    if trend is not None and not trend.empty:
        data["trend"] = [
            {
                "period": row.get("period"),
                "strong_buy": row.get("strongBuy"),
                "buy": row.get("buy"),
                "hold": row.get("hold"),
                "sell": row.get("sell"),
                "strong_sell": row.get("strongSell"),
            }
            for row in trend.to_dict("records")
        ]

    grades_df = t.upgrades_downgrades
    if grades_df is not None and not grades_df.empty:
        grades = []
        for row in grades_df.to_dict("records")[:20]:
            date = row.get("GradeDate") or row.get("date")
            grades.append({
                "date": str(date)[:10] if date else None,
                "firm": row.get("Firm"),
                "action": row.get("Action"),
                "to_grade": row.get("ToGrade"),
                "from_grade": row.get("FromGrade"),
                "target": row.get("currentPriceTarget"),
                "prior_target": row.get("priorPriceTarget"),
            })
        data["grades"] = grades
    return data


# ---------------------------------------------------------------------------
# Cache + public API
# ---------------------------------------------------------------------------


def _load_cache(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict, path: Path) -> None:
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(cache, ensure_ascii=False),
                             encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        print(f"Warning: analyst cache write failed ({exc})", file=sys.stderr)


def _fetch_fresh(ticker: str) -> dict:
    """Futu first, yfinance fallback, per-piece warnings."""
    data = None
    try:
        data = _futu_analyst_data(ticker)
    except Exception as exc:
        print(f"Warning: {ticker}: Futu analyst data unavailable ({exc}); "
              "using yfinance", file=sys.stderr)
    if data is not None and data["trend"]:
        return data
    # Futu has no monthly recommendation trend (or failed entirely):
    # supplement/fall back to yfinance.
    try:
        yf_data = _yf_analyst_data(ticker)
    except Exception as exc:
        print(f"Warning: {ticker}: yfinance analyst fetch failed ({exc})",
              file=sys.stderr)
        return data if data is not None else _empty()
    if data is None:
        return yf_data
    if not data["trend"]:
        data["trend"] = yf_data["trend"]
    return data


def fetch_analyst_data(
    ticker: str,
    max_age_hours: int = CACHE_TTL_HOURS,
    cache_path: Path = CACHE_PATH,
) -> dict:
    """Normalized analyst data for a ticker, cached for max_age_hours."""
    ticker = ticker.upper()
    cache = _load_cache(cache_path)
    entry = cache.get(ticker)
    if entry:
        try:
            fetched = datetime.fromisoformat(entry["fetched_at"])
            if datetime.now() - fetched < timedelta(hours=max_age_hours):
                return entry["data"]
        except (KeyError, ValueError):
            pass

    data = _fetch_fresh(ticker)
    cache[ticker] = {"fetched_at": datetime.now().isoformat(), "data": data}
    _save_cache(cache, cache_path)
    return data
