"""Flask web UI for the stock ticker: watchlist, charts, earnings calendar."""

import json
import re
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
from flask import Flask, jsonify, request, send_from_directory

from collector import fetch_history_rows
from db import (
    delete_holding,
    get_holdings,
    init_db,
    resolve_db_path,
    upsert_earnings,
    upsert_holding,
    upsert_prices,
)
from earnings_reminder import get_earnings_info
from indicators import macd, rsi
from main import load_settings
from ticker import WATCHLIST_PATH

SETTINGS = load_settings()
MARKET_TZ = ZoneInfo(SETTINGS["market_timezone"])

STATIC_DIR = Path(__file__).with_name("static")
SYMBOLS_PATH = STATIC_DIR / "symbols.json"
SYMBOL_RE = re.compile(r"^[A-Z.]{1,6}$")

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


def today():
    """Today's date in the market timezone."""
    return datetime.now(MARKET_TZ).date()


def open_db():
    return init_db(resolve_db_path(SETTINGS["db_path"]))


def read_watchlist() -> list[str]:
    """Read tickers from the watchlist file (no exits, unlike ticker.load_watchlist)."""
    if not WATCHLIST_PATH.exists():
        return []
    tickers = []
    for line in WATCHLIST_PATH.read_text().splitlines():
        ticker = line.strip().upper()
        if ticker and not ticker.startswith("#"):
            tickers.append(ticker)
    return tickers


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/config")
def api_config():
    return jsonify({"earnings_remind_days": SETTINGS["earnings_remind_days"]})


@app.get("/api/watchlist")
def api_watchlist():
    return jsonify(read_watchlist())


@app.post("/api/watchlist")
def api_watchlist_add():
    data = request.get_json(force=True, silent=True) or {}
    symbol = str(data.get("symbol", "")).strip().upper()
    if not SYMBOL_RE.match(symbol):
        return jsonify({"error": "invalid symbol"}), 400

    tickers = read_watchlist()
    added = False
    if symbol not in tickers:
        with WATCHLIST_PATH.open("a") as f:
            f.write(f"{symbol}\n")
        added = True

    # Fetch history and earnings immediately so the UI works instantly.
    conn = open_db()
    try:
        try:
            rows = fetch_history_rows(symbol, "1y")
            upsert_prices(conn, symbol, rows)
        except ValueError as exc:
            print(f"Warning: {exc}")
        try:
            info = get_earnings_info(symbol)
            if info is not None:
                _s, earnings_date, eps = info
                upsert_earnings(
                    conn,
                    symbol,
                    earnings_date.isoformat(),
                    eps,
                    datetime.now(MARKET_TZ).isoformat(),
                )
        except ValueError as exc:
            print(f"Warning: {exc}")
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "symbol": symbol, "added": added})


@app.delete("/api/watchlist/<symbol>")
def api_watchlist_delete(symbol: str):
    symbol = symbol.strip().upper()
    tickers = read_watchlist()
    if symbol not in tickers:
        return jsonify({"error": "not in watchlist"}), 404

    lines = WATCHLIST_PATH.read_text().splitlines()
    kept = [line for line in lines if line.strip().upper() != symbol]
    WATCHLIST_PATH.write_text("\n".join(kept) + "\n")
    # DB rows are kept deliberately.
    return jsonify({"ok": True, "symbol": symbol})


def search_static(query: str) -> list[dict]:
    """Substring match against the bundled static/symbols.json fallback."""
    if not SYMBOLS_PATH.exists():
        return []
    entries = json.loads(SYMBOLS_PATH.read_text())
    q = query.lower()
    prefix, substring = [], []
    for entry in entries:
        symbol, name = entry["symbol"], entry["name"]
        if symbol.lower().startswith(q):
            prefix.append(entry)
        elif q in symbol.lower() or q in name.lower():
            substring.append(entry)
        if len(prefix) >= 10:
            break
    return (prefix + substring)[:10]


