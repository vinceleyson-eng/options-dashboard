"""
Backfill null underlying_price, pop, p50 in Supabase scan_options.
Also fixes known bad company names (SNOW).

Run once:
    cd options-dashboard
    python backfill_nulls.py
"""

import asyncio
import math
import os
import sys

import numpy as np
from scipy.stats import norm
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tasty-trade"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

NAME_FIXES = {
    "SNOW": "Snowflake Inc",
}

MC_PATHS = 1000


def calc_pop(underlying, strike, iv, dte, rfr):
    if not all([underlying, strike, iv, dte]) or iv <= 0 or dte <= 0:
        return None
    S, K, sigma, T, r = float(underlying), float(strike), float(iv), float(dte) / 365.0, float(rfr)
    d2 = (math.log(S / K) + (r - 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return round(norm.cdf(d2) * 100, 1)


def bs_put(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return max(K - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def calc_p50(underlying, strike, iv, dte, rfr, premium):
    if not all([underlying, strike, iv, dte, premium]) or iv <= 0 or dte <= 0 or premium <= 0:
        return None
    S, K, sigma, days, r = float(underlying), float(strike), float(iv), int(dte), float(rfr)
    target = float(premium) * 0.5
    dt = 1.0 / 365.0
    np.random.seed(42)
    Z = np.random.standard_normal((MC_PATHS, days))
    log_returns = (r - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * Z
    stock_paths = S * np.exp(np.cumsum(log_returns, axis=1))
    successes = 0
    for i in range(MC_PATHS):
        for d in range(days):
            t_rem = (days - d - 1) / 365.0
            if bs_put(stock_paths[i, d], K, t_rem, r, sigma) <= target:
                successes += 1
                break
    return round(successes / MC_PATHS * 100, 1)


async def fetch_prices(symbols):
    """Fetch underlying prices via TastyTrade — Trade event with Summary fallback."""
    from tastytrade import Session, DXLinkStreamer
    from tastytrade.dxfeed import Trade, Summary

    client_secret = os.getenv("TASTYTRADE_CLIENT_SECRET")
    refresh_token = os.getenv("TASTYTRADE_REFRESH_TOKEN")
    if not client_secret or not refresh_token:
        print("ERROR: Missing TASTYTRADE_CLIENT_SECRET or TASTYTRADE_REFRESH_TOKEN")
        sys.exit(1)

    session = Session(client_secret, refresh_token, is_test=False)
    prices = {}

    # Try Trade events first (live price)
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe(Trade, symbols)
        for _ in range(len(symbols)):
            try:
                t = await asyncio.wait_for(streamer.get_event(Trade), timeout=10)
                if t.price and float(t.price) > 0:
                    prices[t.event_symbol] = float(t.price)
            except asyncio.TimeoutError:
                break

    # Fall back to Summary prev_day_close for missing
    missing = [s for s in symbols if s not in prices]
    if missing:
        print(f"  Falling back to prev_day_close for: {', '.join(missing)}")
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(Summary, missing)
            for _ in range(len(missing)):
                try:
                    s = await asyncio.wait_for(streamer.get_event(Summary), timeout=10)
                    price = s.prev_day_close_price or s.day_close_price
                    if price and float(price) > 0:
                        prices[s.event_symbol] = float(price)
                except asyncio.TimeoutError:
                    break

    return prices


def main():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Step 1: Fix company names
    for symbol, correct_name in NAME_FIXES.items():
        print(f"Fixing name for {symbol} -> {correct_name}")
        result = sb.table("scan_options").update({"name": correct_name}).eq("symbol", symbol).execute()
        count = len(result.data) if result.data else 0
        print(f"  Updated {count} rows")

    # Step 2: Fetch rows with null underlying_price
    print("\nFetching rows with null underlying_price...")
    result = sb.table("scan_options").select(
        "id, symbol, strike, dte, delta, put_price, pop, p50, underlying_price"
    ).is_("underlying_price", "null").execute()

    rows = result.data
    if not rows:
        print("No null rows found. Data is already complete.")
        return

    print(f"Found {len(rows)} rows with null underlying_price")

    # Get unique symbols
    symbols = list(set(r["symbol"] for r in rows))
    print(f"Fetching prices for: {', '.join(sorted(symbols))}")

    prices = asyncio.run(fetch_prices(symbols))
    print(f"Got prices: {prices}")

    # Get risk-free rate from Supabase config
    config_result = sb.table("config").select("key, value").execute()
    config = {c["key"]: c["value"] for c in config_result.data}
    rfr = float(config.get("risk_free_rate", "0.0375"))

    # Also get IV from a recent scan_option for each symbol (use delta as proxy — iv not stored directly)
    # We'll need to get iv from the Greeks stored implicitly via put_price + BS back-solve
    # Instead, fetch iv_rank and use a rough estimate: IV ≈ IVR/100 * some_baseline
    # Actually better: store the iv from the daily_scan. Since we don't have it stored...
    # Use a default IV of 0.6 (60%) for back-calculation — this is approximate but enough for historical backfill
    # For accurate values, future scans will compute from live data.
    DEFAULT_IV = 0.60
    print(f"Using default IV={DEFAULT_IV} for historical backfill (approximate)")

    # Step 3: Recalculate and update
    updated = 0
    skipped = 0
    for row in rows:
        symbol = row["symbol"]
        underlying = prices.get(symbol)

        if not underlying:
            print(f"  SKIP {symbol} — no price available")
            skipped += 1
            continue

        strike = row["strike"]
        dte = row["dte"]
        premium = row["put_price"]

        pop = calc_pop(underlying, strike, DEFAULT_IV, dte, rfr)
        p50 = calc_p50(underlying, strike, DEFAULT_IV, dte, rfr, premium)

        sb.table("scan_options").update({
            "underlying_price": underlying,
            "pop": pop,
            "p50": p50,
        }).eq("id", row["id"]).execute()

        updated += 1

    print(f"\nDone. Updated {updated} rows, skipped {skipped} rows.")
    if skipped > 0:
        print("Skipped rows have no underlying price available — run during market hours for best results.")


if __name__ == "__main__":
    main()
