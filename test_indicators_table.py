"""Tests for generate_indicators_table: indicator math, scoring, HTML output.

Run with: bin/python -m unittest test_indicators_table -v
No network access required.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from db import init_db
from generate_indicators_table import (
    INDICATOR_SPECS,
    MAX_RAW_SCORE,
    build_table_html,
    combine_score,
    evaluate_indicators,
    history_from_db,
    score_label,
)
from indicators import bollinger_bands, rsi, sma


def rows_by_name(rows):
    return {r["name"]: r for r in rows}


def make_result(symbol="AAA", score=80.0, label="Strong Bullish"):
    rows = [
        {
            "name": name,
            "value": "1.0",
            "signal": "bullish",
            "reliability": rel,
            "weight": weight,
            "points": weight * rel,
        }
        for name, weight, rel in INDICATOR_SPECS
    ]
    return {"symbol": symbol, "score": score, "label": label, "rows": rows}


class TestIndicatorMath(unittest.TestCase):
    def test_rsi_all_gains_is_100(self):
        closes = [float(i) for i in range(1, 30)]
        self.assertAlmostEqual(rsi(closes)[-1], 100.0)

    def test_rsi_all_losses_is_0(self):
        closes = [float(100 - i) for i in range(30)]
        self.assertAlmostEqual(rsi(closes)[-1], 0.0)

    def test_sma(self):
        out = sma([1.0, 2.0, 3.0, 4.0], 2)
        self.assertIsNone(out[0])
        self.assertEqual(out[1:], [1.5, 2.5, 3.5])

    def test_bollinger_flat_price_zero_width(self):
        closes = [10.0] * 25
        mid, upper, lower = bollinger_bands(closes)
        self.assertAlmostEqual(mid[-1], 10.0)
        self.assertAlmostEqual(upper[-1], 10.0)
        self.assertAlmostEqual(lower[-1], 10.0)

    def test_bollinger_bands_bracket_price(self):
        closes = [10.0 + (i % 5) for i in range(30)]
        _mid, upper, lower = bollinger_bands(closes)
        self.assertGreater(upper[-1], lower[-1])


class TestEvaluateIndicators(unittest.TestCase):
    def test_crash_is_bullish_on_rsi_and_bollinger(self):
        # Long flat stretch then a sharp drop: RSI oversold and the close
        # lands far below the lower band.
        closes = [100.0] * 40 + [100.0 - 3 * i for i in range(1, 8)]
        volumes = [1000.0] * len(closes)
        rows = rows_by_name(evaluate_indicators(closes, volumes))

        self.assertEqual(rows["RSI (14)"]["signal"], "bullish")
        self.assertEqual(rows["Bollinger Bands (20, 2)"]["signal"], "bullish")
        self.assertEqual(rows["MACD (12, 26, 9)"]["signal"], "bearish")
        self.assertEqual(rows["EMA Trend (9 vs 21)"]["signal"], "bearish")
        # Flat volume, no up/down day with volume > SMA -> neutral.
        self.assertEqual(rows["Volume vs SMA (20)"]["signal"], "neutral")

    def test_points_sign_matches_signal(self):
        closes = [100.0] * 40 + [100.0 - 3 * i for i in range(1, 8)]
        volumes = [1000.0] * len(closes)
        for row in evaluate_indicators(closes, volumes):
            max_points = row["weight"] * row["reliability"]
            if row["signal"] == "bullish":
                self.assertAlmostEqual(row["points"], max_points)
            elif row["signal"] == "bearish":
                self.assertAlmostEqual(row["points"], -max_points)
            else:
                self.assertEqual(row["points"], 0.0)

    def test_volume_confirms_direction(self):
        # Rising prices with a final up day on heavy volume -> bullish.
        closes = [100.0 + i for i in range(40)]
        volumes = [1000.0] * 39 + [5000.0]
        rows = rows_by_name(evaluate_indicators(closes, volumes))
        self.assertEqual(rows["Volume vs SMA (20)"]["signal"], "bullish")
        # Same heavy volume but a down day -> bearish.
        closes_down = closes[:-1] + [closes[-1] - 5.0]
        rows = rows_by_name(evaluate_indicators(closes_down, volumes))
        self.assertEqual(rows["Volume vs SMA (20)"]["signal"], "bearish")


class TestScoring(unittest.TestCase):
    def make_rows(self, signal):
        return [
            {
                "name": name,
                "value": "",
                "signal": signal,
                "reliability": rel,
                "weight": weight,
                "points": {"bullish": 1, "bearish": -1}.get(signal, 0) * weight * rel,
            }
            for name, weight, rel in INDICATOR_SPECS
        ]

    def test_all_bullish_is_100(self):
        self.assertEqual(combine_score(self.make_rows("bullish")), 100.0)

    def test_all_bearish_is_0(self):
        self.assertEqual(combine_score(self.make_rows("bearish")), 0.0)

    def test_all_neutral_is_50(self):
        self.assertEqual(combine_score(self.make_rows("neutral")), 50.0)

    def test_single_bullish_rsi(self):
        rows = self.make_rows("neutral")
        rows[0]["signal"] = "bullish"
        rows[0]["points"] = rows[0]["weight"] * rows[0]["reliability"]
        expected = round(50 + 50 * (30 * 0.79) / MAX_RAW_SCORE, 1)
        self.assertEqual(combine_score(rows), expected)

    def test_label_thresholds(self):
        self.assertEqual(score_label(100), "Strong Bullish")
        self.assertEqual(score_label(70), "Strong Bullish")
        self.assertEqual(score_label(69.9), "Moderate Bullish")
        self.assertEqual(score_label(50), "Moderate Bullish")
        self.assertEqual(score_label(49.9), "Neutral")
        self.assertEqual(score_label(30), "Neutral")
        self.assertEqual(score_label(29.9), "Moderate Bearish")
        self.assertEqual(score_label(10), "Moderate Bearish")
        self.assertEqual(score_label(9.9), "Strong Bearish")
        self.assertEqual(score_label(0), "Strong Bearish")


class TestHistoryFromDb(unittest.TestCase):
    def test_reads_closes_and_volumes_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = init_db(Path(tmp) / "test.db")
            conn.executemany(
                "INSERT INTO daily_prices (symbol, date, close, volume) VALUES (?, ?, ?, ?)",
                [
                    ("AAA", "2026-07-21", 11.0, None),
                    ("AAA", "2026-07-20", 10.0, 500),
                    ("AAA", "2026-07-19", None, 300),  # skipped
                ],
            )
            closes, volumes = history_from_db(conn, "AAA")
            conn.close()
        self.assertEqual(closes, [10.0, 11.0])
        self.assertEqual(volumes, [500.0, 0.0])


class TestTableHtml(unittest.TestCase):
    def test_html_contains_all_indicators_and_sections(self):
        html = build_table_html([make_result()], "2026-07-22 10:00:00")
        self.assertIn("<html", html.lower())
        self.assertIn("AAA", html)
        for name, _weight, _rel in INDICATOR_SPECS:
            self.assertIn(name, html)
        self.assertIn("Strong Bullish", html)
        # Dark theme, summary, legend, and reliability guide.
        self.assertIn("#0d1117", html)
        self.assertIn("Summary", html)
        self.assertIn("Signal thresholds", html)
        self.assertIn("win rate", html.lower())
        # Score marker positioned at the score.
        self.assertIn("left:80.0%", html)

    def test_html_is_self_contained(self):
        html = build_table_html([make_result()], "2026-07-22 10:00:00")
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("<link", html.lower())
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_html_file_written(self):
        html = build_table_html([make_result()], "2026-07-22 10:00:00")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "indicators_table.html"
            path.write_text(html)
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
