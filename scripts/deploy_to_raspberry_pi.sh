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
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.mypy_cache/' \
  --exclude '.idea/' \
  --exclude '__pycache__/' \
  --exclude 'artifacts/' \
  --exclude 'build/' \
  --exclude 'dist/' \
  --exclude 'identifier.sqlite' \
  --exclude 'webui/node_modules/' \
  "$project_dir/" \
  "$remote:~/$remote_dir/"

printf '\nTAKT · Raspberry Pi vollständig einrichten\n'
ssh -t "$remote" "cd ~/$remote_dir && bash scripts/install_raspberry_pi.sh"

printf '\nFERTIG · Deployment und Server-Neustart waren erfolgreich.\n'
