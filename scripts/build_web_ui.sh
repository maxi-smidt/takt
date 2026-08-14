#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir/webui"

if [[ ! -x node_modules/.bin/tsc || ! -x node_modules/.bin/eslint || ! -x node_modules/.bin/vitest || ! -x node_modules/.bin/vite || ! -d node_modules/@typescript-eslint/eslint-plugin || ! -d node_modules/@typescript-eslint/parser || ! -d node_modules/@types/node || ! -d node_modules/@types/react || ! -d node_modules/@types/react-dom ]]; then
  npm ci
fi

npm run typecheck
npm run check
npm test
npm run build
