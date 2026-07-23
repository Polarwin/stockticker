"""Tests for non-equity symbol detection and the skip cache."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fundamentals import earnings_tracker, fetcher


class NonEquityCacheTest(unittest.TestCase):
    def test_is_non_equity_symbol(self):
        self.assertTrue(fetcher.is_non_equity_symbol("^VIX"))
        self.assertFalse(fetcher.is_non_equity_symbol("AAPL"))

    def test_record_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "non_equity.json"
            fetcher.record_non_equity("SPY", "ETF", path=path)
            fetcher.record_non_equity("QQQ", "ETF", path=path)
            self.assertEqual(fetcher.load_non_equity(path), {"SPY", "QQQ"})
            data = json.loads(path.read_text())
            self.assertEqual(data["SPY"], "ETF")

    def test_load_missing_or_invalid_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            self.assertEqual(fetcher.load_non_equity(missing), set())
            bad = Path(tmp) / "bad.json"
            bad.write_text("not json")
            self.assertEqual(fetcher.load_non_equity(bad), set())

    def test_equity_only_filters_index_and_cached(self):
        watchlist = ["AAPL", "^VIX", "SPY", "MSFT"]
        with mock.patch.object(
            fetcher, "load_non_equity", return_value={"SPY"}
        ):
            self.assertEqual(
                earnings_tracker._equity_only(watchlist), ["AAPL", "MSFT"]
            )


if __name__ == "__main__":
    unittest.main()
