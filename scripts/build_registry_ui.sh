#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir/webui"

if [[ ! -d node_modules ]]; then
  npm ci
fi

npm run typecheck
npm run check
npm test
npm run build:fleet
