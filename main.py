"""Orchestrator loop: periodic ticker rounds and daily earnings reminders."""

import argparse
import json
import sys
import time
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from collector import check_signals, db_update_due, update_database, update_earnings
from earnings_reminder import format_match, run_earnings_check
from earnings_report import check_new_reports
from earnings_watch import run_watch_tick
from notify import send_telegram
from premarket_report import build_report
from ticker import run_ticker_round

SETTINGS_PATH = Path(__file__).with_name("settings.json")

DEFAULT_SETTINGS = {
    "ticker_interval_seconds": 600,
    "ticker_enabled": False,
    "ticker_market_hours_only": True,
    "market_timezone": "America/New_York",
    "market_open": "09:30",
    "market_close": "16:00",
    "earnings_remind_days": 7,
    "earnings_check_time": "08:00",
    "db_enabled": True,
    "db_path": "stockticker.db",
    "db_update_time": "18:00",
    "db_backfill_days": 365,
    "web_host": "127.0.0.1",
    "web_port": 8010,
    "earnings_watch_enabled": True,
    "earnings_watch_interval_minutes": 10,
    "earnings_watch_duration_minutes": 120,
    "premarket_report_enabled": True,
    "premarket_check_time": "08:30",
    "premarket_move_threshold_pct": 2.0,
}


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    """Load settings from JSON, falling back to defaults with a warning."""
    if not path.exists():
        print(
            f"Warning: settings file not found: {path}; using defaults.",
            file=sys.stderr,
        )
        return dict(DEFAULT_SETTINGS)

    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"Warning: failed to load settings from {path} ({exc}); using defaults.",
            file=sys.stderr,
        )
        return dict(DEFAULT_SETTINGS)

    settings = dict(DEFAULT_SETTINGS)
    settings.update(loaded)
    return settings


def is_market_open(now: datetime, market_open: str, market_close: str) -> bool:
    """True when `now` falls inside Mon-Fri market_open..market_close."""
    if now.weekday() >= 5:
        return False
    open_t = dt_time.fromisoformat(market_open)
    close_t = dt_time.fromisoformat(market_close)
    return open_t <= now.time() <= close_t


def do_ticker_round(test: bool) -> None:
    """Run one ticker round and send its Telegram message unless testing."""
    _lines, message = run_ticker_round(test=test)
    if message and not test:
        send_telegram(message)


def do_earnings_check(days: int, test: bool) -> None:
    """Run one earnings check and send one Telegram message on matches."""
    matches = run_earnings_check(days, test=test)
    if matches and not test:
        lines = [format_match(match) for match in matches]
        send_telegram("📅 Earnings Reminder\n" + "\n".join(lines))


def do_earnings_report_check(settings: dict, test: bool) -> None:
    """Check for newly released earnings reports; send one message per report."""
    messages = check_new_reports(settings, test=test)
    if not test:
        for message in messages:
            send_telegram(message)
    else:
        for message in messages:
            print(message)


def do_earnings_watch(settings: dict, test: bool) -> None:
    """Advance post-earnings watches; send one Telegram message per update."""
    messages = run_watch_tick(settings, test=test)
    if not test:
        for message in messages:
            send_telegram(message)
    else:
        for message in messages:
            print(message)


def do_signal_check(settings: dict, test: bool) -> None:
    """Check for MACD/RSI crossovers and send one Telegram alert on signals."""
    signals = check_signals(settings, test=test)
    if signals and not test:
        lines = [
            f"{symbol}: {indicator} {direction} crossover ({date})"
            for symbol, indicator, direction, date in signals
        ]
        send_telegram("📊 Indicator Alerts\n" + "\n".join(lines))


