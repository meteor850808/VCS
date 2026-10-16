@echo off
rem Legacy entry kept for compatibility. Delegates to the offline deploy subsystem.
echo This script now delegates to deploy\deploy_offline.bat
call "%~dp0deploy\deploy_offline.bat" %*
exit /b %errorlevel%