def fetch_live_quotes(symbols: list[str]) -> dict:
    """Batch-fetch the latest price and % change vs previous close from yfinance.

    Returns {symbol: {"price": float, "change_pct": float|None}}; symbols that
    fail are simply omitted (caller falls back to DB values).
    """
    try:
        data = yf.download(
            " ".join(symbols),
            period="2d",
            group_by="ticker",
            threads=True,
            progress=False,
        )
    except Exception as exc:
        print(f"Warning: live quotes fetch failed ({exc})")
        return {}

    quotes = {}
    for symbol in symbols:
        try:
            closes = (
                data[symbol]["Close"].dropna()
                if len(symbols) > 1
                else data["Close"].dropna()
            )
            if closes.empty:
                continue
            price = float(closes.iloc[-1])
            change_pct = None
            if len(closes) > 1 and float(closes.iloc[-2]):
                change_pct = round(
                    (price - float(closes.iloc[-2])) / float(closes.iloc[-2]) * 100, 2
                )
            quotes[symbol] = {"price": round(price, 2), "change_pct": change_pct}
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return quotes


@app.get("/api/quotes")
def api_quotes():
    """Live price and % change vs previous close for each watchlist symbol.

    Batch-fetched from yfinance; symbols that fail fall back to the latest
    values stored in daily_prices.
    """
    symbols = read_watchlist()
    quotes = fetch_live_quotes(symbols)

    missing = [s for s in symbols if s not in quotes]
    if missing:
        conn = open_db()
        try:
            for symbol in missing:
                rows = conn.execute(
                    """
                    SELECT close FROM daily_prices
                    WHERE symbol = ? ORDER BY date DESC LIMIT 2
                    """,
                    (symbol,),
                ).fetchall()
                if not rows or rows[0][0] is None:
                    continue
                price = rows[0][0]
                change_pct = None
                if len(rows) == 2 and rows[1][0]:
                    change_pct = round((price - rows[1][0]) / rows[1][0] * 100, 2)
                quotes[symbol] = {"price": round(price, 2), "change_pct": change_pct}
        finally:
            conn.close()
    return jsonify(quotes)


@app.get("/api/holdings")
def api_holdings():
    """All portfolio holdings as {symbol: {avg_price, quantity}}."""
    conn = open_db()
    try:
        return jsonify(get_holdings(conn))
    finally:
        conn.close()


@app.post("/api/holdings")
def api_holdings_set():
    """Set the avg buy price and quantity for a symbol."""
    data = request.get_json(force=True, silent=True) or {}
    symbol = str(data.get("symbol", "")).strip().upper()
    if not SYMBOL_RE.match(symbol):
        return jsonify({"error": "invalid symbol"}), 400
    try:
        avg_price = float(data.get("avg_price"))
        quantity = float(data.get("quantity"))
    except (TypeError, ValueError):
        return jsonify({"error": "avg_price and quantity must be numbers"}), 400
    if avg_price < 0 or quantity < 0:
        return jsonify({"error": "avg_price and quantity must be >= 0"}), 400

    conn = open_db()
    try:
        upsert_holding(
            conn, symbol, avg_price, quantity, datetime.now(MARKET_TZ).isoformat()
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "symbol": symbol})


@app.delete("/api/holdings/<symbol>")
def api_holdings_delete(symbol: str):
    """Remove the holding for a symbol."""
    symbol = symbol.strip().upper()
    conn = open_db()
    try:
        delete_holding(conn, symbol)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "symbol": symbol})


