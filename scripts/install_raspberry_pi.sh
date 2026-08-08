#!/usr/bin/env bash
set -Eeuo pipefail

# Allow launching with `sudo ./install_raspberry_pi.sh`: re-exec as the
# invoking normal user before touching anything, so the rest of the script
# (venv, pip, config files) runs unprivileged as intended and only the
# explicit `sudo` calls further down elevate individual commands. Without
# this, running the whole script as root would leave the venv, pip cache and
# config files root-owned, which the takt.service user can't use afterwards.
if [[ "$EUID" -eq 0 ]]; then
  if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
    script_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
    exec sudo -u "$SUDO_USER" -H env \
      TAKT_HOSTNAME="${TAKT_HOSTNAME:-}" \
      TAKT_PORT="${TAKT_PORT:-}" \
      bash "$script_path" "$@"
  fi
  printf '\nFEHLER: %s\n' "Bitte dieses Skript nicht direkt als root starten." >&2
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
  # Support an admin managing another account via `sudo -u <user> ./install...`.
  install_user="$SUDO_USER"
else
  install_user="$USER"
fi
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
  [[ -z "${bluetooth_conf_temp:-}" ]] || rm -f "$bluetooth_conf_temp"
}
trap cleanup EXIT

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
  rfkill \
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
sudo rfkill unblock bluetooth || true

say "Bluetooth-Konfiguration härten"
# A longer TemporaryTimeout keeps unpaired scan results around instead of
# BlueZ evicting them ~30 s after discovery stops, and AutoEnable powers the
# adapter back on automatically after a reboot.
bluetooth_conf="/etc/bluetooth/main.conf"
bluetooth_conf_temp="$(mktemp)"
python3 - "$bluetooth_conf" "$bluetooth_conf_temp" <<'PY'
from pathlib import Path
import sys

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
content = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
lines = content.splitlines()


def ensure_setting(lines: list[str], section: str, key: str, value: str) -> list[str]:
    section_header = f"[{section}]"
    setting = f"{key} = {value}"
    section_start = None
    for index, line in enumerate(lines):
        if line.strip() == section_header:
            section_start = index
            break
    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(section_header)
        lines.append(setting)
        return lines
    section_end = len(lines)
    for index in range(section_start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_end = index
            break
    for index in range(section_start + 1, section_end):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        existing_key = stripped.split("=", 1)[0].strip()
        if existing_key == key:
            lines[index] = setting
            return lines
    lines.insert(section_end, setting)
    return lines


lines = ensure_setting(lines, "General", "TemporaryTimeout", "300")
lines = ensure_setting(lines, "Policy", "AutoEnable", "true")
target_path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
PY
sudo install -m 0644 "$bluetooth_conf_temp" "$bluetooth_conf"
rm -f "$bluetooth_conf_temp"
sudo systemctl restart bluetooth

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
    "Restart=always" \
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
    # AmbientCapabilities alone is enough to bind the privileged port as a
    # non-root user. Also setting CapabilityBoundingSet would strip every
    # other capability from the unit, including the ones setuid sudo needs
    # to elevate to root for the shutdown button's `sudo systemctl poweroff`.
    printf '%s\n' "AmbientCapabilities=CAP_NET_BIND_SERVICE"
  fi
  printf '%s\n' \
    "" \
    "[Install]" \
    "WantedBy=multi-user.target"
} >"$unit_file"
sudo install -m 0644 "$unit_file" "/etc/systemd/system/$service_name"

sudoers_file="/etc/sudoers.d/takt-poweroff-$install_user"
sudoers_temp="$(mktemp)"
{
  # usrmerge symlinks /bin to /usr/bin on current Raspberry Pi OS, but sudo
  # matches the exact path a command is invoked with, so allow both.
  printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff\n' "$install_user"
  printf '%s ALL=(root) NOPASSWD: /bin/systemctl poweroff\n' "$install_user"
} >"$sudoers_temp"
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

# Non-destructive check: confirm the passwordless sudo rule for the shutdown
# button actually applies, without ever powering the device off here.
if ! sudo -n -l 2>/dev/null | grep -q "systemctl poweroff"; then
  printf '\nWARNUNG: Die sudo-Berechtigung für "systemctl poweroff" konnte für ' >&2
  printf 'Benutzer %s nicht bestätigt werden. Der Button "Herunterfahren" in der ' "$install_user" >&2
  printf 'Weboberfläche könnte fehlschlagen.\n' >&2
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
