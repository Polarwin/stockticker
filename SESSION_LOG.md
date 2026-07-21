# Session Log — Stockticker Project

**Date:** 2026-07-20  
**Repository:** https://github.com/Polarwin/stockticker  
**Branch:** `main`

This log records the work done in this CLI session, in chronological order.

---

## 1. Initial Git Setup and First Commit

**Asked:** Set up git for the project, create `.gitignore`, write a simple `main.py` that fetches the latest AAPL price using `yfinance`, make the first commit, create a private GitHub repo named `stockticker` with `gh`, push to it, and show the repo URL.

**Done:**
- Ran `git init`.
- Wrote `.gitignore` excluding the Python venv (`bin/`, `lib/`, `lib64/`, `include/`, `share/`, `pyvenv.cfg`), `__pycache__/`, `*.pyc`, and `.env`.
- Wrote `main.py` to print `AAPL: $333.74` using `yfinance`.
- Committed: `022be75 Initial commit: add .gitignore and AAPL price fetcher`.
- Created private GitHub repo via `gh repo create stockticker --private --source=. --remote=origin --push`.
- Pushed `main` branch.

**Key decisions:**
- Used `gh` CLI because it was already authenticated and creates the remote + push in one step.
- Kept `.env` gitignored so credentials never land in the repo.
- `lib64` was a symlink to `lib`, so `.gitignore` was later adjusted to ignore it as a symlink (`lib64` without the trailing slash) in commit `4109854`.

---

## 2. Watcher Rewrite

**Asked:** Rewrite `main.py` into a watcher with a `watchlist.txt` containing AAPL/MSFT/NVDA/TSLA, timestamped output per stock, per-symbol error handling, a 10-minute loop, and a `--once` flag.

**Done:**
- Created `watchlist.txt` with the four tickers.
- Rewrote `main.py` to:
  - Read the watchlist from `watchlist.txt`.
  - Print `YYYY-MM-DD HH:MM:SS TICKER: $price`.
  - Catch errors per symbol so one bad fetch doesn't crash the run.
  - Loop every 600 seconds by default, or exit after one pass with `--once`.
- Committed: `3bcf4cb Rewrite watcher: watchlist, timestamps, per-symbol errors, --once flag`.

**Key decisions:**
- Used `argparse` for `--once` so future flags are easy to add.
- Used `time.sleep(600)` rather than a scheduler to keep dependencies minimal.

---

## 3. Telegram Alerting

**Asked:** Add Telegram alerting: load `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from `.env` using `python-dotenv`, post a single summary message after each round, make `--once` send the message too, warn (don't crash) if env vars are missing, then test and push.

**Done:**
- Added `python-dotenv` and `requests` usage to `main.py`.
- Added `send_telegram(message)` that POSTs to `https://api.telegram.org/bot<TOKEN>/sendMessage`.
- Built a single summary message with header `📈 Stock Update (timestamp)`.
- Printed a clear warning and skipped sending when credentials were missing.
- Tested `python main.py --once`; the Telegram message sent successfully.
- Committed: `6c9688b Add Telegram alerting with python-dotenv and requests`.

**Key decisions:**
- One combined Telegram message per round instead of one per ticker to avoid spam.
- Graceful degradation: missing `.env` values only warn, preserving the console watcher.

---

## 4. Watchlist Deduplication Script

**Asked:** Create a small script to remove duplicate lines from `watchlist.txt`.

**Done:**
- Created `dedup_watchlist.py`.
- Ran it; it removed 1 duplicate while preserving order and comments.
- Committed both the new script and the cleaned `watchlist.txt`: `904180d Add dedup_watchlist.py and remove duplicate watchlist entry`.

**Key decisions:**
- Normalized tickers to uppercase for duplicate detection while keeping the original file casing.

---

## 5. Add % Change vs Previous Close

**Asked:** Add % change from the previous close to each symbol, handle the single-row edge case as `N/A`, format as `AAPL: $333.74 (+0.14%)` with sign and two decimals, use 🟢/🔴 emojis in Telegram but plain ASCII in the console, then test and push.

**Done:**
- Changed fetch to `history(period="2d")` and computed `(latest - previous) / previous * 100`.
- Returned `None` for change when fewer than two rows came back.
- Added `format_change()` and `format_price_line()` helpers.
- Telegram lines got 🟢 for gains and 🔴 for losses.
- Tested `python main.py --once`.
- Committed: `4b0d754 Add % change vs previous close to console and Telegram output`.

