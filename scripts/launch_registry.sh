#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ -z "${TAKT_REGISTRY_ADMIN_PASSWORD:-}" ]]; then
  printf '%s\n' \
    'FEHLER: TAKT_REGISTRY_ADMIN_PASSWORD muss gesetzt sein (mindestens 10 Zeichen).' \
    'Beispiel: TAKT_REGISTRY_ADMIN_PASSWORD="ein-langes-passwort" ./scripts/launch_registry.sh' >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

./scripts/build_registry_ui.sh
.venv/bin/python -m pip install --disable-pip-version-check -e '.[server]'

exec .venv/bin/takt-registry \
  --host "${TAKT_REGISTRY_HOST:-0.0.0.0}" \
  --port "${TAKT_REGISTRY_PORT:-8090}" \
  "$@"
