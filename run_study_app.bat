@echo off
setlocal enabledelayedexpansion

REM Reusable launcher for Study PDF Desktop MVP.
REM Works for first-time setup and future updates.
REM
REM Usage:
REM   run_study_app.bat             -> setup (if needed) + run app (auto-pause after exit)
REM   run_study_app.bat run         -> run app
REM   run_study_app.bat setup       -> create/update venv + install deps
REM   run_study_app.bat update      -> upgrade installer tooling + reinstall app
REM   run_study_app.bat resetdb     -> delete local SQLite db

cd /d "%~dp0"
set "EXITCODE=0"
set "COMMAND=%~1"
if "%COMMAND%"=="" set "COMMAND=run"

REM If user double-clicks (no args), pause at end so output remains visible.
set "PAUSE_AT_END=0"
if "%~1"=="" set "PAUSE_AT_END=1"

REM Optional overrides:
REM   set KEEP_OPEN_ON_ERROR=1  (pause on error)
REM   set KEEP_OPEN_ALWAYS=1    (pause always)

set "PY_CMD="
where py >nul 2>&1
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set "PY_CMD=python"
    ) else (
        echo [ERROR] Python 3.11+ was not found. Install Python and try again.
        set "EXITCODE=1"
        goto :finish
    )
)

if /i "%COMMAND%"=="resetdb" (
    if exist "study_app.db" (
        del /f /q "study_app.db"
        if errorlevel 1 (
            echo [ERROR] Failed to delete study_app.db
            set "EXITCODE=1"
            goto :finish
        )
        echo Deleted study_app.db
    ) else (
        echo No study_app.db found.
    )
    goto :finish
)

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv
        set "EXITCODE=1"
        goto :finish
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate .venv
    set "EXITCODE=1"
    goto :finish
)

if /i "%COMMAND%"=="setup" goto :install
if /i "%COMMAND%"=="update" goto :update
if /i "%COMMAND%"=="run" goto :run

echo [WARN] Unknown command "%COMMAND%". Falling back to run.
goto :run

:install
echo [INFO] Installing project dependencies...
python -m pip install -e .
if errorlevel 1 (
    echo [ERROR] pip install failed.
    set "EXITCODE=1"
    goto :finish
)
echo [INFO] Setup complete.
goto :finish

:update
echo [INFO] Upgrading packaging tooling and reinstalling project...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [ERROR] Tooling upgrade failed.
    set "EXITCODE=1"
    goto :finish
)
python -m pip install -e .
if errorlevel 1 (
    echo [ERROR] Reinstall failed.
    set "EXITCODE=1"
    goto :finish
)
echo [INFO] Update complete.
goto :finish

:run
echo [INFO] Ensuring dependencies are installed...
python -m pip install -e .
if errorlevel 1 (
    echo [ERROR] pip install failed.
    set "EXITCODE=1"
    goto :finish
)

echo [INFO] Launching study app...
python -m study_app.main
set "EXITCODE=%errorlevel%"

if not "%EXITCODE%"=="0" (
    echo [WARN] python -m study_app.main failed with %EXITCODE%. Trying study-app launcher...
    where study-app >nul 2>&1
    if %errorlevel%==0 (
        study-app
        set "EXITCODE=%errorlevel%"
    )
)

if not "%EXITCODE%"=="0" (
    echo [ERROR] App failed to launch. Exit code: %EXITCODE%
)

goto :finish

:finish
if /i "%KEEP_OPEN_ALWAYS%"=="1" set "PAUSE_AT_END=1"
if not "%EXITCODE%"=="0" (
    if /i "%KEEP_OPEN_ON_ERROR%"=="1" set "PAUSE_AT_END=1"
)

if "%PAUSE_AT_END%"=="1" (
    echo.
    if "%EXITCODE%"=="0" (
        echo [INFO] Completed. Press any key to close . . .
    ) else (
        echo [INFO] Exited with error %EXITCODE%. Press any key to close . . .
    )
    pause >nul
)

exit /b %EXITCODE%
