"""Tests for fundamentals.moat_scorer: cagr, metrics, and the moat rubric.

Run with: bin/python -m pytest tests/ -v
No network access required; all fixtures are synthetic dicts.
"""

import unittest

from fundamentals.moat_scorer import cagr, compute_moat_metrics, moat_score


def annual(fiscal_date, **values):
    row = {"ticker": "TEST", "fiscal_date": fiscal_date, "report_type": "10-K"}
    row.update(values)
    return row


class TestCagr(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(cagr(121.0, 100.0, 2), 0.10)
        self.assertAlmostEqual(cagr(133.1, 100.0, 3), 0.10, places=5)

    def test_none_cases(self):
        self.assertIsNone(cagr(None, 100.0, 2))
        self.assertIsNone(cagr(121.0, None, 2))
        self.assertIsNone(cagr(121.0, 0.0, 2))
        self.assertIsNone(cagr(121.0, -5.0, 2))
        self.assertIsNone(cagr(-1.0, 100.0, 2))
        self.assertIsNone(cagr(121.0, 100.0, 0))


class TestComputeMoatMetrics(unittest.TestCase):
    def test_cagr_over_available_gaps(self):
        rows = [
            annual("2024-12-31", revenue=133.1, eps=1.331, free_cash_flow=13.31,
                   gross_profit=66.55, operating_income=39.93),
            annual("2023-12-31", revenue=121.0, eps=1.21, free_cash_flow=12.1,
                   gross_profit=60.5, operating_income=36.3),
            annual("2022-12-31", revenue=110.0, eps=1.10, free_cash_flow=11.0,
                   gross_profit=55.0, operating_income=33.0),
            annual("2021-12-31", revenue=100.0, eps=1.00, free_cash_flow=10.0,
                   gross_profit=50.0, operating_income=30.0),
        ]
        metrics = compute_moat_metrics(rows)
        self.assertAlmostEqual(metrics["revenue_cagr_3yr"], 0.10, places=5)
        # Only 4 annual rows -> 5yr request also resolves over 3 gaps.
        self.assertAlmostEqual(metrics["revenue_cagr_5yr"], 0.10, places=5)
        self.assertAlmostEqual(metrics["eps_cagr_3yr"], 0.10, places=5)
        self.assertAlmostEqual(metrics["fcf_cagr_3yr"], 0.10, places=5)
        self.assertAlmostEqual(metrics["gross_margin_5yr_avg"], 0.50)
        self.assertAlmostEqual(metrics["operating_margin_5yr_avg"], 0.30)

    def test_cagr_none_with_fewer_than_three_annual_rows(self):
        rows = [
            annual("2024-12-31", revenue=121.0),
            annual("2023-12-31", revenue=100.0),
        ]
        metrics = compute_moat_metrics(rows)
        self.assertIsNone(metrics["revenue_cagr_3yr"])

    def test_ttm_and_balance_metrics(self):
        rows = [
            {"ticker": "T", "fiscal_date": "2025-12-31", "report_type": "10-Q",
             "revenue": 100.0, "gross_profit": 60.0, "operating_income": 30.0,
             "net_income": 25.0, "interest_expense": 5.0,
             "shareholders_equity": 200.0, "total_assets": 400.0,
             "total_debt": 50.0, "cash_and_equivalents": 10.0,
             "current_assets": 120.0, "current_liabilities": 60.0},
        ]
        metrics = compute_moat_metrics(rows)
        self.assertAlmostEqual(metrics["gross_margin"], 0.60)
        self.assertAlmostEqual(metrics["net_margin"], 0.25)
        self.assertAlmostEqual(metrics["roe"], 0.125)
        self.assertAlmostEqual(metrics["roa"], 0.0625)
        # NOPAT = 30 * 0.79 = 23.7; IC = 200 + 50 - 10 = 240
        self.assertAlmostEqual(metrics["roic"], 23.7 / 240)
        self.assertAlmostEqual(metrics["debt_to_equity"], 0.25)
        self.assertAlmostEqual(metrics["interest_coverage"], 6.0)
        self.assertAlmostEqual(metrics["current_ratio"], 2.0)
        self.assertEqual(metrics["fiscal_date"], "2025-12-31")


def score_of(**metrics):
    """moat_score with only the given components present."""
    return moat_score(metrics)


class TestMoatScoreBands(unittest.TestCase):
    def breakdown(self, **metrics):
        return moat_score(metrics)[2]

    def test_pricing_power_bands(self):
        bp = self.breakdown
        self.assertEqual(bp(gross_margin=0.51)["pricing_power"], 25)
        self.assertEqual(bp(gross_margin=0.45)["pricing_power"], 20)
        self.assertEqual(bp(gross_margin=0.35)["pricing_power"], 15)
        self.assertEqual(bp(gross_margin=0.25)["pricing_power"], 10)
        self.assertEqual(bp(gross_margin=0.10)["pricing_power"], 5)

    def test_capital_efficiency_bands(self):
        bp = self.breakdown
        self.assertEqual(bp(roic=0.21)["capital_efficiency"], 25)
        self.assertEqual(bp(roic=0.17)["capital_efficiency"], 20)
        self.assertEqual(bp(roic=0.12)["capital_efficiency"], 15)
        self.assertEqual(bp(roic=0.07)["capital_efficiency"], 10)
        self.assertEqual(bp(roic=0.03)["capital_efficiency"], 0)

    def test_profitability_bands(self):
        bp = self.breakdown
        self.assertEqual(bp(roe=0.21)["profitability"], 20)
        self.assertEqual(bp(roe=0.17)["profitability"], 15)
        self.assertEqual(bp(roe=0.12)["profitability"], 10)
        self.assertEqual(bp(roe=0.07)["profitability"], 5)
        self.assertEqual(bp(roe=0.02)["profitability"], 0)

    def test_growth_consistency_bands(self):
        bp = self.breakdown
        self.assertEqual(bp(revenue_cagr_5yr=0.16)["growth_consistency"], 15)
        self.assertEqual(bp(revenue_cagr_5yr=0.12)["growth_consistency"], 12)
        self.assertEqual(bp(revenue_cagr_5yr=0.07)["growth_consistency"], 8)
        self.assertEqual(bp(revenue_cagr_5yr=0.02)["growth_consistency"], 4)
        self.assertEqual(bp(revenue_cagr_5yr=0.0)["growth_consistency"], 0)
        self.assertEqual(bp(revenue_cagr_5yr=-0.05)["growth_consistency"], 0)

    def test_financial_strength_bands(self):
        bp = self.breakdown
        self.assertEqual(bp(debt_to_equity=0.2)["financial_strength"], 15)
        self.assertEqual(bp(debt_to_equity=0.5)["financial_strength"], 12)
        self.assertEqual(bp(debt_to_equity=0.8)["financial_strength"], 8)
        self.assertEqual(bp(debt_to_equity=1.2)["financial_strength"], 4)
        self.assertEqual(bp(debt_to_equity=2.0)["financial_strength"], 0)
        # Negative equity (negative D/E) scores 0, not the top band.
        self.assertEqual(bp(debt_to_equity=-0.5)["financial_strength"], 0)


class TestMoatScoreAssembly(unittest.TestCase):
    def test_full_score_and_rating(self):
        metrics = {
            "gross_margin": 0.51, "roic": 0.21, "roe": 0.21,
            "revenue_cagr_5yr": 0.16, "debt_to_equity": 0.2,
        }
        score, rating, _ = moat_score(metrics)
        self.assertEqual(score, 100)
        self.assertEqual(rating, "Wide Moat")

    def test_rating_bands(self):
        cases = [
            ({"gross_margin": 0.51, "roic": 0.21, "roe": 0.21,
              "revenue_cagr_5yr": 0.16, "debt_to_equity": 2.0}, 85, "Wide Moat"),
            ({"gross_margin": 0.45, "roic": 0.12, "roe": 0.12,
              "revenue_cagr_5yr": 0.12, "debt_to_equity": 0.8}, 65, "Narrow Moat"),
            ({"gross_margin": 0.25, "roic": 0.07, "roe": 0.12,
              "revenue_cagr_5yr": 0.16, "debt_to_equity": 2.0}, 45, "Weak Moat"),
            ({"gross_margin": 0.10, "roic": 0.03, "roe": 0.02,
              "revenue_cagr_5yr": -0.01, "debt_to_equity": 0.2}, 20, "No Moat"),
        ]
        for metrics, expected_score, expected_rating in cases:
            score, rating, _ = moat_score(metrics)
            self.assertEqual(score, expected_score, metrics)
            self.assertEqual(rating, expected_rating, metrics)

    def test_missing_components_rescale(self):
        # Only pricing power present, at max -> rescaled to 100.
        score, rating, breakdown = moat_score({"gross_margin": 0.51})
        self.assertEqual(score, 100)
        self.assertEqual(rating, "Wide Moat")
        self.assertIsNone(breakdown["capital_efficiency"])

    def test_rescale_partial(self):
        # gross 0.51 -> 25/25, roe 0.21 -> 20/20: earned 45/45 -> 100.
        score, _, _ = moat_score({"gross_margin": 0.51, "roe": 0.21})
        self.assertEqual(score, 100)
        # gross 0.10 -> 5/25, roe 0.02 -> 0/20: earned 5/45 -> 11.
        score, _, _ = moat_score({"gross_margin": 0.10, "roe": 0.02})
        self.assertEqual(score, 11)

    def test_all_missing_scores_zero(self):
        score, rating, _ = moat_score({})
        self.assertEqual(score, 0)
        self.assertEqual(rating, "No Moat")


if __name__ == "__main__":
    unittest.main()
