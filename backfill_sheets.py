"""Backfill Google Sheet tabs with daily data from scan_options + snapshots.

One tab per symbol+strike (e.g., POS-ADBE-215P). Multiple expirations in same tab.
Columns: Date, OCC, Expiration, DTE, Share Price, Strike, Difference, Option Price, P&L
P&L = Option Price - Price Paid
"""
import time
from collections import defaultdict
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

def build_occ(symbol, exp_date, strike):
    exp_dt = _dt.strptime(exp_date, "%Y-%m-%d")
    return f"{symbol:<6}{exp_dt.strftime('%y%m%d')}P{int(float(strike) * 1000):08d}"

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

positions = sb.table("positions").select("*").eq("status", "open").execute().data
print(f"Loaded {len(all_options)} scan options, {len(positions)} positions")

all_snapshots = {}
for pos in positions:
    snaps = sb.table("position_snapshots").select("*").eq(
        "position_id", pos["id"]).order("snapshot_date").execute().data
    all_snapshots[pos["id"]] = snaps

# Group positions by symbol+strike (merge different expirations)
# Group by OCC (one tab per OCC)
groups = defaultdict(list)
for pos in positions:
    occ = build_occ(pos["symbol"], pos["exp_date"], float(pos["strike"]))
    groups[occ].append(pos)

print(f"Unique OCC contracts: {len(groups)}")

# Clear old tabs
meta = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

service.spreadsheets().batchUpdate(
    spreadsheetId=SHEET_ID,
    body={"requests": [{"addSheet": {"properties": {"title": "_temp"}}}]},
).execute()
del_reqs = [{"deleteSheet": {"sheetId": sid}} for n, sid in tabs.items()]
if del_reqs:
    service.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": del_reqs}).execute()
print("Cleared old tabs")

for occ_key, pos_list in sorted(groups.items()):
    tab_name = occ_key  # OCC as tab name

    # Use first position's info for header
    first = pos_list[0]
    symbol = first["symbol"]
    strike_int = int(float(first["strike"]))
    company = first.get("name", symbol)
    price_paid = float(first.get("price_paid", 0) or 0)
    quantity = int(first.get("quantity", 1) or 1)
    direction = first.get("direction", "Short")

    # Collect daily data for this OCC
    daily_data = []
    for pos in pos_list:
        exp_date = pos["exp_date"]
        pp = float(pos.get("price_paid", 0) or 0)
        opened_date = str(pos.get("opened_at", ""))[:10]
        occ = occ_key

        # From scan_options
        for scan in scans:
            sd = scan["scan_date"]
            if sd < opened_date:
                continue
            key = (symbol, float(pos["strike"]), exp_date, sd)
            opt = option_lookup.get(key)
            if opt:
                daily_data.append({
                    "date": sd, "occ": occ, "exp": exp_date,
                    "dte": opt.get("dte", 0) or 0,
                    "share_price": float(opt.get("underlying_price", 0) or 0),
                    "option_price": float(opt.get("put_price", 0) or 0),
                    "price_paid": pp,
                })

        # From snapshots
        existing_dates_occ = {(d["date"], d["occ"]) for d in daily_data}
        for snap in all_snapshots.get(pos["id"], []):
            if (snap["snapshot_date"], occ) not in existing_dates_occ:
                daily_data.append({
                    "date": snap["snapshot_date"], "occ": occ, "exp": exp_date,
                    "dte": snap.get("dte", 0) or 0,
                    "share_price": float(snap.get("share_price", 0) or 0),
                    "option_price": float(snap.get("option_price", 0) or 0),
                    "price_paid": pp,
                })

    daily_data.sort(key=lambda d: (d["date"], d["occ"]))

    # Dedup: one row per (date, occ) — keep first occurrence
    seen = set()
    deduped = []
    for d in daily_data:
        k = (d["date"], d["occ"])
        if k not in seen:
            seen.add(k)
            deduped.append(d)
    daily_data = deduped

    # Expiration from first position
    exp_display = first["exp_date"]

    print(f"Creating {tab_name}: {len(daily_data)} rows, exps: {exp_display}")

    try:
        add_result = service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        sheet_id = add_result["replies"][0]["addSheet"]["properties"]["sheetId"]
    except Exception as e:
        print(f"  Error: {e}")
        time.sleep(2)
        continue

    reqs = []

    # Row 1: Title
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

    # Row 2: Symbol, Name, Strike, Price Paid
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

    # Row 3: Expiration(s), Quantity, Direction, Purchase Date
    opened_date = str(first.get("opened_at", ""))[:10]
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3,
                  "startColumnIndex": 0, "endColumnIndex": 8},
        "rows": [{"values": [
            {"userEnteredValue": {"stringValue": "Expiration:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": exp_display}},
            {"userEnteredValue": {"stringValue": "Quantity:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"numberValue": quantity}},
            {"userEnteredValue": {"stringValue": "Direction:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": direction}},
            {"userEnteredValue": {"stringValue": "Purchase Date:"}, "userEnteredFormat": bold_f},
            {"userEnteredValue": {"stringValue": opened_date}},
        ]}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Row 5: Headers
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
        diff = round(share_price - strike_int, 2) if share_price else 0
        pl = round(day["price_paid"] - opt_price, 2)

        cells = [
            {"userEnteredValue": {"numberValue": to_serial(day["date"])}, "userEnteredFormat": dt_fmt},
            {"userEnteredValue": {"stringValue": day["occ"]}, "userEnteredFormat": d_fmt},
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
        print(f"  Done ({len(daily_data)} rows)")
    except Exception as e:
        print(f"  Error: {e}")

    time.sleep(0.3)

# Delete _temp
meta = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
for s in meta["sheets"]:
    if s["properties"]["title"] == "_temp":
        service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"deleteSheet": {"sheetId": s["properties"]["sheetId"]}}]},
        ).execute()

print("\nDone!")
