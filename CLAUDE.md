# Stan — Options Trading Dashboard

## Project
Custom web dashboard for daily options trading research and position management. Replaces Google Sheets with Supabase + Streamlit.

## Status: Phase 2 LIVE — Sandbox trading + Position Tracker (2026-03-18)

## Live URL
- **Dashboard:** https://options-dashboard-stan.streamlit.app/
- **GitHub:** https://github.com/vinceleyson-eng/options-dashboard (public)
- **Hosting:** Streamlit Community Cloud (free tier)
- **Secrets:** Configured in Streamlit Cloud Advanced Settings (not in repo)

## TastyTrade Sandbox
- **Sandbox account:** 5WT87999 (Individual, Cash, $1M) — Stan's sandbox
- **Second sandbox:** 5WW77042 (Individual, Cash, $100K)
- **Sandbox user:** stanvince
- **OAuth app:** stanvince Sandbox OAuth2 App
- **Client ID:** 1a545b06-77cc-4e4e-8174-6c1145961aad
- **Mode toggle:** `TASTYTRADE_MODE` env var — "sandbox" or "live"
- **Daily reset:** Sandbox trades/positions/balances reset every 24h — Supabase is source of truth

## Architecture
- **Database:** Supabase (PostgreSQL)
- **Frontend:** Streamlit dashboard (deployed on Streamlit Cloud)
- **Data source:** Tastytrade API (existing `daily_scan.py` in `../tasty-trade/`)
- **Automation:** n8n (existing workflows, updating destination from Google Sheets to Supabase)
- **Position tracking:** Checkbox → confirmation dialog → two paths: Track Position (Sheets) or Place Order (TastyTrade)
- **Google Sheets integration:** Position Tracker sheet (`1F2jvkbnAFDMZQ_BbMXyVLVFgAutKrZ2QMSUKzy0RUXE`) — one tab per **trade** (e.g., `ADBE  260515P00215000 (20260320)`), same contract on different day = different tab
- **Order flow:** Dry-run validation → buying power check → confirm & place order
- **Secrets:** `st.secrets` on Streamlit Cloud, `.env` for local dev (via `get_secret()` helper)
- **Google SA:** `[google_service_account]` section in Streamlit secrets, local file at `C:/Users/acer/.claude/credentials/google-service-account.json`

## Supabase
- **Project ID:** tdzaxiwzbbqockidasfq
- **Instance URL:** https://tdzaxiwzbbqockidasfq.supabase.co
- **Credentials:** `.env` in project folder

## Database Schema

### `daily_scans` — Daily scan metadata
| Column | Type | Notes |
|--------|------|-------|
| id | uuid | PK |
| scan_date | date | unique, one row per day |
| vix | numeric | VIX at time of scan |
| risk_free_rate | numeric | |
| created_at | timestamptz | |

### `scan_options` — Individual option rows from each scan
| Column | Type | Notes |
|--------|------|-------|
| id | uuid | PK |
| scan_id | uuid | FK → daily_scans.id |
| symbol | text | e.g., MU, NVDA |
| name | text | Company name |
| iv_rank | numeric | IVR percentage |
| iv | numeric | Raw implied volatility (decimal, e.g. 0.577 = 57.7%). Used to calculate Expected Move. Populated from daily_scan.py → g.volatility (Greeks streamer). |
| dte | integer | Days to expiration |
| delta | numeric | |
| exp_date | date | Expiration date |
| pop | numeric | Probability of Profit (%) |
| p50 | numeric | Probability of 50% profit (%) |
| strike | numeric | |
| bid | numeric | |
| ask | numeric | |
| bid_ask_spread | numeric | |
| put_price | numeric | Mid price |
| earnings | date | Next earnings date |
| underlying_price | numeric | |
| selected | boolean | DEFAULT false — checkbox column |
| created_at | timestamptz | |

### `positions` — Open position reports (generated when checkbox ticked)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid | PK |
| scan_option_id | uuid | FK → scan_options.id |
| symbol | text | |
| name | text | |
| option_type | text | 'Put' |
| strike | numeric | |
| exp_date | date | |
| price_paid | numeric | Put price at time of selection |
| quantity | integer | DEFAULT 1 |
| direction | text | 'Short' |
| opened_at | timestamptz | When checkbox was ticked |
| closed_at | timestamptz | NULL until closed |
| status | text | 'open' / 'closed' |

### `position_snapshots` — Daily P&L tracking for open positions
| Column | Type | Notes |
|--------|------|-------|
| id | uuid | PK |
| position_id | uuid | FK → positions.id |
| snapshot_date | date | |
| dte | integer | |
| share_price | numeric | Underlying price |
| option_price | numeric | Current option price |
| difference | numeric | share_price - strike |
| pl | numeric | Unrealized P&L ($) |
| created_at | timestamptz | |

