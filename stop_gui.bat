@echo off
rem ============================================================
rem  autofw stopper v3 (ASCII-only, 2026-10-24)
rem  v1 bug: UTF-8 Chinese text was misparsed by cmd.exe under
rem  codepage 936, so the kill logic never executed.
rem  v2: ASCII-only + LISTENING-only port lookup +
rem      orphan "cmd /k gui\server.py" host-window sweep.
rem  v3 (2026-10-24 UX): verify the occupant is really the autofw
rem      panel (probe_panel.py, same check as start.bat) BEFORE
rem      killing - v2 killed any foreign process on 8765 silently.
rem      Non-autofw occupants now show their process name and need
rem      an explicit Y/N. Window pauses on double-click.
rem  note: linear structure on purpose - no labels inside for-blocks
rem      (batch blocks parse-time expansion pitfall).
rem ============================================================
setlocal EnableExtensions

set "LOGDIR=%~dp0logs"
set "LOG=%LOGDIR%\startup.log"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "TS=%date% %time%"

echo [autofw] stopping panel service (port 8765)...

set "PID8765="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr /i "LISTENING"') do set "PID8765=%%P"
if not defined PID8765 goto :not_running

rem ---- probe identity before killing (v3) ----
set "PROBE=NOHTTP"
for /f %%R in ('".venv\Scripts\python.exe" probe_panel.py 2^>nul') do set "PROBE=%%R"
if "%PROBE%"=="AUTOFW" goto :do_kill

echo [WARN] port 8765 is occupied by PID %PID8765% but it is NOT the autofw panel (probe=%PROBE%).
echo [WARN] occupant process:
tasklist /FI "PID eq %PID8765%"
where choice >nul 2>nul
if errorlevel 1 goto :abort_foreign
choice /c YN /n /m "Kill this process anyway? [Y=kill  N=leave it and exit] "
if errorlevel 2 goto :abort_foreign
>>"%LOG%" echo [%TS%] stop_gui v3: killing NON-autofw occupant PID %PID8765% (user approved)
goto :do_kill

:do_kill
echo [autofw] killing PID %PID8765%
taskkill /F /T /PID %PID8765% >nul 2>nul
set "KILLED=1"
goto :sweep

:not_running
echo [autofw] panel was not running - nothing on port 8765.
>>"%LOG%" echo [%TS%] stop_gui v3: not running
goto :sweep

:abort_foreign
echo [autofw] aborted: foreign process on 8765 left untouched.
>>"%LOG%" echo [%TS%] stop_gui v3: aborted, foreign occupant left untouched
goto :done

:sweep
rem sweep orphan cmd /k host windows left behind by start.bat
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter 'Name=''cmd.exe''' | Where-Object { $_.CommandLine -like '*gui\server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul

ping -n 2 127.0.0.1 >nul
set "STILL="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr /i "LISTENING"') do set "STILL=%%P"

if defined STILL (
    echo [autofw] WARN: port 8765 still LISTENING on PID %STILL% - maybe not autofw?
    >>"%LOG%" echo [%TS%] stop_gui v3: WARN still-listening pid=%STILL%
    goto :done
)
if defined KILLED (
    echo [autofw] panel service stopped.
    >>"%LOG%" echo [%TS%] stop_gui v3: panel stopped
) else (
    echo [autofw] panel was not running - nothing on port 8765.
    >>"%LOG%" echo [%TS%] stop_gui v3: not running
)

:done
rem v3: keep window open on double-click (same trick as start.bat)
echo %cmdcmdline% | findstr /i "cmd.exe /c" >nul
if not errorlevel 1 (
    echo.
    echo press any key to exit...
    pause >nul
)
endlocal
exit /b 0
