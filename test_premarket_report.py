"""Tests for premarket_report: scoring, bonuses, Telegram formatting.

Run with: bin/python -m pytest test_premarket_report.py -v
No network access required.
"""

import unittest

from options_flow import flow_label, is_unusual, options_bonus
from premarket_report import (
    TELEGRAM_LIMIT,
    _elide,
    apply_bonuses,
    build_actions,
    format_telegram,
    options_indicator_row,
    pattern_bonus,
    pattern_indicator_row,
    select_symbols,
    update_pattern_status,
)
from sentiment import sentiment_bonus, sentiment_label


def make_pattern(name="Hammer", tier=2, direction="bullish", status="Tentative"):
    return {
        "name": name,
        "tier": tier,
        "date": "2026-07-22",
        "direction": direction,
        "reliability": 0.60,
        "trend_context": "downtrend, after 3-day drop",
        "volume_confirmed": True,
        "status": status,
    }


def make_holding(symbol, score=50.0, pct=0.1, patterns=None, pcr=1.0):
    return {
        "symbol": symbol,
        "price": 100.0,
        "pct": pct,
        "prev_close": 99.9,
        "earnings": None,
        "sentiment": {"count": 3, "score": 0.1, "label": "Neutral",
                      "headlines": [("Src", f"{symbol} headline")]},
        "options": {"call_volume": 1000, "put_volume": int(1000 * pcr),
                    "pcr": pcr, "expiries": ["2026-07-24"],
                    "unusual": False, "label": flow_label(pcr),
                    "baseline_days": 5},
        "patterns": patterns if patterns is not None else [],
        "score": {"base": score, "pattern": 0, "sentiment": 0, "options": 0,
                  "final": score, "label": "Neutral"},
        "indicator_rows": [],
    }


def make_data(holdings, ticker=None):
    data = {
        "generated_at": "Thu Jul 23, 08:45 EDT",
        "ticker": ticker,
        "overview": {
            "quotes": {
                "ES=F": (6000.0, 0.3, 5982.0),
                "NQ=F": (22000.0, 0.5, 21890.0),
                "^VIX": (15.2, 1.2, 15.0),
                "^TNX": (42.5, -0.3, 42.6),
            },
            "headlines": [("Reuters", "Markets rise overnight")],
        },
        "holdings": holdings,
        "sources": {"news": "yfinance", "timing": "n/a"},
    }
    data["actions"] = build_actions(data)
    return data


class TestSentimentThresholds(unittest.TestCase):
    def test_label_boundaries(self):
        self.assertEqual(sentiment_label(0.5), "Bullish")
        self.assertEqual(sentiment_label(0.49), "Leaning Bullish")
        self.assertEqual(sentiment_label(0.15), "Leaning Bullish")
        self.assertEqual(sentiment_label(0.14), "Neutral")
        self.assertEqual(sentiment_label(-0.15), "Neutral")
        self.assertEqual(sentiment_label(-0.16), "Leaning Bearish")
        self.assertEqual(sentiment_label(-0.5), "Leaning Bearish")
        self.assertEqual(sentiment_label(-0.51), "Bearish")
        self.assertEqual(sentiment_label(None), "n/a")

    def test_bonus_boundaries(self):
        self.assertEqual(sentiment_bonus(0.5), 5)
        self.assertEqual(sentiment_bonus(0.49), 0)
        self.assertEqual(sentiment_bonus(-0.5), -5)
        self.assertEqual(sentiment_bonus(-0.49), 0)
        self.assertEqual(sentiment_bonus(None), 0)


class TestOptionsThresholds(unittest.TestCase):
    def test_label_boundaries(self):
        self.assertEqual(flow_label(0.69), "Strong Bullish")
        self.assertEqual(flow_label(0.7), "Bullish")
        self.assertEqual(flow_label(0.99), "Bullish")
        self.assertEqual(flow_label(1.0), "Bearish")
        self.assertEqual(flow_label(1.5), "Bearish")
        self.assertEqual(flow_label(1.51), "Strong Bearish")
        self.assertEqual(flow_label(None), "n/a")

    def test_bonus_boundaries(self):
        self.assertEqual(options_bonus(0.69), 8)
        self.assertEqual(options_bonus(0.7), 4)
        self.assertEqual(options_bonus(0.99), 4)
        self.assertEqual(options_bonus(1.0), -4)
        self.assertEqual(options_bonus(1.5), -4)
        self.assertEqual(options_bonus(1.51), -8)
        self.assertEqual(options_bonus(None), 0)

    def test_unusual_needs_baseline(self):
        flow = {"call_volume": 30_000, "put_volume": 1000}
        short = [("2026-07-2%d" % d, 10_000, 1000) for d in range(4)]
        self.assertIsNone(is_unusual(flow, short))  # < 5 baseline days
        full = [("2026-07-1%d" % d, 10_000, 1000) for d in range(5)]
        self.assertTrue(is_unusual(flow, full))  # 3x average call volume
        normal = {"call_volume": 10_000, "put_volume": 1000}
        self.assertFalse(is_unusual(normal, full))


