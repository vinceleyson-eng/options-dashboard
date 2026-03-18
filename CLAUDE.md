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
- **Google Sheets integration:** Position Tracker sheet (`1F2jvkbnAFDMZQ_BbMXyVLVFgAutKrZ2QMSUKzy0RUXE`) — creates formatted tabs with daily P&L tracking
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
- `push_to_supabase.py` — pushes scan_results.json to Supabase (replaces push_to_sheets.py)
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
- 8 scan dates: 2026-03-09 through 2026-03-18
- Monthly expirations only (weeklies purged 2026-03-16)
- Mar 18: 106 options (fresh scan with live POP/P50)
- Mar 17: 113 options (backfilled POP/P50 with default IV=0.6)
- Mar 9-16: 219+ options across 6 dates

## Scan Data Columns (15 + checkbox)
Symbol, Name, IVR, DTE, Delta, Exp Date, POP, P50, Strike, Bid, Ask, Bid-Ask, Put Price, Earnings, Underlying Price, **Select** (checkbox)

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
- **4 pages:** Daily Research, Open Positions, Position History, Config
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
- **Google Sheets Position Tracker** — new tabs created with exact formatting: dark blue headers, borders, alternating row colors, number formats, first data row auto-populated with Date/DTE/Share Price/Strike/Option Price/P&L
- **Order type:** Sell-to-Open short put, Limit at mid price (put_price), Day order, Qty 1
- **Filter by symbol**, sort by IVR/POP/P50/Delta/DTE, show selected only
- **Position cards** with expandable daily P&L snapshots, close position button
- **CSV export buttons** on Daily Research (`options_{date}.csv`), Open Positions (`open_positions_{today}.csv`), Position History (`position_history_{today}.csv`) — added 2026-03-17
- **Select column** — `CheckboxColumn` (not a button). Streamlit has no `ButtonColumn` — per-row buttons break horizontal scroll. Checkbox ticked → trade dialog opens. This is the only scrollable per-row interaction Streamlit supports.

## Trading Flow (Phase 2)
1. User ticks checkbox on an option in Daily Research
2. Confirmation dialog opens with order details + SANDBOX/LIVE badge
3. **Path A — Manual Track:** Click "Track Position" → tab created in Google Sheets + position in Supabase (no broker order)
4. **Path B — Broker Order:** Click "Validate Order" → dry-run sent to TastyTrade API
5. Buying power impact, fees, and warnings displayed
6. Click "Confirm & Place Order" → real order placed on TastyTrade
7. Position recorded in Supabase with order ID
8. OCC symbol format: `SYMBOL  YYMMDDP00STRIKE000` (e.g., `NVDA  260417P00220000`)

## Key Decisions
- Supabase over Google Sheets: no row limits, real-time triggers, proper relational data
- Supabase over Airtable: no 1,000 record free tier limit, Vince already runs 3 instances
- Streamlit for dashboard: Python-based (matches existing stack), free deployment, fast to build
- Theme: uses Streamlit native theming (not CSS override) — CSS can't reach data_editor iframe
- Google Sheets kept as backup: n8n pushes to both Supabase and Sheets on each scan

## n8n Workflows
- **Location:** `../tasty-trade/n8n/`
- `tastytrade_daily_scan.json` — Cron trigger (weekdays 10:00 AM ET) → daily scan + push
- `tastytrade_position_tracker.json` — Position tracking workflow
- Import into n8n via Workflows > Import from file

## Known Issues
- **tasty-trade/.env** was missing Supabase credentials — caused daily_scan.py config load to fail. Fixed 2026-03-18.
- **Mar 17 data** had null POP/P50/underlying_price — backfilled with default IV=0.6 (approximate). Future scans use live Greeks.
- **Sandbox Cash account** has $0 equity buying power for naked puts — may need Reg T margin upgrade

## Next Steps
- Set up daily position tracker to append rows to Google Sheets tabs (daily P&L snapshots)
- Connect position_tracker.py to write snapshots to Supabase
- Phase 3: Moomoo paper trading (after Stan's specs)
- Consider upgrading sandbox account to Reg T margin (currently Cash — $0 buying power for naked puts)
