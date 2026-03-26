"""Migrate Position Tracker from per-contract tabs (POS-ADBE-225P) to per-symbol tabs (POS-ADBE).

Reads all existing POS-* tabs, groups by symbol, creates new merged tabs, deletes old ones.
"""
import re
from collections import defaultdict
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

SA_PATH = "C:/Users/acer/.claude/credentials/google-service-account.json"
SHEET_ID = "1F2jvkbnAFDMZQ_BbMXyVLVFgAutKrZ2QMSUKzy0RUXE"

creds = service_account.Credentials.from_service_account_file(
    SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
service = build("sheets", "v4", credentials=creds)


def get_tabs():
    spreadsheet = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in spreadsheet["sheets"]}


def read_tab(tab_name):
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{tab_name}'!A1:G100",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return result.get("values", [])


def extract_symbol(tab_name):
    """Extract symbol from tab name like POS-ADBE-225P or POS-LULU-140P-0417."""
    m = re.match(r"POS-([A-Z]+)-", tab_name)
    return m.group(1) if m else None


def parse_tab(tab_name, rows):
    """Extract contract info + data rows from a tab."""
    info = {"tab_name": tab_name}

    # Row 2: OCC, Strike, Expiration
    if len(rows) >= 2:
        r2 = rows[1]
        info["occ"] = r2[1] if len(r2) > 1 else ""
        info["strike"] = r2[3] if len(r2) > 3 else ""
        info["exp"] = r2[5] if len(r2) > 5 else ""

    # Row 3: Entry Premium, Entry Date, Direction
    if len(rows) >= 3:
        r3 = rows[2]
        info["entry_premium"] = r3[1] if len(r3) > 1 else ""
        info["entry_date"] = r3[3] if len(r3) > 3 else ""

    # Row 1: Title — extract company name
    if len(rows) >= 1:
        title = rows[0][0] if rows[0] else ""
        # "Position: ADOBE SYSTEMS INC (ADBE) — 230 Put"
        m = re.match(r"Position:\s*(.+?)\s*\(", title)
        info["company"] = m.group(1).strip() if m else ""

    # Data rows (after header in row 5, so row index 5+)
    data_rows = []
    for row in rows[5:]:
        if len(row) >= 7:
            data_rows.append({
                "date": row[0],
                "dte": row[1],
                "share_price": row[2],
                "strike": row[3],
                "difference": row[4],
                "option_price": row[5],
                "pl": row[6],
            })
    info["data"] = data_rows
    return info


