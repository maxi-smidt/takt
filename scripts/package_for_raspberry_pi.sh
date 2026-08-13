#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_name="$(basename "$project_dir")"
default_archive="$project_dir/dist/takt-raspberry-pi.tar.gz"
archive="${1:-$default_archive}"

if [[ "$archive" != /* ]]; then
  archive="$project_dir/$archive"
fi

say() {
  printf '\nTAKT · %s\n' "$1"
}

fail() {
  printf '\nFEHLER: %s\n' "$1" >&2
  exit 1
}

for command in tar npm; do
  command -v "$command" >/dev/null 2>&1 \
    || fail "$command wurde auf diesem Laptop nicht gefunden."
done

say "Browser-Oberfläche aktualisieren"
"$project_dir/scripts/build_web_ui.sh"

required_files=(
  "pyproject.toml"
  "config.example.toml"
  "scripts/install_raspberry_pi.sh"
  "scripts/takt_wifi_helper.py"
  "src/takt/server_main.py"
  "src/takt/web/static/index.html"
  "src/takt/assets/start_signal.wav"
  "src/takt/assets/start_signal_source.mp3"
)

for required_file in "${required_files[@]}"; do
  [[ -f "$project_dir/$required_file" ]] \
    || fail "Erforderliche Datei fehlt: $required_file"
done

archive_dir="$(dirname "$archive")"
mkdir -p "$archive_dir"
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/takt-package.XXXXXX")"
temporary_archive="$temporary_dir/takt-raspberry-pi.tar.gz"
trap 'rm -rf "$temporary_dir"' EXIT

say "Sauberes Raspberry-Pi-Paket erstellen"
(
  cd "$(dirname "$project_dir")"
  COPYFILE_DISABLE=1 tar \
    --exclude="$project_name/.git" \
    --exclude="$project_name/.venv" \
    --exclude="$project_name/.env" \
    --exclude="$project_name/.env.*" \
    --exclude="$project_name/dist" \
    --exclude="$project_name/bundled-release" \
    --exclude="$project_name/build" \
    --exclude="$project_name/.pytest_cache" \
    --exclude="$project_name/.ruff_cache" \
    --exclude="$project_name/.mypy_cache" \
    --exclude="$project_name/.idea" \
    --exclude="$project_name/.registry-preview" \
    --exclude="$project_name/registry-data" \
    --exclude="$project_name/artifacts" \
    --exclude="$project_name/identifier.sqlite" \
    --exclude="$project_name/webui/node_modules" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude="*.pyo" \
    --exclude="*.egg-info" \
    --exclude="*.db" \
    --exclude="*.sqlite" \
    --exclude="*.sqlite3" \
    --exclude="*.pem" \
    --exclude="*.key" \
    --exclude=".DS_Store" \
    -czf "$temporary_archive" \
    "$project_name"
)

for required_file in "${required_files[@]}"; do
  tar -tzf "$temporary_archive" "$project_name/$required_file" >/dev/null \
    || fail "Paketprüfung fehlgeschlagen: $required_file"
done

mv -f "$temporary_archive" "$archive"

checksum_file="$archive.sha256"
archive_filename="$(basename "$archive")"
if command -v sha256sum >/dev/null 2>&1; then
  checksum="$(sha256sum "$archive" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  checksum="$(shasum -a 256 "$archive" | awk '{print $1}')"
else
  checksum=""
fi
if [[ -n "$checksum" ]]; then
  printf '%s  %s\n' "$checksum" "$archive_filename" >"$checksum_file"
fi

size="$(du -h "$archive" | awk '{print $1}')"
printf '\nFERTIG · Raspberry-Pi-Paket erstellt (%s):\n%s\n' "$size" "$archive"
if [[ -n "$checksum" ]]; then
  printf 'Prüfsumme:\n%s\n' "$checksum_file"
fi
printf '\nAuf dem Raspberry Pi entpacken und installieren:\n'
printf 'tar -xzf ~/%s -C ~\n' "$archive_filename"
printf 'cd ~/%s && ./scripts/install_raspberry_pi.sh\n' "$project_name"
