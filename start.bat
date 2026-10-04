@echo off
rem ============================================================
rem  autofw launcher v3 (ASCII-only, goto-structured, logged)
rem  fixes: codepage-safe ASCII, startup.log audit trail,
rem         port-8765 busy skip, retry-wait server check,
rem         friendly errors, pause on double-click
rem  2026-09-29: port checks now require LISTENING state - stale TIME_WAIT
rem          rows after a kill no longer misread as "port busy"/"server up"
rem  usage: double-click or run "start.bat"
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"

rem ---------- logging init ----------
set "LOGDIR=%~dp0logs"
set "LOG=%LOGDIR%\startup.log"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "TS=%date% %time%"
>>"%LOG%" echo ============================================================
>>"%LOG%" echo [%TS%] autofw launcher start (cwd: %~dp0)
>>"%LOG%" echo [%TS%] os: %OS% user: %USERNAME%

rem ---------- 1. python check ----------
echo [autofw] checking python...
if exist ".venv\Scripts\python.exe" goto :venv_ok
where python >nul 2>nul
if errorlevel 1 goto :err_python
>>"%LOG%" echo [%TS%] STAGE1 OK: python available
goto :venv_create

rem ---------- 1.5 install bundled example workflow (first run) ----------
:install_example
if exist "state\flows\com.android.settings\main.json" goto :example_done
if not exist "examples\com.android.settings__main.json" goto :example_done
echo [autofw] installing bundled example workflow...
if not exist "state\flows\com.android.settings" mkdir "state\flows\com.android.settings"
copy /y "examples\com.android.settings__main.json" "state\flows\com.android.settings\main.json" >nul
if exist "examples\com.android.settings__meta.json" copy /y "examples\com.android.settings__meta.json" "state\flows\com.android.settings\meta.json" >nul
>>"%LOG%" echo [%TS%] example workflow installed to state\flows\com.android.settings\
:example_done
goto :example_install_done

:err_python
>>"%LOG%" echo [%TS%] STAGE1 FAIL: python not found in PATH
echo [ERROR] python not found. Install Python 3.12+ and add it to PATH.
echo [ERROR] log: %LOG%
pause
exit /b 1

:venv_create
echo [autofw] first run: creating venv...
python -m virtualenv .venv >"%LOGDIR%\venv_create.log" 2>&1
set "VENV_RC=%errorlevel%"
>>"%LOG%" echo [%TS%] STAGE2 virtualenv rc=%VENV_RC%
if "%VENV_RC%"=="0" goto :pip_install
echo [WARN] virtualenv missing, trying python -m venv ...
python -m venv .venv >"%LOGDIR%\venv_create.log" 2>&1
set "VENV_RC=%errorlevel%"
>>"%LOG%" echo [%TS%] STAGE2 venv rc=%VENV_RC%
if not "%VENV_RC%"=="0" goto :err_venv
goto :pip_install

:err_venv
>>"%LOG%" echo [%TS%] STAGE2 FAIL: venv creation failed rc=%VENV_RC%
echo [ERROR] venv creation failed (rc %VENV_RC%)
echo [ERROR] details: %LOGDIR%\venv_create.log
echo [ERROR] hint: run "python -m ensurepip" or reinstall Python
pause
exit /b 1

:err_pip
>>"%LOG%" echo [%TS%] STAGE2 FAIL: pip install failed rc=%PIP_RC%
echo [ERROR] dependency install failed (rc %PIP_RC%)
echo [ERROR] details: %LOGDIR%\pip_install.log
echo [ERROR] hint: check network / proxy, or run manually:
echo [ERROR]   .venv\Scripts\python.exe -m pip install --no-user -r requirements.txt
pause
exit /b 1

:pip_install
echo [autofw] installing deps (first run, ~1-2 min)...
".venv\Scripts\pip.exe" install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple >"%LOGDIR%\pip_install.log" 2>&1
set "PIP_RC=%errorlevel%"
>>"%LOG%" echo [%TS%] STAGE2 pip rc=%PIP_RC%
if not "%PIP_RC%"=="0" goto :err_pip
:venv_ok

rem ---------- install example (idempotent, after python check) ----------
goto :install_example
:example_install_done

