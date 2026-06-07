#!/usr/bin/env bash
# Resize_bot — tail bot logs (structured JSON). Pass --redis to follow
# redis container instead. Ctrl+C to detach.

set -euo pipefail

target="${1:-bot}"
case "$target" in
  --redis|redis)
    docker logs -f resize-redis
    ;;
  --bot|bot|*)
    docker logs -f resize-bot
    ;;
esac
