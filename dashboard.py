"""
Stan's Options Trading Dashboard — Streamlit App

Views:
1. Daily Option Research — browse daily scan reports, select options via checkbox
2. Open Positions — view all open positions with daily P&L tracking

Run: streamlit run dashboard.py
"""

import os
import json
import time
import asyncio
from decimal import Decimal
from datetime import date, datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# Google Sheets Position Tracker
POSITION_TRACKER_SHEET_ID = "1F2jvkbnAFDMZQ_BbMXyVLVFgAutKrZ2QMSUKzy0RUXE"
SA_PATH = "C:/Users/acer/.claude/credentials/google-service-account.json"


def get_secret(key, default=None):
    """Get secret from Streamlit Cloud secrets or .env fallback."""
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return os.getenv(key, default)


# --- TastyTrade Connection ---
def get_tastytrade_mode():
    return get_secret("TASTYTRADE_MODE", "sandbox").lower()


def get_tastytrade_session():
    """Create a TastyTrade session for the current mode."""
    from tastytrade import Session

    mode = get_tastytrade_mode()
    is_sandbox = mode == "sandbox"

    if is_sandbox:
        client_secret = get_secret("TASTYTRADE_SANDBOX_CLIENT_SECRET")
        refresh_token = get_secret("TASTYTRADE_SANDBOX_REFRESH_TOKEN")
    else:
        client_secret = get_secret("TASTYTRADE_CLIENT_SECRET")
        refresh_token = get_secret("TASTYTRADE_REFRESH_TOKEN")

    if not client_secret or not refresh_token:
        return None, f"Missing TastyTrade {mode} credentials"

    session = Session(client_secret, refresh_token, is_test=is_sandbox)
    return session, None


@st.cache_data(ttl=60)
def load_tastytrade_account():
    """Fetch account details from TastyTrade API with a hard 12s timeout."""
    import concurrent.futures

    def _sync():
        try:
            from tastytrade import Account

            session, error = get_tastytrade_session()
            if error:
                return {"error": error}

            mode = get_tastytrade_mode()

            async def _fetch():
                accounts = await asyncio.wait_for(Account.get(session), timeout=8)
                if not accounts:
                    return {"mode": mode, "accounts": []}
                account_list = []
                for acc in accounts:
                    balances = await asyncio.wait_for(acc.get_balances(session), timeout=8)
                    account_list.append({
                        "account_number": acc.account_number,
                        "account_type": getattr(acc, "account_type_name", "N/A"),
                        "margin_type": getattr(acc, "margin_or_cash", "N/A"),
                        "cash_balance": float(balances.cash_balance or 0),
                        "net_liquidating_value": float(balances.net_liquidating_value or 0),
                        "equity_buying_power": float(balances.equity_buying_power or 0),
                        "option_buying_power": float(getattr(balances, "derivative_buying_power", 0) or 0),
                        "maintenance_requirement": float(getattr(balances, "maintenance_requirement", 0) or 0),
                    })
                return {"mode": mode, "accounts": account_list}

            return asyncio.run(_fetch())
        except Exception as e:
            return {"error": str(e)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_sync)
        try:
            return future.result(timeout=12)
        except concurrent.futures.TimeoutError:
            return {"error": "TastyTrade connection timed out"}


def place_trade_on_tastytrade(option_data, quantity=1, dry_run=True):
    """Place a sell-to-open put order on TastyTrade.

    Returns dict with order details or error.
    dry_run=True validates without executing.
    """
    from tastytrade import Account
    from tastytrade.order import (
        NewOrder, Leg, OrderAction, OrderType,
        OrderTimeInForce, InstrumentType,
    )

    session, error = get_tastytrade_session()
    if error:
        return {"error": error}

    # Build the OCC option symbol: SYMBOL + YYMMDD + P + strike*1000 (8 digits)
    exp = option_data["exp_date"]  # "2026-04-17"
    exp_dt = datetime.strptime(exp, "%Y-%m-%d")
    exp_code = exp_dt.strftime("%y%m%d")
    strike_code = f"{int(float(option_data['strike']) * 1000):08d}"
    symbol = option_data["symbol"]
    occ_symbol = f"{symbol:<6}{exp_code}P{strike_code}"

    # Limit price at the mid (put_price from scan)
    limit_price = Decimal(str(option_data["put_price"]))

    leg = Leg(
        instrument_type=InstrumentType.EQUITY_OPTION,
        symbol=occ_symbol,
        action=OrderAction.SELL_TO_OPEN,
        quantity=quantity,
    )

    order = NewOrder(
        time_in_force=OrderTimeInForce.DAY,
        order_type=OrderType.LIMIT,
        legs=[leg],
        price=limit_price,  # positive = credit for selling
    )

    async def _place():
        accounts = await Account.get(session)
        if not accounts:
            return {"error": "No trading accounts found"}
        acc = accounts[0]
        result = await acc.place_order(session, order, dry_run=dry_run)
        return {
            "success": True,
            "dry_run": dry_run,
            "order_id": result.order.id if result.order else None,
            "status": result.order.status.value if result.order else None,
            "buying_power_effect": {
                "change": float(result.buying_power_effect.change_in_buying_power),
                "current": float(result.buying_power_effect.current_buying_power),
                "new": float(result.buying_power_effect.new_buying_power),
            },
            "fees": {
                "total": float(result.fee_calculation.total_fees),
                "commission": float(result.fee_calculation.commission),
            } if result.fee_calculation else None,
            "warnings": [str(w) for w in result.warnings] if result.warnings else [],
            "errors": [str(e) for e in result.errors] if result.errors else [],
        }

    try:
        return asyncio.run(_place())
    except Exception as e:
        return {"error": str(e)}


# --- Supabase Connection ---
@st.cache_resource
def get_supabase():
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        st.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in secrets")
        st.stop()
    return create_client(url, key)


