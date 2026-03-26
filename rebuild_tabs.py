"""Rebuild per-contract tabs from Supabase data. One tab per contract."""
import time
from datetime import datetime as _dt
from dotenv import load_dotenv
load_dotenv()

import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from supabase import create_client

SA_PATH = "C:/Users/acer/.claude/credentials/google-service-account.json"
SHEET_ID = "1F2jvkbnAFDMZQ_BbMXyVLVFgAutKrZ2QMSUKzy0RUXE"

creds = service_account.Credentials.from_service_account_file(
    SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"])
service = build("sheets", "v4", credentials=creds)
sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


def to_serial(date_str):
    try:
        return (_dt.strptime(date_str, "%Y-%m-%d") - _dt(1899, 12, 30)).days
    except:
        return 0

def sf(v):
    try:
        return float(str(v).replace(",", ""))
    except:
        return 0.0

def si(v):
    try:
        return int(float(str(v).replace(",", "")))
    except:
        return 0


# Load all open positions from Supabase
positions = sb.table("positions").select("*").eq("status", "open").execute().data
print(f"Open positions from Supabase: {len(positions)}")

# Load snapshots for each position
snapshots_by_pos = {}
for pos in positions:
    snaps = sb.table("position_snapshots").select("*").eq(
        "position_id", pos["id"]).order("snapshot_date").execute().data
    snapshots_by_pos[pos["id"]] = snaps

# Formatting
DARK_BLUE = {"red": 0.149, "green": 0.247, "blue": 0.447}
LIGHT_GRAY = {"red": 0.949, "green": 0.949, "blue": 0.949}
WHITE_TEXT = {"red": 1, "green": 1, "blue": 1}
BORDER_CLR = {"red": 0.698, "green": 0.698, "blue": 0.698}
THIN = {"style": "SOLID", "width": 1, "color": BORDER_CLR}
ALL_BORDERS = {"top": THIN, "bottom": THIN, "left": THIN, "right": THIN}

# Delete _temp if exists, or any remaining tabs
meta = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

created_tabs = []

for pos in positions:
    symbol = pos["symbol"]
    strike = int(float(pos["strike"]))
    exp = pos["exp_date"]
    company = pos.get("name", symbol)
    price_paid = float(pos.get("price_paid", 0) or 0)
    quantity = int(pos.get("quantity", 1) or 1)
    direction = pos.get("direction", "Short")
    snaps = snapshots_by_pos.get(pos["id"], [])

    tab_name = f"POS-{symbol}-{strike}P"
    if tab_name in created_tabs:
        exp_dt = _dt.strptime(exp, "%Y-%m-%d")
        tab_name = f"{tab_name}-{exp_dt.strftime('%m%d')}"
    created_tabs.append(tab_name)

    print(f"Creating {tab_name} ({len(snaps)} snapshots)...")

    # Skip if tab already exists from a previous partial run
    if tab_name in existing:
        print(f"  Already exists, skipping")
        continue

    try:
        add_result = service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        sheet_id = add_result["replies"][0]["addSheet"]["properties"]["sheetId"]
    except Exception as e:
        print(f"  Error creating tab: {e}")
        time.sleep(2)
        continue

    reqs = []
    bold = {"textFormat": {"bold": True}}
    num = {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}

    # Row 1: Title
    reqs.append({"mergeCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": 8},
        "mergeType": "MERGE_ALL",
    }})
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": 1},
        "rows": [{"values": [
            {"userEnteredValue": {"stringValue": f"Position: {company} ({symbol}) \u2014 {strike} Put"},
             "userEnteredFormat": {"backgroundColor": DARK_BLUE, "horizontalAlignment": "CENTER",
                                   "textFormat": {"foregroundColor": WHITE_TEXT, "fontSize": 13, "bold": True}}}
        ]}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Row 2: Symbol, Name, Strike, Price Paid (white, no borders)
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                  "startColumnIndex": 0, "endColumnIndex": 8},
        "rows": [{"values": [
            {"userEnteredValue": {"stringValue": "Symbol:"}, "userEnteredFormat": bold},
            {"userEnteredValue": {"stringValue": symbol}},
            {"userEnteredValue": {"stringValue": "Name:"}, "userEnteredFormat": bold},
            {"userEnteredValue": {"stringValue": company}},
            {"userEnteredValue": {"stringValue": "Strike:"}, "userEnteredFormat": bold},
            {"userEnteredValue": {"numberValue": strike}},
            {"userEnteredValue": {"stringValue": "Price Paid:"}, "userEnteredFormat": bold},
            {"userEnteredValue": {"numberValue": price_paid}, "userEnteredFormat": num},
        ]}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Row 3: Expiration, Quantity, Direction (white, no borders)
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3,
                  "startColumnIndex": 0, "endColumnIndex": 6},
        "rows": [{"values": [
            {"userEnteredValue": {"stringValue": "Expiration:"}, "userEnteredFormat": bold},
            {"userEnteredValue": {"stringValue": exp}},
            {"userEnteredValue": {"stringValue": "Quantity:"}, "userEnteredFormat": bold},
            {"userEnteredValue": {"numberValue": quantity}},
            {"userEnteredValue": {"stringValue": "Direction:"}, "userEnteredFormat": bold},
            {"userEnteredValue": {"stringValue": direction}},
        ]}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Row 5: Headers
    hdr_fmt = {"backgroundColor": DARK_BLUE, "borders": ALL_BORDERS, "horizontalAlignment": "CENTER",
               "textFormat": {"foregroundColor": WHITE_TEXT, "fontSize": 10, "bold": True}}
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 4, "endRowIndex": 5,
                  "startColumnIndex": 0, "endColumnIndex": 7},
        "rows": [{"values": [
            {"userEnteredValue": {"stringValue": h}, "userEnteredFormat": hdr_fmt}
            for h in ["Date", "DTE", "Share Price", "Strike", "Difference", "Option Price", "P&L"]
        ]}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Row 6: Entry row (day position was opened)
    d_fmt = {"backgroundColor": LIGHT_GRAY, "borders": ALL_BORDERS, "horizontalAlignment": "CENTER",
             "verticalAlignment": "MIDDLE", "textFormat": {"fontSize": 10}}
    n_fmt = {**d_fmt, "numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}
    dt_fmt = {**d_fmt, "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}

    opened_date = str(pos.get("opened_at", ""))[:10]
    opened_dte = ((_dt.strptime(exp, "%Y-%m-%d") - _dt.strptime(opened_date, "%Y-%m-%d")).days
                  if opened_date and exp else 0)

    # Use first snapshot's share price if available, otherwise 0
    first_share = float(snaps[0]["share_price"]) if snaps and snaps[0].get("share_price") else 0
    first_diff = round(first_share - strike, 2) if first_share else 0

    entry_cells = [
        {"userEnteredValue": {"numberValue": to_serial(opened_date)}, "userEnteredFormat": dt_fmt},
        {"userEnteredValue": {"numberValue": opened_dte}, "userEnteredFormat": d_fmt},
        {"userEnteredValue": {"numberValue": first_share}, "userEnteredFormat": n_fmt},
        {"userEnteredValue": {"numberValue": strike}, "userEnteredFormat": d_fmt},
        {"userEnteredValue": {"numberValue": first_diff}, "userEnteredFormat": n_fmt},
        {"userEnteredValue": {"numberValue": price_paid}, "userEnteredFormat": n_fmt},
        {"userEnteredValue": {"numberValue": 0.00}, "userEnteredFormat": n_fmt},
    ]
    reqs.append({"updateCells": {
        "range": {"sheetId": sheet_id, "startRowIndex": 5, "endRowIndex": 6,
                  "startColumnIndex": 0, "endColumnIndex": 7},
        "rows": [{"values": entry_cells}],
        "fields": "userEnteredValue,userEnteredFormat",
    }})

    # Subsequent snapshot rows (P&L = option price today - option price yesterday)
    prev_opt = price_paid
    for i, snap in enumerate(snaps):
        snap_date = snap["snapshot_date"]
        if snap_date == opened_date:
            continue  # Skip if same as entry day

        dte = snap.get("dte", 0) or 0
        share_price = float(snap.get("share_price", 0) or 0)
        opt_price = float(snap.get("option_price", 0) or 0)
        diff = round(share_price - strike, 2) if share_price else 0
        pl = round(opt_price - prev_opt, 2) if prev_opt is not None else 0.00
        prev_opt = opt_price

        row_idx = 6 + i
        cells = [
            {"userEnteredValue": {"numberValue": to_serial(snap_date)}, "userEnteredFormat": dt_fmt},
            {"userEnteredValue": {"numberValue": dte}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"numberValue": share_price}, "userEnteredFormat": n_fmt},
            {"userEnteredValue": {"numberValue": strike}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"numberValue": diff}, "userEnteredFormat": n_fmt},
            {"userEnteredValue": {"numberValue": opt_price}, "userEnteredFormat": n_fmt},
            {"userEnteredValue": {"numberValue": pl}, "userEnteredFormat": n_fmt},
        ]
        reqs.append({"updateCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                      "startColumnIndex": 0, "endColumnIndex": 7},
            "rows": [{"values": cells}],
            "fields": "userEnteredValue,userEnteredFormat",
        }})

    # Column widths
    for ci, w in enumerate([100, 60, 100, 70, 90, 100, 80]):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": ci, "endIndex": ci + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize",
        }})

    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID, body={"requests": reqs}).execute()
        print(f"  Done")
    except Exception as e:
        print(f"  Error writing: {e}")
        time.sleep(2)

    time.sleep(0.5)  # Rate limit

# Delete _temp
try:
    meta = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == "_temp":
            service.spreadsheets().batchUpdate(
                spreadsheetId=SHEET_ID,
                body={"requests": [{"deleteSheet": {"sheetId": s["properties"]["sheetId"]}}]},
            ).execute()
except:
    pass

print("\nDone!")
