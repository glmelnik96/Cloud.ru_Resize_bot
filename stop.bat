@echo off
REM =====================================================================
REM  Resize_bot - stop.
REM  docker compose down (preserves named volumes: heroes / renders /
REM  zips / redis_data). Pass --wipe as first arg to also wipe volumes
REM  (DESTRUCTIVE - clears Redis session state and generated artifacts).
REM
REM  ASCII-only on purpose. See start.bat for the codepage rant.
REM =====================================================================

@chcp 65001 > nul
@title Resize_bot stop

cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] docker is not on PATH.
    pause
    exit /b 1
)

if /i "%~1"=="--wipe" goto wipe

echo [stop] docker compose down   (volumes preserved)
docker compose down
set EXIT_CODE=%ERRORLEVEL%
goto done

:wipe
echo [stop] --wipe specified: data volumes will be DELETED.
set /p CONFIRM=Type 'yes' to confirm:
if /i not "%CONFIRM%"=="yes" (
    echo [stop] aborted.
    pause
    exit /b 1
)
echo [stop] docker compose down -v
docker compose down -v
set EXIT_CODE=%ERRORLEVEL%

:done
echo.
echo [stop] done (exit code %EXIT_CODE%).
pause
exit /b %EXIT_CODE%