### `shadow_positions` — Auto-created for every scan option (analytics)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid | PK |
| scan_option_id | uuid | FK → scan_options.id |
| scan_date | date | Date of the scan |
| symbol | text | |
| name | text | |
| strike | numeric | |
| exp_date | date | |
| put_price | numeric | Premium at scan time |
| underlying_price | numeric | |
| delta | numeric | |
| iv_rank | numeric | |
| pop | numeric | |
| p50 | numeric | |
| dte | integer | |
| created_at | timestamptz | |

### `shadow_snapshots` — Daily P&L tracking for shadow positions
| Column | Type | Notes |
|--------|------|-------|
| id | uuid | PK |
| shadow_position_id | uuid | FK → shadow_positions.id (CASCADE) |
| snapshot_date | date | |
| dte | integer | |
| share_price | numeric | |
| option_price | numeric | |
| strike | numeric | |
| difference | numeric | |
| pl | numeric | |
| created_at | timestamptz | |

### `config` — Scanner configuration (replaces Google Sheets Config tab)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid | PK |
| key | text | e.g., 'symbols', 'delta_min' |
| value | text | JSON or plain value |
| updated_at | timestamptz | |

## Data Retention
- Minimum 60 days of daily reports (per Stan's requirement)
- No hard delete — can archive older data if needed

## Existing Google Sheets (to migrate FROM)
- **Scanner data:** `1yN1tn0EXseDW9sf6SWOehxZKy3LX09dOdsoyGdhaGlk`
- **Position tracker:** `1F2jvkbnAFDMZQ_BbMXyVLVFgAutKrZ2QMSUKzy0RUXE`

## Key Files (this folder)
- `dashboard.py` — Streamlit app (run with `streamlit run dashboard.py`)
- `push_to_supabase.py` — pushes scan_results.json to Supabase + auto-creates shadow positions
- `position_tracker_daily.py` — daily cron: fetches live prices, writes snapshots to Supabase + appends rows to Google Sheets + rebuilds Summary sheet
- `backfill_sheets.py` — rebuilds all Google Sheet tabs from Supabase scan_options + snapshots (run manually when sheet needs full refresh). Interpolates dummy prices for gap dates. **Deletes all existing tabs** — run `rebuild_missing_and_summary.py` AFTER this script.
- `rebuild_missing_and_summary.py` — recreates missing contract tabs + rebuilds Summary sheet (run manually, must run AFTER `backfill_sheets.py`)
- `backfill_april1.py` — one-time script that interpolated April 1st scan from March 31 + April 2 data (already run)
- `migrate_sheets_to_supabase.py` — one-time migration from Google Sheets (already run)
- `schema.sql` — database schema (already applied)
- `.env` — Supabase + Tastytrade credentials
- `requirements.txt` — Python dependencies

## Infrastructure (../tasty-trade/) — Updated
- `daily_scan.py` — scans 15 stocks via Tastytrade API → `scan_results.json` (now reads config from Supabase)
- `push_to_sheets.py` — pushes to Google Sheets (kept as backup)
- `n8n_runner.py` — updated: pushes to Supabase first, then Sheets as backup. New endpoint: `/run/push-to-supabase`
- `position_tracker.py` — tracks open positions with P&L

## How to Run
```bash
# Dashboard
cd options-dashboard
streamlit run dashboard.py

# Manual scan + push
cd tasty-trade
python daily_scan.py
cd ../options-dashboard
python push_to_supabase.py
```

## Data Migrated
- 19 scan dates: 2026-03-09 through 2026-04-02 (April 1 interpolated)
- Monthly expirations only (weeklies purged 2026-03-16)
- 33 open positions across 15 symbols
- 1000 shadow positions for analytics
- Mar 9-16: 219+ options across 6 dates

## Scan Data Columns (Stan's display order, 2026-03-20)
**Select** (checkbox), Symbol, Company, **Strike, Put Price, DTE, POP**, IVR, Delta, Exp Date, P50, Bid, Ask, Spread, Underlying, Earnings

## Scanner Rules
- **Monthly expirations only** — weeklies filtered out (Stan's requirement 2026-03-16)
- Filter: `expiration_type == "Regular"` in `daily_scan.py:find_all_valid_expirations()`
- In TastyTrade, monthly = no "W" marker, weekly = has "W" marker
- Change applied in `../tasty-trade/daily_scan.py`

## Phases
1. **Dashboard + Position Reports** (DONE) — Supabase tables, Streamlit UI, checkbox → position report
2. **TastyTrade Sandbox** (DONE 2026-03-16) — checkbox → confirmation dialog → dry-run → place order on sandbox
3. **Moomoo Paper Trading** — checkbox also places order in Moomoo paper trade (waiting Stan's specs)

## Dashboard Features (v2.0)
- **5 pages:** Daily Research, Open Positions, Position History, Shadow Positions, Config
- **Calendar date picker** in sidebar (snaps to nearest available scan date)
- **Light/Dark theme** via Streamlit native settings (hamburger menu > Settings > Theme)
- **Data table** using `st.data_editor` — spreadsheet-style grid with checkboxes, dollar formatting, scrollable
- **TastyTrade account panel** in sidebar — shows account number, cash balance, net liq, SANDBOX/LIVE badge
- **Trade confirmation dialog** (`@st.dialog`) — pops up when checkbox ticked:
  1. Shows order details: symbol, strike, exp, limit price, direction
  2. **"Track Position"** button → creates formatted tab in Google Sheets Position Tracker + Supabase position (no order placed)
  3. "Validate Order" button → dry-run on TastyTrade, shows buying power impact + fees
  4. "Confirm & Place Order" button → executes real order, records in Supabase
  5. "Cancel" button → closes dialog without action
- **Google Sheets Position Tracker** — one tab per **trade** (e.g., `ADBE  260515P00215000 (20260320)`). Tab name = OCC symbol + opened date. Same contract on different day = separate tab. Format: dark blue title, white info rows, dark blue data headers, light gray data rows.
  - **Header rows (white, no borders):**
    - Row 1: Title (dark blue, merged) — "Position: COMPANY (SYMBOL) — STRIKE Put"
    - Row 2: Symbol | Name | Strike | Price Paid | IVx | Range (12 cols)
    - Row 3: Expiration | Quantity | Direction | Purchase Date | VIX
  - **Data columns (9):** Date, OCC, Expiration, DTE, Share Price, Strike, Difference, Option Price, P&L
  - **P&L formula:** `Price Paid - Option Price` (positive = profit for short put, option price dropped)
  - **Dedup:** By (date, OCC) — one row per date per contract
  - **Tab naming:** `build_tab_name()` returns `OCC (YYYYMMDD)` where date is when trade was opened. Same contract on different day = different tab. (Same in dashboard.py, position_tracker_daily.py, backfill_sheets.py)
  - **Daily update:** `position_tracker_daily.py` appends new rows with live prices from TastyTrade + rebuilds Summary
  - **Backfill:** `backfill_sheets.py` rebuilds all tabs from scan_options + snapshots data. Gap dates filled with interpolated dummy prices (±3% noise between real data points).
  - **Summary sheet** — first tab, all open positions sorted by purchase date: OCC (clickable hyperlink to trade tab), Symbol, Company, Strike, Expiration, Purchase Date, Price Paid, Current Price, P&L, Status, IVR, VIX, IVx, Range. Rebuilt daily by cron + appended on new trades from dashboard.
- **OCC/OSI symbol** — each position identified by standard 21-char code (e.g., `ADBE  260515P00225000`). Built by `build_occ_symbol()`. Tab name adds opened date: `OCC (YYYYMMDD)`. Same OCC on different day = different tab/trade. Dedup prevents same OCC+date from being inserted twice (uses `FORMATTED_VALUE` to read dates correctly from Sheets).
- **Shadow database** — auto-creates `shadow_positions` for every option in each scan (for analytics). Separate from user-selected `positions`. Backfilled 618 rows across 9 dates.
- **Order type:** Sell-to-Open short put, Limit at mid price (put_price), Day order, Qty 1
- **Filter by symbol**, sort by IVR/POP/P50/Delta/DTE, show selected only
- **Position cards** with expandable daily P&L snapshots, close position button
- **CSV export buttons** on Daily Research (`options_{date}.csv`), Open Positions (`open_positions_{today}.csv`), Position History (`position_history_{today}.csv`) — added 2026-03-17
- **Shadow Positions page** — browse all auto-tracked positions, filter by symbol/date, summary by symbol (avg premium, avg POP, avg delta, scan dates), CSV export. Added 2026-03-20.
- **Select column** — `CheckboxColumn` (not a button). Streamlit has no `ButtonColumn` — per-row buttons break horizontal scroll. Checkbox ticked → trade dialog opens.
- **Daily Research** — shows watchlist symbols only (from config). Defaults to latest scan date. Filter by date + symbol multiselect. Sort by Scan Date (default, newest first), Symbol, IVR%, POP%, P50%, Delta, DTE. No "Total Options" metric (removed to avoid client confusion).

## Trading Flow (Phase 2)
1. User ticks checkbox on an option in Daily Research
2. Confirmation dialog opens with order details + SANDBOX/LIVE badge
3. **Path A — Manual Track:** Click "Track Position" → tab created in Google Sheets + Summary row appended + position in Supabase (no broker order)
4. **Path B — Broker Order:** Click "Validate Order" → dry-run sent to TastyTrade API
5. Buying power impact, fees, and warnings displayed
6. Click "Confirm & Place Order" → real order placed on TastyTrade + Sheets tab + Summary row
7. **After any action:** dialog closes, dashboard auto-refreshes (`st.rerun()`), row becomes disabled
8. **Row disabling:** positions matched by scan_option_id only — same contract on a different scan date can be ordered again (creates separate tab with different opened date)
9. OCC symbol format: `SYMBOL  YYMMDDP00STRIKE000` (e.g., `NVDA  260417P00220000`). Tab name: `OCC (YYYYMMDD)` (e.g., `NVDA  260417P00220000 (20260330)`)

## Key Decisions
- Supabase over Google Sheets: no row limits, real-time triggers, proper relational data
- Supabase over Airtable: no 1,000 record free tier limit, Vince already runs 3 instances
- Streamlit for dashboard: Python-based (matches existing stack), free deployment, fast to build
- Theme: uses Streamlit native theming (not CSS override) — CSS can't reach data_editor iframe
- Google Sheets kept as backup: n8n pushes to both Supabase and Sheets on each scan

## Automation — Windows Scheduled Task
- **Task name:** "Options Dashboard - Daily Scan" (in Windows Task Scheduler)
- **Schedule:** Mon–Fri at 10:00 PM GMT+8 (= 10:00 AM ET, US market open)
- **Script:** `daily_scan_cron.bat` → runs 3 steps:
  1. `daily_scan.py` — scan TastyTrade for options
  2. `push_to_supabase.py` — push scan data + create shadow positions
  3. `position_tracker_daily.py` — fetch live prices, write P&L snapshots to Supabase + Google Sheets
- **Python path:** Uses full path `C:\Users\acer\AppData\Local\Programs\Python\Python313\python.exe` (Task Scheduler doesn't inherit PATH)
- **Logs:** `options-dashboard/logs/scan_YYYYMMDD.log`
- **Monitoring:** Healthchecks.io (`https://hc-ping.com/052fc046-9af9-47aa-b04e-309917304c2b`) — pings /start at begin, /success or /fail at end. Alerts via email (vince.leyson@gmail.com) + Slack if cron misses 2-hour grace window.
- **Requires:** Laptop on and awake at 10 PM; skips if missed
- **Replaces:** n8n workflows (had issues)

## n8n Workflows (deprecated — replaced by Windows Task Scheduler)
- **Location:** `../tasty-trade/n8n/`
- `tastytrade_daily_scan.json` — Cron trigger (weekdays 10:00 AM ET) → daily scan + push
- `tastytrade_position_tracker.json` — Position tracking workflow

## Known Issues
- **tasty-trade/.env** was missing Supabase credentials — caused daily_scan.py config load to fail. Fixed 2026-03-18.
- **Mar 17 data** had null POP/P50/underlying_price — backfilled with default IV=0.6 (approximate). Future scans use live Greeks.
- **Bid/Ask null outside market hours** — scanner only gets real bid/ask quotes during market hours (9:30 AM–4:00 PM ET). Pre-market scans will have null bid/ask but valid put_price (theo price). Scheduled task runs at 10 AM ET to avoid this.
- **Sandbox Cash account** has $0 equity buying power for naked puts — may need Reg T margin upgrade
- **IPv6 timeout** — httplib2 tries IPv6 first which times out on Vince's network. All Google Sheets scripts patched with IPv4-only socket fix (2026-04-03).
- **Google Sheets rate limit** — 60 writes/minute. Full backfill of 47 tabs hits this. `rebuild_missing_and_summary.py` picks up failed tabs. Always run after `backfill_sheets.py`.
- **DTE gap** — changed dte_min from 45 to 40 (2026-04-03) because May 15 expiry fell below 45-day threshold causing empty scans.
- **April 1st scan** — missed (cron didn't run). Backfilled by interpolating March 31 + April 2 data (`backfill_april1.py`). VIX 27.57, 90 options.

## Next Steps
- Stan reviews dashboard and workflow (in progress)
- Phase 3 specs from Stan after manual tracking period
- Consider upgrading sandbox account to Reg T margin (currently Cash — $0 buying power for naked puts)