# --- Page Config ---
st.set_page_config(
    page_title="Options Trading Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- CSS Enhancements (works with both light & dark Streamlit themes) ---
st.markdown("""
<style>
    h1 { font-size: 1.8rem !important; }
    h2 { font-size: 1.3rem !important; }
    h3 { font-size: 1.1rem !important; }
    [data-testid="stMetric"] {
        border-radius: 8px;
        padding: 12px 16px;
        border: 1px solid rgba(128, 128, 128, 0.2);
    }
    [data-testid="stMetricValue"] { font-size: 1.4rem !important; }
    .stDataFrame { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)


# --- Data Loading ---
@st.cache_data(ttl=30)
def load_scan_dates():
    sb = get_supabase()
    result = sb.table("daily_scans").select("id, scan_date, vix, risk_free_rate").order("scan_date", desc=True).execute()
    return result.data


@st.cache_data(ttl=10)
def load_scan_options(scan_id):
    sb = get_supabase()
    result = sb.table("scan_options").select("*").eq("scan_id", scan_id).order("symbol").execute()
    return result.data


@st.cache_data(ttl=30)
def load_all_scan_options():
    """Load scan options across all dates for watchlist symbols only."""
    sb = get_supabase()
    scans = sb.table("daily_scans").select("id, scan_date").order("scan_date", desc=True).execute().data
    scan_map = {s["id"]: s["scan_date"] for s in scans}

    # Get watchlist symbols from config
    config = sb.table("config").select("value").eq("key", "symbols").execute().data
    if config:
        import json
        val = config[0]["value"]
        watchlist = json.loads(val) if isinstance(val, str) else val
    else:
        watchlist = []

    # Only load scan_options for watchlist symbols
    all_options = []
    for sym in watchlist:
        result = sb.table("scan_options").select("*").eq("symbol", sym).order("symbol").execute()
        all_options.extend(result.data)

    for row in all_options:
        row["scan_date"] = scan_map.get(row["scan_id"], "Unknown")
    return all_options


@st.cache_data(ttl=10)
def load_positions():
    sb = get_supabase()
    result = sb.table("positions").select("*").eq("status", "open").order("opened_at", desc=True).execute()
    return result.data


@st.cache_data(ttl=10)
def load_all_positions():
    sb = get_supabase()
    result = sb.table("positions").select("*").order("opened_at", desc=True).execute()
    return result.data


@st.cache_data(ttl=30)
def load_position_snapshots(position_id):
    sb = get_supabase()
    result = sb.table("position_snapshots").select("*").eq("position_id", position_id).order("snapshot_date", desc=True).execute()
    return result.data


def toggle_selection(option_id, selected):
    sb = get_supabase()
    sb.table("scan_options").update({"selected": selected}).eq("id", option_id).execute()


def create_position(option, order_id=None, order_status=None):
    sb = get_supabase()
    position = {
        "scan_option_id": option["id"],
        "symbol": option["symbol"],
        "name": option["name"],
        "option_type": "Put",
        "strike": option["strike"],
        "exp_date": option["exp_date"],
        "price_paid": option["put_price"],
        "quantity": 1,
        "direction": "Short",
        "status": "open",
    }
    result = sb.table("positions").insert(position).execute()
    return result.data[0] if result.data else None


def get_google_sheets_service():
    """Get authenticated Google Sheets service — Streamlit secrets or local file."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    try:
        # Streamlit Cloud: read from secrets
        sa_info = dict(st.secrets["google_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    except (KeyError, FileNotFoundError):
        # Local: read from file
        creds = service_account.Credentials.from_service_account_file(
            SA_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    return build("sheets", "v4", credentials=creds)


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


def add_position_to_sheets(option):
    """Add position to Google Sheets Position Tracker.

    One tab per symbol+strike (e.g., POS-CRM-160P). Header rows with trade info.
    9 columns: Date, OCC, Expiration, DTE, Share Price, Strike, Difference, Option Price, P&L.
    P&L = Price Paid - Option Price (positive = profit for short put).
    """
    try:
        service = get_google_sheets_service()

        symbol = option["symbol"]
        strike = int(option["strike"])
        occ = build_occ_symbol(symbol, option["exp_date"], option["strike"])
        today_str = datetime.utcnow().strftime("%Y-%m-%d")

        # Get existing tabs
        spreadsheet = service.spreadsheets().get(spreadsheetId=POSITION_TRACKER_SHEET_ID).execute()
        existing_tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in spreadsheet["sheets"]}
        tab_name = build_tab_name(symbol, strike, option["exp_date"], opened_date=today_str, existing_tabs=existing_tabs)

        # Colors & formatting constants
        DARK_BLUE = {"red": 0.149, "green": 0.247, "blue": 0.447}
        LIGHT_GRAY = {"red": 0.949, "green": 0.949, "blue": 0.949}
        WHITE_TEXT = {"red": 1, "green": 1, "blue": 1}
        BORDER_CLR = {"red": 0.698, "green": 0.698, "blue": 0.698}
        THIN = {"style": "SOLID", "width": 1, "color": BORDER_CLR}
        ALL_BORDERS = {"top": THIN, "bottom": THIN, "left": THIN, "right": THIN}

        tab_exists = tab_name in existing_tabs

        from datetime import datetime as _dt

        put_price = option.get("put_price", 0) or 0
        dte_val = option.get("dte", 0) or 0
        share_price = option.get("underlying_price", 0) or 0
        difference = round(float(share_price) - float(strike), 2) if share_price else 0
        today_serial = (_dt.now() - _dt(1899, 12, 30)).days
        today_str = date.today().isoformat()
        exp_str = option.get("exp_date", "")

        # Data row formatting
        data_fmt = {
            "backgroundColor": LIGHT_GRAY, "borders": ALL_BORDERS,
            "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
            "textFormat": {"fontSize": 10},
        }
        num_fmt = {**data_fmt, "numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}
        date_fmt = {**data_fmt, "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}

        NUM_COLS = 9

        def _build_data_row(pl_value=0.00):
            """9 columns: Date, OCC, Expiration, DTE, Share Price, Strike, Difference, Option Price, P&L."""
            return [
                {"userEnteredValue": {"numberValue": today_serial}, "userEnteredFormat": date_fmt},
                {"userEnteredValue": {"stringValue": occ}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"stringValue": exp_str}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": dte_val}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": float(share_price)}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": strike}, "userEnteredFormat": data_fmt},
                {"userEnteredValue": {"numberValue": difference}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": float(put_price)}, "userEnteredFormat": num_fmt},
                {"userEnteredValue": {"numberValue": pl_value}, "userEnteredFormat": num_fmt},
            ]

        if tab_exists:
            sheet_id = existing_tabs[tab_name]

            result = service.spreadsheets().values().get(
                spreadsheetId=POSITION_TRACKER_SHEET_ID,
                range=f"'{tab_name}'!A:I",
                valueRenderOption="FORMATTED_VALUE",
            ).execute()
            existing_rows = result.get("values", [])
            next_row = len(existing_rows)

            # Deduplicate: check if today + OCC already exists
            for row in existing_rows:
                if len(row) >= 2 and row[0] == today_str and row[1] == occ:
                    return {"success": True, "tab_name": tab_name, "action": "already_exists",
                            "occ": occ, "message": f"{occ} already tracked on {today_str}"}

            # P&L = Price Paid - Option Price (0 on entry day)
            pl = round(float(put_price) - float(put_price), 2)

            requests = [{"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": next_row, "endRowIndex": next_row + 1,
                          "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
                "rows": [{"values": _build_data_row(pl)}],
                "fields": "userEnteredValue,userEnteredFormat",
            }}]

            service.spreadsheets().batchUpdate(
                spreadsheetId=POSITION_TRACKER_SHEET_ID, body={"requests": requests}).execute()

            return {"success": True, "tab_name": tab_name, "action": "appended", "occ": occ}

        else:
            # Create new tab
            add_result = service.spreadsheets().batchUpdate(
                spreadsheetId=POSITION_TRACKER_SHEET_ID,
                body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
            ).execute()
            sheet_id = add_result["replies"][0]["addSheet"]["properties"]["sheetId"]

            requests = []
            name = option.get("name", symbol)
            bold = {"textFormat": {"bold": True}}
            num = {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}

            # Row 1: Title (merged, dark blue)
            requests.append({"mergeCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                          "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
                "mergeType": "MERGE_ALL",
            }})
            requests.append({"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                          "startColumnIndex": 0, "endColumnIndex": 1},
                "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": f"Position: {name} ({symbol}) \u2014 {strike} Put"},
                     "userEnteredFormat": {"backgroundColor": DARK_BLUE, "horizontalAlignment": "CENTER",
                                           "textFormat": {"foregroundColor": WHITE_TEXT, "fontSize": 13, "bold": True}}}
                ]}],
                "fields": "userEnteredValue,userEnteredFormat",
            }})

            # Row 2: Symbol, Name, Strike, Price Paid
            requests.append({"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                          "startColumnIndex": 0, "endColumnIndex": 8},
                "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": "Symbol:"}, "userEnteredFormat": bold},
                    {"userEnteredValue": {"stringValue": symbol}},
                    {"userEnteredValue": {"stringValue": "Name:"}, "userEnteredFormat": bold},
                    {"userEnteredValue": {"stringValue": name}},
                    {"userEnteredValue": {"stringValue": "Strike:"}, "userEnteredFormat": bold},
                    {"userEnteredValue": {"numberValue": strike}},
                    {"userEnteredValue": {"stringValue": "Price Paid:"}, "userEnteredFormat": bold},
                    {"userEnteredValue": {"numberValue": float(put_price)}, "userEnteredFormat": num},
                ]}],
                "fields": "userEnteredValue,userEnteredFormat",
            }})

            # Row 3: Expiration, Quantity, Direction, Purchase Date
            requests.append({"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3,
                          "startColumnIndex": 0, "endColumnIndex": 8},
                "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": "Expiration:"}, "userEnteredFormat": bold},
                    {"userEnteredValue": {"stringValue": exp_str}},
                    {"userEnteredValue": {"stringValue": "Quantity:"}, "userEnteredFormat": bold},
                    {"userEnteredValue": {"numberValue": 1}},
                    {"userEnteredValue": {"stringValue": "Direction:"}, "userEnteredFormat": bold},
                    {"userEnteredValue": {"stringValue": "Short"}},
                    {"userEnteredValue": {"stringValue": "Purchase Date:"}, "userEnteredFormat": bold},
                    {"userEnteredValue": {"stringValue": today_str}},
                ]}],
                "fields": "userEnteredValue,userEnteredFormat",
            }})

            # Row 5: Headers
            headers = ["Date", "OCC", "Expiration", "DTE", "Share Price", "Strike", "Difference", "Option Price", "P&L"]
            hdr_fmt = {"backgroundColor": DARK_BLUE, "borders": ALL_BORDERS, "horizontalAlignment": "CENTER",
                       "textFormat": {"foregroundColor": WHITE_TEXT, "fontSize": 10, "bold": True}}
            hdr_cells = [{"userEnteredValue": {"stringValue": h}, "userEnteredFormat": hdr_fmt} for h in headers]
            requests.append({"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 4, "endRowIndex": 5,
                          "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
                "rows": [{"values": hdr_cells}],
                "fields": "userEnteredValue,userEnteredFormat",
            }})

            # Row 6: First data row (P&L = 0 on entry day)
            requests.append({"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 5, "endRowIndex": 6,
                          "startColumnIndex": 0, "endColumnIndex": NUM_COLS},
                "rows": [{"values": _build_data_row()}],
                "fields": "userEnteredValue,userEnteredFormat",
            }})

            # Column widths
            for i, w in enumerate([100, 180, 100, 50, 100, 70, 90, 100, 80]):
                requests.append({"updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                    "properties": {"pixelSize": w}, "fields": "pixelSize",
                }})

            service.spreadsheets().batchUpdate(
                spreadsheetId=POSITION_TRACKER_SHEET_ID, body={"requests": requests}).execute()

            return {"success": True, "tab_name": tab_name, "action": "created", "occ": occ}

    except Exception as e:
        return {"error": str(e)}


