#!/usr/bin/env bash
# Resize_bot — shutdown helper. Stops the bot + redis containers but keeps
# named volumes (heroes / renders / zips / redis_data) intact so a
# subsequent start.sh resumes with all history.
#
# Pass --wipe to also remove the data volumes (DESTRUCTIVE — clears Redis
# session state and all generated artifacts). Use only when you really
# want a clean slate.

set -euo pipefail

cd "$(dirname "$0")"

if [[ "${1:-}" == "--wipe" ]]; then
  echo "[stop] docker compose down -v   (volumes will be DELETED)"
  read -p "Are you sure? Type 'yes' to confirm: " confirm
  if [[ "$confirm" != "yes" ]]; then
    echo "[stop] aborted."
    exit 1
  fi
  docker compose down -v
else
  echo "[stop] docker compose down   (volumes preserved)"
  docker compose down
fi

echo "[stop] done."
