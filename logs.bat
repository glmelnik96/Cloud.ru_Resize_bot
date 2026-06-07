@echo off
REM =====================================================================
REM  Resize_bot - tail logs of either bot or redis container.
REM  Usage:
REM     logs.bat            -> tail bot
REM     logs.bat redis      -> tail redis
REM  Ctrl+C to detach.
REM =====================================================================

@chcp 65001 > nul
@title Resize_bot logs

cd /d "%~dp0"

if /i "%~1"=="redis" (
    docker logs -f resize-redis
) else (
    docker logs -f resize-bot
)
