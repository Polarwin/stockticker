"""Tests for fundamentals.dcf_valuator: DCF math, clamps, and sensitivity.

Run with: bin/python -m pytest tests/ -v
No network access required; all fixtures are synthetic.
"""

import unittest

from fundamentals.dcf_valuator import (
    compute_dcf,
    dcf_value,
    mos_label,
    net_debt_from_rows,
    sensitivity_grid,
)

FCF_TTM = 1_000_000_000.0
SHARES = 100_000_000.0  # -> fcf_per_share = 10.0


def expected_intrinsic(fcf_per_share, growth, discount, terminal=0.025):
    """Independent replication of the PV formula for cross-checking."""
    pv = 0.0
    fcf = fcf_per_share
    for year in range(1, 6):
        fcf *= 1 + growth
        pv += fcf / (1 + discount) ** year
    tv = fcf * (1 + terminal) / (discount - terminal)
    return pv + tv / (1 + discount) ** 5


class TestDcfValue(unittest.TestCase):
    def test_matches_independent_formula(self):
        result = dcf_value(FCF_TTM, SHARES, 0.10, 0.10)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["fcf_per_share_ttm"], 10.0)
        self.assertAlmostEqual(
            result["intrinsic_value_per_share"],
            expected_intrinsic(10.0, 0.10, 0.10),
        )
        projected = [10.0 * 1.1 ** y for y in range(1, 6)]
        self.assertAlmostEqual(result["projected_fcf_5yr"], sum(projected))
        self.assertAlmostEqual(
            result["terminal_value"], projected[-1] * 1.025 / 0.075
        )

    def test_none_cases(self):
        self.assertIsNone(dcf_value(0.0, SHARES, 0.10, 0.10))
        self.assertIsNone(dcf_value(-5.0, SHARES, 0.10, 0.10))
        self.assertIsNone(dcf_value(None, SHARES, 0.10, 0.10))
        self.assertIsNone(dcf_value(FCF_TTM, 0.0, 0.10, 0.10))
        self.assertIsNone(dcf_value(FCF_TTM, None, 0.10, 0.10))
        # discount <= terminal growth: Gordon denominator not positive.
        self.assertIsNone(dcf_value(FCF_TTM, SHARES, 0.10, 0.025))
        self.assertIsNone(dcf_value(FCF_TTM, SHARES, 0.10, 0.02))

    def test_net_debt_bridge(self):
        base = dcf_value(FCF_TTM, SHARES, 0.10, 0.10)
        net_debt = 500_000_000.0  # 5.0 per share
        bridged = dcf_value(FCF_TTM, SHARES, 0.10, 0.10, net_debt=net_debt)
        self.assertAlmostEqual(
            bridged["intrinsic_value_per_share"],
            base["intrinsic_value_per_share"] - 5.0,
        )
        # Enterprise view is unaffected by the bridge.
        self.assertAlmostEqual(
            bridged["enterprise_value_per_share"],
            base["intrinsic_value_per_share"],
        )
        # Net cash (negative net debt) lifts the per-share value.
        net_cash = dcf_value(FCF_TTM, SHARES, 0.10, 0.10,
                             net_debt=-200_000_000.0)
        self.assertAlmostEqual(
            net_cash["intrinsic_value_per_share"],
            base["intrinsic_value_per_share"] + 2.0,
        )


class TestNetDebtFromRows(unittest.TestCase):
    def test_debt_minus_cash(self):
        rows = [{"fiscal_date": "2025-12-31", "total_debt": 800.0,
                 "cash_and_equivalents": 300.0}]
        self.assertEqual(net_debt_from_rows(rows), 500.0)

    def test_uses_newest_row(self):
        rows = [
            {"fiscal_date": "2024-12-31", "total_debt": 1000.0,
             "cash_and_equivalents": 0.0},
            {"fiscal_date": "2025-12-31", "total_debt": 800.0,
             "cash_and_equivalents": 300.0},
        ]
        self.assertEqual(net_debt_from_rows(rows), 500.0)

    def test_partial_and_missing_fields(self):
        self.assertEqual(net_debt_from_rows(
            [{"fiscal_date": "2025-12-31", "total_debt": 800.0}]), 800.0)
        self.assertEqual(net_debt_from_rows(
            [{"fiscal_date": "2025-12-31", "cash_and_equivalents": 300.0}]),
            -300.0)
        self.assertIsNone(net_debt_from_rows(
            [{"fiscal_date": "2025-12-31"}]))
        self.assertIsNone(net_debt_from_rows([]))