**Key decisions:**
- Always showed the sign (`+0.14%`, `-2.21%`) to make scanning easier.
- Kept the console output emoji-free for readability in logs.

---

## 6. Sort Output by % Change

**Asked:** Sort watcher output by % change descending (biggest gainer first, biggest loser last), put errors and `N/A` results at the bottom, prefix error lines with ⚠️ in Telegram, then test and push.

**Done:**
- Added `sort_results()` to order symbols by descending change.
- Valid results came first; `N/A` and error results went to the bottom.
- Added ⚠️ prefix for error/`N/A` lines in Telegram summaries.
- Tested `python main.py --once`; output sorted correctly from `FIG: $23.95 (+2.31%)` down to `ISRG: $345.42 (-14.15%)`.
- Committed: `2e08cb1 Sort watcher output by % change descending; errors/N/A at bottom`.

---

## 7. Systemd Service Scripts

**Asked:** Create `install_service.sh` and `uninstall_service.sh` to manage a systemd service for the watcher. Requirements: dynamic path detection, correct unit-file contents, idempotent install, status + cheat sheet at the end, and commit/push without running (sudo needed).

**Done:**
- Created `install_service.sh` that:
  - Detects the project directory via `BASH_SOURCE[0]` and `pwd`.
  - Writes `/etc/systemd/system/stockticker.service` with `sudo tee`.
  - Sets `User=$(whoami)`, `WorkingDirectory=<project>`, `ExecStart=<project>/bin/python <project>/main.py`.
  - Uses `Restart=always`, `RestartSec=10`, `After/Wants=network-online.target`, `WantedBy=multi-user.target`.
  - Runs `daemon-reload`, then restarts if running or `enable --now` if not.
  - Prints `systemctl status` and a cheat sheet.
- Created `uninstall_service.sh` that stops, disables, removes the unit file, and reloads systemd.
- Made both scripts executable.
- Committed: `5311fa4 Add systemd install/uninstall scripts`.

**Key decisions:**
- Chose dynamic path detection so the script works for any user/path, not just `/home/justin`.
- Made install idempotent by checking `systemctl is-active` before deciding between `restart` and `enable --now`.

---

## 8. Validate Service Script

**Asked:** Validate `install_service.sh` without executing sudo parts: syntax check, shellcheck, dry-run the generated service file, verify paths and user, confirm idempotency, then commit/push any fixes.

**Done:**
- Ran `bash -n install_service.sh uninstall_service.sh` → syntax OK.
- `shellcheck` was not installed; downloaded the portable v0.10.0 binary to `/tmp` and ran it → both scripts passed with no warnings.
- Dry-ran the path detection and printed the generated unit file.
- Verified:
  - `User=justin` (not root).
  - `WorkingDirectory=/home/justin/Projects/stockticker` exists.
  - `/home/justin/Projects/stockticker/bin/python` exists and is executable.
  - `main.py` exists.
- Confirmed second-run behavior: the script overwrites the unit, reloads, and restarts if already active.
- Added `Environment=PYTHONUNBUFFERED=1` so logs appear immediately in `journalctl`.
- Committed: `f1c7219 Add PYTHONUNBUFFERED=1 to systemd service for immediate log output`.

**Key decisions:**
- Downloaded a portable `shellcheck` binary instead of using `apt-get` because `sudo` required an interactive password.
- Added `PYTHONUNBUFFERED=1` as a best practice for Python services under systemd.

---

## 9. Modular Refactor with Settings File

**Asked:** Split the monolithic `main.py` into modules with a `settings.json` config: move price logic to `ticker.py`, add an earnings reminder module using `Ticker.calendar`, share one Telegram sender in `notify.py`, make `main.py` an orchestrator loop with market-hours gating and a daily earnings check, keep `install_service.sh` working, test, then commit and push.

