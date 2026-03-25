@echo off
REM Daily Options Scanner - Runs at 10:00 PM GMT+8 (10:00 AM ET)
REM Scans TastyTrade API and pushes to Supabase
REM Healthchecks.io monitoring: https://hc-ping.com/052fc046-9af9-47aa-b04e-309917304c2b

REM Signal start to Healthchecks.io
curl -fsS -m 10 --retry 5 "https://hc-ping.com/052fc046-9af9-47aa-b04e-309917304c2b/start" > nul 2>&1

cd /d "G:\Other computers\My Laptop\Upwork\Stan\tasty-trade"
"C:\Users\acer\AppData\Local\Programs\Python\Python313\python.exe" daily_scan.py >> "%~dp0logs\scan_%date:~-4%%date:~4,2%%date:~7,2%.log" 2>&1

cd /d "G:\Other computers\My Laptop\Upwork\Stan\options-dashboard"
"C:\Users\acer\AppData\Local\Programs\Python\Python313\python.exe" push_to_supabase.py >> "%~dp0logs\scan_%date:~-4%%date:~4,2%%date:~7,2%.log" 2>&1

cd /d "G:\Other computers\My Laptop\Upwork\Stan\options-dashboard"
"C:\Users\acer\AppData\Local\Programs\Python\Python313\python.exe" position_tracker_daily.py >> "%~dp0logs\scan_%date:~-4%%date:~4,2%%date:~7,2%.log" 2>&1

echo [%date% %time%] Scan + tracker complete >> "%~dp0logs\scan_%date:~-4%%date:~4,2%%date:~7,2%.log"

REM Signal success to Healthchecks.io (sends exit code)
if %ERRORLEVEL% EQU 0 (
    curl -fsS -m 10 --retry 5 "https://hc-ping.com/052fc046-9af9-47aa-b04e-309917304c2b" > nul 2>&1
) else (
    curl -fsS -m 10 --retry 5 "https://hc-ping.com/052fc046-9af9-47aa-b04e-309917304c2b/fail" > nul 2>&1
)