def make_dcf_inputs(**overrides):
    inputs = {
        "profile": {"shares_outstanding": SHARES, "beta": 1.0},
        "fin_rows": [
            {"ticker": "T", "fiscal_date": "2025-12-31", "report_type": "10-Q",
             "free_cash_flow": FCF_TTM, "shares_outstanding": SHARES},
        ],
        "moat_metrics": {"revenue_cagr_5yr": 0.10},
        "price": 100.0,
        "risk_free_rate": 0.045,
    }
    inputs.update(overrides)
    return inputs


class TestComputeDcf(unittest.TestCase):
    def test_growth_cap_and_default(self):
        result = compute_dcf(**make_dcf_inputs(
            moat_metrics={"revenue_cagr_5yr": 0.50}))
        self.assertEqual(result["fcf_growth_rate_5yr"], 0.25)
        result = compute_dcf(**make_dcf_inputs(
            moat_metrics={"revenue_cagr_5yr": None}))
        self.assertEqual(result["fcf_growth_rate_5yr"], 0.05)

    def test_negative_growth_floor(self):
        result = compute_dcf(**make_dcf_inputs(
            moat_metrics={"revenue_cagr_5yr": -0.50}))
        self.assertEqual(result["fcf_growth_rate_5yr"], -0.10)
        result = compute_dcf(**make_dcf_inputs(
            moat_metrics={"revenue_cagr_5yr": -0.05}))
        self.assertAlmostEqual(result["fcf_growth_rate_5yr"], -0.04)

    def test_discount_clamps(self):
        # beta huge -> raw rate way above 0.20 -> clamped to 0.20.
        result = compute_dcf(**make_dcf_inputs(
            profile={"shares_outstanding": SHARES, "beta": 50.0}))
        self.assertEqual(result["discount_rate"], 0.20)
        # risk-free 0 + beta 0 -> 0 -> clamped up to terminal + 0.01.
        result = compute_dcf(**make_dcf_inputs(
            profile={"shares_outstanding": SHARES, "beta": 0.0},
            risk_free_rate=0.0))
        self.assertAlmostEqual(result["discount_rate"], 0.035)
        # beta None defaults to 1.0: 0.045 + 0.055 = 0.10.
        result = compute_dcf(**make_dcf_inputs(
            profile={"shares_outstanding": SHARES, "beta": None}))
        self.assertAlmostEqual(result["discount_rate"], 0.10)

    def test_upside_and_label(self):
        result = compute_dcf(**make_dcf_inputs())
        expected = expected_intrinsic(10.0, 0.08, 0.10)
        self.assertAlmostEqual(result["intrinsic_value_per_share"], expected)
        self.assertAlmostEqual(
            result["upside_downside_pct"], (expected - 100.0) / 100.0 * 100
        )
        self.assertEqual(
            result["margin_of_safety"], result["upside_downside_pct"]
        )
        self.assertEqual(result["intrinsic_value"],
                         result["intrinsic_value_per_share"] * SHARES)

    def test_no_fcf_returns_none(self):
        inputs = make_dcf_inputs()
        inputs["fin_rows"][0]["free_cash_flow"] = -100.0
        self.assertIsNone(compute_dcf(**inputs))

    def test_missing_price_keeps_value_but_no_label(self):
        result = compute_dcf(**make_dcf_inputs(price=None))
        self.assertIsNotNone(result["intrinsic_value_per_share"])
        self.assertIsNone(result["upside_downside_pct"])
        self.assertIsNone(result["mos_label"])

    def test_equity_bridge_from_balance_sheet(self):
        inputs = make_dcf_inputs()
        inputs["fin_rows"][0].update({
            "total_debt": 800_000_000.0,
            "cash_and_equivalents": 300_000_000.0,
        })
        result = compute_dcf(**inputs)
        # net_debt 500M over 100M shares -> 5.0/share below the EV view.
        self.assertEqual(result["net_debt"], 500_000_000.0)
        self.assertAlmostEqual(
            result["intrinsic_value_per_share"],
            result["enterprise_value_per_share"] - 5.0,
        )

    def test_no_balance_sheet_data_skips_bridge(self):
        result = compute_dcf(**make_dcf_inputs())
        self.assertIsNone(result["net_debt"])
        self.assertAlmostEqual(
            result["intrinsic_value_per_share"],
            result["enterprise_value_per_share"],
        )


