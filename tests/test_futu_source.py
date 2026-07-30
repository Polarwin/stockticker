"""Tests for futu_source and its provider-chain wiring.

Run with: bin/python -m pytest tests/ -v
No network access and no real futu SDK required: a fake `futu` module is
injected into sys.modules, and futu_source caches are reset per test.
"""

import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

import futu_source
import sentiment
import ticker
from fundamentals import database, reporter


class FakeFrame:
    """Minimal stand-in for the pandas DataFrames the SDK returns."""

    def __init__(self, records):
        self._records = records

    def to_dict(self, orient):
        assert orient == "records"
        return list(self._records)


class FakeQuoteContext:
    """Canned OpenQuoteContext; tests configure the attributes."""

    news_records = []
    snapshot_row = {}
    basicinfo_row = {}
    profile_records = []
    statement_reports = {}  # statement_type -> list[report dict]
    market_states = {}  # futu code -> state string
    fail_on = set()  # method names that should return (RET_ERROR, msg)

    RET_ERROR = -1

    def _maybe_fail(self, name, payload):
        if name in self.fail_on:
            return self.RET_ERROR, f"{name} boom"
        return 0, payload

    def get_search_news(self, code, max_count=None, news_sub_type=None):
        return self._maybe_fail("news", FakeFrame(self.news_records))

    def get_market_snapshot(self, code_list):
        return self._maybe_fail("snapshot", FakeFrame([self.snapshot_row]))

    def get_stock_basicinfo(self, market=None, stock_type=None, code_list=None):
        return self._maybe_fail("basicinfo", FakeFrame([self.basicinfo_row]))

    def get_company_profile(self, code):
        return self._maybe_fail("profile", FakeFrame(self.profile_records))

    def get_market_state(self, code_list):
        records = [
            {"code": c, "stock_name": c,
             "market_state": self.market_states.get(c, "")}
            for c in code_list
        ]
        return self._maybe_fail("market_state", FakeFrame(records))

    kline_records = []

    def request_history_kline(
        self, code, start=None, end=None, ktype=None, autype=None,
        fields=None, max_count=None, page_req_key=None, extended_time=False,
        session=None,
    ):
        if "kline" in self.fail_on:
            return self.RET_ERROR, "kline boom", None
        return 0, FakeFrame(self.kline_records), None

    def get_financials_statements(
        self, code, statement_type=None, financial_type=None,
        currency_code=None, next_key=None, num=None,
    ):
        reports = self.statement_reports.get(statement_type, [])
        return self._maybe_fail(
            "statements", {"report_list": reports, "next_key": "-1"}
        )


def fake_futu_module(ctx):
    """A fake `futu` module bound to the given context."""
    module = types.ModuleType("futu")
    module.RET_OK = 0
    module.NewsSubType = types.SimpleNamespace(ALL="ALL", NEWS="NEWS")
    module.SysConfig = types.SimpleNamespace(
        set_all_thread_daemon=lambda value: None
    )
    module.KLType = types.SimpleNamespace(K_WEEK="K_WEEK", K_DAY="K_DAY")
    module.OpenQuoteContext = lambda host, port: ctx
    return module


