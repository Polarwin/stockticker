"""Tests for the quotes_cache table and options-volume getter in db.py.

Run with: bin/python -m pytest tests/ -v
No network access required; all tests use :memory: databases.
"""

import unittest

from db import (
    get_cached_quotes,
    get_options_volume,
    init_db,
    insert_intraday_quotes,
    upsert_cached_quotes,
    upsert_options_volume,
)


class TestQuotesCache(unittest.TestCase):
    def setUp(self):
        self.conn = init_db(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_round_trip(self):
        quotes = {
            "AAPL": {"price": 200.5, "change_pct": 1.25, "prev_close": 198.03},
            "MSFT": {"price": 400.0, "change_pct": None, "prev_close": None},
        }
        count = upsert_cached_quotes(self.conn, quotes, "2026-07-27T10:00:00")
        self.conn.commit()
        self.assertEqual(count, 2)

        cached = get_cached_quotes(self.conn, ["AAPL", "MSFT", "NOPE"])
        self.assertEqual(set(cached), {"AAPL", "MSFT"})  # NOPE omitted
        self.assertEqual(cached["AAPL"]["price"], 200.5)
        self.assertEqual(cached["AAPL"]["change_pct"], 1.25)
        self.assertIsNone(cached["MSFT"]["change_pct"])

    def test_upsert_replaces(self):
        row = {"price": 100.0, "change_pct": 0.5, "prev_close": 99.5}
        upsert_cached_quotes(self.conn, {"AAPL": row}, "2026-07-26T10:00:00")
        upsert_cached_quotes(
            self.conn,
            {"AAPL": {"price": 101.0, "change_pct": 1.0, "prev_close": 100.0}},
            "2026-07-27T10:00:00",
        )
        self.conn.commit()
        cached = get_cached_quotes(self.conn, ["AAPL"])
        self.assertEqual(cached["AAPL"]["price"], 101.0)

    def test_fetched_at_stored(self):
        upsert_cached_quotes(
            self.conn,
            {"AAPL": {"price": 100.0, "change_pct": None, "prev_close": None}},
            "2026-07-27T10:00:00",
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT fetched_at FROM quotes_cache WHERE symbol = 'AAPL'"
        ).fetchone()
        self.assertEqual(row[0], "2026-07-27T10:00:00")


class TestIntradayQuotes(unittest.TestCase):
    def setUp(self):
        self.conn = init_db(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_snapshots_accumulate(self):
        quotes = {"AAPL": {"price": 200.0, "change_pct": 1.0, "prev_close": 198.0}}
        insert_intraday_quotes(self.conn, quotes, "2026-07-27T09:30:00")
        insert_intraday_quotes(
            self.conn,
            {"AAPL": {"price": 201.0, "change_pct": 1.5, "prev_close": 198.0}},
            "2026-07-27T09:35:00",
        )
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT ts, price FROM intraday_quotes WHERE symbol = 'AAPL' ORDER BY ts"
        ).fetchall()
        self.assertEqual(
            rows, [("2026-07-27T09:30:00", 200.0), ("2026-07-27T09:35:00", 201.0)]
        )

    def test_duplicate_snapshot_ignored(self):
        quotes = {"AAPL": {"price": 200.0, "change_pct": 1.0, "prev_close": 198.0}}
        insert_intraday_quotes(self.conn, quotes, "2026-07-27T09:30:00")
        inserted = insert_intraday_quotes(
            self.conn,
            {"AAPL": {"price": 999.0, "change_pct": 1.0, "prev_close": 198.0}},
            "2026-07-27T09:30:00",  # same ts: retried snapshot
        )
        self.conn.commit()
        self.assertEqual(inserted, 0)
        row = self.conn.execute(
            "SELECT price FROM intraday_quotes WHERE symbol = 'AAPL'"
        ).fetchone()
        self.assertEqual(row[0], 200.0)  # first write wins


class TestGetOptionsVolume(unittest.TestCase):
    def setUp(self):
        self.conn = init_db(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_hit_and_miss(self):
        upsert_options_volume(self.conn, "AAPL", "2026-07-27", 1200, 800)
        self.conn.commit()
        self.assertEqual(
            get_options_volume(self.conn, "AAPL", "2026-07-27"), (1200, 800)
        )
        self.assertIsNone(get_options_volume(self.conn, "AAPL", "2026-07-26"))
        self.assertIsNone(get_options_volume(self.conn, "NOPE", "2026-07-27"))


if __name__ == "__main__":
    unittest.main()
