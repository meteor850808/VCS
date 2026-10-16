@echo off
rem ============================================================
rem  Redis 启动脚本（Windows）
rem  优先使用 Windows 服务（推荐，开机自启）：
rem     redis-server --service-install redis.windows-service.conf --service-name Redis
rem  若无服务则前台启动 redis-server.exe
rem ============================================================

set REDIS_DIR=C:\Program Files\Redis
set REDIS_EXE=%REDIS_DIR%\redis-server.exe
set REDIS_CLI=%REDIS_DIR%\redis-cli.exe

:: 方式一：Windows 服务（已有 Redis 服务时）
sc query Redis >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=4" %%a in ('sc query Redis ^| findstr STATE') do (
        if "%%a"=="RUNNING" (
            echo Redis service already running.
        ) else (
            echo Starting Redis service...
            net start Redis >nul 2>&1
            echo Redis service started.
        )
    )
    goto :verify
)

:: 方式二：无服务 → 注册服务
if exist "%REDIS_EXE%" (
    echo Installing Redis as Windows service...
    "%REDIS_EXE%" --service-install "%REDIS_DIR%\redis.windows-service.conf" --service-name Redis
    net start Redis >nul 2>&1
    echo Redis service installed and started.
    goto :verify
)

:: 方式三：连 redis-server.exe 都没有 → 提示
echo ERROR: redis-server.exe not found at %REDIS_DIR%
echo Please install Redis for Windows (e.g. https://github.com/tporadowski/redis)
pause
exit /b 1

:verify
"%REDIS_CLI%" ping
if %errorlevel% equ 0 (
    echo Redis is up: PONG
) else (
    echo WARN: redis-cli ping failed - check service status
)
pause