def close_position(position_id):
    sb = get_supabase()
    sb.table("positions").update({
        "status": "closed",
        "closed_at": datetime.utcnow().isoformat(),
    }).eq("id", position_id).execute()


def update_summary_sheet(option, position):
    """Append a row to the Summary sheet when a new position is created."""
    try:
        service = get_google_sheets_service()
        today_str = date.today().isoformat()
        occ = build_occ_symbol(option["symbol"], option["exp_date"], option["strike"])
        tab_name = build_tab_name(option["symbol"], int(option["strike"]), option["exp_date"], opened_date=today_str)

        spreadsheet = service.spreadsheets().get(spreadsheetId=POSITION_TRACKER_SHEET_ID).execute()
        tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in spreadsheet["sheets"]}

        if "Summary" not in tabs:
            return

        sheet_id = tabs["Summary"]

        # Read existing rows to find next empty row
        result = service.spreadsheets().values().get(
            spreadsheetId=POSITION_TRACKER_SHEET_ID,
            range="'Summary'!A:J",
            valueRenderOption="FORMATTED_VALUE",
        ).execute()
        next_row = len(result.get("values", []))

        LIGHT_GRAY = {"red": 0.949, "green": 0.949, "blue": 0.949}
        BORDER_CLR = {"red": 0.698, "green": 0.698, "blue": 0.698}
        THIN = {"style": "SOLID", "width": 1, "color": BORDER_CLR}
        ALL_BORDERS = {"top": THIN, "bottom": THIN, "left": THIN, "right": THIN}
        d_fmt = {"backgroundColor": LIGHT_GRAY, "borders": ALL_BORDERS, "horizontalAlignment": "CENTER",
                 "verticalAlignment": "MIDDLE", "textFormat": {"fontSize": 10}}
        n_fmt = {**d_fmt, "numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}

        symbol = option["symbol"]
        strike = int(option["strike"])
        put_price = float(option.get("put_price", 0) or 0)
        company = option.get("name", symbol)
        exp_date = option.get("exp_date", "")

        cells = [
            {"userEnteredValue": {"stringValue": tab_name}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"stringValue": symbol}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"stringValue": company}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"numberValue": strike}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"stringValue": exp_date}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"stringValue": today_str}, "userEnteredFormat": d_fmt},
            {"userEnteredValue": {"numberValue": put_price}, "userEnteredFormat": n_fmt},
            {"userEnteredValue": {"numberValue": put_price}, "userEnteredFormat": n_fmt},
            {"userEnteredValue": {"numberValue": 0.00}, "userEnteredFormat": n_fmt},
            {"userEnteredValue": {"stringValue": "OPEN"}, "userEnteredFormat": d_fmt},
        ]

        service.spreadsheets().batchUpdate(
            spreadsheetId=POSITION_TRACKER_SHEET_ID,
            body={"requests": [{"updateCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": next_row, "endRowIndex": next_row + 1,
                          "startColumnIndex": 0, "endColumnIndex": 10},
                "rows": [{"values": cells}],
                "fields": "userEnteredValue,userEnteredFormat",
            }}]},
        ).execute()
    except Exception as e:
        pass  # Don't block the main flow if Summary update fails


