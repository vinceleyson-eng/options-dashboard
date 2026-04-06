"""Backfill April 1st scan data by interpolating March 31 and April 2."""
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
import os

sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))

# Check if April 1 scan already exists
existing = sb.table('daily_scans').select('id').eq('scan_date', '2026-04-01').execute().data
if existing:
    print("April 1st daily_scan already exists — aborting to avoid duplicates")
    exit(1)

# Get March 31 and April 2 scans
s31 = sb.table('daily_scans').select('*').eq('scan_date', '2026-03-31').execute().data[0]
s02 = sb.table('daily_scans').select('*').eq('scan_date', '2026-04-02').execute().data[0]

vix31 = float(s31['vix'])
vix02 = float(s02['vix'])
vix_apr1 = round((vix31 + vix02) / 2, 2)
rfr = s31['risk_free_rate']

print(f"Interpolated VIX: ({vix31} + {vix02}) / 2 = {vix_apr1}")

# Create daily_scan for April 1
scan_result = sb.table('daily_scans').insert({
    'scan_date': '2026-04-01',
    'vix': vix_apr1,
    'risk_free_rate': rfr,
}).execute()
scan_id = scan_result.data[0]['id']
print(f"Created daily_scan: 2026-04-01 (VIX: {vix_apr1})")

# Get scan_options for both dates
opts31 = sb.table('scan_options').select('*').eq('scan_id', s31['id']).execute().data
opts02 = sb.table('scan_options').select('*').eq('scan_id', s02['id']).execute().data

lookup31 = {(o['symbol'], str(o['strike']), o['exp_date']): o for o in opts31}
lookup02 = {(o['symbol'], str(o['strike']), o['exp_date']): o for o in opts02}
all_keys = set(lookup31.keys()) | set(lookup02.keys())


def avg(a, b):
    if a is not None and b is not None:
        return round((float(a) + float(b)) / 2, 4)
    return a if a is not None else b


def avg_int(a, b):
    if a is not None and b is not None:
        return round((int(a) + int(b)) / 2)
    return a if a is not None else b


option_rows = []
for key in sorted(all_keys):
    o31 = lookup31.get(key)
    o02 = lookup02.get(key)
    base = o31 or o02

    if o31 and o02:
        row = {
            'scan_id': scan_id,
            'symbol': base['symbol'],
            'name': base.get('name'),
            'iv_rank': avg(o31.get('iv_rank'), o02.get('iv_rank')),
            'iv': avg(o31.get('iv'), o02.get('iv')),
            'dte': avg_int(o31.get('dte'), o02.get('dte')),
            'delta': avg(o31.get('delta'), o02.get('delta')),
            'exp_date': base['exp_date'],
            'pop': avg(o31.get('pop'), o02.get('pop')),
            'p50': avg(o31.get('p50'), o02.get('p50')),
            'strike': base['strike'],
            'bid': avg(o31.get('bid'), o02.get('bid')),
            'ask': avg(o31.get('ask'), o02.get('ask')),
            'bid_ask_spread': avg(o31.get('bid_ask_spread'), o02.get('bid_ask_spread')),
            'put_price': avg(o31.get('put_price'), o02.get('put_price')),
            'earnings': base.get('earnings'),
            'underlying_price': avg(o31.get('underlying_price'), o02.get('underlying_price')),
            'selected': False,
        }
    else:
        src = o31 if o31 else o02
        dte_val = src.get('dte')
        if o31 and dte_val:
            dte_val = int(dte_val) - 1
        elif o02 and dte_val:
            dte_val = int(dte_val) + 1
        row = {
            'scan_id': scan_id,
            'symbol': src['symbol'],
            'name': src.get('name'),
            'iv_rank': src.get('iv_rank'),
            'iv': src.get('iv'),
            'dte': dte_val,
            'delta': src.get('delta'),
            'exp_date': src['exp_date'],
            'pop': src.get('pop'),
            'p50': src.get('p50'),
            'strike': src['strike'],
            'bid': src.get('bid'),
            'ask': src.get('ask'),
            'bid_ask_spread': src.get('bid_ask_spread'),
            'put_price': src.get('put_price'),
            'earnings': src.get('earnings'),
            'underlying_price': src.get('underlying_price'),
            'selected': False,
        }
    option_rows.append(row)

# Insert scan_options in batches
inserted_options = []
for i in range(0, len(option_rows), 50):
    batch = option_rows[i:i + 50]
    result = sb.table('scan_options').insert(batch).execute()
    inserted_options.extend(result.data)

print(f"Inserted {len(inserted_options)} interpolated scan_options")

# Create shadow positions
shadow_rows = []
for opt in inserted_options:
    shadow_rows.append({
        'scan_option_id': opt['id'],
        'scan_date': '2026-04-01',
        'symbol': opt['symbol'],
        'name': opt.get('name'),
        'strike': opt['strike'],
        'exp_date': opt.get('exp_date'),
        'put_price': opt.get('put_price'),
        'underlying_price': opt.get('underlying_price'),
        'delta': opt.get('delta'),
        'iv_rank': opt.get('iv_rank'),
        'pop': opt.get('pop'),
        'p50': opt.get('p50'),
        'dte': opt.get('dte'),
    })

for i in range(0, len(shadow_rows), 50):
    batch = shadow_rows[i:i + 50]
    sb.table('shadow_positions').insert(batch).execute()

print(f"Created {len(shadow_rows)} shadow positions")
print("Done!")
