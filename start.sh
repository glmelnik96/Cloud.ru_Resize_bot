#!/usr/bin/env bash
# Resize_bot — boot helper. Pulls latest .env, rebuilds the image so any
# code/prompt/template change is picked up (the bot container has no
# bind-mount), starts the stack in detached mode, then tails the bot log
# until it reports `graph_ready`.

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "ERROR: .env is missing. Copy .env.example and fill in TELEGRAM_BOT_TOKEN / CLOUDRU_* / WHITELIST_USER_IDS first." >&2
  exit 1
fi

echo "[start] docker compose up -d --build"
docker compose up -d --build

echo "[start] waiting for graph_ready (timeout 60s)..."
for i in $(seq 1 60); do
  if docker logs resize-bot 2>&1 | grep -q '"event": "graph_ready"'; then
    echo "[start] OK — bot is ready."
    docker logs resize-bot --tail 5
    exit 0
  fi
  sleep 1
done

echo "[start] WARNING: graph_ready not seen in 60s. Last 20 log lines:" >&2
docker logs resize-bot --tail 20
exit 1
