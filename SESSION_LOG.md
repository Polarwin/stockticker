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

## 14. Nginx Reverse Proxy at /stockticker

**Asked:** Instead of exposing the Flask UI directly on 0.0.0.0:8000, deploy nginx so the UI is served at http://192.168.0.9/stockticker, via an install script.

**Done:**
- Changed `web_host` in `settings.json` back to "127.0.0.1" — Flask now listens on localhost only; nginx is the LAN entry point.
- Changed all frontend fetch paths in `static/index.html` from absolute (`/api/...`) to relative (`api/...`) so the page works both under the `/stockticker/` prefix and when served directly by Flask.
- Created `install_nginx.sh` (requires sudo, run by the user):
  - Reads `web_host`/`web_port` from `settings.json` for the proxy target.
  - Installs nginx via apt if missing.
  - Writes `/etc/nginx/sites-available/stockticker`: `listen 80 default_server`, `location = /stockticker` → 301 to `/stockticker/`, and `location /stockticker/` → `proxy_pass http://127.0.0.1:8000/` (trailing slash strips the prefix, so Flask keeps its plain `/` and `/api/...` routes) with standard `X-Forwarded-*` headers.
  - Removes the stock `sites-enabled/default` placeholder, symlinks the site, runs `nginx -t`, then reloads (or enables/starts) nginx.
  - Prints the LAN URL and a cheat sheet.
- Restarted the running `web.py` (no systemd service installed yet): now bound to 127.0.0.1:8000, verified with curl (`/api/watchlist` → 200, served HTML uses relative `api/` paths).
- `bash -n install_nginx.sh` passes. The nginx side itself could not be tested here — installing and reloading nginx needs the user's sudo password; run `bash install_nginx.sh`, then open http://192.168.0.9/stockticker.

**Key decisions:**
- Prefix stripping in nginx (`proxy_pass ...:8000/;`) instead of teaching Flask about the subpath — zero backend changes, and direct localhost access to Flask keeps working for debugging.
- The nginx server block takes over `default_server` on port 80 (only the placeholder welcome page is displaced).

---

## 15. Move Flask Off Port 8000

**Asked:** Use a port other than 8000 for the web UI so 8000 stays free for testing other projects.

**Done:**
- Changed `web_port` to 8010 in `settings.json` and in `main.py` `DEFAULT_SETTINGS`.
- Updated the fallback port in `install_nginx.sh` to 8010 (the script reads `settings.json` at runtime, so the generated nginx config proxies to 127.0.0.1:8010 automatically).
- Restarted `web.py`: now listening on 127.0.0.1:8010 (`curl /api/watchlist` → 200); port 8000 is free (connection refused).

**Key decisions:**
- Chose 8010 as an uncommon dev port; only the settings value and defaults changed since every consumer (web.py, install_nginx.sh) reads the port from settings.

---

## 16. Merge Install Scripts

**Asked:** There are two install scripts (`install_service.sh`, `install_nginx.sh`) — merge them into one.

