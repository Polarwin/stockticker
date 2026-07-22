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
daily bars (yfinance, falling back to the local database). The same score and
indicator breakdown also appear in the web UI (`python web.py`) as a
"Confluence Score" table under the price chart whenever a symbol is selected
(hidden for symbols with too little history).

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

## Other commands

- `python main.py --sector-heatmap` — sector allocation treemap (`sector_heatmap.html`)
- `python main.py --signals` — MACD/RSI crossover alerts
- `python main.py --update-db` / `--update-earnings` / `--earnings-reports`
- `python web.py` — web UI on 127.0.0.1:8010 with built-in notifications

## Tests

```
bin/python -m unittest test_indicators_table -v
bin/python -m unittest test_sector_heatmap -v
```
