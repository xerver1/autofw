@echo off
rem ============================================================
rem  autofw stopper v2 (ASCII-only, 2026-09-29)
rem  v1 bug: UTF-8 Chinese text was misparsed by cmd.exe under
rem  codepage 936, so the kill logic never executed.
rem  v2: ASCII-only + LISTENING-only port lookup +
rem      orphan "cmd /k gui\server.py" host-window sweep.
rem ============================================================
setlocal EnableExtensions

set "LOGDIR=%~dp0logs"
set "LOG=%LOGDIR%\startup.log"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

echo [autofw] stopping panel service (port 8765)...

set "KILLED="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr /i "LISTENING"') do (
    echo [autofw] killing PID %%P
    taskkill /F /T /PID %%P >nul 2>nul
    set "KILLED=1"
)

rem sweep orphan cmd /k host windows left behind by start.bat
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter 'Name=''cmd.exe''' | Where-Object { $_.CommandLine -like '*gui\server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul

ping -n 2 127.0.0.1 >nul
set "STILL="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr /i "LISTENING"') do set "STILL=%%P"

if defined STILL (
    echo [autofw] WARN: port 8765 still LISTENING on PID %STILL% - maybe not autofw?
    >>"%LOG%" echo [%date% %time%] stop_gui v2: WARN still-listening pid=%STILL%
    endlocal
    exit /b 1
)
if defined KILLED (
    echo [autofw] panel service stopped.
    >>"%LOG%" echo [%date% %time%] stop_gui v2: panel stopped
) else (
    echo [autofw] panel was not running - nothing on port 8765.
    >>"%LOG%" echo [%date% %time%] stop_gui v2: not running
)
endlocal
exit /b 0