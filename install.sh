#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ${1:-} == --help ]]; then
  echo 'Uso: sudo bash install.sh [--storage-uuid UUID] [--interface wlan0] [--no-content] [--no-activate] [--check]'
  exit 0
fi
if [[ $EUID -ne 0 ]]; then echo 'Execute com sudo bash install.sh'; exit 1; fi
if [[ ! -f /etc/os-release ]]; then echo 'Não foi possível identificar o sistema.'; exit 1; fi
source /etc/os-release
if ! command -v python3 >/dev/null; then
  case "$ID" in
    alpine) apk add --no-cache python3 ca-certificates ;;
    ubuntu|debian) apt-get update; apt-get install -y --no-install-recommends python3 ca-certificates ;;
    *) echo "Sistema não suportado: $ID"; exit 1 ;;
  esac
fi
exec python3 scripts/install.py "$@"
