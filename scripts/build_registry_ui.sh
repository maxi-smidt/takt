#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir/webui"

if [[ ! -d node_modules ]]; then
  npm ci
fi

npm run check
npm run build:fleet
