@echo off
rem ===========================================================================
rem VCS offline deploy - SILENT, UNATTENDED entry point.
rem
rem Use this from a scheduler / remote shell / already-elevated technician
rem session. It never prompts: no UAC self-elevation and no final pause, so it
rem cannot hang waiting for input.
rem
rem It must already be running as Administrator. If it is not, it fails fast with
rem a readable message instead of triggering a UAC dialog - use deploy_offline.bat
rem when you need the interactive elevation prompt.
rem
rem Exit code: 0 on success, 1 on failure. Machine-readable verdict:
rem   deploy\last_result.json
rem Full stage-by-stage log:
rem   deploy\logs\deploy_<stamp>.log
rem On failure a diagnostics bundle is written to:
rem   deploy\diagnostics\<stamp>\
rem
rem Extra arguments are passed through, e.g.  deploy_silent.bat --from 20
rem ===========================================================================
setlocal
title VCS Offline Deploy (silent)

rem Administrator probe that does not depend on the Server service being started.
fltmc >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] deploy_silent.bat requires an elevated session ^(Administrator^).
    echo         Run it from an elevated command prompt, or use deploy_offline.bat
    echo         which will ask for elevation through UAC.
    exit /b 1
)

rem Pass the caller's arguments through untouched (unquoted, so "--from 20" stays
rem two arguments for PowerShell), and add --silent --unattended.
rem deploy.ps1 accepts both "--from 20" and "-From 20" spellings, and rejects an
rem argument it does not recognise (exit 2) instead of silently ignoring it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy.ps1" %* --silent --unattended
set RC=%errorlevel%
if %RC% neq 0 (
    if %RC% equ 2 echo [ERROR] Bad command line - see the log for the supported options.
    echo [ERROR] Deploy FAILED ^(exit %RC%^). See deploy\logs\ and deploy\last_result.json
) else (
    echo [OK] Deploy SUCCESS.
)
exit /b %RC%