**Done:**
- Folded the nginx deployment from `install_nginx.sh` into `install_service.sh`: after installing/restarting the two systemd services, the script now reads `web_host`/`web_port` from `settings.json`, installs nginx if missing, writes `/etc/nginx/sites-available/stockticker` (proxy `/stockticker/` → Flask with prefix stripping), removes the stock default site, symlinks, runs `nginx -t`, and reloads/starts nginx. The summary prints the LAN URL (http://<LAN-IP>/stockticker) plus the cheat sheet.
- Deleted `install_nginx.sh`.
- Extended `uninstall_service.sh` to also remove the nginx stockticker site (symlink + config, `nginx -t` + reload); the nginx package and the local database are left untouched.
- `bash -n` and shellcheck v0.10.0 both pass on install and uninstall scripts.
- Note: `stockticker-web.service` was already part of `install_service.sh` since the web UI commit, but had never been installed on this machine (only `stockticker.service` was). The leftover manual `nohup web.py` process was stopped so the service can take port 8010.

**Key decisions:**
- One script to run: `bash install_service.sh` now covers apps + proxy; one to undo: `bash uninstall_service.sh`.
- Uninstall removes the nginx *site* but not the nginx *package*, since nginx may host other things later.

---

## 17. Web UI Usability Improvements

**Asked:** Make the website more user-friendly.

**Done:**
- `web.py`: new `GET /api/quotes` returning latest close + % change vs previous day per watchlist symbol (from `daily_prices`).
- Rewrote `static/index.html`:
  - Watchlist rows now show live price and a green/red % change badge next to each symbol; delete button appears on hover and asks for confirmation (noting that price history is kept).
  - Autocomplete supports keyboard navigation (↑/↓ to move, Enter to pick, Esc to close) in addition to mouse.
  - Chart header: OHLC legend that follows the crosshair (falls back to the latest bar), color-coded close and % change; range buttons (1M/3M/6M/1Y) via visible logical range; a "Loading…" overlay while fetching.
  - MA toggle states and the selected symbol persist in `localStorage` across visits.
  - Earnings calendar rows are clickable (jump to that symbol's chart) and show an "In N days" column.
  - Error toasts instead of silent failures/alerts; the UI degrades gracefully when `/api/quotes` is unavailable (older backend before restart).
  - Header hint line and small polish (focus ring on input, scrollable watchlist, tighter mobile layout).
- Tested via Flask `test_client`: `/api/quotes` returns 42 symbols (IBM +0.16%, AAPL -2.14%, matching the ticker's % change), all existing endpoints still 200.

**Key decisions:**
- Quotes come from the local DB (not live fetches) so the watchlist loads instantly; values match the last ticker/collector update.
- The frontend tolerates a missing `/api/quotes` so the page keeps working until the `stockticker-web` service is restarted (needs the user's sudo).

---

## 18. Sort Web Watchlist by % Change

**Asked:** Sort the web UI watchlist by price change percentage, descending.

**Done:**
- `static/index.html`: `loadWatchlist()` now sorts symbols by `change_pct` from `/api/quotes`, descending — biggest gainer first, biggest loser last; symbols without quote data go at the bottom (alphabetical among themselves), matching the console ticker's sort rule.
- Verified the comparator with node (gainers → losers → no-data ordering correct).

**Key decisions:**
- Sorted client-side rather than in `/api/watchlist` so the raw watchlist API keeps file order while the UI applies presentation ordering. Static-only change — no service restart needed.

---

## 19. Chart Features: Volume, Markers, Price Line

**Asked:** Add lightweight-charts volume histogram, series markers, and price lines to the chart.

**Done:**
- `web.py`: `GET /api/prices/X` now also returns a `volumes[]` array (aligned with `dates[]`/`ohlc[]`) from `daily_prices`.
- `static/index.html`:
  - Volume histogram on an overlay price scale pinned to the bottom 20% of the chart, each bar green/red to match its candle (null volumes skipped).
  - Earnings series marker: a yellow `E` arrow below the bar on the next earnings date when it falls inside the loaded 1-year range (future dates beyond the last bar are skipped, since lightweight-charts only renders markers at existing data points).
  - Dashed price line at the latest close, colored by candle direction, with an axis label ("last"); replaced on each symbol change.
- Tested `/api/prices/IBM` (260 volumes) and `/api/prices/^VIX` (zero volumes handled) via test_client, then restarted the manually-run `web.py` (the systemd service stays stopped per the user's testing setup) and verified the live endpoint through nginx.

**Key decisions:**
- Volume uses the standard overlay-scale recipe (`priceScaleId: ''` + scale margins) so it never compresses the price axis.
- The earnings marker is driven by `/api/status` (`next_earnings`), so no extra endpoint was needed.

---

## 20. Fix Add Button

**Asked:** "Add symbol" doesn't work (tried PLTR); also wants a confirmation when removing a symbol.

**Done:**
- Reproduced: the backend was fine — `POST /api/watchlist {"symbol":"PLTR"}` succeeded (PLTR added with 1y history + earnings, now showing $134.85 +1.87%), and the web log showed the user's searches but never a POST. Root cause: the `#add-btn` button never had a click handler — only autocomplete click/Enter worked, since the first version of the page.
- Added the missing `add-btn` click listener (adds the uppercased input value). Verified the inline JS with `node --check`.
- Delete confirmation already exists since the usability rewrite (`confirm('Remove X from the watchlist? (price history is kept)')` in the delete handler) — the user likely had a cached page; hard refresh (Ctrl+F5) shows it.

**Key decisions:**
- Left PLTR in the watchlist (added during the reproduction) since that's what the user wanted.

---

## 21. Filter Autocomplete Suggestions to Addable Symbols

**Asked:** Autocomplete suggestions "not working" — is a database of possible symbols needed?

**Done:**
- Diagnosed: `/api/search` worked (web log showed the user's searches returning 200), but `yf.Search` returns futures, forex, and foreign-exchange variants (`PLT=F`, `AAPL.SW`, `D90.F`) that the watchlist POST validator (`^[A-Z.]{1,6}$`) rejects — clicking such a suggestion produced an "invalid symbol" error, which looked like autocomplete was broken.
- `web.py`: search now filters `yf.Search` results to `quoteType` in EQUITY/ETF/INDEX whose symbol passes the same `SYMBOL_RE` validator as the add endpoint, so every suggestion is guaranteed addable.
- Verified live: `q=plt` → PLTR/PLTK/PLA/PLTU (futures gone); `q=apple` → AAPL/APLE only (foreign listings filtered).
- No new database needed: `yf.Search` is the live, comprehensive source; the bundled `static/symbols.json` (503 S&P 500 names) stays as the offline/rate-limit fallback.

---

## 22. Fix Autocomplete Dropdown, Legend % Change, Calendar Placement

**Asked:** Dropdown doesn't appear (user verified with a screenshot and hard refresh — not a cache issue). Instrument the input handler, add a debug line, verify dropdown CSS against the real DOM, unify the % change definition (watchlist vs chart legend disagreed for BABA), and make the earnings calendar visible (move it above the chart). Verify by actually opening the page.

**Done:**
- Root cause found: the render code showed the dropdown with `acBox.style.display = ''`, which removes the inline style and falls back to the stylesheet rule `display: none` — the dropdown was populated but never visible. Fixed to `display = 'block'`.
- Input handler now logs the fetched suggestions to the console and wraps rendering in try/catch (errors logged, not silent); suggestion DOM nodes are built with `createElement`/`textContent` (no innerHTML, null-name safe).
- Added the requested debug line under the input: `debug: N suggestions received` (`#ac-debug`).
- CSS verified against the live DOM (not just by reading code): `position: absolute`, `z-index: 1000` (raised from 10), solid background, no `overflow: hidden` ancestors.
- % change unified to "vs previous close" everywhere: the chart legend previously computed intraday `(close-open)/open` (BABA showed +1.20% vs watchlist +4.67%); the crosshair handler now looks up the hovered bar's previous close and the default legend uses the last two bars.
- Moved the earnings calendar panel above the watchlist/chart layout so it's visible without scrolling.
- Verified with headless Chromium (Playwright, installed in the venv) against the nginx-served page (`/stockticker/`):
  - Console: `autocomplete suggestions for "pl": [Object]` — no exceptions, no page errors.
  - Debug line: `debug: 1 suggestions received` ("PL"; the EQUITY/ETF/INDEX filter trims Yahoo's exotics).
  - Dropdown: `display: block`, `position: absolute`, `z-index: 1000`, solid background, real bounding box (274×35 below the input), zero `overflow:hidden` ancestors.
  - Calendar renders at top=40, above the chart (top=319), with 5 rows for "This week".
  - BABA consistency: watchlist `+4.67%` == legend `C 120.34 +4.67%`.

**Key decisions:**
- Playwright + chromium-headless-shell installed into the project venv (gitignored) for DOM verification — no more guessing about frontend behavior.

---

## 23. Live Prices in the Web Watchlist

**Asked:** Show real-time prices in the web watchlist, especially on page refresh (prices were stale — from the once-daily DB update).

**Done:**
- `web.py`: `/api/quotes` now batch-fetches live quotes with a single `yf.download(..., period="2d", group_by="ticker", threads=True)` call for all watchlist symbols (~1.4s for 42 symbols) instead of reading the once-daily `daily_prices` table. Symbols missing from the batch fall back to the latest DB values; total failure falls back entirely.
- `static/index.html`: `loadWatchlist()` renders the symbol list immediately, then patches in prices and re-sorts (gainers first) when live quotes arrive — split into `renderWatchlist(symbols, quotes)`.
- Verified: live endpoint returns 42 symbols in 1.4s (BABA -1.9% live vs +4.67% stale DB); headless Chromium shows the list rendered and re-sorted with live prices (TER $363.79 +9.00% at top), no page errors. PLTR absent because the user deleted it from the watchlist while testing (its 252 DB rows are kept, as designed).

**Key decisions:**
- One batched `yf.download` instead of 42 individual `yf.Ticker` calls — ~10x faster and a single point of failure handling.
- % change stays "vs previous close" — same definition as the legend and console ticker.

---

## 24. MACD/RSI Indicators and Crossover Alerts

**Asked:** Add MACD and RSI indicators computed from the local DB, and send an alert on bullish/bearish crossovers.

**Done:**
- Created `indicators.py`: EMA (SMA-seeded), MACD (12/26/9: macd/signal/histogram), Wilder's RSI (14), and `detect_crossovers()` comparing the last two bars — MACD line vs signal line, RSI crossing up through 30 (bullish) or down through 70 (bearish). All series are None-padded through their warm-up periods.
- `collector.py`: new `check_signals(settings, test=False)` loads closes from `daily_prices` per watchlist symbol, prints one timestamped console line per signal (or "No MACD/RSI crossovers detected"), and returns the signal list.
- `main.py`: after the scheduled daily `update_database()` completes, `do_signal_check()` runs and sends one Telegram message `📊 Indicator Alerts` with lines like `AAPL: RSI bearish crossover (2026-07-20)` — only when there are signals; `--test` stays console-only. New `--signals` flag runs the check immediately and exits.
- `web.py`: `/api/prices/X` now computes MACD/RSI over the full DB history (correct EMA warm-up) and returns the last 260 values as `macd[]`, `macd_signal[]`, `macd_histogram[]`, `rsi[]` aligned with `dates[]`.
- `static/index.html`: two synced 120px sub-charts under the main chart — MACD (histogram colored by sign + macd/signal lines) and RSI (line with dashed 30/70 guides) — sharing the visible time window with the price chart in both directions (recursion-guarded), each with a persisted show/hide toggle; range buttons apply to all three charts.
- Tested:
  - Indicator sanity on 365 IBM closes: MACD warm-up at index 25, signal at 33, RSI at 14, all RSI within 0-100.
  - `bin/python main.py --signals --test` → real signals on 2026-07-20: `AAPL: RSI bearish crossover`, `GOOG/AA/NFLX: MACD bearish crossover`.
  - `/api/prices/IBM` returns all four new arrays (260 values, last MACD -12.468, RSI 31.63).
  - Headless Chromium: both panes render canvases, toggle hides the MACD pane and persists in localStorage, no page errors.

**Key decisions:**
- Crossover detection compares only the last two bars and runs once daily after the DB update, so each crossover alerts exactly once.
- Indicators are computed from up to 365 stored closes rather than the 260 shown, reducing EMA warm-up error at the left edge of the chart.

---

## 25. Portfolio Tracking in the Web Watchlist

**Asked:** Add portfolio columns to the watchlist — average buying price, quantity bought, total market value, gain/loss, and percentage of the total portfolio — with user-selectable ascending/descending sort on each column.

**Done:**
- `db.py`: new `holdings(symbol TEXT PRIMARY KEY, avg_price REAL, quantity REAL, updated_at TEXT)` table plus `get_holdings`/`upsert_holding`/`delete_holding` helpers.
- `web.py`: `GET /api/holdings` (all holdings), `POST /api/holdings {symbol, avg_price, quantity}` (validated: symbol format, numbers >= 0), `DELETE /api/holdings/<symbol>`.
- `static/index.html`: watchlist list replaced by a sortable table in a wider (660px) panel:
  - Columns: Symbol, Price, Chg%, Avg Buy, Qty, Value (qty × live price), G/L ($ and % vs avg buy), Port% (share of total position value), plus ✎/✕ actions per row.
  - Click any column header to sort; click again to flip direction (▲/▼ indicator, persisted in localStorage; symbols without data sink to the bottom).
  - ✎ edit button prompts for avg buy price and quantity, saves via `POST /api/holdings`; totals footer row shows portfolio value, total G/L, and 100%.
  - Values use the live quotes (same batched fetch as prices), so the portfolio updates on refresh.
- Verified headless: seeded IBM 10@$180.50 and AAPL 5@$300 → IBM row `$2,114.10 / +$309 (+17.12%) / 56.4%`, AAPL `43.6%`, footer `Total $3,748.85 +$444 (+13.43%)`; Value-header sort flips IBM/AAPL order desc/asc; invalid POST rejected with 400; no page errors. Test holdings were deleted afterwards, leaving an empty portfolio for the user to fill.

**Key decisions:**
- Holdings live in SQLite (not watchlist.txt) so they survive watchlist removal — deleting a symbol keeps its holding, which returns if the symbol is re-added.
- Sorting is client-side over already-fetched quotes/holdings, so it's instant and needs no new endpoints per sort.

---

## 26. Sector Allocation Heatmap

**Asked:** Add a sector allocation heatmap (treemap): group the portfolio into sectors, size each box by the sector's share of total portfolio value, color it green/red by the sector's daily change, show the change % inside each box, output a self-contained `sector_heatmap.html`, add a `python main.py --sector-heatmap` CLI command, and add tests for the aggregation math and HTML output.

**Done:**
- Installed plotly 6.9.0 into the project venv (`bin/pip install plotly`).
- `db.py`: new `sectors(symbol TEXT PRIMARY KEY, sector TEXT, updated_at TEXT)` table created by `init_db()`, plus `get_sectors()`/`upsert_sector()` helpers. New `get_latest_quotes(conn, symbols)` helper: latest close + % change vs previous close from `daily_prices` — extracted from `web.py`'s `/api/quotes` fallback so the heatmap and the web endpoint share it.
- `ticker.py`: `fetch_live_quotes()` moved here from `web.py` (batched `yf.download(..., period="2d", group_by="ticker")`); `web.py` now imports it, so the console ticker, web UI, and heatmap all use the same live-quote path.
- Created `generate_sector_heatmap.py`:
  - `fetch_sector(symbol)`: sector from `yf.Ticker(symbol).info`; per-symbol failures warn and skip; ETFs/missing data → `None` → "Unknown".
  - `aggregate_sectors(holdings, quotes, sectors)`: pure, testable math — sector value = Σ(qty × price), weight = sector value / total, change = value-weighted average of the stocks' daily changes (None when no stock in the sector has a change). Positions with quantity ≤ 0 or no price are skipped; missing sectors count as "Unknown". Rows sorted by value descending.
  - `build_heatmap_html(rows, generated_at)`: Plotly treemap — box area = sector value, red/green diverging colorscale (symmetric range, min ±3%, neutral grey midpoint for unknown change) with a "Daily change %" colorbar, sector name + change % inside each box, value/change on hover; `to_html(include_plotlyjs=True)` makes a single self-contained file, `config={"responsive": True}` for reflow.
  - `generate_sector_heatmap(settings, test=False, output_path=...)`: reads holdings with quantity > 0 from the DB, fetches and caches missing sectors in the sectors table (subsequent runs do no sector fetches), gets live quotes with DB fallback, writes `sector_heatmap.html`, prints one console line per sector. Returns None (and writes nothing) when the portfolio is empty or unpriced.
- `main.py`: new `--sector-heatmap` flag runs the generator and exits; the import is lazy so the daemon loop never loads plotly.
- `.gitignore`: `sector_heatmap.html` (generated artifact, like `*.db`).
- Created `test_sector_heatmap.py` (stdlib unittest, no network): 7 tests covering weights/values, value-weighted change, None change, "Unknown" fallback, empty/unpriced inputs, sort order, and HTML file output. All pass.
- Verified end-to-end: `bin/python main.py --sector-heatmap --test` on the real 29-position portfolio → 7 sectors (Technology 55.7%, Healthcare 20.7%, …, SPY → "Unknown" as an ETF), sectors cached for all 29 symbols, 4.8 MB self-contained HTML. Headless Chromium (Playwright): all 7 sector labels render with correct ±% text, zero page errors, clean reflow at 500px width. Flask test_client: `/api/watchlist`, `/api/holdings`, `/api/prices/IBM` all still 200 after the web.py refactor.

**Key decisions:**
- Sectors are cached in SQLite (same pattern as earnings) because `Ticker.info` is a slow per-symbol fetch — first run fetches 29 sectors, later runs read the cache.
- The heatmap prices from the live batch fetch (falling back to the DB) rather than the once-daily DB values, so the daily-change colors are current.
- Only holdings with quantity > 0 are plotted — the watchlist alone doesn't define the portfolio.

---

## 27. Serve the Sector Heatmap from the Web UI

**Asked:** The heatmap URL returned 404 (http://192.168.0.9/heatmap.html); then: regenerate the page with the most recent stock values on each visit and add a link from the main UI.

**Done:**
- `web.py`: new `GET /heatmap` (+ `/heatmap.html` alias) route. Each request calls `generate_sector_heatmap(SETTINGS)` (lazy import, so plotly only loads when the heatmap is requested) to regenerate `sector_heatmap.html` with live prices, then serves it via `send_file`. On regeneration failure it warns and falls back to the previously generated file; 404 with a hint when no file exists at all (e.g. empty portfolio).
- `static/index.html`: "🗺 Sector Heatmap" link in the header (accent color, opens in a new tab). Relative `href="heatmap"` so it works both under the nginx `/stockticker/` prefix and when Flask is accessed directly.
- Restarted `stockticker-web.service` (kill + systemd `Restart=always`, as sudo needs the user's password).
- Verified: direct Flask and nginx (`/stockticker/heatmap` and `/stockticker/heatmap.html`) all return 200; each request rewrites `sector_heatmap.html` (mtime check, ~6s per visit for the live quote fetch + 4.8 MB write); headless Chromium shows the header link with no page errors.

**Key decisions:**
- Regenerate-on-visit rather than a separate refresh button: the heatmap is a single snapshot page, so every load is current by construction. The ~6s latency is the cost of the batched live quote fetch.

---

## 28. Semiconductors Sub-Industry in the Heatmap

**Asked:** List semiconductors as a sub-industry of Technology in the heatmap, showing the semiconductor share of the whole portfolio.

**Done:**
- `db.py`: new `industry` column on the `sectors` table — added to `CREATE TABLE` plus a `PRAGMA table_info`/`ALTER TABLE` migration in `init_db()` for existing databases. `get_sectors()` now returns `{symbol: {"sector", "industry"}}`; `upsert_sector()` takes the industry. `industry=NULL` marks legacy cache rows (refetch once); `""` means yfinance reported no industry.
- `generate_sector_heatmap.py`:
  - `fetch_sector()` → `fetch_sector_info()`, returning `(sector, industry)` from `Ticker.info`.
  - `aggregate_sectors()`: any sector holding `industry == "Semiconductors"` stocks is split into child rows "Semiconductors" and "<Sector> (other)". Row schema is now `{name, parent, value, weight_pct, change_pct}` — top-level sectors value-descending, each immediately followed by its children. Parent rows carry the combined value and value-weighted change of their children, so totals stay consistent.
  - `build_heatmap_html()`: real treemap hierarchy via `ids`/`parents` with `branchvalues="total"` (Semiconductors renders as a sub-box inside Technology); hover now also shows "Weight: N.N% of portfolio" via customdata.
  - Console output indents child rows: `  Semiconductors: 19.9% of portfolio ($314,371.00, +4.61%)`.
- Tests: updated to the new row schema; new `test_semiconductor_split` (child weights vs whole portfolio, parent = sum of children, parent change = weighted average, ordering) and `test_no_split_without_semiconductors`. 9 tests, all pass.
- Verified: `bin/python main.py --sector-heatmap --test` → Technology 55.7% splits into Technology (other) 35.8% and **Semiconductors 19.9%** of the whole portfolio; headless Chromium renders the parent/child boxes with no page errors (screenshot checked). Restarted `stockticker-web.service` so the web process drops the old cached module; nginx `/stockticker/heatmap` serves the split.

**Key decisions:**
- The split is driven by yfinance's `industry` field (exact match "Semiconductors"), not a hand-maintained ticker list — new semiconductor holdings are picked up automatically.
- `branchvalues="total"` (parent value = sum of children) instead of "remainder" so parent boxes keep their real value in hovers.

---

## 29. Extended-Hours (Pre/Post-Market) Prices

**Asked:** The prices come from yfinance regular-trading-hours data — use the most recent price including pre- and post-market instead.

**Done:**
- `ticker.py` `fetch_live_quotes()` rewritten: two batched `yf.download` calls — daily bars (`period="5d"`) for the previous regular-session close, and 1-minute bars (`period="1d", interval="1m", prepost=True`) for the most recent price including extended hours. Per symbol: price = last prepost minute bar (falls back to the last daily close when the intraday fetch fails or has no data); change_pct = (price − previous regular close) / previous regular close, where "previous" is the last daily close before the date of the price bar. Same change definition as before, so the console ticker, web watchlist, and heatmap stay consistent.
- Simplified the single-symbol path: with `group_by="ticker"`, `data[symbol]["Close"]` works for one symbol or many (yfinance 1.5.1 returns MultiIndex columns even for a single symbol — the old `len(symbols) > 1` special case was broken for single-symbol calls; fixed and covered by a manual check: single/multi/empty all return correctly).
- Verified live at 05:33 ET (pre-market): NVDA 205.13 −1.04% vs yesterday's regular close 207.29 (old code showed 207.29 from the daily bar); MSFT/BABA/SPY all consistent with their previous regular closes.
- Regenerated the heatmap: sectors now colored by pre-market changes (Technology −0.80%, Semiconductors −1.64%). Restarted `stockticker-web.service`; `/api/quotes` serves pre-market prices (NVDA 205.05 −1.08%). 9/9 unit tests still pass (aggregation tests don't touch the network).

**Key decisions:**
- Changed the one shared quote function rather than the heatmap alone — the web watchlist and heatmap now show the same extended-hours price with the same change definition.
- Kept % change measured against the previous regular-session close (not the previous minute bar or today's open) so extended-hours moves read as a daily change.

---

## Final Project State

**Tracked files:**
- `.gitignore`
- `collector.py`
- `db.py`
- `dedup_watchlist.py`
- `earnings_reminder.py`
- `generate_sector_heatmap.py`
- `indicators.py`
- `install_service.sh`
- `main.py`
- `notify.py`
- `SESSION_LOG.md` (this file)
- `settings.json`
- `static/index.html`
- `static/symbols.json`
- `test_sector_heatmap.py`
- `ticker.py`
- `uninstall_service.sh`
- `watchlist.txt`
- `web.py`

(`stockticker.db` and `sector_heatmap.html` are created at runtime and gitignored.)

**Runtime behavior:**
- `python main.py --once` runs one ticker round and one earnings check immediately (ignoring the schedule), prints timestamped output, and sends Telegram messages if credentials are configured.
- Running without `--once` loops every `ticker_interval_seconds` (600). Ticker rounds only run when `ticker_enabled` is true, and are skipped outside market hours (Mon-Fri 09:30-16:00 America/New_York); the earnings reminder runs once per day at/after 08:00 market time regardless; the price database updates once per day at/after 18:00 market time when `db_enabled` is true (prices + earnings table).
- `--update-db` updates the local SQLite price database immediately and exits (logs `DB already up to date` when current); `--update-earnings` refreshes only the earnings table; `--signals` checks MACD/RSI crossovers and exits. After the scheduled daily DB update, crossover alerts are checked automatically and sent via Telegram when found.
- `--sector-heatmap` generates `sector_heatmap.html` — a self-contained Plotly treemap of the portfolio grouped by sector (box size = sector weight, green/red = value-weighted daily change) — and exits. Sectors are cached in the `sectors` DB table; ETFs/unknowns fall into "Unknown". Tests: `bin/python -m unittest test_sector_heatmap`.
- `--test` suppresses Telegram; `--days N` overrides the earnings look-ahead window; `--once` runs a ticker round even when `ticker_enabled` is false.
- `python web.py` serves the web UI on `web_host`:`web_port` (default 127.0.0.1:8010): watchlist management with search, candlestick charts with SMAs, earnings badge and calendar.
- All schedule/market/earnings/db/web settings live in `settings.json`; missing file falls back to built-in defaults with a warning.
- `bash install_service.sh` (requires sudo) installs the watcher and the web UI as systemd services AND deploys nginx as a reverse proxy, so the UI is reachable on the LAN at http://<LAN-IP>/stockticker while Flask stays on localhost.
- `bash uninstall_service.sh` (requires sudo) removes both services and the nginx site.

**Repo URL:** https://github.com/Polarwin/stockticker
