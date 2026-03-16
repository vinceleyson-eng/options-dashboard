"""
Migrate existing Google Sheets scan data to Supabase.

Reads all date-named tabs (e.g., '2026-03-10') from the Tasty Trade Data sheet
and inserts them into the daily_scans + scan_options tables in Supabase.

Run once to backfill historical data.
"""

import os
import re
import sys
from datetime import date
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from supabase import create_client

load_dotenv()

# Google Sheets
SPREADSHEET_ID = "1yN1tn0EXseDW9sf6SWOehxZKy3LX09dOdsoyGdhaGlk"
SA_PATH = os.path.expanduser("~/.claude/credentials/google-service-account.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Column mapping — matches push_to_sheets.py COLUMNS order
# Row 1: Title, Row 2: VIX info, Row 3: Headers, Row 4+: Data
COLUMN_NAMES = [
    "symbol", "name", "iv_rank", "dte", "delta", "exp_date", "pop", "p50",
    "strike", "bid", "ask", "bid_ask_spread", "put_price", "earnings", "underlying_price",
]


def get_sheets_service():
    creds = service_account.Credentials.from_service_account_file(SA_PATH, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def parse_numeric(val):
    """Parse a numeric value, stripping % signs. Returns None for '-' or empty."""
    if val is None or val == "" or val == "-":
        return None
    val = str(val).strip().replace("%", "").replace("$", "").replace(",", "")
    try:
        return float(val)
    except ValueError:
        return None


def parse_date(val):
    """Parse a date string (YYYY-MM-DD). Returns None if invalid."""
    if val is None or val == "" or val == "-":
        return None
    try:
        # Validate it's a real date
        parts = str(val).strip().split("-")
        if len(parts) == 3:
            date(int(parts[0]), int(parts[1]), int(parts[2]))
            return str(val).strip()
    except (ValueError, IndexError):
        pass
    return None


def parse_vix_from_row(row):
    """Extract VIX value from row 2 text like 'VIX: 27.29'."""
    if not row:
        return None
    text = str(row[0]) if row else ""
    match = re.search(r"VIX:\s*([\d.]+)", text)
    if match:
        return float(match.group(1))
    return None


def is_date_tab(tab_name):
    """Check if tab name looks like a date (YYYY-MM-DD)."""
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", tab_name))


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
        sys.exit(1)

    # Connect to services
    sheets_service = get_sheets_service()
    sheets = sheets_service.spreadsheets()
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Get all tabs
    meta = sheets.get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties").execute()
    all_tabs = [s["properties"]["title"] for s in meta["sheets"]]
    date_tabs = sorted([t for t in all_tabs if is_date_tab(t)])

    print(f"Found {len(date_tabs)} date tabs to migrate: {', '.join(date_tabs)}")

    # Check which dates already exist in Supabase
    existing = supabase.table("daily_scans").select("scan_date").execute()
    existing_dates = {row["scan_date"] for row in existing.data}
    print(f"Already in Supabase: {len(existing_dates)} dates")

    migrated = 0
    skipped = 0

    for tab_name in date_tabs:
        if tab_name in existing_dates:
            print(f"  SKIP {tab_name} — already exists")
            skipped += 1
            continue

        print(f"\n  Migrating {tab_name}...")

        # Read all data from this tab
        result = sheets.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{tab_name}'!A1:O500",
        ).execute()
        rows = result.get("values", [])

        if len(rows) < 4:
            print(f"    WARNING: Tab has only {len(rows)} rows, skipping")
            continue

        # Row 0: Title, Row 1: VIX, Row 2: Headers, Row 3+: Data
        vix = parse_vix_from_row(rows[1] if len(rows) > 1 else [])
        data_rows = rows[3:]  # Skip title, VIX, headers

        if not data_rows:
            print(f"    WARNING: No data rows, skipping")
            continue

        # Insert daily_scan record
        scan_result = supabase.table("daily_scans").insert({
            "scan_date": tab_name,
            "vix": vix,
            "risk_free_rate": None,  # Not stored in sheets
        }).execute()

        scan_id = scan_result.data[0]["id"]
        print(f"    Created daily_scan: {scan_id} (VIX: {vix})")

        # Build option rows
        options_batch = []
        for row in data_rows:
            if len(row) < 6:  # Need at least symbol through exp_date
                continue

            # Pad row to 15 columns
            padded = row + [""] * (15 - len(row))

            option = {
                "scan_id": scan_id,
                "symbol": padded[0] if padded[0] else None,
                "name": padded[1] if padded[1] else None,
                "iv_rank": parse_numeric(padded[2]),
                "dte": int(parse_numeric(padded[3])) if parse_numeric(padded[3]) is not None else None,
                "delta": parse_numeric(padded[4]),
                "exp_date": parse_date(padded[5]),
                "pop": parse_numeric(padded[6]),
                "p50": parse_numeric(padded[7]),
                "strike": parse_numeric(padded[8]),
                "bid": parse_numeric(padded[9]),
                "ask": parse_numeric(padded[10]),
                "bid_ask_spread": parse_numeric(padded[11]),
                "put_price": parse_numeric(padded[12]),
                "earnings": parse_date(padded[13]),
                "underlying_price": parse_numeric(padded[14]),
                "selected": False,
            }

            # Skip rows without a symbol
            if not option["symbol"]:
                continue

            options_batch.append(option)

        if options_batch:
            # Insert in batches of 50
            for i in range(0, len(options_batch), 50):
                batch = options_batch[i:i+50]
                supabase.table("scan_options").insert(batch).execute()

            print(f"    Inserted {len(options_batch)} option rows")
        else:
            print(f"    WARNING: No valid option rows found")

        migrated += 1

    print(f"\n{'='*50}")
    print(f"Migration complete: {migrated} dates migrated, {skipped} skipped")
    print(f"Total dates in Supabase: {migrated + len(existing_dates)}")


if __name__ == "__main__":
    main()
