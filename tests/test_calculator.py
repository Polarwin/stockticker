"""Tests for fundamentals.calculator: ttm and valuation ratios.

Run with: bin/python -m pytest tests/ -v
No network access required; all fixtures are synthetic dicts.
"""

import unittest

from fundamentals.calculator import compute_valuation_ratios, latest, ttm

PROFILE = {
    "ticker": "TEST",
    "market_cap": 10_000_000_000.0,
    "forward_eps": 5.0,
    "dividend_rate": 4.0,
    "shares_outstanding": 100_000_000.0,
    "current_price": 100.0,
    "beta": 1.2,
}


def make_quarter(fiscal_date, **overrides):
    row = {
        "ticker": "TEST",
        "fiscal_date": fiscal_date,
        "report_type": "10-Q",
        "revenue": 1_000_000_000.0,
        "gross_profit": 500_000_000.0,
        "operating_income": 300_000_000.0,
        "net_income": 250_000_000.0,
        "eps": 2.5,
        "total_assets": 8_000_000_000.0,
        "total_liabilities": 3_000_000_000.0,
        "shareholders_equity": 5_000_000_000.0,
        "total_debt": 2_000_000_000.0,
        "cash_and_equivalents": 1_000_000_000.0,
        "operating_cash_flow": 400_000_000.0,
        "free_cash_flow": 300_000_000.0,
        "capital_expenditure": -100_000_000.0,
        "shares_outstanding": 100_000_000.0,
        "depreciation_amortization": 50_000_000.0,
        "interest_expense": 20_000_000.0,
        "current_assets": 3_000_000_000.0,
        "current_liabilities": 1_500_000_000.0,
    }
    row.update(overrides)
    return row


def make_rows():
    return [
        make_quarter("2025-12-31"),
        make_quarter("2025-09-30"),
        make_quarter("2025-06-30"),
        make_quarter("2025-03-31"),
        make_quarter("2024-12-31", report_type="10-K",
                     revenue=3_500_000_000.0),
    ]


class TestTtm(unittest.TestCase):
    def test_sums_newest_four_quarters(self):
        self.assertEqual(ttm(make_rows(), "revenue"), 4_000_000_000.0)
        self.assertEqual(ttm(make_rows(), "net_income"), 1_000_000_000.0)

    def test_partial_quarters_sum_what_exists(self):
        rows = make_rows()[:2]  # only two quarters
        self.assertEqual(ttm(rows, "revenue"), 2_000_000_000.0)

    def test_falls_back_to_annual(self):
        rows = [make_quarter("2024-12-31", report_type="10-K",
                             revenue=3_500_000_000.0)]
        self.assertEqual(ttm(rows, "revenue"), 3_500_000_000.0)

    def test_annual_fallback_only_when_no_quarterly_has_key(self):
        rows = make_rows()
        # Quarters lack free_cash_flow; annual has it -> annual value wins.
        for row in rows[:4]:
            row["free_cash_flow"] = None
        rows[4]["free_cash_flow"] = 999.0
        self.assertEqual(ttm(rows, "free_cash_flow"), 999.0)

    def test_missing_key_returns_none(self):
        self.assertIsNone(ttm(make_rows(), "nonexistent"))
        self.assertIsNone(ttm([], "revenue"))

    def test_latest(self):
        rows = make_rows()
        self.assertEqual(latest(rows)["fiscal_date"], "2025-12-31")
        self.assertIsNone(latest([]))


class TestValuationRatios(unittest.TestCase):
    def test_hand_computed_ratios(self):
        ratios = compute_valuation_ratios(PROFILE, make_rows(), 100.0,
                                          eps_cagr_5yr=0.10)
        self.assertAlmostEqual(ratios["pe_ratio"], 10.0)          # 10e9 / 1e9
        self.assertAlmostEqual(ratios["forward_pe"], 20.0)        # 10e9 / 5e8
        self.assertAlmostEqual(ratios["pb_ratio"], 2.0)           # 10e9 / 5e9
        self.assertAlmostEqual(ratios["ps_ratio"], 2.5)           # 10e9 / 4e9
        self.assertAlmostEqual(ratios["p_fcf_ratio"], 10e9 / 1.2e9)
        # EV = 10e9 + 2e9 - 1e9 = 11e9; EBITDA = 1.2e9 + 0.2e9 = 1.4e9
        self.assertAlmostEqual(ratios["ev_ebitda"], 11e9 / 1.4e9)
        self.assertAlmostEqual(ratios["peg_ratio"], 1.0)          # 10 / (0.10*100)
        self.assertAlmostEqual(ratios["dividend_yield"], 0.04)    # 4*1e8 / 1e10
        self.assertEqual(ratios["fiscal_date"], "2025-12-31")

    def test_negative_net_income_gives_no_pe(self):
        rows = make_rows()
        for row in rows:
            row["net_income"] = -abs(row["net_income"])
        ratios = compute_valuation_ratios(PROFILE, rows, 100.0)
        self.assertIsNone(ratios["pe_ratio"])

    def test_missing_forward_eps_gives_no_forward_pe(self):
        profile = dict(PROFILE, forward_eps=None)
        ratios = compute_valuation_ratios(profile, make_rows(), 100.0)
        self.assertIsNone(ratios["forward_pe"])

    def test_market_cap_falls_back_to_price_times_shares(self):
        profile = dict(PROFILE, market_cap=None)
        ratios = compute_valuation_ratios(profile, make_rows(), 100.0)
        # price 100 x 100M shares = 10e9, same as before.
        self.assertAlmostEqual(ratios["pe_ratio"], 10.0)

    def test_negative_cagr_gives_no_peg(self):
        ratios = compute_valuation_ratios(PROFILE, make_rows(), 100.0,
                                          eps_cagr_5yr=-0.05)
        self.assertIsNone(ratios["peg_ratio"])


if __name__ == "__main__":
    unittest.main()