def do_premarket_report(settings: dict, test: bool) -> None:
    """Build the pre-market portfolio report; send one Telegram message."""
    message = build_report(settings)
    if not test:
        send_telegram(message)
    else:
        print(message)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stock price watcher with earnings reminders."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one ticker round and one earnings check immediately, then exit.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Console output only; do not send Telegram messages.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override earnings_remind_days from settings.",
    )
    parser.add_argument(
        "--update-db",
        action="store_true",
        help="Update the local price database immediately, then exit.",
    )
    parser.add_argument(
        "--update-earnings",
        action="store_true",
        help="Update the earnings table immediately, then exit.",
    )
    parser.add_argument(
        "--earnings-reports",
        action="store_true",
        help="Check for newly released earnings reports immediately, then exit.",
    )
    parser.add_argument(
        "--signals",
        action="store_true",
        help="Check MACD/RSI crossovers immediately, then exit.",
    )
    parser.add_argument(
        "--earnings-watch",
        action="store_true",
        help="Run one post-earnings watch tick immediately, then exit.",
    )
    parser.add_argument(
        "--sector-heatmap",
        action="store_true",
        help="Generate the sector allocation heatmap (sector_heatmap.html), then exit.",
    )
    parser.add_argument(
        "--premarket-report",
        action="store_true",
        help="Send the pre-market portfolio report immediately, then exit.",
    )
    args = parser.parse_args()

    settings = load_settings()
    market_tz = ZoneInfo(settings["market_timezone"])
    earnings_days = (
        args.days if args.days is not None else settings["earnings_remind_days"]
    )

    if args.update_db:
        update_database(settings, test=args.test)
        return

    if args.update_earnings:
        update_earnings(settings, test=args.test)
        return

    if args.earnings_reports:
        do_earnings_report_check(settings, args.test)
        return

    if args.signals:
        do_signal_check(settings, args.test)
        return

    if args.earnings_watch:
        do_earnings_watch(settings, args.test)
        return

    if args.sector_heatmap:
        # Lazy import so the daemon loop never pays for plotly.
        from generate_sector_heatmap import generate_sector_heatmap

        generate_sector_heatmap(settings, test=args.test)
        return

    if args.premarket_report:
        do_premarket_report(settings, args.test)
        return

    if args.once:
        do_ticker_round(args.test)
        do_earnings_check(earnings_days, args.test)
        do_earnings_report_check(settings, args.test)
        return

    interval = settings["ticker_interval_seconds"]
    check_time = dt_time.fromisoformat(settings["earnings_check_time"])
    db_update_time = dt_time.fromisoformat(settings["db_update_time"])
    premarket_time = dt_time.fromisoformat(settings["premarket_check_time"])
    last_earnings_check_date = None
    last_premarket_date = None

    if not settings["ticker_enabled"]:
        print("Ticker disabled in settings, earnings reminder active")

    while True:
        now = datetime.now(market_tz)

        ticker_enabled = settings["ticker_enabled"]
        if ticker_enabled:
            if settings["ticker_market_hours_only"] and not is_market_open(
                now, settings["market_open"], settings["market_close"]
            ):
                print(
                    f"{now.strftime('%Y-%m-%d %H:%M:%S')} "
                    "Market closed, skipping ticker round"
                )
            else:
                do_ticker_round(args.test)

        if now.time() >= check_time and last_earnings_check_date != now.date():
            last_earnings_check_date = now.date()
            do_earnings_check(earnings_days, args.test)
            do_earnings_report_check(settings, args.test)

        if (
            settings["premarket_report_enabled"]
            and now.weekday() < 5
            and now.time() >= premarket_time
            and last_premarket_date != now.date()
        ):
            last_premarket_date = now.date()
            do_premarket_report(settings, args.test)

        do_earnings_watch(settings, args.test)

        if (
            settings["db_enabled"]
            and now.time() >= db_update_time
            and db_update_due(settings, now.date().isoformat())
        ):
            update_database(settings, test=args.test)
            do_signal_check(settings, args.test)

        time.sleep(interval)


if __name__ == "__main__":
    main()