**Done:**
- Created `settings.json` with `ticker_interval_seconds` (600), `ticker_market_hours_only` (true), `market_timezone` ("America/New_York"), `market_open` ("09:30"), `market_close` ("16:00"), `earnings_remind_days` (7), `earnings_check_time` ("08:00"). `main.py` loads it with `json` and falls back to the same defaults with a warning if the file is missing or unreadable.
- Created `notify.py` with the single shared `send_telegram(message)` (same `.env` / `python-dotenv` pattern; missing credentials warn, don't crash).
- Created `ticker.py` with all price-fetching, % change, sorting, and formatting logic moved verbatim from `main.py`. It exposes `run_ticker_round(test=False)` returning `(console_lines, telegram_message_or_None)`; behavior is identical to the old `main.py` round.
- Created `earnings_reminder.py` using `yf.Ticker(ticker).calendar` (`'Earnings Date'` / `'Earnings Average'`, no `get_earnings_dates`, no lxml). `run_earnings_check(days, test=False)` prints one timestamped line per match sorted by date ascending (`IBM: earnings on 2026-07-22 (in 1 day), EPS est 2.9331`, `N/A` when the estimate is missing), warns and skips per-symbol errors, and returns the matches list.
- Rewrote `main.py` as the orchestrator:
  - Every `ticker_interval_seconds`: when `ticker_market_hours_only` is true and the market time (stdlib `zoneinfo`) is outside Mon-Fri open..close, prints `Market closed, skipping ticker round` and skips fetch+Telegram; otherwise runs the round and sends the summary.
  - Once per day at/after `earnings_check_time` (market timezone): runs the earnings check and sends one `📅 Earnings Reminder` Telegram message only when there are matches.
  - `--once` runs one ticker round + one earnings check immediately (ignores schedule) and exits; `--test` is console-only (no Telegram); `--days N` overrides `earnings_remind_days`.
- `install_service.sh` unchanged: `ExecStart` still runs `main.py`.
- Tested `bin/python main.py --once --test` and `bin/python main.py --once --test --days 30`; both passed, with `IBM: earnings on 2026-07-22 (in 1 day), EPS est 2.9331` in the output.

**Key decisions:**
- `run_ticker_round()` prints console lines itself and returns the Telegram message for the caller to send, so `--test` only gates sending in `main.py`.
- Symbols with no earnings date in their calendar are skipped silently; only real fetch/format errors warn.
- The earnings check fires on the first loop iteration at/after `earnings_check_time`, tracked by market-timezone date, so it runs exactly once per day.

---

## 10. `ticker_enabled` Setting

**Asked:** Add a `ticker_enabled` boolean (default false) to `settings.json`. When false, the loop skips all ticker rounds and only performs the daily earnings check, printing one startup line: `Ticker disabled in settings, earnings reminder active`. `--once` must override `ticker_enabled` for that single run (manual price check) while still respecting `--test`.

**Done:**
- Added `"ticker_enabled": false` to `settings.json` and to `DEFAULT_SETTINGS` in `main.py`.
- Gated the loop's ticker round (including the market-hours skip message) behind `ticker_enabled`; the daily earnings check runs regardless.
- Printed the startup line once before the loop when the ticker is disabled.
- Left the `--once` path running the ticker round unconditionally (overrides `ticker_enabled`), with `--test` still suppressing Telegram.
- Verified:
  - `bin/python main.py --once --test` still runs a full ticker round + earnings check despite `ticker_enabled: false`.
  - Loop mode with `ticker_enabled: false` prints only the startup line before 08:00 market time; with `earnings_check_time` temporarily set to `00:00`, the loop printed the startup line plus earnings matches and no ticker round (settings restored afterwards).

**Key decisions:**
- The startup line is printed once at loop start rather than every round, per the spec.
- The market-closed skip message only appears when the ticker is enabled, so a disabled ticker produces no per-round noise.

---

## 11. Local Price Database

**Asked:** Add a local SQLite price database: new `db_*` settings keys, a `db.py` schema/helper module, a `collector.py` with `update_database()`, a daily scheduled update in the loop (independent of `ticker_enabled`), a `--update-db` flag, console-only logging, tests, then commit and push.

**Done:**
- Added to `settings.json` (and `DEFAULT_SETTINGS`): `db_enabled` (true), `db_path` ("stockticker.db"), `db_update_time` ("18:00", market timezone), `db_backfill_days` (365). Added `*.db` to `.gitignore`.
- Created `db.py` (stdlib `sqlite3` only): `init_db(path)` creates `daily_prices(symbol, date, open, high, low, close, volume, PRIMARY KEY(symbol,date))` and `meta(key TEXT PRIMARY KEY, value TEXT)`; `upsert_prices(conn, symbol, rows)` uses `INSERT OR REPLACE`; `get_meta`/`set_meta` helpers; `resolve_db_path()` anchors relative paths at the project dir.
- Created `collector.py` with `update_database(settings, test=False)`:
  - First run (no `meta.last_update_date`): downloads `db_backfill_days` of daily OHLCV per symbol; later runs download only the last 5 days to cover downtime gaps.
  - Upserts all rows; per-symbol errors warn and skip.
  - On success sets `meta.last_update_date` to today (market timezone).
  - Console-only logging: `YYYY-MM-DD HH:MM:SS DB updated: N symbols, M rows total`; logs `DB already up to date` and does nothing when already current. No Telegram message.
  - `db_update_due(settings, today)` helper lets `main.py` gate the scheduled update on `meta.last_update_date < today`.
- `main.py`: once per day at/after `db_update_time` (market timezone), if `db_enabled` and the DB is due, calls `update_database()` — independent of `ticker_enabled`. New `--update-db` flag runs the update immediately and exits; works with `--test`.
- Tested:
  - `bin/python main.py --update-db --test` → backfilled 42 symbols, 14855 rows.
  - SQL check: every symbol has daily rows up to 2026-07-20 (365 rows for most; fewer for recent listings FIG/SPCX and ^VIX).
  - Second `--update-db` run → `DB already up to date (last update 2026-07-21)`.

**Key decisions:**
- Incremental updates re-download the last 5 days (not just today) so weekend/downtime gaps self-heal via `INSERT OR REPLACE`.
- `meta.last_update_date` is only written when at least one symbol succeeded, so a total outage doesn't mark the day done.
- `--update-db` still respects the up-to-date check (logs and exits) rather than force-refetching.

---

## 12. Web UI

**Asked:** Add a Flask web UI: earnings DB table + collector update, `web.py` serving a single-page app with watchlist management, symbol search, candlestick chart with SMAs, earnings calendar, and a per-symbol status endpoint; `web_host`/`web_port` settings; systemd service for the web UI; tests, then commit and push.

**Done:**
- Installed Flask 3.1.3 into the venv (`bin/pip install flask`).
- `db.py`: new `earnings(symbol TEXT PRIMARY KEY, earnings_date TEXT, eps_estimate REAL, updated_at TEXT)` table created by `init_db()`; new `upsert_earnings()` helper.
- `collector.py`: new `update_earnings(settings, test=False, conn=None)` reads `Ticker.calendar` `'Earnings Date'`[0] / `'Earnings Average'` per watchlist symbol (reusing `earnings_reminder.get_earnings_info`), upserts into the earnings table, warns and skips per-symbol errors. `update_database()` calls it after prices; new `--update-earnings` flag in `main.py` runs it alone.
- `settings.json` (+ defaults): `web_host` ("127.0.0.1"), `web_port` (8000).
- Created `web.py` (Flask):
  - `GET /` serves `static/index.html`; `GET /api/config` exposes `earnings_remind_days` for the badge threshold.
  - `GET /api/watchlist`; `POST /api/watchlist` validates `^[A-Z.]{1,6}$` (uppercased), appends if absent, then immediately fetches 1y history into `daily_prices` and earnings into `earnings`; `DELETE /api/watchlist/X` removes the line (DB rows kept).
  - `GET /api/search?q=` uses `yf.Search` (verified working), falling back to substring match over bundled `static/symbols.json` (503 S&P 500 constituents, generated from the datasets/s-and-p-500-companies CSV).
  - `GET /api/prices/X` returns the last 260 trading days from `daily_prices` with `dates[]`, `ohlc[]`, and `sma5/20/50/200[]` computed in Python (None until enough data).
  - `GET /api/earnings?range=week|next-week|month` reads the earnings table only (week = today..Sunday, next-week = next Mon..Sun, month = rest of calendar month), sorted by date.
  - `GET /api/status/X` returns `{next_earnings, days_until, eps_estimate}` or null (past dates count as null).
- Created `static/index.html`: single page, plain JS + fetch, lightweight-charts v4 from unpkg. Left panel: watchlist with autocomplete add-input (debounced `/api/search`, add on click/Enter) and per-row delete. Right panel: candlestick chart with toggleable MA5/20/50/200 line series and an earnings badge (`📅 Earnings in N days — EPS est X`) shown when `days_until <= earnings_remind_days`. Bottom: earnings calendar with This week / Next week / This month tabs. Panels stack under 768px.
- `install_service.sh` now also installs `stockticker-web.service` (same User/WorkingDirectory, `ExecStart=<project>/bin/python <project>/web.py`, `Restart=always`); `uninstall_service.sh` removes both services. `bash -n` passes on both.
- Tested:
  - `bin/python main.py --update-earnings --test` → `Earnings table updated: 39 symbols`.
  - `GET /api/watchlist` → 42 symbols; `GET /api/search?q=app` → APP/AAPL/AMAT/AAOI/APLD.
  - `GET /api/prices/IBM` → 260 days, all four SMA arrays present with correct None prefixes.
  - `GET /api/earnings?range=month` → 12 rows including IBM 2026-07-22; `GET /api/status/IBM` → `{days_until: 1, eps_estimate: 2.9331, next_earnings: "2026-07-22"}`.
  - POST KO → appended, 251 price rows + earnings fetched immediately; invalid symbol → 400; DELETE KO → removed from file, 251 DB rows kept.

**Key decisions:**
- Added `GET /api/config` (not in the spec) so the frontend knows `earnings_remind_days` for the badge without hardcoding it; `/api/status` also carries `eps_estimate` so the badge can show `EPS est X`.
- `yf.Search` is the primary search path; the bundled `symbols.json` is the offline/rate-limit fallback (symbol-prefix matches rank before substring matches).
- Web endpoints read the watchlist file directly instead of `ticker.load_watchlist()` to avoid `SystemExit` on an empty file (possible after deleting the last symbol via the UI).

---

## 13. Bind Web UI to the LAN

**Asked:** Bind the web UI to the LAN (`web_host` → "0.0.0.0"), verify the port binding, restart the web process, check ufw, print LAN URLs, then commit and push.

**Done:**
- Changed `web_host` in `settings.json` from "127.0.0.1" to "0.0.0.0" (port stays 8000).
- `ss -tlnp | grep 8000` showed a leftover `bin/python web.py` (pid 1171801, started manually, not via systemd) still bound to 127.0.0.1:8000.
- No `stockticker-web.service` exists yet (install script was never run with sudo), so `web.py` was restarted directly: killed pid 1171801, relaunched with `nohup bin/python web.py`.
- After restart: `ss -tlnp` shows `0.0.0.0:8000` listening; `curl http://127.0.0.1:8000/api/watchlist` and `curl http://192.168.0.9:8000/api/watchlist` both return 200.
- `sudo -n ufw status` needs an interactive password and `ufw` is not installed anyway, so no firewall rule is needed.
- LAN URLs for other devices: http://192.168.0.9:8000 (also reachable via Tailscale at http://100.103.88.9:8000).

**Key decisions:**
- Restarted the manually-started process in place rather than installing the systemd service, since installing requires sudo and the user runs that step themselves.

---

## Final Project State

**Tracked files:**
- `.gitignore`
- `collector.py`
- `db.py`
- `dedup_watchlist.py`
- `earnings_reminder.py`
- `install_service.sh`
- `main.py`
- `notify.py`
- `SESSION_LOG.md` (this file)
- `settings.json`
- `static/index.html`
- `static/symbols.json`
- `ticker.py`
- `uninstall_service.sh`
- `watchlist.txt`
- `web.py`

(`stockticker.db` is created at runtime and gitignored.)

**Runtime behavior:**
- `python main.py --once` runs one ticker round and one earnings check immediately (ignoring the schedule), prints timestamped output, and sends Telegram messages if credentials are configured.
- Running without `--once` loops every `ticker_interval_seconds` (600). Ticker rounds only run when `ticker_enabled` is true, and are skipped outside market hours (Mon-Fri 09:30-16:00 America/New_York); the earnings reminder runs once per day at/after 08:00 market time regardless; the price database updates once per day at/after 18:00 market time when `db_enabled` is true (prices + earnings table).
- `--update-db` updates the local SQLite price database immediately and exits (logs `DB already up to date` when current); `--update-earnings` refreshes only the earnings table.
- `--test` suppresses Telegram; `--days N` overrides the earnings look-ahead window; `--once` runs a ticker round even when `ticker_enabled` is false.
- `python web.py` serves the web UI on `web_host`:`web_port` (default 127.0.0.1:8000): watchlist management with search, candlestick charts with SMAs, earnings badge and calendar.
- All schedule/market/earnings/db/web settings live in `settings.json`; missing file falls back to built-in defaults with a warning.
- `bash install_service.sh` (requires sudo) installs the watcher and the web UI as systemd services.
- `bash uninstall_service.sh` (requires sudo) removes both.

**Repo URL:** https://github.com/Polarwin/stockticker
