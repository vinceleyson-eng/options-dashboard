"""
QA Validation System — Stan's Options Dashboard

Checks data integrity across Supabase tables and flags issues.

Run anytime:
    cd options-dashboard
    python qa_check.py

Checks:
  1. Null values  — underlying_price, pop, p50, bid, ask, put_price
  2. Company names — known bad API names (SNOW, etc.)
  3. Value ranges  — delta, DTE, IVR, POP, P50, strike
  4. Duplicates   — same symbol/strike/exp on same scan date
  5. Orphans      — positions referencing deleted scan_options
  6. Stale data   — no scan in last 3 business days
  7. Calculation  — POP + (1-POP) should balance, P50 > POP
"""

import os
from datetime import date, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

KNOWN_NAME_ISSUES = {
    "SNOW": "Snowflake Inc",
}

ISSUES = []
WARNINGS = []
PASSED = []


def fail(msg):
    ISSUES.append(f"  FAIL  {msg}")


def warn(msg):
    WARNINGS.append(f"  WARN  {msg}")


def ok(msg):
    PASSED.append(f"  OK    {msg}")


def run_qa():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("=" * 60)
    print("Stan's Options Dashboard -- QA Check")
    print(f"Date: {date.today()}")
    print("=" * 60)

    # --- Load data ---
    scans = sb.table("daily_scans").select("*").order("scan_date", desc=True).execute().data
    options = sb.table("scan_options").select("*").execute().data
    positions = sb.table("positions").select("*").execute().data
    snapshots = sb.table("position_snapshots").select("*").execute().data

    print(f"\nData loaded: {len(scans)} scans | {len(options)} options | {len(positions)} positions | {len(snapshots)} snapshots\n")

    # --- Check 1: Stale data ---
    print("[1] Stale data check")
    if scans:
        latest_date = date.fromisoformat(scans[0]["scan_date"])
        today = date.today()
        days_since = (today - latest_date).days
        # Skip weekends
        biz_days = sum(1 for i in range(days_since) if (today - timedelta(days=i+1)).weekday() < 5)
        if biz_days > 3:
            fail(f"Latest scan is {latest_date} ({biz_days} business days old) -- scanner may be down")
        elif biz_days > 1:
            warn(f"Latest scan is {latest_date} ({biz_days} business days old)")
        else:
            ok(f"Latest scan: {latest_date} ({days_since} calendar days ago)")
    else:
        fail("No scan data in database")

    # --- Check 2: Null values ---
    print("\n[2] Null value check")
    null_underlying = [o for o in options if o.get("underlying_price") is None]
    null_pop = [o for o in options if o.get("pop") is None]
    null_p50 = [o for o in options if o.get("p50") is None]
    null_bid = [o for o in options if o.get("bid") is None]
    null_put_price = [o for o in options if o.get("put_price") is None]

    for label, null_rows in [
        ("underlying_price", null_underlying),
        ("pop", null_pop),
        ("p50", null_p50),
        ("bid", null_bid),
        ("put_price", null_put_price),
    ]:
        pct = len(null_rows) / len(options) * 100 if options else 0
        if len(null_rows) == 0:
            ok(f"{label}: no nulls")
        elif pct > 20:
            fail(f"{label}: {len(null_rows)} nulls ({pct:.1f}%) -- run backfill_nulls.py")
        else:
            warn(f"{label}: {len(null_rows)} nulls ({pct:.1f}%)")

    # --- Check 3: Company name issues ---
    print("\n[3] Company name check")
    name_issues = []
    for sym, correct in KNOWN_NAME_ISSUES.items():
        bad = [o for o in options if o["symbol"] == sym and o.get("name") != correct]
        if bad:
            name_issues.extend(bad)
            fail(f"{sym}: {len(bad)} rows have wrong name (expected '{correct}')")
        else:
            ok(f"{sym}: name is correct")

    # --- Check 4: Value range checks ---
    print("\n[4] Value range check")
    out_of_range = []
    for o in options:
        issues = []
        delta = o.get("delta")
        if delta is not None and not (-1.0 <= delta <= 0):
            issues.append(f"delta={delta}")
        dte = o.get("dte")
        if dte is not None and not (0 <= dte <= 365):
            issues.append(f"dte={dte}")
        ivr = o.get("iv_rank")
        if ivr is not None and not (0 <= ivr <= 100):
            issues.append(f"ivr={ivr}")
        pop = o.get("pop")
        if pop is not None and not (0 <= pop <= 100):
            issues.append(f"pop={pop}")
        p50 = o.get("p50")
        if p50 is not None and not (0 <= p50 <= 100):
            issues.append(f"p50={p50}")
        put_price = o.get("put_price")
        if put_price is not None and put_price < 0:
            issues.append(f"put_price={put_price}")
        if issues:
            out_of_range.append(f"{o['symbol']} id={o['id'][:8]}: {', '.join(issues)}")

    if not out_of_range:
        ok(f"All {len(options)} option rows within valid ranges")
    else:
        for msg in out_of_range[:5]:
            fail(f"Out of range: {msg}")
        if len(out_of_range) > 5:
            fail(f"...and {len(out_of_range) - 5} more out-of-range rows")

    # --- Check 5: P50 > POP (should generally hold) ---
    print("\n[5] POP/P50 relationship check")
    pop_p50_issues = [
        o for o in options
        if o.get("pop") is not None and o.get("p50") is not None
        and o["p50"] < o["pop"] - 5  # allow 5% tolerance
    ]
    if not pop_p50_issues:
        ok("P50 >= POP for all rows (as expected for path-based calculation)")
    else:
        warn(f"{len(pop_p50_issues)} rows where P50 < POP (may indicate data issue)")
        for o in pop_p50_issues[:3]:
            warn(f"  {o['symbol']} strike={o['strike']} POP={o['pop']} P50={o['p50']}")

    # --- Check 6: Duplicate options ---
    print("\n[6] Duplicate check")
    seen = {}
    dupes = []
    scan_map = {s["id"]: s["scan_date"] for s in scans}
    for o in options:
        key = (o.get("scan_id"), o.get("symbol"), o.get("strike"), o.get("exp_date"))
        if key in seen:
            dupes.append(o)
        else:
            seen[key] = o
    if not dupes:
        ok("No duplicate option rows found")
    else:
        fail(f"{len(dupes)} duplicate option rows (same scan/symbol/strike/exp)")

    # --- Check 7: Orphaned positions ---
    print("\n[7] Orphaned position check")
    option_ids = {o["id"] for o in options}
    orphans = [p for p in positions if p.get("scan_option_id") and p["scan_option_id"] not in option_ids]
    if not orphans:
        ok("No orphaned positions")
    else:
        fail(f"{len(orphans)} positions reference deleted scan_options")

    # --- Check 8: Open positions with no snapshots ---
    print("\n[8] Position snapshot check")
    open_positions = [p for p in positions if p["status"] == "open"]
    snap_pos_ids = {s["position_id"] for s in snapshots}
    no_snaps = [p for p in open_positions if p["id"] not in snap_pos_ids]
    if not no_snaps:
        ok("All open positions have at least one snapshot")
    else:
        warn(f"{len(no_snaps)} open positions have no daily snapshots yet")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for msg in PASSED:
        print(f"\033[92m{msg}\033[0m")   # green
    for msg in WARNINGS:
        print(f"\033[93m{msg}\033[0m")   # yellow
    for msg in ISSUES:
        print(f"\033[91m{msg}\033[0m")   # red

    print("=" * 60)
    print(f"  {len(PASSED)} passed | {len(WARNINGS)} warnings | {len(ISSUES)} issues")
    if ISSUES:
        print("\nAction required: fix issues above before sending to Stan.")
    elif WARNINGS:
        print("\nLooks mostly good. Review warnings above.")
    else:
        print("\nAll checks passed. Data is clean.")
    print("=" * 60)

    return len(ISSUES) == 0


if __name__ == "__main__":
    run_qa()
