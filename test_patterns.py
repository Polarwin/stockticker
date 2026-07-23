"""Tests for patterns: candlestick detection, trend gating, volume flags.

Run with: bin/python -m pytest test_patterns.py -v
No network access required.
"""

import unittest

from patterns import detect_patterns

FLAT_BASE = (100.0, 100.5, 99.5, 100.0)  # (open, high, low, close)
LOW_BASE = (95.0, 95.5, 94.5, 95.0)


def build_series(bars, base=FLAT_BASE, base_count=20, volume=1_000_000):
    """Aligned OHLCV lists: `base_count` flat base bars plus `bars`."""
    ohlc = [base] * base_count + list(bars)
    opens = [b[0] for b in ohlc]
    highs = [b[1] for b in ohlc]
    lows = [b[2] for b in ohlc]
    closes = [b[3] for b in ohlc]
    volumes = [float(volume)] * len(ohlc)
    return opens, highs, lows, closes, volumes


def names(hits):
    return [h["name"] for h in hits]


class TestTier1Patterns(unittest.TestCase):
    def test_bullish_engulfing_after_drop(self):
        bars = [
            (103.0, 103.5, 102.8, 103.0),
            (102.0, 102.2, 100.3, 100.5),
            (100.5, 100.6, 98.3, 98.5),   # bearish bar
            (98.4, 101.2, 98.2, 101.0),   # engulfs the previous body
        ]
        hits = detect_patterns(*build_series(bars))
        self.assertIn("Bullish Engulfing", names(hits))
        hit = next(h for h in hits if h["name"] == "Bullish Engulfing")
        self.assertEqual(hit["tier"], 1)
        self.assertEqual(hit["direction"], "bullish")
        self.assertEqual(hit["status"], "Tentative")
        self.assertIn("drop", hit["trend_context"])
        self.assertNotIn("Bearish Engulfing", names(hits))

    def test_bearish_engulfing_after_rally(self):
        bars = [
            (97.0, 97.3, 96.5, 97.0),
            (98.0, 99.7, 97.8, 99.5),
            (99.5, 101.7, 99.3, 101.5),   # bullish bar
            (101.6, 101.8, 98.8, 99.0),   # engulfs the previous body
        ]
        hits = detect_patterns(*build_series(bars))
        self.assertIn("Bearish Engulfing", names(hits))
        hit = next(h for h in hits if h["name"] == "Bearish Engulfing")
        self.assertEqual(hit["direction"], "bearish")
        self.assertIn("rally", hit["trend_context"])

    def test_engulfing_gated_by_trend(self):
        # Same engulfing shape, but the 3-day slope is up: no bullish signal.
        bars = [
            (97.0, 97.3, 96.5, 97.0),
            (98.0, 99.0, 97.8, 99.0),
            (100.5, 100.6, 98.3, 98.5),   # bearish bar
            (98.4, 101.2, 98.2, 101.0),   # engulfs, but closes[i-3] < close
        ]
        hits = detect_patterns(*build_series(bars))
        self.assertNotIn("Bullish Engulfing", names(hits))

    def test_morning_star(self):
        bars = [
            (104.0, 104.3, 103.7, 104.0),
            (103.0, 103.3, 99.8, 100.0),   # big bearish
            (99.5, 99.9, 99.2, 99.6),     # small star body
            (99.8, 102.2, 99.6, 102.0),   # big bullish, above midpoint
        ]
        hits = detect_patterns(*build_series(bars))
        self.assertIn("Morning Star", names(hits))
        hit = next(h for h in hits if h["name"] == "Morning Star")
        self.assertEqual(hit["tier"], 1)
        self.assertEqual(hit["direction"], "bullish")

    def test_evening_star(self):
        bars = [
            (96.0, 96.3, 95.7, 96.0),
            (97.0, 100.2, 96.8, 100.0),   # big bullish
            (100.5, 100.8, 100.1, 100.4),  # small star body
            (100.2, 100.4, 97.8, 98.0),   # big bearish, below midpoint
        ]
        hits = detect_patterns(*build_series(bars))
        self.assertIn("Evening Star", names(hits))
        hit = next(h for h in hits if h["name"] == "Evening Star")
        self.assertEqual(hit["direction"], "bearish")


