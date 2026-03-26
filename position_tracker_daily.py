"""
Daily Position Tracker — runs after daily scan.

For each open position (user-selected) and shadow position (all scan options):
1. Fetches current share price + option price from TastyTrade
2. Calculates DTE, Difference, P&L
3. Writes snapshot to Supabase (position_snapshots / shadow_snapshots)
4. Appends daily row to Google Sheets Position Tracker (user positions only)

Run: python position_tracker_daily.py
Schedule: Added to daily_scan_cron.bat (10 PM GMT+8 = 10 AM ET)
"""

import asyncio
import os
import sys
from datetime import date, datetime

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
CLIENT_SECRET = os.getenv("TASTYTRADE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("TASTYTRADE_REFRESH_TOKEN")

POSITION_TRACKER_SHEET_ID = "1F2jvkbnAFDMZQ_BbMXyVLVFgAutKrZ2QMSUKzy0RUXE"
SA_PATH = "C:/Users/acer/.claude/credentials/google-service-account.json"


def get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        sys.exit(1)
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def build_occ_symbol(symbol, exp_date, strike):
    """Build OSI/OCC option symbol: SYMBOL  YYMMDDP00STRIKE000."""
    exp_dt = datetime.strptime(exp_date, "%Y-%m-%d")
    exp_code = exp_dt.strftime("%y%m%d")
    strike_code = f"{int(float(strike) * 1000):08d}"
    return f"{symbol:<6}{exp_code}P{strike_code}"


def build_tab_name(symbol, strike, exp_date, existing_tabs):
    """Build per-contract tab name (e.g., POS-ADBE-225P).

    If same symbol+strike already exists with a different exp, add suffix.
    """
    strike_int = int(float(strike))
    base = f"POS-{symbol}-{strike_int}P"

    if base in existing_tabs:
        return base

    # Check for conflict (same base, different exp suffix)
    for tab in existing_tabs:
        if tab.startswith(base) and tab != base:
            exp_dt = datetime.strptime(exp_date, "%Y-%m-%d")
            return f"{base}-{exp_dt.strftime('%m%d')}"

    return base


def build_streamer_symbol(symbol, exp_date, strike):
    """Build DXLink streamer symbol for option quotes."""
    exp_dt = datetime.strptime(exp_date, "%Y-%m-%d")
    exp_str = exp_dt.strftime("%y%m%d")
    strike_val = float(strike)
    strike_str = str(int(strike_val)) if strike_val == int(strike_val) else str(strike_val)
    return f".{symbol}{exp_str}P{strike_str}"


def load_open_positions(sb):
    """Load all open user-selected positions from Supabase."""
    result = sb.table("positions").select("*").eq("status", "open").execute()
    return result.data


def load_active_shadow_positions(sb, today):
    """Load shadow positions that haven't expired yet."""
    result = sb.table("shadow_positions").select("*").gte("exp_date", today.isoformat()).execute()
    return result.data


async def fetch_market_data(session, symbols, option_symbols_map):
    """Fetch underlying prices and option prices from TastyTrade.

    symbols: list of underlying tickers
    option_symbols_map: dict of streamer_sym -> position info

    Returns (underlying_prices, option_prices)
    """
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Trade, Summary, Greeks

    # Fetch underlying prices
    underlying_prices = {}
    if symbols:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(Trade, symbols)
            for _ in range(len(symbols)):
                try:
                    t = await asyncio.wait_for(streamer.get_event(Trade), timeout=10)
                    if t.price and float(t.price) > 0:
                        underlying_prices[t.event_symbol] = float(t.price)
                except asyncio.TimeoutError:
                    break

        # Fallback for missing
        missing = [s for s in symbols if s not in underlying_prices]
        if missing:
            print(f"  Falling back to prev_day_close for: {', '.join(missing)}")
            async with DXLinkStreamer(session) as streamer:
                await streamer.subscribe(Summary, missing)
                for _ in range(len(missing)):
                    try:
                        s = await asyncio.wait_for(streamer.get_event(Summary), timeout=10)
                        price = s.prev_day_close_price or s.day_close_price
                        if price and float(price) > 0:
                            underlying_prices[s.event_symbol] = float(price)
                    except asyncio.TimeoutError:
                        break

    # Fetch option prices (Greeks for theo price, Summary for prev close)
    option_prices = {}
    opt_syms = list(option_symbols_map.keys())
    if opt_syms:
        # Process in batches of 50 to avoid streamer overload
        for batch_start in range(0, len(opt_syms), 50):
            batch = opt_syms[batch_start:batch_start + 50]
            print(f"  Streaming option batch {batch_start + 1}-{batch_start + len(batch)} of {len(opt_syms)}...")

            async with DXLinkStreamer(session) as streamer:
                await streamer.subscribe(Greeks, batch)
                await streamer.subscribe(Summary, batch)

                for _ in range(len(batch)):
                    try:
                        g = await asyncio.wait_for(streamer.get_event(Greeks), timeout=8)
                        if g.price:
                            option_prices[g.event_symbol] = round(float(g.price), 2)
                    except asyncio.TimeoutError:
                        break

                # Fill gaps with Summary prev_day_close
                missing_opts = [s for s in batch if s not in option_prices]
                for _ in range(len(batch)):
                    try:
                        s = await asyncio.wait_for(streamer.get_event(Summary), timeout=5)
                        if s.event_symbol not in option_prices and s.prev_day_close_price:
                            option_prices[s.event_symbol] = round(float(s.prev_day_close_price), 2)
                    except asyncio.TimeoutError:
                        break

    return underlying_prices, option_prices


def process_positions(sb, positions, underlying_prices, option_prices, today):
    """Write snapshots for user-selected positions. Returns results for Sheets push."""
    results = []

    for pos in positions:
        symbol = pos["symbol"]
        strike = float(pos["strike"])
        exp_date = pos["exp_date"]
        price_paid = float(pos.get("price_paid", 0) or 0)
        quantity = int(pos.get("quantity", 1) or 1)

        streamer_sym = build_streamer_symbol(symbol, exp_date, strike)
        dte = (datetime.strptime(exp_date, "%Y-%m-%d").date() - today).days

        share_price = underlying_prices.get(symbol)
        option_price = option_prices.get(streamer_sym)

        difference = round(share_price - strike, 2) if share_price else None
        pl = round((price_paid - option_price) * quantity * 100, 2) if price_paid and option_price else None

        # Check for existing snapshot today
        existing = sb.table("position_snapshots").select("id").eq(
            "position_id", pos["id"]).eq("snapshot_date", today.isoformat()).execute()
        if existing.data:
            print(f"  {symbol} {strike:.0f} — already has snapshot for {today}")
            # Still include in results for Sheets push
            results.append({
                "position": pos,
                "dte": dte,
                "share_price": share_price,
                "option_price": option_price,
                "difference": difference,
                "pl": pl,
                "already_exists": True,
            })
            continue

        # Insert snapshot
        snapshot = {
            "position_id": pos["id"],
            "snapshot_date": today.isoformat(),
            "dte": dte,
            "share_price": share_price,
            "option_price": option_price,
            "difference": difference,
            "pl": pl,
        }
        sb.table("position_snapshots").insert(snapshot).execute()

        # Auto-close expired positions
        if dte <= 0:
            sb.table("positions").update({
                "status": "closed",
                "closed_at": datetime.utcnow().isoformat(),
            }).eq("id", pos["id"]).execute()
            print(f"  {symbol} {strike:.0f} — EXPIRED (DTE={dte}), auto-closed")

        print(f"  {symbol} {strike:.0f} — DTE={dte} | Share=${share_price} | Opt=${option_price} | P&L=${pl}")
        results.append({
            "position": pos,
            "dte": dte,
            "share_price": share_price,
            "option_price": option_price,
            "difference": difference,
            "pl": pl,
            "already_exists": False,
        })

    return results


def process_shadow_positions(sb, shadows, underlying_prices, option_prices, today):
    """Write snapshots for shadow positions (analytics only, no Sheets)."""
    inserted = 0
    skipped = 0

    # Batch: check which shadow_position_ids already have snapshots today
    shadow_ids = [s["id"] for s in shadows]

    # Get all existing snapshots for today in one query
    existing_snaps = set()
    for batch_start in range(0, len(shadow_ids), 100):
        batch = shadow_ids[batch_start:batch_start + 100]
        result = sb.table("shadow_snapshots").select("shadow_position_id").in_(
            "shadow_position_id", batch).eq("snapshot_date", today.isoformat()).execute()
        for row in result.data:
            existing_snaps.add(row["shadow_position_id"])

    rows_to_insert = []
    for shadow in shadows:
        if shadow["id"] in existing_snaps:
            skipped += 1
            continue

        symbol = shadow["symbol"]
        strike = float(shadow["strike"])
        exp_date = shadow["exp_date"]
        put_price = float(shadow.get("put_price", 0) or 0)

        streamer_sym = build_streamer_symbol(symbol, exp_date, strike)
        dte = (datetime.strptime(exp_date, "%Y-%m-%d").date() - today).days

        share_price = underlying_prices.get(symbol)
        option_price = option_prices.get(streamer_sym)
        difference = round(share_price - strike, 2) if share_price else None
        pl = round((put_price - option_price) * 100, 2) if put_price and option_price else None

        rows_to_insert.append({
            "shadow_position_id": shadow["id"],
            "snapshot_date": today.isoformat(),
            "dte": dte,
            "share_price": share_price,
            "option_price": option_price,
            "strike": strike,
            "difference": difference,
            "pl": pl,
        })

    # Batch insert
    for i in range(0, len(rows_to_insert), 50):
        batch = rows_to_insert[i:i + 50]
        sb.table("shadow_snapshots").insert(batch).execute()
        inserted += len(batch)

    print(f"  Shadow snapshots: {inserted} inserted, {skipped} skipped (already existed)")
    return inserted


def push_snapshots_to_sheets(results):
    """Append daily snapshot rows to Google Sheets Position Tracker.

    Per-symbol tabs (POS-ADBE). 9 columns: Date, OCC, Strike, Exp, DTE,
    Share Price, Difference, Option Price, P&L.
    Per-contract tabs (POS-ADBE-225P). 7 columns: Date, DTE, Share Price,
    Strike, Difference, Option Price, P&L.
    P&L = option price today - option price yesterday.
    """
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        sheets_service = build("sheets", "v4", credentials=creds)

        meta = sheets_service.spreadsheets().get(spreadsheetId=POSITION_TRACKER_SHEET_ID).execute()
        existing_tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

        # Formatting constants
        LIGHT_GRAY = {"red": 0.949, "green": 0.949, "blue": 0.949}
        BORDER_CLR = {"red": 0.698, "green": 0.698, "blue": 0.698}
        THIN = {"style": "SOLID", "width": 1, "color": BORDER_CLR}
        ALL_BORDERS = {"top": THIN, "bottom": THIN, "left": THIN, "right": THIN}
        data_fmt = {"backgroundColor": LIGHT_GRAY, "borders": ALL_BORDERS, "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE", "textFormat": {"fontSize": 10}}
        num_fmt = {**data_fmt, "numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}
        date_fmt = {**data_fmt, "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}

        NUM_COLS = 7
        today_str = date.today().isoformat()
        today_serial = (datetime.now() - datetime(1899, 12, 30)).days
        appended = 0

        # Cache: read tab data once per tab
        tab_data_cache = {}

        for r in results:
            pos = r["position"]
            symbol = pos["symbol"]
            strike = float(pos["strike"])
            exp_date = pos["exp_date"]

            tab_name = build_tab_name(symbol, strike, exp_date, existing_tabs)

            if tab_name not in existing_tabs:
                print(f"  Sheets: no tab {tab_name}, skipping")
                continue

            sheet_id = existing_tabs[tab_name]

            # Read existing rows (cache per tab)
            if tab_name not in tab_data_cache:
                existing = sheets_service.spreadsheets().values().get(
                    spreadsheetId=POSITION_TRACKER_SHEET_ID,
                    range=f"'{tab_name}'!A:G",
                    valueRenderOption="FORMATTED_VALUE",
                ).execute()
                tab_data_cache[tab_name] = existing.get("values", [])

            existing_rows = tab_data_cache[tab_name]

            # Deduplicate: check if today's date already exists
            already_in_sheet = False
            for row in existing_rows:
                if len(row) >= 1 and row[0] == today_str:
                    already_in_sheet = True
                    break

            if already_in_sheet:
                continue

            next_row = len(existing_rows)

            option_price = float(r["option_price"] or 0)
            difference = round(float(r["share_price"] or 0) - strike, 2) if r.get("share_price") else 0

            # Find previous option price (last data row, col F = Option Price, index 5)
            prev_option_price = None
            for row in reversed(existing_rows):
                if len(row) >= 6 and row[0] not in ("Date", "Symbol:", "Expiration:", "Position:", ""):
                    try:
                        prev_option_price = float(str(row[5]).replace(",", ""))
                    except (ValueError, TypeError):
                        pass
                    break

            # P&L = option price today - option price yesterday
            pl = round(option_price - prev_option_price, 2) if prev_option_price is not None else 0.00

            # 7 columns: Date, DTE, Share Price, Strike, Difference, Option Price, P&L
            row_cells = [
                {"userEnteredValue": {"numberValue": today_serial}, "userEnteredFormat": date_fmt},
                {"userEnteredValue": {"numberValue": r["dte"] or 0}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": float(r["share_price"] or 0)}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": int(strike)}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": difference}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": option_price}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": pl}, "userEnteredFormat": num_fmt},
            ]

            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=POSITION_TRACKER_SHEET_ID,
                body={"requests": [{"updateCells": {
                    "range": {"sheetId": sheet_id, "startRowIndex": next_row, "endRowIndex": next_row + 1,
                              "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
                    "rows": [{"values": row_cells}],
                    "fields": "userEnteredValue,userEnteredFormat",
                }}]},
            ).execute()

            # Update cache
            existing_rows.append([today_str, str(r["dte"] or 0), str(r["share_price"] or 0),
                                  str(int(strike)), str(difference), str(option_price), str(pl)])
            appended += 1

        print(f"  Sheets: {appended} daily rows appended")

    except Exception as e:
        print(f"  Sheets ERROR: {e}")


async def main():
    today = date.today()
    print(f"Position Tracker — {today}")
    print("=" * 50)

    sb = get_supabase()

    # Load positions
    positions = load_open_positions(sb)
    shadows = load_active_shadow_positions(sb, today)
    print(f"Open positions: {len(positions)}")
    print(f"Active shadow positions: {len(shadows)}")

    if not positions and not shadows:
        print("Nothing to track.")
        return

    # Create TastyTrade session (production — need real market data)
    from tastytrade import Session
    if not CLIENT_SECRET or not REFRESH_TOKEN:
        print("ERROR: Missing TASTYTRADE_CLIENT_SECRET or TASTYTRADE_REFRESH_TOKEN")
        sys.exit(1)
    session = Session(CLIENT_SECRET, REFRESH_TOKEN, is_test=False)

    # Collect all unique symbols and option streamer symbols
    all_underlyings = set()
    option_symbols_map = {}

    for pos in positions:
        all_underlyings.add(pos["symbol"])
        sym = build_streamer_symbol(pos["symbol"], pos["exp_date"], pos["strike"])
        option_symbols_map[sym] = pos

    for shadow in shadows:
        all_underlyings.add(shadow["symbol"])
        sym = build_streamer_symbol(shadow["symbol"], shadow["exp_date"], shadow["strike"])
        if sym not in option_symbols_map:
            option_symbols_map[sym] = shadow

    print(f"Unique underlyings: {len(all_underlyings)}")
    print(f"Unique option symbols: {len(option_symbols_map)}")
    print()

    # Fetch market data
    print("Fetching market data...")
    underlying_prices, option_prices = await fetch_market_data(
        session, list(all_underlyings), option_symbols_map)
    print(f"  Got {len(underlying_prices)} underlying prices, {len(option_prices)} option prices")
    print()

    # Process user positions
    if positions:
        print(f"Processing {len(positions)} user positions...")
        results = process_positions(sb, positions, underlying_prices, option_prices, today)
        print()

        # Push to Google Sheets
        print("Pushing to Google Sheets...")
        push_snapshots_to_sheets(results)
        print()

    # Process shadow positions
    if shadows:
        print(f"Processing {len(shadows)} shadow positions...")
        process_shadow_positions(sb, shadows, underlying_prices, option_prices, today)

    print()
    print("=" * 50)
    print("Position tracking complete!")


if __name__ == "__main__":
    asyncio.run(main())
