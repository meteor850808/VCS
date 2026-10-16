@echo off
title VCS v2.23.0 Restart
color 0A

set PORT=45679
set PROJECT_DIR=%~dp0
set VENV_DIR=%PROJECT_DIR%.venv
set PYTHON=%VENV_DIR%\Scripts\python.exe
set PIP=%VENV_DIR%\Scripts\pip.exe
set UVICORN=%VENV_DIR%\Scripts\uvicorn.exe
set CELERY=%VENV_DIR%\Scripts\celery.exe
set ALEMBIC=%VENV_DIR%\Scripts\alembic.exe

set OLDPORT1=45678
set OLDPORT2=45679
set OLDPORT3=45680

echo.
echo ========================================
echo   VCS v2.23.0 Server Restart
echo   Code: %PROJECT_DIR%app
echo   Venv: %VENV_DIR%
echo ========================================
echo.

:: Step 1: Check Python venv
echo [1/7] Checking Python venv...
if not exist "%PYTHON%" (
    echo       Creating venv...
    where python >nul 2>&1
    if %errorlevel% neq 0 (
        echo ERROR: Python not found
        goto :fail
    )
    python -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create venv
        goto :fail
    )
    echo       OK - venv created
) else (
    echo       OK - venv exists
)

:: Step 2: Install dependencies
echo [2/7] Checking dependencies...
"%PYTHON%" -c "import fastapi" >nul 2>&1
if %errorlevel% neq 0 (
    echo       Installing dependencies...
    "%PIP%" install -r "%PROJECT_DIR%requirements.txt" -q
    if %errorlevel% neq 0 (
        echo ERROR: Failed to install deps
        goto :fail
    )
    echo       OK - installed
) else (
    echo       OK - ready
)

:: Step 3: Kill ALL VCS server processes
:: 1) Match by command line (catches uvicorn reloader; /t also kills spawn_main server child)
:: 2) Also kill Celery worker processes
:: 3) Fallback: kill anything still LISTENING on our ports (LISTENING only; TIME_WAIT is harmless)
echo [3/7] Stopping old processes on port %OLDPORT1%, %OLDPORT2%, %OLDPORT3%...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app.main:app*' -or $_.CommandLine -like '*celery*worker*' } | ForEach-Object { & taskkill.exe /f /t /pid $_.ProcessId 2>&1 | Out-Null }"
ping -n 4 127.0.0.1 >nul
for %%p in (%OLDPORT1% %OLDPORT2% %OLDPORT3%) do (
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr LISTENING ^| findstr ":%%p"') do (
        taskkill /f /t /pid %%a >nul 2>&1
    )
)
echo       Done

:: Verify port is free (LISTENING only; TIME_WAIT is harmless)
netstat -ano 2>nul | findstr LISTENING | findstr ":%PORT%" >nul 2>&1
if %errorlevel% equ 0 (
    echo       WARN: Port %PORT% still in use, trying alternate...
    :: 45680 为历史错误端口（v2.5 已回退 45679），备用一律用 45681
    set PORT=45681
)

:: Step 4: Create data dirs
echo [4/7] Checking data directories...
if not exist "%PROJECT_DIR%data\uploads" mkdir "%PROJECT_DIR%data\uploads"
if not exist "%PROJECT_DIR%data\outputs" mkdir "%PROJECT_DIR%data\outputs"
if not exist "%PROJECT_DIR%data\quality_reports" mkdir "%PROJECT_DIR%data\quality_reports"
echo       OK

:: Step 5: Database migration
echo [5/7] Running database migration...
cd /d "%PROJECT_DIR%"
"%ALEMBIC%" upgrade head >nul 2>&1
if %errorlevel% equ 0 (
    echo       OK - migration done
) else (
    echo       WARN - migration issue, stamping current state...
    "%ALEMBIC%" stamp head >nul 2>&1
    echo       OK - stamped
)

:: Step 6: Start uvicorn
echo [6/7] Starting uvicorn on port %PORT%...
cd /d "%PROJECT_DIR%"
echo       uvicorn app.main:app --host 127.0.0.1 --port %PORT%
powershell -NoProfile -Command "Start-Process -FilePath '%UVICORN%' -ArgumentList 'app.main:app','--host','127.0.0.1','--port','%PORT%' -WindowStyle Hidden -RedirectStandardOutput '%PROJECT_DIR%server_out.log' -RedirectStandardError '%PROJECT_DIR%server_err.log'"
echo       Waiting for startup...
ping -n 9 127.0.0.1 >nul

:: Step 7: Start Celery worker (needed for batch conversion tasks)
echo [7/7] Starting Celery worker...
cd /d "%PROJECT_DIR%"
powershell -NoProfile -Command "Start-Process -FilePath '%CELERY%' -ArgumentList '-A','app.workers.celery_app','worker','--loglevel=info','--pool=solo' -WindowStyle Hidden -RedirectStandardOutput '%PROJECT_DIR%worker_out.log' -RedirectStandardError '%PROJECT_DIR%worker_err.log'"
echo       Waiting for worker startup...
ping -n 4 127.0.0.1 >nul

:: Verify
curl -s -m 5 http://127.0.0.1:%PORT%/health >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo   SUCCESS - Service is running!
    echo   Frontend : http://127.0.0.1:%PORT%/app/
    echo   API Docs : http://127.0.0.1:%PORT%/docs
    echo   This window will close automatically.
    echo ========================================
    start http://127.0.0.1:%PORT%/app/
    ping -n 4 127.0.0.1 >nul
    exit /b 0
) else (
    echo.
    echo ========================================
    echo   FAILED - check server_err.log for details
    echo   Try manually: "%UVICORN%" app.main:app --host 127.0.0.1 --port %PORT%
    echo ========================================
)

goto :end

:fail
echo.
echo ERROR - see message above
pause
exit /b 1

:end
pause
