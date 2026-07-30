"""Tests for market_news: topic matching, dedup, priority routing, fallback.

Run with: bin/python -m pytest tests/ -v
No network access; providers and notify are mocked.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import market_news

TOPICS = {
    "Fed & Rates": {
        "query": "Federal Reserve",
        "keywords": ["fed", "rate cut"],
        "priority": "high",
    },
    "Oil & Energy": {
        "query": "oil",
        "keywords": ["oil", "crude"],
        "priority": "digest",
    },
}

SETTINGS = {"market_news_topics": TOPICS}


def futu_item(title, url="", published=""):
    return {"topic": "", "title": title, "source": "Futu",
            "url": url, "published": published}


class TestMatchTopic(unittest.TestCase):
    def test_case_insensitive_substring(self):
        self.assertTrue(market_news.match_topic("Fed hints at RATE CUT", ["rate cut"]))
        self.assertFalse(market_news.match_topic("Apple launches phone", ["rate cut"]))


class TestRunRound(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "state.json"

    def _pool(self):
        return {
            "Fed & Rates": [futu_item("Fed signals rate cut in September",
                                      url="https://x/1")],
            "Oil & Energy": [futu_item("Crude oil jumps 3%", url="https://x/2")],
        }

    def test_first_round_seeds_without_alerts(self):
        with mock.patch.object(market_news, "fetch_pool",
                               return_value=self._pool()):
            counts = market_news.run_round(SETTINGS, notify=mock.Mock(),
                                           state_path=self.state_path)
        self.assertEqual(counts, {"alerts": 0, "digest": 0})

    def test_second_round_alerts_high_and_digests_rest(self):
        notify = mock.Mock()
        with mock.patch.object(market_news, "fetch_pool",
                               return_value=self._pool()):
            market_news.run_round(SETTINGS, state_path=self.state_path)
            counts = market_news.run_round(SETTINGS, notify=notify,
                                           state_path=self.state_path)
        # Same items: already seen, nothing new.
        self.assertEqual(counts, {"alerts": 0, "digest": 0})
        notify.assert_not_called()

        # New items: high-priority alerts, digest is stored not sent.
        pool = self._pool()
        pool["Fed & Rates"][0]["url"] = "https://x/3"
        pool["Oil & Energy"][0]["url"] = "https://x/4"
        with mock.patch.object(market_news, "fetch_pool", return_value=pool):
            counts = market_news.run_round(SETTINGS, notify=notify,
                                           state_path=self.state_path)
        self.assertEqual(counts, {"alerts": 1, "digest": 1})
        notify.assert_called_once()
        message = notify.call_args[0][0]
        self.assertIn("Fed & Rates", message)
        self.assertIn("rate cut", message)
        digest = market_news.recent_digest(self.state_path)
        self.assertEqual(len(digest), 1)
        self.assertEqual(digest[0]["topic"], "Oil & Energy")

    def test_dedup_falls_back_to_title_when_no_url(self):
        with mock.patch.object(market_news, "fetch_pool",
                               return_value=self._pool()):
            market_news.run_round(SETTINGS, state_path=self.state_path)
            counts = market_news.run_round(SETTINGS, notify=mock.Mock(),
                                           state_path=self.state_path)
        self.assertEqual(counts["alerts"], 0)


class TestFetchPoolFallback(unittest.TestCase):
    def test_falls_back_to_yfinance(self):
        with (
            mock.patch.object(market_news.futu_source, "search_news",
                              side_effect=ValueError("OpenD down")),
            mock.patch.object(market_news, "_finnhub_key", return_value=None),
            mock.patch.object(market_news, "fetch_yfinance_headlines",
                              return_value=[("YF", "Fed cuts rates"),
                                            ("YF", "Local weather")]),
        ):
            pool = market_news.fetch_pool(TOPICS)
        self.assertEqual(len(pool["Fed & Rates"]), 1)
        self.assertEqual(pool["Fed & Rates"][0]["title"], "Fed cuts rates")
        self.assertEqual(pool["Oil & Energy"], [])


class TestState(unittest.TestCase):
    def test_state_round_trip_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = market_news.load_state(path)
            state["seen"]["k"] = "2020-01-01T00:00:00"
            market_news.save_state(state, path)
            loaded = market_news.load_state(path)
            market_news._prune_seen(loaded, market_news.datetime.now())
            self.assertEqual(loaded["seen"], {})


if __name__ == "__main__":
    unittest.main()
