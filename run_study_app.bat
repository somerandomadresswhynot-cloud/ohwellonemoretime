@echo off
setlocal

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

REM Prefer py launcher with 3.11, then 3.10.
where py >nul 2>&1
if not errorlevel 1 (
    py -3.11 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY_CMD=py -3.11"
    if "%PY_CMD%"=="" (
        py -3.10 -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PY_CMD=py -3.10"
    )
)

REM Fallback to python on PATH (must be >=3.10).
if "%PY_CMD%"=="" (
    where python >nul 2>&1
    if not errorlevel 1 set "PY_CMD=python"
)

if "%PY_CMD%"=="" (
    echo [ERROR] Python 3.10+ was not found.
    echo [HINT] Install Python 3.10+ and retry.
    set "EXITCODE=1"
    goto :finish
)

%PY_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Detected interpreter is below Python 3.10.
    %PY_CMD% -c "import sys; print('Detected Python:', sys.version)"
    echo [ERROR] This app requires Python 3.10+ per pyproject constraints.
    echo [HINT] Install Python 3.10+ and ensure either "py -3.10" or "python" resolves correctly.
    set "EXITCODE=1"
    goto :finish
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
    echo [INFO] Creating virtual environment using %PY_CMD% ...
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

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] The virtual environment is not using Python 3.10+.
    python -c "import sys; print('Venv Python:', sys.version)"
    echo [HINT] Delete .venv and rerun after installing Python 3.10+, or run: py -3.10 -m venv .venv
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
    if not errorlevel 1 (
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
