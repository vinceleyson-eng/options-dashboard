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

NUM_COLS = 9

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
scans = sb.table("daily_scans").select("id, scan_date").order("scan_date").execute().data
scan_map = {s["id"]: s["scan_date"] for s in scans}

all_options = sb.table("scan_options").select(
    "scan_id, symbol, strike, exp_date, put_price, underlying_price, dte").execute().data
option_lookup = {}
for o in all_options:
    sd = scan_map.get(o["scan_id"])
    if sd:
        option_lookup[(o["symbol"], float(o["strike"]), o["exp_date"], sd)] = o

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

    # Row 2
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                  "startColumnIndex": 0, "endColumnIndex": 8},
        "rows": [{"values": [
            {"userEnteredValue": {"stringValue": "Symbol:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": symbol}},
            {"userEnteredValue": {"stringValue": "Name:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": company}},
            {"userEnteredValue": {"stringValue": "Strike:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"numberValue": strike_int}},
            {"userEnteredValue": {"stringValue": "Price Paid:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"numberValue": price_paid}, "userEnteredFormat": num_f},
        ]}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Row 3
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3,
                  "startColumnIndex": 0, "endColumnIndex": 8},
        "rows": [{"values": [
            {"userEnteredValue": {"stringValue": "Expiration:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": exp_date}},
            {"userEnteredValue": {"stringValue": "Quantity:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"numberValue": quantity}},
            {"userEnteredValue": {"stringValue": "Direction:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": direction}},
            {"userEnteredValue": {"stringValue": "Purchase Date:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": opened_date}},
        ]}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Headers
    headers = ["Date", "OCC", "Expiration", "DTE", "Share Price", "Strike", "Difference", "Option Price", "P&L"]
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
        ]
        reqs.append({"updateCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                      "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
            "rows": [{"values": cells}],
            "fields": "userEnteredValue,userEnteredFormat",
        }})

    for ci, w in enumerate([100, 180, 100, 50, 100, 70, 90, 100, 80]):
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
SUMMARY_COLS = 10
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
             "Price Paid", "Current Price", "P&L", "Status"]
s_hdr_cells = [{"userEnteredValue": {"stringValue": h}, "userEnteredFormat": hdr_fmt} for h in s_headers]
reqs.append({"updateCells": {
    "range": {"sheetId": summary_id, "startRowIndex": 2, "endRowIndex": 3,
              "startColumnIndex": 0, "endColumnIndex": SUMMARY_COLS},
    "rows": [{"values": s_hdr_cells}],
    "fields": "userEnteredValue,userEnteredFormat",
}})

# Data rows — get latest option price from snapshots or last sheet row
for i, pos in enumerate(sorted(positions, key=lambda p: (p["symbol"], float(p["strike"])))):
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

    cells = [
        {"userEnteredValue": {"stringValue": tab_label}, "userEnteredFormat": d_fmt},
        {"userEnteredValue": {"stringValue": symbol}, "userEnteredFormat": d_fmt},
        {"userEnteredValue": {"stringValue": company}, "userEnteredFormat": d_fmt},
        {"userEnteredValue": {"numberValue": int(strike)}, "userEnteredFormat": d_fmt},
        {"userEnteredValue": {"stringValue": exp_date}, "userEnteredFormat": d_fmt},
        {"userEnteredValue": {"stringValue": opened_date}, "userEnteredFormat": d_fmt},
        {"userEnteredValue": {"numberValue": price_paid}, "userEnteredFormat": n_fmt},
        {"userEnteredValue": {"numberValue": current_price}, "userEnteredFormat": n_fmt},
        {"userEnteredValue": {"numberValue": pl}, "userEnteredFormat": n_fmt},
        {"userEnteredValue": {"stringValue": status}, "userEnteredFormat": d_fmt},
    ]
    reqs.append({"updateCells": {
        "range": {"sheetId": summary_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                  "startColumnIndex": 0, "endColumnIndex": SUMMARY_COLS},
        "rows": [{"values": cells}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

# Column widths
for ci, w in enumerate([180, 60, 180, 60, 100, 100, 90, 90, 80, 70]):
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": summary_id, "dimension": "COLUMNS", "startIndex": ci, "endIndex": ci + 1},
        "properties": {"pixelSize": w}, "fields": "pixelSize",
    }})

service.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": reqs}).execute()
print(f"Summary sheet created with {len(positions)} positions")

print("\nDone!")