class TestConfluenceScore(unittest.TestCase):
    def test_clamps_high_with_stacked_bonuses(self):
        patterns = [make_pattern("Morning Star", tier=1, direction="bullish")]
        result = apply_bonuses(95.0, patterns, 0.6, 0.5)
        # 95 + 20 (Tier 1) + 5 (sentiment) + 8 (PCR) = 128 -> clamped.
        self.assertEqual(result["final"], 100.0)
        self.assertEqual(result["label"], "Strong Bullish")
        self.assertEqual(result["pattern"], 20)
        self.assertEqual(result["sentiment"], 5)
        self.assertEqual(result["options"], 8)

    def test_clamps_low_with_stacked_bonuses(self):
        patterns = [make_pattern("Evening Star", tier=1, direction="bearish")]
        result = apply_bonuses(5.0, patterns, -0.6, 2.0)
        # 5 - 20 - 5 - 8 = -28 -> clamped.
        self.assertEqual(result["final"], 0.0)
        self.assertEqual(result["label"], "Strong Bearish")

    def test_pattern_bonus_ignores_expired(self):
        expired = make_pattern("Morning Star", tier=1, status="Expired")
        tentative = make_pattern("Hammer", tier=2)
        self.assertEqual(pattern_bonus([expired]), 0)
        self.assertEqual(pattern_bonus([expired, tentative]), 15)

    def test_pattern_bonus_prefers_tier1(self):
        tier2 = make_pattern("Hammer", tier=2)
        tier1 = make_pattern("Bullish Engulfing", tier=1)
        self.assertEqual(pattern_bonus([tier2, tier1]), 20)

    def test_update_pattern_status(self):
        patterns = [make_pattern(direction="bullish")]
        update_pattern_status(patterns, 1.2)
        self.assertEqual(patterns[0]["status"], "Confirmed")
        update_pattern_status(patterns, -0.8)
        self.assertEqual(patterns[0]["status"], "Expired")
        patterns = [make_pattern(direction="bullish")]
        update_pattern_status(patterns, None)
        self.assertEqual(patterns[0]["status"], "Tentative")


class TestPanelRows(unittest.TestCase):
    """The Candlestick Pattern / Options Flow rows in /api/indicators."""

    def test_pattern_row_bullish_tier1(self):
        row = pattern_indicator_row([make_pattern("Morning Star", tier=1)])
        self.assertEqual(row["name"], "Candlestick Pattern")
        self.assertEqual(row["value"], "Morning Star (Tentative)")
        self.assertEqual(row["signal"], "bullish")
        self.assertEqual(row["reliability"], 0.75)
        self.assertEqual(row["weight"], 20)
        self.assertEqual(row["points"], 20.0)

    def test_pattern_row_bearish_tier2(self):
        row = pattern_indicator_row(
            [make_pattern("Shooting Star", tier=2, direction="bearish")]
        )
        self.assertEqual(row["value"], "Shooting Star (Tentative)")
        self.assertEqual(row["signal"], "bearish")
        self.assertEqual(row["reliability"], 0.50)
        self.assertEqual(row["points"], -15.0)

    def test_pattern_row_neutral_when_none_or_expired(self):
        for patterns in ([], [make_pattern(status="Expired")]):
            row = pattern_indicator_row(patterns)
            self.assertEqual(row["value"], "none in last 3 bars")
            self.assertEqual(row["signal"], "neutral")
            self.assertEqual(row["reliability"], 0.50)
            self.assertEqual(row["weight"], 20)
            self.assertEqual(row["points"], 0.0)

    def test_options_row(self):
        row = options_indicator_row({"pcr": 2.14})
        self.assertEqual(row["name"], "Options Flow (PCR)")
        self.assertEqual(row["value"], "PCR 2.14 — Strong Bearish")
        self.assertEqual(row["signal"], "bearish")
        self.assertEqual(row["reliability"], 0.55)
        self.assertEqual(row["weight"], 8)
        self.assertEqual(row["points"], -8.0)

        row = options_indicator_row({"pcr": 0.5})
        self.assertEqual(row["signal"], "bullish")
        self.assertEqual(row["points"], 8.0)

    def test_options_row_neutral_on_failure_or_no_data(self):
        for flow in (None, {"pcr": None}):
            row = options_indicator_row(flow)
            self.assertEqual(row["value"], "n/a")
            self.assertEqual(row["signal"], "neutral")
            self.assertEqual(row["points"], 0.0)

    def test_panel_score_stacking(self):
        # The panel path: apply_bonuses(base, patterns, None, pcr) — no
        # sentiment input, so the sentiment leg must be zero.
        self.assertEqual(sentiment_bonus(None), 0)
        patterns = [make_pattern("Hammer", tier=2)]
        result = apply_bonuses(60.0, patterns, None, 0.5)
        self.assertEqual(result["sentiment"], 0)
        self.assertEqual(result["final"], 60.0 + 15 + 8)
        self.assertEqual(result["label"], "Strong Bullish")


