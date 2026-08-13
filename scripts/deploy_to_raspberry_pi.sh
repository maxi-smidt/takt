#!/usr/bin/env bash
set -Eeuo pipefail

# Manual fallback for offline or registry-less installs. Fleet Manager deployments
# use the registry-hosted release path and do not require this operator-laptop script.

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
current_hostname="$(ssh "$remote" 'hostnamectl --static')"
[[ "$current_hostname" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,62}$ ]] || {
  printf 'FEHLER: Der aktuelle Hostname konnte nicht validiert werden.\n' >&2
  exit 1
}
hostname_target="${TAKT_HOSTNAME:-}"
hostname_confirmation="${TAKT_CONFIRM_HOSTNAME_CHANGE:-}"
if [[ -n "$hostname_target" ]]; then
  [[ "$hostname_target" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,62}$ ]] || {
    printf 'FEHLER: Ungültiges TAKT_HOSTNAME: %s\n' "$hostname_target" >&2
    exit 1
  }
  [[ "$hostname_confirmation" == "$hostname_target" ]] || {
    printf 'FEHLER: Bestätige den Ziel-Hostname mit TAKT_CONFIRM_HOSTNAME_CHANGE=%s.\n' "$hostname_target" >&2
    exit 1
  }
  if [[ "$hostname_target" != "$current_hostname" ]]; then
    printf '\nWARNUNG · Hostname-Änderung: %s → %s.local\n' "$current_hostname" "$hostname_target"
    printf 'SSH-Adresse, mDNS, DHCP, Host-Keys und die Verbindung ändern sich.\n'
    printf 'Die Änderung wurde für den exakten Ziel-Hostname bestätigt.\n'
  fi
fi

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
printf -v hostname_confirmation_escaped '%q' "${TAKT_CONFIRM_HOSTNAME_CHANGE:-}"
ssh -t "$remote" \
  "cd ~/$remote_dir && TAKT_REGISTRY_URL=$registry_url_escaped TAKT_REGISTRY_ALLOW_INSECURE_HTTP=$allow_insecure_http_escaped TAKT_ENROLLMENT_CODE=$enrollment_code_escaped TAKT_DEVICE_NAME=$device_name_escaped TAKT_HOSTNAME=$hostname_escaped TAKT_CONFIRM_HOSTNAME_CHANGE=$hostname_confirmation_escaped bash scripts/install_raspberry_pi.sh"

if [[ -n "$hostname_target" && "$hostname_target" != "$current_hostname" ]]; then
  remote_user="${remote%@*}"
  if [[ "$remote" == *@* ]]; then
    new_remote="$remote_user@$hostname_target.local"
  else
    new_remote="$hostname_target.local"
  fi
  printf '\nTAKT · Neue Adresse prüfen: %s\n' "$new_remote"
  printf -v expected_hostname_escaped '%q' "$hostname_target"
  if ! ssh "$new_remote" "test \"\$(hostnamectl --static)\" = $expected_hostname_escaped"; then
    printf 'FEHLER: Die neue Adresse konnte nicht verifiziert werden; Wiederherstellung wird versucht.\n' >&2
    printf -v current_hostname_escaped '%q' "$current_hostname"
    ssh "$remote" \
      "sudo hostnamectl set-hostname $current_hostname_escaped && sudo systemctl restart avahi-daemon" \
      || printf 'WARNUNG: Wiederherstellung über die alte Adresse fehlgeschlagen. Hostname zurücksetzen: %s\n' "$current_hostname" >&2
    exit 1
  fi
fi

printf '\nFERTIG · Deployment und Server-Neustart waren erfolgreich.\n'
