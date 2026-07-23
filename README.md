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
