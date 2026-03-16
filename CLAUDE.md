# Stan — Options Trading Dashboard

## Project
Custom web dashboard for daily options trading research and position management. Replaces Google Sheets with Supabase + Streamlit.

## Status: Phase 1 LIVE — Dashboard deployed, sandbox connected (2026-03-16)

## Live URL
- **Dashboard:** https://options-dashboard-stan.streamlit.app/
- **GitHub:** https://github.com/vinceleyson-eng/options-dashboard (public)
- **Hosting:** Streamlit Community Cloud (free tier)
- **Secrets:** Configured in Streamlit Cloud Advanced Settings (not in repo)

## TastyTrade Sandbox
- **Sandbox account:** 5WW77042 (Individual, Cash, $100K)
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
- **Position tracking:** Checkbox in dashboard → generates position report (Phase 1)
- **Secrets:** `st.secrets` on Streamlit Cloud, `.env` for local dev (via `get_secret()` helper)

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
- 5 dates: 2026-03-09, 2026-03-10, 2026-03-12, 2026-03-13, 2026-03-14
- 653 total option rows

## Scan Data Columns (15 + checkbox)
Symbol, Name, IVR, DTE, Delta, Exp Date, POP, P50, Strike, Bid, Ask, Bid-Ask, Put Price, Earnings, Underlying Price, **Select** (checkbox)

## Phases
1. **Dashboard + Position Reports** (current) — Supabase tables, Streamlit UI, checkbox → position report
2. **TastyTrade Sandbox** — checkbox also places order in TastyTrade sandbox
3. **Moomoo Paper Trading** — checkbox also places order in Moomoo paper trade

## Dashboard Features
- **4 pages:** Daily Research, Open Positions, Position History, Config
- **Calendar date picker** in sidebar (snaps to nearest available scan date)
- **Light/Dark theme** via Streamlit native settings (hamburger menu > Settings > Theme)
- **Data table** using `st.data_editor` — spreadsheet-style grid with checkboxes, dollar formatting, scrollable
- **Checkbox → position creation** — ticking Select creates a position record (NO live trades)
- **Filter by symbol**, sort by IVR/POP/P50/Delta/DTE, show selected only
- **Position cards** with expandable daily P&L snapshots, close position button

## Key Decisions
- Supabase over Google Sheets: no row limits, real-time triggers, proper relational data
- Supabase over Airtable: no 1,000 record free tier limit, Vince already runs 3 instances
- Streamlit for dashboard: Python-based (matches existing stack), free deployment, fast to build
- Theme: uses Streamlit native theming (not CSS override) — CSS can't reach data_editor iframe
- Google Sheets kept as backup: n8n pushes to both Supabase and Sheets on each scan

## Next Steps
- Get Stan's feedback on dashboard
- Deploy for remote access (currently localhost:8501 only)
- Connect position_tracker.py to write snapshots to Supabase
- Phase 2: TastyTrade Sandbox integration (after Stan's specs)
- Phase 3: Moomoo paper trading (after Stan's specs)
