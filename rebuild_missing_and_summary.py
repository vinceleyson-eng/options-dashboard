# Force IPv4 — httplib2 tries IPv6 first which times out on some networks
import socket
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_only(*args, **kwargs):
    return [r for r in _orig_getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET]
socket.getaddrinfo = _ipv4_only

"""Recreate missing tabs + add Summary sheet."""
import time
from datetime import datetime as _dt
from dotenv import load_dotenv
load_dotenv()

import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from supabase import create_client

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
SA_PATH = "C:/Users/acer/.claude/credentials/google-service-account.json"
SHEET_ID = "1F2jvkbnAFDMZQ_BbMXyVLVFgAutKrZ2QMSUKzy0RUXE"

creds = service_account.Credentials.from_service_account_file(
    SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
service = build("sheets", "v4", credentials=creds)

NUM_COLS = 11

import math as _math
from scipy.stats import norm as _norm
from scipy.optimize import brentq as _brentq


def _calc_iv_range(opt_price, share_price, strike, dte, r=0.0375):
    """Back-calculate IV and Range from option price. Returns (iv_pct, range) or (None, None)."""
    if not (opt_price and share_price and strike and dte and dte > 0):
        return None, None
    T = float(dte) / 365.0
    S, K, P = float(share_price), float(strike), float(opt_price)
    intrinsic = max(K * _math.exp(-r * T) - S, 0)
    if P < intrinsic:
        return None, None
    def _bs(sigma):
        if sigma <= 0:
            return intrinsic
        d1 = (_math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * _math.sqrt(T))
        d2 = d1 - sigma * _math.sqrt(T)
        return K * _math.exp(-r * T) * _norm.cdf(-d2) - S * _norm.cdf(-d1)
    try:
        iv = _brentq(lambda s: _bs(s) - P, 0.01, 5.0, xtol=1e-5)
        if 0.01 <= iv <= 5.0:
            return round(iv * 100, 1), round(S * iv * _math.sqrt(T), 2)
    except (ValueError, RuntimeError):
        pass
    return None, None

def to_serial(ds):
    return (_dt.strptime(ds, "%Y-%m-%d") - _dt(1899, 12, 30)).days

def build_occ(symbol, exp_date, strike):
    exp_dt = _dt.strptime(exp_date, "%Y-%m-%d")
    return f"{symbol:<6}{exp_dt.strftime('%y%m%d')}P{int(float(strike) * 1000):08d}"

def build_tab_label(occ, opened_date):
    """Build tab name: OCC + opened date (e.g., ADBE  260515P00215000 (20260320))."""
    if opened_date:
        date_str = str(opened_date).replace("-", "")[:8]
        return f"{occ} ({date_str})"
    return occ

DARK_BLUE = {"red": 0.149, "green": 0.247, "blue": 0.447}
LIGHT_GRAY = {"red": 0.949, "green": 0.949, "blue": 0.949}
WHITE_TEXT = {"red": 1, "green": 1, "blue": 1}
BORDER_CLR = {"red": 0.698, "green": 0.698, "blue": 0.698}
THIN = {"style": "SOLID", "width": 1, "color": BORDER_CLR}
ALL_BORDERS = {"top": THIN, "bottom": THIN, "left": THIN, "right": THIN}
d_fmt = {"backgroundColor": LIGHT_GRAY, "borders": ALL_BORDERS, "horizontalAlignment": "CENTER",
         "verticalAlignment": "MIDDLE", "textFormat": {"fontSize": 10}}
n_fmt = {**d_fmt, "numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}
dt_fmt = {**d_fmt, "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}
hdr_fmt = {"backgroundColor": DARK_BLUE, "borders": ALL_BORDERS, "horizontalAlignment": "CENTER",
           "textFormat": {"foregroundColor": WHITE_TEXT, "fontSize": 10, "bold": True}}
bold_f = {"textFormat": {"bold": True}}
num_f = {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}

# Load data
scans = sb.table("daily_scans").select("id, scan_date, vix").order("scan_date").execute().data
scan_map = {s["id"]: s["scan_date"] for s in scans}
scan_vix_map = {s["id"]: s.get("vix") for s in scans}

# Paginate — Supabase default limit is 1000 rows
all_options = []
_offset = 0
while True:
    _batch = sb.table("scan_options").select("*").range(_offset, _offset + 999).execute().data
    if not _batch:
        break
    all_options.extend(_batch)
    if len(_batch) < 1000:
        break
    _offset += 1000
option_lookup = {}
for o in all_options:
    sd = scan_map.get(o["scan_id"])
    if sd:
        option_lookup[(o["symbol"], float(o["strike"]), o["exp_date"], sd)] = o

# Build VIX + IV + expected_move lookups: scan_option_id → vix / iv / ul / dte / ivr / em
vix_by_scan_option = {}
iv_by_scan_option = {}
ul_by_scan_option = {}
dte_by_scan_option = {}
ivr_by_scan_option = {}
em_by_scan_option = {}
em_by_key = {}
for o in all_options:
    vix_by_scan_option[o["id"]] = scan_vix_map.get(o["scan_id"])
    iv_by_scan_option[o["id"]] = o.get("iv")
    ul_by_scan_option[o["id"]] = o.get("underlying_price")
    dte_by_scan_option[o["id"]] = o.get("dte")
    ivr_by_scan_option[o["id"]] = o.get("iv_rank")
    em_by_scan_option[o["id"]] = o.get("expected_move")
    sd = scan_map.get(o["scan_id"])
    if sd and o.get("expected_move"):
        em_by_key[(o["symbol"], o["exp_date"], sd)] = float(o["expected_move"])

positions = sb.table("positions").select("*").eq("status", "open").order("opened_at").execute().data
print(f"Positions: {len(positions)}")

all_snapshots = {}
for pos in positions:
    snaps = sb.table("position_snapshots").select("*").eq(
        "position_id", pos["id"]).order("snapshot_date").execute().data
    all_snapshots[pos["id"]] = snaps

# Current sheet tabs
meta = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
existing_tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

# --- Part 1: Recreate missing tabs ---
print("\n--- Recreating missing tabs ---")
for pos in positions:
    symbol = pos["symbol"]
    strike = float(pos["strike"])
    strike_int = int(strike)
    exp_date = pos["exp_date"]
    occ = build_occ(symbol, exp_date, strike)
    opened_date = str(pos.get("opened_at", ""))[:10]
    tab_name = build_tab_label(occ, opened_date)

    if tab_name in existing_tabs:
        continue  # Already on sheet

    company = pos.get("name", symbol)
    price_paid = float(pos.get("price_paid", 0) or 0)
    quantity = int(pos.get("quantity", 1) or 1)
    direction = pos.get("direction", "Short")

    # Collect data: entry row + scan data after opened + snapshots
    daily_data = []

    # Entry row
    opened_dte = (_dt.strptime(exp_date, "%Y-%m-%d") - _dt.strptime(opened_date, "%Y-%m-%d")).days
    entry_share = 0
    for scan in scans:
        if scan["scan_date"] == opened_date:
            key = (symbol, strike, exp_date, opened_date)
            opt = option_lookup.get(key)
            if opt:
                entry_share = float(opt.get("underlying_price", 0) or 0)
            break
    if not entry_share:
        for scan in scans:
            if scan["scan_date"] == opened_date:
                for o in all_options:
                    if o["scan_id"] == scan["id"] and o["symbol"] == symbol and o.get("underlying_price"):
                        entry_share = float(o["underlying_price"])
                        break
                break

    daily_data.append({
        "date": opened_date, "occ": occ, "exp": exp_date,
        "dte": opened_dte, "share_price": entry_share,
        "option_price": price_paid, "price_paid": price_paid,
    })

    # Scan data after opened
    for scan in scans:
        sd = scan["scan_date"]
        if sd <= opened_date:
            continue
        key = (symbol, strike, exp_date, sd)
        opt = option_lookup.get(key)
        if opt:
            daily_data.append({
                "date": sd, "occ": occ, "exp": exp_date,
                "dte": opt.get("dte", 0) or 0,
                "share_price": float(opt.get("underlying_price", 0) or 0),
                "option_price": float(opt.get("put_price", 0) or 0),
                "price_paid": price_paid,
            })

    # Snapshots
    existing_dates = {d["date"] for d in daily_data}
    for snap in all_snapshots.get(pos["id"], []):
        if snap["snapshot_date"] not in existing_dates:
            daily_data.append({
                "date": snap["snapshot_date"], "occ": occ, "exp": exp_date,
                "dte": snap.get("dte", 0) or 0,
                "share_price": float(snap.get("share_price", 0) or 0),
                "option_price": float(snap.get("option_price", 0) or 0),
                "price_paid": price_paid,
            })

    daily_data.sort(key=lambda d: d["date"])

    # Dedup
    seen = set()
    deduped = []
    for d in daily_data:
        if d["date"] not in seen:
            seen.add(d["date"])
            deduped.append(d)
    daily_data = deduped

    print(f"Creating {tab_name}: {len(daily_data)} rows")

    try:
        add_result = service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        sheet_id = add_result["replies"][0]["addSheet"]["properties"]["sheetId"]
    except Exception as e:
        print(f"  Error: {e}")
        time.sleep(1)
        continue

    reqs = []

    # Title
    reqs.append({"mergeCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
        "mergeType": "MERGE_ALL",
    }})
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": 1},
        "rows": [{"values": [
            {"userEnteredValue": {"stringValue": f"Position: {company} ({symbol}) \u2014 {strike_int} Put"},
             "userEnteredFormat": {"backgroundColor": DARK_BLUE, "horizontalAlignment": "CENTER",
                                   "textFormat": {"foregroundColor": WHITE_TEXT, "fontSize": 13, "bold": True}}}
        ]}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Compute header-level IVx/Range from first scan_option match
    scan_opt_iv = iv_by_scan_option.get(pos.get("scan_option_id"))
    scan_opt_ul = ul_by_scan_option.get(pos.get("scan_option_id"))
    scan_opt_dte = dte_by_scan_option.get(pos.get("scan_option_id"))
    hdr_iv_pct = round(float(scan_opt_iv) * 100, 1) if scan_opt_iv else None
    # Use stored expected_move (straddle-based) if available, else fall back to IV formula
    hdr_range = em_by_scan_option.get(pos.get("scan_option_id"))
    if hdr_range:
        hdr_range = float(hdr_range)
    elif scan_opt_iv and scan_opt_ul and scan_opt_dte:
        hdr_range = round(float(scan_opt_ul) * float(scan_opt_iv) * _math.sqrt(float(scan_opt_dte) / 365), 2)

    # Row 2 (12 cols with IVx, Range)
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                  "startColumnIndex": 0, "endColumnIndex": 12},
        "rows": [{"values": [
            {"userEnteredValue": {"stringValue": "Symbol:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": symbol}},
            {"userEnteredValue": {"stringValue": "Name:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": company}},
            {"userEnteredValue": {"stringValue": "Strike:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"numberValue": strike_int}},
            {"userEnteredValue": {"stringValue": "Price Paid:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"numberValue": price_paid}, "userEnteredFormat": num_f},
            {"userEnteredValue": {"stringValue": "IVx:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": f"{hdr_iv_pct}%"} if hdr_iv_pct is not None else {"stringValue": "N/A"}},
            {"userEnteredValue": {"stringValue": "Range:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": f"±${hdr_range:.2f}"} if hdr_range is not None else {"stringValue": "N/A"}},
        ]}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Row 3 (10 cols with VIX)
    scan_vix_hdr = vix_by_scan_option.get(pos.get("scan_option_id"))
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3,
                  "startColumnIndex": 0, "endColumnIndex": 10},
        "rows": [{"values": [
            {"userEnteredValue": {"stringValue": "Expiration:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": exp_date}},
            {"userEnteredValue": {"stringValue": "Quantity:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"numberValue": quantity}},
            {"userEnteredValue": {"stringValue": "Direction:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": direction}},
            {"userEnteredValue": {"stringValue": "Purchase Date:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": opened_date}},
            {"userEnteredValue": {"stringValue": "VIX:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"numberValue": float(scan_vix_hdr)} if scan_vix_hdr is not None else {"stringValue": "N/A"}, "userEnteredFormat": num_f if scan_vix_hdr is not None else {}},
        ]}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Headers
    headers = ["Date", "OCC", "Expiration", "DTE", "Share Price", "Strike", "Difference", "Option Price", "P&L", "Range", "Limit"]
    hdr_cells = [{"userEnteredValue": {"stringValue": h}, "userEnteredFormat": hdr_fmt} for h in headers]
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 4, "endRowIndex": 5,
                  "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
        "rows": [{"values": hdr_cells}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Data rows
    for i, day in enumerate(daily_data):
        row_idx = 5 + i
        opt_price = day["option_price"]
        share_price = day["share_price"]
        diff = round(share_price - strike, 2) if share_price else 0
        pl = round(price_paid - opt_price, 2)

        em_key = (symbol, exp_date, day["date"])
        em_val = em_by_key.get(em_key)
        if em_val:
            range_r = em_val
        else:
            _iv_pct_r, range_r = _calc_iv_range(opt_price, share_price, strike_int, day["dte"])
        limit_r = round(float(share_price) - range_r, 2) if range_r is not None and share_price else None
        cells = [
            {"userEnteredValue": {"numberValue": to_serial(day["date"])}, "userEnteredFormat": dt_fmt},
            {"userEnteredValue": {"stringValue": occ}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"stringValue": day["exp"]}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"numberValue": day["dte"]}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"numberValue": share_price}, "userEnteredFormat": n_fmt},
            {"userEnteredValue": {"numberValue": strike_int}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"numberValue": diff}, "userEnteredFormat": n_fmt},
            {"userEnteredValue": {"numberValue": opt_price}, "userEnteredFormat": n_fmt},
            {"userEnteredValue": {"numberValue": pl}, "userEnteredFormat": n_fmt},
            {"userEnteredValue": {"stringValue": f"±${range_r:.2f}"} if range_r is not None else {"stringValue": "N/A"}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"numberValue": limit_r} if limit_r is not None else {"stringValue": "N/A"}, "userEnteredFormat": n_fmt if limit_r is not None else d_fmt},
        ]
        reqs.append({"updateCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                      "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
            "rows": [{"values": cells}],
            "fields": "userEnteredValue,userEnteredFormat",
        }})

    for ci, w in enumerate([100, 180, 100, 50, 100, 70, 90, 100, 80, 80, 90]):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": ci, "endIndex": ci + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize",
        }})

    try:
        service.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": reqs}).execute()
        existing_tabs[tab_name] = sheet_id
    except Exception as e:
        print(f"  Error: {e}")

    time.sleep(0.3)

# --- Part 2: Create Summary sheet ---
print("\n--- Creating Summary sheet ---")

# Delete existing Summary tab if present
if "Summary" in existing_tabs:
    service.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [{"deleteSheet": {"sheetId": existing_tabs["Summary"]}}]},
    ).execute()

add_result = service.spreadsheets().batchUpdate(
    spreadsheetId=SHEET_ID,
    body={"requests": [{"addSheet": {"properties": {"title": "Summary", "index": 0}}}]},
).execute()
summary_id = add_result["replies"][0]["addSheet"]["properties"]["sheetId"]

reqs = []

# Title
SUMMARY_COLS = 14
reqs.append({"mergeCells": {
    "range": {"sheetId": summary_id, "startRowIndex": 0, "endRowIndex": 1,
              "startColumnIndex": 0, "endColumnIndex": SUMMARY_COLS},
    "mergeType": "MERGE_ALL",
}})
reqs.append({"updateCells": {
    "range": {"sheetId": summary_id, "startRowIndex": 0, "endRowIndex": 1,
              "startColumnIndex": 0, "endColumnIndex": 1},
    "rows": [{"values": [
        {"userEnteredValue": {"stringValue": "Trade Summary — All Open Positions"},
         "userEnteredFormat": {"backgroundColor": DARK_BLUE, "horizontalAlignment": "CENTER",
                               "textFormat": {"foregroundColor": WHITE_TEXT, "fontSize": 13, "bold": True}}}
    ]}],
    "fields": "userEnteredValue,userEnteredFormat",
}})

# Headers
s_headers = ["OCC", "Symbol", "Company", "Strike", "Expiration", "Purchase Date",
             "Price Paid", "Current Price", "P&L", "Status", "IVR", "VIX", "IVx", "Range"]
s_hdr_cells = [{"userEnteredValue": {"stringValue": h}, "userEnteredFormat": hdr_fmt} for h in s_headers]
reqs.append({"updateCells": {
    "range": {"sheetId": summary_id, "startRowIndex": 2, "endRowIndex": 3,
              "startColumnIndex": 0, "endColumnIndex": SUMMARY_COLS},
    "rows": [{"values": s_hdr_cells}],
    "fields": "userEnteredValue,userEnteredFormat",
}})

# Reload tab list for hyperlinks (includes all tabs created in Part 1)
meta = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
all_tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

# Data rows — get latest option price from snapshots or last sheet row
for i, pos in enumerate(sorted(positions, key=lambda p: str(p.get("opened_at", ""))[:10])):
    row_idx = 3 + i
    symbol = pos["symbol"]
    strike = float(pos["strike"])
    exp_date = pos["exp_date"]
    occ = build_occ(symbol, exp_date, strike)
    company = pos.get("name", symbol)
    price_paid = float(pos.get("price_paid", 0) or 0)
    opened_date = str(pos.get("opened_at", ""))[:10]
    tab_label = build_tab_label(occ, opened_date)
    status = pos.get("status", "open").upper()

    # Get latest option price from snapshots
    snaps = all_snapshots.get(pos["id"], [])
    if snaps:
        latest = snaps[-1]
        current_price = float(latest.get("option_price", 0) or 0)
    else:
        current_price = price_paid  # No snapshots yet

    pl = round(price_paid - current_price, 2)

    scan_opt_id = pos.get("scan_option_id")
    ivr = ivr_by_scan_option.get(scan_opt_id)
    scan_vix = vix_by_scan_option.get(scan_opt_id)
    iv_raw = iv_by_scan_option.get(scan_opt_id)
    iv_pct = round(float(iv_raw) * 100, 1) if iv_raw else None
    ul_at_scan = ul_by_scan_option.get(scan_opt_id)
    dte_at_scan = dte_by_scan_option.get(scan_opt_id)
    exp_move = em_by_scan_option.get(scan_opt_id)
    if exp_move:
        exp_move = float(exp_move)
    elif iv_raw and ul_at_scan and dte_at_scan:
        import math as _math
        exp_move = round(float(ul_at_scan) * float(iv_raw) * _math.sqrt(float(dte_at_scan) / 365), 2)

    # Build hyperlink to the trade tab if it exists
    tab_gid = all_tabs.get(tab_label)
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
        {"userEnteredValue": {"stringValue": status}, "userEnteredFormat": d_fmt},
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

# Column widths
for ci, w in enumerate([180, 60, 180, 60, 100, 100, 90, 90, 80, 70, 50, 60, 60, 80]):
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": summary_id, "dimension": "COLUMNS", "startIndex": ci, "endIndex": ci + 1},
        "properties": {"pixelSize": w}, "fields": "pixelSize",
    }})

service.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": reqs}).execute()
print(f"Summary sheet created with {len(positions)} positions")

print("\nDone!")
