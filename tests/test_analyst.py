"""Tests for analyst.py (mapping, fallback, cache) and stock_report rendering.

Run with: bin/python -m pytest tests/ -v
No network access; providers are mocked.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import analyst
import stock_report

FUTU_CONSENSUS = {
    "highest": 650.0, "average": 581.24, "lowest": 493.87, "rating": 4,
    "total": 13, "update_time_str": "2026-07-30",
    "buy": 76.9, "hold": 23.1, "sell": 0.0,
}

FUTU_SUMMARY = {
    "next_key": "-1",
    "inst_rating_summary_list": [{
        "institution_info": {"institution_en_name": "Wells Fargo"},
        "rating_item_list": [{
            "rating": 3, "last_rating": 4,
            "recommendation_date_str": "2026-07-21",
        }],
    }],
}


class TestFutuMapping(unittest.TestCase):
    def test_consensus_and_grades_normalized(self):
        ctx = mock.Mock()
        ctx.get_research_analyst_consensus.return_value = (0, FUTU_CONSENSUS)
        ctx.get_research_rating_summary.return_value = (0, FUTU_SUMMARY)
        with (
            mock.patch.object(analyst.futu_source, "to_futu_code",
                              return_value="US.SNPS"),
            mock.patch.object(analyst.futu_source, "_quote_ctx",
                              return_value=ctx),
            mock.patch.object(analyst.futu_source, "_futu") as futu_mod,
        ):
            futu_mod.return_value.RET_OK = 0
            data = analyst._futu_analyst_data("SNPS")

        consensus = data["consensus"]
        self.assertEqual(consensus["mean_target"], 581.24)
        self.assertEqual(consensus["rating_label"], "Buy")
        self.assertEqual(consensus["total"], 13)
        grade = data["grades"][0]
        self.assertEqual(grade["firm"], "Wells Fargo")
        self.assertEqual(grade["to_grade"], "Hold")
        self.assertEqual(grade["from_grade"], "Buy")
        self.assertEqual(grade["date"], "2026-07-21")


class TestYfinanceFallback(unittest.TestCase):
    def test_fallback_when_futu_fails(self):
        ticker = mock.Mock()
        ticker.analyst_price_targets = {
            "mean": 581.24, "high": 650.0, "low": 493.87,
            "numberOfAnalysts": 13,
        }
        import pandas as pd
        ticker.recommendations_summary = pd.DataFrame([{
            "period": "0m", "strongBuy": 4, "buy": 14, "hold": 5,
            "sell": 0, "strongSell": 1,
        }])
        ticker.upgrades_downgrades = pd.DataFrame([{
            "GradeDate": "2026-07-16T10:32:39", "Firm": "Benchmark",
            "Action": "init", "ToGrade": "Buy", "FromGrade": "",
            "currentPriceTarget": 570.0, "priorPriceTarget": 0.0,
        }])
        with (
            mock.patch.object(analyst, "_futu_analyst_data",
                              side_effect=ValueError("OpenD down")),
            mock.patch("yfinance.Ticker", return_value=ticker),
        ):
            data = analyst._fetch_fresh("SNPS")
        self.assertEqual(data["consensus"]["mean_target"], 581.24)
        self.assertEqual(data["trend"][0]["strong_buy"], 4)
        self.assertEqual(data["grades"][0]["firm"], "Benchmark")
        self.assertEqual(data["grades"][0]["date"], "2026-07-16")

    def test_total_failure_returns_empty_shape(self):
        with (
            mock.patch.object(analyst, "_futu_analyst_data",
                              side_effect=ValueError("down")),
            mock.patch.object(analyst, "_yf_analyst_data",
                              side_effect=ValueError("down too")),
        ):
            data = analyst._fetch_fresh("SNPS")
        self.assertEqual(data, {"consensus": None, "trend": [], "grades": []})


class TestCache(unittest.TestCase):
    def test_cache_hit_within_ttl_and_miss_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            fresh = {"consensus": {"mean_target": 1.0}, "trend": [],
                     "grades": []}
            with mock.patch.object(analyst, "_fetch_fresh",
                                   return_value=fresh) as fetch:
                first = analyst.fetch_analyst_data("SNPS", cache_path=path)
                second = analyst.fetch_analyst_data("SNPS", cache_path=path)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(first, second)

                # Age the entry beyond the TTL.
                cache = json.loads(path.read_text())
                cache["SNPS"]["fetched_at"] = (
                    datetime.now() - timedelta(hours=7)
                ).isoformat()
                path.write_text(json.dumps(cache))
                analyst.fetch_analyst_data("SNPS", cache_path=path)
                self.assertEqual(fetch.call_count, 2)


class TestRenderStockPage(unittest.TestCase):
    def _base_data(self):
        return {
            "ticker": "SNPS",
            "generated_at": "Thu Jul 30, 18:00 EDT",
            "quote": {"price": 610.0, "change_pct": 1.5, "prev_close": 601.0},
            "holding": {
                "symbol": "SNPS", "price": 610.0, "pct": 1.5,
                "score": {"base": 60.0, "pattern": 0, "sentiment": 0,
                          "options": 0, "final": 60.0, "label": "Bullish"},
                "indicator_rows": [{
                    "name": "RSI(14)", "value": "58.0", "signal": "bullish",
                    "points": 8.0,
                }],
                "patterns": [],
                "options": None,
                "sentiment": {"label": "Neutral", "count": 1,
                              "headlines": [("Reuters", "SNPS beats")]},
            },
            "fundamentals": None,
            "analyst": {"consensus": None, "trend": [], "grades": []},
            "news_source": "futu",
        }

    def test_renders_sparse_without_fundamentals(self):
        page = stock_report.render_stock_page(self._base_data())
        self.assertIn("SNPS", page)
        self.assertIn("Technical Analysis", page)
        self.assertIn("n/a", page)

    def test_renders_full_data(self):
        data = self._base_data()
        data["fundamentals"] = {
            "name": "Synopsys", "sector": "Technology",
            "industry": "Software", "market_cap": 9.5e10,
            "ratios": {"pe_ratio": 40.0, "pb_ratio": 8.0},
            "history_percentiles": {"pe_ratio": 55.0},
            "fundamental_score": {"total": 81},
            "moat_score": 52, "moat_rating": "Weak Moat",
            "sector_percentile": 60.0,
            "dcf": {"intrinsic_value_per_share": 700.0,
                    "upside_downside_pct": 14.8, "mos_label": "Buy"},
        }
        data["analyst"] = {
            "consensus": {
                "mean_target": 581.0, "high_target": 650.0,
                "low_target": 493.0, "total": 13, "buy_pct": 76.9,
                "hold_pct": 23.1, "sell_pct": 0.0, "rating_label": "Buy",
                "date": "2026-07-30",
            },
            "trend": [{"period": "0m", "strong_buy": 4, "buy": 14,
                       "hold": 5, "sell": 0, "strong_sell": 1}],
            "grades": [{"date": "2026-07-16", "firm": "Benchmark",
                        "action": "init", "to_grade": "Buy",
                        "from_grade": "", "target": 570.0,
                        "prior_target": 0.0}],
        }
        page = stock_report.render_stock_page(data)
        self.assertIn("Synopsys", page)
        self.assertIn("Analyst Consensus", page)
        self.assertIn("Benchmark", page)
        self.assertIn("Recommendation trend", page)
        self.assertIn("DCF fair value", page)
        self.assertIn("55th pct", page)


if __name__ == "__main__":
    unittest.main()
