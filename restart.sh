#!/usr/bin/env bash
# Resize_bot — convenience: stop, then start with rebuild. Use after
# editing bot code, prompts, or template manifest (none of which are
# bind-mounted into the container).

set -euo pipefail

cd "$(dirname "$0")"

bash ./stop.sh
bash ./start.sh