class TestTier2Patterns(unittest.TestCase):
    def test_hammer_after_drop(self):
        bars = [
            (104.0, 104.2, 103.5, 104.0),
            (102.0, 102.2, 101.5, 102.0),
            (100.0, 100.2, 99.7, 100.0),
            (99.5, 99.6, 96.4, 99.4),     # long lower shadow, tiny body
        ]
        hits = detect_patterns(*build_series(bars))
        self.assertIn("Hammer", names(hits))
        hit = next(h for h in hits if h["name"] == "Hammer")
        self.assertEqual(hit["tier"], 2)
        self.assertEqual(hit["direction"], "bullish")

    def test_hammer_gated_by_trend(self):
        # Hammer shape after a 3-day rally: not a valid reversal context.
        bars = [
            (96.0, 96.2, 95.5, 96.0),
            (98.0, 98.2, 97.5, 98.0),
            (100.0, 100.2, 99.5, 100.0),
            (100.5, 100.6, 97.4, 100.4),
        ]
        hits = detect_patterns(*build_series(bars))
        self.assertNotIn("Hammer", names(hits))

    def test_shooting_star_after_rally(self):
        bars = [
            (96.0, 96.2, 95.8, 96.0),
            (98.0, 98.2, 97.8, 98.0),
            (100.0, 100.2, 99.8, 100.0),
            (100.6, 103.6, 100.4, 100.5),  # long upper shadow, tiny body
        ]
        hits = detect_patterns(*build_series(bars))
        self.assertIn("Shooting Star", names(hits))
        hit = next(h for h in hits if h["name"] == "Shooting Star")
        self.assertEqual(hit["direction"], "bearish")

    def test_inverted_hammer_after_drop(self):
        # Low base keeps the close away from the 20-bar edges (no Doji).
        bars = [
            (104.0, 104.2, 103.5, 104.0),
            (102.0, 102.2, 101.5, 102.0),
            (100.0, 100.2, 99.7, 100.0),
            (99.4, 102.6, 99.3, 99.5),    # long upper shadow after a drop
        ]
        hits = detect_patterns(*build_series(bars, base=LOW_BASE))
        self.assertIn("Inverted Hammer", names(hits))
        hit = next(h for h in hits if h["name"] == "Inverted Hammer")
        self.assertEqual(hit["direction"], "bullish")


class TestDoji(unittest.TestCase):
    def test_doji_at_20bar_high(self):
        bars = [
            (96.0, 96.2, 95.5, 96.0),
            (98.0, 98.2, 97.5, 98.0),
            (100.0, 100.2, 99.5, 100.0),
            (100.49, 100.8, 100.2, 100.5),  # near-zero body at the range top
        ]
        hits = detect_patterns(*build_series(bars))
        self.assertIn("Doji", names(hits))
        hit = next(h for h in hits if h["name"] == "Doji")
        self.assertEqual(hit["tier"], 3)
        self.assertEqual(hit["direction"], "bearish")  # at resistance

    def test_doji_mid_range_not_flagged(self):
        # Wide 20-bar range: the doji close sits mid-range, no edge signal.
        bars = [(100.49, 100.8, 100.2, 100.5)]
        base = (100.0, 102.0, 98.0, 100.0)
        hits = detect_patterns(*build_series(bars, base=base))
        self.assertEqual(hits, [])


class TestVolumeConfirmation(unittest.TestCase):
    HAMMER_BARS = [
        (104.0, 104.2, 103.5, 104.0),
        (102.0, 102.2, 101.5, 102.0),
        (100.0, 100.2, 99.7, 100.0),
        (99.5, 99.6, 96.4, 99.4),
    ]

    def test_volume_confirmed_on_high_volume(self):
        series = build_series(self.HAMMER_BARS)
        series[4][-1] = 3_000_000.0  # pattern bar volume >> SMA20
        hit = next(
            h for h in detect_patterns(*series) if h["name"] == "Hammer"
        )
        self.assertTrue(hit["volume_confirmed"])

    def test_volume_not_confirmed_on_low_volume(self):
        series = build_series(self.HAMMER_BARS)
        series[4][-1] = 500_000.0  # pattern bar volume < SMA20
        hit = next(
            h for h in detect_patterns(*series) if h["name"] == "Hammer"
        )
        self.assertFalse(hit["volume_confirmed"])

    def test_volume_unknown_with_short_history(self):
        # Fewer than 20 bars: no SMA20, volume_confirmed is None.
        bars = [
            (103.0, 103.2, 102.5, 103.0),
            (102.0, 102.2, 101.5, 102.0),
            (100.0, 100.2, 99.7, 100.0),
            (99.5, 99.6, 96.4, 99.4),
        ]
        hits = detect_patterns(*build_series(bars, base_count=3))
        hit = next(h for h in hits if h["name"] == "Hammer")
        self.assertIsNone(hit["volume_confirmed"])


class TestMetadata(unittest.TestCase):
    def test_dates_label_hits(self):
        bars = [
            (104.0, 104.2, 103.5, 104.0),
            (102.0, 102.2, 101.5, 102.0),
            (100.0, 100.2, 99.7, 100.0),
            (99.5, 99.6, 96.4, 99.4),
        ]
        series = build_series(bars)
        dates = [f"2026-07-{d:02d}" for d in range(1, len(series[0]) + 1)]
        hits = detect_patterns(*series, dates=dates)
        hit = next(h for h in hits if h["name"] == "Hammer")
        self.assertEqual(hit["date"], dates[-1])

    def test_lookback_limits_scan(self):
        # A hammer 4 bars back is outside the default 3-bar lookback.
        bars = [
            (104.0, 104.2, 103.5, 104.0),
            (102.0, 102.2, 101.5, 102.0),
            (99.5, 99.6, 96.4, 99.4),      # hammer shape
            (100.0, 100.2, 99.7, 100.0),
            (100.0, 100.2, 99.7, 100.0),
            (100.0, 100.2, 99.7, 100.0),
        ]
        hits = detect_patterns(*build_series(bars))
        self.assertNotIn("Hammer", names(hits))


if __name__ == "__main__":
    unittest.main()
