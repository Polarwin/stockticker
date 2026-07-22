"""Tests for generate_sector_heatmap: aggregation math and HTML output.

Run with: bin/python -m unittest test_sector_heatmap -v
No network access required.
"""

import tempfile
import unittest
from pathlib import Path

from generate_sector_heatmap import (
    UNKNOWN_SECTOR,
    aggregate_sectors,
    build_heatmap_html,
)


def make_inputs():
    holdings = {
        "AAA": {"quantity": 10},  # 10 * 100 = 1000 (Tech, semiconductors)
        "BBB": {"quantity": 5},   # 5 * 200 = 1000 (Tech, software)
        "CCC": {"quantity": 2},   # 2 * 500 = 1000 (Banks)
        "DDD": {"quantity": 0},   # zero quantity: skipped
        "EEE": {"quantity": 4},   # no quote: skipped
    }
    quotes = {
        "AAA": {"price": 100.0, "change_pct": 2.0},
        "BBB": {"price": 200.0, "change_pct": -1.0},
        "CCC": {"price": 500.0, "change_pct": None},
    }
    sectors = {
        "AAA": {"sector": "Technology", "industry": "Semiconductors"},
        "BBB": {"sector": "Technology", "industry": "Software - Application"},
        "CCC": {"sector": "Banks", "industry": "Banks - Diversified"},
    }
    return holdings, quotes, sectors


def rows_by_name(rows):
    return {r["name"]: r for r in rows}


class TestAggregateSectors(unittest.TestCase):
    def test_weights_and_values(self):
        holdings, quotes, sectors = make_inputs()
        rows = rows_by_name(aggregate_sectors(holdings, quotes, sectors))

        # Total priced value = 3000: Tech 2000 (two stocks of 1000), Banks 1000.
        self.assertAlmostEqual(rows["Technology"]["value"], 2000.0)
        self.assertAlmostEqual(rows["Technology"]["weight_pct"], 200.0 / 3)
        self.assertAlmostEqual(rows["Banks"]["value"], 1000.0)
        self.assertAlmostEqual(rows["Banks"]["weight_pct"], 100.0 / 3)

    def test_semiconductor_split(self):
        holdings, quotes, sectors = make_inputs()
        rows = aggregate_sectors(holdings, quotes, sectors)
        by_name = rows_by_name(rows)

        # Technology splits into Semiconductors + Technology (other) children.
        self.assertIsNone(by_name["Technology"]["parent"])
        self.assertEqual(by_name["Semiconductors"]["parent"], "Technology")
        self.assertEqual(by_name["Technology (other)"]["parent"], "Technology")

        # Semiconductors: 1000 of the 3000 total = 33.3% of the whole portfolio.
        self.assertAlmostEqual(by_name["Semiconductors"]["value"], 1000.0)
        self.assertAlmostEqual(by_name["Semiconductors"]["weight_pct"], 100.0 / 3)
        self.assertAlmostEqual(by_name["Semiconductors"]["change_pct"], 2.0)
        self.assertAlmostEqual(by_name["Technology (other)"]["change_pct"], -1.0)

        # Parent totals equal the sum of its children.
        self.assertAlmostEqual(
            by_name["Technology"]["value"],
            by_name["Semiconductors"]["value"]
            + by_name["Technology (other)"]["value"],
        )
        # Parent change = value-weighted average across both children.
        self.assertAlmostEqual(by_name["Technology"]["change_pct"], 0.5)

        # Children are listed immediately after their parent.
        names = [r["name"] for r in rows]
        tech = names.index("Technology")
        self.assertEqual(names[tech + 1 : tech + 3], ["Semiconductors", "Technology (other)"])

    def test_no_split_without_semiconductors(self):
        holdings = {"BBB": {"quantity": 5}, "CCC": {"quantity": 2}}
        quotes = {
            "BBB": {"price": 200.0, "change_pct": 1.0},
            "CCC": {"price": 500.0, "change_pct": 1.0},
        }
        sectors = {
            "BBB": {"sector": "Technology", "industry": "Software - Application"},
            "CCC": {"sector": "Technology", "industry": "Consumer Electronics"},
        }
        rows = aggregate_sectors(holdings, quotes, sectors)
        self.assertEqual([r["name"] for r in rows], ["Technology"])
        self.assertIsNone(rows[0]["parent"])

    def test_weighted_daily_change_none(self):
        holdings, quotes, sectors = make_inputs()
        rows = rows_by_name(aggregate_sectors(holdings, quotes, sectors))
        # Banks: the only stock has no change value -> None.
        self.assertIsNone(rows["Banks"]["change_pct"])

    def test_unknown_sector_fallback(self):
        holdings = {"ZZZ": {"quantity": 1}}
        quotes = {"ZZZ": {"price": 50.0, "change_pct": 1.5}}
        rows = aggregate_sectors(holdings, quotes, {})

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], UNKNOWN_SECTOR)
        self.assertAlmostEqual(rows[0]["weight_pct"], 100.0)

    def test_empty_when_nothing_priced(self):
        self.assertEqual(aggregate_sectors({}, {}, {}), [])
        self.assertEqual(
            aggregate_sectors({"AAA": {"quantity": 1}}, {}, {"AAA": {"sector": "Tech"}}),
            [],
        )

    def test_sorted_by_value_descending(self):
        holdings = {"AAA": {"quantity": 1}, "CCC": {"quantity": 10}}
        quotes = {
            "AAA": {"price": 10.0, "change_pct": 1.0},
            "CCC": {"price": 10.0, "change_pct": 1.0},
        }
        sectors = {
            "AAA": {"sector": "Tech", "industry": ""},
            "CCC": {"sector": "Banks", "industry": ""},
        }
        rows = aggregate_sectors(holdings, quotes, sectors)
        self.assertEqual([r["name"] for r in rows], ["Banks", "Tech"])


class TestHeatmapHtml(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"name": "Technology", "parent": None, "value": 2000.0, "weight_pct": 57.1, "change_pct": 1.25},
            {"name": "Semiconductors", "parent": "Technology", "value": 1500.0, "weight_pct": 42.9, "change_pct": 2.10},
            {"name": "Technology (other)", "parent": "Technology", "value": 500.0, "weight_pct": 14.3, "change_pct": -1.30},
            {"name": "Banks", "parent": None, "value": 1000.0, "weight_pct": 28.6, "change_pct": -0.75},
            {"name": UNKNOWN_SECTOR, "parent": None, "value": 500.0, "weight_pct": 14.3, "change_pct": None},
        ]

    def test_html_contains_sectors_and_changes(self):
        html = build_heatmap_html(self.rows, "2026-07-22 10:00:00")
        self.assertIn("<html", html.lower())
        for name in ("Technology", "Semiconductors", "Technology (other)", "Banks", UNKNOWN_SECTOR):
            self.assertIn(name, html)
        self.assertIn("treemap", html.lower())
        # Hierarchy: Semiconductors' id is parent-prefixed; plotly's JSON
        # escapes "/" as the literal 6-character sequence \u002f.
        self.assertIn("Technology\\u002fSemiconductors", html)
        # Embedded plotly.js makes the file self-contained.
        self.assertIn("plotly.js", html.lower())

    def test_html_file_written(self):
        html = build_heatmap_html(self.rows, "2026-07-22 10:00:00")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sector_heatmap.html"
            path.write_text(html)
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
