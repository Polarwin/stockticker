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

## Final Project State

**Tracked files:**
- `.gitignore`
- `dedup_watchlist.py`
- `install_service.sh`
- `main.py`
- `SESSION_LOG.md` (this file)
- `uninstall_service.sh`
- `watchlist.txt`

**Runtime behavior:**
- `python main.py --once` fetches prices for all tickers in `watchlist.txt`, sorts them by % change, prints timestamped output, and sends a Telegram summary if credentials are configured.
- Running without `--once` repeats every 10 minutes.
- `bash install_service.sh` (requires sudo) installs the watcher as a systemd service.
- `bash uninstall_service.sh` (requires sudo) removes it.

**Repo URL:** https://github.com/Polarwin/stockticker
