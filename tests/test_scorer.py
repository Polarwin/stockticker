"""Tests for fundamentals.scorer: percentile points and the composite score.

Run with: bin/python -m pytest tests/ -v
No network access required; all fixtures are synthetic.
"""

import unittest

from fundamentals.scorer import fundamental_score, percentile_points


class TestPercentilePoints(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(percentile_points(19), 6)
        self.assertEqual(percentile_points(20), 5)
        self.assertEqual(percentile_points(39), 5)
        self.assertEqual(percentile_points(40), 4)
        self.assertEqual(percentile_points(59), 4)
        self.assertEqual(percentile_points(60), 2)
        self.assertEqual(percentile_points(79), 2)
        self.assertEqual(percentile_points(80), 0)
        self.assertEqual(percentile_points(95), 0)

    def test_none(self):
        self.assertIsNone(percentile_points(None))


def score(**overrides):
    inputs = {
        "valuation_percentiles": {},
        "moat_score_val": None,
        "revenue_cagr_3yr": None,
        "debt_to_equity": None,
        "surprises": [],
    }
    inputs.update(overrides)
    return fundamental_score(**inputs)


class TestFundamentalScore(unittest.TestCase):
    def test_valuation_full_set(self):
        percentiles = {key: 10 for key in
                       ("pe_ratio", "pb_ratio", "ps_ratio", "p_fcf_ratio",
                        "ev_ebitda")}
        self.assertEqual(score(valuation_percentiles=percentiles)["valuation"],
                         30)

    def test_valuation_rescales_over_available(self):
        # Two ratios at percentile 10 -> 6+6 of 12 max -> rescaled to 30.
        percentiles = {"pe_ratio": 10, "pb_ratio": 10, "ps_ratio": None,
                       "p_fcf_ratio": None, "ev_ebitda": None}
        self.assertEqual(score(valuation_percentiles=percentiles)["valuation"],
                         30)
        # 6 + 4 = 10 of 12 max -> 25.
        percentiles = {"pe_ratio": 10, "pb_ratio": 50}
        self.assertEqual(score(valuation_percentiles=percentiles)["valuation"],
                         25)

    def test_valuation_none_available(self):
        self.assertEqual(score()["valuation"], 0)

    def test_moat_leg(self):
        self.assertEqual(score(moat_score_val=80)["moat"], 20)
        self.assertEqual(score(moat_score_val=100)["moat"], 25)
        self.assertEqual(score()["moat"], 0)

    def test_growth_bands(self):
        self.assertEqual(score(revenue_cagr_3yr=0.16)["growth"], 20)
        self.assertEqual(score(revenue_cagr_3yr=0.12)["growth"], 15)
        self.assertEqual(score(revenue_cagr_3yr=0.07)["growth"], 10)
        self.assertEqual(score(revenue_cagr_3yr=0.0)["growth"], 0)
        self.assertEqual(score(revenue_cagr_3yr=-0.05)["growth"], 0)
        self.assertEqual(score()["growth"], 0)

    def test_stability_bands(self):
        self.assertEqual(score(debt_to_equity=0.4)["stability"], 15)
        self.assertEqual(score(debt_to_equity=0.7)["stability"], 10)
        self.assertEqual(score(debt_to_equity=1.5)["stability"], 0)
        self.assertEqual(score()["stability"], 0)

    def test_earnings_quality_bands(self):
        self.assertEqual(score(surprises=[1.0, 2.0, 3.0, 4.0])
                         ["earnings_quality"], 10)
        self.assertEqual(score(surprises=[1.0, -1.0, 2.0, 3.0])
                         ["earnings_quality"], 7)
        self.assertEqual(score(surprises=[1.0, 2.0, -1.0, -1.0])
                         ["earnings_quality"], 5)
        # None entries count as not-beat.
        self.assertEqual(score(surprises=[1.0, -1.0, -1.0, None])
                         ["earnings_quality"], 2)
        self.assertEqual(score(surprises=[-1.0, None, 0.0, -2.0])
                         ["earnings_quality"], 0)
        # Only the 4 most recent entries (head of the list) are considered.
        self.assertEqual(score(surprises=[-1.0, -1.0, -1.0, -1.0, 5.0, 5.0])
                         ["earnings_quality"], 0)

    def test_total_assembly(self):
        percentiles = {"pe_ratio": 10, "pb_ratio": 30, "ps_ratio": 50,
                       "p_fcf_ratio": 70, "ev_ebitda": 90}
        result = score(
            valuation_percentiles=percentiles,
            moat_score_val=80,
            revenue_cagr_3yr=0.12,
            debt_to_equity=0.4,
            surprises=[1.0, 2.0, -1.0, 3.0],
        )
        # valuation: 6+5+4+2+0 = 17 of 30 -> 17; moat 20; growth 15;
        # stability 15; earnings_quality 7.
        self.assertEqual(result["valuation"], 17)
        self.assertEqual(result["moat"], 20)
        self.assertEqual(result["growth"], 15)
        self.assertEqual(result["stability"], 15)
        self.assertEqual(result["earnings_quality"], 7)
        self.assertEqual(result["total"], 17 + 20 + 15 + 15 + 7)


if __name__ == "__main__":
    unittest.main()
