# stockticker

Personal stock watcher: watchlist price alerts, earnings reminders and
post-earnings watches over Telegram, a local SQLite price database, a web UI
(charts, watchlist management), a sector allocation heatmap, and a technical
indicators confluence table.

## Indicators table

```
python main.py --indicators-table
```

Generates `indicators_table.html` — a self-contained, dark-themed page with a
bullish/bearish confluence score (0–100) per watchlist symbol, computed from
daily bars (yfinance, falling back to the local database). In the web UI
(`python web.py`) the "Confluence Score" table under the price chart shows the
same five base rows plus two bonus rows — Candlestick Pattern (±20/±15 by
tier) and Options Flow put/call ratio (±8/±4) — stacked on the base score and
clamped to 0-100, matching the premarket report's score exactly (hidden for
symbols with too little history). The chart itself can overlay Bollinger
Bands (20, 2) via the "BB (20,2)" toggle, and the header links to the
premarket report page.

### How to read it

- **Summary table** — every symbol's score and signal label, best first.
- **Per-symbol card** — the score bar (red → gray → green, white marker at
  the score) and one row per indicator: its current value, its vote
  (▲ bullish / ▼ bearish / ● neutral), its reliability bar, its weight, and
  the signed points it contributed.
- **Score** — each indicator contributes `±weight × reliability` (bullish
  adds, bearish subtracts, neutral is 0). The raw total is normalized so 50 =
  perfectly balanced and 0/100 = all five indicators voting the same way.
- **Signal labels** — ≥ 70 Strong Bullish, 50–69 Moderate Bullish,
  30–49 Neutral, 10–29 Moderate Bearish, < 10 Strong Bearish.

### Indicators and weights

| Indicator | Weight | Reliability | Bullish when | Bearish when |
|---|---|---|---|---|
| RSI (14) | 30 | 79% | < 30, or 50–70 in an uptrend | > 70, or < 50 in a downtrend |
| Bollinger Bands (20, 2) | 25 | 78% | close at/below lower band | close at/above upper band |
| MACD (12, 26, 9) | 20 | 40% | MACD above signal line | MACD below signal line |
| EMA Trend (9 vs 21) | 15 | 31% | EMA9 > EMA21 | EMA9 < EMA21 |
| Volume vs SMA (20) | 10 | 55% | volume > SMA on an up day | volume > SMA on a down day |

"Uptrend/downtrend" for RSI is EMA9 vs EMA21. MACD votes by its position
relative to the signal line (the state the latest crossover left behind)
rather than requiring a crossover on the very last bar.

Reliability is a historical **win rate** — how often the signal pointed the
right way in backtests — **not** an expected return. A high win rate with
small average gains can underperform a low win rate with large gains, so the
low-reliability indicators (MACD, EMA trend) carry small weights and should
only confirm, never drive a decision alone.

## Premarket report

```
python main.py --premarket-report             # full portfolio briefing
python main.py --premarket-report --ticker TSM  # single-stock deep dive
```

Runs automatically on weekdays at `premarket_check_time` (08:45 ET) from the
web process, sending a concise Telegram briefing and writing the full report to
`premarket_report.html` (served at `/premarket`). Covers every holding across
eight sections: market overview (ES/NQ futures, VIX, 10-year yield, overnight
headlines), earnings calendar (EPS/revenue estimates, beat history, BMO/AMC
timing), news sentiment (VADER over the last 24h of headlines), options flow
(put/call ratio, unusual-volume flags), candlestick reversal patterns
(engulfing, stars, hammer/shooting star, doji), the confluence score (the
indicators-table base score plus pattern/sentiment/options bonuses), premarket
movers, and auto-generated action items.

News headlines come from Finnhub, falling back to Alpha Vantage and then
yfinance; options chains always come from yfinance. Set `FINNHUB_API_KEY`
and/or `ALPHAVANTAGE_API_KEY` in `.env` to enable the paid sources — without
them everything still works on yfinance. The "unusual volume" flag needs a
baseline that accumulates in the `options_volume` DB table (~a week of daily
snapshots); until then only the put/call ratio is reported.

