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
    "earnings_watch_duration_minutes": 180,
    "edgar_user_agent": "stockticker/1.0 admin@example.com",
    "premarket_report_enabled": True,
    "premarket_check_time": "08:45",
    "premarket_move_threshold_pct": 2.0,
    "fundamentals_db_path": "data/fundamentals.db",
    "quotes_refresh_seconds": 300,
    "quotes_market_hours_only": True,
    "premarket_open": "04:00",
    "postmarket_close": "20:00",
    "fundamentals_refresh_days": 7,
    "market_news_enabled": True,
    "market_news_interval_seconds": 900,
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


def _append_fundamental_lines(
    settings: dict, message: str, ticker: str | None = None
) -> str:
    """Append compact 'Fund: .. | Moat: .. | DCF: ..' lines to a report message.

    Silent no-op when the fundamentals DB is missing or no mentioned
    ticker has a stored score.
    """
    try:
        from fundamentals import database, reporter

        db_path = reporter.resolve_fundamentals_db_path(settings)
        if not Path(db_path).exists():
            return message
        conn = database.init_db(db_path)
        try:
            tickers = [ticker.strip().upper()] if ticker else None
            liners = reporter.fundamental_one_liners(conn, tickers)
        finally:
            conn.close()
    except Exception:
        return message
    lines = [line for symbol, line in liners.items() if symbol in message]
    if not lines:
        return message
    return message + "\n" + "\n".join(f"  {line}" for line in lines)


def do_premarket_report(
    settings: dict,
    test: bool,
    ticker: str | None = None,
    include_fundamentals: bool = False,
) -> None:
    """Build the pre-market portfolio report; send one Telegram message."""
    message = build_report(settings, ticker=ticker)
    if include_fundamentals:
        message = _append_fundamental_lines(settings, message, ticker)
    if not test:
        send_telegram(message)
    else:
        print(message)


def do_update_fundamentals(
    settings: dict, test: bool, ticker: str | None, all_tickers: bool
) -> None:
    """Fetch + compute + store fundamentals for one ticker or the watchlist."""
    from fundamentals import database, reporter
    from ticker import load_watchlist

    tickers = load_watchlist() if all_tickers else [ticker.strip().upper()]
    max_age_days = int(settings.get("fundamentals_refresh_days", 7))
    conn = database.init_db(reporter.resolve_fundamentals_db_path(settings))
    try:
        reporter.update_all(conn, tickers, test=test, max_age_days=max_age_days)
    finally:
        conn.close()


def do_fundamental_dashboard(settings: dict, test: bool) -> None:
    """Render the fundamental dashboard from stored data."""
    from fundamentals import reporter

    reporter.generate_dashboard(settings, test=test)


def do_peer_comparison(settings: dict, ticker: str) -> None:
    """Print the stored peer comparison for a ticker (no fetching)."""
    from fundamentals import database, reporter

    ticker = ticker.strip().upper()
    conn = database.init_db(reporter.resolve_fundamentals_db_path(settings))
    try:
        rows = database.get_peer_comparison(conn, ticker)
    finally:
        conn.close()
    if not rows:
        print(f"{ticker}: no peer data — run --update-fundamentals first")
        return

    def fmt(value) -> str:
        return "N/A" if value is None else f"{value:.2f}"

    print(f"Peer comparison — {ticker} (premium/discount vs peer median)")
    by_metric: dict[str, list[dict]] = {}
    for row in rows:
        by_metric.setdefault(row["metric"], []).append(row)
    for metric, metric_rows in by_metric.items():
        own = metric_rows[0]["ticker_value"]
        median = metric_rows[0]["sector_median"]
        premium = metric_rows[0]["premium_discount_pct"]
        premium_str = "N/A" if premium is None else f"{premium:+.1f}%"
        print(f"  {metric}: {fmt(own)} (median {fmt(median)}, {premium_str})")
        for row in metric_rows:
            print(f"    {row['peer_ticker']}: {fmt(row['peer_value'])}")


