#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_user="${SUDO_USER:-$USER}"
install_home="$(getent passwd "$install_user" | cut -d: -f6)"
install_uid="$(id -u "$install_user")"
service_name="takt.service"
bluetooth_agent_service="takt-bluetooth-agent.service"
hostname_target="${TAKT_HOSTNAME:-takt}"
port="${TAKT_PORT:-80}"
health_url="http://127.0.0.1:$port/health"

say() {
  printf '\nTAKT · %s\n' "$1"
}

fail() {
  printf '\nFEHLER: %s\n' "$1" >&2
  exit 1
}

cleanup() {
  [[ -z "${unit_file:-}" ]] || rm -f "$unit_file"
  [[ -z "${sudoers_temp:-}" ]] || rm -f "$sudoers_temp"
  [[ -z "${bluetooth_agent_unit:-}" ]] || rm -f "$bluetooth_agent_unit"
}
trap cleanup EXIT

[[ "$EUID" -ne 0 ]] || fail "Bitte dieses Skript als normaler Benutzer starten, nicht mit sudo."
[[ "$(uname -m)" == "aarch64" ]] || fail "TAKT benötigt Raspberry Pi OS Lite 64-bit (aarch64)."
[[ -r /etc/os-release ]] || fail "/etc/os-release wurde nicht gefunden."
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "raspbian" || "${ID:-}" == "debian" || "${ID_LIKE:-}" == *debian* ]] \
  || fail "TAKT unterstützt Raspberry Pi OS (Debian-basiert)."
[[ -n "$install_home" ]] || fail "Das Benutzerverzeichnis konnte nicht ermittelt werden."
[[ "$hostname_target" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,62}$ ]] \
  || fail "TAKT_HOSTNAME ist kein gültiger Hostname."
[[ "$port" =~ ^[0-9]+$ ]] || fail "TAKT_PORT muss eine Zahl sein."
((port >= 1 && port <= 65535)) || fail "TAKT_PORT muss zwischen 1 und 65535 liegen."

say "Systempakete für Raspberry Pi OS Lite installieren"
sudo env DEBIAN_FRONTEND=noninteractive apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  alsa-utils \
  avahi-daemon \
  bluez \
  bluez-tools \
  curl \
  pi-bluetooth \
  pipewire \
  pipewire-alsa \
  pipewire-audio \
  pipewire-pulse \
  pulseaudio-utils \
  python3-gpiozero \
  python3-lgpio \
  python3-pip \
  python3-setuptools \
  python3-venv \
  wireplumber

