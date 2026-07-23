"""Options put/call flow from yfinance option chains.

Sums call/put volume over the nearest 1-2 expiries and compares the
totals against the trailing baseline stored in the options_volume table
(one snapshot per report run). A day is "unusual" when call or put
volume exceeds 2x its trailing average, once at least 5 baseline days
have accumulated; until then the flag is None ("collecting baseline").
"""

import sys

import yfinance as yf

MIN_BASELINE_DAYS = 5
UNUSUAL_MULTIPLE = 2.0
MAX_EXPIRIES = 2


def fetch_options_flow(symbol: str) -> dict | None:
    """Call/put volume totals over the nearest expiries.

    Returns {call_volume, put_volume, pcr, expiries}; pcr is None when
    call volume is zero. Per-symbol errors warn and return None.
    """
    try:
        ticker = yf.Ticker(symbol)
        expiries = ticker.options[:MAX_EXPIRIES]
        if not expiries:
            return None
        call_volume = 0
        put_volume = 0
        for expiry in expiries:
            chain = ticker.option_chain(expiry)
            call_volume += int(chain.calls["volume"].fillna(0).sum())
            put_volume += int(chain.puts["volume"].fillna(0).sum())
    except Exception as exc:
        print(f"Warning: {symbol}: options flow fetch failed ({exc})", file=sys.stderr)
        return None
    pcr = round(put_volume / call_volume, 3) if call_volume > 0 else None
    return {
        "call_volume": call_volume,
        "put_volume": put_volume,
        "pcr": pcr,
        "expiries": list(expiries),
    }


def is_unusual(
    flow: dict, history: list[tuple[str, int, int]]
) -> bool | None:
    """True when today's call or put volume tops 2x the trailing average.

    history rows are (date, call_volume, put_volume) from
    db.get_options_volume_history. Returns None while the baseline has
    fewer than MIN_BASELINE_DAYS snapshots.
    """
    if len(history) < MIN_BASELINE_DAYS:
        return None
    avg_calls = sum(row[1] for row in history) / len(history)
    avg_puts = sum(row[2] for row in history) / len(history)
    if avg_calls > 0 and flow["call_volume"] > UNUSUAL_MULTIPLE * avg_calls:
        return True
    if avg_puts > 0 and flow["put_volume"] > UNUSUAL_MULTIPLE * avg_puts:
        return True
    return False


def flow_label(pcr: float | None) -> str:
    """Map a put/call ratio to its flow label."""
    if pcr is None:
        return "n/a"
    if pcr < 0.7:
        return "Strong Bullish"
    if pcr < 1.0:
        return "Bullish"
    if pcr <= 1.5:
        return "Bearish"
    return "Strong Bearish"


def options_bonus(pcr: float | None) -> int:
    """Confluence-score bonus from the put/call ratio."""
    if pcr is None:
        return 0
    if pcr < 0.7:
        return 8
    if pcr < 1.0:
        return 4
    if pcr <= 1.5:
        return -4
    return -8
