@echo off
REM Daily Options Scanner - Runs at 10:00 PM GMT+8 (10:00 AM ET)
REM Scans TastyTrade API and pushes to Supabase

cd /d "G:\Other computers\My Laptop\Upwork\Stan\tasty-trade"
python daily_scan.py >> "%~dp0logs\scan_%date:~-4%%date:~4,2%%date:~7,2%.log" 2>&1

cd /d "G:\Other computers\My Laptop\Upwork\Stan\options-dashboard"
python push_to_supabase.py >> "%~dp0logs\scan_%date:~-4%%date:~4,2%%date:~7,2%.log" 2>&1

cd /d "G:\Other computers\My Laptop\Upwork\Stan\options-dashboard"
python position_tracker_daily.py >> "%~dp0logs\scan_%date:~-4%%date:~4,2%%date:~7,2%.log" 2>&1

echo [%date% %time%] Scan + tracker complete >> "%~dp0logs\scan_%date:~-4%%date:~4,2%%date:~7,2%.log"
