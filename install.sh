#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ${1:-} == --help ]]; then
  echo 'Uso: sudo bash install.sh [--interface wlan0] [--data-dir /mnt/saberlocal] [--no-content] [--check]'
  exit 0
fi
if [[ $EUID -ne 0 ]]; then echo 'Execute com sudo bash install.sh'; exit 1; fi
if [[ ! -f /etc/os-release || ! -d /run/systemd/system ]]; then
  echo 'Requer Ubuntu/Armbian com systemd, já inicializado na TV Box.'; exit 1
fi
if ! command -v python3 >/dev/null; then
  apt-get update
  apt-get install -y --no-install-recommends python3 ca-certificates
fi
exec python3 scripts/install.py "$@"
