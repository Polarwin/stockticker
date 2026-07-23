"""Candlestick reversal-pattern detection over daily OHLCV bars.

Pure functions over aligned price/volume lists (no I/O). Scans the most
recent bars for reversal patterns in three reliability tiers:

- Tier 1: Bullish/Bearish Engulfing, Morning Star, Evening Star.
- Tier 2: Hammer, Shooting Star, Inverted Hammer (long shadow vs body,
  in the correct trend context).
- Tier 3: Doji, only when it forms within ~1% of the 20-bar high/low
  (a support/resistance proxy).

Trend context comes from indicators.ema (9 vs 21) plus a 3-day slope, so
bullish reversals are only flagged after a drop and bearish reversals
after a rally. Reliability figures are historical win-rate estimates,
same convention as generate_indicators_table.
"""

from indicators import ema, sma

# Historical win-rate estimates per pattern (marked as estimates wherever
# they are displayed).
RELIABILITY = {
    "Bullish Engulfing": 0.63,
    "Bearish Engulfing": 0.63,
    "Morning Star": 0.66,
    "Evening Star": 0.66,
    "Hammer": 0.60,
    "Inverted Hammer": 0.59,
    "Shooting Star": 0.59,
    "Doji": 0.52,
}

TIER = {
    "Bullish Engulfing": 1,
    "Bearish Engulfing": 1,
    "Morning Star": 1,
    "Evening Star": 1,
    "Hammer": 2,
    "Inverted Hammer": 2,
    "Shooting Star": 2,
    "Doji": 3,
}

DOJI_BODY_PCT = 0.05  # body < 5% of bar range
DOJI_EDGE_PCT = 0.01  # within ~1% of the 20-bar high/low
SLOPE_DAYS = 3  # "after N-day drop/rally" lookback


def body(open_: float, close: float) -> float:
    """Candle body size (always >= 0)."""
    return abs(close - open_)


def bar_range(high: float, low: float) -> float:
    """Total bar range (high - low)."""
    return high - low


def upper_shadow(open_: float, high: float, close: float) -> float:
    """Wick above the body."""
    return high - max(open_, close)


def lower_shadow(open_: float, low: float, close: float) -> float:
    """Wick below the body."""
    return min(open_, close) - low


def trend_context(closes: list[float], i: int) -> dict:
    """Trend state just before/at bar i.

    {"trend": "up"|"down"|None} from EMA9 vs EMA21, and
    {"slope": "drop"|"rally"|"flat"} from the close SLOPE_DAYS back.
    """
    ema9 = ema(closes[: i + 1], 9)[-1]
    ema21 = ema(closes[: i + 1], 21)[-1]
    trend = None
    if ema9 is not None and ema21 is not None:
        trend = "up" if ema9 > ema21 else "down"

    slope = "flat"
    if i >= SLOPE_DAYS:
        change = closes[i] - closes[i - SLOPE_DAYS]
        if change < 0:
            slope = "drop"
        elif change > 0:
            slope = "rally"
    return {"trend": trend, "slope": slope}


def _describe_context(ctx: dict) -> str:
    """Human-readable trend context, e.g. 'downtrend, after 3-day drop'."""
    parts = []
    if ctx["trend"]:
        parts.append(f"{ctx['trend']}trend")
    parts.append(f"after {SLOPE_DAYS}-day {ctx['slope']}")
    return ", ".join(parts)


def _volume_confirmed(volumes: list[float], i: int) -> bool | None:
    """True when bar i's volume beats its SMA20; None without enough data."""
    vol_sma = sma(volumes[: i + 1], 20)[-1]
    if vol_sma is None or vol_sma <= 0:
        return None
    return volumes[i] > vol_sma


def _hit(
    name: str,
    date: str | None,
    direction: str,
    ctx: dict,
    volume_confirmed: bool | None,
) -> dict:
    """Assemble one pattern-hit dict; status starts 'Tentative'."""
    return {
        "name": name,
        "tier": TIER[name],
        "date": date,
        "direction": direction,
        "reliability": RELIABILITY[name],
        "trend_context": _describe_context(ctx),
        "volume_confirmed": volume_confirmed,
        "status": "Tentative",
    }


