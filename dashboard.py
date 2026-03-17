"""
Stan's Options Trading Dashboard — Streamlit App

Views:
1. Daily Option Research — browse daily scan reports, select options via checkbox
2. Open Positions — view all open positions with daily P&L tracking

Run: streamlit run dashboard.py
"""

import os
import json
import asyncio
from decimal import Decimal
from datetime import date, datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()


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
    """Fetch account details from TastyTrade API."""
    try:
        from tastytrade import Account

        session, error = get_tastytrade_session()
        if error:
            return {"error": error}

        mode = get_tastytrade_mode()

        async def _fetch():
            accounts = await asyncio.wait_for(Account.get(session), timeout=10)
            if not accounts:
                return {"mode": mode, "accounts": []}

            account_list = []
            for acc in accounts:
                balances = await asyncio.wait_for(acc.get_balances(session), timeout=10)
                account_list.append({
                    "account_number": acc.account_number,
                    "cash_balance": float(balances.cash_balance or 0),
                    "net_liquidating_value": float(balances.net_liquidating_value or 0),
                    "equity_buying_power": float(balances.equity_buying_power or 0),
                })
            return {"mode": mode, "accounts": account_list}

        return asyncio.run(_fetch())
    except Exception as e:
        return {"error": str(e)}


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
        accounts = await asyncio.wait_for(Account.get(session), timeout=15)
        if not accounts:
            return {"error": "No trading accounts found"}
        acc = accounts[0]
        result = await asyncio.wait_for(acc.place_order(session, order, dry_run=dry_run), timeout=15)
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

