"""Tests for fundamentals.database: schema and upsert/get round-trips.

Run with: bin/python -m pytest tests/ -v
No network access required.
"""

import unittest

from fundamentals import database


def make_fin_row(**overrides):
    row = {
        "ticker": "TEST",
        "fiscal_date": "2025-12-31",
        "report_type": "10-Q",
        "revenue": 1_000.0,
        "gross_profit": 500.0,
        "operating_income": 300.0,
        "net_income": 250.0,
        "eps": 2.5,
        "total_assets": 8_000.0,
        "total_liabilities": 3_000.0,
        "shareholders_equity": 5_000.0,
        "total_debt": 2_000.0,
        "cash_and_equivalents": 1_000.0,
        "operating_cash_flow": 400.0,
        "free_cash_flow": 300.0,
        "capital_expenditure": -100.0,
        "shares_outstanding": 100.0,
        "depreciation_amortization": 50.0,
        "interest_expense": 20.0,
        "current_assets": 3_000.0,
        "current_liabilities": 1_500.0,
    }
    row.update(overrides)
    return row


class TestSchema(unittest.TestCase):
    def setUp(self):
        self.conn = database.init_db(":memory:")

    def tearDown(self):
        self.conn.close()

    def columns(self, table):
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def test_all_eight_tables_created(self):
        tables = {
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertEqual(
            tables,
            {
                "company_profiles", "quarterly_financials", "valuation_ratios",
                "historical_valuation", "earnings_history", "moat_metrics",
                "dcf_valuation", "peer_comparison",
            },
        )

    def test_quarterly_financials_extra_columns(self):
        cols = self.columns("quarterly_financials")
        for extra in ("depreciation_amortization", "interest_expense",
                      "current_assets", "current_liabilities"):
            self.assertIn(extra, cols)

    def test_moat_metrics_score_cache_columns(self):
        cols = self.columns("moat_metrics")
        self.assertIn("moat_score", cols)
        self.assertIn("moat_rating", cols)

    def test_dcf_valuation_key_columns(self):
        cols = self.columns("dcf_valuation")
        for key in ("ticker", "valuation_date", "intrinsic_value_per_share",
                    "margin_of_safety", "mos_label"):
            self.assertIn(key, cols)


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.conn = database.init_db(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_quarterly_financials_round_trip(self):
        rows = [
            make_fin_row(fiscal_date="2025-12-31"),
            make_fin_row(fiscal_date="2025-09-30", report_type="10-Q",
                         revenue=900.0),
            make_fin_row(fiscal_date="2024-12-31", report_type="10-K",
                         revenue=3_500.0),
        ]
        self.assertEqual(database.upsert_quarterly_financials(self.conn, rows), 3)
        self.conn.commit()

        stored = database.get_quarterly_financials(self.conn, "TEST")
        self.assertEqual([r["fiscal_date"] for r in stored],
                         ["2025-12-31", "2025-09-30", "2024-12-31"])
        self.assertEqual(stored[0]["revenue"], 1_000.0)
        self.assertEqual(stored[0]["interest_expense"], 20.0)
        self.assertIsNotNone(stored[0]["updated_at"])

        annual = database.get_quarterly_financials(self.conn, "TEST", "10-K")
        self.assertEqual(len(annual), 1)
        self.assertEqual(annual[0]["report_type"], "10-K")

    def test_quarterly_financials_upsert_is_idempotent(self):
        database.upsert_quarterly_financials(self.conn, [make_fin_row()])
        database.upsert_quarterly_financials(
            self.conn, [make_fin_row(revenue=2_000.0)]
        )
        self.conn.commit()
        stored = database.get_quarterly_financials(self.conn, "TEST")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["revenue"], 2_000.0)

    def test_upsert_replaces_same_quarter_from_other_source(self):
        # Futu and yfinance fiscal period-ends differ by a few days; the
        # upsert must replace the same quarter, not store both.
        database.upsert_quarterly_financials(self.conn, [make_fin_row()])
        database.upsert_quarterly_financials(
            self.conn, [make_fin_row(fiscal_date="2025-12-26", revenue=3_000.0)]
        )
        self.conn.commit()
        stored = database.get_quarterly_financials(self.conn, "TEST")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["fiscal_date"], "2025-12-26")
        self.assertEqual(stored[0]["revenue"], 3_000.0)

    def test_upsert_keeps_annual_and_quarterly_same_month(self):
        database.upsert_quarterly_financials(
            self.conn, [make_fin_row(report_type="10-K",
                                     fiscal_date="2025-12-26")]
        )
        database.upsert_quarterly_financials(self.conn, [make_fin_row()])
        self.conn.commit()
        stored = database.get_quarterly_financials(self.conn, "TEST")
        self.assertEqual(len(stored), 2)

    def test_dcf_valuation_round_trip(self):
        valuation = {
            "ticker": "TEST",
            "valuation_date": "2026-07-23",
            "current_price": 100.0,
            "fcf_per_share_ttm": 10.0,
            "fcf_growth_rate_5yr": 0.10,
            "fcf_growth_rate_terminal": 0.025,
            "discount_rate": 0.10,
            "projected_fcf_5yr": 61.05,
            "terminal_value": 220.10,
            "intrinsic_value": 18_666.0,
            "intrinsic_value_per_share": 186.66,
            "upside_downside_pct": 86.66,
            "margin_of_safety": 86.66,
            "mos_label": "Strong Buy",
        }
        database.upsert_dcf_valuation(self.conn, valuation)
        self.conn.commit()

        latest = database.get_latest_dcf_valuation(self.conn, "TEST")
        self.assertEqual(latest["intrinsic_value_per_share"], 186.66)
        self.assertEqual(latest["mos_label"], "Strong Buy")

        # Idempotent: same primary key replaces, does not duplicate.
        valuation["intrinsic_value_per_share"] = 190.0
        database.upsert_dcf_valuation(self.conn, valuation)
        self.conn.commit()
        rows = database.get_dcf_valuation(self.conn, "TEST")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["intrinsic_value_per_share"], 190.0)


if __name__ == "__main__":
    unittest.main()
