#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

./scripts/build_web_ui.sh
.venv/bin/python -m pip install -e '.[dev]'
exec .venv/bin/takt-server \
  --host 127.0.0.1 \
  --port 8080 \
  --mock-gpio \
  --mock-buzzer \
  "$@"