# --- CSS Enhancements ---
st.markdown("""
<style>
    /* Typography */
    h1 { font-size: 1.8rem !important; }
    h2 { font-size: 1.3rem !important; }
    h3 { font-size: 1.1rem !important; }

    /* Metric cards */
    [data-testid="stMetric"] {
        border-radius: 8px;
        padding: 12px 16px;
        border: 1px solid rgba(128, 128, 128, 0.15);
        background: var(--secondary-background-color);
    }
    [data-testid="stMetricValue"] { font-size: 1.4rem !important; }
    [data-testid="stMetricLabel"] { font-size: 0.8rem !important; opacity: 0.7; }

    /* Data tables */
    .stDataFrame, [data-testid="stDataFrame"] {
        border-radius: 8px;
        overflow: hidden;
        border: 1px solid rgba(128, 128, 128, 0.15);
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        border-right: 1px solid rgba(128, 128, 128, 0.15);
    }
    [data-testid="stSidebar"] .stMarkdown p {
        color: inherit;
    }

    /* Buttons */
    .stButton > button {
        border-radius: 6px;
        font-weight: 500;
    }

    /* Expanders */
    [data-testid="stExpander"] {
        border-radius: 8px;
        border: 1px solid rgba(128, 128, 128, 0.15);
    }

    /* Dividers */
    hr { opacity: 0.2; }

    /* Caption */
    .stCaption { opacity: 0.6; font-size: 0.8rem; }
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


def close_position(position_id):
    sb = get_supabase()
    sb.table("positions").update({
        "status": "closed",
        "closed_at": datetime.utcnow().isoformat(),
    }).eq("id", position_id).execute()


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

    # Step 1: Dry-run validation
    if "dry_run_result" not in st.session_state:
        st.session_state.dry_run_result = None

    if st.button("Validate Order (Dry Run)", type="secondary", use_container_width=True):
        with st.spinner("Validating order with TastyTrade..."):
            result = place_trade_on_tastytrade(option, quantity=1, dry_run=True)
            st.session_state.dry_run_result = result

    # Show dry-run results
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

            # Step 2: Confirm & place real order
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
                        if option.get("id"):
                            toggle_selection(option["id"], True)
                        create_position(option, order_id=order_id, order_status=order_status)

                        st.session_state.dry_run_result = None
                        st.cache_data.clear()
                        st.success(
                            f"Order placed! {option['symbol']} {option['strike']:.0f} Put | "
                            f"Order #{order_id} | Status: {order_status}"
                        )
                        st.balloons()

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
    st.sidebar.error(f"TastyTrade: {tt_data['error']}")
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

    if tt_data["accounts"]:
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
    ["Daily Research", "Open Positions", "Position History", "Config"],
    index=0,
)

# --- Daily Research Page ---
if page == "Daily Research":
    st.title("Daily Option Research")
    st.caption(f"Today is {date.today().strftime('%A, %B %d, %Y')}")

    scan_dates = load_scan_dates()

    if not scan_dates:
        st.warning("No scan data available yet. Run the daily scanner first.")
        st.stop()

    # Date selector — calendar picker
    date_options = {s["scan_date"]: s for s in scan_dates}
    available_dates = [date.fromisoformat(d) for d in date_options.keys()]
    min_date = min(available_dates)
    max_date = max(available_dates)

    picked_date = st.sidebar.date_input(
        "Select Scan Date",
        value=max_date,
        min_value=min_date,
        max_value=max_date,
        format="YYYY-MM-DD",
    )

    # Snap to nearest available scan date
    picked_str = picked_date.isoformat()
    if picked_str in date_options:
        selected_date = picked_str
    else:
        # Find closest available date
        closest = min(available_dates, key=lambda d: abs((d - picked_date).days))
        selected_date = closest.isoformat()
        st.sidebar.caption(f"No scan on {picked_str}. Showing nearest: **{selected_date}**")

    scan = date_options[selected_date]
    options = load_scan_options(scan["id"])

    # Header metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Report Date", selected_date)
    with col2:
        st.metric("VIX", f"{scan['vix']:.2f}" if scan["vix"] else "N/A")
    with col3:
        st.metric("Risk-Free Rate", f"{scan['risk_free_rate']:.4f}" if scan["risk_free_rate"] else "N/A")
    with col4:
        st.metric("Options Found", len(options))

    st.divider()

    if not options:
        st.info("No options data for this date.")
        st.stop()

    # Filter controls
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        symbols = sorted(set(o["symbol"] for o in options))
        selected_symbols = st.multiselect("Filter by Symbol", symbols, default=symbols)
    with col_f2:
        show_selected_only = st.checkbox("Show selected only", value=False)
    with col_f3:
        sort_by = st.selectbox("Sort by", ["Symbol", "IVR %", "POP %", "P50 %", "Delta", "DTE"], index=0)

    # Filter
    filtered = [o for o in options if o["symbol"] in selected_symbols]
    if show_selected_only:
        filtered = [o for o in filtered if o.get("selected")]

    # Get existing positions for checkbox state
    existing_positions = load_all_positions()
    existing_option_ids = {p["scan_option_id"] for p in existing_positions if p.get("scan_option_id")}

    # Build DataFrame
    df = build_options_dataframe(filtered, existing_option_ids)

    if df.empty:
        st.info("No options match your filters.")
        st.stop()

    # Sort
    sort_col_map = {
        "Symbol": "Symbol",
        "IVR %": "IVR %",
        "POP %": "POP %",
        "P50 %": "P50 %",
        "Delta": "Delta",
        "DTE": "DTE",
    }
    sort_col = sort_col_map.get(sort_by, "Symbol")
    ascending = sort_by in ["Symbol", "Delta", "DTE"]
    df = df.sort_values(by=sort_col, ascending=ascending, na_position="last").reset_index(drop=True)

    st.subheader(f"Options — {selected_date} ({len(df)} rows)")

    # Display columns (hide internal columns)
    display_cols = ["Select", "Symbol", "Company", "IVR %", "DTE", "Delta", "Exp Date",
                    "POP %", "P50 %", "Strike", "Bid", "Ask", "Spread", "Put Price",
                    "Underlying", "Earnings"]

    # Column config for st.data_editor
    column_config = {
        "Select": st.column_config.CheckboxColumn(
            "Select",
            help="Tick to open trade confirmation",
            width="small",
        ),
        "Symbol": st.column_config.TextColumn("Symbol", width="small"),
        "Company": st.column_config.TextColumn("Company", width="large"),
        "IVR %": st.column_config.NumberColumn("IVR %", format="%.1f%%", width="small"),
        "DTE": st.column_config.NumberColumn("DTE", format="%d days", width="small"),
        "Delta": st.column_config.NumberColumn("Delta", format="%.4f", width="small"),
        "Exp Date": st.column_config.TextColumn("Exp Date", width="medium"),
        "POP %": st.column_config.NumberColumn("POP %", format="%.1f%%", width="small"),
        "P50 %": st.column_config.NumberColumn("P50 %", format="%.1f%%", width="small"),
        "Strike": st.column_config.NumberColumn("Strike", format="$%.0f", width="small"),
        "Bid": st.column_config.NumberColumn("Bid", format="$%.2f", width="small"),
        "Ask": st.column_config.NumberColumn("Ask", format="$%.2f", width="small"),
        "Spread": st.column_config.NumberColumn("Spread", format="$%.2f", width="small"),
        "Put Price": st.column_config.NumberColumn("Put Price", format="$%.2f", width="small"),
        "Underlying": st.column_config.NumberColumn("Underlying $", format="$%.2f", width="small"),
        "Earnings": st.column_config.TextColumn("Earnings", width="small"),
    }

    # Editable table
    edited_df = st.data_editor(
        df[display_cols],
        column_config=column_config,
        width="stretch",
        hide_index=True,
        height=min(len(df) * 36 + 40, 700),
        disabled=[c for c in display_cols if c != "Select"],
        key="options_table",
    )

    # Detect checkbox changes — open trade confirmation dialog
    if edited_df is not None:
        for idx in range(len(edited_df)):
            new_selected = edited_df.iloc[idx]["Select"]
            old_selected = df.iloc[idx]["Select"]
            option_id = df.iloc[idx]["_id"]
            has_position = df.iloc[idx]["_has_position"]

            if new_selected and not old_selected and not has_position:
                # Find the original option data
                opt = next((o for o in filtered if o["id"] == option_id), None)
                if opt:
                    # Clear any previous dry-run state
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
    col1, col2 = st.columns(2)
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
    st.dataframe(
        pos_df,
        column_config={
            "Strike": st.column_config.NumberColumn("Strike", format="%.0f"),
            "Price Paid": st.column_config.NumberColumn("Price Paid", format="$%.2f"),
        },
        width="stretch",
        hide_index=True,
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
        st.dataframe(
            hist_df,
            column_config={
                "Strike": st.column_config.NumberColumn("Strike", format="%.0f"),
                "Price Paid": st.column_config.NumberColumn("Price Paid", format="$%.2f"),
            },
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No positions match your filter.")


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
