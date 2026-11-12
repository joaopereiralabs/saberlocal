#!/usr/bin/env python3
"""Demonstração local do portal, sem configurar Wi-Fi ou baixar coleções."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.server import create_app
from scripts.content import seed

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default=str(ROOT / 'data'))
    parser.add_argument('--port', type=int, default=8090)
    args = parser.parse_args()
    config = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
    config['data_dir'] = str(Path(args.data_dir).resolve())
    # A demonstração não monta nem exige o microSD de produção.
    config.pop('data_mount', None)
    config.pop('storage_uuid', None)
    seed(config['data_dir'])
    from waitress import serve
    print(f'Prévia local: http://127.0.0.1:{args.port}', flush=True)
    serve(create_app(config), host='127.0.0.1', port=args.port, threads=2)
