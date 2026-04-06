"""Backfill raw IV for historical scan_options by solving Black-Scholes.

Given put_price, underlying_price, strike, dte, risk_free_rate → solve for IV.
Uses scipy.optimize.brentq for root-finding.
"""
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client
import os
import math
from scipy.stats import norm
from scipy.optimize import brentq

sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))


def bs_put_price(S, K, T, r, sigma):
    """Black-Scholes put price."""
    if T <= 0:
        return max(K - S, 0)
    if sigma <= 0:
        # σ→0 limit = discounted intrinsic value
        return max(K * math.exp(-r * T) - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def implied_vol(market_price, S, K, T, r):
    """Solve for IV that makes BS put price equal market price."""
    if market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    # Intrinsic value check — if market price < intrinsic, no valid IV
    intrinsic = max(K * math.exp(-r * T) - S, 0)
    if market_price < intrinsic:
        return None
    try:
        # Search between 1% and 500% IV
        iv = brentq(lambda s: bs_put_price(S, K, T, r, s) - market_price, 0.01, 5.0, xtol=1e-5)
        return round(iv, 6)
    except (ValueError, RuntimeError):
        return None


# Get risk-free rate per scan date
scans = sb.table('daily_scans').select('id, scan_date, risk_free_rate').execute().data
rfr_by_scan = {s['id']: float(s['risk_free_rate'] or 0.0375) for s in scans}

# Find scan_options missing iv but having all needed inputs
# Paginate because Supabase default limit is 1000 rows
opts = []
page_size = 1000
offset = 0
while True:
    batch = sb.table('scan_options').select(
        'id, scan_id, strike, dte, put_price, underlying_price, iv'
    ).range(offset, offset + page_size - 1).execute().data
    if not batch:
        break
    opts.extend(batch)
    if len(batch) < page_size:
        break
    offset += page_size
print(f"Total scan_options: {len(opts)}")

to_solve = [o for o in opts if o.get('iv') is None
            and o.get('put_price') and o.get('underlying_price')
            and o.get('strike') and o.get('dte')]
print(f"Missing iv with solvable inputs: {len(to_solve)}")

updated = 0
failed = 0
for o in to_solve:
    S = float(o['underlying_price'])
    K = float(o['strike'])
    T = float(o['dte']) / 365.0
    r = rfr_by_scan.get(o['scan_id'], 0.0375)
    P = float(o['put_price'])

    iv = implied_vol(P, S, K, T, r)
    if iv is not None and 0.01 <= iv <= 5.0:
        sb.table('scan_options').update({'iv': iv}).eq('id', o['id']).execute()
        updated += 1
    else:
        failed += 1

print(f"Backfilled iv: {updated} solved, {failed} failed")