def build_options_dataframe(options, existing_option_ids):
    """Build a clean DataFrame from options data for display."""
    rows = []
    for o in options:
        has_position = o["id"] in existing_option_ids
        rows.append({
            "Select": has_position or o.get("selected", False),
            "Symbol": o.get("symbol", "-"),
            "Company": (o.get("name") or "-"),
            "IVR %": round(o["iv_rank"], 1) if o.get("iv_rank") is not None else None,
            "DTE": o.get("dte"),
            "Delta": round(o["delta"], 4) if o.get("delta") is not None else None,
            "Exp Date": o.get("exp_date", "-"),
            "POP %": round(o["pop"], 1) if o.get("pop") is not None else None,
            "P50 %": round(o["p50"], 1) if o.get("p50") is not None else None,
            "Strike": o.get("strike"),
            "Bid": o.get("bid"),
            "Ask": o.get("ask"),
            "Spread": o.get("bid_ask_spread"),
            "Put Price": o.get("put_price"),
            "Underlying": o.get("underlying_price"),
            "Earnings": o.get("earnings", "-"),
            "_id": o["id"],
            "_has_position": has_position,
        })
    return pd.DataFrame(rows)


# --- Trade Confirmation Dialog ---
@st.dialog("Confirm Trade", width="large")
def trade_confirmation_dialog(option):
    """Show trade confirmation popup with dry-run validation."""
    mode = get_tastytrade_mode()
    is_sandbox = mode == "sandbox"
    mode_label = "SANDBOX" if is_sandbox else "LIVE"
    mode_color = "#f0ad4e" if is_sandbox else "#d9534f"

    st.markdown(
        f'<span style="background:{mode_color}; color:#fff; padding:3px 10px; '
        f'border-radius:4px; font-size:0.8rem; font-weight:600;">{mode_label} ORDER</span>',
        unsafe_allow_html=True,
    )

    st.subheader(f"Sell to Open: {option['symbol']} Put")

    # Order details
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Symbol:** {option['symbol']}")
        st.markdown(f"**Company:** {option.get('name', '-')}")
        st.markdown(f"**Strike:** ${option['strike']:.0f}")
        st.markdown(f"**Expiration:** {option['exp_date']}")
    with col2:
        st.markdown(f"**Direction:** Sell to Open (Short Put)")
        st.markdown(f"**Quantity:** 1 contract")
        st.markdown(f"**Limit Price:** ${option['put_price']:.2f} (credit)")
        st.markdown(f"**Time in Force:** Day")
        st.markdown(f"**Order Type:** Limit")

    st.divider()

    # Action buttons — two paths
    btn_col1, btn_col2 = st.columns(2)

    # Path 1: Track Position (manual — Google Sheets + Supabase)
    with btn_col1:
        if st.button("Track Position", type="primary", use_container_width=True,
                      help="Add to Google Sheets Position Tracker + Supabase (no order placed)"):
            with st.spinner("Adding to Position Tracker..."):
                sheet_result = add_position_to_sheets(option)
                if "error" in sheet_result:
                    st.error(f"Sheets error: {sheet_result['error']}")
                else:
                    if sheet_result.get("action") == "already_exists":
                        st.warning(sheet_result.get("message", "Already tracked"))
                    else:
                        # Record in Supabase
                        toggle_selection(option["id"], True)
                        pos = create_position(option)
                        # Update Summary sheet
                        update_summary_sheet(option, pos)
                        st.cache_data.clear()
                        st.session_state.dry_run_result = None
                        st.toast(f"Position tracked: {option['symbol']} {option['strike']:.0f} Put", icon="✅")
                        time.sleep(1)
                        st.rerun()

    # Path 2: Validate & Place Order (TastyTrade)
    with btn_col2:
        if st.button("Validate Order (Dry Run)", type="secondary", use_container_width=True,
                      help="Validate order with TastyTrade before placing"):
            with st.spinner("Validating order with TastyTrade..."):
                result = place_trade_on_tastytrade(option, quantity=1, dry_run=True)
                st.session_state.dry_run_result = result

    # Show dry-run results
    if "dry_run_result" not in st.session_state:
        st.session_state.dry_run_result = None

    if st.session_state.dry_run_result:
        result = st.session_state.dry_run_result

        if "error" in result:
            st.error(f"Validation failed: {result['error']}")
        else:
            st.success("Order validated successfully")

            # Buying power impact
            bp = result.get("buying_power_effect", {})
            bp_col1, bp_col2, bp_col3 = st.columns(3)
            bp_col1.metric("Current Buying Power", f"${bp.get('current', 0):,.2f}")
            bp_col2.metric("Change", f"${bp.get('change', 0):,.2f}")
            bp_col3.metric("New Buying Power", f"${bp.get('new', 0):,.2f}")

            # Fees
            fees = result.get("fees")
            if fees:
                st.caption(f"Fees: ${fees['total']:.2f} (Commission: ${fees['commission']:.2f})")

            # Warnings
            if result.get("warnings"):
                for w in result["warnings"]:
                    st.warning(w)

            if result.get("errors"):
                for e in result["errors"]:
                    st.error(e)

            st.divider()

            # Confirm & place real order
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Confirm & Place Order", type="primary", use_container_width=True):
                    with st.spinner("Placing order on TastyTrade..."):
                        live_result = place_trade_on_tastytrade(option, quantity=1, dry_run=False)

                    if "error" in live_result:
                        st.error(f"Order failed: {live_result['error']}")
                    else:
                        order_id = live_result.get("order_id")
                        order_status = live_result.get("status")

                        # Record in Supabase
                        toggle_selection(option["id"], True)
                        pos = create_position(option, order_id=order_id, order_status=order_status)

                        # Also add to Google Sheets + Summary
                        add_position_to_sheets(option)
                        update_summary_sheet(option, pos)

                        st.session_state.dry_run_result = None
                        st.cache_data.clear()
                        st.toast(f"Order placed: {option['symbol']} {option['strike']:.0f} Put | Order #{order_id}", icon="✅")
                        time.sleep(1)
                        st.rerun()

            with c2:
                if st.button("Cancel", use_container_width=True):
                    st.session_state.dry_run_result = None
                    st.rerun()


