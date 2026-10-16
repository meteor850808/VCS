@echo off
setlocal enabledelayedexpansion
title VCS Offline Deploy
rem Interactive entry point: self-elevates through UAC when needed and pauses at
rem the end so the operator can read the verdict.
rem For a fully non-interactive run use deploy_silent.bat from an elevated session.

rem --silent implies "do not pause", so this entry point can also be driven by a
rem script that just wants the UAC prompt handled.
set SILENT_MODE=0
for %%A in (%*) do (
    if /i "%%A"=="--silent" set SILENT_MODE=1
)

rem Self-elevate to Administrator if needed.
rem The elevated run gets its own console, so we hand the child to PowerShell with
rem -Wait: this window stays alive until the deploy finishes, prints the real exit
rem code, and only then pauses.
fltmc >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs -Wait"
    set RC=!errorlevel!
    echo.
    if !RC! neq 0 (
        echo Elevated run FAILED ^(exit !RC!^). See deploy\logs\ and deploy\last_result.json
    ) else (
        echo Elevated run finished OK ^(exit 0^).
    )
    if "!SILENT_MODE!"=="1" exit /b !RC!
    pause
    exit /b !RC!
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy.ps1" %*
set RC=%errorlevel%
if %RC% neq 0 (
    echo.
    echo Deploy FAILED ^(exit %RC%^). See deploy\logs\ for details.
) else (
    echo.
    echo Deploy SUCCESS.
)
if "%SILENT_MODE%"=="1" exit /b %RC%
pause
exit /b %RC%