def do_valuation_history(settings: dict, ticker: str, years: int) -> None:
    """Print the stored historical valuation series + percentile summary."""
    from datetime import date, timedelta

    from fundamentals import database, reporter

    ticker = ticker.strip().upper()
    cutoff = (date.today() - timedelta(days=365 * years)).isoformat()
    conn = database.init_db(reporter.resolve_fundamentals_db_path(settings))
    try:
        rows = [
            row
            for row in database.get_historical_valuation(conn, ticker)
            if row["date"] >= cutoff
        ]
        results = reporter.load_results(conn, [ticker])
    finally:
        conn.close()
    if not rows:
        print(f"{ticker}: no valuation history — run --update-fundamentals first")
        return

    def fmt(value) -> str:
        return "N/A" if value is None else f"{value:.1f}"

    print(f"Valuation history — {ticker} (last {years}y, {len(rows)} snapshots)")
    print("  date        P/E    P/B    P/S    P/FCF  EV/EBITDA  sectorPE  %ile")
    for row in reversed(rows):  # oldest first
        pct = row.get("percentile_vs_history")
        print(
            f"  {row['date']}  {fmt(row.get('pe_ratio')):>6} "
            f"{fmt(row.get('pb_ratio')):>6} {fmt(row.get('ps_ratio')):>6} "
            f"{fmt(row.get('p_fcf_ratio')):>6} {fmt(row.get('ev_ebitda')):>9} "
            f"{fmt(row.get('sector_median_pe')):>8} "
            f"{('N/A' if pct is None else f'{pct:.0f}%'):>5}"
        )
    if results:
        result = results[0]
        print("Percentiles vs own history (last 20 snapshots):")
        for key, pct in (result.get("history_percentiles") or {}).items():
            print(f"  {key}: {'N/A' if pct is None else f'{pct:.0f}%'}")
        sector_pct = result.get("sector_percentile")
        print(f"  vs sector (P/E): {'N/A' if sector_pct is None else f'{sector_pct:.0f}%'}")


def do_dcf_valuation(settings: dict, test: bool, ticker: str) -> None:
    """Print the DCF breakdown + sensitivity grid (refresh unless stored today)."""
    from datetime import date

    from fundamentals import database, reporter

    ticker = ticker.strip().upper()
    conn = database.init_db(reporter.resolve_fundamentals_db_path(settings))
    try:
        latest = database.get_latest_dcf_valuation(conn, ticker)
        if latest and latest.get("valuation_date") == date.today().isoformat():
            print(f"(using today's stored DCF for {ticker})")
            loaded = reporter.load_results(conn, [ticker])
            result = loaded[0] if loaded else None
        else:
            result = reporter.update_ticker(conn, ticker, watchlist=[ticker])
            conn.commit()
    finally:
        conn.close()

    if result is None or not result.get("dcf"):
        print(f"{ticker}: no computable DCF (negative or missing FCF)")
        return
    dcf = result["dcf"]

    def fmt(value, digits=2) -> str:
        return "N/A" if value is None else f"{value:.{digits}f}"

    print(f"DCF valuation — {ticker}")
    print(f"  Current price:        {fmt(dcf.get('current_price'))}")
    print(f"  FCF/share (TTM):      {fmt(dcf.get('fcf_per_share_ttm'))}")
    print(f"  Growth (5yr):         {fmt(dcf.get('fcf_growth_rate_5yr'), 3)}")
    print(f"  Discount rate:        {fmt(dcf.get('discount_rate'), 3)}")
    print(f"  Terminal growth:      {fmt(dcf.get('fcf_growth_rate_terminal'), 3)}")
    print(f"  Projected FCF (5yr):  {fmt(dcf.get('projected_fcf_5yr'))}")
    print(f"  Terminal value:       {fmt(dcf.get('terminal_value'))}")
    print(f"  Intrinsic/share:      {fmt(dcf.get('intrinsic_value_per_share'))}")
    upside = dcf.get("upside_downside_pct")
    print(f"  Upside/downside:      {'N/A' if upside is None else f'{upside:+.1f}%'}")
    print(f"  Margin of safety:     {dcf.get('mos_label') or 'N/A'}")

    grid = result.get("sensitivity")
    if not grid:
        print("  Sensitivity: N/A")
        return
    print("  Sensitivity (intrinsic/share; rows=growth, cols=discount):")
    discounts = grid["discount_rates"]
    print("            " + "".join(f"{d * 100:>8.1f}%" for d in discounts))
    for growth, values in zip(grid["growth_rates"], grid["values"]):
        cells = "".join(
            f"{v:>9.1f}" if v is not None else f"{'—':>9}" for v in values
        )
        print(f"    {growth * 100:>5.1f}%{cells}")


def do_moat_score(settings: dict, ticker: str) -> None:
    """Print the stored moat score, rating, and component breakdown."""
    from fundamentals import database, moat_scorer, reporter

    ticker = ticker.strip().upper()
    conn = database.init_db(reporter.resolve_fundamentals_db_path(settings))
    try:
        moat = database.get_latest_moat_metrics(conn, ticker)
    finally:
        conn.close()
    if not moat or moat.get("moat_score") is None:
        print(f"{ticker}: no moat data — run --update-fundamentals first")
        return
    _score, _rating, breakdown = moat_scorer.moat_score(moat)
    print(f"Moat score — {ticker}: {moat['moat_score']:.0f}/100 "
          f"({moat.get('moat_rating') or 'N/A'})")
    for key, (label, max_points) in reporter.MOAT_COMPONENTS.items():
        points = breakdown.get(key)
        print(f"  {label}: {'N/A' if points is None else points}/{max_points}")