say "TAKT-Umgebung einrichten"
cd "$project_dir"
python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ -e .venv ]] && {
  [[ ! -x .venv/bin/python ]] \
    || [[ "$(.venv/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "$python_version" ]];
}; then
  mv .venv ".venv.incompatible.$(date +%Y%m%d%H%M%S)"
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv --system-site-packages .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
.venv/bin/python -m pip install --disable-pip-version-check --upgrade -e '.[server]'

config_dir="$install_home/.config/takt"
mkdir -p "$config_dir"
if [[ ! -f "$config_dir/config.toml" ]]; then
  cp "$project_dir/config.example.toml" "$config_dir/config.toml"
fi

sudo usermod -a -G gpio,audio "$install_user"
sudo systemctl enable --now avahi-daemon bluetooth

say "Headless-Bluetooth-Agent einrichten"
bluetooth_agent_unit="$(mktemp)"
{
  printf '%s\n' \
    "[Unit]" \
    "Description=TAKT headless Bluetooth pairing agent" \
    "After=bluetooth.service" \
    "Requires=bluetooth.service" \
    "" \
    "[Service]" \
    "Type=simple" \
    "ExecStart=/usr/bin/bt-agent --capability NoInputNoOutput" \
    "Restart=on-failure" \
    "RestartSec=2" \
    "" \
    "[Install]" \
    "WantedBy=multi-user.target"
} >"$bluetooth_agent_unit"
sudo install -m 0644 \
  "$bluetooth_agent_unit" "/etc/systemd/system/$bluetooth_agent_service"
rm -f "$bluetooth_agent_unit"
sudo systemctl daemon-reload
sudo systemctl enable --now "$bluetooth_agent_service"

say "Headless-Audio einrichten"
# PipeWire is a user service. Lingering starts that user session at boot even
# when the Lite system has no local login, and also creates /run/user/$uid.
sudo loginctl enable-linger "$install_user"
for _ in {1..20}; do
  [[ -S "/run/user/$install_uid/bus" ]] && break
  sleep 0.25
done
if [[ -S "/run/user/$install_uid/bus" ]]; then
  sudo -u "$install_user" env \
    HOME="$install_home" \
    XDG_RUNTIME_DIR="/run/user/$install_uid" \
    systemctl --user enable --now pipewire.socket pipewire-pulse.socket wireplumber.service
else
  fail "Die PipeWire-Benutzersitzung konnte nicht gestartet werden."
fi

say "Lokale Netzwerkadresse konfigurieren"
current_hostname="$(hostnamectl --static)"
if [[ "$current_hostname" != "$hostname_target" ]]; then
  sudo hostnamectl set-hostname "$hostname_target"
fi
sudo systemctl restart avahi-daemon

say "TAKT-Systemdienst installieren"
unit_file="$(mktemp)"
{
  printf '%s\n' \
    "[Unit]" \
    "Description=TAKT local stopwatch server" \
    "After=network-online.target bluetooth.target sound.target $bluetooth_agent_service" \
    "Wants=network-online.target bluetooth.target $bluetooth_agent_service" \
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
    "Environment=PYTHONUNBUFFERED=1" \
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
sudo visudo -cf "$sudoers_temp"
sudo install -m 0440 "$sudoers_temp" "$sudoers_file"

sudo systemctl daemon-reload
sudo systemctl enable "$service_name"
# enable --now does not restart an already running service after an update.
sudo systemctl restart "$service_name"

say "Alte lokale Kiosk-Konfiguration entfernen"
autostart_file="$install_home/.config/labwc/autostart"
if [[ -f "$autostart_file" ]]; then
  python3 - "$autostart_file" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
start = "# TAKT-KIOSK-BEGIN"
end = "# TAKT-KIOSK-END"
content = path.read_text(encoding="utf-8")
if start in content and end in content:
    before, remainder = content.split(start, 1)
    _, after = remainder.split(end, 1)
    content = before.rstrip() + "\n" + after.lstrip("\n")
content = "\n".join(
    line for line in content.splitlines()
    if not (".venv/bin/takt" in line and "takt-server" not in line)
)
path.write_text(content.rstrip() + "\n", encoding="utf-8")
PY
fi
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_boot_behaviour B1
fi

say "Installation prüfen"
ready=false
for _ in {1..30}; do
  if curl --silent --fail --max-time 2 "$health_url" >/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  sudo systemctl status "$service_name" --no-pager || true
  sudo journalctl -u "$service_name" -n 50 --no-pager || true
  fail "Der TAKT-Server ist nicht erreichbar."
fi

url="http://$hostname_target.local"
if [[ "$port" != "80" ]]; then
  url="$url:$port"
fi
printf '\nFERTIG · TAKT läuft headless und ist im lokalen Netzwerk erreichbar:\n%s\n' "$url"
printf 'Der physische Taster verwendet GPIO17 und GND.\n'
printf 'Ein Bildschirm oder Desktop auf dem Raspberry Pi ist nicht erforderlich.\n'

if [[ -t 0 ]]; then
  printf '\nJetzt neu starten, um die neue Headless- und Audio-Konfiguration vollständig zu übernehmen? [J/n] '
  read -r answer
  if [[ -z "$answer" || "$answer" =~ ^[JjYy]$ ]]; then
    sudo reboot
  fi
fi