@app.get("/api/search")
def api_search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify([])

    try:
        results = yf.Search(query, max_results=20).quotes
        matches = []
        for quote in results:
            symbol = (quote.get("symbol") or "").upper()
            name = quote.get("shortname") or quote.get("longname") or ""
            # Only suggest symbols the watchlist POST endpoint would accept
            # (stocks/ETFs; skips futures, forex, foreign-exchange variants).
            if (
                symbol
                and SYMBOL_RE.match(symbol)
                and quote.get("quoteType") in ("EQUITY", "ETF", "INDEX")
            ):
                matches.append({"symbol": symbol, "name": name})
            if len(matches) >= 10:
                break
        if matches:
            return jsonify(matches)
    except Exception as exc:
        print(f"Warning: yfinance search failed ({exc}); using static fallback")

    return jsonify(search_static(query))


def sma(values: list[float | None], window: int) -> list[float | None]:
    """Simple moving average aligned with values; None until enough data."""
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        chunk = values[i + 1 - window : i + 1]
        if any(v is None for v in chunk):
            out.append(None)
        else:
            out.append(round(sum(chunk) / window, 4))
    return out


@app.get("/api/prices/<symbol>")
def api_prices(symbol: str):
    symbol = symbol.strip().upper()
    conn = open_db()
    try:
        # Full history so indicator EMAs/smoothing warm up correctly,
        # then sliced to the last 260 trading days below.
        rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume FROM daily_prices
            WHERE symbol = ? ORDER BY date
            """,
            (symbol,),
        ).fetchall()
    finally:
        conn.close()

    all_closes = [r[4] for r in rows]
    macd_data = macd(all_closes)
    rsi_data = rsi(all_closes)

    rows = rows[-260:]
    offset = len(all_closes) - len(rows)
    dates = [r[0] for r in rows]
    closes = [r[4] for r in rows]
    return jsonify(
        {
            "symbol": symbol,
            "dates": dates,
            "ohlc": [
                {"time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]}
                for r in rows
            ],
            "volumes": [r[5] for r in rows],
            "sma5": sma(closes, 5),
            "sma20": sma(closes, 20),
            "sma50": sma(closes, 50),
            "sma200": sma(closes, 200),
            "macd": macd_data["macd"][offset:],
            "macd_signal": macd_data["signal"][offset:],
            "macd_histogram": macd_data["histogram"][offset:],
            "rsi": rsi_data[offset:],
        }
    )


EARNINGS_RANGES = {"week", "next-week", "month"}


@app.get("/api/earnings")
def api_earnings():
    range_name = request.args.get("range") or "week"
    if range_name not in EARNINGS_RANGES:
        return jsonify({"error": "range must be week, next-week, or month"}), 400

    now = today()
    if range_name == "week":
        start, end = now, now + timedelta(days=6 - now.weekday())
    elif range_name == "next-week":
        start = now + timedelta(days=7 - now.weekday())
        end = start + timedelta(days=6)
    else:  # month: rest of the current calendar month
        start = now
        end = now.replace(day=monthrange(now.year, now.month)[1])

    conn = open_db()
    try:
        rows = conn.execute(
            """
            SELECT symbol, earnings_date, eps_estimate FROM earnings
            WHERE earnings_date BETWEEN ? AND ?
            ORDER BY earnings_date, symbol
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    return jsonify(
        [
            {"symbol": s, "earnings_date": d, "eps_estimate": e}
            for s, d, e in rows
        ]
    )


@app.get("/api/status/<symbol>")
def api_status(symbol: str):
    symbol = symbol.strip().upper()
    conn = open_db()
    try:
        row = conn.execute(
            "SELECT earnings_date, eps_estimate FROM earnings WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    finally:
        conn.close()

    if not row or not row[0]:
        return jsonify(None)

    earnings_date = datetime.strptime(row[0], "%Y-%m-%d").date()
    days_until = (earnings_date - today()).days
    if days_until < 0:
        return jsonify(None)

    return jsonify(
        {
            "next_earnings": row[0],
            "days_until": days_until,
            "eps_estimate": row[1],
        }
    )


def main() -> None:
    app.run(host=SETTINGS["web_host"], port=int(SETTINGS["web_port"]))


if __name__ == "__main__":
    main()