def main():
    tabs = get_tabs()
    pos_tabs = {name: sid for name, sid in tabs.items() if name.startswith("POS-")}

    print(f"Found {len(pos_tabs)} POS-* tabs")

    # Group by symbol
    symbol_groups = defaultdict(list)
    for tab_name in sorted(pos_tabs.keys()):
        symbol = extract_symbol(tab_name)
        if symbol:
            symbol_groups[symbol].append(tab_name)

    print(f"\nSymbols: {list(symbol_groups.keys())}")
    for sym, tab_list in symbol_groups.items():
        print(f"  {sym}: {tab_list}")

    # Read & parse all tabs
    all_contracts = {}
    for tab_name in pos_tabs:
        rows = read_tab(tab_name)
        all_contracts[tab_name] = parse_tab(tab_name, rows)
        print(f"  Read {tab_name}: {len(all_contracts[tab_name]['data'])} data rows")

    # Build merged data per symbol
    from datetime import datetime as _dt

    # Colors & formatting
    DARK_BLUE = {"red": 0.149, "green": 0.247, "blue": 0.447}
    LIGHT_GRAY = {"red": 0.949, "green": 0.949, "blue": 0.949}
    WHITE_TEXT = {"red": 1, "green": 1, "blue": 1}
    BORDER_CLR = {"red": 0.698, "green": 0.698, "blue": 0.698}
    THIN = {"style": "SOLID", "width": 1, "color": BORDER_CLR}
    ALL_BORDERS = {"top": THIN, "bottom": THIN, "left": THIN, "right": THIN}

    data_fmt = {
        "backgroundColor": LIGHT_GRAY,
        "borders": ALL_BORDERS,
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "textFormat": {"fontSize": 10},
    }
    num_fmt = {**data_fmt, "numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}
    date_fmt = {**data_fmt, "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}
    hdr_fmt = {
        "backgroundColor": DARK_BLUE,
        "borders": ALL_BORDERS,
        "horizontalAlignment": "CENTER",
        "textFormat": {"foregroundColor": WHITE_TEXT, "fontSize": 10, "bold": True},
    }
    title_fmt = {
        "backgroundColor": DARK_BLUE,
        "horizontalAlignment": "CENTER",
        "textFormat": {"foregroundColor": WHITE_TEXT, "fontSize": 13, "bold": True},
    }

    NUM_COLS = 9  # Date, OCC, Strike, Exp, DTE, Share Price, Difference, Option Price, P&L

    def to_serial(date_str):
        """Convert YYYY-MM-DD to Google Sheets serial date."""
        try:
            dt = _dt.strptime(date_str, "%Y-%m-%d")
            return (dt - _dt(1899, 12, 30)).days
        except Exception:
            return 0

    def safe_float(val):
        try:
            return float(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    def safe_int(val):
        try:
            return int(float(str(val).replace(",", "")))
        except (ValueError, TypeError):
            return 0

    for symbol, tab_list in symbol_groups.items():
        new_tab_name = f"POS-{symbol}"
        print(f"\n--- Creating {new_tab_name} from {len(tab_list)} tabs ---")

        # Get company name from first contract
        first_contract = all_contracts[tab_list[0]]
        company = first_contract.get("company", symbol)

        # Collect all data rows with OCC + Exp info
        merged_rows = []
        for tab_name in tab_list:
            contract = all_contracts[tab_name]
            occ = contract.get("occ", "")
            strike = contract.get("strike", "")
            exp = contract.get("exp", "")

            for d in contract["data"]:
                merged_rows.append({
                    "date": d["date"],
                    "occ": occ,
                    "strike": strike,
                    "exp": exp,
                    "dte": d["dte"],
                    "share_price": d["share_price"],
                    "difference": d["difference"],
                    "option_price": d["option_price"],
                    "pl": d["pl"],
                })

        # Sort by date (newest first), then OCC
        merged_rows.sort(key=lambda r: (r["date"], r["occ"]))

        print(f"  Total merged data rows: {len(merged_rows)}")

        # Create new tab
        add_result = service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": new_tab_name}}}]},
        ).execute()
        new_sheet_id = add_result["replies"][0]["addSheet"]["properties"]["sheetId"]

        requests = []

        # Row 1: Title (merged)
        requests.append({"mergeCells": {
            "range": {"sheetId": new_sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                       "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
            "mergeType": "MERGE_ALL",
        }})
        requests.append({"updateCells": {
            "range": {"sheetId": new_sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                       "startColumnIndex": 0, "endColumnIndex": 1},
            "rows": [{"values": [
                {"userEnteredValue": {"stringValue": f"{company} ({symbol}) \u2014 All Positions"},
                 "userEnteredFormat": title_fmt}
            ]}],
            "fields": "userEnteredValue,userEnteredFormat",
        }})

        # Row 3: Header (row index 2)
        headers = ["Date", "OCC", "Strike", "Exp", "DTE", "Share Price", "Difference", "Option Price", "P&L"]
        hdr_cells = [{"userEnteredValue": {"stringValue": h}, "userEnteredFormat": hdr_fmt} for h in headers]
        requests.append({"updateCells": {
            "range": {"sheetId": new_sheet_id, "startRowIndex": 2, "endRowIndex": 3,
                       "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
            "rows": [{"values": hdr_cells}],
            "fields": "userEnteredValue,userEnteredFormat",
        }})

        # Data rows (starting at row index 3)
        for i, row in enumerate(merged_rows):
            row_idx = 3 + i
            cells = [
                {"userEnteredValue": {"numberValue": to_serial(row["date"])}, "userEnteredFormat": date_fmt},
                {"userEnteredValue": {"stringValue": row["occ"]}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": safe_int(row["strike"])}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"stringValue": row["exp"]}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": safe_int(row["dte"])}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": safe_float(row["share_price"])}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": safe_float(row["difference"])}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": safe_float(row["option_price"])}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": safe_float(row["pl"])}, "userEnteredFormat": num_fmt},
            ]
            requests.append({"updateCells": {
                "range": {"sheetId": new_sheet_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                           "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
                "rows": [{"values": cells}],
                "fields": "userEnteredValue,userEnteredFormat",
            }})

        # Column widths
        col_widths = [100, 180, 70, 100, 50, 100, 90, 100, 80]
        for i, w in enumerate(col_widths):
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": new_sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": w},
                "fields": "pixelSize",
            }})

        service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": requests},
        ).execute()
        print(f"  Created {new_tab_name} with {len(merged_rows)} rows")

    # Delete old per-contract tabs
    print("\n--- Deleting old per-contract tabs ---")
    # Re-fetch tabs to get current state
    tabs = get_tabs()
    delete_requests = []
    for tab_name in pos_tabs:
        # Only delete old-style tabs (with strike suffix), not new POS-SYMBOL ones
        if tab_name in tabs and re.match(r"POS-[A-Z]+-\d+", tab_name):
            delete_requests.append({"deleteSheet": {"sheetId": tabs[tab_name]}})
            print(f"  Deleting {tab_name}")

    if delete_requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": delete_requests},
        ).execute()
        print(f"\nDeleted {len(delete_requests)} old tabs")

    print("\nDone! Migration complete.")


if __name__ == "__main__":
    main()
