@echo off
REM =====================================================================
REM  Resize_bot - start.
REM  Wraps docker compose. Rebuilds the image so any change in bot code,
REM  prompts, or template manifest is picked up (the container has no
REM  bind-mount), then waits for "graph_ready" in the bot log.
REM
REM  ASCII-only on purpose: cmd.exe parses .bat files in the active OEM
REM  codepage (cp866 on RU Windows). Non-ASCII chars break the parser.
REM  chcp 65001 switches only the *output* encoding.
REM =====================================================================

@chcp 65001 > nul
@title Resize_bot start

cd /d "%~dp0"

if not exist ".env" (
    echo [ERROR] .env not found in %CD%
    echo Copy .env.example to .env and fill TELEGRAM_BOT_TOKEN / CLOUDRU_* / WHITELIST_USER_IDS.
    pause
    exit /b 1
)

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] docker is not on PATH. Install Docker Desktop and re-open the terminal.
    pause
    exit /b 1
)

echo [start] docker compose up -d --build
docker compose up -d --build
if errorlevel 1 (
    echo [ERROR] docker compose failed.
    pause
    exit /b 1
)

echo [start] waiting for graph_ready (timeout 60s)...
set /a TRIES=0
:wait_loop
docker logs resize-bot 2>&1 | findstr /C:"\"event\": \"graph_ready\"" >nul
if not errorlevel 1 goto ready
set /a TRIES+=1
if %TRIES% GEQ 60 goto timeout
REM ping is more portable than timeout.exe across PATH setups (Git Bash
REM ships a Unix-style `timeout` that hijacks the command in some shells).
ping -n 2 127.0.0.1 >nul
goto wait_loop

:ready
echo [start] OK - bot is ready.
docker logs resize-bot --tail 5
echo.
pause
exit /b 0

:timeout
echo [start] WARNING: graph_ready not seen in 60s. Last 20 log lines:
docker logs resize-bot --tail 20
pause
exit /b 1