rem ---------- 3. check / start GUI server ----------
echo [autofw] checking panel service (port 8765)...
netstat -ano | findstr ":8765" | findstr /i "LISTENING" >nul 2>nul
if not errorlevel 1 goto :server_already
goto :start_server

:start_server
>>"%LOG%" echo [%TS%] STAGE3 port free, starting server...
start "autofw-gui" cmd /k ".venv\Scripts\python.exe gui\server.py"
set "TRY=1"
:wait_server
ping -n 2 127.0.0.1 >nul
netstat -ano | findstr ":8765" | findstr /i "LISTENING" >nul 2>nul
if not errorlevel 1 goto :server_ok
set /a TRY+=1
if %TRY% LEQ 5 goto :wait_server
>>"%LOG%" echo [%TS%] STAGE3 FAIL: port 8765 not listening after 10s
echo [ERROR] panel service failed to start (port 8765 not listening after 10s)
echo [ERROR] debug: check the autofw-gui window, or run manually:
echo [ERROR]   .venv\Scripts\python.exe gui\server.py
echo [ERROR] log: %LOG%
pause
exit /b 1

:server_ok
>>"%LOG%" echo [%TS%] STAGE3 OK: listening on 127.0.0.1:8765
goto :open_browser

:server_already
rem Port busy: probe if it is a REAL autofw panel before skipping the start.
rem (2026-09-09 fix: a foreign process on 8765 used to be mistaken for "panel running",
rem  so start.bat skipped launching and the browser opened a dead port.)
set "PROBE=NOHTTP"
for /f %%R in ('".venv\Scripts\python.exe" probe_panel.py 2^>nul') do set "PROBE=%%R"
if "%PROBE%"=="AUTOFW" (
    >>"%LOG%" echo [%TS%] STAGE3 SKIP: port 8765 busy - probe OK (real autofw panel)
    echo [autofw] panel already running on 8765 (verified), opening browser...
    goto :open_browser
)
>>"%LOG%" echo [%TS%] STAGE3 WARN: port 8765 busy by NON-panel process (probe=%PROBE%)
echo.
echo [WARN] port 8765 is occupied but it is NOT the autofw panel (probe=%PROBE%)
set "OCCUPY_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr /i LISTENING') do set "OCCUPY_PID=%%P"
if defined OCCUPY_PID (
    echo [WARN]   occupant PID: %OCCUPY_PID%
    >>"%LOG%" echo [%TS%] STAGE3 occupant PID=%OCCUPY_PID%
) else (
    echo [WARN]   occupant PID not resolved
)
echo [WARN] autofw cannot serve on 8765 while it is occupied.
where choice >nul 2>nul
if errorlevel 1 goto :occupied_abort
if not defined OCCUPY_PID goto :occupied_abort
echo.
choice /c YN /n /m "Kill the occupying process and start autofw panel? [Y=kill/start  N=exit] "
if errorlevel 2 goto :occupied_abort
taskkill /PID %OCCUPY_PID% /F >nul 2>&1
>>"%LOG%" echo [%TS%] STAGE3 killed occupant PID=%OCCUPY_PID% (user approved)
ping -n 3 127.0.0.1 >nul
goto :start_server

:occupied_abort
>>"%LOG%" echo [%TS%] STAGE3 ABORT: occupant not cleared (declined or unresolvable)
echo [autofw] aborted: close the occupying process, then run start.bat again.
echo %cmdcmdline% | findstr /i "cmd.exe /c" >nul
if not errorlevel 1 exit /b 1
pause
exit /b 1

:open_browser

rem ---------- 4. open browser ----------
start "" http://127.0.0.1:8765 >nul 2>nul
>>"%LOG%" echo [%TS%] STAGE4 browser requested: http://127.0.0.1:8765

rem ---------- 5. done ----------
echo.
echo [autofw] started. panel: http://127.0.0.1:8765
echo [autofw] startup log: %LOG%
echo [autofw] stop panel: run stop_gui.bat or close the autofw-gui window
set "TS=%date% %time%"
>>"%LOG%" echo [%TS%] LAUNCH OK (exit 0)

rem keep window on double-click
echo %cmdcmdline% | findstr /i "cmd.exe /c" >nul
if not errorlevel 1 (
    echo.
    echo press any key to exit...
    pause >nul
)
endlocal
