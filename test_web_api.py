"""Tests for the web API: Bollinger Bands and the extended indicator rows.

Run with: bin/python -m pytest test_web_api.py -v
Uses the Flask test client against the real local database; the options
fetch is mocked so no network is needed for /api/indicators. /api/prices
tolerates a failed live-quote overlay (falls back to stored bars).
"""

import unittest
from unittest import mock

from web import app


class TestPricesBollinger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def test_bollinger_bands_present_and_aligned(self):
        data = self.client.get("/api/prices/AAPL").get_json()
        self.assertIsNotNone(data)
        n = len(data["dates"])
        self.assertGreater(n, 30)
        for key in ("bb_upper", "bb_middle", "bb_lower"):
            self.assertIn(key, data)
            self.assertEqual(len(data[key]), n)
        # Bands are computed over the full history before slicing to the
        # displayed window (same as MACD/RSI), so warm-up nulls are
        # already sliced off and the latest bar always has values.
        self.assertIsNotNone(data["bb_upper"][-1])
        self.assertIsNotNone(data["bb_middle"][-1])
        self.assertIsNotNone(data["bb_lower"][-1])
        # Bands bracket the middle at the latest bar.
        self.assertGreater(data["bb_upper"][-1], data["bb_middle"][-1])
        self.assertGreater(data["bb_middle"][-1], data["bb_lower"][-1])


class TestIndicatorsExtended(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def test_seven_rows_with_new_indicators(self):
        flow = {
            "call_volume": 1000,
            "put_volume": 2000,
            "pcr": 2.0,
            "expiries": ["2026-07-24"],
        }
        with mock.patch(
            "options_flow.fetch_options_flow", return_value=flow
        ):
            data = self.client.get("/api/indicators/AAPL").get_json()
        self.assertIsNotNone(data)
        rows = data["rows"]
        self.assertEqual(len(rows), 7)

        names = [r["name"] for r in rows]
        self.assertIn("Candlestick Pattern", names)
        self.assertIn("Options Flow (PCR)", names)

        pattern_row = rows[names.index("Candlestick Pattern")]
        self.assertEqual(pattern_row["weight"], 20)
        self.assertIn(pattern_row["signal"], ("bullish", "bearish", "neutral"))

        options_row = rows[names.index("Options Flow (PCR)")]
        self.assertEqual(options_row["weight"], 8)
        self.assertEqual(options_row["value"], "PCR 2.00 — Strong Bearish")
        self.assertEqual(options_row["signal"], "bearish")
        self.assertEqual(options_row["points"], -8.0)

        # Score stays on the clamped 0-100 scale with a matching label.
        self.assertGreaterEqual(data["score"], 0.0)
        self.assertLessEqual(data["score"], 100.0)
        self.assertTrue(data["label"])

    def test_options_fetch_failure_is_neutral(self):
        with mock.patch("options_flow.fetch_options_flow", return_value=None):
            data = self.client.get("/api/indicators/AAPL").get_json()
        self.assertIsNotNone(data)
        names = [r["name"] for r in data["rows"]]
        options_row = data["rows"][names.index("Options Flow (PCR)")]
        self.assertEqual(options_row["value"], "n/a")
        self.assertEqual(options_row["signal"], "neutral")
        self.assertEqual(options_row["points"], 0.0)

    def test_insufficient_history_returns_null(self):
        data = self.client.get("/api/indicators/ZZZZZ").get_json()
        self.assertIsNone(data)


if __name__ == "__main__":
    unittest.main()