## Fundamentals

The technical confluence score tells you **when** to trade; the fundamental
score tells you **what** to own. The `fundamentals/` package fetches company
financials from yfinance into a local SQLite database (`data/fundamentals.db`),
then computes valuation ratios, a moat score, a DCF valuation, peer
comparisons, and a composite 0–100 fundamental score per ticker.

```
python main.py --update-fundamentals --ticker AAPL   # fetch + store one ticker
python main.py --update-fundamentals --all           # whole watchlist
python main.py --fundamental-dashboard               # fundamental_dashboard.html (+ reports/)
python main.py --fundamental-report --ticker AAPL    # console report + JSON files in reports/
python main.py --dcf-valuation --ticker AAPL         # DCF breakdown + 5x5 sensitivity grid
python main.py --moat-score --ticker AAPL            # moat score + component breakdown
python main.py --peer-comparison --ticker AAPL       # stored peer table
python main.py --valuation-history --ticker AAPL --years 5
python main.py --check-earnings                      # Telegram alert for today's reporters
python main.py --premarket-report --include-fundamentals   # append score lines
# Cron: weekday evening earnings-day alerts
# 0 19 * * 1-5 cd ~/Projects/stockticker && bin/python main.py --check-earnings
```

### How to read it

- **Moat score (0–100)** — five components: Pricing Power (gross margin,
  25 pts), Capital Efficiency (ROIC, 25), Profitability (ROE, 20), Growth
  Consistency (5yr revenue CAGR, 15), Financial Strength (debt/equity, 15).
  Missing components are rescaled over what's available. Ratings:
  ≥ 80 Wide Moat, ≥ 60 Narrow, ≥ 40 Weak, else No Moat.
- **DCF valuation** — 5-year FCF-per-share projection (growth = 5yr revenue
  CAGR × 0.8, capped at 25%, floored at −10%), terminal growth 2.5%, discount
  rate = risk-free (^TNX) + beta × 5.5% clamped to [3.5%, 20%]. Margin-of-
  safety bands: > 30% Strong Buy, > 15% Buy, > 0% Fair Value, > −15% Slightly
  Overvalued, else Overvalued.
- **Fundamental score (0–100)** — valuation percentiles vs own history
  (30 pts), moat (25), 3yr revenue growth (20), balance-sheet stability (15),
  earnings beat streak (10).
- **Data caveats** — yfinance provides ~5 quarters of quarterly statements and
  ~4 years of annuals, so true 5-year CAGRs and the 20-snapshot history
  percentiles build up as daily snapshots accumulate; guidance fields are
  usually N/A (no reliable free source). ADRs (TSM, NOK, ASML, BABA, PDD,
  VALE…) report statements in their home currency: statement money values
  are FX-converted to the listing currency at the current rate before any
  ratio math, and per-share figures use ADR-equivalent share counts.
  Indexes/ETFs (`^VIX`, SPY, QQQ) are auto-skipped via yfinance `quoteType`
  and cached in `data/non_equity.json` (delete the file to re-detect).

## Other commands

- `python main.py --premarket-report` — premarket briefing (Telegram + `premarket_report.html`); `--ticker SYM` for a single-stock deep dive
- `python main.py --sector-heatmap` — sector allocation treemap (`sector_heatmap.html`)
- `python main.py --signals` — MACD/RSI crossover alerts
- `python main.py --update-db` / `--update-earnings` / `--earnings-reports`
- `python web.py` — web UI on 127.0.0.1:8010 with built-in notifications

## Tests

```
bin/python -m pytest                 # full suite (patterns, premarket report, ...)
bin/python -m unittest test_indicators_table -v
bin/python -m unittest test_sector_heatmap -v
```
