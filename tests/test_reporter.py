"""Tests for fundamentals.reporter: dashboard, Telegram alert, JSON, one-liners.

Run with: bin/python -m pytest tests/ -v
No network access required; all fixtures are synthetic dicts / :memory: DBs.
"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from fundamentals import database, reporter


def make_result(**overrides):
    result = {
        "ticker": "TEST",
        "name": "Test Co",
        "sector": "Technology",
        "industry": "Semiconductors",
        "country": "US",
        "market_cap": 1e9,
        "employees": 100,
        "price": 100.0,
        "ratios": {
            "pe_ratio": 24.5, "forward_pe": 18.2, "pb_ratio": 2.0,
            "ps_ratio": 2.5, "p_fcf_ratio": 18.5, "ev_ebitda": 12.8,
        },
        "history_percentiles": {"pe_ratio": 35.0, "pb_ratio": 50.0},
        "sector_percentile": 42.0,
        "moat": {
            "gross_margin": 0.52, "roic": 0.21, "roe": 0.22,
            "revenue_cagr_5yr": 0.16, "debt_to_equity": 0.2,
        },
        "moat_score": 82,
        "moat_rating": "Wide Moat",
        "moat_breakdown": {
            "pricing_power": 25, "capital_efficiency": 25,
            "profitability": 20, "growth_consistency": 15,
            "financial_strength": 15,
        },
        "dcf": {
            "intrinsic_value_per_share": 520.0, "current_price": 441.0,
            "upside_downside_pct": 17.9, "mos_label": "Buy",
            "fcf_growth_rate_5yr": 0.10, "discount_rate": 0.10,
        },
        "sensitivity": {
            "growth_rates": [0.06, 0.08, 0.10, 0.12, 0.14],
            "discount_rates": [0.08, 0.09, 0.10, 0.11, 0.12],
            "values": [[100 + i + j for j in range(5)] for i in range(5)],
        },
        "fundamental_score": {
            "total": 78, "valuation": 25, "moat": 20, "growth": 15,
            "stability": 15, "earnings_quality": 3,
        },
        "surprises": [5.2, 3.1, -1.0, 2.0],
        "peer_comparison": {
            "pe_ratio": {"ticker": 24.5, "median": 32.3,
                         "premium_discount_pct": -24.0},
        },
        "valuation_history": [
            {"date": "2026-07-22", "pe_ratio": 23.0, "sector_median_pe": 31.0},
            {"date": "2026-07-23", "pe_ratio": 24.5, "sector_median_pe": 32.3},
        ],
    }
    result.update(overrides)
    return result


SPARSE_RESULT = {
    "ticker": "EMPTY", "name": None, "sector": None, "industry": None,
    "country": None, "market_cap": None, "employees": None, "price": None,
    "ratios": {}, "history_percentiles": {}, "sector_percentile": None,
    "moat": {}, "moat_score": None, "moat_rating": None, "moat_breakdown": {},
    "dcf": None, "sensitivity": None,
    "fundamental_score": {"total": 0, "valuation": 0, "moat": 0, "growth": 0,
                          "stability": 0, "earnings_quality": 0},
    "surprises": [], "peer_comparison": {}, "valuation_history": [],
}


class TestRenderDashboard(unittest.TestCase):
    def test_sections_and_content(self):
        page = reporter.render_dashboard([make_result(), SPARSE_RESULT])
        self.assertIn("Portfolio Overview", page)
        self.assertIn("Peer Comparison", page)
        self.assertIn("TEST", page)
        self.assertIn("Test Co", page)
        self.assertIn("Wide Moat", page)
        self.assertIn("N/A", page)  # sparse result renders N/A, not a crash
        self.assertIn("Fundamental Score", page)
        self.assertIn("Guidance", page)

    def test_sensitivity_grid_renders_5x5(self):
        page = reporter.render_dashboard([make_result()])
        # Header row + 5 growth rows inside the sensitivity <details>.
        self.assertIn("DCF sensitivity (5x5)", page)
        for growth in ("6.0%", "8.0%", "10.0%", "12.0%", "14.0%"):
            self.assertIn(growth, page)
        self.assertIn('class="base"', page)  # highlighted base cell

    def test_earnings_calendar_section(self):
        calendar = [{"ticker": "TEST", "date": "2026-07-30", "eps_estimate": 1.42}]
        page = reporter.render_dashboard([make_result()], calendar)
        self.assertIn("Earnings Calendar", page)
        self.assertIn("2026-07-30", page)
        self.assertNotIn(
            "Earnings Calendar", reporter.render_dashboard([make_result()])
        )

    def test_pe_chart_with_and_without_history(self):
        page = reporter.render_dashboard([make_result()])
        self.assertIn("<svg", page)  # 2 points -> chart renders
        sparse_page = reporter.render_dashboard([SPARSE_RESULT])
        self.assertIn("builds up as daily snapshots accumulate", sparse_page)

    def test_pe_chart_clips_extreme_values_without_hiding_them_from_data(self):
        history = [
            {"date": f"2026-01-{day:02d}", "pe_ratio": 20.0 + day,
             "sector_median_pe": 25.0}
            for day in range(1, 31)
        ]
        history[-1]["pe_ratio"] = 10_000.0
        page = reporter.render_dashboard([
            make_result(valuation_history=history)
        ])
        self.assertIn("extreme observation", page)

    def test_dashboard_names_both_data_sources(self):
        page = reporter.render_dashboard([make_result()])
        self.assertIn("Futu OpenD + yfinance data", page)


class TestTelegramAlert(unittest.TestCase):
    def test_alert_content_and_limit(self):
        message = reporter.build_telegram_alert(make_result())
        self.assertIn("TEST", message)
        self.assertIn("Fundamental Score: 78/100", message)
        self.assertIn("Wide Moat", message)
        self.assertIn("cheap", message)
        self.assertLess(len(message), reporter.TELEGRAM_LIMIT)

    def test_alert_with_sparse_result(self):
        message = reporter.build_telegram_alert(SPARSE_RESULT)
        self.assertIn("EMPTY", message)
        self.assertIn("N/A", message)
        self.assertLess(len(message), reporter.TELEGRAM_LIMIT)

    def test_elide_caps_long_messages(self):
        result = make_result(surprises=[9.9] * 4)
        long_message = reporter.build_telegram_alert(result)
        capped = reporter._elide(long_message + "\n" + "x" * 5000)
        self.assertLessEqual(len(capped), reporter.TELEGRAM_LIMIT)


class TestJsonReports(unittest.TestCase):
    def test_writes_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            fundamental_path, dcf_path = reporter.write_json_reports(
                [make_result(), SPARSE_RESULT], tmp
            )
            self.assertTrue(fundamental_path.exists())
            self.assertTrue(dcf_path.exists())
            self.assertEqual(fundamental_path.parent, Path(tmp))
            today = date.today().isoformat()
            self.assertIn(f"fundamental_report_{today}", fundamental_path.name)
            self.assertIn(f"dcf_report_{today}", dcf_path.name)

            full = json.loads(fundamental_path.read_text())
            self.assertEqual(len(full), 2)
            self.assertEqual(full[0]["ticker"], "TEST")
            dcf = json.loads(dcf_path.read_text())
            self.assertIn("TEST", dcf)
            self.assertIn("sensitivity", dcf["TEST"])
            self.assertNotIn("EMPTY", dcf)  # no DCF -> omitted


def seed_db(conn):
    """One ticker with enough rows for load_results / one-liners."""
    database.upsert_company_profile(conn, {
        "ticker": "SEED", "name": "Seed Co", "sector": "Technology",
        "industry": "Software",
    })
    database.upsert_quarterly_financials(conn, [{
        "ticker": "SEED", "fiscal_date": "2025-12-31", "report_type": "10-Q",
        "revenue": 100.0, "free_cash_flow": 30.0, "shares_outstanding": 10.0,
    }])
    database.upsert_valuation_ratios(conn, {
        "ticker": "SEED", "fiscal_date": "2025-12-31", "pe_ratio": 20.0,
    })
    database.upsert_moat_metrics(conn, {
        "ticker": "SEED", "fiscal_date": "2025-12-31", "gross_margin": 0.55,
        "moat_score": 85, "moat_rating": "Wide Moat",
    })
    database.upsert_dcf_valuation(conn, {
        "ticker": "SEED", "valuation_date": "2026-07-23",
        "current_price": 100.0, "intrinsic_value_per_share": 120.0,
        "upside_downside_pct": 20.0, "mos_label": "Buy",
        "fcf_growth_rate_5yr": 0.10, "discount_rate": 0.10,
    })
    conn.commit()


class TestOneLiners(unittest.TestCase):
    def test_seeded_ticker(self):
        conn = database.init_db(":memory:")
        try:
            seed_db(conn)
            liners = reporter.fundamental_one_liners(conn, ["SEED"])
        finally:
            conn.close()
        self.assertIn("SEED", liners)
        self.assertIn("SEED — Fund:", liners["SEED"])
        self.assertIn("Moat: 85 Wide", liners["SEED"])
        self.assertIn("DCF: +20.0%", liners["SEED"])

    def test_missing_ticker_and_all_stored(self):
        conn = database.init_db(":memory:")
        try:
            seed_db(conn)
            self.assertEqual(reporter.fundamental_one_liners(conn, ["NOPE"]), {})
            # tickers=None covers every stored profile.
            self.assertIn("SEED", reporter.fundamental_one_liners(conn))
        finally:
            conn.close()


class TestLoadResults(unittest.TestCase):
    def test_rebuilds_result_dict_from_db(self):
        conn = database.init_db(":memory:")
        try:
            seed_db(conn)
            results = reporter.load_results(conn, ["SEED", "NOPE"])
        finally:
            conn.close()
        self.assertEqual(len(results), 1)  # NOPE has no profile -> skipped
        result = results[0]
        self.assertEqual(result["ticker"], "SEED")
        self.assertEqual(result["name"], "Seed Co")
        self.assertEqual(result["moat_score"], 85)
        self.assertEqual(result["ratios"]["pe_ratio"], 20.0)
        self.assertEqual(result["price"], 100.0)  # from the DCF row
        self.assertIn("total", result["fundamental_score"])

    def test_price_falls_back_to_market_cap_and_newest_available_shares(self):
        conn = database.init_db(":memory:")
        try:
            database.upsert_company_profile(conn, {
                "ticker": "SEED", "name": "Seed Co",
                "market_cap": 1_000.0,
            })
            database.upsert_quarterly_financials(conn, [
                {
                    "ticker": "SEED", "fiscal_date": "2026-06-30",
                    "report_type": "10-Q", "revenue": 100.0,
                    "shares_outstanding": None,
                },
                {
                    "ticker": "SEED", "fiscal_date": "2026-03-31",
                    "report_type": "10-Q", "revenue": 90.0,
                    "shares_outstanding": 10.0,
                },
            ])
            results = reporter.load_results(conn, ["SEED"])
        finally:
            conn.close()
        self.assertEqual(results[0]["price"], 100.0)


class TestUpdateAllTTL(unittest.TestCase):
    def _fake_result(self, ticker):
        result = make_result(ticker=ticker)
        return result

    def _run_update_all(self, conn, tickers, max_age_days):
        with (
            mock.patch.object(
                reporter.fetcher, "fetch_risk_free_rate", return_value=0.04
            ),
            mock.patch.object(reporter, "update_ticker") as update_ticker,
        ):
            update_ticker.side_effect = lambda conn, t, **kw: self._fake_result(t)
            results = reporter.update_all(
                conn, tickers, max_age_days=max_age_days
            )
        return results, update_ticker

    def test_fresh_profile_skipped_stale_refetched(self):
        conn = database.init_db(":memory:")
        try:
            database.upsert_company_profile(conn, {
                "ticker": "FRESH", "name": "Fresh Co",
                "updated_at": date.today().isoformat(),
            })
            database.upsert_company_profile(conn, {
                "ticker": "STALE", "name": "Stale Co",
                "updated_at": "2020-01-01",
            })
            conn.commit()
            results, update_ticker = self._run_update_all(
                conn, ["FRESH", "STALE"], max_age_days=7
            )
        finally:
            conn.close()
        self.assertEqual([r["ticker"] for r in results], ["STALE"])
        update_ticker.assert_called_once()

    def test_no_ttl_refetches_everything(self):
        conn = database.init_db(":memory:")
        try:
            database.upsert_company_profile(conn, {
                "ticker": "FRESH", "name": "Fresh Co",
                "updated_at": date.today().isoformat(),
            })
            conn.commit()
            results, update_ticker = self._run_update_all(
                conn, ["FRESH"], max_age_days=0
            )
        finally:
            conn.close()
        self.assertEqual(len(results), 1)
        update_ticker.assert_called_once()

    def test_is_profile_fresh(self):
        conn = database.init_db(":memory:")
        try:
            database.upsert_company_profile(conn, {
                "ticker": "NEW", "name": "New Co",
            })  # updated_at defaults to today
            database.upsert_company_profile(conn, {
                "ticker": "OLD", "name": "Old Co", "updated_at": "2020-01-01",
            })
            conn.commit()
            self.assertTrue(database.is_profile_fresh(conn, "NEW", 7))
            self.assertFalse(database.is_profile_fresh(conn, "OLD", 7))
            self.assertFalse(database.is_profile_fresh(conn, "MISSING", 7))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