class TestTelegramFormat(unittest.TestCase):
    def test_under_limit_with_ten_holdings(self):
        holdings = [
            make_holding(f"S{i:02d}", score=90 - i * 8, pct=2.0 - i * 0.5,
                         patterns=[make_pattern()])
            for i in range(10)
        ]
        message = format_telegram(make_data(holdings))
        self.assertLess(len(message), TELEGRAM_LIMIT)
        self.assertIn("Pre-Market Report", message)
        self.assertIn("Action items", message)

    def test_elide_hard_cap(self):
        message = "\n".join(f"line {i} " + "x" * 100 for i in range(100))
        elided = _elide(message, TELEGRAM_LIMIT)
        self.assertLessEqual(len(elided), TELEGRAM_LIMIT)
        self.assertTrue(elided.endswith("full report at /premarket)"))

    def test_deep_dive_format(self):
        holding = make_holding("AAPL", score=72.5, patterns=[make_pattern()])
        holding["indicator_rows"] = [
            {"name": "RSI (14)", "value": "58.2", "signal": "bullish",
             "reliability": 0.79, "weight": 30, "points": 23.7},
        ]
        holding["earnings"] = {
            "date": "2026-07-30", "when": None, "eps_estimate": 1.42,
            "revenue_estimate": 8.9e10, "timing": "AMC", "surprises": "Beat 3/4",
        }
        message = format_telegram(make_data([holding], ticker="AAPL"))
        self.assertLess(len(message), TELEGRAM_LIMIT)
        self.assertIn("AAPL", message)
        self.assertIn("Indicators:", message)
        self.assertIn("RSI (14)", message)
        self.assertIn("Patterns:", message)
        self.assertIn("Hammer", message)
        self.assertIn("Options:", message)
        self.assertIn("Expiries:", message)
        self.assertIn("Beat 3/4", message)


class TestTickerFilter(unittest.TestCase):
    def test_select_symbols(self):
        self.assertEqual(select_symbols(["A", "B"], None), ["A", "B"])
        self.assertEqual(select_symbols(["A", "B"], "c"), ["C"])
        self.assertEqual(select_symbols([], "aapl"), ["AAPL"])

    def test_actions_grouping(self):
        bull = make_holding("BULL", score=70.0,
                            patterns=[make_pattern(status="Confirmed")])
        bear = make_holding("BEAR", score=20.0)
        flat = make_holding("FLAT", score=50.0)
        earnings = make_holding("EARN", score=50.0)
        earnings["earnings"] = {"date": "2026-07-23", "when": "today",
                                "eps_estimate": None, "revenue_estimate": None,
                                "timing": "n/a", "surprises": None}
        actions = build_actions(
            make_data([bull, bear, flat, earnings])
        )
        self.assertEqual(actions["bullish"], ["BULL"])
        self.assertEqual(actions["bearish"], ["BEAR"])
        self.assertIn("FLAT", actions["neutral"])
        self.assertEqual(actions["earnings_today"], ["EARN"])


if __name__ == "__main__":
    unittest.main()
