# Force IPv4 — httplib2 tries IPv6 first which times out on some networks
import socket
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_only(*args, **kwargs):
    return [r for r in _orig_getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET]
socket.getaddrinfo = _ipv4_only

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
import math
import os
import sys
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from scipy.optimize import brentq
from scipy.stats import norm
from supabase import create_client

load_dotenv()


# --- Black-Scholes IV back-solver (module-level, reused across all positions) ---
def _bs_put(S, K, T, r, sigma):
    """Black-Scholes European put price."""
    if T <= 0:
        return max(K - S, 0)
    if sigma <= 0:
        return max(K * math.exp(-r * T) - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def calc_iv_and_range(option_price, share_price, strike, dte, rfr=0.0375):
    """Back-solve IV from put price, then compute Range = S * IV * sqrt(T).

    Returns (iv_pct, range_dollars) or (None, None) if inputs invalid
    or option_price is below the discounted intrinsic floor.
    """
    try:
        if not (option_price and share_price and strike and dte):
            return None, None
        P = float(option_price)
        S = float(share_price)
        K = float(strike)
        T = float(dte) / 365.0
        if P <= 0 or S <= 0 or K <= 0 or T <= 0:
            return None, None
        intrinsic = max(K * math.exp(-rfr * T) - S, 0)
        if P < intrinsic:
            return None, None
        iv = brentq(lambda s: _bs_put(S, K, T, rfr, s) - P, 0.01, 5.0, xtol=1e-5)
        if not (0.01 <= iv <= 5.0):
            return None, None
        iv_pct = round(iv * 100, 1)
        range_val = round(S * iv * math.sqrt(T), 2)
        return iv_pct, range_val
    except (ValueError, RuntimeError, TypeError):
        return None, None

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


def build_tab_name(symbol, strike, exp_date, opened_date=None, existing_tabs=None):
    """Build tab name: OCC symbol + opened date (e.g., ADBE  260515P00215000 (20260330))."""
    occ = build_occ_symbol(symbol, exp_date, strike)
    if opened_date:
        date_str = str(opened_date).replace("-", "")[:8]
        return f"{occ} ({date_str})"
    return occ


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


def push_snapshots_to_sheets(results, market_date=None):
    """Append daily snapshot rows to Google Sheets Position Tracker.

    Per-contract tabs (POS-ADBE-225P). 9 columns: Date, OCC, Expiration,
    DTE, Share Price, Strike, Difference, Option Price, P&L.
    P&L = Option Price - Price Paid.
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

        NUM_COLS = 11
        mdate = market_date or get_market_date()
        today_str = mdate.isoformat()
        today_serial = (datetime(mdate.year, mdate.month, mdate.day) - datetime(1899, 12, 30)).days
        appended = 0

        tab_data_cache = {}

        for r in results:
            pos = r["position"]
            symbol = pos["symbol"]
            strike = float(pos["strike"])
            exp_date = pos["exp_date"]
            price_paid = float(pos.get("price_paid", 0) or 0)

            opened_date = str(pos.get("opened_at", ""))[:10]
            occ = build_occ_symbol(symbol, exp_date, strike)
            tab_name = build_tab_name(symbol, strike, exp_date, opened_date=opened_date, existing_tabs=existing_tabs)

            if tab_name not in existing_tabs:
                print(f"  Sheets: no tab {tab_name}, skipping")
                continue

            sheet_id = existing_tabs[tab_name]

            if tab_name not in tab_data_cache:
                existing = sheets_service.spreadsheets().values().get(
                    spreadsheetId=POSITION_TRACKER_SHEET_ID,
                    range=f"'{tab_name}'!A:K",
                    valueRenderOption="FORMATTED_VALUE",
                ).execute()
                tab_data_cache[tab_name] = existing.get("values", [])

            existing_rows = tab_data_cache[tab_name]

            # Deduplicate: check if today + OCC already exists
            already_in_sheet = False
            for row in existing_rows:
                if len(row) >= 2 and row[0] == today_str and row[1] == occ:
                    already_in_sheet = True
                    break

            if already_in_sheet:
                continue

            next_row = len(existing_rows)

            option_price = float(r["option_price"] or 0)
            difference = round(float(r["share_price"] or 0) - strike, 2) if r.get("share_price") else 0

            # P&L = Option Price - Price Paid
            pl = round(price_paid - option_price, 2)

            # Back-calculate IVx and Range from option price (module-level helper)
            iv_pct_row, range_val = calc_iv_and_range(
                option_price, r.get("share_price"), strike, r.get("dte")
            )

            # 11 columns: Date, OCC, Expiration, DTE, Share Price, Strike, Difference, Option Price, P&L, IVx, Range
            row_cells = [
                {"userEnteredValue": {"numberValue": today_serial}, "userEnteredFormat": date_fmt},
                {"userEnteredValue": {"stringValue": occ}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"stringValue": exp_date}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": r["dte"] or 0}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": float(r["share_price"] or 0)}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": int(strike)}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": difference}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": option_price}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": pl}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"stringValue": f"{iv_pct_row}%"} if iv_pct_row is not None else {"stringValue": "N/A"}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"stringValue": f"±${range_val:.2f}"} if range_val is not None else {"stringValue": "N/A"}, "userEnteredFormat": data_fmt},
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

            iv_str = f"{iv_pct_row}%" if iv_pct_row is not None else "N/A"
            range_str = f"±${range_val:.2f}" if range_val is not None else "N/A"
            existing_rows.append([today_str, occ, exp_date, str(r["dte"] or 0),
                                  str(r["share_price"] or 0), str(int(strike)),
                                  str(difference), str(option_price), str(pl),
                                  iv_str, range_str])
            appended += 1

        print(f"  Sheets: {appended} daily rows appended")

    except Exception as e:
        print(f"  Sheets ERROR: {e}")


def update_summary_sheet(sb, positions, results, underlying_prices, option_prices):
    """Rebuild Summary sheet with current prices for all open positions."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        sheets_service = build("sheets", "v4", credentials=creds)

        meta = sheets_service.spreadsheets().get(spreadsheetId=POSITION_TRACKER_SHEET_ID).execute()
        tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

        if "Summary" not in tabs:
            print("  Summary tab not found, skipping")
            return

        summary_id = tabs["Summary"]

        # Formatting
        DARK_BLUE = {"red": 0.149, "green": 0.247, "blue": 0.447}
        LIGHT_GRAY = {"red": 0.949, "green": 0.949, "blue": 0.949}
        WHITE_TEXT = {"red": 1, "green": 1, "blue": 1}
        BORDER_CLR = {"red": 0.698, "green": 0.698, "blue": 0.698}
        THIN = {"style": "SOLID", "width": 1, "color": BORDER_CLR}
        ALL_BORDERS = {"top": THIN, "bottom": THIN, "left": THIN, "right": THIN}
        d_fmt = {"backgroundColor": LIGHT_GRAY, "borders": ALL_BORDERS, "horizontalAlignment": "CENTER",
                 "verticalAlignment": "MIDDLE", "textFormat": {"fontSize": 10}}
        n_fmt = {**d_fmt, "numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}
        hdr_fmt = {"backgroundColor": DARK_BLUE, "borders": ALL_BORDERS, "horizontalAlignment": "CENTER",
                   "textFormat": {"foregroundColor": WHITE_TEXT, "fontSize": 10, "bold": True}}

        SUMMARY_COLS = 14

        # Clear existing data (rows 4+, keep title + headers)
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=POSITION_TRACKER_SHEET_ID,
            body={"requests": [{"updateCells": {
                "range": {"sheetId": summary_id, "startRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": SUMMARY_COLS},
                "fields": "userEnteredValue",
            }}]},
        ).execute()

        # Build result lookup by position id
        result_by_id = {}
        for r in results:
            result_by_id[r["position"]["id"]] = r

        # Load ALL open positions (not just ones with results)
        all_positions = sb.table("positions").select("*").eq("status", "open").order("opened_at").execute().data

        # Build VIX + IVx lookup: scan_option_id → vix / iv / underlying / dte / ivr
        vix_lookup = {}
        iv_lookup = {}       # scan_option_id → iv decimal
        ul_lookup = {}       # scan_option_id → underlying_price at scan
        dte_lookup = {}      # scan_option_id → dte at scan
        ivr_lookup = {}      # scan_option_id → iv_rank
        scan_option_ids = [p["scan_option_id"] for p in all_positions if p.get("scan_option_id")]
        if scan_option_ids:
            scan_opts = sb.table("scan_options").select("*").in_("id", scan_option_ids).execute().data
            scan_ids = list(set(so["scan_id"] for so in scan_opts))
            scans_data = sb.table("daily_scans").select("id, vix").in_("id", scan_ids).execute().data
            scan_vix_map = {s["id"]: s.get("vix") for s in scans_data}
            for so in scan_opts:
                vix_lookup[so["id"]] = scan_vix_map.get(so["scan_id"])
                iv_lookup[so["id"]] = so.get("iv")
                ul_lookup[so["id"]] = so.get("underlying_price")
                dte_lookup[so["id"]] = so.get("dte")
                ivr_lookup[so["id"]] = so.get("iv_rank")

        reqs = []
        for i, pos in enumerate(sorted(all_positions, key=lambda p: str(p.get("opened_at", ""))[:10])):
            row_idx = 3 + i
            symbol = pos["symbol"]
            strike = float(pos["strike"])
            exp_date = pos["exp_date"]
            company = pos.get("name", symbol)
            price_paid = float(pos.get("price_paid", 0) or 0)
            opened_date = str(pos.get("opened_at", ""))[:10]

            occ = build_occ_symbol(symbol, exp_date, strike)
            tab_label = build_tab_name(symbol, strike, exp_date, opened_date=opened_date)

            # Get current option price from results or snapshots
            r = result_by_id.get(pos["id"])
            if r and r.get("option_price"):
                current_price = float(r["option_price"])
            else:
                current_price = price_paid  # No data yet

            pl = round(price_paid - current_price, 2)

            # Get IVR, VIX, IVx, ExpMove at time of scan
            scan_opt_id = pos.get("scan_option_id")
            ivr = ivr_lookup.get(scan_opt_id)
            scan_vix = vix_lookup.get(scan_opt_id)
            iv_raw = iv_lookup.get(scan_opt_id)
            iv_pct = round(float(iv_raw) * 100, 1) if iv_raw else None
            ul_at_scan = ul_lookup.get(scan_opt_id)
            dte_at_scan = dte_lookup.get(scan_opt_id)
            exp_move = None
            if iv_raw and ul_at_scan and dte_at_scan:
                import math as _math
                exp_move = round(float(ul_at_scan) * float(iv_raw) * _math.sqrt(float(dte_at_scan) / 365), 2)

            # Build hyperlink to the trade tab if it exists
            tab_gid = tabs.get(tab_label)
            if tab_gid is not None:
                occ_cell = {"userEnteredValue": {"formulaValue": f'=HYPERLINK("#gid={tab_gid}", "{tab_label}")'}, "userEnteredFormat": d_fmt}
            else:
                occ_cell = {"userEnteredValue": {"stringValue": tab_label}, "userEnteredFormat": d_fmt}

            cells = [
                occ_cell,
                {"userEnteredValue": {"stringValue": symbol}, "userEnteredFormat": d_fmt},
                {"userEnteredValue": {"stringValue": company}, "userEnteredFormat": d_fmt},
                {"userEnteredValue": {"numberValue": int(strike)}, "userEnteredFormat": d_fmt},
                {"userEnteredValue": {"stringValue": exp_date}, "userEnteredFormat": d_fmt},
                {"userEnteredValue": {"stringValue": opened_date}, "userEnteredFormat": d_fmt},
                {"userEnteredValue": {"numberValue": price_paid}, "userEnteredFormat": n_fmt},
                {"userEnteredValue": {"numberValue": current_price}, "userEnteredFormat": n_fmt},
                {"userEnteredValue": {"numberValue": pl}, "userEnteredFormat": n_fmt},
                {"userEnteredValue": {"stringValue": "OPEN"}, "userEnteredFormat": d_fmt},
                {"userEnteredValue": {"stringValue": f"{round(float(ivr), 1)}%"} if ivr is not None else {"stringValue": "N/A"}, "userEnteredFormat": d_fmt},
                {"userEnteredValue": {"numberValue": float(scan_vix)} if scan_vix is not None else {"stringValue": "N/A"}, "userEnteredFormat": n_fmt if scan_vix is not None else d_fmt},
                {"userEnteredValue": {"stringValue": f"{iv_pct}%"} if iv_pct is not None else {"stringValue": "N/A"}, "userEnteredFormat": d_fmt},
                {"userEnteredValue": {"stringValue": f"±${exp_move:.2f}"} if exp_move is not None else {"stringValue": "N/A"}, "userEnteredFormat": d_fmt},
            ]
            reqs.append({"updateCells": {
                "range": {"sheetId": summary_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                          "startColumnIndex": 0, "endColumnIndex": SUMMARY_COLS},
                "rows": [{"values": cells}],
                "fields": "userEnteredValue,userEnteredFormat",
            }})

        if reqs:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=POSITION_TRACKER_SHEET_ID,
                body={"requests": reqs},
            ).execute()

        print(f"  Summary sheet updated with {len(all_positions)} positions")

    except Exception as e:
        print(f"  Summary sheet ERROR: {e}")


def get_market_date():
    """Return the market date for this run.

    Cron is scheduled for 10 PM GMT+8 (= 10 AM ET). If run after midnight
    GMT+8 (e.g., manual re-run or delayed execution), use yesterday's date
    so the snapshot is tagged to the correct trading day.
    Cutoff: before 5 AM GMT+8 (= before 5 PM ET, after market close).
    """
    now = datetime.now()
    if now.hour < 5:
        return (now - timedelta(days=1)).date()
    return now.date()


async def main():
    today = get_market_date()
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
        push_snapshots_to_sheets(results, market_date=today)
        print()

    # Process shadow positions
    if shadows:
        print(f"Processing {len(shadows)} shadow positions...")
        process_shadow_positions(sb, shadows, underlying_prices, option_prices, today)

    # Update Summary sheet with latest prices
    if positions:
        print("\nUpdating Summary sheet...")
        update_summary_sheet(sb, positions, results, underlying_prices, option_prices)

    print()
    print("=" * 50)
    print("Position tracking complete!")


if __name__ == "__main__":
    asyncio.run(main())