# --- Sidebar ---
st.sidebar.title("Options Dashboard")
st.sidebar.markdown(f"**Today:** {date.today().strftime('%A, %B %d, %Y')}")

# TastyTrade Account Info
tt_data = load_tastytrade_account()
if "error" in tt_data:
    st.sidebar.warning(f"TastyTrade: {tt_data['error']}")
else:
    mode = tt_data["mode"]
    is_sandbox = mode == "sandbox"
    mode_label = "SANDBOX" if is_sandbox else "LIVE"
    mode_color = "#f0ad4e" if is_sandbox else "#5cb85c"

    st.sidebar.markdown(
        f'<div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">'
        f'<span style="font-weight:600;">TastyTrade</span>'
        f'<span style="background:{mode_color}; color:#fff; padding:2px 8px; '
        f'border-radius:4px; font-size:0.75rem; font-weight:600;">{mode_label}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if tt_data.get("accounts"):
        for acc in tt_data["accounts"]:
            st.sidebar.markdown(f"**Account:** `{acc['account_number']}`")
            sb_col1, sb_col2 = st.sidebar.columns(2)
            sb_col1.metric("Cash", f"${acc['cash_balance']:,.0f}")
            sb_col2.metric("Net Liq", f"${acc['net_liquidating_value']:,.0f}")
    else:
        st.sidebar.warning("No trading accounts found.")

st.sidebar.caption("Theme: Settings (top-right) > Theme")

st.sidebar.divider()

page = st.sidebar.radio(
    "Navigate",
    ["Daily Research", "Open Positions", "Position History", "Shadow Positions", "Config"],
    index=0,
)

# --- Daily Research Page ---
if page == "Daily Research":
    st.title("Daily Option Research")
    st.caption(f"Today is {date.today().strftime('%A, %B %d, %Y')}")

    # Load ALL options across all scan dates
    all_options = load_all_scan_options()

    if not all_options:
        st.warning("No scan data available yet. Run the daily scanner first.")
        st.stop()

    # Get unique dates and symbols for filters
    all_dates = sorted(set(o["scan_date"] for o in all_options), reverse=True)
    all_symbols = sorted(set(o["symbol"] for o in all_options))

    # Header metrics
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Scan Dates", len(all_dates))
    with col2:
        st.metric("Symbols", len(all_symbols))

    st.divider()

    # Filter controls
    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    with col_f1:
        selected_dates = st.multiselect("Filter by Date", all_dates, default=[all_dates[0]] if all_dates else [])
    with col_f2:
        selected_symbols = st.multiselect("Filter by Symbol", all_symbols, default=all_symbols)
    with col_f3:
        show_selected_only = st.checkbox("Show selected only", value=False)
    with col_f4:
        sort_by = st.selectbox("Sort by", ["Scan Date", "Symbol", "IVR %", "POP %", "P50 %", "Delta", "DTE"], index=0)

    # Filter
    filtered = [o for o in all_options if o["scan_date"] in selected_dates and o["symbol"] in selected_symbols]
    if show_selected_only:
        filtered = [o for o in filtered if o.get("selected")]

    # Get existing positions for checkbox state — match by scan_option_id only
    # Same contract on a different scan date can be ordered again (different sheet tab)
    existing_positions = load_all_positions()
    existing_option_ids = {p["scan_option_id"] for p in existing_positions if p.get("scan_option_id")}

    # Build DataFrame with Scan Date column
    rows = []
    for o in filtered:
        has_position = o["id"] in existing_option_ids
        rows.append({
            "Select": has_position or o.get("selected", False),
            "Scan Date": o.get("scan_date", "-"),
            "Symbol": o.get("symbol", "-"),
            "Company": (o.get("name") or "-"),
            "IVR %": round(o["iv_rank"], 1) if o.get("iv_rank") is not None else None,
            "DTE": o.get("dte"),
            "Delta": round(o["delta"], 4) if o.get("delta") is not None else None,
            "Exp Date": o.get("exp_date", "-"),
            "POP %": round(o["pop"], 1) if o.get("pop") is not None else None,
            "P50 %": round(o["p50"], 1) if o.get("p50") is not None else None,
            "Strike": o.get("strike"),
            "Bid": o.get("bid"),
            "Ask": o.get("ask"),
            "Spread": round(o["bid_ask_spread"], 2) if o.get("bid_ask_spread") is not None else None,
            "Put Price": o.get("put_price"),
            "Underlying": o.get("underlying_price"),
            "Earnings": str(o["earnings"]) if o.get("earnings") else "-",
            "_id": o["id"],
            "_has_position": has_position,
        })

    df = pd.DataFrame(rows)

    if df.empty:
        st.info("No options match your filters.")
        st.stop()

    # Sort
    sort_col_map = {
        "Scan Date": "Scan Date",
        "Symbol": "Symbol",
        "IVR %": "IVR %",
        "POP %": "POP %",
        "P50 %": "P50 %",
        "Delta": "Delta",
        "DTE": "DTE",
    }
    sort_col = sort_col_map.get(sort_by, "Scan Date")
    ascending = sort_by in ["Symbol", "Delta", "DTE"]
    if sort_by == "Scan Date":
        df = df.sort_values(by=["Scan Date", "Symbol"], ascending=[False, True], na_position="last").reset_index(drop=True)
    else:
        df = df.sort_values(by=sort_col, ascending=ascending, na_position="last").reset_index(drop=True)

    col_hdr, col_export = st.columns([3, 1])
    with col_hdr:
        st.subheader(f"All Options ({len(df)} rows)")
    with col_export:
        export_cols = ["Scan Date", "Symbol", "Company", "Strike", "Put Price", "DTE", "POP %",
                       "IVR %", "Delta", "Exp Date", "P50 %", "Bid", "Ask", "Spread",
                       "Underlying", "Earnings"]
        st.download_button(
            label="Export CSV",
            data=df[export_cols].to_csv(index=False),
            file_name=f"options_all_{date.today()}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # Display columns with Scan Date
    display_cols = ["Select", "Scan Date", "Symbol", "Company", "Strike", "Put Price", "DTE", "POP %",
                    "IVR %", "Delta", "Exp Date", "P50 %", "Bid", "Ask", "Spread",
                    "Underlying", "Earnings"]

    column_config = {
        "Select": st.column_config.CheckboxColumn("Select", help="Check to open trade dialog", width="small"),
        "Scan Date": st.column_config.TextColumn("Scan Date", width="small"),
        "Symbol": st.column_config.TextColumn("Symbol", width="small"),
        "Company": st.column_config.TextColumn("Company", width="medium"),
        "IVR %": st.column_config.NumberColumn("IVR %", format="%.1f", width="small"),
        "DTE": st.column_config.NumberColumn("DTE", format="%d", width="small"),
        "Delta": st.column_config.NumberColumn("Delta", format="%.4f", width="small"),
        "Exp Date": st.column_config.TextColumn("Exp Date", width="small"),
        "POP %": st.column_config.NumberColumn("POP %", format="%.1f", width="small"),
        "P50 %": st.column_config.NumberColumn("P50 %", format="%.1f", width="small"),
        "Strike": st.column_config.NumberColumn("Strike", format="%.0f", width="small"),
        "Bid": st.column_config.NumberColumn("Bid", format="$%.2f", width="small"),
        "Ask": st.column_config.NumberColumn("Ask", format="$%.2f", width="small"),
        "Spread": st.column_config.NumberColumn("Spread", format="$%.2f", width="small"),
        "Put Price": st.column_config.NumberColumn("Put Price", format="$%.2f", width="small"),
        "Underlying": st.column_config.NumberColumn("Underlying", format="$%.2f", width="medium"),
        "Earnings": st.column_config.TextColumn("Earnings", width="small"),
    }

    edited_df = st.data_editor(
        df[display_cols],
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
        height=min(len(df) * 35 + 38, 800),
        disabled=[c for c in display_cols if c != "Select"],
        key="options_table",
    )

    # Detect checkbox → open trade dialog
    if edited_df is not None:
        for idx in range(len(edited_df)):
            new_sel = edited_df.iloc[idx]["Select"]
            old_sel = df.iloc[idx]["Select"]
            option_id = df.iloc[idx]["_id"]
            has_position = df.iloc[idx]["_has_position"]
            if new_sel and not old_sel and not has_position:
                opt = next((o for o in filtered if o["id"] == option_id), None)
                if opt:
                    st.session_state.dry_run_result = None
                    trade_confirmation_dialog(opt)


# --- Open Positions Page ---
elif page == "Open Positions":
    st.title("Open Positions")
    st.caption(f"Today is {date.today().strftime('%A, %B %d, %Y')}")

    positions = load_positions()

    if not positions:
        st.info("No open positions. Select options from the Daily Research page to create positions.")
        st.stop()

    # Summary metrics
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        st.metric("Open Positions", len(positions))
    with col2:
        symbols = set(p["symbol"] for p in positions)
        st.metric("Symbols", len(symbols))

    st.divider()

    # Positions table
    pos_rows = []
    for pos in positions:
        pos_rows.append({
            "Symbol": pos["symbol"],
            "Company": pos.get("name", "-"),
            "Type": pos.get("option_type", "Put"),
            "Strike": pos.get("strike"),
            "Direction": pos.get("direction", "Short"),
            "Qty": pos.get("quantity", 1),
            "Price Paid": pos.get("price_paid"),
            "Exp Date": pos.get("exp_date", "-"),
            "Opened": str(pos.get("opened_at", ""))[:10],
        })

    pos_df = pd.DataFrame(pos_rows)

    col_tbl, col_exp = st.columns([3, 1])
    with col_tbl:
        st.dataframe(
            pos_df,
            column_config={
                "Strike": st.column_config.NumberColumn("Strike", format="%.0f"),
                "Price Paid": st.column_config.NumberColumn("Price Paid", format="$%.2f"),
            },
            width="stretch",
            hide_index=True,
        )
    with col_exp:
        st.download_button(
            label="Export CSV",
            data=pos_df.to_csv(index=False),
            file_name=f"open_positions_{date.today()}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.divider()

    # Position detail expanders
    for pos in positions:
        with st.expander(
            f"{pos['symbol']} — {pos.get('name', '')} | "
            f"Strike: {pos['strike']:.0f} | "
            f"Exp: {pos['exp_date']}"
        ):
            # Daily snapshots
            snapshots = load_position_snapshots(pos["id"])
            if snapshots:
                snap_rows = []
                for snap in snapshots:
                    snap_rows.append({
                        "Date": snap["snapshot_date"],
                        "DTE": snap.get("dte"),
                        "Share Price": snap.get("share_price"),
                        "Option Price": snap.get("option_price"),
                        "Difference": snap.get("difference"),
                        "P&L": snap.get("pl"),
                    })
                snap_df = pd.DataFrame(snap_rows)
                st.dataframe(
                    snap_df,
                    column_config={
                        "Share Price": st.column_config.NumberColumn("Share Price", format="$%.2f"),
                        "Option Price": st.column_config.NumberColumn("Option Price", format="$%.2f"),
                        "Difference": st.column_config.NumberColumn("Difference", format="$%.2f"),
                        "P&L": st.column_config.NumberColumn("P&L", format="$%+.2f"),
                    },
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("No daily snapshots yet. Position tracker will populate this data.")

            if st.button("Close Position", key=f"close_{pos['id']}", type="secondary"):
                close_position(pos["id"])
                st.toast(f"Position closed: {pos['symbol']} {pos['strike']:.0f}", icon="🔒")
                st.cache_data.clear()
                st.rerun()


# --- Position History Page ---
elif page == "Position History":
    st.title("Position History")
    st.caption(f"Today is {date.today().strftime('%A, %B %d, %Y')}")

    all_positions = load_all_positions()

    if not all_positions:
        st.info("No positions yet.")
        st.stop()

    open_count = sum(1 for p in all_positions if p["status"] == "open")
    closed_count = sum(1 for p in all_positions if p["status"] == "closed")

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Positions", len(all_positions))
    col2.metric("Open", open_count)
    col3.metric("Closed", closed_count)

    st.divider()

    status_filter = st.selectbox("Filter by Status", ["All", "Open", "Closed"], index=0)

    filtered_positions = all_positions
    if status_filter == "Open":
        filtered_positions = [p for p in all_positions if p["status"] == "open"]
    elif status_filter == "Closed":
        filtered_positions = [p for p in all_positions if p["status"] == "closed"]

    if filtered_positions:
        hist_rows = []
        for pos in filtered_positions:
            hist_rows.append({
                "Status": "Open" if pos["status"] == "open" else "Closed",
                "Symbol": pos["symbol"],
                "Company": pos.get("name", "-"),
                "Strike": pos.get("strike"),
                "Exp Date": pos.get("exp_date", "-"),
                "Price Paid": pos.get("price_paid"),
                "Direction": pos.get("direction", "Short"),
                "Opened": str(pos.get("opened_at", ""))[:10],
                "Closed": str(pos.get("closed_at", ""))[:10] if pos.get("closed_at") else "-",
            })
        hist_df = pd.DataFrame(hist_rows)
        col_tbl, col_exp = st.columns([3, 1])
        with col_tbl:
            st.dataframe(
                hist_df,
                column_config={
                    "Strike": st.column_config.NumberColumn("Strike", format="%.0f"),
                    "Price Paid": st.column_config.NumberColumn("Price Paid", format="$%.2f"),
                },
                width="stretch",
                hide_index=True,
            )
        with col_exp:
            st.download_button(
                label="Export CSV",
                data=hist_df.to_csv(index=False),
                file_name=f"position_history_{date.today()}.csv",
                mime="text/csv",
                use_container_width=True,
            )
    else:
        st.info("No positions match your filter.")


# --- Shadow Positions Page ---
elif page == "Shadow Positions":
    st.title("Shadow Positions (Analytics)")
    st.caption("Auto-tracked positions for every option in each scan — for performance analysis")

    sb = get_supabase()

    # Load all shadow positions
    @st.cache_data(ttl=30)
    def load_shadow_positions(symbol_filter=None, date_filter=None):
        query = sb.table("shadow_positions").select("*").order("scan_date", desc=True)
        if symbol_filter:
            query = query.eq("symbol", symbol_filter)
        if date_filter:
            query = query.eq("scan_date", date_filter)
        return query.execute().data

    # Get available symbols and dates for filters
    all_shadow = sb.table("shadow_positions").select("symbol, scan_date").execute().data
    if not all_shadow:
        st.info("No shadow positions yet. They are auto-created when scans are pushed.")
        st.stop()

    available_symbols = sorted(set(s["symbol"] for s in all_shadow))
    available_dates = sorted(set(s["scan_date"] for s in all_shadow), reverse=True)

    # Summary metrics
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Shadow Positions", len(all_shadow))
    col2.metric("Symbols", len(available_symbols))
    col3.metric("Scan Dates", len(available_dates))

    st.divider()

    # Filters
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        sym_filter = st.selectbox("Filter by Symbol", ["All"] + available_symbols, index=0)
    with col_f2:
        date_filter = st.selectbox("Filter by Scan Date", ["All"] + available_dates, index=0)

    # Load filtered data
    sym_val = sym_filter if sym_filter != "All" else None
    date_val = date_filter if date_filter != "All" else None
    shadow_data = load_shadow_positions(sym_val, date_val)

    if not shadow_data:
        st.info("No shadow positions match your filters.")
        st.stop()

    # Build DataFrame
    shadow_rows = []
    for sp in shadow_data:
        shadow_rows.append({
            "Scan Date": sp["scan_date"],
            "Symbol": sp["symbol"],
            "Company": sp.get("name", "-"),
            "Strike": sp.get("strike"),
            "Premium": sp.get("put_price"),
            "DTE": sp.get("dte"),
            "POP %": round(sp["pop"], 1) if sp.get("pop") is not None else None,
            "IVR %": round(sp["iv_rank"], 1) if sp.get("iv_rank") is not None else None,
            "Delta": round(sp["delta"], 4) if sp.get("delta") is not None else None,
            "Exp Date": sp.get("exp_date", "-"),
            "Underlying": sp.get("underlying_price"),
            "P50 %": round(sp["p50"], 1) if sp.get("p50") is not None else None,
        })

    shadow_df = pd.DataFrame(shadow_rows)

    col_hdr, col_exp = st.columns([3, 1])
    with col_hdr:
        st.subheader(f"Shadow Positions ({len(shadow_df)} rows)")
    with col_exp:
        st.download_button(
            label="Export CSV",
            data=shadow_df.to_csv(index=False),
            file_name=f"shadow_positions_{date.today()}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.dataframe(
        shadow_df,
        column_config={
            "Strike": st.column_config.NumberColumn("Strike", format="%.0f"),
            "Premium": st.column_config.NumberColumn("Premium", format="$%.2f"),
            "DTE": st.column_config.NumberColumn("DTE", format="%d"),
            "POP %": st.column_config.NumberColumn("POP %", format="%.1f"),
            "IVR %": st.column_config.NumberColumn("IVR %", format="%.1f"),
            "Delta": st.column_config.NumberColumn("Delta", format="%.4f"),
            "Underlying": st.column_config.NumberColumn("Underlying", format="$%.2f"),
            "P50 %": st.column_config.NumberColumn("P50 %", format="%.1f"),
        },
        use_container_width=True,
        hide_index=True,
        height=min(len(shadow_df) * 35 + 38, 800),
    )

    # Symbol summary
    st.divider()
    st.subheader("Summary by Symbol")
    if not shadow_df.empty:
        summary = shadow_df.groupby("Symbol").agg(
            Positions=("Symbol", "count"),
            Avg_Premium=("Premium", "mean"),
            Avg_POP=("POP %", "mean"),
            Avg_Delta=("Delta", "mean"),
            Scan_Dates=("Scan Date", "nunique"),
        ).round(2).reset_index()
        summary.columns = ["Symbol", "Positions", "Avg Premium", "Avg POP %", "Avg Delta", "Scan Dates"]
        st.dataframe(
            summary,
            column_config={
                "Avg Premium": st.column_config.NumberColumn("Avg Premium", format="$%.2f"),
                "Avg POP %": st.column_config.NumberColumn("Avg POP %", format="%.1f"),
                "Avg Delta": st.column_config.NumberColumn("Avg Delta", format="%.4f"),
            },
            use_container_width=True,
            hide_index=True,
        )


# --- Config Page ---
elif page == "Config":
    st.title("Scanner Configuration")
    st.caption(f"Today is {date.today().strftime('%A, %B %d, %Y')}")

    sb = get_supabase()
    config_result = sb.table("config").select("*").execute()

    if not config_result.data:
        st.warning("No config found in database.")
        st.stop()

    config = {row["key"]: row["value"] for row in config_result.data}

    st.subheader("Watchlist Symbols")
    symbols = json.loads(config.get("symbols", "[]"))
    symbols_text = st.text_area("Symbols (one per line)", value="\n".join(symbols), height=200)

    st.subheader("Entry Rules")
    col1, col2 = st.columns(2)
    with col1:
        delta_min = st.number_input("Delta Min", value=float(config.get("delta_min", -0.30)), step=0.01, format="%.2f")
        dte_min = st.number_input("DTE Min", value=int(float(config.get("dte_min", 30))), step=1)
    with col2:
        delta_max = st.number_input("Delta Max", value=float(config.get("delta_max", -0.15)), step=0.01, format="%.2f")
        dte_max = st.number_input("DTE Max", value=int(float(config.get("dte_max", 60))), step=1)

    if st.button("Save Configuration", type="primary"):
        new_symbols = [s.strip().upper() for s in symbols_text.strip().split("\n") if s.strip()]
        updates = {
            "symbols": json.dumps(new_symbols),
            "delta_min": str(delta_min),
            "delta_max": str(delta_max),
            "dte_min": str(int(dte_min)),
            "dte_max": str(int(dte_max)),
        }
        for key, value in updates.items():
            sb.table("config").update({"value": value, "updated_at": datetime.utcnow().isoformat()}).eq("key", key).execute()
        st.success(f"Config saved! {len(new_symbols)} symbols, Delta {delta_min} to {delta_max}, DTE {int(dte_min)}-{int(dte_max)}")
        st.cache_data.clear()


# --- Footer ---
st.sidebar.divider()
st.sidebar.caption(f"Stan's Options Dashboard v2.0 | TastyTrade: {get_tastytrade_mode().upper()}")
st.sidebar.caption(f"Data: {len(load_scan_dates())} scan dates")
