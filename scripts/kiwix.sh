#!/usr/bin/env bash
set -Eeuo pipefail
shopt -s nullglob
data_dir=$(python3 -c 'import json; print(json.load(open("/etc/saberlocal/config.json"))["data_dir"])')
books=("$data_dir"/zim/*.zim)
if ((${#books[@]} == 0)); then
  echo 'Nenhuma coleção ZIM instalada. Use sudo saberlocal download.' >&2
  exit 0
fi
exec /usr/bin/kiwix-serve --address=127.0.0.1 --port=8081 --threads=2 --urlRootLocation=/biblioteca "${books[@]}"
