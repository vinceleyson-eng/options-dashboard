"""
Push scan_results.json to Supabase — replaces push_to_sheets.py.

Reads scan_results.json (produced by daily_scan.py) and inserts into
daily_scans + scan_options tables.

Also provides read_config_from_supabase() for daily_scan.py to use.
"""

import json
import os
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

SCAN_RESULTS_PATH = os.path.join(os.path.dirname(__file__), "..", "tasty-trade", "scan_results.json")

# Default config (fallback if Supabase config table is empty)
DEFAULT_SYMBOLS = [
    "MU", "SNOW", "ORCL", "BIDU", "CRM", "AVGO", "ADBE",
    "BABA", "MRVL", "LULU", "VST", "NVDA", "META", "MSFT", "TSLA",
]
DEFAULT_CONFIG = {
    "delta_min": -0.30,
    "delta_max": -0.15,
    "dte_min": 30,
    "dte_max": 60,
}


def get_supabase():
    """Create and return Supabase client."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def read_config_from_supabase():
    """
    Read scanner config from Supabase config table.
    Returns dict with keys: symbols, delta_min, delta_max, dte_min, dte_max.
    Falls back to defaults if config is missing.
    """
    try:
        sb = get_supabase()
        result = sb.table("config").select("key, value").execute()

        config = {**DEFAULT_CONFIG}
        symbols = DEFAULT_SYMBOLS

        for row in result.data:
            key = row["key"]
            val = row["value"]
            if key == "symbols":
                symbols = json.loads(val)
            elif key == "delta_min":
                config["delta_min"] = float(val)
            elif key == "delta_max":
                config["delta_max"] = float(val)
            elif key == "dte_min":
                config["dte_min"] = int(float(val))
            elif key == "dte_max":
                config["dte_max"] = int(float(val))

        print(f"Config loaded: {len(symbols)} symbols, delta {config['delta_min']} to {config['delta_max']}, DTE {config['dte_min']}-{config['dte_max']}")
        return {"symbols": symbols, **config}

    except Exception as e:
        print(f"WARNING: Could not read config from Supabase ({e}) — using defaults")
        return {"symbols": DEFAULT_SYMBOLS, **DEFAULT_CONFIG}


def parse_date(val):
    """Return date string if valid, else None."""
    if val is None or val == "" or val == "-":
        return None
    return str(val).strip()


def main():
    sb = get_supabase()

    # Load scan results
    results_path = SCAN_RESULTS_PATH
    if not os.path.exists(results_path):
        # Also check local directory
        results_path = os.path.join(os.path.dirname(__file__), "scan_results.json")
    if not os.path.exists(results_path):
        print("ERROR: scan_results.json not found")
        sys.exit(1)

    with open(results_path) as f:
        data = json.load(f)

    scan_date = data["date"]
    vix = data.get("vix")
    risk_free_rate = data.get("risk_free_rate")
    options = data.get("options", [])

    if not options:
        print("No options data to push.")
        return

    # Check if this date already exists
    existing = sb.table("daily_scans").select("id").eq("scan_date", scan_date).execute()
    if existing.data:
        print(f"Date '{scan_date}' already exists in Supabase — skipping to avoid duplicates.")
        print("Delete the record manually if you want to re-push.")
        return

    # Insert daily_scan record
    scan_result = sb.table("daily_scans").insert({
        "scan_date": scan_date,
        "vix": vix,
        "risk_free_rate": risk_free_rate,
    }).execute()

    scan_id = scan_result.data[0]["id"]
    print(f"Created daily_scan: {scan_date} (VIX: {vix}, RFR: {risk_free_rate})")

    # Build option rows
    option_rows = []
    for o in options:
        option_rows.append({
            "scan_id": scan_id,
            "symbol": o["symbol"],
            "name": o.get("name"),
            "iv_rank": o.get("iv_rank"),
            "iv": o.get("iv"),
            "dte": o.get("dte"),
            "delta": o.get("delta"),
            "exp_date": parse_date(o.get("exp_date")),
            "pop": o.get("pop"),
            "p50": o.get("p50"),
            "strike": o.get("strike"),
            "bid": o.get("bid"),
            "ask": o.get("ask"),
            "bid_ask_spread": o.get("bid_ask_spread"),
            "put_price": o.get("put_price"),
            "earnings": parse_date(o.get("earnings")),
            "underlying_price": o.get("underlying_price"),
            "expected_move": o.get("expected_move"),
            "selected": False,
        })

    # Insert in batches of 50
    inserted_options = []
    for i in range(0, len(option_rows), 50):
        batch = option_rows[i:i+50]
        result = sb.table("scan_options").insert(batch).execute()
        inserted_options.extend(result.data)

    print(f"Inserted {len(option_rows)} option rows for {scan_date}")

    # --- Shadow positions: auto-create for every scan option (analytics) ---
    shadow_rows = []
    for opt in inserted_options:
        shadow_rows.append({
            "scan_option_id": opt["id"],
            "scan_date": scan_date,
            "symbol": opt["symbol"],
            "name": opt.get("name"),
            "strike": opt["strike"],
            "exp_date": opt.get("exp_date"),
            "put_price": opt.get("put_price"),
            "underlying_price": opt.get("underlying_price"),
            "delta": opt.get("delta"),
            "iv_rank": opt.get("iv_rank"),
            "pop": opt.get("pop"),
            "p50": opt.get("p50"),
            "dte": opt.get("dte"),
        })

    for i in range(0, len(shadow_rows), 50):
        batch = shadow_rows[i:i+50]
        sb.table("shadow_positions").insert(batch).execute()

    print(f"Created {len(shadow_rows)} shadow positions for analytics")
    print("Done!")


if __name__ == "__main__":
    main()
