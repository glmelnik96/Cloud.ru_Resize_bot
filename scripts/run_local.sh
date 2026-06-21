#!/usr/bin/env bash
# Local manual smoke for App3 / creatives, before pushing for redeploy.
#
# Runs the REAL pipeline (Cloud.ru FM: GLM-5.1 + DeepSeek-V4-Pro) the same way
# the VM does (deploy/app3.service: venv + uvicorn), but with no gateway and no
# App1 — auth is faked via APP3_DEV_USER and the hero is uploaded by hand in the
# browser. Needs Python 3.11 (langgraph interrupt() in async nodes needs 3.11).
#
# One-time setup:
#   py -3.11 -m venv .venv311          # Windows (Git Bash); or: python3.11 -m venv .venv311
#   .venv311/Scripts/pip install -e '.[web]'   # POSIX: .venv311/bin/pip
#
# Run:
#   CLOUDRU_API_KEY=... ./scripts/run_local.sh
#   then open http://127.0.0.1:8013/
#
# Manual checklist:
#   1. page loads (canon header, no 401)
#   2. fill brief (product / goal / audience) -> "Запустить генерацию"
#   3. wait for text candidate -> "Принять"
#   4. on the hero step press "Загрузить картинку" and pick a local image
#   5. download the ZIP, open it, eyeball the per-format banners
set -euo pipefail

cd "$(dirname "$0")/.."

# venv python (Windows Git Bash uses Scripts/, POSIX uses bin/)
if [ -x ".venv311/Scripts/python.exe" ]; then
  PY=".venv311/Scripts/python.exe"
elif [ -x ".venv311/bin/python" ]; then
  PY=".venv311/bin/python"
else
  echo "error: .venv311 not found. Create it first:" >&2
  echo "  py -3.11 -m venv .venv311 && .venv311/Scripts/pip install -e '.[web]'" >&2
  exit 1
fi

if [ -z "${CLOUDRU_API_KEY:-}" ]; then
  echo "error: CLOUDRU_API_KEY is not set (needed for the real /new pipeline)." >&2
  echo "  CLOUDRU_API_KEY=... ./scripts/run_local.sh" >&2
  exit 1
fi

# Dev affordances for a no-gateway local run (prod-safe: these are opt-in env).
export APP3_DEV_USER="${APP3_DEV_USER:-gleb@cloud.ru}"  # fake the gateway user
export APP3_PREFIX=""                                    # assets/API at root
export HERO_GEN_BACKEND="none"                           # manual hero upload only

echo "App3 local smoke: http://127.0.0.1:8013/  (dev user: $APP3_DEV_USER)"
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8013 --reload