class FutuTestCase(unittest.TestCase):
    def setUp(self):
        futu_source.reset_for_tests()
        self.ctx = FakeQuoteContext()
        # Reset class-level canned data between tests.
        FakeQuoteContext.news_records = []
        FakeQuoteContext.snapshot_row = {}
        FakeQuoteContext.basicinfo_row = {}
        FakeQuoteContext.profile_records = []
        FakeQuoteContext.statement_reports = {}
        FakeQuoteContext.market_states = {}
        FakeQuoteContext.kline_records = []
        FakeQuoteContext.fail_on = set()
        patcher = mock.patch.dict(
            sys.modules, {"futu": fake_futu_module(self.ctx)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # Keep the suite fast: no real sleeping between statement calls.
        throttle = mock.patch.object(futu_source, "_STATEMENT_MIN_INTERVAL", 0)
        throttle.start()
        self.addCleanup(throttle.stop)
        self.addCleanup(futu_source.reset_for_tests)


class TestToFutuCode(unittest.TestCase):
    def test_us_symbol(self):
        self.assertEqual(futu_source.to_futu_code("aapl"), "US.AAPL")

    def test_vix_maps_to_futu_index_code(self):
        # ^VIX is in SYMBOL_MAP; unmapped indexes still return None.
        self.assertEqual(futu_source.to_futu_code("^VIX"), "US..VIX")
        self.assertIsNone(futu_source.to_futu_code("^GSPC"))

    def test_class_share_dash_to_dot(self):
        self.assertEqual(futu_source.to_futu_code("BRK-B"), "US.BRK.B")

    def test_junk_returns_none(self):
        self.assertIsNone(futu_source.to_futu_code("TWDUSD=X"))
        self.assertIsNone(futu_source.to_futu_code(""))


class TestAvailability(unittest.TestCase):
    def setUp(self):
        futu_source.reset_for_tests()
        self.addCleanup(futu_source.reset_for_tests)

    def test_unavailable_when_connect_fails(self):
        module = types.ModuleType("futu")
        module.RET_OK = 0

        def boom(host, port):
            raise ConnectionRefusedError("no OpenD")

        module.OpenQuoteContext = boom
        with mock.patch.dict(sys.modules, {"futu": module}):
            self.assertFalse(futu_source.available())
            # Failure is cached: still disabled without retrying.
            module.OpenQuoteContext = lambda host, port: object()
            self.assertFalse(futu_source.available())

    def test_unavailable_without_sdk(self):
        with mock.patch.dict(sys.modules, {"futu": None}):
            self.assertFalse(futu_source.available())


class TestFetchHeadlines(FutuTestCase):
    def test_maps_and_filters_by_hours(self):
        fresh = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        stale = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        FakeQuoteContext.news_records = [
            {"title": "Fresh news", "source": "Reuters", "publish_time": fresh},
            {"title": "Old news", "source": "Reuters", "publish_time": stale},
            {"title": "", "source": "Reuters", "publish_time": fresh},
        ]
        items = futu_source.fetch_headlines("AAPL", hours=24)
        self.assertEqual(items, [("Reuters", "Fresh news")])

    def test_non_us_symbol_raises(self):
        with self.assertRaises(ValueError):
            futu_source.fetch_headlines("^GSPC")

    def test_api_error_raises_valueerror(self):
        FakeQuoteContext.fail_on = {"news"}
        with self.assertRaisesRegex(ValueError, "AAPL"):
            futu_source.fetch_headlines("AAPL")


class TestFetchProfile(FutuTestCase):
    def test_maps_snapshot_and_basicinfo(self):
        FakeQuoteContext.snapshot_row = {
            "last_price": 210.5,
            "total_market_val": 3.2e12,
            "issued_shares": 15.3e9,
        }
        FakeQuoteContext.basicinfo_row = {
            "name": "Apple Inc.", "stock_type": "STOCK",
        }
        FakeQuoteContext.profile_records = [
            {"name": "Industry", "value": "Consumer Electronics"},
            {"name": "Company Introduction", "value": "Makes phones."},
        ]
        profile = futu_source.fetch_profile("AAPL")
        self.assertEqual(profile["ticker"], "AAPL")
        self.assertEqual(profile["name"], "Apple Inc.")
        self.assertEqual(profile["current_price"], 210.5)
        self.assertEqual(profile["market_cap"], 3.2e12)
        self.assertEqual(profile["shares_outstanding"], 15.3e9)
        self.assertEqual(profile["quote_type"], "EQUITY")
        self.assertEqual(profile["industry"], "Consumer Electronics")
        self.assertEqual(profile["business_summary"], "Makes phones.")
        self.assertEqual(profile["currency"], "USD")
        self.assertIsNone(profile["financial_currency"])
        self.assertIsNone(profile["forward_eps"])

    def test_etf_maps_to_non_equity_type(self):
        FakeQuoteContext.snapshot_row = {"last_price": 500.0}
        FakeQuoteContext.basicinfo_row = {"name": "SPY", "stock_type": "ETF"}
        profile = futu_source.fetch_profile("SPY")
        self.assertEqual(profile["quote_type"], "ETF")

    def test_snapshot_failure_raises(self):
        FakeQuoteContext.fail_on = {"snapshot"}
        with self.assertRaisesRegex(ValueError, "AAPL"):
            futu_source.fetch_profile("AAPL")


def _report(date_str, ftype, items):
    return {
        "date_time_str": date_str,
        "fiscal_year": int(date_str[:4]),
        "financial_type": ftype,
        "period_text": "",
        "item_list": [
            {"field_id": i, "display_name": name, "data": value}
            for i, (name, value) in enumerate(items, start=1)
        ],
    }


class TestFetchFinancials(FutuTestCase):
    def _seed_reports(self):
        FakeQuoteContext.statement_reports = {
            1: [  # income statement
                _report("2025-09-30", 7, [
                    ("Total Operating Revenue", 400e9),
                    ("Cost of Revenue", 200e9),
                    ("Gross Profit", 200e9),
                    ("Net Profit", 100e9),
                    ("Diluted EPS", 6.5),
                ]),
                _report("2025-06-30", 3, [
                    ("Total Operating Revenue", 90e9),
                    ("Net Profit", 22e9),
                ]),
            ],
            2: [  # balance sheet
                _report("2025-09-30", 7, [
                    ("Total Assets", 350e9),
                    ("Total Current Assets", 140e9),
                    ("Long Term Debt and Capital Lease Obligation", 80e9),
                    ("Short-Term Debt and Capital Lease Obligation", 20e9),
                ]),
            ],
            3: [  # cash flow
                _report("2025-09-30", 7, [
                    ("Operating Cash Flow", 110e9),
                    ("Net PPE Purchase and Sale", 12e9),
                ]),
            ],
        }

    def test_rows_merged_and_ordered(self):
        self._seed_reports()
        rows = futu_source.fetch_financials("AAPL")
        self.assertEqual([r["report_type"] for r in rows], ["10-K", "10-Q"])
        annual, quarter = rows
        self.assertEqual(annual["fiscal_date"], "2025-09-30")
        self.assertEqual(annual["revenue"], 400e9)
        self.assertEqual(annual["gross_profit"], 200e9)
        self.assertEqual(annual["net_income"], 100e9)
        self.assertEqual(annual["eps"], 6.5)
        self.assertEqual(annual["total_assets"], 350e9)
        self.assertEqual(annual["current_assets"], 140e9)
        # Debt parts summed when no explicit total_debt field exists.
        self.assertEqual(annual["total_debt"], 100e9)
        self.assertNotIn("_debt_long", annual)
        self.assertEqual(annual["operating_cash_flow"], 110e9)
        # FCF derived sign-aware: OCF - |CapEx|; CapEx stored negative.
        self.assertEqual(annual["free_cash_flow"], 110e9 - 12e9)
        self.assertEqual(annual["capital_expenditure"], -12e9)
        # Unmapped fields default to None so the schema matches fetcher.py.
        self.assertIsNone(annual["total_liabilities"])
        self.assertEqual(quarter["fiscal_date"], "2025-06-30")
        self.assertEqual(quarter["report_type"], "10-Q")

    def test_map_item_prefers_longest_candidate(self):
        self.assertEqual(
            futu_source._map_item("Total Current Assets"), "current_assets"
        )
        self.assertEqual(futu_source._map_item("Total Assets"), "total_assets")
        # "Cost of Revenue" must not collapse into revenue.
        self.assertIsNone(futu_source._map_item("Cost of Revenue"))

    def test_no_reports_raises(self):
        with self.assertRaisesRegex(ValueError, "AAPL"):
            futu_source.fetch_financials("AAPL")

    def test_api_error_raises_valueerror(self):
        FakeQuoteContext.fail_on = {"statements"}
        with self.assertRaisesRegex(ValueError, "AAPL"):
            futu_source.fetch_financials("AAPL")


class TestFetchPriceHistory(FutuTestCase):
    def test_returns_sorted_points(self):
        FakeQuoteContext.kline_records = [
            {"time_key": "2025-01-13 00:00:00", "close": 105.0},
            {"time_key": "2025-01-06 00:00:00", "close": 100.0},
            {"time_key": "bad", "close": None},
        ]
        points = futu_source.fetch_price_history("AAPL", years=1)
        self.assertEqual(
            points, [("2025-01-06", 100.0), ("2025-01-13", 105.0)]
        )

    def test_empty_history_raises(self):
        with self.assertRaisesRegex(ValueError, "AAPL"):
            futu_source.fetch_price_history("AAPL")

    def test_api_error_raises(self):
        FakeQuoteContext.fail_on = {"kline"}
        with self.assertRaisesRegex(ValueError, "AAPL"):
            futu_source.fetch_price_history("AAPL")


class TestBackfillValuationHistory(unittest.TestCase):
    def test_asof_snapshots_written(self):
        from fundamentals import database, history

        conn = database.init_db(":memory:")
        try:
            fin_rows = [
                {"ticker": "AAA", "fiscal_date": "2024-12-31",
                 "report_type": "10-K", "net_income": 200.0,
                 "shareholders_equity": 1000.0},
                {"ticker": "AAA", "fiscal_date": "2023-12-31",
                 "report_type": "10-K", "net_income": 100.0,
                 "shareholders_equity": 800.0},
            ]
            points = [
                ("2023-06-01", 10.0),   # before any report -> skipped
                ("2024-06-01", 100.0),  # TTM = 2023 annual only
                ("2025-06-01", 400.0),  # TTM = 2024 annual
            ]
            written = history.backfill_valuation_history(
                conn, "AAA", {"shares_outstanding": 10.0}, fin_rows, points
            )
            self.assertEqual(written, 2)
            rows = {
                r["date"]: r
                for r in database.get_historical_valuation(conn, "AAA")
            }
            # 2024 point: mcap 100*10=1000, pe = 1000/100 = 10, pb = 1.25
            self.assertAlmostEqual(rows["2024-06-01"]["pe_ratio"], 10.0)
            self.assertAlmostEqual(rows["2024-06-01"]["pb_ratio"], 1.25)
            # 2025 point: mcap 4000, pe = 4000/200 = 20
            self.assertAlmostEqual(rows["2025-06-01"]["pe_ratio"], 20.0)
            self.assertNotIn("2023-06-01", rows)
        finally:
            conn.close()

    def test_no_shares_writes_nothing(self):
        from fundamentals import database, history

        conn = database.init_db(":memory:")
        try:
            written = history.backfill_valuation_history(
                conn, "AAA", {}, [{"fiscal_date": "2024-12-31"}],
                [("2025-01-01", 1.0)],
            )
            self.assertEqual(written, 0)
        finally:
            conn.close()


class TestFetchQuotes(FutuTestCase):
    def test_maps_snapshot_rows(self):
        def snapshot(code_list):
            rows = [
                {"code": c, "last_price": 100.0, "prev_close_price": 98.0}
                for c in code_list
            ]
            return 0, FakeFrame(rows)

        self.ctx.get_market_snapshot = snapshot
        quotes = futu_source.fetch_quotes(["AAPL", "MSFT"])
        self.assertEqual(
            quotes,
            {
                "AAPL": {"price": 100.0, "change_pct": 2.04, "prev_close": 98.0},
                "MSFT": {"price": 100.0, "change_pct": 2.04, "prev_close": 98.0},
            },
        )

    def test_skips_indexes_and_bad_rows(self):
        def snapshot(code_list):
            return 0, FakeFrame([
                {"code": "US.AAPL", "last_price": 100.0,
                 "prev_close_price": 0},  # no prev close -> change None
                {"code": "US.MSFT", "last_price": None,
                 "prev_close_price": 1.0},  # no price -> dropped
            ])

        self.ctx.get_market_snapshot = snapshot
        quotes = futu_source.fetch_quotes(["AAPL", "MSFT", "^VIX"])
        self.assertEqual(
            quotes,
            {"AAPL": {"price": 100.0, "change_pct": None, "prev_close": None}},
        )

    def test_api_error_returns_empty(self):
        FakeQuoteContext.fail_on = {"snapshot"}
        self.assertEqual(futu_source.fetch_quotes(["AAPL"]), {})

    def test_batch_failure_retries_per_code(self):
        def snapshot(code_list):
            if len(code_list) > 1:
                return -1, "one bad code poisoned the batch"
            return 0, FakeFrame([{
                "code": code_list[0], "last_price": 100.0,
                "prev_close_price": 98.0,
            }])

        self.ctx.get_market_snapshot = snapshot
        quotes = futu_source.fetch_quotes(["AAPL", "MSFT"])
        self.assertEqual(sorted(quotes), ["AAPL", "MSFT"])
        self.assertEqual(quotes["AAPL"]["price"], 100.0)

    def test_session_aware_price_pick(self):
        row = {
            "code": "US.AAPL", "last_price": 100.0, "prev_close_price": 98.0,
            "pre_price": 101.0, "after_price": 102.0, "overnight_price": 103.0,
        }

        def snapshot(code_list):
            return 0, FakeFrame([dict(row, code=c) for c in code_list])

        self.ctx.get_market_snapshot = snapshot
        cases = {
            "PRE_MARKET_BEGIN": 101.0,
            "AFTER_HOURS_BEGIN": 102.0,
            "NIGHT_OPEN": 103.0,
            "MORNING": 100.0,
            "": 100.0,  # state unknown -> regular last price
        }
        for state, expected in cases.items():
            FakeQuoteContext.market_states = {"US.AAPL": state}
            quotes = futu_source.fetch_quotes(["AAPL"])
            self.assertEqual(
                quotes["AAPL"]["price"], expected, f"state={state!r}"
            )
            # change_pct always measured against the regular prev close.
            self.assertEqual(
                quotes["AAPL"]["change_pct"],
                round((expected - 98.0) / 98.0 * 100, 2),
            )

    def test_session_price_falls_back_to_last_when_empty(self):
        def snapshot(code_list):
            return 0, FakeFrame([{
                "code": "US.AAPL", "last_price": 100.0,
                "prev_close_price": 98.0, "pre_price": None,
            }])

        self.ctx.get_market_snapshot = snapshot
        FakeQuoteContext.market_states = {"US.AAPL": "PRE_MARKET_BEGIN"}
        self.assertEqual(futu_source.fetch_quotes(["AAPL"])["AAPL"]["price"], 100.0)


class TestSymbolMap(unittest.TestCase):
    def test_override_wins(self):
        with mock.patch.dict(futu_source.SYMBOL_MAP, {"FOO": "US.BAR"}):
            self.assertEqual(futu_source.to_futu_code("foo"), "US.BAR")

    def test_default_rule_unchanged_without_entry(self):
        self.assertEqual(futu_source.to_futu_code("AAPL"), "US.AAPL")
        self.assertIsNone(futu_source.to_futu_code("^GSPC"))


class TestTickerQuoteChain(unittest.TestCase):
    def test_futu_first_yf_fills_gaps(self):
        futu_quotes = {
            "AAPL": {"price": 100.0, "change_pct": 2.0, "prev_close": 98.0}
        }
        yf_quotes = {
            "^VIX": {"price": 20.0, "change_pct": -1.0, "prev_close": 20.2}
        }
        with (
            mock.patch.object(
                ticker.futu_source, "fetch_quotes", return_value=futu_quotes
            ) as futu_fetch,
            mock.patch.object(
                ticker, "_fetch_live_quotes_yf", return_value=yf_quotes
            ) as yf_fetch,
        ):
            quotes = ticker.fetch_live_quotes(["AAPL", "^VIX"])
        self.assertEqual(quotes, {**futu_quotes, **yf_quotes})
        futu_fetch.assert_called_once_with(["AAPL", "^VIX"])
        # Only the symbols Futu missed go to yfinance.
        yf_fetch.assert_called_once_with(["^VIX"])

    def test_yf_not_called_when_futu_covers_all(self):
        futu_quotes = {
            "AAPL": {"price": 100.0, "change_pct": 2.0, "prev_close": 98.0}
        }
        with (
            mock.patch.object(
                ticker.futu_source, "fetch_quotes", return_value=futu_quotes
            ),
            mock.patch.object(ticker, "_fetch_live_quotes_yf") as yf_fetch,
        ):
            quotes = ticker.fetch_live_quotes(["AAPL"])
        self.assertEqual(quotes, futu_quotes)
        yf_fetch.assert_not_called()


class TestSentimentChain(unittest.TestCase):
    def setUp(self):
        sentiment._providers_used.clear()
        self.addCleanup(sentiment._providers_used.clear)

    def test_futu_tried_first(self):
        with (
            mock.patch.object(
                sentiment.futu_source, "available", return_value=True
            ),
            mock.patch.object(
                sentiment.futu_source, "fetch_headlines",
                return_value=[("Futu", "Headline")],
            ) as futu_fetch,
            mock.patch.object(sentiment, "_finnhub_key", return_value="k"),
            mock.patch.object(sentiment, "_finnhub_headlines") as finnhub_fetch,
        ):
            items = sentiment.fetch_headlines("AAPL")
        self.assertEqual(items, [("Futu", "Headline")])
        futu_fetch.assert_called_once()
        finnhub_fetch.assert_not_called()
        self.assertIn("futu", sentiment._providers_used)

    def test_falls_through_to_yfinance_when_futu_fails(self):
        with (
            mock.patch.object(
                sentiment.futu_source, "available", return_value=True
            ),
            mock.patch.object(
                sentiment.futu_source, "fetch_headlines",
                side_effect=ValueError("OpenD down"),
            ),
            mock.patch.object(sentiment, "_finnhub_key", return_value=None),
            mock.patch.object(sentiment, "_alphavantage_key", return_value=None),
            mock.patch.object(
                sentiment, "fetch_yfinance_headlines",
                return_value=[("YF", "Fallback")],
            ),
        ):
            items = sentiment.fetch_headlines("AAPL")
        self.assertEqual(items, [("YF", "Fallback")])


class TestReporterFallback(unittest.TestCase):
    """update_ticker falls back to the yfinance fetcher when Futu fails."""

    def _run(self):
        profile = {
            "ticker": "AAPL", "name": "Apple", "quote_type": "EQUITY",
            "currency": "USD", "financial_currency": "USD",
        }
        fin_rows = [{"ticker": "AAPL", "fiscal_date": "2025-09-30",
                     "report_type": "10-K"}]
        with (
            mock.patch.object(
                reporter.futu_source, "fetch_profile",
                side_effect=ValueError("OpenD down"),
            ),
            mock.patch.object(
                reporter.fetcher, "fetch_profile", return_value=profile
            ) as yf_profile,
            mock.patch.object(
                reporter.futu_source, "fetch_financials",
                side_effect=ValueError("OpenD down"),
            ),
            mock.patch.object(
                reporter.fetcher, "fetch_financials", return_value=fin_rows
            ) as yf_financials,
            mock.patch.object(
                reporter.fetcher, "fetch_price", return_value=200.0
            ),
            mock.patch.object(
                reporter.fetcher, "fetch_earnings", return_value=[]
            ),
            mock.patch.object(reporter.fetcher, "is_non_equity_symbol",
                              return_value=False),
            mock.patch.object(reporter.fetcher, "load_non_equity",
                              return_value=set()),
            mock.patch.object(reporter, "database"),
            mock.patch.object(reporter.moat_scorer, "compute_moat_metrics",
                              return_value={}),
            mock.patch.object(reporter.moat_scorer, "moat_score",
                              return_value=(0, "None", {})),
            mock.patch.object(reporter.calculator, "compute_valuation_ratios",
                              return_value={}),
            mock.patch.object(reporter.dcf_valuator, "compute_dcf",
                              return_value=None),
            mock.patch.object(reporter.history,
                              "update_historical_valuation", return_value={}),
            mock.patch.object(reporter.peers, "compute_peer_comparison",
                              return_value={}),
            mock.patch.object(reporter.peers, "peers_for", return_value=[]),
            mock.patch.object(reporter.peers, "update_sector_percentiles",
                              return_value=None),
            mock.patch.object(reporter.scorer, "fundamental_score",
                              return_value={}),
        ):
            result = reporter.update_ticker(
                mock.sentinel.conn, "AAPL", risk_free_rate=0.04
            )
        return result, yf_profile, yf_financials

    def test_yfinance_used_on_futu_failure(self):
        result, yf_profile, yf_financials = self._run()
        yf_profile.assert_called_once_with("AAPL")
        yf_financials.assert_called_once_with("AAPL")
        self.assertEqual(result["ticker"], "AAPL")
        self.assertEqual(result["price"], 200.0)


class TestReporterBackfill(unittest.TestCase):
    """update_ticker backfills valuation history from Futu weekly prices."""

    def test_backfills_when_history_sparse(self):
        conn = database.init_db(":memory:")
        profile = {
            "ticker": "AAA", "name": "AAA", "quote_type": "EQUITY",
            "currency": "USD", "financial_currency": None,
            "shares_outstanding": 10.0, "market_cap": None,
            "current_price": 50.0,
        }
        fin_rows = [{
            "ticker": "AAA", "fiscal_date": "2020-12-31",
            "report_type": "10-K", "net_income": 100.0,
            "shareholders_equity": 500.0,
        }]
        points = [("2021-01-15", 100.0), ("2021-01-22", 110.0)]
        try:
            with (
                mock.patch.object(reporter.futu_source, "fetch_profile",
                                  return_value=profile),
                mock.patch.object(reporter.futu_source, "fetch_financials",
                                  return_value=fin_rows),
                mock.patch.object(reporter.futu_source, "available",
                                  return_value=True),
                mock.patch.object(reporter.futu_source,
                                  "fetch_price_history",
                                  return_value=points) as price_history,
                mock.patch.object(reporter.fetcher, "fetch_earnings",
                                  return_value=[]),
                mock.patch.object(reporter.fetcher, "is_non_equity_symbol",
                                  return_value=False),
                mock.patch.object(reporter.fetcher, "load_non_equity",
                                  return_value=set()),
            ):
                reporter.update_ticker(conn, "AAA", risk_free_rate=0.04)
                conn.commit()
            price_history.assert_called_once_with("AAA")
            rows = database.get_historical_valuation(conn, "AAA")
            dates = {r["date"] for r in rows}
            # Two backfilled weekly points plus today's snapshot.
            self.assertIn("2021-01-15", dates)
            self.assertIn("2021-01-22", dates)
            self.assertGreaterEqual(len(rows), 3)
            # Backfilled P/E uses the as-of annual row: mcap 1000 / 100.
            by_date = {r["date"]: r for r in rows}
            self.assertAlmostEqual(by_date["2021-01-15"]["pe_ratio"], 10.0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