def detect_patterns(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    lookback: int = 3,
    dates: list[str] | None = None,
) -> list[dict]:
    """Scan the last `lookback` bars for reversal patterns.

    All series are aligned oldest-first. `dates`, when given, labels each
    hit with its bar date. Bullish reversals require a 3-day drop context,
    bearish reversals a 3-day rally; Tier-3 Dojis additionally require the
    close to sit within ~1% of the trailing 20-bar high or low.
    """
    n = len(closes)
    hits: list[dict] = []
    for i in range(max(0, n - lookback), n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        rng = bar_range(h, l)
        if rng <= 0:
            continue
        date = dates[i] if dates and i < len(dates) else None
        ctx = trend_context(closes, i)
        vol_confirmed = _volume_confirmed(volumes, i)
        after_drop = ctx["slope"] == "drop"
        after_rally = ctx["slope"] == "rally"

        b = body(o, c)
        up_shadow = upper_shadow(o, h, c)
        low_shadow = lower_shadow(o, l, c)

        # --- Tier 1: two-bar engulfing (needs a previous bar) ---
        if i >= 1:
            po, pc = opens[i - 1], closes[i - 1]
            prev_bearish = pc < po
            prev_bullish = pc > po
            cur_bullish = c > o
            cur_bearish = c < o
            if (
                after_drop
                and prev_bearish
                and cur_bullish
                and o <= pc
                and c >= po
            ):
                hits.append(
                    _hit("Bullish Engulfing", date, "bullish", ctx, vol_confirmed)
                )
            elif (
                after_rally
                and prev_bullish
                and cur_bearish
                and o >= pc
                and c <= po
            ):
                hits.append(
                    _hit("Bearish Engulfing", date, "bearish", ctx, vol_confirmed)
                )

        # --- Tier 1: three-bar morning/evening star ---
        if i >= 2:
            o1, c1 = opens[i - 2], closes[i - 2]
            o2, c2 = opens[i - 1], closes[i - 1]
            b1 = body(o1, c1)
            b2 = body(o2, c2)
            big_first = b1 > 0.5 * bar_range(highs[i - 2], lows[i - 2])
            small_mid = b2 <= 0.3 * b1 if b1 > 0 else False
            big_last = b > 0.5 * rng
            midpoint1 = (o1 + c1) / 2
            if after_drop and big_first and small_mid and big_last:
                if c1 < o1 and c > o and c > midpoint1:
                    hits.append(
                        _hit("Morning Star", date, "bullish", ctx, vol_confirmed)
                    )
            if after_rally and big_first and small_mid and big_last:
                if c1 > o1 and c < o and c < midpoint1:
                    hits.append(
                        _hit("Evening Star", date, "bearish", ctx, vol_confirmed)
                    )

        # --- Tier 2: long-shadow single bars (shadow >= 2x body) ---
        # eps absorbs float noise when a shadow and the body are equal.
        eps = 1e-9
        min_body = max(b, 0.05 * rng)  # treat near-zero bodies as small, not zero
        if after_drop and low_shadow >= 2 * min_body and up_shadow <= b + eps:
            hits.append(_hit("Hammer", date, "bullish", ctx, vol_confirmed))
        elif after_drop and up_shadow >= 2 * min_body and low_shadow <= b + eps:
            hits.append(
                _hit("Inverted Hammer", date, "bullish", ctx, vol_confirmed)
            )
        elif after_rally and up_shadow >= 2 * min_body and low_shadow <= b + eps:
            hits.append(
                _hit("Shooting Star", date, "bearish", ctx, vol_confirmed)
            )

        # --- Tier 3: doji at the 20-bar edge ---
        if b < DOJI_BODY_PCT * rng and i >= 19:
            window_high = max(highs[i - 19 : i + 1])
            window_low = min(lows[i - 19 : i + 1])
            if c >= window_high * (1 - DOJI_EDGE_PCT):
                hits.append(
                    _hit("Doji", date, "bearish", ctx, vol_confirmed)
                )
            elif c <= window_low * (1 + DOJI_EDGE_PCT):
                hits.append(
                    _hit("Doji", date, "bullish", ctx, vol_confirmed)
                )

    return hits
