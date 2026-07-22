"""Technical indicators (MACD, RSI) computed from daily close prices."""

# A series is a list of floats aligned with the input dates; entries are None
# until enough data exists for the indicator's warm-up period.

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_PERIOD = 14
RSI_BULLISH_LEVEL = 30  # crossing up through oversold
RSI_BEARISH_LEVEL = 70  # crossing down through overbought


def ema(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average, seeded with the SMA of the first `period` values."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def macd(closes: list[float]) -> dict[str, list[float | None]]:
    """MACD (12/26/9): macd line, signal line, and histogram."""
    fast = ema(closes, MACD_FAST)
    slow = ema(closes, MACD_SLOW)
    macd_line: list[float | None] = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(fast, slow)
    ]
    valid = [v for v in macd_line if v is not None]
    signal_valid = ema(valid, MACD_SIGNAL)
    signal: list[float | None] = [None] * (len(macd_line) - len(valid)) + signal_valid
    histogram: list[float | None] = [
        (m - s) if m is not None and s is not None else None
        for m, s in zip(macd_line, signal)
    ]
    return {"macd": macd_line, "signal": signal, "histogram": histogram}


def rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Wilder's RSI."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out

    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out[period] = _rsi_value(avg_gain, avg_loss)

    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def sma(values: list[float], period: int) -> list[float | None]:
    """Simple moving average."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    window_sum = sum(values[:period])
    out[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        out[i] = window_sum / period
    return out


def bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Bollinger Bands: (middle SMA, upper, lower); population std, as is customary."""
    mid = sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        mean = mid[i]
        window = closes[i - period + 1 : i + 1]
        variance = sum((c - mean) ** 2 for c in window) / period
        std = variance**0.5
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return mid, upper, lower


def detect_crossovers(closes: list[float]) -> list[tuple[str, str]]:
    """Detect bullish/bearish crossovers between the last two bars.

    Returns a list of (indicator, direction), e.g. [("MACD", "bullish")].
    - MACD: macd line crossing the signal line.
    - RSI: crossing up through 30 (bullish) or down through 70 (bearish).
    """
    signals: list[tuple[str, str]] = []

    m = macd(closes)
    macd_line, signal = m["macd"], m["signal"]
    if (
        len(closes) >= 2
        and macd_line[-1] is not None
        and macd_line[-2] is not None
        and signal[-1] is not None
        and signal[-2] is not None
    ):
        if macd_line[-2] <= signal[-2] and macd_line[-1] > signal[-1]:
            signals.append(("MACD", "bullish"))
        elif macd_line[-2] >= signal[-2] and macd_line[-1] < signal[-1]:
            signals.append(("MACD", "bearish"))

    r = rsi(closes)
    if len(closes) >= 2 and r[-1] is not None and r[-2] is not None:
        if r[-2] <= RSI_BULLISH_LEVEL and r[-1] > RSI_BULLISH_LEVEL:
            signals.append(("RSI", "bullish"))
        elif r[-2] >= RSI_BEARISH_LEVEL and r[-1] < RSI_BEARISH_LEVEL:
            signals.append(("RSI", "bearish"))

    return signals
