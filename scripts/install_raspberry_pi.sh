#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_user="${SUDO_USER:-$USER}"
install_home="$(getent passwd "$install_user" | cut -d: -f6)"
install_uid="$(id -u "$install_user")"
service_name="takt.service"
hostname_target="${TAKT_HOSTNAME:-takt}"
port="${TAKT_PORT:-80}"

say() {
  printf '\nTAKT · %s\n' "$1"
}

fail() {
  printf '\nFEHLER: %s\n' "$1" >&2
  exit 1
}

[[ "$EUID" -ne 0 ]] || fail "Bitte dieses Skript als normaler Benutzer starten, nicht mit sudo."
[[ "$(uname -m)" == "aarch64" ]] || fail "TAKT benötigt Raspberry Pi OS Desktop 64-bit (aarch64)."
[[ -n "$install_home" ]] || fail "Das Benutzerverzeichnis konnte nicht ermittelt werden."
[[ "$port" =~ ^[0-9]+$ ]] || fail "TAKT_PORT muss eine Zahl sein."
((port >= 1 && port <= 65535)) || fail "TAKT_PORT muss zwischen 1 und 65535 liegen."

say "Systempakete installieren"
sudo apt-get update
sudo apt-get install -y \
  alsa-utils \
  avahi-daemon \
  bluez \
  curl \
  pulseaudio-utils \
  python3-gpiozero \
  python3-lgpio \
  python3-pip \
  python3-setuptools \
  python3-venv

if ! command -v chromium >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
  if ! sudo apt-get install -y chromium; then
    sudo apt-get install -y chromium-browser
  fi
fi

chromium_command="$(command -v chromium || command -v chromium-browser)"

say "TAKT-Umgebung einrichten"
cd "$project_dir"
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -e '.[server]'

config_dir="$install_home/.config/takt"
mkdir -p "$config_dir"
if [[ ! -f "$config_dir/config.toml" ]]; then
  cp "$project_dir/config.example.toml" "$config_dir/config.toml"
fi

sudo usermod -a -G gpio "$install_user"
sudo usermod -a -G audio "$install_user"
sudo systemctl enable --now avahi-daemon
sudo systemctl enable --now bluetooth

say "Feste lokale Adresse konfigurieren"
current_hostname="$(hostnamectl --static)"
if [[ "$current_hostname" != "$hostname_target" ]]; then
  sudo hostnamectl set-hostname "$hostname_target"
fi
sudo systemctl restart avahi-daemon

say "TAKT-Systemdienst installieren"
unit_file="$(mktemp)"
trap 'rm -f "$unit_file"' EXIT
{
  printf '%s\n' \
    "[Unit]" \
    "Description=TAKT local stopwatch server" \
    "After=network-online.target bluetooth.target sound.target" \
    "Wants=network-online.target bluetooth.target" \
    "" \
    "[Service]" \
    "Type=simple" \
    "User=$install_user" \
    "Group=$install_user" \
    "SupplementaryGroups=gpio audio" \
    "WorkingDirectory=$project_dir" \
    "Environment=HOME=$install_home" \
    "Environment=XDG_RUNTIME_DIR=/run/user/$install_uid" \
    "Environment=GPIOZERO_PIN_FACTORY=lgpio" \
    "ExecStart=$project_dir/.venv/bin/takt-server --host 0.0.0.0 --port $port" \
    "Restart=on-failure" \
    "RestartSec=2" \
    "TimeoutStopSec=10" \
    "PrivateTmp=true"
  if ((port < 1024)); then
    printf '%s\n' \
      "AmbientCapabilities=CAP_NET_BIND_SERVICE" \
      "CapabilityBoundingSet=CAP_NET_BIND_SERVICE"
  fi
  printf '%s\n' \
    "" \
    "[Install]" \
    "WantedBy=multi-user.target"
} >"$unit_file"
sudo install -m 0644 "$unit_file" "/etc/systemd/system/$service_name"

systemctl_path="$(command -v systemctl)"
sudoers_file="/etc/sudoers.d/takt-poweroff-$install_user"
sudoers_temp="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: %s poweroff\n' "$install_user" "$systemctl_path" >"$sudoers_temp"
sudo install -m 0440 "$sudoers_temp" "$sudoers_file"
rm -f "$sudoers_temp"
sudo visudo -cf "$sudoers_file"

sudo systemctl daemon-reload
sudo systemctl enable --now "$service_name"

say "Vollbildanzeige beim Desktop-Start einrichten"
autostart_dir="$install_home/.config/labwc"
autostart_file="$autostart_dir/autostart"
mkdir -p "$autostart_dir"
touch "$autostart_file"
python3 - "$autostart_file" "$chromium_command" "$port" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
chromium = sys.argv[2]
port = sys.argv[3]
start = "# TAKT-KIOSK-BEGIN"
end = "# TAKT-KIOSK-END"
url = "http://127.0.0.1" if port == "80" else f"http://127.0.0.1:{port}"
block = f"""\
{start}
(
  until curl --silent --fail {url}/health >/dev/null; do sleep 1; done
  while true; do
    {chromium} --kiosk --noerrdialogs --disable-infobars --no-first-run \
      --disable-session-crashed-bubble --check-for-update-interval=31536000 {url}
    sleep 2
  done
) &
{end}
"""
content = path.read_text(encoding="utf-8")
# Remove the earlier desktop-app autostart line, if the old manual guide added it.
content = "\n".join(
    line
    for line in content.splitlines()
    if not (".venv/bin/takt" in line and "takt-server" not in line)
) + "\n"
if start in content and end in content:
    before, remainder = content.split(start, 1)
    _, after = remainder.split(end, 1)
    content = before.rstrip() + "\n\n" + block + after.lstrip("\n")
else:
    content = content.rstrip() + "\n\n" + block
path.write_text(content, encoding="utf-8")
PY

if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_boot_behaviour B4
fi

say "Installation prüfen"
sleep 2
curl --silent --fail "http://127.0.0.1${port:+:$port}/health" >/dev/null \
  || {
    sudo systemctl status "$service_name" --no-pager
    fail "Der TAKT-Server ist nicht erreichbar."
  }

url="http://$hostname_target.local"
if [[ "$port" != "80" ]]; then
  url="$url:$port"
fi
printf '\nFERTIG · TAKT ist im lokalen Netzwerk erreichbar:\n%s\n' "$url"
printf 'Der physische Taster verwendet GPIO17 und GND.\n'
printf 'Beim nächsten Desktop-Start öffnet sich TAKT automatisch im Vollbild.\n'

if [[ -t 0 ]]; then
  printf '\nJetzt neu starten, damit Gruppen- und Autostart-Einstellungen aktiv werden? [J/n] '
  read -r answer
  if [[ -z "$answer" || "$answer" =~ ^[JjYy]$ ]]; then
    sudo reboot
  fi
fi