class TestMosLabel(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(mos_label(31.0), "Strong Buy")
        self.assertEqual(mos_label(30.0), "Buy")
        self.assertEqual(mos_label(16.0), "Buy")
        self.assertEqual(mos_label(15.0), "Fair Value")
        self.assertEqual(mos_label(1.0), "Fair Value")
        self.assertEqual(mos_label(0.0), "Slightly Overvalued")
        self.assertEqual(mos_label(-14.0), "Slightly Overvalued")
        self.assertEqual(mos_label(-15.0), "Overvalued")
        self.assertEqual(mos_label(-50.0), "Overvalued")


class TestSensitivityGrid(unittest.TestCase):
    def test_shape_and_center(self):
        grid = sensitivity_grid(FCF_TTM, SHARES, 0.10, 0.10)
        self.assertEqual(len(grid["growth_rates"]), 5)
        self.assertEqual(len(grid["discount_rates"]), 5)
        self.assertEqual(len(grid["values"]), 5)
        for row in grid["values"]:
            self.assertEqual(len(row), 5)
        self.assertAlmostEqual(grid["growth_rates"][2], 0.10)
        self.assertAlmostEqual(grid["discount_rates"][2], 0.10)
        base = dcf_value(FCF_TTM, SHARES, 0.10, 0.10)
        self.assertAlmostEqual(
            grid["values"][2][2], base["intrinsic_value_per_share"]
        )

    def test_none_cells_when_discount_too_low(self):
        grid = sensitivity_grid(FCF_TTM, SHARES, 0.10, 0.035)
        # Lowest grid discount = 0.015 <= terminal 0.025 -> None column.
        self.assertIsNone(grid["values"][2][0])
        self.assertIsNotNone(grid["values"][2][2])

    def test_none_inputs(self):
        self.assertIsNone(sensitivity_grid(None, SHARES, 0.10, 0.10))
        self.assertIsNone(sensitivity_grid(FCF_TTM, None, 0.10, 0.10))
        self.assertIsNone(sensitivity_grid(FCF_TTM, SHARES, None, 0.10))
        self.assertIsNone(sensitivity_grid(FCF_TTM, SHARES, 0.10, None))

    def test_grid_applies_net_debt_bridge(self):
        net_debt = 500_000_000.0  # 5.0 per share
        grid = sensitivity_grid(FCF_TTM, SHARES, 0.10, 0.10,
                                net_debt=net_debt)
        base = dcf_value(FCF_TTM, SHARES, 0.10, 0.10, net_debt=net_debt)
        self.assertAlmostEqual(
            grid["values"][2][2], base["intrinsic_value_per_share"]
        )
        plain = sensitivity_grid(FCF_TTM, SHARES, 0.10, 0.10)
        self.assertAlmostEqual(
            grid["values"][2][2], plain["values"][2][2] - 5.0
        )


if __name__ == "__main__":
    unittest.main()
