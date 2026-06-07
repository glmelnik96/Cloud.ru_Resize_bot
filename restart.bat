@echo off
REM =====================================================================
REM  Resize_bot - restart. Convenience: stop, then start with rebuild.
REM  Use after editing bot code / prompts / templates.json.
REM =====================================================================

@chcp 65001 > nul
@title Resize_bot restart

cd /d "%~dp0"

call "%~dp0stop.bat"
call "%~dp0start.bat"
