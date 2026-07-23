"""Tests for ADR FX conversion of financial statement rows."""

import unittest

from fundamentals import fetcher


def _row(**overrides):
    row = {
        "ticker": "TSM", "fiscal_date": "2025-12-31", "report_type": "10-K",
        "revenue": 1000.0, "gross_profit": 500.0, "operating_income": 400.0,
        "net_income": 300.0, "eps": 2.5, "total_assets": 5000.0,
        "total_liabilities": 2000.0, "shareholders_equity": 3000.0,
        "total_debt": 800.0, "cash_and_equivalents": 600.0,
        "operating_cash_flow": 700.0, "free_cash_flow": 550.0,
        "capital_expenditure": -150.0, "shares_outstanding": 25_000_000_000.0,
        "depreciation_amortization": 100.0, "interest_expense": 10.0,
        "current_assets": 1200.0, "current_liabilities": 900.0,
    }
    row.update(overrides)
    return row


class ApplyFxRateTest(unittest.TestCase):
    def test_monetary_fields_converted(self):
        (row,) = fetcher.apply_fx_rate([_row()], 0.031)
        self.assertAlmostEqual(row["revenue"], 31.0)
        self.assertAlmostEqual(row["net_income"], 9.3)
        self.assertAlmostEqual(row["eps"], 2.5 * 0.031)
        self.assertAlmostEqual(row["capital_expenditure"], -150.0 * 0.031)

    def test_shares_and_metadata_untouched(self):
        (row,) = fetcher.apply_fx_rate([_row()], 0.031)
        self.assertEqual(row["shares_outstanding"], 25_000_000_000.0)
        self.assertEqual(row["fiscal_date"], "2025-12-31")
        self.assertEqual(row["report_type"], "10-K")
        self.assertEqual(row["ticker"], "TSM")

    def test_none_stays_none(self):
        (row,) = fetcher.apply_fx_rate([_row(revenue=None)], 0.031)
        self.assertIsNone(row["revenue"])

    def test_rate_one_is_identity_and_same_object(self):
        rows = [_row()]
        self.assertIs(fetcher.apply_fx_rate(rows, 1.0), rows)

    def test_original_rows_not_mutated(self):
        original = _row()
        fetcher.apply_fx_rate([original], 2.0)
        self.assertEqual(original["revenue"], 1000.0)


if __name__ == "__main__":
    unittest.main()
