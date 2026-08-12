#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote="${1:-msmidt@raspberrypi.local}"
remote_dir="${TAKT_REMOTE_DIR:-takt}"

if [[ ! "$remote_dir" =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ ]] \
  || [[ "$remote_dir" == *".."* ]]; then
  printf 'FEHLER: Ungültiges TAKT_REMOTE_DIR: %s\n' "$remote_dir" >&2
  exit 1
fi

for command in npm rsync ssh; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'FEHLER: %s wurde auf diesem Laptop nicht gefunden.\n' "$command" >&2
    exit 1
  }
done

printf '\nTAKT · Zielsystem prüfen\n'
ssh "$remote" 'test "$(uname -m)" = aarch64 && test -r /etc/os-release' || {
  printf 'FEHLER: Das Ziel benötigt Raspberry Pi OS Lite 64-bit (aarch64).\n' >&2
  exit 1
}

printf '\nTAKT · Übertragung auf dem Ziel vorbereiten\n'
ssh -t "$remote" '
  if ! command -v rsync >/dev/null 2>&1; then
    sudo env DEBIAN_FRONTEND=noninteractive apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends rsync
  fi
'

printf '\nTAKT · Browser-Oberfläche prüfen und bauen\n'
"$project_dir/scripts/build_web_ui.sh"

printf '\nTAKT · Projekt auf %s übertragen\n' "$remote"
ssh "$remote" "mkdir -p ~/$remote_dir"
rsync -az \
  --delete-delay \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.registry-preview/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.mypy_cache/' \
  --exclude '.idea/' \
  --exclude '__pycache__/' \
  --exclude 'artifacts/' \
  --exclude 'build/' \
  --exclude 'dist/' \
  --exclude 'registry-data/' \
  --exclude 'identifier.sqlite' \
  --exclude '*.db' \
  --exclude '*.sqlite' \
  --exclude '*.sqlite3' \
  --exclude '*.pem' \
  --exclude '*.key' \
  --exclude 'webui/node_modules/' \
  "$project_dir/" \
  "$remote:~/$remote_dir/"

printf '\nTAKT · Raspberry Pi vollständig einrichten\n'
printf -v registry_url_escaped '%q' "${TAKT_REGISTRY_URL:-}"
printf -v allow_insecure_http_escaped '%q' "${TAKT_REGISTRY_ALLOW_INSECURE_HTTP:-}"
printf -v enrollment_code_escaped '%q' "${TAKT_ENROLLMENT_CODE:-}"
printf -v device_name_escaped '%q' "${TAKT_DEVICE_NAME:-}"
printf -v hostname_escaped '%q' "${TAKT_HOSTNAME:-}"
ssh -t "$remote" \
  "cd ~/$remote_dir && TAKT_REGISTRY_URL=$registry_url_escaped TAKT_REGISTRY_ALLOW_INSECURE_HTTP=$allow_insecure_http_escaped TAKT_ENROLLMENT_CODE=$enrollment_code_escaped TAKT_DEVICE_NAME=$device_name_escaped TAKT_HOSTNAME=$hostname_escaped bash scripts/install_raspberry_pi.sh"

printf '\nFERTIG · Deployment und Server-Neustart waren erfolgreich.\n'