def do_fundamental_report(settings: dict, test: bool, ticker: str) -> None:
    """Full console fundamental report for one ticker + JSON report files."""
    from fundamentals import database, reporter

    ticker = ticker.strip().upper()
    conn = database.init_db(reporter.resolve_fundamentals_db_path(settings))
    try:
        result = reporter.update_ticker(conn, ticker, watchlist=[ticker])
        conn.commit()
    finally:
        conn.close()
    print(
        reporter.build_telegram_alert(result).replace(
            f"📊 Fundamental Alert — {ticker} Earnings Today",
            f"📊 Fundamental Report — {ticker}",
            1,
        )
    )
    fundamental_path, dcf_path = reporter.write_json_reports(
        [result], reporter.REPORTS_DIR
    )
    print(f"JSON reports written to {fundamental_path} and {dcf_path}")


def do_check_earnings(settings: dict, test: bool) -> None:
    """Send fundamental alerts for watchlist tickers reporting today."""
    from fundamentals import reporter

    reporter.run_earnings_check(settings, test=test)


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
        "--market-news",
        action="store_true",
        help="Run one macro market-news crawl round immediately, then exit.",
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
    parser.add_argument(
        "--ticker",
        default=None,
        metavar="SYMBOL",
        help="Deep-dive a single symbol in the pre-market report.",
    )
    parser.add_argument(
        "--indicators-table",
        action="store_true",
        help="Generate the bullish/bearish indicators table (indicators_table.html), then exit.",
    )
    parser.add_argument(
        "--update-fundamentals",
        action="store_true",
        help="Fetch + store fundamentals for --ticker SYMBOL or --all, then exit.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Apply --update-fundamentals to the whole watchlist.",
    )
    parser.add_argument(
        "--fundamental-dashboard",
        action="store_true",
        help="Render fundamental_dashboard.html from stored data, then exit.",
    )
    parser.add_argument(
        "--peer-comparison",
        action="store_true",
        help="Print the stored peer comparison for --ticker SYMBOL, then exit.",
    )
    parser.add_argument(
        "--valuation-history",
        action="store_true",
        help="Print the stored valuation history for --ticker SYMBOL, then exit.",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="Look-back window for --valuation-history (default: 5).",
    )
    parser.add_argument(
        "--dcf-valuation",
        action="store_true",
        help="Print the DCF breakdown + sensitivity grid for --ticker SYMBOL, then exit.",
    )
    parser.add_argument(
        "--moat-score",
        action="store_true",
        help="Print the moat score breakdown for --ticker SYMBOL, then exit.",
    )
    parser.add_argument(
        "--fundamental-report",
        action="store_true",
        help="Full console fundamental report for --ticker SYMBOL + JSON files, then exit.",
    )
    parser.add_argument(
        "--check-earnings",
        action="store_true",
        help="Send fundamental alerts for watchlist tickers reporting today, then exit.",
    )
    parser.add_argument(
        "--include-fundamentals",
        action="store_true",
        help="Append compact fundamental score lines to the pre-market report.",
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

    if args.market_news:
        import market_news

        counts = market_news.run_round(
            settings, notify=None if args.test else send_telegram
        )
        print(f"Market news: {counts['alerts']} alerts, "
              f"{counts['digest']} digest items")
        return

    if args.sector_heatmap:
        # Lazy import so the daemon loop never pays for plotly.
        from generate_sector_heatmap import generate_sector_heatmap

        generate_sector_heatmap(settings, test=args.test)
        return

    if args.update_fundamentals:
        if not args.ticker and not args.all:
            parser.error("--update-fundamentals requires --ticker SYMBOL or --all")
        do_update_fundamentals(
            settings, args.test, ticker=args.ticker, all_tickers=args.all
        )
        return

    if args.fundamental_dashboard:
        do_fundamental_dashboard(settings, args.test)
        return

    if args.peer_comparison:
        if not args.ticker:
            parser.error("--peer-comparison requires --ticker SYMBOL")
        do_peer_comparison(settings, args.ticker)
        return

    if args.valuation_history:
        if not args.ticker:
            parser.error("--valuation-history requires --ticker SYMBOL")
        do_valuation_history(settings, args.ticker, args.years)
        return

    if args.dcf_valuation:
        if not args.ticker:
            parser.error("--dcf-valuation requires --ticker SYMBOL")
        do_dcf_valuation(settings, args.test, args.ticker)
        return

    if args.moat_score:
        if not args.ticker:
            parser.error("--moat-score requires --ticker SYMBOL")
        do_moat_score(settings, args.ticker)
        return

    if args.fundamental_report:
        if not args.ticker:
            parser.error("--fundamental-report requires --ticker SYMBOL")
        do_fundamental_report(settings, args.test, args.ticker)
        return

    if args.check_earnings:
        do_check_earnings(settings, args.test)
        return

    if args.premarket_report:
        do_premarket_report(
            settings, args.test, ticker=args.ticker,
            include_fundamentals=args.include_fundamentals,
        )
        return

    if args.indicators_table:
        from generate_indicators_table import generate_indicators_table

        generate_indicators_table(settings, test=args.test)
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
