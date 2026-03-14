@echo off
setlocal enabledelayedexpansion

REM Reusable launcher for Study PDF Desktop MVP.
REM Works for first-time setup and future updates.
REM
REM Usage:
REM   run_study_app.bat             -> setup (if needed) + run app
REM   run_study_app.bat run         -> run app
REM   run_study_app.bat setup       -> create/update venv + install deps
REM   run_study_app.bat update      -> upgrade installer tooling + reinstall app
REM   run_study_app.bat resetdb     -> delete local SQLite db

cd /d "%~dp0"

set "COMMAND=%~1"
if "%COMMAND%"=="" set "COMMAND=run"

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
        exit /b 1
    )
)

if /i "%COMMAND%"=="resetdb" (
    if exist "study_app.db" (
        del /f /q "study_app.db"
        echo Deleted study_app.db
    ) else (
        echo No study_app.db found.
    )
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate .venv
    exit /b 1
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
    exit /b 1
)
echo [INFO] Setup complete.
exit /b 0

:update
echo [INFO] Upgrading packaging tooling and reinstalling project...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [ERROR] Tooling upgrade failed.
    exit /b 1
)
python -m pip install -e .
if errorlevel 1 (
    echo [ERROR] Reinstall failed.
    exit /b 1
)
echo [INFO] Update complete.
exit /b 0

:run
echo [INFO] Ensuring dependencies are installed...
python -m pip install -e .
if errorlevel 1 (
    echo [ERROR] pip install failed.
    exit /b 1
)

echo [INFO] Launching study app...
study-app
set "EXITCODE=%errorlevel%"
if not "%EXITCODE%"=="0" (
    echo [WARN] App exited with code %EXITCODE%.
)
exit /b %EXITCODE%
